"""advizr_overlay.engine_events — build + SSE-frame Advizr `EngineEvent`s.

These dicts mirror the TS `EngineEvent` union in lib/agents/engine/types.ts
EXACTLY (the brain-agnostic vocabulary the consumer UI renders). The TS adapter
decodes one event per `data: {json}\n\n` frame. Keep JSON compact (single line,
no embedded newlines) so the frame boundary stays a blank line.
"""

from __future__ import annotations

import json
from typing import Any


def sse_frame(event: dict[str, Any]) -> str:
    """Serialize one EngineEvent as a single-line SSE `data:` frame."""
    return "data: " + json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n\n"


# ── EngineEvent builders (names match the TS discriminated union) ─────────────


def run_start(run_id: str) -> dict[str, Any]:
    return {"type": "run.start", "runId": run_id}


def text_delta(delta: str) -> dict[str, Any]:
    return {"type": "text.delta", "delta": delta}


def thinking_delta(delta: str) -> dict[str, Any]:
    return {"type": "thinking.delta", "delta": delta}


def tool_call_start(call_id: str, tool_slug: str, tool_input: Any) -> dict[str, Any]:
    return {"type": "tool.call.start", "callId": call_id, "toolSlug": tool_slug, "input": tool_input}


def tool_call_end(
    call_id: str,
    tool_slug: str,
    output: Any = None,
    result_class: str = "success",
    duration_ms: int | None = None,
) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "type": "tool.call.end",
        "callId": call_id,
        "toolSlug": tool_slug,
        "output": output if output is not None else {},
        "resultClass": result_class,
    }
    if duration_ms is not None:
        ev["durationMs"] = duration_ms
    return ev


def tool_call_error(call_id: str, tool_slug: str, message: str) -> dict[str, Any]:
    return {"type": "tool.call.error", "callId": call_id, "toolSlug": tool_slug, "message": message}


def usage(input_tokens: int, output_tokens: int, cost_usd: float | None = None) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "type": "usage",
        "inputTokens": int(input_tokens or 0),
        "outputTokens": int(output_tokens or 0),
    }
    if cost_usd is not None:
        ev["costUsd"] = cost_usd
    return ev


def run_end(run_id: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {"type": "run.end", "runId": run_id, "finishReason": finish_reason}


def error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}
