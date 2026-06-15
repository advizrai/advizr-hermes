"""advizr_overlay.event_bridge — translate Hermes callbacks → EngineEvents.

Hermes drives delivery through callbacks that fire on a WORKER thread (the sync
`run_conversation` runs via `asyncio.to_thread`). This bridge captures the main
event loop and hands events across the thread boundary onto an `asyncio.Queue`
via `call_soon_threadsafe`, which the SSE generator drains in order.

Callback contract (verified against hermes-agent):
  · stream_delta_callback(delta: str | None)         → text.delta (None = EOS)
  · tool_progress_callback(event_type, name, preview, args)
        event_type ∈ {reasoning.available, tool.started, tool.completed, tool.error}
        - reasoning.available → thinking.delta(preview)   (full block, not deltas)
        - tool.started        → tool.call.start(callId, name, args)
        - tool.completed      → tool.call.end(callId, name, output=<bridged result>)
        - tool.error          → tool.call.error(callId, name, preview)
The defensive arg parsing tolerates the legacy single-string progress mode.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any

from advizr_overlay import engine_events as ev
from advizr_overlay.result_store import ResultStore

#: Enqueued as the worker thread's LAST act so the generator drains everything
#: emitted before the turn finished, in order, with no race against the future.
SENTINEL = object()


class EngineEventBridge:
    def __init__(self, loop: asyncio.AbstractEventLoop, run_id: str, results: ResultStore) -> None:
        self._loop = loop
        self._run_id = run_id
        self._results = results
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._tool_call_ids: dict[str, deque[str]] = {}
        self._counter = 0
        self._lock = threading.Lock()
        self._any_text = False

    # ── main-loop side ──────────────────────────────────────────────────────

    async def get(self) -> Any:
        return await self._queue.get()

    @property
    def any_text(self) -> bool:
        return self._any_text

    def finish(self) -> None:
        """Worker thread's last act — signal end-of-stream to the generator."""
        self._emit(SENTINEL)

    # ── worker-thread side (Hermes callbacks) ───────────────────────────────

    def stream_delta(self, delta: str | None = None, *_args: Any, **_kwargs: Any) -> None:
        if delta is None:
            return
        text = delta if isinstance(delta, str) else str(delta)
        if text == "":
            return
        self._any_text = True
        self._emit(ev.text_delta(text))

    def tool_progress(self, *args: Any, **kwargs: Any) -> None:
        # Legacy single-string progress mode — no structured event to emit.
        if len(args) < 2 and not kwargs.get("event_type"):
            return
        event_type = args[0] if args else kwargs.get("event_type")
        name = args[1] if len(args) > 1 else kwargs.get("name")
        preview = args[2] if len(args) > 2 else kwargs.get("preview")
        tool_args = args[3] if len(args) > 3 else kwargs.get("args")
        name = name or "tool"

        if event_type == "reasoning.available":
            if preview:
                self._emit(ev.thinking_delta(str(preview)))
            return
        if event_type == "tool.started":
            call_id = self._push_call_id(name)
            self._emit(ev.tool_call_start(call_id, name, tool_args or {}))
            return
        if event_type == "tool.completed":
            call_id = self._pop_call_id(name)
            output = self._results.pop(name)
            self._emit(ev.tool_call_end(call_id, name, output=output, result_class="success"))
            return
        if event_type == "tool.error":
            call_id = self._pop_call_id(name)
            self._emit(ev.tool_call_error(call_id, name, str(preview or "tool error")))
            return
        # Unknown event types are ignored (forward-compatible).

    # ── internals ───────────────────────────────────────────────────────────

    def _emit(self, item: Any) -> None:
        # Safe to call from any thread; schedules the put on the captured loop.
        self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    def _push_call_id(self, name: str) -> str:
        with self._lock:
            self._counter += 1
            call_id = f"tc-{self._counter}"
            self._tool_call_ids.setdefault(name, deque()).append(call_id)
            return call_id

    def _pop_call_id(self, name: str) -> str:
        with self._lock:
            q = self._tool_call_ids.get(name)
            if q:
                return q.popleft()
            self._counter += 1
            return f"tc-{self._counter}"
