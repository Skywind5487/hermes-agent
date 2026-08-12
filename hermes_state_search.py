"""Full-text / trigram / CJK message search and FTS maintenance for SessionDB.

Mixin contract: this is a plain mixin class consumed by
``hermes_state.SessionDB``. It defines no ``__init__`` and no state of its
own; methods access the host's attributes (``self._conn``, ``self.db_path``,
``self._execute_write`` and other SessionDB methods) established by
``SessionDB.__init__``. It must never import hermes_state (cycle) — shared
module-level constants live in hermes_state_common.
"""

import logging
import json
import os
import re
import sqlite3
import time
from typing import Any, Callable, Collection, Dict, Iterator, List, Optional, Tuple

from agent.skill_commands import describe_skill_invocation
from hermes_state_common import (
    FTS_CJK_STALE_KEY,
    FTS_SESSION_CJK_STALE_KEY,
    FTS_SQL,
    FTS_SESSION_TRIGRAM_STALE_KEY,
    FTS_STORAGE_VERSION,
    FTS_TRIGRAM_SQL,
    FTS_INDEXES,
    MAX_FTS5_QUERY_CHARS,
    SCHEMA_VERSION,
    _FTS_CJK_TRIGGERS,
    _FTS_SESSION_CJK_TRIGGERS,
    _fts_descriptor,
)

# Moved methods logged under the "hermes_state" logger before the split;
# keep that logger identity so log filtering/capture behavior is unchanged.
logger = logging.getLogger("hermes_state")


# ── Compression-lineage resolver (#68) ────────────────────────────────────
#
# One logical session-search query resolves each ranked owner candidate to
# its positive compression-continuation root with a query-local
# memo/path-compression pass.  Generic parentage is NOT lineage: only a
# child whose parent exists, whose parent ended by ``'compression'``, that is
# not a ``tool`` session, and whose branch/delegate markers do not explicitly
# point at that parent forms a compression edge.  Foreign markers pointing
# elsewhere do not disqualify the edge.
#
# Safety is orthogonal to lineage identity: a traversal-local seen-set proves
# cycles, and a global per-query work budget bounds indexed row fetches.  A
# budget-exhausted partial path is operational uncertainty, never semantic
# evidence, so it is not memoized as unresolved.
_LINEAGE_WORK_BUDGET = 2000
_UNRESOLVED = object()
_BUDGET_EXHAUSTED = object()

_LINEAGE_NODE_SQL = """
SELECT
    child.id,
    child.parent_session_id,
    child.source,
    child.model_config,
    parent.id AS parent_exists,
    parent.end_reason AS parent_end_reason
FROM sessions child
LEFT JOIN sessions parent ON parent.id = child.parent_session_id
WHERE child.id = ?
"""


def _lineage_markers(
    model_config: Any,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Return ``(config_valid, branched_from, delegate_from)`` for a session.

    Malformed / non-object ``model_config`` is treated as "no proven positive
    edge" (the conservative direction): the current node becomes its own root
    rather than traversing an unproven parent.
    """
    if model_config in (None, ""):
        return True, None, None
    value = model_config
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return False, None, None
    if not isinstance(value, dict):
        return False, None, None
    markers: List[Optional[str]] = []
    for key in ("_branched_from", "_delegate_from"):
        marker = value.get(key)
        if marker is None:
            markers.append(None)
        elif isinstance(marker, str) and marker:
            markers.append(marker)
        else:
            return False, None, None
    return True, markers[0], markers[1]


class _LineageResolutionState:
    """Per-logical-query resolver state for one winner search.

    ``work`` counts exactly one successful uncached lineage-node row fetch;
    memo hits, absent-row fetches, and local cycle checks consume nothing.
    ``memo`` maps a lineage node to a resolved root string or ``_UNRESOLVED``.
    A budget-exhausted partial path is never written to the memo.
    """

    __slots__ = (
        "budget",
        "work",
        "memo",
        "memo_hits",
        "bound_hit",
        "candidates_inspected",
        "accepted_roots",
    )

    def __init__(self, budget: int = _LINEAGE_WORK_BUDGET) -> None:
        self.budget = max(1, int(budget))
        self.work = 0
        self.memo: Dict[str, Any] = {}
        self.memo_hits = 0
        self.bound_hit = False
        self.candidates_inspected = 0
        self.accepted_roots = 0

    def _memoize_unresolved(self, path: List[str], node: str) -> None:
        """Mark *path* plus *node* as proven unresolved (zero later lookups)."""
        for visited in path:
            self.memo.setdefault(visited, _UNRESOLVED)
        self.memo.setdefault(node, _UNRESOLVED)


# Deferred-rebuild specs shared by the message and session metadata FTS
# engines. Each spec describes one external-content index family so the
# crash-safe claim / chunk / finish rules (accepted through #76832 for
# messages, #25 for sessions) are implemented ONCE in
# ``fts_rebuild_status/step`` / ``_fts_rebuild_finish`` and only the SQL,
# table, row key, and marker names differ. ``available`` /
# ``trigram_available`` are callables taking the SessionDB host (the mixin
# methods cannot read ``self._fts_enabled`` at import time).
#
# Static identity (table / source / row key / columns) is derived from the
# authoritative ``FTS_INDEXES`` registry (issue #27) instead of being
# repeated here, so membership can never drift from the lifecycle consumers;
# lane-specific state (H/P marker keys, availability callbacks, reset
# targets, finish hooks) stays in the specs.
_MESSAGES_FTS_DESC = _fts_descriptor("messages_fts")
_MESSAGES_TRIGRAM_DESC = _fts_descriptor("messages_fts_trigram")
_MESSAGES_CJK_DESC = _fts_descriptor("messages_fts_cjk")
_SESSIONS_FTS_DESC = _fts_descriptor("sessions_fts")
_SESSIONS_CJK_DESC = _fts_descriptor("sessions_fts_cjk")
_SESSIONS_TRIGRAM_DESC = _fts_descriptor("sessions_fts_trigram")

_FTS_MESSAGE_SPEC = {
    "name": "messages",
    "high_water_key": "fts_rebuild_high_water",
    "progress_key": "fts_rebuild_progress",
    "descriptor": _MESSAGES_FTS_DESC,
    "trigram_descriptor": _MESSAGES_TRIGRAM_DESC,
    "fts_table": _MESSAGES_FTS_DESC.table,
    "fts_columns": _MESSAGES_FTS_DESC.columns,
    "source_table": _MESSAGES_FTS_DESC.source,
    "source_columns": _MESSAGES_FTS_DESC.columns,
    "row_key": _MESSAGES_FTS_DESC.row_key,
    "trigram_fts": _MESSAGES_TRIGRAM_DESC.table,
    "trigram_columns": _MESSAGES_TRIGRAM_DESC.columns,
    "trigram_where": "role <> 'tool'",
    "reset_tables": (
        _MESSAGES_FTS_DESC.table,
        _MESSAGES_TRIGRAM_DESC.table,
    ),
    "available": lambda self: self._fts_enabled,
    "trigram_available": lambda self: self._trigram_available,
}

_FTS_SESSION_SPEC = {
    "name": "sessions",
    "high_water_key": "fts_session_rebuild_high_water",
    "progress_key": "fts_session_rebuild_progress",
    "descriptor": _SESSIONS_FTS_DESC,
    "fts_table": _SESSIONS_FTS_DESC.table,
    # Raw canonical values only — no normalization / synthetic concatenation
    # (normalized arbitrary infix belongs to #30).
    "fts_columns": _SESSIONS_FTS_DESC.columns,
    "source_table": _SESSIONS_FTS_DESC.source,
    "source_columns": _SESSIONS_FTS_DESC.columns,
    "row_key": _SESSIONS_FTS_DESC.row_key,
    "trigram_fts": None,
    "trigram_columns": (),
    "trigram_where": None,
    "reset_tables": (_SESSIONS_FTS_DESC.table,),
    "available": lambda self: getattr(self, "_sessions_fts_available", False),
    "trigram_available": lambda self: False,
}

# #30 normalized session trigram rebuild: its OWN marker pair and descriptor.
# ``P`` means target-specific processed completeness, so the trigram lane must
# NEVER share the Unicode lane's ``fts_session_rebuild_*`` claim — either
# target can be created / repaired / reset independently, and a shared P
# would let one target's completion falsely assert the other's.
# The source is the derived ``sessions_fts_trigram_src`` VIEW (compact title,
# raw id, compact display_name) so the chunk worker and the finish sweep read
# the same projection the live triggers do.
_FTS_SESSION_TRIGRAM_SPEC = {
    "name": "sessions_trigram",
    "high_water_key": "fts_session_trigram_rebuild_high_water",
    "progress_key": "fts_session_trigram_rebuild_progress",
    "descriptor": _SESSIONS_TRIGRAM_DESC,
    "fts_table": _SESSIONS_TRIGRAM_DESC.table,
    "fts_columns": _SESSIONS_TRIGRAM_DESC.columns,
    "source_table": _SESSIONS_TRIGRAM_DESC.source,
    "source_columns": _SESSIONS_TRIGRAM_DESC.columns,
    "row_key": _SESSIONS_TRIGRAM_DESC.row_key,
    "trigram_fts": None,
    "trigram_columns": (),
    "trigram_where": None,
    "reset_tables": (_SESSIONS_TRIGRAM_DESC.table,),
    "available": lambda self: getattr(
        self, "_sessions_trigram_available", False
    ),
    "trigram_available": lambda self: False,
}

# Optional CJK session-metadata specialization (issue #26) of the SAME
# generic rebuild engine: identical external-content raw (title, id,
# display_name) document keyed by named row_id, its OWN H/P marker pair and
# stale key (never the Unicode-session pair, never the message-CJK pair).
#
# The worker gate is ``_sessions_cjk_worker_operable`` (can this process
# build/maintain the CJK index) — NOT ``_sessions_cjk_available`` (search
# serving). The donor deadlock was one boolean doing both jobs: pending made
# serving false, the same false blocked the worker, finish never ran, search
# never became available. The #77629 invariant (optional capability gates
# finish exactly as it gates step) is honored by routing both through this
# same callback and by the availability gate in ``_fts_rebuild_finish``.
# ``finish_hook`` flips search-serving on only after the boundary sweep
# clears the CJK markers.
_FTS_SESSION_CJK_SPEC = {
    "name": "sessions_cjk",
    "high_water_key": "fts_session_cjk_rebuild_high_water",
    "progress_key": "fts_session_cjk_rebuild_progress",
    "descriptor": _SESSIONS_CJK_DESC,
    "fts_table": _SESSIONS_CJK_DESC.table,
    "fts_columns": _SESSIONS_CJK_DESC.columns,
    "source_table": _SESSIONS_CJK_DESC.source,
    "source_columns": _SESSIONS_CJK_DESC.columns,
    "row_key": _SESSIONS_CJK_DESC.row_key,
    "trigram_fts": None,
    "trigram_columns": (),
    "trigram_where": None,
    "reset_tables": (_SESSIONS_CJK_DESC.table,),
    "available": lambda self: getattr(self, "_sessions_cjk_worker_operable", False),
    "trigram_available": lambda self: False,
    "finish_hook": lambda self: self._fts_session_cjk_finish_set_serving(),
}

# Optional message-CJK rebuild lane, folded onto the SAME generic engine as
# every other lane (issue #27). Its OWN marker pair (``fts_cjk_rebuild_*`` —
# never the message Unicode pair) and its own stale breadcrumb
# (``FTS_CJK_STALE_KEY``) are preserved; only the bespoke status/step/finish
# implementations are replaced by the shared engine. The worker gate is
# ``_fts_cjk_loaded`` (this process can tokenize), and finish flips
# search-serving exactly as the pre-#27 bespoke ``_fts_cjk_rebuild_finish``
# did. The backfill reads through the ``messages_fts_cjk_src`` VIEW (the
# descriptor's canonical derived source) which already excludes tool rows.
_FTS_MESSAGE_CJK_SPEC = {
    "name": "messages_cjk",
    "high_water_key": "fts_cjk_rebuild_high_water",
    "progress_key": "fts_cjk_rebuild_progress",
    "descriptor": _MESSAGES_CJK_DESC,
    "fts_table": _MESSAGES_CJK_DESC.table,
    "fts_columns": _MESSAGES_CJK_DESC.columns,
    "source_table": _MESSAGES_CJK_DESC.source,
    "source_columns": _MESSAGES_CJK_DESC.columns,
    "row_key": _MESSAGES_CJK_DESC.row_key,
    "trigram_fts": None,
    "trigram_columns": (),
    "trigram_where": None,
    "reset_tables": (_MESSAGES_CJK_DESC.table,),
    "available": lambda self: self._fts_enabled and self._fts_cjk_loaded,
    "trigram_available": lambda self: False,
    "finish_hook": lambda self: setattr(self, "_fts_cjk_available", True),
}


# ── Shared deferred-rebuild lane iteration (issue #27) ────────────────────
# The status emitter and foreground worker loop in ``optimize_fts_storage``
# used to probe/execute each rebuild lane one-by-one (message, message-CJK,
# session Unicode, session trigram, session CJK). This ordered lane surface
# centralizes that sequencing so the loops are data-driven. It is SEPARATE
# from the ``FTS_INDEXES`` registry: H/P markers and stale breadcrumbs are
# lane-specific state that lives in the rebuild specs, not in the index
# descriptor. ``stale_key`` (when present) names the lane's durable stale
# breadcrumb for the pending-work probe; ``settlement_reason`` is the
# storage-v2 refusal label for the lane (issue #31).
_FTS_REBUILD_LANES: Tuple[Dict[str, Any], ...] = (
    {
        "spec": _FTS_MESSAGE_SPEC,
        "stale_key": None,
        "status": lambda self: self.fts_rebuild_status(),
        "step": lambda self: self.fts_rebuild_step(),
    },
    {
        "spec": _FTS_MESSAGE_CJK_SPEC,
        "stale_key": FTS_CJK_STALE_KEY,
        "settlement_reason": "message_cjk_incomplete",
        "status": lambda self: self.fts_cjk_rebuild_status(),
        "step": lambda self: self.fts_cjk_rebuild_step(),
    },
    {
        "spec": _FTS_SESSION_SPEC,
        "stale_key": None,
        "settlement_reason": "session_unicode_incomplete",
        "status": lambda self: self.fts_session_rebuild_status(),
        "step": lambda self: self.fts_session_rebuild_step(),
    },
    {
        "spec": _FTS_SESSION_TRIGRAM_SPEC,
        "stale_key": FTS_SESSION_TRIGRAM_STALE_KEY,
        "settlement_reason": "session_trigram_incomplete",
        "status": lambda self: self.fts_session_trigram_rebuild_status(),
        "step": lambda self: self.fts_session_trigram_rebuild_step(),
    },
    {
        "spec": _FTS_SESSION_CJK_SPEC,
        "stale_key": FTS_SESSION_CJK_STALE_KEY,
        "settlement_reason": "session_cjk_incomplete",
        "status": lambda self: self.fts_session_cjk_rebuild_status(),
        "step": lambda self: self.fts_session_cjk_rebuild_step(),
    },
)


class SessionSearchMixin:
    """See module docstring — mixin for SessionDB (Search cluster)."""

    _SEARCH_MESSAGE_RESULT_FIELDS = (
        "id",
        "session_id",
        "role",
        "snippet",
        "timestamp",
        "tool_name",
        "source",
        "model",
        "session_started",
        "context",
    )

    @classmethod
    def _search_message_fields(
        cls, fields: Optional[Collection[str]]
    ) -> Optional[Tuple[str, ...]]:
        """Validate and canonically order an optional result projection."""
        if fields is None:
            return None
        if isinstance(fields, str):
            raise TypeError("search fields must be a collection of field names, not a string")
        requested = set(fields)
        unknown = requested.difference(cls._SEARCH_MESSAGE_RESULT_FIELDS)
        if unknown:
            raise ValueError(f"unknown search result field(s): {', '.join(sorted(unknown))}")
        return tuple(
            field for field in cls._SEARCH_MESSAGE_RESULT_FIELDS if field in requested
        )

    def _try_incremental_merge_fts(self) -> None:
        """Run one bounded FTS5 merge pass without failing the completed write."""
        if not self._fts_enabled:
            return
        try:
            self._merge_fts_incrementally(
                max_pages=self._FTS_MERGE_MAX_PAGES_PER_INDEX
            )
        except sqlite3.Error as exc:
            # Routine maintenance is best effort, but unexpected SQLite errors
            # must remain visible instead of being silently mistaken for an
            # optional missing index.
            logger.warning("FTS incremental merge failed: %s", exc)

    def _fts_rebuild_pause(self, chunk_seconds: float) -> None:
        """Inter-chunk throttle shared by every deferred FTS rebuild loop.

        Extracted from ``optimize_fts_storage``'s nested closure so both the
        message and the session metadata (issue #25) rebuilds route through
        ONE monkeypatchable helper. The duty cycle is what keeps a live
        gateway/CLI process sharing the DB responsive: without it, back-to-
        back BEGIN IMMEDIATE chunks starve concurrent writers out of their
        lock retries (the measured ~85% write-lock ownership that froze
        concurrent sessions). No session-specific copy of ``500`` / ``4.0`` /
        ``0.2`` or this formula is introduced.
        """
        time.sleep(max(
            self._FTS_REBUILD_MIN_PAUSE,
            chunk_seconds * self._FTS_REBUILD_DUTY_FACTOR,
        ))

    def fts_rebuild_status(
        self, spec: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Return deferred-rebuild progress, or None when no rebuild pending.

        Shape: {"pending": True, "total": <rows at drop time>,
        "indexed": <rows backfilled>, "percent": <0-100 int>}.
        Consumed by search_messages() notes and by status surfaces
        (dashboard/desktop can poll this to render a progress indicator).

        ``spec`` selects the marker pair: the message rebuild by default, or
        the session Unicode metadata rebuild (issue #25). Reads state_meta
        directly via _read_ctx instead of calling get_meta() (which takes
        self._lock) so search_messages doesn't block on the writer lock when
        checking rebuild status.
        """
        spec = spec or _FTS_MESSAGE_SPEC
        with self._read_ctx() as conn:
            row = conn.execute(
                "SELECT key, value FROM state_meta WHERE key IN (?, ?)",
                (spec["high_water_key"], spec["progress_key"]),
            ).fetchall()
        meta = {r["key"]: r["value"] for r in row}
        high_water = meta.get(spec["high_water_key"])
        if high_water is None:
            return None
        progress = int(meta.get(spec["progress_key"]) or 0)
        total = int(high_water)
        if total <= 0:
            return None
        pct = min(100, int(100 * progress / total))
        return {"pending": True, "total": total, "indexed": progress, "percent": pct}

    def _fts_lane_durable_keys(self, lane: Dict[str, Any]) -> Tuple[str, ...]:
        """All durable settlement-relevant ``state_meta`` keys for one
        deferred rebuild lane: the H/P marker pair plus the lane's stale
        breadcrumb when it has one. Membership comes from
        ``_FTS_REBUILD_LANES`` (issue #31) — never a hand-maintained ladder.
        """
        spec = lane["spec"]
        keys = [spec["high_water_key"], spec["progress_key"]]
        stale = lane.get("stale_key")
        if stale:
            keys.append(stale)
        return tuple(keys)

    def _fts_meta_has_any(self, conn, keys: Collection[str]) -> bool:
        """True when any of *keys* is present in ``state_meta`` (SELECT-only)."""
        placeholders = ", ".join("?" for _ in keys)
        row = conn.execute(
            f"SELECT 1 FROM state_meta WHERE key IN ({placeholders}) LIMIT 1",
            tuple(keys),
        ).fetchone()
        return row is not None

    def _fts_lane_actionable(self, lane: Dict[str, Any]) -> bool:
        """True when THIS process can operate the lane's ordinary optimize /
        repair work — the lane's own worker gate, used ONLY for the
        storage-v2 "actionable here" flag, never as proof of DB acceptance
        completeness (issue #31)."""
        return bool(lane["spec"]["available"](self))

    def _fts_storage_v2_blockers(self, conn) -> Iterator[Tuple[str, bool]]:
        """Yield EVERY storage-v2 settlement blocker ``(reason,
        actionable_here)`` in evaluation order (issue #31).

        SELECT-only and durable/schema-aware: every check reads
        ``sqlite_master`` or ``state_meta`` — never process-local serving
        booleans as evidence of DB completeness. ``actionable_here`` says
        whether THIS process can resolve that one blocker via
        optimize-storage; because the shared worker skips lanes it cannot
        operate and keeps going, the global "is there runnable work" question
        is ANY(actionable) across the whole set — never the first blocker's
        flag alone.

        ``conn`` must be a connection the caller already holds under a
        suitable lock/read context (this helper does NOT take ``self._lock``,
        which is non-reentrant).
        """
        if not getattr(self, "_fts_enabled", False):
            yield ("fts5_unavailable", False)
            return

        # ── Required message base ──
        if self._db_has_legacy_inline_fts(conn):
            yield ("legacy_inline", True)
            return
        if self._has_fts_trash(conn):
            yield ("teardown_incomplete", True)
            return
        if self._fts_external_index_empty_with_messages(conn):
            yield ("backfill_incomplete", True)
            return
        if self._fts_meta_has_any(
            conn,
            (_FTS_MESSAGE_SPEC["high_water_key"], _FTS_MESSAGE_SPEC["progress_key"]),
        ):
            yield ("backfill_incomplete", True)
            return

        # ── Optional / session deferred lanes: durable H/P/stale state ──
        # Any H, P, or stale breadcrumb is non-settled durable state — even
        # on a host that cannot operate the lane (missing capability is never
        # evidence of completion). Membership is ``_FTS_REBUILD_LANES``.
        for lane in _FTS_REBUILD_LANES[1:]:
            if self._fts_meta_has_any(conn, self._fts_lane_durable_keys(lane)):
                yield (lane["settlement_reason"], self._fts_lane_actionable(lane))

        # ── Optional / session structural (schema-identity) states ──
        # message CJK orphan-empty (optional; an absent table is valid
        # absence, and an empty source is nothing to index).
        if self._fts_external_index_empty_with_source(
            conn, "messages_fts_cjk_src", "messages_fts_cjk"
        ):
            yield ("message_cjk_orphan_empty", self._fts_cjk_loaded)
        # session Unicode legacy/internal shape or orphan-empty.
        if self._db_has_internal_content_sessions_fts(conn):
            yield ("session_unicode_legacy", True)
        if self._fts_external_index_empty_with_source(
            conn, "sessions", "sessions_fts"
        ):
            yield ("session_unicode_orphan_empty", True)
        # session CJK legacy/internal shape or orphan-empty (optional).
        if self._db_has_internal_content_sessions_fts_cjk(conn):
            yield ("session_cjk_legacy", self._sessions_cjk_worker_operable)
        if self._fts_external_index_empty_with_source(
            conn, "sessions", "sessions_fts_cjk"
        ):
            yield ("session_cjk_orphan_empty", self._sessions_cjk_worker_operable)
        # session trigram structural (#30 fail-closed).
        trigram = self._fts_session_trigram_settlement_blocker(conn)
        if trigram is not None:
            yield trigram

    def _fts_storage_v2_blocker(self, conn) -> Optional[Tuple[str, bool]]:
        """Return the FIRST storage-v2 settlement blocker ``(reason,
        actionable_here)``, or None when the database is acceptance-complete
        for ``fts_storage_version = 2`` (issue #31).

        None is the single proof that v2 may be claimed. The returned
        ``actionable_here`` describes ONLY this first blocker; callers that
        need "is any work runnable on this host" must scan the whole blocker
        set via ``_fts_storage_v2_blockers`` (e.g. ``fts_optimize_available``),
        because an earlier incapable blocker can shadow a later actionable one
        while the shared worker still makes progress. The SAME evaluator
        drives startup auto-settlement, the foreground pre-VACUUM refusal,
        and the final transactional stamp, so no completion decision can
        diverge.

        ``conn`` must be a connection the caller already holds under a
        suitable lock/read context (this helper does NOT take ``self._lock``,
        which is non-reentrant).
        """
        return next(self._fts_storage_v2_blockers(conn), None)

    def _fts_session_trigram_settlement_blocker(
        self, conn
    ) -> Optional[Tuple[str, bool]]:
        """Storage-v2 structural refusal for the #30 normalized session
        trigram lane (issue #31): an ``unknown_same_name`` object or any
        noncanonical root/source/trigger ownership fails closed — v2 is
        refused and the object is left untouched (never deleted or demoted
        inside #31)."""
        classification = self._classify_sessions_fts_trigram(conn)
        if classification == "unknown_same_name":
            return ("session_trigram_unknown_same_name", False)
        _owned, foreign, missing = self._sessions_trigram_namespace_owned(
            conn, classification
        )
        if foreign:
            return ("session_trigram_namespace_foreign", False)
        if classification == "absent":
            if not self._sessions_trigram_src_compatible(conn):
                return ("session_trigram_source_collision", False)
            return None
        # modern_trigram: an incomplete exact trigger set is a fail-closed
        # state the #30 lifecycle cannot create — refuse without repair.
        if missing:
            return ("session_trigram_trigger_incomplete", False)
        if self._fts_external_index_empty_with_source(
            conn, "sessions_fts_trigram_src", "sessions_fts_trigram"
        ):
            return ("session_trigram_orphan_empty", self._sessions_trigram_available)
        return None

    def _fts_storage_v2_withdraw_claim(self, conn) -> None:
        """Withdraw any stale storage-layout claim (issue #31): a blocker
        found by the settlement evaluator means the DB is not
        acceptance-complete, so an existing ``fts_storage_version`` must not
        keep advertising completion. DML on the caller's connection (a cursor
        works too)."""
        conn.execute("DELETE FROM state_meta WHERE key = 'fts_storage_version'")

    def _fts_first_pending_lane_status(self) -> Optional[Dict[str, Any]]:
        """First non-None deferred-rebuild status across the ordered lanes
        (issue #27) — the shared surface for progress/status emission, so the
        emitter no longer hard-codes each lane one-by-one."""
        for lane in _FTS_REBUILD_LANES:
            status = lane["status"](self)
            if status is not None:
                return status
        return None

    def _fts_run_pending_lane_steps(
        self, on_chunk: Optional[Callable[[], None]] = None
    ) -> None:
        """Run one full pass of every deferred-rebuild lane with pending work
        (issue #27), using the shared inter-chunk pacing.

        Replaces the hard-coded per-lane phase ladders in
        ``optimize_fts_storage``; each lane's step loop only starts when its
        status reports pending work, so a DB with no work for a lane never
        enters (and never trips a monkeypatched step). ``on_chunk`` (if any)
        is invoked after each chunk so progress emission is preserved. H/P
        state stays lane-specific — this surface only sequences the lanes.
        """
        for lane in _FTS_REBUILD_LANES:
            if lane["status"](self) is None:
                continue
            while True:
                _t0 = time.monotonic()
                if not lane["step"](self):
                    break
                if on_chunk is not None:
                    on_chunk()
                self._fts_rebuild_pause(time.monotonic() - _t0)

    def _fts_rebuild_finish(self, spec: Optional[Dict[str, Any]] = None) -> None:
        """Finalize a deferred rebuild: boundary sweep + clear markers.

        The sweep is cheap insurance against any write that slipped through
        the migration-boundary instant (between high_water capture and
        trigger activation): re-index any row near the boundary that the
        index is missing. docsize has one row per indexed doc, so the
        anti-join is exact and runs on a narrow id range.

        The trigram half of the sweep is gated on the spec's availability
        probe for the same reason ``fts_rebuild_step()`` gates its backfill
        INSERT: when the SQLite build has no trigram tokenizer (or the table
        was never created), an unconditional INSERT raises ``no such table``
        and aborts the whole rebuild — taking ``optimize_fts_storage()``
        down with it.
        """
        spec = spec or _FTS_MESSAGE_SPEC
        # #77629 invariant: the SAME optional operability capability that
        # gates the chunked step must gate finish. A host that lost the
        # capability (e.g. a CJK tokenizer it could load at open) must not
        # enter a finish path that falsely settles durable work.
        if not spec["available"](self):
            return
        include_trigram = bool(
            spec.get("trigram_fts") and spec["trigram_available"](self)
        )
        fts_table = spec["fts_table"]
        source_table = spec["source_table"]
        row_key = spec["row_key"]
        fts_cols = ", ".join(spec["fts_columns"])
        src_cols = ", ".join(spec["source_columns"])

        def _do(conn):
            hw_row = conn.execute(
                "SELECT value FROM state_meta WHERE key = ?",
                (spec["high_water_key"],),
            ).fetchone()
            if hw_row is not None:
                hw = int(hw_row[0])
                # Sweep a generous window around the boundary.
                lo, hi = hw - 1000, hw + 1000
                conn.execute(
                    f"INSERT INTO {fts_table}(rowid, {fts_cols}) "
                    f"SELECT {source_table}.{row_key}, {src_cols} "
                    f"FROM {source_table} "
                    f"WHERE {source_table}.{row_key} > ? "
                    f"AND {source_table}.{row_key} <= ? "
                    f"AND NOT EXISTS (SELECT 1 FROM {fts_table}_docsize d "
                    f"                WHERE d.id = {source_table}.{row_key})",
                    (lo, hi),
                )
                trigram = spec.get("trigram_fts")
                if trigram and include_trigram:
                    trigram_cols = ", ".join(spec["trigram_columns"])
                    conn.execute(
                        f"INSERT INTO {trigram}(rowid, {trigram_cols}) "
                        f"SELECT {source_table}.{row_key}, {src_cols} "
                        f"FROM {source_table} "
                        f"WHERE {source_table}.{row_key} > ? "
                        f"AND {source_table}.{row_key} <= ? "
                        f"AND {spec['trigram_where']} "
                        f"AND NOT EXISTS (SELECT 1 FROM {trigram}_docsize d "
                        f"                WHERE d.id = {source_table}.{row_key})",
                        (lo, hi),
                    )
            conn.execute(
                "DELETE FROM state_meta WHERE key IN (?, ?)",
                (spec["high_water_key"], spec["progress_key"]),
            )
        self._execute_write(_do)
        # Optional per-spec post-finish transition (e.g. flip session-CJK
        # search-serving availability once the boundary sweep has settled).
        hook = spec.get("finish_hook")
        if hook is not None:
            hook(self)
        logger.info(
            "Deferred %s FTS rebuild complete — all rows indexed.", spec["name"]
        )

    def _fts_teardown_trash_step(self) -> bool:
        """Tear down one chunk of a demoted v22 FTS shadow table.

        The trash tables are PLAIN tables (their vtable parent was demoted
        away during the migration), so chunked DELETE + final DROP involve
        no FTS5 machinery at all. Returns True while teardown work remains.
        """
        with self._lock:
            trash = [
                r[0] for r in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name LIKE ? ESCAPE '\\'",
                    (self._FTS_TRASH_PREFIX.replace("_", "\\_") + "%",),
                ).fetchall()
            ]
        if not trash:
            return False

        tbl = trash[0]

        def _do(conn):
            pk_cols = [
                r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")
                if r[5] > 0
            ]
            key = ", ".join(pk_cols) if pk_cols else "rowid"
            cur = conn.execute(
                f"DELETE FROM {tbl} WHERE ({key}) IN "
                f"(SELECT {key} FROM {tbl} LIMIT {self._FTS_REBUILD_CHUNK_ROWS})"
            )
            if cur.rowcount == 0:
                # Empty — the DROP is cheap now.
                conn.execute(f"DROP TABLE IF EXISTS {tbl}")
                logger.info("Old FTS shadow table %s torn down.", tbl)
            return True  # re-check: more trash tables / chunks may remain

        try:
            return bool(self._execute_write(_do))
        except sqlite3.OperationalError as exc:
            logger.debug("FTS trash teardown chunk failed (will retry): %s", exc)
            return True

    def fts_rebuild_step(self, spec: Optional[Dict[str, Any]] = None) -> bool:
        """Backfill one chunk of a deferred FTS rebuild.

        Returns True when more work remains, False when the rebuild is
        complete (or none is pending). Safe to call from any process at any
        time; chunks are claimed atomically inside the write transaction, so
        concurrent callers interleave instead of duplicating rows.

        ``spec`` selects the rebuild: messages by default, or the session
        Unicode metadata rebuild (issue #25) via ``fts_session_rebuild_step``.
        """
        spec = spec or _FTS_MESSAGE_SPEC
        if not spec["available"](self):
            return False
        high_water_raw = self.get_meta(spec["high_water_key"])
        if high_water_raw is None:
            return False
        high_water = int(high_water_raw)
        chunk = self._FTS_REBUILD_CHUNK_ROWS
        fts_table = spec["fts_table"]
        source_table = spec["source_table"]
        row_key = spec["row_key"]
        fts_cols = ", ".join(spec["fts_columns"])
        src_cols = ", ".join(spec["source_columns"])
        include_trigram = bool(
            spec.get("trigram_fts") and spec["trigram_available"](self)
        )

        def _do(conn):
            # Re-read progress inside the write transaction (BEGIN IMMEDIATE
            # is already held by _execute_write) — this is the claim: two
            # workers can't read the same progress value concurrently.
            row = conn.execute(
                "SELECT value FROM state_meta WHERE key = ?",
                (spec["progress_key"],),
            ).fetchone()
            if row is None:
                return False  # finished (or cleared) by another process
            progress = int(row[0])
            if progress >= high_water:
                return False

            # The chunk upper bound is an id, not a row count, so gaps from
            # deleted rows don't shrink chunks below the claimed range.
            upper = min(progress + chunk, high_water)
            conn.execute(
                f"INSERT INTO {fts_table}(rowid, {fts_cols}) "
                f"SELECT {row_key}, {src_cols} FROM {source_table} "
                f"WHERE {row_key} > ? AND {row_key} <= ?",
                (progress, upper),
            )
            trigram = spec.get("trigram_fts")
            if trigram and include_trigram:
                trigram_cols = ", ".join(spec["trigram_columns"])
                conn.execute(
                    f"INSERT INTO {trigram}(rowid, {trigram_cols}) "
                    f"SELECT {row_key}, {src_cols} FROM {source_table} "
                    f"WHERE {row_key} > ? AND {row_key} <= ? "
                    f"AND {spec['trigram_where']}",
                    (progress, upper),
                )
            # Publish progress in the same transaction as the rows it
            # covers — crash-atomic: either both land or neither does.
            conn.execute(
                "UPDATE state_meta SET value = ? WHERE key = ?",
                (str(upper), spec["progress_key"]),
            )
            return upper < high_water

        try:
            more = self._execute_write(_do)
        except sqlite3.OperationalError as exc:
            logger.debug(
                "%s FTS rebuild chunk failed (will retry): %s", spec["name"], exc
            )
            return True  # transient (lock contention) — caller retries
        if more is False:
            status = self.fts_rebuild_status(spec)
            if status is not None and status["indexed"] >= status["total"]:
                self._fts_rebuild_finish(spec)
            return False
        return bool(more)

    def fts_session_rebuild_status(self) -> Optional[Dict[str, Any]]:
        """Session Unicode metadata index backfill progress, or None when none
        is pending (issue #25)."""
        return self.fts_rebuild_status(spec=_FTS_SESSION_SPEC)

    def fts_session_rebuild_step(self) -> bool:
        """Backfill one chunk of the session Unicode metadata index (issue #25).

        True while work remains. Shares the crash-safe chunk claim / atomic
        progress / finish rules with the message rebuild — only the source
        table, row key, columns, and marker names differ.
        """
        return self.fts_rebuild_step(spec=_FTS_SESSION_SPEC)

    def fts_session_trigram_rebuild_status(self) -> Optional[Dict[str, Any]]:
        """Session normalized trigram metadata index backfill progress, or
        None when none is pending (issue #30). None also when the lane is
        durably stale/quarantined (round-10 finding 4): a stale lane must not
        advertise a rebuild that would replay onto an unindexed window."""
        with self._read_ctx() as conn:
            stale = self._session_trigram_is_stale(conn)
        if stale:
            return None
        return self.fts_rebuild_status(spec=_FTS_SESSION_TRIGRAM_SPEC)

    def fts_session_trigram_rebuild_step(self) -> bool:
        """Backfill one chunk of the session normalized trigram index (#30).

        True while work remains. Shares the crash-safe chunk claim / atomic
        progress / finish rules with the message + Unicode session rebuilds —
        only the source VIEW, row key, columns, and its OWN marker names
        differ. No second scheduler, pacing loop, missing-progress algorithm,
        or finish algorithm is introduced. False when the lane is durably
        stale — only the dedicated stale-recovery path may reset a stale
        target (round-10 finding 4)."""
        with self._read_ctx() as conn:
            stale = self._session_trigram_is_stale(conn)
        if stale:
            return False
        return self.fts_rebuild_step(spec=_FTS_SESSION_TRIGRAM_SPEC)

    def fts_session_cjk_rebuild_status(self) -> Optional[Dict[str, Any]]:
        """Session CJK metadata index backfill progress, or None when none is
        pending (issue #26)."""
        return self.fts_rebuild_status(spec=_FTS_SESSION_CJK_SPEC)

    def fts_session_cjk_rebuild_step(self) -> bool:
        """Backfill one chunk of the session CJK metadata index (issue #26).

        True while work remains. Shares the generic chunk / finish / crash
        rules with the Unicode session and message rebuilds. The worker gate
        is **worker operability** (``_sessions_cjk_worker_operable``), never
        search-serving availability — a pending CJK backfill (W=1, S=0) must
        still advance, run finish, and only then flip to serving.
        """
        return self.fts_rebuild_step(spec=_FTS_SESSION_CJK_SPEC)

    def _fts_session_cjk_finish_set_serving(self) -> None:
        """Flip session-CJK search-serving on after finish, unless the index
        is stale (issue #26).

        #77629/#26 invariant: only a successful boundary-sweep finish that
        also finds no stale breadcrumb makes the index search-serving. An
        incapable host may have persisted ``fts_session_cjk_stale`` and
        dropped the CJK triggers mid-rebuild, leaving a gap of unknown
        extent — that index is never served until a capable host resets and
        rebuilds.
        """
        with self._read_ctx() as conn:
            stale = conn.execute(
                "SELECT 1 FROM state_meta WHERE key = ?",
                (FTS_SESSION_CJK_STALE_KEY,),
            ).fetchone()
        self._sessions_cjk_available = stale is None

    def fts_cjk_rebuild_status(self) -> Optional[Dict[str, Any]]:
        """CJK-index backfill progress, or None when none is pending.

        Delegates to the shared deferred-rebuild engine (issue #27) with the
        message-CJK lane spec; the H/P marker pair and status shape are
        unchanged.
        """
        return self.fts_rebuild_status(spec=_FTS_MESSAGE_CJK_SPEC)

    def fts_cjk_rebuild_step(self) -> bool:
        """Backfill one chunk of the CJK index. True while work remains.

        Delegates to the shared deferred-rebuild engine (issue #27); the
        message-CJK H/P marker pair, id-gated chunk SQL, and crash-safe claim
        semantics are unchanged (the chunk now reads through the
        ``messages_fts_cjk_src`` VIEW, which already excludes tool rows).
        """
        return self.fts_rebuild_step(spec=_FTS_MESSAGE_CJK_SPEC)

    def _fts_cjk_rebuild_finish(self) -> None:
        """Boundary sweep + clear the cjk markers; index becomes servable.

        Delegates to the shared deferred-rebuild finish (issue #27); the
        finish hook flips ``_fts_cjk_available`` exactly as the pre-#27
        bespoke implementation did.
        """
        self._fts_rebuild_finish(spec=_FTS_MESSAGE_CJK_SPEC)

    def _fts_reset_stale_cjk_surface(
        self,
        *,
        stale_key: str,
        trigger_tuple: Collection[str],
        drop_tables: Collection[str],
        drop_views: Collection[str] = (),
        meta_keys: Collection[str],
        recreate: Callable[[], None],
    ) -> None:
        """Drop-and-recreate a stale optional CJK index from scratch, shared
        by the message (``_fts_cjk_reset_if_stale``) and session
        (``_fts_session_cjk_reset_if_stale``) CJK surfaces.

        A stale index (its triggers were dropped by a tokenizer-less host) has
        a gap of unknown extent, so the only safe recovery is a from-scratch
        rebuild: drop the table + triggers, clear the stale breadcrumb and any
        H/P, then let ``recreate`` re-ensure the surface (which sets fresh
        backfill markers on a populated DB). No-op when not stale or not
        tokenizer-capable.
        """
        if not self._fts_cjk_loaded:
            return

        def _do(conn):
            stale = conn.execute(
                "SELECT 1 FROM state_meta WHERE key = ?", (stale_key,),
            ).fetchone()
            if not stale:
                return False
            for trig in trigger_tuple:
                conn.execute(f"DROP TRIGGER IF EXISTS {trig}")
            for tbl in drop_tables:
                conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            for view in drop_views:
                conn.execute(f"DROP VIEW IF EXISTS {view}")
            ph = ", ".join("?" for _ in meta_keys)
            conn.execute(
                f"DELETE FROM state_meta WHERE key IN ({ph})", tuple(meta_keys),
            )
            return True

        was_stale = self._execute_write(_do)
        if was_stale:
            recreate()

    def _fts_cjk_reset_if_stale(self) -> None:
        """Rebuild path for a stale message CJK index (triggers were dropped).
        See ``_fts_reset_stale_cjk_surface``."""
        def _recreate():
            # Recreate outside the write transaction — _ensure_fts_cjk_schema
            # uses executescript(), which implicitly commits any pending
            # transaction and must not run inside _execute_write's BEGIN
            # IMMEDIATE. Sets fresh backfill markers on a populated DB.
            with self._lock:
                self._ensure_fts_cjk_schema(self._conn)
                self._conn.commit()

        self._fts_reset_stale_cjk_surface(
            stale_key=FTS_CJK_STALE_KEY,
            trigger_tuple=_FTS_CJK_TRIGGERS,
            drop_tables=("messages_fts_cjk",),
            drop_views=("messages_fts_cjk_src",),
            meta_keys=(
                FTS_CJK_STALE_KEY,
                "fts_cjk_rebuild_high_water",
                "fts_cjk_rebuild_progress",
            ),
            recreate=_recreate,
        )

    def _fts_session_cjk_reset_if_stale(self) -> None:
        """Rebuild path for a stale session-CJK index (triggers were dropped
        by a tokenizer-less host, issue #26). See
        ``_fts_reset_stale_cjk_surface``."""
        def _recreate():
            # Recreate OUTSIDE the write transaction and OUTSIDE self._lock:
            # _ensure_sessions_fts_cjk_schema manages its own locking (its
            # crash-atomic transition uses _execute_write, and self._lock is a
            # plain non-reentrant Lock). Sets fresh CJK-session markers on a
            # populated DB.
            self._ensure_sessions_fts_cjk_schema(self._conn)
            self._conn.commit()

        self._fts_reset_stale_cjk_surface(
            stale_key=FTS_SESSION_CJK_STALE_KEY,
            trigger_tuple=_FTS_SESSION_CJK_TRIGGERS,
            drop_tables=(
                "sessions_fts_cjk", "sessions_fts_cjk_data",
                "sessions_fts_cjk_idx", "sessions_fts_cjk_content",
                "sessions_fts_cjk_docsize", "sessions_fts_cjk_config",
            ),
            meta_keys=(
                FTS_SESSION_CJK_STALE_KEY,
                "fts_session_cjk_rebuild_high_water",
                "fts_session_cjk_rebuild_progress",
            ),
            recreate=_recreate,
        )

    def _fts_external_index_empty_with_source(
        self, conn, source_table: str, fts_table: str
    ) -> bool:
        """True when an external-content FTS table exists but indexes nothing
        while its canonical source table has rows. Caller must hold
        ``self._lock``.

        This is the post-demote / crash-window empty-index shape: external-
        content FTS with zero ``<fts>_docsize`` rows against a non-empty
        source table. Healthy installs (and mid-backfill installs that still
        hold markers) never match.
        """
        try:
            has_src = conn.execute(
                f"SELECT EXISTS(SELECT 1 FROM {source_table})"
            ).fetchone()[0]
            if not has_src:
                return False
            # docsize is the authoritative "is this rowid indexed" surface for
            # external-content FTS5; probing the virtual table itself is
            # not reliable across SQLite builds. EXISTS instead of COUNT(*):
            # this runs on every writable open via the _init_schema stamp
            # condition, and COUNT(*) is a full b-tree scan (~100ms on a
            # 2M-row table) while EXISTS is O(1).
            has_fts = conn.execute(
                f"SELECT EXISTS(SELECT 1 FROM {fts_table}_docsize)"
            ).fetchone()[0]
            return not has_fts
        except sqlite3.OperationalError:
            # Table absent / FTS disabled mid-init — not this failure class.
            return False

    def _fts_external_index_empty_with_messages(self, conn) -> bool:
        """Message-index variant of ``_fts_external_index_empty_with_source``."""
        return self._fts_external_index_empty_with_source(
            conn, "messages", "messages_fts"
        )

    def _fts_index_known_empty(
        self, conn, fts_table: str = "messages_fts"
    ) -> bool:
        """True when an external-content index holds no rows.

        A missing table counts as empty: the schema ensure that follows
        creates it fresh.
        """
        try:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {fts_table}_docsize"
            ).fetchone()[0]
            return int(n) == 0
        except sqlite3.OperationalError:
            return True

    def _reset_fts_index_to_empty(
        self, conn, tables: Optional[Collection[str]] = None
    ) -> None:
        """Delete every indexed row from the given external-content tables.

        Uses the FTS5 ``'delete-all'`` special command — the documented O(1)
        truncate for external-content tables. A plain no-WHERE ``DELETE`` is
        O(rows) on external-content FTS5 (each row's delete tokens are
        regenerated from the content table; measured ~12µs/row, minutes on a
        large index, while holding the write lock) and corrupts the index if
        indexed rows have diverged from the canonical source table — precisely
        the broken-bookkeeping shape this repair path handles. The backfill
        chunk worker replays its whole selected id range with no anti-join, so
        a replay from zero is only safe once the index is known empty — this
        is how a partially indexed DB gets there.
        """
        for tbl in tables or ("messages_fts", "messages_fts_trigram"):
            try:
                conn.execute(f"INSERT INTO {tbl}({tbl}) VALUES('delete-all')")
            except sqlite3.OperationalError:
                pass  # table absent — already an empty surface

    def _seed_fts_rebuild_markers(
        self, conn, spec: Optional[Dict[str, Any]] = None, *, force: bool = False
    ) -> int:
        """Write a rebuild's high_water / progress keys for a full backfill.
        Returns the high-water id.

        When ``force`` is False and high_water is already set, only repairs a
        missing progress key (stuck no-op when high_water exists alone), and
        only after the index is known empty: the chunk worker replays its
        whole selected id range without an anti-join, so a partially indexed
        DB is first reset to a known-empty surface rather than rebuilt from
        zero on top of surviving rows. Caller must hold the write
        transaction / lock as appropriate.
        """
        spec = spec or _FTS_MESSAGE_SPEC
        existing_hw = conn.execute(
            "SELECT value FROM state_meta WHERE key = ?",
            (spec["high_water_key"],),
        ).fetchone()
        if existing_hw is not None and not force:
            hw = int(existing_hw[0])
            progress = conn.execute(
                "SELECT value FROM state_meta WHERE key = ?",
                (spec["progress_key"],),
            ).fetchone()
            if progress is None:
                # high_water without progress: fts_rebuild_step treats missing
                # progress as "done by another process" and optimize would
                # no-op then stamp. Re-seed progress so the chunk loop runs.
                if not self._fts_index_known_empty(conn, spec["fts_table"]):
                    self._reset_fts_index_to_empty(conn, spec["reset_tables"])
                conn.execute(
                    "INSERT INTO state_meta (key, value) VALUES (?, '0') "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (spec["progress_key"],),
                )
            return hw

        hw = conn.execute(
            f"SELECT COALESCE(MAX({spec['row_key']}), 0) FROM {spec['source_table']}"
        ).fetchone()[0]
        for k, v in (
            (spec["high_water_key"], str(hw)),
            (spec["progress_key"], "0"),
        ):
            conn.execute(
                "INSERT INTO state_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, v),
            )
        return int(hw)

    def _seed_session_metadata_fts_rebuild_markers(
        self, conn, spec: Dict[str, Any], *, force: bool = False
    ) -> int:
        """Shared session-metadata variant of ``_seed_fts_rebuild_markers``.

        An empty DB is complete by construction (the triggers cover every
        future row) and must not carry a spurious claim that would make
        ``fts_optimize_available`` advertise pending work forever. Shared by
        the Unicode (#25), normalized trigram (#30), and CJK (#26) session
        metadata lanes so the empty-DB rule lives once — only the spec
        (marker keys, source table, row key) differs.
        """
        n = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        if n == 0:
            # Zero canonical sessions is complete by construction regardless
            # of force intent (round-10 finding 7): never leave an H=0/P=0
            # zombie claim — fts_rebuild_status returns None for total<=0, the
            # worker never finishes/clears it, and fts_optimize_available
            # would advertise permanently-pending trigram work forever. Clear
            # this spec's markers so a later non-empty open re-seeds fresh.
            conn.execute(
                "DELETE FROM state_meta WHERE key IN (?, ?)",
                (spec["high_water_key"], spec["progress_key"]),
            )
            return 0
        return self._seed_fts_rebuild_markers(conn, spec, force=force)

    def _seed_session_fts_rebuild_markers(
        self, conn, *, force: bool = False
    ) -> int:
        """Session Unicode metadata variant of ``_seed_fts_rebuild_markers``
        (issue #25)."""
        return self._seed_session_metadata_fts_rebuild_markers(
            conn, _FTS_SESSION_SPEC, force=force
        )

    def _seed_session_trigram_fts_rebuild_markers(
        self, conn, *, force: bool = False
    ) -> int:
        """Session normalized trigram variant of ``_seed_fts_rebuild_markers``
        (issue #30). Same empty-DB-is-complete rule on its OWN marker pair."""
        return self._seed_session_metadata_fts_rebuild_markers(
            conn, _FTS_SESSION_TRIGRAM_SPEC, force=force
        )

    def _seed_session_cjk_fts_rebuild_markers(
        self, conn, *, force: bool = False
    ) -> int:
        """Session CJK metadata variant of ``_seed_fts_rebuild_markers``
        (issue #26). Gated on tokenizer capability by the caller."""
        return self._seed_session_metadata_fts_rebuild_markers(
            conn, _FTS_SESSION_CJK_SPEC, force=force
        )

    def _repair_missing_progress(self, conn, spec: Dict[str, Any]) -> bool:
        """Repair an orphan high_water-without-progress for one rebuild spec.

        Re-seeds P=0 so the chunk loop is not a no-op, resetting a partially
        populated index to a known-empty surface first so the anti-join-free
        chunk replay cannot duplicate rows. This is THE crash-safe recovery
        rule, shared by the message and session metadata rebuilds (the #76832
        seam) — session repairs must never re-implement it.

        Returns True when P was (re)published; False when the required primary
        target could NOT be proven empty after the reset (round-10 finding 5)
        — callers must then treat the repair as refused (H-without-P is
        preserved for a later capable retry), NOT proceed as though the index
        was reset. The message spec's optional trigram sidecar is separate:
        its absence/unavailability is not a failure to reset the required base
        table.
        """
        if not self._fts_index_known_empty(conn, spec["fts_table"]):
            self._reset_fts_index_to_empty(conn, spec["reset_tables"])
            # Reset postcondition: never publish P=0 unless the required
            # primary target is PROVEN empty after the reset. A failed
            # delete-all (e.g. unavailable declared tokenizer) leaves docsize
            # non-empty; publishing P=0 would make a later capable replay
            # duplicate postings on top of surviving rows (external-content
            # integrity check → malformed).
            if not self._fts_index_known_empty(conn, spec["fts_table"]):
                return False
        conn.execute(
            "INSERT INTO state_meta (key, value) VALUES (?, '0') "
            "ON CONFLICT(key) DO UPDATE SET value = '0'",
            (spec["progress_key"],),
        )
        return True

    def _repair_optimize_bookkeeping(
        self, spec: Optional[Dict[str, Any]] = None
    ) -> None:
        """Heal interrupted demote/backfill bookkeeping before optimize runs.

        Covers two post-#65798 failure classes:

        1. Empty external-content index with the source table present and no
           rebuild markers (demote crash window after empty v23 tables landed
           but before markers, or settle that stamped without backfill). Seed
           a full backfill.
        2. high_water present without progress (partial meta) — seed progress
           so the chunk loop is not a no-op, resetting a partially populated
           index to a known-empty surface first so the anti-join-free chunk
           replay cannot duplicate rows.

        Must not invent markers on a still-legacy inline DB: that would make
        ``optimize_fts_storage`` skip demote (``legacy and not pending``) and
        attempt v23-shaped INSERTs against the inline table forever.
        """
        spec = spec or _FTS_MESSAGE_SPEC

        def _do(conn):
            existing_hw = conn.execute(
                "SELECT value FROM state_meta WHERE key = ?",
                (spec["high_water_key"],),
            ).fetchone()

            if existing_hw is not None:
                # Repair orphan high_water-without-progress only. Never
                # invent a fresh claim on a healthy complete index.
                progress = conn.execute(
                    "SELECT 1 FROM state_meta WHERE key = ?",
                    (spec["progress_key"],),
                ).fetchone()
                if progress is None:
                    self._repair_missing_progress(conn, spec)
                return

            # No markers. On a still-legacy DB demote owns marker creation.
            if self._db_has_legacy_inline_fts(conn):
                return

            # Non-legacy empty external index (demote crash window / premature
            # stamp): seed a full backfill claim.
            if self._fts_external_index_empty_with_messages(conn):
                conn.execute(
                    "DELETE FROM state_meta WHERE key = 'fts_storage_version'"
                )
                self._seed_fts_rebuild_markers(conn, force=True)
        self._execute_write(_do)

    def _repair_session_trigram_fts_bookkeeping(self) -> None:
        """Session normalized trigram variant of
        ``_repair_session_spec_bookkeeping`` (issue #30). Independent markers;
        shares the crash-safe implementation.

        Serving gate: only repairs a target this process serves (available at
        open) and that is not durably stale. An unknown same-name object is
        never available → never mutated (no H/P seed, no delete-all) even
        when it happens to look canonical; a stale target is only repaired
        through the dedicated stale-recovery path.
        """
        if not self._sessions_trigram_available:
            return
        with self._read_ctx() as conn:
            if self._session_trigram_is_stale(conn):
                return
        self._repair_session_spec_bookkeeping(_FTS_SESSION_TRIGRAM_SPEC)

    def _repair_session_spec_bookkeeping(self, spec: Dict[str, Any]) -> None:
        """Shared session-metadata repair for interrupted backfill bookkeeping
        (#25 Unicode / #30 trigram / #26 CJK). Reuses
        ``_repair_missing_progress`` (the shared crash-safe rule) for the
        orphan high_water-without-progress case, and seeds a fresh claim for
        a fresh external index over a populated DB that lost its markers.
        Never re-implements the reset-before-replay rule.
        """
        def _do(conn):
            existing_hw = conn.execute(
                "SELECT value FROM state_meta WHERE key = ?",
                (spec["high_water_key"],),
            ).fetchone()
            if existing_hw is not None:
                progress = conn.execute(
                    "SELECT 1 FROM state_meta WHERE key = ?",
                    (spec["progress_key"],),
                ).fetchone()
                if progress is None:
                    self._repair_missing_progress(conn, spec)
                return
            # No claim: a freshly-created external index over a populated DB
            # that lost its markers, or a crash window after schema ensure
            # without a claim. Seed a full backfill (orphan recovery).
            if self._fts_external_index_empty_with_source(
                conn, spec["source_table"], spec["fts_table"]
            ):
                self._seed_session_metadata_fts_rebuild_markers(
                    conn, spec, force=True
                )
        self._execute_write(_do)

    def _repair_session_fts_bookkeeping(self) -> None:
        """Heal interrupted session Unicode metadata backfill bookkeeping
        (#25). See ``_repair_session_spec_bookkeeping``."""
        self._repair_session_spec_bookkeeping(_FTS_SESSION_SPEC)

    def _repair_session_cjk_fts_bookkeeping(self) -> None:
        """Heal interrupted session-CJK backfill bookkeeping (issue #26). See
        ``_repair_session_spec_bookkeeping``. Only meaningful on a
        tokenizer-capable host: an incapable host cannot have created the CJK
        surface, and must never fabricate durable CJK claims.
        """
        if not self._fts_cjk_loaded:
            return
        self._repair_session_spec_bookkeeping(_FTS_SESSION_CJK_SPEC)

    def fts_optimize_available(self) -> bool:
        """True when `optimize_fts_storage()` has work THIS process can
        finish: the DB is not yet acceptance-complete for the current storage
        layout AND at least one remaining blocker is actionable on this host
        (legacy inline layout to demote, deferred rebuild lanes to backfill,
        trash to tear down, orphan/empty indexes to heal).

        Derived from the SAME shared storage-v2 evaluator as startup
        auto-settlement and the final stamp (issue #31), so "is there work"
        and "may v2 be claimed" can never diverge. A blocker that requires an
        optional tokenizer / external resolution this process lacks is NOT
        advertised here (the stamp is still refused; status surfaces report
        why). False for fresh/fully-settled installs, when FTS5 is
        unavailable, and on read-only opens."""
        if not self._fts_enabled or self.read_only:
            return False
        with self._lock:
            return any(
                actionable
                for _reason, actionable in self._fts_storage_v2_blockers(self._conn)
            )

    def _demote_legacy_fts_to_trash(self) -> int:
        """Demote the legacy inline FTS vtables and stage their shadow tables
        for chunked teardown. Returns MAX(messages.id) as the rebuild high
        water. O(1) schema surgery — the heavy delete is deferred to the
        chunked teardown, exactly as the validated auto path did.

        Markers are written in the same BEGIN IMMEDIATE as the demote, *before*
        the empty v23 schema is created. Schema creation uses
        ``executescript`` and therefore cannot run inside that transaction
        (it issues an implicit COMMIT — see the CJK recreate path). Creating
        the empty schema only after markers are durable closes the crash
        window where trash + empty v23 tables exist with no backfill claim.
        """
        def _stage(conn):
            # Message-scoped teardown: the demote re-creates the message
            # schema immediately, so only the message Unicode/trigram triggers
            # are dropped here — session metadata triggers keep live-indexing
            # during the message-layout migration (issue #27).
            self._drop_fts_triggers(
                conn,
                names=(
                    _fts_descriptor("messages_fts").trigger_names
                    + _fts_descriptor("messages_fts_trigram").trigger_names
                ),
            )
            conn.execute("DROP VIEW IF EXISTS messages_fts_trigram_src")
            had = bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('messages_fts', 'messages_fts_trigram') "
                "AND sql LIKE 'CREATE VIRTUAL TABLE%' LIMIT 1"
            ).fetchone())
            if had:
                conn.execute("PRAGMA writable_schema=ON")
                conn.execute(
                    "DELETE FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('messages_fts', 'messages_fts_trigram') "
                    "AND sql LIKE 'CREATE VIRTUAL TABLE%'"
                )
                conn.execute("PRAGMA writable_schema=RESET")
                shadows = [
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' "
                        "AND (name LIKE 'messages_fts_%' ESCAPE '\\' "
                        "OR name LIKE 'messages_fts_trigram_%' ESCAPE '\\')"
                    ).fetchall()
                ]
                for sh in shadows:
                    conn.execute(f"ALTER TABLE {sh} RENAME TO fts_v22_trash_{sh}")
            # Claim the backfill *before* empty v23 tables exist. A crash
            # between this commit and schema ensure still leaves markers, so
            # optimize-storage resumes instead of tearing down trash and
            # stamping an empty index as complete.
            hw = self._seed_fts_rebuild_markers(conn, force=True)
            conn.execute(
                "DELETE FROM state_meta WHERE key = 'fts_optimize_available'"
            )
            return hw

        hw = int(self._execute_write(_stage))

        # Create the empty v23 schema outside the write transaction —
        # ``_ensure_fts_schema`` uses executescript(), which implicitly
        # commits any pending transaction and must not run inside
        # ``_execute_write``'s BEGIN IMMEDIATE (same rule as the CJK recreate
        # path above). Markers are already durable.
        with self._lock:
            base_ok = self._ensure_fts_schema(self._conn, "messages_fts", FTS_SQL)
            trigram_ok = self._ensure_fts_schema(
                self._conn, "messages_fts_trigram", FTS_TRIGRAM_SQL
            )
            self._trigram_available = bool(trigram_ok)
            if not base_ok:
                raise sqlite3.OperationalError(
                    "failed to create v23 messages_fts during optimize-storage demote"
                )
            self._conn.commit()
        return hw

    def optimize_fts_storage(
        self,
        *,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        vacuum: bool = True,
    ) -> Dict[str, Any]:
        """Migrate a legacy v22 inline-FTS DB to the v23 external-content
        schema, foreground and to completion. Safe to re-run: if a previous
        attempt was interrupted it resumes from the progress marker.

        ``progress_cb`` receives {"phase", "percent", "indexed", "total"}
        dicts for a CLI progress bar. Returns a summary dict.

        The trigram tokenizer being unavailable is not fatal — the base index
        is still rebuilt (CJK falls back to LIKE), mirroring normal startup.
        """
        if not self._fts_enabled:
            return {"ok": False, "reason": "fts5_unavailable"}
        if self.read_only:
            return {"ok": False, "reason": "read_only"}

        # Heal empty-index / orphan-marker bookkeeping from an interrupted
        # demote *before* deciding whether to demote again. This re-seeds
        # markers when trash was already staged (or torn down) without a
        # backfill claim so the phases below actually run.
        self._repair_optimize_bookkeeping()
        # Same healing for the session Unicode metadata rebuild (issue #25).
        self._repair_session_fts_bookkeeping()
        # Same healing for the session normalized trigram rebuild (#30) — its
        # OWN markers, independent of the Unicode lane.
        self._repair_session_trigram_fts_bookkeeping()
        # Same healing for the session CJK metadata rebuild (issue #26);
        # no-op on a host that cannot tokenize.
        self._repair_session_cjk_fts_bookkeeping()

        # Only demote if we're actually still on the legacy shape. If a prior
        # run already demoted (markers/trash present), skip straight to
        # finishing the backfill + teardown — this is what makes re-running
        # after an interruption safe.
        with self._lock:
            legacy = self._db_has_legacy_inline_fts(self._conn)
        pending = self.get_meta("fts_rebuild_high_water") is not None
        if legacy and not pending:
            self._demote_legacy_fts_to_trash()
        elif pending and not legacy:
            # Resume mid-demote: markers exist, empty v23 tables may still be
            # missing if the process died between the staged demote commit and
            # schema ensure. Re-ensure is IF NOT EXISTS and cheap.
            with self._lock:
                base_ok = self._ensure_fts_schema(
                    self._conn, "messages_fts", FTS_SQL
                )
                trigram_ok = self._ensure_fts_schema(
                    self._conn, "messages_fts_trigram", FTS_TRIGRAM_SQL
                )
                self._trigram_available = bool(trigram_ok)
                if not base_ok:
                    # Fail fast: without the base table the backfill loop
                    # below would retry "no such table" errors forever.
                    raise sqlite3.OperationalError(
                        "failed to re-create v23 messages_fts "
                        "on optimize-storage resume"
                    )
                self._conn.commit()

        # A stale CJK index (triggers dropped by a tokenizer-less process)
        # can only be recovered from scratch — reset it now so the cjk
        # backfill phase below rebuilds it. No-op without the tokenizer.
        self._fts_cjk_reset_if_stale()
        # A stale session-trigram target (quarantined by a tokenizer-less
        # process: stale breadcrumb set + owned triggers dropped) can only be
        # recovered from scratch — reset it now so the trigram backfill phase
        # below rebuilds it. No-op without trigram capability (finding 1
        # point 4).
        self._fts_session_trigram_recover_if_stale()
        # Same from-scratch recovery for a stale session-CJK index (issue
        # #26); no-op without the tokenizer.
        self._fts_session_cjk_reset_if_stale()
        # An optimized v23 DB gaining the cjk index for the first time (no
        # legacy work left, tokenizer newly installed): ensure the table +
        # markers exist so the backfill phase has work to claim.
        if self._fts_cjk_loaded:
            with self._lock:
                self._ensure_fts_cjk_schema(self._conn)
                self._conn.commit()

        def _emit(phase: str) -> None:
            if progress_cb is None:
                return
            st = self._fts_first_pending_lane_status()
            progress_cb({
                "phase": phase,
                "percent": st["percent"] if st else 100,
                "indexed": st["indexed"] if st else 0,
                "total": st["total"] if st else 0,
            })

        # Phase 1: backfill every deferred-rebuild lane with pending work,
        # foreground and throttled between chunks so a live gateway sharing
        # the DB stays responsive. The shared lane surface (issue #27)
        # sequences the message / message-CJK / session Unicode / session
        # trigram / session CJK lanes instead of a hard-coded ladder.
        _emit("backfill")
        self._fts_run_pending_lane_steps(on_chunk=lambda: _emit("backfill"))
        _emit("backfill")

        # Phase 2: tear down the demoted legacy shadow tables in chunks.
        _emit("teardown")
        while True:
            _t0 = time.monotonic()
            if not self._fts_teardown_trash_step():
                break
            _emit("teardown")
            self._fts_rebuild_pause(time.monotonic() - _t0)

        # Refuse to stamp "optimized" while the shared storage-v2 evaluator
        # reports ANY incomplete durable state — message/session Unicode/CJK/
        # trigram H/P or stale breadcrumbs, demoted trash, orphan-empty or
        # legacy shapes, and #30 fail-closed trigram ownership. This is the
        # SAME predicate startup and the final transactional re-check use
        # (issue #31), so a DB cannot be stamped here and refused there (or
        # vice versa), and pre-fix code can never settle past a no-op
        # backfill into permanent search-index loss again.
        with self._lock:
            blocker = self._fts_storage_v2_blocker(self._conn)
        if blocker is not None:
            # Withdraw any stale layout claim — the DB is not
            # acceptance-complete (issue #31).
            self._execute_write(self._fts_storage_v2_withdraw_claim)
            logger.warning(
                "FTS storage optimization did not settle (%s, actionable=%s)",
                blocker[0], blocker[1],
            )
            return {"ok": False, "reason": blocker[0], "vacuumed": None}

        # Phase 3: reclaim freed pages to the OS.
        vacuum_ok = None
        if vacuum:
            _emit("vacuum")
            try:
                with self._lock:
                    self._conn.execute("VACUUM")
                vacuum_ok = True
            except sqlite3.OperationalError as exc:
                # Most common cause: not enough free disk for VACUUM's temp
                # copy. The optimization still succeeded; space just isn't
                # reclaimed until a later VACUUM. Non-fatal.
                logger.warning("VACUUM after FTS optimize failed: %s", exc)
                vacuum_ok = False
            # Best-effort: fold the WAL back into the main file so the on-disk
            # size settles now rather than at close(). NOTE this is REFUSED
            # (SQLITE_BUSY) while any other connection holds a WAL read-mark —
            # e.g. a live gateway sharing the DB — so it is not sufficient on
            # its own. Callers must therefore NOT size the result by stat()ing
            # the file; use :meth:`logical_size_bytes`, which is truthful
            # immediately regardless of readers.
            try:
                with self._lock:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as exc:
                logger.debug(
                    "WAL checkpoint (TRUNCATE) after optimize VACUUM failed: %s",
                    exc,
                )

        # Phase 4: stamp the FTS storage layout as current, clear the "available"
        # flag, and advance schema_version if it was somehow still behind (the
        # main version normally advances on open now, but bump defensively so a
        # DB opened only by pre-decoupling code still settles). The FTS-layout
        # marker is the source of truth for "is this DB optimized".
        def _settle(conn):
            # Re-check inside the write transaction that performs the claim
            # so a concurrent writer cannot race a stamp past incomplete work
            # (the #76832 rule). Uses the SAME shared storage-v2 evaluator as
            # startup / availability / the pre-VACUUM refusal (issue #31).
            # Returns a refusal reason (stamping nothing) or None once the
            # stamp is written.
            blocker = self._fts_storage_v2_blocker(conn)
            if blocker is not None:
                # Withdraw any stale claim in the same transaction that would
                # have stamped it (issue #31).
                self._fts_storage_v2_withdraw_claim(conn)
                return blocker[0]
            conn.execute(
                "INSERT INTO state_meta (key, value) VALUES ('fts_storage_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(FTS_STORAGE_VERSION),),
            )
            conn.execute("DELETE FROM state_meta WHERE key = 'fts_optimize_available'")
            conn.execute(
                "UPDATE schema_version SET version = ? WHERE version < ?",
                (SCHEMA_VERSION, SCHEMA_VERSION),
            )
            return None
        refusal = self._execute_write(_settle)
        if refusal is not None:
            # A concurrent process re-seeded markers, left trash, or emptied
            # the index between the pre-vacuum check above and this write
            # transaction. Nothing was stamped. Report the failure instead of
            # crashing the CLI with a traceback; a re-run can still settle.
            logger.warning(
                "FTS storage optimization settle refused (%s)", refusal
            )
            return {"ok": False, "reason": refusal, "vacuumed": vacuum_ok}
        _emit("done")
        logger.info(
            "FTS storage optimization complete (layout v%d).", FTS_STORAGE_VERSION
        )
        return {"ok": True, "vacuumed": vacuum_ok}

    def get_anchored_view(
        self,
        session_id: str,
        around_message_id: int,
        window: int = 5,
        bookend: int = 3,
        keep_roles: Optional[Tuple[str, ...]] = ("user", "assistant"),
    ) -> Dict[str, Any]:
        """Return an anchored window plus session bookends.

        Built on top of ``get_messages_around``. Three slices:

          - ``window``: messages immediately surrounding the anchor. Filtered
            to ``keep_roles`` (tool-response noise dropped by default), EXCEPT
            the anchor itself is always preserved regardless of role.
          - ``bookend_start``: first ``bookend`` user/assistant messages of the
            session — but only those whose id is strictly before the window's
            first message id. Empty when the window already overlaps the
            session head. Empty-content messages (tool-call-only assistant
            turns) are skipped so they don't crowd out actual prose openings.
          - ``bookend_end``: last ``bookend`` user/assistant messages of the
            session, same non-overlap rule at the tail.

        Bookends let an FTS5 hit anywhere in a long session yield the goal
        (opening) and the resolution (closing) on a single call — without
        loading the whole transcript.

        Returns ``{"window": [], "messages_before": 0, "messages_after": 0,
        "bookend_start": [], "bookend_end": []}`` when the anchor isn't in
        the session.

        ``keep_roles=None`` disables role filtering (raw window + raw
        bookends).
        """
        if bookend < 0:
            bookend = 0

        # Reuse the primitive — handles anchor-existence, content decoding,
        # tool_calls deserialisation, and boundary counts.
        primitive = self.get_messages_around(
            session_id, around_message_id, window=window
        )
        window_rows = primitive["window"]
        if not window_rows:
            return {
                "window": [],
                "messages_before": 0,
                "messages_after": 0,
                "bookend_start": [],
                "bookend_end": [],
            }

        # Apply role filter to the window, but never drop the anchor itself.
        if keep_roles is not None:
            keep_set = set(keep_roles)
            filtered_window = [
                m for m in window_rows
                if m.get("id") == around_message_id or m.get("role") in keep_set
            ]
        else:
            filtered_window = window_rows

        window_min_id = window_rows[0]["id"]
        window_max_id = window_rows[-1]["id"]

        # Fetch bookends only when there's room outside the window. SQL filters
        # by id range, role, and non-empty content — tool-call-only assistant
        # turns (content='' with tool_calls populated) are excluded so they
        # don't crowd out actual prose openings/closings.
        bookend_start_rows: List[Any] = []
        bookend_end_rows: List[Any] = []
        if bookend > 0:
            with self._read_ctx() as conn:
                role_clause = ""
                role_params: list = []
                if keep_roles is not None:
                    role_placeholders = ",".join("?" for _ in keep_roles)
                    role_clause = f" AND role IN ({role_placeholders})"
                    role_params = list(keep_roles)

                bookend_start_rows = conn.execute(
                    f"SELECT * FROM messages "
                    f"WHERE session_id = ? AND id < ?{role_clause} "
                    f"AND length(content) > 0 "
                    f"ORDER BY id ASC LIMIT ?",
                    (session_id, window_min_id, *role_params, bookend),
                ).fetchall()

                bookend_end_rows = conn.execute(
                    f"SELECT * FROM messages "
                    f"WHERE session_id = ? AND id > ?{role_clause} "
                    f"AND length(content) > 0 "
                    f"ORDER BY id DESC LIMIT ?",
                    (session_id, window_max_id, *role_params, bookend),
                ).fetchall()
                # End rows came back DESC for the LIMIT cap; flip to ASC.
                bookend_end_rows = list(reversed(bookend_end_rows))

        def _hydrate(row) -> Dict[str, Any]:
            msg = dict(row)
            if "content" in msg:
                msg["content"] = self._decode_content(msg["content"])
            if msg.get("tool_calls"):
                try:
                    msg["tool_calls"] = json.loads(msg["tool_calls"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "Failed to deserialize tool_calls in get_anchored_view, falling back to []"
                    )
                    msg["tool_calls"] = []
            if msg.get("display_metadata") is not None:
                msg["display_metadata"] = self._decode_display_metadata(msg["display_metadata"])
            return msg

        return {
            "window": filtered_window,
            "messages_before": primitive["messages_before"],
            "messages_after": primitive["messages_after"],
            "bookend_start": [_hydrate(r) for r in bookend_start_rows],
            "bookend_end": [_hydrate(r) for r in bookend_end_rows],
        }

    def list_recent_user_messages(
        self,
        session_id: str,
        limit: int = 20,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return the *limit* most-recent user messages, newest first.

        Each entry is a dict with keys ``id``, ``timestamp``, ``preview``.
        ``preview`` is the first 80 characters of the message content
        (with line breaks collapsed to spaces). Used by the /rewind
        slash command picker, CLI/TUI/gateway ``/undo [N]``, and any other
        caller that needs real user-turn targets.

        Bookkeeping timeline rows (``display_kind`` set — e.g. model_switch,
        async_delegation_complete, auto_continue, hidden) are excluded. They
        are durable ``role='user'`` rows for the API transcript, but no client
        counts them as user turns (desktop demotes them to system / drops them;
        the CLI already uses ``not m.get("display_kind")``). Including them here
        made ``/undo`` soft-delete from a marker instead of the last real turn —
        same class of index skew as the prompt.submit ordinal bug.

        By default only active messages are returned.
        """
        active_clause = "" if include_inactive else " AND active = 1"
        # Match CLI/desktop: only real user turns, not timeline bookkeeping.
        display_clause = " AND (display_kind IS NULL OR display_kind = '')"
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, timestamp, content FROM messages "
                "WHERE session_id = ? AND role = 'user'"
                f"{active_clause}{display_clause} "
                "ORDER BY id DESC LIMIT ?",
                (session_id, int(limit)),
            )
            rows = cursor.fetchall()

        result: List[Dict[str, Any]] = []
        for row in rows:
            decoded = self._decode_content(row["content"])
            if isinstance(decoded, list):
                # Multimodal — flatten text parts.
                text_parts = [
                    p.get("text", "") for p in decoded
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                preview = " ".join(t for t in text_parts if t).strip()
                if not preview:
                    preview = "[multimodal content]"
            elif isinstance(decoded, str):
                # A /skill turn embeds the whole skill body; show what the user
                # typed instead of the skill's opening prose.
                preview = describe_skill_invocation(decoded) or decoded
            else:
                preview = ""
            preview = " ".join(preview.split())  # collapse whitespace
            if len(preview) > 80:
                preview = preview[:77] + "..."
            result.append(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "preview": preview,
                }
            )
        return result

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        """Sanitize user input for safe use in FTS5 MATCH queries.

        FTS5 has its own query syntax where characters like ``"``, ``(``, ``)``,
        ``+``, ``*``, ``{``, ``}``, the column-filter operator ``:`` and bare
        boolean operators (``AND``, ``OR``, ``NOT``) have special meaning.
        Passing raw user input directly to MATCH can cause
        ``sqlite3.OperationalError``.

        Strategy:
        - Preserve properly paired quoted phrases (``"exact phrase"``)
        - Strip unmatched FTS5-special characters that would cause errors
        - Wrap unquoted hyphenated and dotted terms in quotes so FTS5
          matches them as exact phrases instead of splitting on the
          hyphen/dot (e.g. ``chat-send``, ``P2.2``, ``my-app.config.ts``)
        """
        # Cap user-controlled FTS input before any regex processing. Search
        # queries do not need to be arbitrarily large, and bounding them keeps
        # sanitizer/runtime behavior predictable under adversarial input.
        query = query[:MAX_FTS5_QUERY_CHARS]

        # Step 1: Extract balanced double-quoted phrases and protect them
        # from further processing via numbered placeholders. Do this with a
        # single linear scan rather than a regex so pathological quote runs
        # cannot induce backtracking.
        _quoted_parts: list = []
        pieces: list[str] = []
        i = 0
        while i < len(query):
            ch = query[i]
            if ch != '"':
                pieces.append(ch)
                i += 1
                continue
            end = query.find('"', i + 1)
            if end == -1:
                # Unmatched quote: replace with whitespace like the old
                # sanitizer's special-char stripping step.
                pieces.append(" ")
                i += 1
                continue
            _quoted_parts.append(query[i:end + 1])
            pieces.append(f"\x00Q{len(_quoted_parts) - 1}\x00")
            i = end + 1

        sanitized = "".join(pieces)

        # Step 2: Strip remaining (unmatched) FTS5-special characters.  ``:`` is
        # FTS5's column-filter operator (``col:term``); since the FTS table has a
        # single ``content`` column, an unquoted colon query like ``TODO: fix``
        # parses as ``column:term`` and raises "no such column" — swallowed at
        # the execute site into zero results.  Strip it like the others.
        sanitized = re.sub(r'[+{}():\"^]', " ", sanitized)

        # Step 3: Collapse repeated * (e.g. "***") into a single one,
        # and remove leading * (prefix-only needs at least one char before *)
        sanitized = re.sub(r"\*+", "*", sanitized)
        sanitized = re.sub(r"(^|\s)\*", r"\1", sanitized)

        # Step 4: Remove dangling boolean operators at start/end that would
        # cause syntax errors (e.g. "hello AND" or "OR world")
        sanitized = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", sanitized.strip())
        sanitized = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", sanitized.strip())

        # Step 5: Wrap unquoted dotted and/or hyphenated terms in double
        # quotes.  FTS5's tokenizer splits on dots and hyphens, turning
        # ``chat-send`` into ``chat AND send`` and ``P2.2`` into ``p2 AND 2``.
        # Quoting preserves phrase semantics.  A single pass avoids the
        # double-quoting bug that would occur if dotted, hyphenated and underscored
        # patterns were applied sequentially (e.g. ``my-app.config``).
        sanitized = re.sub(r"\b(\w+(?:[._-]\w+)+)\b", r'"\1"', sanitized)

        # Step 6: Restore preserved quoted phrases
        for i, quoted in enumerate(_quoted_parts):
            sanitized = sanitized.replace(f"\x00Q{i}\x00", quoted)

        return sanitized.strip()

    @staticmethod
    def _is_cjk_codepoint(cp: int) -> bool:
        return (0x4E00 <= cp <= 0x9FFF or    # CJK Unified Ideographs
                0x3400 <= cp <= 0x4DBF or    # CJK Extension A
                0x20000 <= cp <= 0x2A6DF or  # CJK Extension B
                0x3000 <= cp <= 0x303F or    # CJK Symbols
                0x3040 <= cp <= 0x309F or    # Hiragana
                0x30A0 <= cp <= 0x30FF or    # Katakana
                0xAC00 <= cp <= 0xD7AF)      # Hangul Syllables

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        """Check if text contains CJK (Chinese, Japanese, Korean) characters."""
        for ch in text:
            cp = ord(ch)
            if (0x4E00 <= cp <= 0x9FFF or    # CJK Unified Ideographs
                0x3400 <= cp <= 0x4DBF or    # CJK Extension A
                0x20000 <= cp <= 0x2A6DF or  # CJK Extension B
                0x3000 <= cp <= 0x303F or    # CJK Symbols
                0x3040 <= cp <= 0x309F or    # Hiragana
                0x30A0 <= cp <= 0x30FF or    # Katakana
                0xAC00 <= cp <= 0xD7AF):     # Hangul Syllables
                return True
        return False

    @classmethod
    def _count_cjk(cls, text: str) -> int:
        """Count CJK characters in text."""
        return sum(1 for ch in text if cls._is_cjk_codepoint(ord(ch)))

    @classmethod
    def _has_lone_cjk_run(cls, query: str) -> bool:
        """True when any maximal CJK run in the query is a single char.

        The cjk-bigram index stores bigrams for runs >=2 chars and unigrams
        only for isolated chars, so a 1-char CJK term can't match inside
        longer runs there — those queries keep the LIKE substring route.
        """
        run = 0
        for ch in query:
            if cls._is_cjk_codepoint(ord(ch)):
                run += 1
            else:
                if run == 1:
                    return True
                run = 0
        return run == 1

    @staticmethod
    def _trigram_eligible_tokens(query: str) -> bool:
        """True when every non-operator token is long enough for the trigram
        tokenizer to match (>=3 chars).

        The trigram tokenizer indexes overlapping 3-character sequences, so a
        token shorter than 3 chars produces no trigrams and can never match.
        With FTS5's implicit-AND between tokens, a single short token makes the
        whole MATCH return nothing, so the trigram path is only worth taking
        when every searchable token qualifies.
        """
        tokens = [
            t for t in query.strip('"').strip().split()
            if t.upper() not in {"AND", "OR", "NOT"}
        ]
        return bool(tokens) and all(len(t) >= 3 for t in tokens)

    def _run_trigram_search(
        self,
        raw_query: str,
        *,
        table: str = "messages_fts_trigram",
        order_by_sql: str,
        include_inactive: bool,
        source_filter: List[str] = None,
        exclude_sources: List[str] = None,
        role_filter: List[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Optional[List[Dict[str, Any]]]:
        """Run a search against a substring-capable FTS index.

        ``table`` is ``messages_fts_trigram`` (default) or
        ``messages_fts_cjk``. The trigram tokenizer indexes overlapping
        3-byte sequences, so it matches substrings regardless of word
        boundaries — both CJK phrases the unicode61 tokenizer splits into
        single characters and Latin runs the unicode61 tokenizer fuses onto
        adjacent CJK (e.g. ``修改youer服务端``). The cjk-bigram tokenizer
        splits Latin runs off adjacent CJK, giving the same recovery as an
        exact ranked token match. Each non-operator token is quoted to
        neutralise FTS5 special characters while boolean operators
        (AND/OR/NOT) are preserved.

        Returns the matching rows, or ``None`` when the query cannot be
        executed (e.g. the tokenizer is unavailable at runtime) so the
        caller can fall back to another strategy.
        """
        tokens = raw_query.split()
        parts = []
        for tok in tokens:
            if tok.upper() in {"AND", "OR", "NOT"}:
                parts.append(tok)
            else:
                parts.append('"' + tok.replace('"', '""') + '"')
        trigram_query = " ".join(parts)
        tri_where = [f"{table} MATCH ?"]
        tri_params: list = [trigram_query]
        if not include_inactive:
            tri_where.append("(m.active = 1 OR m.compacted = 1)")
        if source_filter is not None:
            tri_where.append(f"s.source IN ({','.join('?' for _ in source_filter)})")
            tri_params.extend(source_filter)
        if exclude_sources is not None:
            tri_where.append(f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})")
            tri_params.extend(exclude_sources)
        if role_filter:
            tri_where.append(f"m.role IN ({','.join('?' for _ in role_filter)})")
            tri_params.extend(role_filter)
        tri_sql = f"""
            SELECT
                m.id,
                m.session_id,
                m.role,
                snippet({table}, -1, '>>>', '<<<', '...', 40) AS snippet,
                m.content,
                m.timestamp,
                m.tool_name,
                s.source,
                s.model,
                s.started_at AS session_started
            FROM {table}
            JOIN messages m ON m.id = {table}.rowid
            JOIN sessions s ON s.id = m.session_id
            WHERE {' AND '.join(tri_where)}
            {order_by_sql}
            LIMIT ? OFFSET ?
        """
        tri_params.extend([limit, offset])
        with self._read_ctx() as conn:
            try:
                tri_cursor = conn.execute(tri_sql, tri_params)
            except sqlite3.OperationalError:
                # Query failed at runtime — let the caller fall back.
                return None
            return [dict(row) for row in tri_cursor.fetchall()]

    def _resolve_compression_lineage_on_conn(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        state: _LineageResolutionState,
    ) -> Any:
        """Resolve *session_id* to its compression root on *conn*.

        Returns the resolved root string, ``_UNRESOLVED`` for a proven
        semantic unresolved outcome (missing row / dangling parent / positive
        cycle), or ``_BUDGET_EXHAUSTED`` when the global work budget would be
        exceeded by the next uncached lookup.

        The loop ordering is correctness-significant at the B boundary:
        memo lookup first, then traversal-local cycle proof, then the budget
        check (only because another uncached lookup is now required), then the
        one-node point lookup.
        """
        if not session_id:
            return _UNRESOLVED
        node = str(session_id)
        path: List[str] = []
        seen: set[str] = set()

        while True:
            cached = state.memo.get(node)
            if cached is _UNRESOLVED:
                state.memo_hits += 1
                for visited in path:
                    state.memo.setdefault(visited, _UNRESOLVED)
                return _UNRESOLVED
            if cached is not None:
                # Memo hit: path-compress the visited prefix to the known root.
                state.memo_hits += 1
                for visited in path:
                    state.memo.setdefault(visited, cached)
                return cached

            if node in seen:
                # Positive cycle proven from the traversal-local seen-set
                # with no further DB lookup (valid even at work == B).
                state._memoize_unresolved(path, node)
                return _UNRESOLVED
            seen.add(node)

            if state.work >= state.budget:
                # Another uncached lookup is required and the budget is gone.
                # This is operational uncertainty, NOT semantic unresolved, so
                # the partial path is left out of the memo.
                state.bound_hit = True
                return _BUDGET_EXHAUSTED

            row = conn.execute(_LINEAGE_NODE_SQL, (node,)).fetchone()
            if row is None:
                # The node row itself is missing: zero successful-fetch work
                # for that absent row; the traversed path is semantically
                # unresolved.
                state._memoize_unresolved(path, node)
                return _UNRESOLVED
            state.work += 1

            current = str(row["id"])
            path.append(current)
            parent_id = row["parent_session_id"]
            if parent_id is None:
                root = current
                break
            parent_id = str(parent_id)

            if row["parent_exists"] is None:
                # Dangling parent: malformed lineage, fail closed.
                state._memoize_unresolved(path, node)
                return _UNRESOLVED

            config_valid, branched_from, delegate_from = _lineage_markers(
                row["model_config"]
            )
            positive_edge = (
                config_valid
                and row["parent_end_reason"] == "compression"
                and row["source"] != "tool"
                and branched_from != parent_id
                and delegate_from != parent_id
            )
            if not positive_edge:
                root = current
                break

            node = parent_id

        for visited in path:
            state.memo.setdefault(visited, root)
        return root

    def resolve_compression_lineage(
        self,
        session_id: str,
        *,
        work_budget: int = _LINEAGE_WORK_BUDGET,
    ) -> Optional[str]:
        """Resolve *session_id* to its positive compression-continuation root.

        Returns the root id, or ``None`` when the outcome is a proven semantic
        unresolved (missing row / dangling parent / positive cycle) or the work
        budget is exhausted.  Never falls back to generic parent ancestry; an
        unresolved session is kept separate instead of broadening exclusion to
        an unproven ancestor.
        """
        if not session_id:
            return None
        with self._read_ctx() as conn:
            started_tx = not conn.in_transaction
            if started_tx:
                conn.execute("BEGIN")
            try:
                state = _LineageResolutionState(work_budget)
                outcome = self._resolve_compression_lineage_on_conn(
                    conn, str(session_id), state
                )
            finally:
                if started_tx and conn.in_transaction:
                    conn.rollback()
        if outcome is _UNRESOLVED or outcome is _BUDGET_EXHAUSTED:
            return None
        return outcome

    def get_first_message_id(self, session_id: str) -> Optional[int]:
        """Return the first active message id for *session_id*, or None.

        Lightweight bounded anchor lookup used by session-search title
        discovery; avoids loading the whole transcript just to find one id.
        """
        if not session_id:
            return None
        with self._read_ctx() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE session_id = ? AND active = 1 "
                "ORDER BY id LIMIT 1",
                (str(session_id),),
            ).fetchone()
        return row["id"] if row is not None else None

    def _current_lineage_ancestors_on_conn(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        state: _LineageResolutionState,
    ) -> set:
        """Collect the current session's generic parent chain ids.

        Current-session exclusion must also hide live-context ancestors that
        are NOT compression lineage: a delegation/branch child is still
        visible to its parent agent, so the parent's content stays excluded
        even though the child is a distinct compression root (#68).  Walks
        ``parent_session_id`` links on the same connection, counts successful
        row fetches toward the work budget, and stops on a missing row, a
        cycle, or budget exhaustion.
        """
        ancestors: set = set()
        seen: set = set()
        node = str(session_id) if session_id else None
        while node and node not in seen:
            seen.add(node)
            if state.work >= state.budget:
                state.bound_hit = True
                break
            row = conn.execute(_LINEAGE_NODE_SQL, (node,)).fetchone()
            if row is None:
                break
            state.work += 1
            parent = row["parent_session_id"]
            if parent is None:
                break
            node = str(parent)
            ancestors.add(node)
        return ancestors

    def search_session_winners(
        self,
        query: str,
        role_filter: List[str] = None,
        exclude_sources: List[str] = None,
        source_filter: List[str] = None,
        candidate_limit: int = 300,
        result_limit: int = 3,
        sort: str = None,
        include_inactive: bool = False,
        excluded_lineage_roots: Tuple[str, ...] = (),
        current_lineage_root: Optional[str] = None,
        lineage_depth_cap: int = 64,
        request_id: str = None,
        current_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Select discovery winners in SQLite without candidate hydration.

        The candidate scan deliberately remains wider than the requested
        result limit.  SQLite ranks up to ``candidate_limit`` lightweight
        FTS/LIKE rows; a query-local Python resolver then walks each hit's
        owning session to its positive compression-continuation root under
        one coherent logical read snapshot, keeps the first eligible hit per
        owner as its anchor, dedupes by root, and stops as soon as
        ``result_limit`` distinct roots survive (early-K).  The returned rows
        contain no full message content and no candidate context; FTS
        snippets are computed only for the final winners.

        ``current_session_id`` is a raw session id re-resolved inside this
        snapshot with the SAME memo/state used for candidate roots, so
        current-session exclusion and winner dedupe share one root meaning and
        one work budget.  Exact-title exclusion arrives via
        ``excluded_lineage_roots`` (resolved by the caller with the same
        compression-lineage implementation).

        ``lineage_depth_cap`` is retained only for caller compatibility; #68
        replaced depth as an identity/safety boundary with a traversal-local
        cycle seen-set plus a global successful-row work budget.
        """
        del lineage_depth_cap
        empty = {"winners": [], "stats": {
            "candidate_count": 0,
            "candidate_unique_sessions": 0,
            "lineage_count": 0,
            "winner_count": 0,
            "lineage_work": 0,
            "lineage_memo_hits": 0,
            "lineage_memo_entries": 0,
            "lineage_candidates_inspected": 0,
            "lineage_bound_hit": False,
            "route": "none",
        }}
        if not self._fts_enabled or not query or not query.strip():
            return empty

        query = self._sanitize_fts5_query(query)
        if not query:
            return empty
        candidate_limit = max(1, min(int(candidate_limit), 1000))
        result_limit = max(0, min(int(result_limit), 100))
        role_filter = list(role_filter or ("user", "assistant"))
        exclude_sources = list(exclude_sources or ())
        source_filter = list(source_filter or ())
        excluded_lineage_roots = tuple(
            root for root in (excluded_lineage_roots or ()) if root
        )

        sort_norm = sort.strip().lower() if isinstance(sort, str) else None
        if sort_norm not in ("newest", "oldest"):
            sort_norm = None

        if sort_norm == "newest":
            candidate_order = "timestamp DESC, fts_rank ASC, message_id ASC"
        elif sort_norm == "oldest":
            candidate_order = "timestamp ASC, fts_rank ASC, message_id ASC"
        else:
            candidate_order = "fts_rank ASC, message_id ASC"

        def _where(prefix: str, params: list) -> str:
            clauses = [f"{prefix} MATCH ?"]
            params.append(query)
            if not include_inactive:
                clauses.append("(m.active = 1 OR m.compacted = 1)")
            if source_filter:
                clauses.append(
                    f"s.source IN ({','.join('?' for _ in source_filter)})"
                )
                params.extend(source_filter)
            if exclude_sources:
                clauses.append(
                    f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})"
                )
                params.extend(exclude_sources)
            if role_filter:
                clauses.append(f"m.role IN ({','.join('?' for _ in role_filter)})")
                params.extend(role_filter)
            return " AND ".join(clauses)

        route = "unicode61"
        params: list = []
        candidate_from: str
        candidate_select: str
        candidate_where: str
        if self._contains_cjk(query) and self._trigram_available:
            route = "trigram"
            raw_query = query.strip('"').strip()
            parts = []
            for token in raw_query.split():
                if token.upper() in {"AND", "OR", "NOT"}:
                    parts.append(token)
                elif any(ord(char) > 127 for char in token):
                    parts.append(token)
                else:
                    parts.append('"' + token.replace('"', '""') + '"')
            # Snippets are display data, not candidate-ranking data.  Computing
            # one for every FTS hit forces SQLite to materialize a wide result
            # set before the candidate LIMIT can take effect.  Recompute it
            # only for the final lineage winners below.
            candidate_select = "NULL"
            candidate_from = "messages_fts_trigram"
            candidate_where = _where("messages_fts_trigram", params)
            # Replace the query value inserted by _where with the tokenized form.
            params[0] = " ".join(parts)
        elif self._contains_cjk(query):
            route = "like"
            raw_query = query.strip('"').strip()
            tokens = [
                token for token in raw_query.split()
                if token.upper() not in {"AND", "OR", "NOT"}
            ] or [raw_query]
            token_clauses = []
            like_values = []
            for token in tokens:
                escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                token_clauses.append(
                    "(m.content LIKE ? ESCAPE '\\' OR "
                    "m.tool_name LIKE ? ESCAPE '\\' OR "
                    "m.tool_calls LIKE ? ESCAPE '\\')"
                )
                like_values.extend([f"%{escaped}%"] * 3)
            params = [tokens[0]]
            clauses = [f"({' OR '.join(token_clauses)})"]
            if not include_inactive:
                clauses.append("(m.active = 1 OR m.compacted = 1)")
            if source_filter:
                clauses.append(f"s.source IN ({','.join('?' for _ in source_filter)})")
                like_values.extend(source_filter)
            if exclude_sources:
                clauses.append(f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})")
                like_values.extend(exclude_sources)
            if role_filter:
                clauses.append(f"m.role IN ({','.join('?' for _ in role_filter)})")
                like_values.extend(role_filter)
            params.extend(like_values)
            params.extend([candidate_limit, 0])
            candidate_from = ""
            candidate_where = " AND ".join(clauses)
            candidate_select = (
                "substr(m.content, max(1, instr(m.content, ?) - 40), 120)"
            )
            candidate_order = "timestamp DESC, message_id ASC"
        else:
            params = []
            # See the trigram route: defer snippet generation until winners are
            # known so the FTS candidate scan stays narrow.
            candidate_select = "NULL"
            candidate_from = "messages_fts"
            candidate_where = _where("messages_fts", params)

        if route != "like":
            params.extend([candidate_limit, 0])

        source_priority = (
            "CASE WHEN COALESCE(source, '') IN ('cron') THEN 1 ELSE 0 END"
        )
        # Ranked Top-N pre-limit is only safe when the primary ordering key is
        # the FTS rank (relevance sort).  For newest/oldest the ordering key is
        # timestamp, which FTS5 cannot stream in rank order, so pre-limiting by
        # rank would drop the freshest candidates before timestamp ordering.
        # The LIKE route has no FTS rank at all and is left untouched.
        use_ranked_prelimit = route != "like" and sort_norm is None
        if route == "like":
            ranked_candidates = ""  # LIKE route has no FTS rank; no pre-limit
            candidate_base = f"""
                SELECT
                    m.id AS message_id,
                    m.session_id AS owning_session_id,
                    m.role,
                    {candidate_select} AS snippet,
                    m.timestamp,
                    s.source,
                    s.model,
                    s.started_at AS session_started,
                    0.0 AS fts_rank,
                    {source_priority} AS source_priority,
                    s.end_reason AS session_end_reason,
                    m.compacted AS message_compacted
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE {candidate_where}
            """
        else:
            fts_candidate = f"""
                SELECT
                    m.id AS message_id,
                    m.session_id AS owning_session_id,
                    m.role,
                    {candidate_select} AS snippet,
                    m.timestamp,
                    s.source,
                    s.model,
                    s.started_at AS session_started,
                    rank AS fts_rank,
                    {source_priority} AS source_priority,
                    s.end_reason AS session_end_reason,
                    m.compacted AS message_compacted
                FROM {candidate_from}
                JOIN messages m ON m.id = {candidate_from}.rowid
                JOIN sessions s ON s.id = m.session_id
                WHERE {candidate_where}
            """
            if use_ranked_prelimit:
                # Relevance sort: rank the full filtered FTS match set inside
                # the candidate CTE, keep only the top candidate_limit rows,
                # and only then run the JOIN / lineage / window machinery.
                # SQLite streams this CTE (CO-ROUTINE + LIMIT pushdown, INDEX
                # 0:M1) instead of materializing every hit before ordering,
                # which is what makes high-hit queries pathological.  The
                # message_id tie-break mirrors production's candidate_order
                # (fts_rank ASC, message_id ASC), so the top-N set is
                # identical; candidate_hits below re-derives the same
                # candidate_order for lineage ranking.
                ranked_candidates = f"""
            ranked_candidates AS (
                {fts_candidate}
                ORDER BY rank, m.id
                LIMIT {candidate_limit} OFFSET 0
            ),
            """
                candidate_base = """
                SELECT * FROM ranked_candidates
            """
            else:
                ranked_candidates = ""
                candidate_base = fts_candidate

        # The bounded ranked raw-hit set stays the upstream source.  Owner
        # dedupe happens in Python AFTER per-hit current-visibility: the first
        # eligible hit of an owner becomes its anchor, so a compacted-history
        # hit is never erased by an earlier live hit that current-session
        # exclusion rejects (#68 review finding).  Lineage resolution supplies
        # only a dedupe/exclusion key and must never rewrite the match anchor
        # to the root session.
        sql = f"""
            WITH
            {ranked_candidates}
            candidate_base AS (
                {candidate_base}
            ),
            candidate_hits AS (
                SELECT
                    candidate_base.*,
                    ROW_NUMBER() OVER (ORDER BY {candidate_order}) AS candidate_order
                FROM candidate_base
                ORDER BY {candidate_order}
                LIMIT ? OFFSET ?
            ),
            candidate_stats AS (
                SELECT
                    COUNT(*) AS candidate_count,
                    COUNT(DISTINCT owning_session_id) AS candidate_unique_sessions
                FROM candidate_hits
            )
            SELECT
                candidate_hits.*,
                stats.candidate_count,
                stats.candidate_unique_sessions
            FROM candidate_hits
            CROSS JOIN candidate_stats stats
            ORDER BY candidate_hits.source_priority, candidate_hits.candidate_order
        """

        request_value = request_id or "-"
        started = time.perf_counter()
        state = _LineageResolutionState(_LINEAGE_WORK_BUDGET)
        winners: List[Dict[str, Any]] = []
        seen_roots: set[str] = set()

        with self._read_ctx() as conn:
            started_tx = not conn.in_transaction
            if started_tx:
                conn.execute("BEGIN")
            try:
                try:
                    execute_started = time.perf_counter()
                    candidates = [
                        dict(row) for row in conn.execute(sql, params).fetchall()
                    ]
                    execute_ms = int(
                        (time.perf_counter() - execute_started) * 1000
                    )
                except sqlite3.OperationalError as exc:
                    # Match search_messages() behavior: malformed FTS input is
                    # a no-result search, not a tool-level failure.  This also
                    # preserves title-only discovery when the title contains
                    # FTS punctuation.
                    logger.warning(
                        "SESSION_WINNERS query failed request_id=%s route=%s error=%s",
                        request_value,
                        route,
                        type(exc).__name__,
                    )
                    return {
                        "winners": [],
                        "stats": {
                            "candidate_count": 0,
                            "candidate_unique_sessions": 0,
                            "lineage_count": 0,
                            "winner_count": 0,
                            "lineage_work": 0,
                            "lineage_memo_hits": 0,
                            "lineage_memo_entries": 0,
                            "lineage_candidates_inspected": 0,
                            "lineage_bound_hit": False,
                            "route": route,
                        },
                    }

                candidate_count = 0
                candidate_unique_sessions = 0
                if candidates:
                    candidate_count = int(
                        candidates[0].pop("candidate_count", 0)
                    )
                    candidate_unique_sessions = int(
                        candidates[0].pop("candidate_unique_sessions", 0)
                    )

                # Exact-title exclusion arrives as pre-resolved roots in
                # ``excluded_lineage_roots`` (the caller resolves them with the
                # same compression-lineage implementation).  Full exclusion:
                # the title already occupies its slot, so its lineage members
                # must not duplicate it as content winners.
                excluded_roots = {str(root) for root in excluded_lineage_roots}

                current_root: Optional[str] = None
                current_ancestors: set = set()
                if current_session_id:
                    # Re-resolve the raw current identity inside this winner
                    # snapshot with the SAME memo/state so current-session
                    # exclusion and candidate dedupe share one root meaning.
                    outcome = self._resolve_compression_lineage_on_conn(
                        conn, str(current_session_id), state
                    )
                    if outcome is _BUDGET_EXHAUSTED:
                        state.bound_hit = True
                    elif outcome is _UNRESOLVED:
                        # Conservative: an unresolved current session excludes
                        # only its own id rather than broadening exclusion to
                        # an unproven ancestor.
                        current_root = str(current_session_id)
                    else:
                        current_root = outcome
                        current_ancestors = (
                            self._current_lineage_ancestors_on_conn(
                                conn, str(current_session_id), state
                            )
                        )
                elif current_lineage_root:
                    current_root = str(current_lineage_root)

                def _is_archived(candidate: Dict[str, Any]) -> bool:
                    """True when a hit's content left the live context."""
                    return (
                        candidate["session_end_reason"] == "compression"
                        or int(candidate["message_compacted"] or 0) == 1
                    )

                # Resolve each owner exactly ONCE (lazily, on first encounter)
                # while iterating the bounded raw hits in rank order.  Winner
                # ordering therefore follows the rank of each owner's FIRST
                # DISPLAYABLE anchor — a live current-lineage hit is skipped so
                # a later compacted-history hit of the same owner can still
                # surface, but it must not let that owner jump ahead of a
                # higher-ranked displayable winner from another session (#68
                # review round-3: ranking/winner ordering unchanged).
                owner_resolved: set = set()
                owner_root: Dict[str, Optional[str]] = {}
                for candidate in candidates:
                    if len(winners) >= result_limit:
                        break
                    owner = str(candidate["owning_session_id"])
                    if owner not in owner_resolved:
                        owner_resolved.add(owner)
                        state.candidates_inspected += 1
                        outcome = self._resolve_compression_lineage_on_conn(
                            conn, owner, state
                        )
                        if outcome is _BUDGET_EXHAUSTED:
                            state.bound_hit = True
                            # Stop the entire ranked scan: the bound candidate
                            # may have produced a higher-ranked new root than
                            # every later candidate, so skipping it would
                            # violate ranking.
                            break
                        owner_root[owner] = (
                            outcome if outcome is not _UNRESOLVED else None
                        )
                    root = owner_root[owner]
                    if root is None:
                        continue
                    if root in excluded_roots or root in seen_roots:
                        continue
                    in_current_context = (
                        (current_root is not None and root == current_root)
                        or owner in current_ancestors
                    )
                    if in_current_context and not _is_archived(candidate):
                        # live content of the current lineage stays hidden; the
                        # owner's displayable anchor is a later compacted hit,
                        # so this hit does not (yet) produce a winner.
                        continue
                    # First displayable hit of this owner in rank order.
                    seen_roots.add(root)
                    state.accepted_roots = len(seen_roots)
                    winners.append({
                        "id": candidate["message_id"],
                        "session_id": owner,
                        "role": candidate["role"],
                        "snippet": candidate["snippet"],
                        "timestamp": candidate["timestamp"],
                        "source": candidate["source"],
                        "model": candidate["model"],
                        "session_started": candidate["session_started"],
                        "lineage_root_id": root,
                        "candidate_order": candidate["candidate_order"],
                        "source_priority": candidate["source_priority"],
                    })

                if route != "like" and winners:
                    # FTS5 snippet() needs the MATCH expression on the same
                    # table cursor.  Keep the exact tokenized query used for
                    # candidate selection and apply it only after winners are
                    # known so the candidate scan stays narrow.
                    match_query = params[0]
                    snippet_sql = (
                        f"SELECT snippet({candidate_from}, 0, '>>>', '<<<', "
                        f"'...', 40) AS snippet FROM {candidate_from} "
                        f"WHERE rowid = ? AND {candidate_from} MATCH ?"
                    )
                    for winner in winners:
                        snippet_row = conn.execute(
                            snippet_sql, (winner["id"], match_query)
                        ).fetchone()
                        winner["snippet"] = (
                            snippet_row["snippet"]
                            if snippet_row is not None else None
                        )

                stats = {
                    "candidate_count": candidate_count,
                    "candidate_unique_sessions": candidate_unique_sessions,
                    "lineage_count": len(seen_roots),
                    "winner_count": len(winners),
                    "lineage_work": state.work,
                    "lineage_memo_hits": state.memo_hits,
                    "lineage_memo_entries": len(state.memo),
                    "lineage_candidates_inspected": state.candidates_inspected,
                    "lineage_bound_hit": state.bound_hit,
                    "route": route,
                }
            finally:
                if started_tx and conn.in_transaction:
                    conn.rollback()

        logger.info(
            "SESSION_WINNERS request_id=%s route=%s candidate_count=%d "
            "candidate_unique_sessions=%d lineage_count=%d winner_count=%d "
            "lineage_work=%d lineage_memo_hits=%d lineage_candidates_inspected=%d "
            "lineage_bound_hit=%s query_ms=%d execute_ms=%d",
            request_value,
            route,
            stats["candidate_count"],
            stats["candidate_unique_sessions"],
            stats["lineage_count"],
            stats["winner_count"],
            stats["lineage_work"],
            stats["lineage_memo_hits"],
            stats["lineage_candidates_inspected"],
            stats["lineage_bound_hit"],
            int((time.perf_counter() - started) * 1000),
            execute_ms,
        )
        return {"winners": winners, "stats": stats}

    def search_messages(
        self,
        query: str,
        source_filter: List[str] = None,
        exclude_sources: List[str] = None,
        role_filter: List[str] = None,
        limit: int = 20,
        offset: int = 0,
        sort: str = None,
        include_inactive: bool = False,
        fields: Optional[Collection[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Instrumented wrapper around :meth:`_search_messages_impl`.

        Logs one line per slow search with the routing path taken, so
        production latency stays attributable per query shape (the 2026-07
        session_search investigation needed trace archaeology to discover
        the LIKE full scans; this makes the next regression a grep).
        Threshold: HERMES_SEARCH_SLOW_MS (default 1000; 0 logs every call).
        """
        started = time.time()
        rows = None
        try:
            rows = self._search_messages_impl(
                query,
                source_filter=source_filter,
                exclude_sources=exclude_sources,
                role_filter=role_filter,
                limit=limit,
                offset=offset,
                sort=sort,
                include_inactive=include_inactive,
                fields=fields,
            )
            return rows
        finally:
            try:
                threshold = float(os.getenv("HERMES_SEARCH_SLOW_MS", "1000"))
            except (TypeError, ValueError):
                threshold = 1000.0
            elapsed_ms = (time.time() - started) * 1000.0
            if elapsed_ms >= threshold:
                logger.info(
                    "slow session search: path=%s elapsed=%.0fms rows=%s query=%r",
                    self._describe_search_path(query),
                    elapsed_ms,
                    len(rows) if rows is not None else "err",
                    query[:200],
                )

    def _describe_search_path(self, query: str) -> str:
        """Best-effort name of the routing path a query takes (log-only)."""
        try:
            sanitized = self._sanitize_fts5_query(query or "")
            if not sanitized:
                return "empty"
            if not self._contains_cjk(sanitized):
                return "fts5"
            raw = sanitized.strip('"').strip()
            if self._fts_cjk_available and not self._has_lone_cjk_run(raw):
                return "fts_cjk"
            tokens = [
                t for t in raw.split()
                if t.upper() not in {"AND", "OR", "NOT"} and self._contains_cjk(t)
            ]
            short = any(self._count_cjk(t) < 3 for t in tokens)
            if self._count_cjk(raw) >= 3 and not short and self._trigram_available:
                return "trigram"
            return "like_scan"
        except Exception:
            return "unknown"

    def _search_messages_impl(
        self,
        query: str,
        source_filter: List[str] = None,
        exclude_sources: List[str] = None,
        role_filter: List[str] = None,
        limit: int = 20,
        offset: int = 0,
        sort: str = None,
        include_inactive: bool = False,
        fields: Optional[Collection[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Full-text search across session messages using FTS5.

        Supports FTS5 query syntax:
          - Simple keywords: "docker deployment"
          - Phrases: '"exact phrase"'
          - Boolean: "docker OR kubernetes", "python NOT java"
          - Prefix: "deploy*"

        Returns matching messages with session metadata, content snippet,
        and surrounding context (1 message before and after the match).
        ``fields`` selects a result projection; omitting it preserves the
        complete legacy result. Context is only loaded when that projection
        consumes it.

        ``sort`` controls temporal ordering:
          - ``None`` (default): FTS5 BM25 relevance only. Time-neutral.
          - ``"newest"``: order by message timestamp DESC, then by rank.
          - ``"oldest"``: order by message timestamp ASC, then by rank.

        The short-CJK LIKE fallback already orders by timestamp DESC and
        ignores ``sort``. The trigram CJK path honours ``sort`` like the main
        FTS5 path.

        Rewound (``active=0``, ``compacted=0``) rows are excluded by default —
        the user took those back. Compaction-archived rows (``active=0``,
        ``compacted=1``) ARE included by default: they were summarized away from
        the live context but remain part of the conversation's record, so the
        pre-compaction transcript stays discoverable after in-place compaction
        (#38763). Pass ``include_inactive=True`` to search every row regardless.
        """
        result_fields = self._search_message_fields(fields)

        if not self._fts_enabled:
            return []

        if not query or not query.strip():
            return []

        query = self._sanitize_fts5_query(query)
        if not query:
            return []

        # Normalise sort. Anything not in the allowed set falls back to None
        # (FTS5 rank-only) so callers can pass through user input without
        # validation.
        if isinstance(sort, str):
            sort_norm = sort.strip().lower()
            if sort_norm not in ("newest", "oldest"):
                sort_norm = None
        else:
            sort_norm = None

        # ORDER BY shared across the main FTS5 path and trigram CJK path.
        # With sort set, timestamp is primary and rank is the tiebreaker.
        if sort_norm == "newest":
            order_by_sql = "ORDER BY m.timestamp DESC, rank"
        elif sort_norm == "oldest":
            order_by_sql = "ORDER BY m.timestamp ASC, rank"
        else:
            order_by_sql = "ORDER BY rank"

        # Build WHERE clauses dynamically
        where_clauses = ["messages_fts MATCH ?"]
        params: list = [query]
        if not include_inactive:
            # Live rows (active=1) AND compaction-archived rows (compacted=1)
            # are discoverable; only rewind/undo rows (active=0, compacted=0)
            # are hidden. See archive_and_compact() / #38763.
            where_clauses.append("(m.active = 1 OR m.compacted = 1)")

        if source_filter is not None:
            source_placeholders = ",".join("?" for _ in source_filter)
            where_clauses.append(f"s.source IN ({source_placeholders})")
            params.extend(source_filter)

        if exclude_sources is not None:
            exclude_placeholders = ",".join("?" for _ in exclude_sources)
            where_clauses.append(f"s.source NOT IN ({exclude_placeholders})")
            params.extend(exclude_sources)

        if role_filter:
            role_placeholders = ",".join("?" for _ in role_filter)
            where_clauses.append(f"m.role IN ({role_placeholders})")
            params.extend(role_filter)

        where_sql = " AND ".join(where_clauses)
        params.extend([limit, offset])

        sql = f"""
            SELECT
                m.id,
                m.session_id,
                m.role,
                snippet(messages_fts, -1, '>>>', '<<<', '...', 40) AS snippet,
                m.content,
                m.timestamp,
                m.tool_name,
                s.source,
                s.model,
                s.started_at AS session_started
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.id = m.session_id
            WHERE {where_sql}
            {order_by_sql}
            LIMIT ? OFFSET ?
        """

        # CJK queries bypass the unicode61 FTS5 table.  The default tokenizer
        # splits CJK characters into individual tokens, so "大别山项目" becomes
        # "大 AND 别 AND 山 AND 项 AND 目" — producing false positives and
        # missing exact phrase matches.
        #
        # For queries with 3+ CJK characters, we use the trigram FTS5 table
        # (indexed substring matching with ranking and snippets).  For shorter
        # CJK queries (1-2 chars), trigram can't match (it needs ≥9 UTF-8
        # bytes = 3 CJK chars), so we fall back to LIKE.
        matches: List[Dict[str, Any]] = []
        is_cjk = self._contains_cjk(query)
        if is_cjk:
            raw_query = query.strip('"').strip()
            cjk_count = self._count_cjk(raw_query)

            # Per-token CJK length check (#20494): trigram needs >=3 CJK chars
            # per token. A query like "广西 OR 桂林 OR 漓江" has cjk_count=6
            # (>=3) but each individual token is only 2 chars — trigram returns 0.
            # Route to LIKE when any non-operator CJK token is <3 CJK chars.
            _tokens_for_check = [
                t for t in raw_query.split()
                if t.upper() not in {"AND", "OR", "NOT"} and self._contains_cjk(t)
            ]
            _any_short_cjk = any(
                self._count_cjk(t) < 3 for t in _tokens_for_check
            )

            _trigram_succeeded = False
            # Tool rows are excluded from the trigram index (they're ~90% of
            # message bytes and machine noise — see FTS_TRIGRAM_SQL). A CJK
            # query explicitly filtering on role='tool' must therefore use
            # the LIKE fallback, which scans the base table directly.
            _wants_tool_rows = bool(role_filter) and "tool" in role_filter

            # ── CJK-bigram route (messages_fts_cjk, cjk_unicode61) ──────
            # When the bigram index is available it serves EVERY CJK query
            # shape the legacy code split between trigram (>=3 chars/token)
            # and LIKE full scans (1-2 char tokens) — the whole point of the
            # index (PR #65544). Exceptions stay on the legacy routes:
            #   - role_filter=['tool'] queries (tool rows aren't in the cjk
            #     index, same exclusion as trigram),
            #   - queries containing a LONE 1-char CJK run: the index stores
            #     bigrams for runs >=2, so a single-char term can only match
            #     isolated chars — LIKE substring semantics are broader.
            if (
                self._fts_cjk_available
                and not _wants_tool_rows
                and not self._has_lone_cjk_run(raw_query)
            ):
                tokens = raw_query.split()
                parts = []
                for tok in tokens:
                    if tok.upper() in {"AND", "OR", "NOT"}:
                        parts.append(tok)
                    else:
                        parts.append('"' + tok.replace('"', '""') + '"')
                cjk_query = " ".join(parts)
                cjk_where = ["messages_fts_cjk MATCH ?"]
                cjk_params: list = [cjk_query]
                if not include_inactive:
                    cjk_where.append("(m.active = 1 OR m.compacted = 1)")
                if source_filter is not None:
                    cjk_where.append(f"s.source IN ({','.join('?' for _ in source_filter)})")
                    cjk_params.extend(source_filter)
                if exclude_sources is not None:
                    cjk_where.append(f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})")
                    cjk_params.extend(exclude_sources)
                if role_filter:
                    cjk_where.append(f"m.role IN ({','.join('?' for _ in role_filter)})")
                    cjk_params.extend(role_filter)
                cjk_sql = f"""
                    SELECT
                        m.id,
                        m.session_id,
                        m.role,
                        snippet(messages_fts_cjk, -1, '>>>', '<<<', '...', 40) AS snippet,
                        m.content,
                        m.timestamp,
                        m.tool_name,
                        s.source,
                        s.model,
                        s.started_at AS session_started
                    FROM messages_fts_cjk
                    JOIN messages m ON m.id = messages_fts_cjk.rowid
                    JOIN sessions s ON s.id = m.session_id
                    WHERE {' AND '.join(cjk_where)}
                    {order_by_sql}
                    LIMIT ? OFFSET ?
                """
                cjk_params.extend([limit, offset])
                try:
                    with self._read_ctx() as conn:
                        cjk_cursor = conn.execute(cjk_sql, cjk_params)
                        matches = [dict(row) for row in cjk_cursor.fetchall()]
                        _trigram_succeeded = True
                except sqlite3.OperationalError:
                    # Tokenizer missing on this connection / query syntax —
                    # the trigram + LIKE routes below still answer.
                    logger.debug(
                        "messages_fts_cjk query failed; falling back to "
                        "trigram/LIKE", exc_info=True,
                    )
                except sqlite3.DatabaseError as exc:
                    # Same corruption class as the other FTS reads: rebuild
                    # in place once and retry; on refusal/failure fall back.
                    if self._try_runtime_fts_rebuild(exc):
                        try:
                            with self._read_ctx() as conn:
                                cjk_cursor = conn.execute(
                                    cjk_sql, cjk_params
                                )
                                matches = [
                                    dict(row) for row in cjk_cursor.fetchall()
                                ]
                                _trigram_succeeded = True
                        except sqlite3.DatabaseError:
                            logger.warning(
                                "CJK-bigram FTS search still failing after "
                                "in-place rebuild; falling back to "
                                "trigram/LIKE."
                            )
                    else:
                        logger.warning(
                            "CJK-bigram FTS search hit a corruption error "
                            "(%s) and no in-place rebuild was possible; "
                            "falling back to trigram/LIKE.", exc,
                        )

            if (
                not _trigram_succeeded
                and cjk_count >= 3
                and not _any_short_cjk
                and self._trigram_available
                and not _wants_tool_rows
            ):
                # Trigram FTS5 path — quote each non-operator token to handle
                # FTS5 special chars (%, *, etc.) while preserving boolean
                # operators (AND, OR, NOT) for multi-term queries.
                tokens = raw_query.split()
                parts = []
                for tok in tokens:
                    if tok.upper() in {"AND", "OR", "NOT"}:
                        parts.append(tok)
                    else:
                        parts.append('"' + tok.replace('"', '""') + '"')
                trigram_query = " ".join(parts)
                tri_where = ["messages_fts_trigram MATCH ?"]
                tri_params: list = [trigram_query]
                if not include_inactive:
                    tri_where.append("(m.active = 1 OR m.compacted = 1)")
                if source_filter is not None:
                    tri_where.append(f"s.source IN ({','.join('?' for _ in source_filter)})")
                    tri_params.extend(source_filter)
                if exclude_sources is not None:
                    tri_where.append(f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})")
                    tri_params.extend(exclude_sources)
                if role_filter:
                    tri_where.append(f"m.role IN ({','.join('?' for _ in role_filter)})")
                    tri_params.extend(role_filter)
                tri_sql = f"""
                    SELECT
                        m.id,
                        m.session_id,
                        m.role,
                        snippet(messages_fts_trigram, -1, '>>>', '<<<', '...', 40) AS snippet,
                        m.content,
                        m.timestamp,
                        m.tool_name,
                        s.source,
                        s.model,
                        s.started_at AS session_started
                    FROM messages_fts_trigram
                    JOIN messages m ON m.id = messages_fts_trigram.rowid
                    JOIN sessions s ON s.id = m.session_id
                    WHERE {' AND '.join(tri_where)}
                    {order_by_sql}
                    LIMIT ? OFFSET ?
                """
                tri_params.extend([limit, offset])
                try:
                    with self._read_ctx() as conn:
                        tri_cursor = conn.execute(tri_sql, tri_params)
                        matches = [dict(row) for row in tri_cursor.fetchall()]
                        _trigram_succeeded = True
                except sqlite3.OperationalError:
                    # Trigram query failed at runtime — fall through to LIKE.
                    pass
                except sqlite3.DatabaseError as exc:
                    # Same corruption class the main FTS5 MATCH branch
                    # self-heals above: a corrupt trigram shadow table raises
                    # malformed / "fts5: corrupt structure record", which is a
                    # DatabaseError (parent of the OperationalError syntax arm
                    # caught first). Rebuild once outside the lock — the lock
                    # is released here so rebuild_fts() can re-acquire it —
                    # and retry the trigram query. If the rebuild is refused
                    # (already attempted / FTS disabled / different error
                    # class) or the retry fails again, fall through to the
                    # LIKE substring path, which reads only the canonical
                    # messages table, so CJK search stays available.
                    if self._try_runtime_fts_rebuild(exc):
                        try:
                            with self._read_ctx() as conn:
                                tri_cursor = conn.execute(
                                    tri_sql, tri_params
                                )
                                matches = [
                                    dict(row) for row in tri_cursor.fetchall()
                                ]
                                _trigram_succeeded = True
                        except sqlite3.DatabaseError:
                            logger.warning(
                                "Trigram FTS search still failing after "
                                "in-place rebuild; falling back to LIKE."
                            )
                    else:
                        logger.warning(
                            "Trigram FTS search hit a corruption error (%s) "
                            "and no in-place rebuild was possible; falling "
                            "back to LIKE.", exc,
                        )
            if not _trigram_succeeded:
                # Short / mixed CJK query, trigram unavailable, or trigram
                # <3 CJK chars. Fall back to LIKE substring search.
                # For multi-token OR queries (e.g. "广西 OR 桂林 OR 漓江"),
                # build one LIKE condition per non-operator token so each term
                # is matched independently (#20494).
                non_op_tokens = [
                    t for t in raw_query.split()
                    if t.upper() not in {"AND", "OR", "NOT"}
                ] or [raw_query]
                token_clauses = []
                like_params: list = []
                for tok in non_op_tokens:
                    esc = tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    token_clauses.append(
                        "(m.content LIKE ? ESCAPE '\\' OR m.tool_name LIKE ? ESCAPE '\\' OR m.tool_calls LIKE ? ESCAPE '\\')"
                    )
                    like_params += [f"%{esc}%", f"%{esc}%", f"%{esc}%"]
                like_where = [f"({' OR '.join(token_clauses)})"]
                if not include_inactive:
                    # Same visibility rule as the FTS5 paths: live rows and
                    # compaction-archived rows are discoverable; rewind/undo
                    # rows (active=0, compacted=0) are hidden (#38763).
                    like_where.append("(m.active = 1 OR m.compacted = 1)")
                if source_filter is not None:
                    like_where.append(f"s.source IN ({','.join('?' for _ in source_filter)})")
                    like_params.extend(source_filter)
                if exclude_sources is not None:
                    like_where.append(f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})")
                    like_params.extend(exclude_sources)
                if role_filter:
                    like_where.append(f"m.role IN ({','.join('?' for _ in role_filter)})")
                    like_params.extend(role_filter)
                like_sql = f"""
                    SELECT m.id, m.session_id, m.role,
                           substr(m.content,
                                  max(1, instr(m.content, ?) - 40),
                                  120) AS snippet,
                           m.content, m.timestamp, m.tool_name,
                           s.source, s.model, s.started_at AS session_started
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE {' AND '.join(like_where)}
                    ORDER BY m.timestamp DESC
                    LIMIT ? OFFSET ?
                """
                like_params.extend([limit, offset])
                # instr() for snippet uses first search token
                like_params = [non_op_tokens[0]] + like_params
                with self._read_ctx() as conn:
                    like_cursor = conn.execute(like_sql, like_params)
                    matches = [dict(row) for row in like_cursor.fetchall()]
        else:
            try:
                with self._read_ctx() as conn:
                    cursor = conn.execute(sql, params)
                    matches = [dict(row) for row in cursor.fetchall()]
            except sqlite3.OperationalError:
                # FTS5 query syntax error despite sanitization — return empty
                return []
            except sqlite3.DatabaseError as exc:
                # A corrupt FTS index raises the malformed / "fts5: corrupt
                # structure record" class on the MATCH read, the same class the
                # write path self-heals (#66296). OperationalError (query
                # syntax) is a subclass caught above; this arm is the corruption
                # parent. Rebuild the index in place once — the read context
                # holds no writer lock, so rebuild_fts() can acquire it — and
                # retry, so search self-heals for read-only sessions (cron/CLI
                # history search) that never trigger a write to repair it first.
                if not self._try_runtime_fts_rebuild(exc):
                    raise
                with self._read_ctx() as conn:
                    cursor = conn.execute(sql, params)
                    matches = [dict(row) for row in cursor.fetchall()]

        # Deferred-rebuild supplement (schema v23): while the background
        # backfill is pending, the FTS indexes only cover rows outside the
        # (progress, high_water] gap. Top the results up with a bounded LIKE
        # scan over just that id range so search never silently loses old
        # messages mid-rebuild. The range shrinks as the backfill advances,
        # so this cost decays to zero. The CJK LIKE-fallback path above
        # already scans the whole base table and needs no supplement.
        rebuild_status = self.fts_rebuild_status()
        if rebuild_status is not None and len(matches) < limit:
            try:
                gap_matches = self._search_unindexed_gap(
                    query,
                    limit - len(matches),
                    include_inactive=include_inactive,
                    source_filter=source_filter,
                    exclude_sources=exclude_sources,
                    role_filter=role_filter,
                )
                seen_ids = {m["id"] for m in matches}
                matches.extend(m for m in gap_matches if m["id"] not in seen_ids)
            except sqlite3.OperationalError as exc:
                logger.debug("Unindexed-gap supplement skipped: %s", exc)

        # Pure-Latin queries run against the unicode61 ``messages_fts`` table,
        # whose tokenizer does not insert a boundary between Latin letters and
        # adjacent CJK characters: "修改youer服务端" is indexed as one token,
        # so MATCH "youer" finds nothing even though the substring is present
        # (#54242). When the exact-token search returns nothing, retry on the
        # substring-capable indexes. Preference order:
        #   1. messages_fts_cjk (when built): its tokenizer splits Latin runs
        #      off adjacent CJK, so "youer" is an exact ranked token match.
        #   2. messages_fts_trigram: substring matching, needs >=3-char
        #      tokens (shorter tokens produce no trigrams).
        # Gated on a zero-result miss so successful Latin searches keep their
        # unicode61 ranking — strictly additive, never reorders existing
        # hits. Trade-off on the trigram leg: any zero-result Latin query
        # gains substring semantics (e.g. "cat" can then match
        # "concatenate"). Genuinely absent terms still return []. Skipped for
        # role_filter=['tool'] queries — both fallback indexes exclude tool
        # rows (v23), so a retry could never add hits.
        if (
            not matches
            and not is_cjk
            and not (bool(role_filter) and "tool" in role_filter)
        ):
            _fb_query = query.strip('"').strip()
            if self._fts_cjk_available:
                cjk_fb = self._run_trigram_search(
                    _fb_query,
                    table="messages_fts_cjk",
                    order_by_sql=order_by_sql,
                    include_inactive=include_inactive,
                    source_filter=source_filter,
                    exclude_sources=exclude_sources,
                    role_filter=role_filter,
                    limit=limit,
                    offset=offset,
                )
                if cjk_fb:
                    matches = cjk_fb
            if (
                not matches
                and self._trigram_available
                and self._trigram_eligible_tokens(query)
            ):
                tri_matches = self._run_trigram_search(
                    _fb_query,
                    order_by_sql=order_by_sql,
                    include_inactive=include_inactive,
                    source_filter=source_filter,
                    exclude_sources=exclude_sources,
                    role_filter=role_filter,
                    limit=limit,
                    offset=offset,
                )
                if tri_matches:
                    matches = tri_matches

        # Add surrounding context (1 message before + after each match) only
        # when the selected result projection consumes it. Each query takes
        # its own fresh read transaction via _read_ctx, so we never hold a
        # lock across N sequential queries.
        context_matches = (
            matches if result_fields is None or "context" in result_fields else ()
        )
        for match in context_matches:
            try:
                with self._read_ctx() as conn:
                    ctx_cursor = conn.execute(
                        """WITH target AS (
                               SELECT session_id, timestamp, id
                               FROM messages
                               WHERE id = ?
                           )
                           SELECT role, content
                           FROM (
                               SELECT m.id, m.timestamp, m.role, m.content
                               FROM messages m
                               JOIN target t ON t.session_id = m.session_id
                               WHERE (m.timestamp < t.timestamp)
                                  OR (m.timestamp = t.timestamp AND m.id < t.id)
                               ORDER BY m.timestamp DESC, m.id DESC
                               LIMIT 1
                           )
                           UNION ALL
                           SELECT role, content
                           FROM messages
                           WHERE id = ?
                           UNION ALL
                           SELECT role, content
                           FROM (
                               SELECT m.id, m.timestamp, m.role, m.content
                               FROM messages m
                               JOIN target t ON t.session_id = m.session_id
                               WHERE (m.timestamp > t.timestamp)
                                  OR (m.timestamp = t.timestamp AND m.id > t.id)
                               ORDER BY m.timestamp ASC, m.id ASC
                               LIMIT 1
                           )""",
                        (match["id"], match["id"]),
                    )
                    context_msgs = []
                    for r in ctx_cursor.fetchall():
                        raw = r["content"]
                        decoded = self._decode_content(raw)
                        # Multimodal context: render a compact text-only
                        # summary for search previews.
                        if isinstance(decoded, list):
                            text_parts = [
                                p.get("text", "") for p in decoded
                                if isinstance(p, dict) and p.get("type") == "text"
                            ]
                            text = " ".join(t for t in text_parts if t).strip()
                            preview = text or "[multimodal content]"
                        elif isinstance(decoded, str):
                            preview = decoded
                        else:
                            preview = ""
                        context_msgs.append(
                            {"role": r["role"], "content": preview[:200]}
                        )
                match["context"] = context_msgs
            except Exception:
                match["context"] = []

        # Remove full content from result (snippet is enough, saves tokens)
        for match in matches:
            match.pop("content", None)

        if result_fields is not None:
            matches = [
                {field: match[field] for field in result_fields if field in match}
                for match in matches
            ]

        return matches

    def _search_unindexed_gap(
        self,
        fts_query: str,
        limit: int,
        *,
        include_inactive: bool = False,
        source_filter: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
        role_filter: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """LIKE-scan the rows the deferred rebuild hasn't indexed yet.

        Only touches ids in (fts_rebuild_progress, fts_rebuild_high_water] —
        a range that shrinks to nothing as the backfill advances. The FTS
        query is degraded to per-token substring terms (AND-joined; quoted
        phrases kept whole), which is deliberately recall-over-precision:
        temporary results beat silently missing ones mid-rebuild.
        """
        status = self.fts_rebuild_status()
        if status is None or limit <= 0:
            return []
        progress, high_water = status["indexed"], status["total"]

        # Degrade the FTS query to LIKE terms: strip operators/wildcards,
        # keep quoted phrases intact, AND the rest.
        terms: List[str] = []
        for raw_tok in re.findall(r'"[^"]+"|\S+', fts_query):
            tok = raw_tok.strip('"').strip("*").strip()
            if not tok or tok.upper() in {"AND", "OR", "NOT", "NEAR"}:
                continue
            terms.append(tok)
        if not terms:
            return []

        where = ["m.id > ? AND m.id <= ?"]
        params: list = [progress, high_water]
        for term in terms:
            esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append(
                "(m.content LIKE ? ESCAPE '\\' OR m.tool_name LIKE ? ESCAPE '\\' "
                "OR m.tool_calls LIKE ? ESCAPE '\\')"
            )
            params += [f"%{esc}%"] * 3
        if not include_inactive:
            where.append("(m.active = 1 OR m.compacted = 1)")
        if source_filter is not None:
            where.append(f"s.source IN ({','.join('?' for _ in source_filter)})")
            params.extend(source_filter)
        if exclude_sources is not None:
            where.append(f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})")
            params.extend(exclude_sources)
        if role_filter:
            where.append(f"m.role IN ({','.join('?' for _ in role_filter)})")
            params.extend(role_filter)

        sql = f"""
            SELECT m.id, m.session_id, m.role,
                   substr(m.content,
                          max(1, instr(m.content, ?) - 40),
                          120) AS snippet,
                   m.content, m.timestamp, m.tool_name,
                   s.source, s.model, s.started_at AS session_started
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE {' AND '.join(where)}
            ORDER BY m.timestamp DESC
            LIMIT ?
        """
        params = [terms[0]] + params + [limit]
        with self._read_ctx() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search_sessions_by_id(
        self,
        query: str,
        limit: int = 20,
        include_archived: bool = True,
        source: str = None,
        sources: List[str] = None,
        exclude_sources: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search surfaced sessions by exact/prefix/substring session id.

        Desktop search uses this alongside FTS message search so users can paste
        a session id from logs, CLI output, or another Hermes surface and jump
        straight to that conversation.  Matching also checks ``_lineage_root_id``
        for projected compression-chain tips, so an old root id still resolves to
        the live continuation row.
        """
        needle = (query or "").strip().lower()
        if not needle or limit <= 0:
            return []

        # SQL-bounded: list_sessions_rich pushes the id LIKE filter into the
        # query (matching the row's own id AND any id in its forward
        # compression chain), so we only materialize matching rows instead of
        # scanning every session. Fetch a small multiple of `limit` so the
        # in-Python exact/prefix/substring ranking below has enough candidates
        # to order, then truncate.
        candidates = self.list_sessions_rich(
            source=source,
            sources=sources,
            exclude_sources=exclude_sources,
            limit=max(limit * 4, limit),
            offset=0,
            include_archived=include_archived,
            order_by_last_active=True,
            id_query=needle,
        )

        def score(row: Dict[str, Any]) -> int:
            ids = [str(row.get("id") or ""), str(row.get("_lineage_root_id") or "")]
            normalized = [value.lower() for value in ids if value]
            if any(value == needle for value in normalized):
                return 0
            if any(value.startswith(needle) for value in normalized):
                return 1
            return 2

        ranked = sorted(
            enumerate(candidates),
            key=lambda item: (score(item[1]), item[0]),
        )
        return [row for _, row in ranked[:limit]]

    def _fts_table_exists(self, name: str) -> bool:
        """True if an FTS5 virtual table is queryable in this DB."""
        try:
            self._conn.execute(f"SELECT 1 FROM {name} LIMIT 0")
            return True
        except sqlite3.DatabaseError:
            # OperationalError ("no such table") or the broader
            # DatabaseError class ("vtable constructor failed", raised when
            # e.g. a required tokenizer is missing or the table is mid-
            # teardown) — in every case the table is not queryable.
            return False

    def _fts_maintenance_tables(self) -> Tuple[str, ...]:
        """Ordered FTS tables applicable to ordinary maintenance (optimize /
        bounded merge / explicit rebuild) on THIS host, derived from the
        authoritative ``FTS_INDEXES`` registry (issue #27).

        Required Unicode indexes (``messages_fts``, ``sessions_fts``)
        participate whenever FTS5 is enabled. Optional tokenizer-gated
        indexes participate only when this process can operate them AND they
        are not quarantined/stale:

        - ``messages_fts_trigram`` — ``self._trigram_available``;
        - ``messages_fts_cjk`` — ``self._fts_cjk_loaded``;
        - ``sessions_fts_cjk`` — ``self._sessions_cjk_worker_operable``
          (worker operability, never search-serving availability);
        - ``sessions_fts_trigram`` — ``self._sessions_trigram_available``.

        The #30 ownership classifier leaves ``_sessions_trigram_available``
        False for an unknown same-name object, so a foreign target is never
        touched by ordinary maintenance. Callers keep the per-table existence
        probe as a safety net for tables absent from the schema.
        """
        if not self._fts_enabled:
            return ()
        sessions_fts_ok = getattr(self, "_sessions_fts_available", False)
        sessions_trigram_ok = getattr(self, "_sessions_trigram_available", False)
        sessions_cjk_ok = getattr(self, "_sessions_cjk_worker_operable", False)
        tables: List[str] = []
        for desc in FTS_INDEXES:
            if desc.capability == "fts5":
                if desc.table == "sessions_fts" and not sessions_fts_ok:
                    continue
                tables.append(desc.table)
            elif desc.capability == "trigram":
                if desc.table == "sessions_fts_trigram":
                    if sessions_trigram_ok:
                        tables.append(desc.table)
                elif self._trigram_available:
                    tables.append(desc.table)
            else:  # cjk
                if desc.table == "sessions_fts_cjk":
                    if sessions_cjk_ok:
                        tables.append(desc.table)
                elif self._fts_cjk_loaded:
                    tables.append(desc.table)
        return tuple(tables)

    def optimize_fts(self) -> int:
        """Merge fragmented FTS5 b-tree segments into one per index.

        FTS5 indexes grow as a series of incremental segments — one per
        ``INSERT`` batch driven by the message triggers. Over tens of
        thousands of messages these segments accumulate, which both bloats
        the ``*_data`` shadow tables and slows ``MATCH`` queries that must
        scan every segment. The special ``'optimize'`` command rewrites each
        index as a single merged segment.

        This is purely a maintenance operation — it changes neither search
        results nor ``snippet()`` output, only on-disk layout and query
        speed. It is complementary to VACUUM: ``optimize`` compacts the FTS
        index internally, then VACUUM returns the freed pages to the OS.

        Iterates the authoritative ``FTS_INDEXES`` registry (issue #27):
        every owned applicable modern index — the three message indexes AND
        the session Unicode/CJK/trigram metadata indexes — participates
        through the shared applicability path. Optional tokenizer-gated
        indexes participate only when this host can operate them; an unknown
        #30 same-name ``sessions_fts_trigram`` is never touched. Skips any
        FTS table that does not exist, so it is safe to call
        unconditionally.

        Returns the number of FTS indexes that were optimized.
        """
        optimized = 0
        with self._lock:
            for tbl in self._fts_maintenance_tables():
                if not self._fts_table_exists(tbl):
                    continue
                try:
                    # The column name in the INSERT must match the table name
                    # for FTS5 special commands.
                    self._conn.execute(
                        f"INSERT INTO {tbl}({tbl}) VALUES('optimize')"
                    )
                    optimized += 1
                except sqlite3.OperationalError as exc:
                    logger.warning(
                        "FTS optimize failed for %s: %s", tbl, exc
                    )
        return optimized

    def rebuild_fts(self) -> int:
        """Rebuild FTS5 indexes from their canonical content sources.

        Uses the FTS5 ``'rebuild'`` command, which rewrites the internal
        b-tree segments from each table's declared content source. This is
        the documented recovery for a corrupt FTS index that rejects writes
        while reads still succeed (issue #50502). Unlike ``optimize_fts``
        (which merges existing segments), ``rebuild`` discards and recreates
        the index data entirely.

        Iterates the authoritative ``FTS_INDEXES`` registry (issue #27):
        every owned applicable modern index is rebuilt, so a corrupt session
        Unicode/CJK/trigram index is recovered by the same runtime seam that
        already covers the message indexes. FTS5's ``rebuild`` command reads
        through each table's declared ``content=`` source, so
        ``sessions_fts_trigram`` naturally rebuilds through the derived
        compact ``sessions_fts_trigram_src`` VIEW — no compact SQL is
        duplicated here. Optional tokenizer-gated indexes participate only
        when this host can operate them; an unknown #30 same-name
        ``sessions_fts_trigram`` is never touched.

        Safe to call when FTS tables don't exist (skips them).
        Returns the number of FTS indexes that were rebuilt.
        """
        rebuilt = 0
        with self._lock:
            for tbl in self._fts_maintenance_tables():
                if not self._fts_table_exists(tbl):
                    continue
                try:
                    self._conn.execute(
                        f"INSERT INTO {tbl}({tbl}) VALUES('rebuild')"
                    )
                    self._conn.commit()
                    rebuilt += 1
                except sqlite3.OperationalError as exc:
                    self._conn.rollback()
                    logger.warning(
                        "FTS rebuild failed for %s: %s", tbl, exc
                    )
        return rebuilt

    def _merge_fts_incrementally(
        self, *, max_pages: int, max_commands: Optional[int] = None
    ) -> int:
        """Run bounded FTS5 ``'merge'`` commands against each present index.

        A positive merge rank tells SQLite to stop after approximately that
        many output pages, so each command holds the write lock for
        milliseconds regardless of index size — unlike ``'optimize'``, which
        rewrites the whole index in one transaction (measured 9-18 s per
        index on a 10 GB production DB, long enough to exhaust a competing
        writer's entire lock-retry patience).

        Protocol (SQLite FTS5 §6.8-6.9):

        - ``usermerge`` is lowered to its minimum of 2 (persisted in the
          ``%_config`` shadow table, applied once per instance) so a
          positive merge acts on ANY level holding >= 2 segments. With the
          default of 4, levels below that threshold are never merged by a
          positive-rank command and a fragmented index cannot converge.
        - Up to *max_commands* merge commands run per index, stopping early
          on the documented no-progress signal: the delta in
          ``total_changes`` is < 2 (the command's own INSERT accounts
          for 1 change; >= 2 means real merge work happened).

        Each command is its own implicit transaction (the connection runs
        with ``isolation_level=None``), so the SQLite write lock is released
        between commands and competing processes can interleave writes
        mid-pass. The applicable index set comes from the authoritative
        ``FTS_INDEXES`` registry (issue #27), so the bounded merge reaches
        the session Unicode/CJK/trigram metadata indexes through the same
        shared applicability path — no session-specific cadence. Missing
        tables are valid schema variants (FTS variants are
        optional, and ``optimize_fts_storage`` legitimately drops + backfills
        these tables while writers keep running) and are skipped, mirroring
        ``optimize_fts``. Other SQLite errors propagate to the caller.

        Returns the number of merge commands executed.
        """
        if isinstance(max_pages, bool) or not isinstance(max_pages, int):
            raise TypeError("max_pages must be an integer")
        if max_pages <= 0:
            raise ValueError("max_pages must be greater than zero")
        if max_commands is None:
            max_commands = self._FTS_MERGE_COMMANDS_PER_PASS
        if isinstance(max_commands, bool) or not isinstance(max_commands, int):
            raise TypeError("max_commands must be an integer")
        if max_commands <= 0:
            raise ValueError("max_commands must be greater than zero")

        executed = 0
        with self._lock:
            for tbl in self._fts_maintenance_tables():
                if not self._fts_table_exists(tbl):
                    continue
                # One-time (per instance) usermerge floor; the value is
                # persisted in the index's config shadow table so future
                # connections inherit it. Setting config is a metadata-only
                # write — it never touches segment data.
                if not getattr(self, "_fts_usermerge_floor_applied", False):
                    self._conn.execute(
                        f"INSERT INTO {tbl}({tbl}, rank) "
                        "VALUES('usermerge', 2)"
                    )
                for _ in range(max_commands):
                    before = self._conn.total_changes
                    self._conn.execute(
                        f"INSERT INTO {tbl}({tbl}, rank) VALUES('merge', ?)",
                        (max_pages,),
                    )
                    executed += 1
                    if self._conn.total_changes - before < 2:
                        break
            self._fts_usermerge_floor_applied = True
        return executed
