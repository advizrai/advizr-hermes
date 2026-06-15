"""advizr_overlay.config — environment + HMAC + model resolution.

TENANCY: the only outbound credential this service holds is OPENROUTER_API_KEY.
It NEVER holds a Supabase credential — business tools execute Next-side via the
HMAC'd tool-exec callback. The absence of any SUPABASE_* var is the isolation
guarantee (asserted by tests/test_isolation.py).
"""

from __future__ import annotations

import hashlib
import hmac
import os

WIRE_VERSION = "1"
SIGNATURE_HEADER = "x-hermes-signature"

# Bare Advizr model slug → OpenRouter id (mirrors lib/ai.ts SLUG_TO_OPENROUTER).
SLUG_TO_OPENROUTER = {
    "claude-opus-4-8": "anthropic/claude-opus-4.8",
    "claude-opus-4-7": "anthropic/claude-opus-4.7",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
}

DEFAULT_MODEL = "anthropic/claude-sonnet-4.6"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def openrouter_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "")


def hermes_auth_key() -> str:
    return os.environ.get("HERMES_AUTH_KEY", "")


def next_tool_exec_url() -> str:
    """The Advizr callback the tool bridge POSTs to (e.g.
    https://advizrclients.com/<slug>/api/agents/engine/tool-exec)."""
    return os.environ.get("NEXT_TOOL_EXEC_URL", "").rstrip("/")


def resolve_model(slug: str | None) -> str:
    if not slug:
        return DEFAULT_MODEL
    return SLUG_TO_OPENROUTER.get(slug, slug)


# ── HMAC (mirrors lib/agents/engine/hermes-wire.ts sign/verify) ──────────────


def sign_body(raw: bytes, secret: str) -> str:
    """HMAC-SHA256 of the raw body bytes, hex."""
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def verify_signature(raw: bytes, signature: str | None, secret: str) -> bool:
    """Constant-time verify of the hex HMAC-SHA256 over the raw body."""
    if not signature or not secret:
        return False
    expected = sign_body(raw, secret)
    try:
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False
