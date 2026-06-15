"""advizr_overlay — Advizr's thin overlay on top of NousResearch/hermes-agent.

This package lives in a directory upstream does NOT have, so it never collides
with `git rebase upstream/main`. It NEVER edits upstream files; it only imports
from them. The overlay exposes the Hermes agent loop as an HTTP/SSE service that
speaks Advizr's brain-agnostic `EngineEvent` contract (see the TS side in
`lib/agents/engine/`), with Advizr business tools bridged back into Advizr.

See ./README.md for the upstream-tracking / rebase workflow.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
