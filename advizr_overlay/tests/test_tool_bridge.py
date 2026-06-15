"""Tool-bridge tests: the handler HMAC-POSTs to the Next.js tool-exec callback."""

from __future__ import annotations

import json

from advizr_overlay import config, tool_bridge
from advizr_overlay.result_store import ResultStore


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"success": True, "data": {"proposals": []}}
        self.text = text

    def json(self):
        return self._payload


def test_call_tool_exec_signs_and_posts(monkeypatch):
    captured = {}

    def fake_post(url, content=None, headers=None, timeout=None):
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr(tool_bridge.httpx, "post", fake_post)

    ctx = {
        "workspaceId": "pilot",
        "agentId": "agent-1",
        "sessionId": "sess-1",
        "runId": "run-1",
        "userId": "user-1",
        "isHeadless": False,
    }
    result = tool_bridge.call_tool_exec("get_proposals", {"status": "pending"}, ctx)

    assert result == {"success": True, "data": {"proposals": []}}
    assert captured["url"] == config.next_tool_exec_url()

    # Body carries the tool call + run/session attribution, NO secrets.
    body = json.loads(captured["content"])
    assert body["toolSlug"] == "get_proposals"
    assert body["input"] == {"status": "pending"}
    assert body["agentId"] == "agent-1"
    assert body["sessionId"] == "sess-1"
    serialized = json.dumps(body)
    assert "service_role" not in serialized and "SUPABASE" not in serialized

    # Signature over the EXACT bytes posted verifies under the shared key.
    sig = captured["headers"][config.SIGNATURE_HEADER]
    assert config.verify_signature(captured["content"], sig, config.hermes_auth_key())


def test_handler_records_result_and_returns_json(monkeypatch):
    monkeypatch.setattr(
        tool_bridge,
        "call_tool_exec",
        lambda slug, args, ctx: {"success": True, "data": {"count": 3}},
    )
    store = ResultStore()
    handler = tool_bridge._make_handler("get_proposals", {"agentId": "a"}, store)

    out = handler({"status": "pending"})
    assert json.loads(out) == {"success": True, "data": {"count": 3}}
    # The event bridge can later pop the summarized output for tool.call.end.
    assert store.pop("get_proposals") == {"count": 3}


def test_call_tool_exec_handles_http_error(monkeypatch):
    monkeypatch.setattr(
        tool_bridge.httpx, "post", lambda *a, **k: _FakeResponse(status_code=500, text="boom")
    )
    result = tool_bridge.call_tool_exec("get_proposals", {}, {"agentId": "a"})
    assert result["success"] is False
    assert "500" in result["error"]
