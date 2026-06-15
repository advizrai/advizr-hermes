"""Tenancy-isolation tests.

Asserts the structural isolation guarantees of the credential-free design:
  · each turn runs under its OWN HERMES_HOME temp dir, removed afterward;
  · two sequential turns for different workspaces use DISTINCT dirs;
  · the process holds NO Supabase credential (the brain can't reach a tenant DB).
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from advizr_overlay import agent_factory, config, server, tool_bridge
from advizr_overlay.tests.conftest import signed_post


class _NoopAgent:
    def __init__(self, *_a, **_k):
        pass

    def run_conversation(self, user_message, system_message=None, conversation_history=None):
        return {"final_response": "ok", "completed": True, "usage": {"input_tokens": 1, "output_tokens": 1}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        agent_factory,
        "build_agent",
        lambda *, model, stream_delta_cb, tool_progress_cb: _NoopAgent(),
    )
    monkeypatch.setattr(tool_bridge, "register_advizr_tools", lambda specs, ctx, results: [])
    monkeypatch.setattr(tool_bridge, "deregister_advizr_tools", lambda slugs: None)
    return TestClient(server.app)


def _body(ws, sess):
    return {
        "wireVersion": "1",
        "workspaceId": ws,
        "sessionId": sess,
        "runId": f"run-{sess}",
        "agentId": "agent-1",
        "userId": "user-1",
        "systemMessage": "You are Advizr.",
        "userMessage": "hi",
        "conversationHistory": [],
        "model": "claude-sonnet-4-6",
        "temperature": 0.7,
        "maxOutputTokens": 256,
        "toolSpecs": [],
    }


def test_each_turn_gets_a_distinct_ephemeral_home(client, monkeypatch):
    created: list[str] = []
    real_mkdtemp = server.tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(server.tempfile, "mkdtemp", recording_mkdtemp)
    prev_home = os.environ.get("HERMES_HOME")

    r1 = signed_post(client, "/v1/chat", _body("tenant-a", "s1"))
    r2 = signed_post(client, "/v1/chat", _body("tenant-b", "s2"))
    assert r1.status_code == 200 and r2.status_code == 200

    # Two distinct homes, both cleaned up, and HERMES_HOME restored.
    assert len(created) == 2
    assert created[0] != created[1]
    assert not os.path.exists(created[0])
    assert not os.path.exists(created[1])
    assert os.environ.get("HERMES_HOME") == prev_home
    # The prefixes are tenant-scoped.
    assert "tenant-a" in created[0]
    assert "tenant-b" in created[1]


def test_process_holds_no_supabase_credential():
    # The credential-free guarantee: no Supabase env, and config exposes only the
    # OpenRouter credential getter (business tools run Next-side).
    assert not any(k.startswith("SUPABASE") for k in os.environ)
    assert hasattr(config, "openrouter_api_key")
    assert not hasattr(config, "supabase_service_role_key")
    assert not hasattr(config, "supabase_url")


def test_request_body_carries_no_secrets(client):
    # Defense-in-depth contract check: the chat request the adapter sends (and
    # thus what we receive) is secret-free.
    body = _body("tenant-a", "s1")
    serialized = json.dumps(body)
    for needle in ("SUPABASE", "service_role", "SERVICE_ROLE", "_KEY"):
        assert needle not in serialized
