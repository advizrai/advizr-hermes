# advizr_overlay — Advizr's Hermes engine overlay

This directory is **Advizr's thin overlay** on top of the upstream
[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent). It
turns the Hermes agent loop into an HTTP/SSE service that speaks Advizr's
brain-agnostic `EngineEvent` contract, so flipping one workspace's
`agents.config.engine` to `'hermes'` swaps the chat brain with **zero UI change**
(see the TS adapter at `lib/agents/engine/hermes-engine.ts` in
`advizrai/advizr-client-template`).

## Why a separate directory

Everything Advizr-specific lives **only** under `advizr_overlay/` — a path
upstream does not have. We never edit upstream files. That means:

```bash
git fetch upstream
git rebase upstream/main      # replays cleanly: no overlay/upstream conflicts
git push origin main
```

If a rebase ever conflicts, it's a signal upstream renamed an API the overlay
imports (e.g. `AIAgent`, `run_agent`, `tools.registry.registry`, the
`stream_delta_callback` / `tool_progress_callback` contract). The overlay is
**defensive** about this: `agent_factory.build_agent` introspects
`AIAgent.__init__` and only passes kwargs it accepts, and `tool_bridge` filters
`registry.register` kwargs the same way — so a new upstream arg won't break us,
and a removed one degrades instead of crashing.

## Remotes

```
origin    git@github.com:advizrai/advizr-hermes.git      (our fork)
upstream  git@github.com:NousResearch/hermes-agent.git    (vendored upstream)
```

## What it does (and deliberately does NOT)

| Concern | Overlay behavior |
|---|---|
| Memory / persona | **Disabled.** Advizr injects memory + persona into the system prompt. `load_soul_identity=False`, `skip_memory=True`, `skip_context_files=True`. |
| Curator / session db | **Disabled.** `session_db=None` (prevents curator init + on-disk writes). |
| Messaging gateway | **Never started.** Only the FastAPI server runs — Telegram/WhatsApp/etc. stay dormant. |
| Native tools | **Not exposed.** Only the `advizr` toolset (business tools) is enabled — no terminal/file/browser execution on the container. |
| Tenancy | The service holds **only** `OPENROUTER_API_KEY`. It has **no** Supabase credential; business tools execute Next-side via the HMAC'd tool-exec callback. |
| HERMES_HOME | A fresh per-(workspace, session) temp dir per turn, removed afterward. |

## Files

| File | Role |
|---|---|
| `server.py` | FastAPI app: `POST /v1/chat` (SSE), `POST /v1/run`, `GET /health`. HMAC-verifies `x-hermes-signature`. |
| `engine_events.py` | EngineEvent builders + SSE framing (mirror of the TS `EngineEvent` union). |
| `event_bridge.py` | Hermes callbacks (worker thread) → EngineEvents on an asyncio queue. |
| `agent_factory.py` | Build a tenant-safe headless `AIAgent`; extract result text/usage. |
| `tool_bridge.py` | Register Advizr business tools; each handler HMAC-POSTs to tool-exec. |
| `config.py` | Env, HMAC sign/verify, model-slug → OpenRouter id. |
| `tests/` | SSE event-sequence, tool-bridge callback, tenancy-isolation tests. |

## Run locally

```bash
pip install -e .                      # installs hermes-agent (this fork) + fastapi/uvicorn
export OPENROUTER_API_KEY=sk-...
export HERMES_AUTH_KEY=dev-secret
export NEXT_TOOL_EXEC_URL=http://localhost:3000/api/agents/engine/tool-exec
uvicorn advizr_overlay.server:app --host 0.0.0.0 --port 8000
curl -s localhost:8000/health
pytest advizr_overlay/tests -q
```
