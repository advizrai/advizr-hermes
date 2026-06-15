"""advizr_overlay.agent_factory — construct a tenant-safe, headless Hermes agent.

Advizr OWNS memory + persona (already inside the system prompt we pass), so we
disable Hermes's on-disk identity/memory/curator/session persistence and the
messaging gateway. Disable flags are applied DEFENSIVELY: we introspect the real
`AIAgent.__init__` and only pass kwargs it accepts (upstream evolves), and also
set the callbacks as attributes as a belt-and-suspenders fallback.

Only the `advizr` toolset is enabled — Hermes's native terminal/file/browser
tools are NOT exposed in the consumer chat (no code execution on the container).
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from advizr_overlay import config

# Kwargs that disable on-disk state + the gateway. Names verified against
# hermes-agent; unsupported ones are silently dropped by the signature filter.
_DISABLE_KWARGS: dict[str, Any] = {
    "load_soul_identity": False,
    "skip_context_files": True,
    "skip_memory": True,
    "session_db": None,
    "quiet_mode": True,
    "enabled_toolsets": ["advizr"],
}


def _import_aiagent():
    from run_agent import AIAgent  # type: ignore

    return AIAgent


def build_agent(
    *,
    model: str | None,
    stream_delta_cb: Callable[..., None],
    tool_progress_cb: Callable[..., None],
) -> Any:
    """Build an `AIAgent` wired to OpenRouter with on-disk state disabled.

    The caller is responsible for setting a per-request `HERMES_HOME` (under the
    turn lock) before calling this, and cleaning it up after.
    """
    AIAgent = _import_aiagent()
    params = _ctor_params(AIAgent)
    has_var_kw = _has_var_kw(AIAgent)

    kwargs: dict[str, Any] = {
        "provider": "openrouter",
        "base_url": config.OPENROUTER_BASE_URL,
        "api_key": config.openrouter_api_key(),
        "model": config.resolve_model(model),
    }
    kwargs.update(_DISABLE_KWARGS)
    kwargs["stream_delta_callback"] = stream_delta_cb
    kwargs["tool_progress_callback"] = tool_progress_cb

    if not has_var_kw:
        kwargs = {k: v for k, v in kwargs.items() if k in params}

    agent = AIAgent(**kwargs)

    # Belt-and-suspenders: ensure callbacks are wired even if not ctor kwargs.
    for attr, fn in (("stream_delta_callback", stream_delta_cb), ("tool_progress_callback", tool_progress_cb)):
        try:
            setattr(agent, attr, fn)
        except Exception:  # noqa: BLE001
            pass
    return agent


def run_turn(agent: Any, *, user_message: str, system_message: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Run one Hermes turn. Returns the raw result dict (defensively shaped)."""
    # NB: do NOT also pass stream_callback here — callbacks are wired on the
    # agent, and passing the same fn twice would double-emit text deltas.
    return agent.run_conversation(
        user_message=user_message,
        system_message=system_message,
        conversation_history=history or None,
    )


# ── result extraction (tolerant to upstream key drift) ───────────────────────


def extract_text(result: dict[str, Any]) -> str:
    for key in ("final_response", "content", "text", "response"):
        v = result.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def extract_usage(result: dict[str, Any]) -> tuple[int, int]:
    u = result.get("usage") or {}
    in_tok = u.get("input_tokens") or u.get("prompt_tokens") or 0
    out_tok = u.get("output_tokens") or u.get("completion_tokens") or 0
    return int(in_tok or 0), int(out_tok or 0)


def is_failed(result: dict[str, Any]) -> bool:
    if result.get("failed"):
        return True
    if "completed" in result:
        return not bool(result.get("completed"))
    return bool(result.get("error"))


def extract_finish(result: dict[str, Any]) -> str:
    if is_failed(result):
        return "error"
    return result.get("finish_reason") or "stop"


def _ctor_params(AIAgent: Any) -> set[str]:
    try:
        return set(inspect.signature(AIAgent.__init__).parameters.keys())
    except (TypeError, ValueError):
        return set()


def _has_var_kw(AIAgent: Any) -> bool:
    try:
        return any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in inspect.signature(AIAgent.__init__).parameters.values()
        )
    except (TypeError, ValueError):
        return False
