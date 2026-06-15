"""Shared pytest fixtures for the advizr_overlay tests.

These tests do NOT require the full hermes-agent runtime: the agent build + run
is monkeypatched (`agent_factory.build_agent`) and tool registration is stubbed,
so the overlay's HTTP/SSE/bridge/auth logic is exercised in isolation.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make the repo root importable (so `import advizr_overlay` works from anywhere).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

AUTH_KEY = "test-secret-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HERMES_AUTH_KEY", AUTH_KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("NEXT_TOOL_EXEC_URL", "http://next.local/api/agents/engine/tool-exec")
    # Tenancy guarantee under test: never any Supabase credential in this process.
    for k in list(os.environ):
        if k.startswith("SUPABASE"):
            monkeypatch.delenv(k, raising=False)
    yield


def sign(raw: bytes) -> str:
    from advizr_overlay import config

    return config.sign_body(raw, AUTH_KEY)


def signed_post(client, path: str, body: dict):
    raw = json.dumps(body).encode()
    from advizr_overlay import config

    headers = {"content-type": "application/json", config.SIGNATURE_HEADER: sign(raw)}
    return client.post(path, content=raw, headers=headers)
