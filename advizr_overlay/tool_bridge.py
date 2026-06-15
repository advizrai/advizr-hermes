"""advizr_overlay.tool_bridge — register Advizr business tools into Hermes.

Each bridged tool's handler POSTs to Advizr's HMAC'd `/api/agents/engine/tool-exec`
route, which runs the REAL `executeSkill(...)` with process-pinned scoped creds.
So this service re-implements NO skill logic and holds NO Supabase credential —
it only forwards the model's tool call and returns the result string.

Registration is request-scoped: the server registers the request's toolSpecs
into the global Hermes registry under a turn lock, then deregisters them. Handlers
close over the per-request context (agentId / sessionId / runId / signer).
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Callable

import httpx

from advizr_overlay import config
from advizr_overlay.result_store import ResultStore


def _registry():
    # Imported lazily so the overlay can be unit-tested without the full agent.
    from tools.registry import registry  # type: ignore

    return registry


def _supported_register_kwargs(register_fn: Callable[..., Any], desired: dict[str, Any]) -> dict[str, Any]:
    """Pass only kwargs the installed `registry.register` actually accepts
    (the upstream signature evolves; we don't want to break on a new arg)."""
    try:
        params = inspect.signature(register_fn).parameters
    except (TypeError, ValueError):
        return desired
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if has_var_kw:
        return desired
    return {k: v for k, v in desired.items() if k in params}


def register_advizr_tools(
    tool_specs: list[dict[str, Any]],
    request_ctx: dict[str, Any],
    results: ResultStore,
) -> list[str]:
    """Register the request's business tools; returns the registered slugs.

    Degrades gracefully: no tools → no registry import; a registry import/API
    failure logs and returns [] so the turn still runs (just without business
    tools) instead of tearing the SSE stream.
    """
    if not tool_specs:
        return []
    try:
        registry = _registry()
    except Exception as exc:  # noqa: BLE001
        print(f"[tool_bridge] tool registry unavailable — running without tools: {exc}", flush=True)
        return []
    registered: list[str] = []
    for spec in tool_specs:
        slug = spec.get("slug")
        if not slug:
            continue
        description = spec.get("description", "")
        input_schema = spec.get("inputSchema") or {"type": "object", "properties": {}}
        # OpenAI function-call shape Hermes expects for the model.
        schema = {"name": slug, "description": description, "parameters": input_schema}
        handler = _make_handler(slug, request_ctx, results)
        desired = {
            "name": slug,
            "toolset": "advizr",
            "schema": schema,
            "handler": handler,
            "is_async": False,
            "description": description,
        }
        try:
            registry.register(**_supported_register_kwargs(registry.register, desired))
            registered.append(slug)
        except Exception as exc:  # noqa: BLE001 — never let one bad tool abort the turn
            print(f"[tool_bridge] failed to register {slug}: {exc}", flush=True)
    return registered


def deregister_advizr_tools(slugs: list[str]) -> None:
    registry = _registry()
    deregister = getattr(registry, "deregister", None)
    if not callable(deregister):
        return
    for slug in slugs:
        try:
            deregister(slug)
        except Exception:  # noqa: BLE001
            pass


def _make_handler(slug: str, request_ctx: dict[str, Any], results: ResultStore) -> Callable[..., str]:
    def handler(args: Any = None, **kwargs: Any) -> str:
        # Hermes invokes handlers as handler(args_dict, **kwargs); be tolerant.
        if args is None:
            args = kwargs.get("args") or {k: v for k, v in kwargs.items() if k not in ("task_id", "session_id", "user_task")}
        if not isinstance(args, dict):
            args = {"value": args}
        result = call_tool_exec(slug, args, request_ctx)
        results.put(slug, _summarize(result))
        return json.dumps(result)

    return handler


def call_tool_exec(slug: str, args: dict[str, Any], request_ctx: dict[str, Any]) -> dict[str, Any]:
    """POST the tool call to Advizr's HMAC'd tool-exec route; return SkillResult."""
    url = config.next_tool_exec_url()
    if not url:
        return {"success": False, "error": "tool-exec callback not configured"}
    body = {
        "workspaceId": request_ctx.get("workspaceId"),
        "agentId": request_ctx.get("agentId"),
        "sessionId": request_ctx.get("sessionId"),
        "runId": request_ctx.get("runId"),
        "userId": request_ctx.get("userId"),
        "userEmail": request_ctx.get("userEmail"),
        "isHeadless": bool(request_ctx.get("isHeadless")),
        "toolSlug": slug,
        "input": args,
    }
    raw = json.dumps(body, separators=(",", ":")).encode()
    sig = config.sign_body(raw, config.hermes_auth_key())
    headers = {"content-type": "application/json", config.SIGNATURE_HEADER: sig}
    try:
        resp = httpx.post(url, content=raw, headers=headers, timeout=60.0)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"tool-exec call failed: {exc}"}
    if resp.status_code != 200:
        return {"success": False, "error": f"tool-exec HTTP {resp.status_code}: {resp.text[:300]}"}
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"success": False, "error": "tool-exec returned non-JSON"}


def _summarize(result: dict[str, Any]) -> Any:
    """Compact value for the tool.call.end EngineEvent output card."""
    if not isinstance(result, dict):
        return result
    if result.get("success") is False:
        return {"error": result.get("error", "tool failed")}
    data = result.get("data", result)
    return data
