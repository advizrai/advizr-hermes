"""advizr_overlay.server — the Hermes engine as an Advizr `EngineEvent` service.

Endpoints (all HMAC-verified except /health):
  · GET  /health   — liveness for the TS adapter's pre-assemble probe + Railway.
  · POST /v1/chat  — SSE stream of EngineEvents (one per `data:` frame).
  · POST /v1/run   — headless turn; returns a `HermesRunResponse` JSON.

The messaging gateway is NEVER started (this module only runs the HTTP server),
so Telegram/WhatsApp/etc. stay dormant. Turns are serialized behind a process
lock for the pilot (the global Hermes tool registry is request-scoped); replicas
scale horizontally. Each turn runs under a fresh per-(workspace,session)
HERMES_HOME that is removed afterward.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from advizr_overlay import agent_factory, config
from advizr_overlay import engine_events as ev
from advizr_overlay import tool_bridge
from advizr_overlay.event_bridge import SENTINEL, EngineEventBridge
from advizr_overlay.result_store import ResultStore

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("advizr-hermes")

app = FastAPI(title="Advizr Hermes Engine", version="0.1.0")

# Pilot: serialize turns (the upstream tool registry is a process-global).
TURN_LOCK = asyncio.Lock()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "advizr-hermes",
        "wireVersion": config.WIRE_VERSION,
        "openrouter": bool(config.openrouter_api_key()),
    }


async def _verified_body(request: Request) -> bytes | None:
    raw = await request.body()
    sig = request.headers.get(config.SIGNATURE_HEADER)
    if not config.verify_signature(raw, sig, config.hermes_auth_key()):
        return None
    return raw


@app.post("/v1/chat")
async def chat(request: Request):
    raw = await _verified_body(request)
    if raw is None:
        return JSONResponse({"error": "Invalid signature"}, status_code=401)
    try:
        body = json.loads(raw)
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    return StreamingResponse(
        _chat_stream(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/run")
async def run(request: Request):
    raw = await _verified_body(request)
    if raw is None:
        return JSONResponse({"error": "Invalid signature"}, status_code=401)
    try:
        body = json.loads(raw)
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    result = await _run_headless(body)
    return JSONResponse(result)


# ── chat (SSE) ────────────────────────────────────────────────────────────────


async def _chat_stream(body: dict[str, Any]) -> AsyncGenerator[str, None]:
    run_id = body.get("runId") or str(uuid.uuid4())
    loop = asyncio.get_running_loop()

    async with TURN_LOCK:
        home = tempfile.mkdtemp(prefix=_home_prefix(body))
        prev_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = home
        results = ResultStore()
        bridge = EngineEventBridge(loop, run_id, results)
        request_ctx = _request_ctx(body, run_id, is_headless=False)
        registered = tool_bridge.register_advizr_tools(body.get("toolSpecs") or [], request_ctx, results)

        yield ev.sse_frame(ev.run_start(run_id))
        future = asyncio.ensure_future(asyncio.to_thread(_run_turn, body, bridge))
        try:
            while True:
                item = await bridge.get()
                if item is SENTINEL:
                    break
                yield ev.sse_frame(item)

            try:
                result = await future
            except Exception as exc:  # noqa: BLE001
                log.exception("hermes turn raised")
                result = {"failed": True, "error": str(exc)}

            if not bridge.any_text:
                text = agent_factory.extract_text(result)
                if text:
                    yield ev.sse_frame(ev.text_delta(text))
            in_tok, out_tok = agent_factory.extract_usage(result)
            yield ev.sse_frame(ev.usage(in_tok, out_tok))
            if agent_factory.is_failed(result):
                yield ev.sse_frame(ev.error("hermes_error", str(result.get("error") or "run failed")))
            yield ev.sse_frame(ev.run_end(run_id, agent_factory.extract_finish(result)))
        except Exception as exc:  # noqa: BLE001
            log.exception("chat stream error")
            yield ev.sse_frame(ev.error("overlay_error", str(exc)))
            yield ev.sse_frame(ev.run_end(run_id, "error"))
        finally:
            tool_bridge.deregister_advizr_tools(registered)
            _restore_home(prev_home)
            shutil.rmtree(home, ignore_errors=True)


def _run_turn(body: dict[str, Any], bridge: EngineEventBridge) -> dict[str, Any]:
    """Worker-thread: build the agent + run one turn. Always signals the bridge."""
    try:
        agent = agent_factory.build_agent(
            model=body.get("model"),
            stream_delta_cb=bridge.stream_delta,
            tool_progress_cb=bridge.tool_progress,
        )
        result = agent_factory.run_turn(
            agent,
            user_message=body.get("userMessage", ""),
            system_message=body.get("systemMessage") or "",
            history=_history(body),
        )
        return result if isinstance(result, dict) else {"final_response": str(result), "completed": True}
    finally:
        bridge.finish()


# ── headless (/v1/run) ────────────────────────────────────────────────────────


async def _run_headless(body: dict[str, Any]) -> dict[str, Any]:
    run_id = body.get("runId") or str(uuid.uuid4())
    loop = asyncio.get_running_loop()

    async with TURN_LOCK:
        home = tempfile.mkdtemp(prefix=_home_prefix(body))
        prev_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = home
        results = ResultStore()
        # Headless: no event bridge needed; tools still execute via the callback.
        bridge = EngineEventBridge(loop, run_id, results)
        request_ctx = _request_ctx(body, run_id, is_headless=True)
        registered = tool_bridge.register_advizr_tools(body.get("toolSpecs") or [], request_ctx, results)
        try:
            result = await asyncio.to_thread(_run_headless_turn, body, bridge)
        except Exception as exc:  # noqa: BLE001
            log.exception("headless turn error")
            result = {"failed": True, "error": str(exc)}
        finally:
            tool_bridge.deregister_advizr_tools(registered)
            _restore_home(prev_home)
            shutil.rmtree(home, ignore_errors=True)

    in_tok, out_tok = agent_factory.extract_usage(result)
    failed = agent_factory.is_failed(result)
    return {
        "runId": run_id,
        "text": agent_factory.extract_text(result),
        "inputTokens": in_tok,
        "outputTokens": out_tok,
        "totalTokens": in_tok + out_tok,
        "finishReason": agent_factory.extract_finish(result),
        "success": not failed,
        "errorCode": str(result.get("error")) if failed and result.get("error") else None,
    }


def _run_headless_turn(body: dict[str, Any], bridge: EngineEventBridge) -> dict[str, Any]:
    agent = agent_factory.build_agent(
        model=body.get("model"),
        stream_delta_cb=bridge.stream_delta,
        tool_progress_cb=bridge.tool_progress,
    )
    result = agent_factory.run_turn(
        agent,
        user_message=body.get("prompt", ""),
        system_message=body.get("systemMessage") or "",
        history=[],
    )
    return result if isinstance(result, dict) else {"final_response": str(result), "completed": True}


# ── helpers ───────────────────────────────────────────────────────────────────


def _history(body: dict[str, Any]) -> list[dict[str, Any]]:
    hist = body.get("conversationHistory") or []
    out = []
    for m in hist:
        if isinstance(m, dict) and "role" in m and "content" in m:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _request_ctx(body: dict[str, Any], run_id: str, *, is_headless: bool) -> dict[str, Any]:
    return {
        "workspaceId": body.get("workspaceId"),
        "agentId": body.get("agentId"),
        "sessionId": body.get("sessionId"),
        "runId": run_id,
        "userId": body.get("userId"),
        "userEmail": body.get("userEmail"),
        "isHeadless": is_headless,
    }


def _home_prefix(body: dict[str, Any]) -> str:
    ws = str(body.get("workspaceId") or "default")
    sid = str(body.get("sessionId") or body.get("runId") or "x")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in f"{ws}-{sid}")
    return f"hermes-{safe[:60]}-"


def _restore_home(prev: str | None) -> None:
    if prev is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = prev
