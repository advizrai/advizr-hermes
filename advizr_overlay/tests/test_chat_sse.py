"""SSE event-sequence + auth tests for POST /v1/chat.

Drives a scripted fake AIAgent (no real hermes-agent runtime needed) and asserts
the overlay emits exactly the EngineEvent sequence the TS adapter expects.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from advizr_overlay import agent_factory, server, tool_bridge
from advizr_overlay.tests.conftest import signed_post


class _FakeAgent:
    """Replays a fixed turn: text → tool call → text, then returns a result."""

    def __init__(self, stream_cb, tool_cb):
        self._stream = stream_cb
        self._tool = tool_cb

    def run_conversation(self, user_message, system_message=None, conversation_history=None):
        self._stream("Hello ")
        self._tool("reasoning.available", "_thinking", "let me check proposals", None)
        self._tool("tool.started", "get_proposals", "{}", {"status": "pending"})
        self._tool("tool.completed", "get_proposals", "ok", None)
        self._stream("world")
        return {
            "final_response": "Hello world",
            "completed": True,
            "usage": {"input_tokens": 12, "output_tokens": 7},
            "finish_reason": "stop",
        }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        agent_factory,
        "build_agent",
        lambda *, model, stream_delta_cb, tool_progress_cb: _FakeAgent(stream_delta_cb, tool_progress_cb),
    )
    # Don't touch the real (uninstalled) hermes tool registry in these tests.
    monkeypatch.setattr(tool_bridge, "register_advizr_tools", lambda specs, ctx, results: [])
    monkeypatch.setattr(tool_bridge, "deregister_advizr_tools", lambda slugs: None)
    return TestClient(server.app)


def _parse_events(text: str):
    events = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data:"):
            events.append(json.loads(frame[len("data:") :].strip()))
    return events


def _body(**over):
    base = {
        "wireVersion": "1",
        "workspaceId": "pilot",
        "sessionId": "sess-1",
        "runId": "run-1",
        "agentId": "agent-1",
        "userId": "user-1",
        "systemMessage": "You are Advizr.",
        "userMessage": "show proposals",
        "conversationHistory": [],
        "model": "claude-sonnet-4-6",
        "temperature": 0.7,
        "maxOutputTokens": 1024,
        "toolSpecs": [{"slug": "get_proposals", "description": "list", "inputSchema": {"type": "object"}}],
    }
    base.update(over)
    return base


def test_chat_streams_engine_events(client):
    resp = signed_post(client, "/v1/chat", _body())
    assert resp.status_code == 200
    events = _parse_events(resp.text)
    types = [e["type"] for e in events]

    assert types[0] == "run.start"
    assert types[-1] == "run.end"
    assert "text.delta" in types
    assert "thinking.delta" in types
    assert "tool.call.start" in types
    assert "tool.call.end" in types
    assert "usage" in types

    # Order invariant: start of a tool call precedes its end.
    assert types.index("tool.call.start") < types.index("tool.call.end")
    # The streamed text reconstructs the reply.
    streamed = "".join(e["delta"] for e in events if e["type"] == "text.delta")
    assert streamed == "Hello world"
    # Usage + clean finish.
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["inputTokens"] == 12 and usage["outputTokens"] == 7
    assert events[-1]["finishReason"] == "stop"
    # tool.call.start/end correlate by callId.
    start = next(e for e in events if e["type"] == "tool.call.start")
    end = next(e for e in events if e["type"] == "tool.call.end")
    assert start["callId"] == end["callId"]
    assert start["toolSlug"] == "get_proposals"


def test_chat_rejects_bad_signature(client):
    raw = json.dumps(_body()).encode()
    resp = client.post(
        "/v1/chat",
        content=raw,
        headers={"content-type": "application/json", "x-hermes-signature": "deadbeef"},
    )
    assert resp.status_code == 401


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
