"""advizr_overlay.result_store — thread-safe FIFO of tool results per slug.

A bridged tool's handler (worker thread) records its result here; the event
bridge pops it (same thread) when Hermes fires `tool.completed`, so the
`tool.call.end` EngineEvent can carry the REAL output. FIFO-per-name correlates
concurrent calls to the same tool (mirrors the ACP adapter's tool-id FIFO).
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class ResultStore:
    def __init__(self) -> None:
        self._by_name: dict[str, deque[Any]] = {}
        self._lock = threading.Lock()

    def put(self, name: str, value: Any) -> None:
        with self._lock:
            self._by_name.setdefault(name, deque()).append(value)

    def pop(self, name: str) -> Any:
        with self._lock:
            q = self._by_name.get(name)
            if q:
                return q.popleft()
            return None
