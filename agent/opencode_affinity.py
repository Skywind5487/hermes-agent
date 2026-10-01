"""``x-opencode-session`` — OpenCode relay session-affinity header.

OpenCode (opencode.ai Zen/Go/free relay) pins requests that share an
``x-opencode-session`` value to the same upstream backend, which is what
keeps its prompt cache warm across the turns of one conversation. The value
only has to be opaque and consistent per conversation, so it is derived the
same way as the other conversation-affinity hints Hermes already sends
(OpenRouter's sticky ``session_id``, xAI's ``x-grok-conv-id``): the ambient
conversation root first, then the physical session id — normalized through
:func:`_cache_scope_from_session_id` so cron fires of one job share a scope.

Every OpenCode request — main turn on any transport, auxiliary calls
(compression, titles, vision, MoA) — goes through
:func:`opencode_session_headers` so the header cannot drift per code path.

Ported from upstream PR #101864 (merged 2026-09-03), adapted to this checkout:
this tree predates ``hermes_cli.models.opencode_provider_family``,
``agent.anthropic_endpoints._is_opencode_endpoint`` and
``agent.portal_tags.get_affinity_scope``, so target detection and scope
resolution are inlined here instead of imported.
"""

from __future__ import annotations

import re
from typing import Any, Optional

OPENCODE_SESSION_HEADER = "x-opencode-session"

# OpenCode relay provider ids: bare family, Zen (pay-as-you-go), Go
# (subscription) and the free tier. Aliases normalize to these before reaching
# the wire (hermes_cli/models.py), but a custom provider pointed at opencode.ai
# is also a target — hence the base_url check in ``is_opencode_target``.
_OPENCODE_PROVIDER_IDS = frozenset(
    {"opencode", "opencode-zen", "opencode-go", "opencode-free"}
)

_CRON_SESSION_ID_RE = re.compile(r"^(cron_.+)_\d{8}_\d{6}$")


def _cache_scope_from_session_id(session_id: Optional[str]) -> str:
    """Normalize a physical session_id into a stable logical cache scope.

    Every non-cron session_id already identifies one conversation/agent
    instance (main run, a specific child/subagent, a sibling child, ...), so it
    is used unchanged. Only cron's per-fire timestamp needs stripping.
    """
    sid = str(session_id or "")
    match = _CRON_SESSION_ID_RE.match(sid)
    return match.group(1) if match else sid


def is_opencode_target(provider: Optional[str], base_url: Optional[str]) -> bool:
    """True when *provider* or *base_url* addresses the OpenCode relay."""
    pid = str(provider or "").strip().lower()
    if pid in _OPENCODE_PROVIDER_IDS or pid.startswith("opencode-"):
        return True
    return "opencode.ai" in str(base_url or "").lower()


def opencode_session_headers(
    provider: Optional[str],
    base_url: Optional[str],
    session_id: Optional[str] = None,
) -> dict[str, str]:
    """Return ``{"x-opencode-session": <key>}`` for OpenCode targets, else ``{}``."""
    if not is_opencode_target(provider, base_url):
        return {}
    try:
        from agent.portal_tags import get_conversation_context

        key = _cache_scope_from_session_id(get_conversation_context() or session_id)
    except Exception:
        key = str(session_id or "")
    return {OPENCODE_SESSION_HEADER: key} if key else {}


def merge_opencode_session_headers(
    kwargs: dict[str, Any],
    provider: Optional[str],
    base_url: Optional[str],
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Merge the affinity header into ``kwargs["extra_headers"]`` (in place).

    Existing per-request headers win, so a caller-pinned value is preserved.
    Non-OpenCode targets are left untouched.
    """
    headers = opencode_session_headers(provider, base_url, session_id)
    if headers:
        existing = kwargs.get("extra_headers")
        merged = dict(existing) if isinstance(existing, dict) else {}
        for key, value in headers.items():
            merged.setdefault(key, value)
        kwargs["extra_headers"] = merged
    return kwargs
