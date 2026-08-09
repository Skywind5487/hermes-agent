"""Compression-lineage aware session-search winner selection.

This module intentionally subclasses the mature FTS/search mixin instead of
forking the entire search implementation. Candidate generation/ranking stays
identical to ``hermes_state_search.SessionSearchMixin``; only the lineage
resolver and winner-selection tail differ.

See #54 / #62.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from hermes_state_search import SessionSearchMixin as _BaseSessionSearchMixin


logger = logging.getLogger("hermes_state")

_LINEAGE_WORK_BUDGET = 1500

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
    """Return config validity plus parent-bound branch/delegate markers."""
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


class _LineageState:
    __slots__ = ("budget", "work", "bound_hit", "memo")

    def __init__(self, budget: int) -> None:
        self.budget = max(1, int(budget))
        self.work = 0
        self.bound_hit = False
        self.memo: Dict[str, str] = {}


class SessionSearchMixin(_BaseSessionSearchMixin):
    """Search mixin with compression-only lineage resolution."""

    def _resolve_compression_lineage_on_conn(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        state: _LineageState,
    ) -> Optional[str]:
        cached = state.memo.get(session_id)
        if cached is not None:
            return cached

        path: List[str] = []
        seen: set[str] = set()
        node = session_id

        while True:
            cached = state.memo.get(node)
            if cached is not None:
                root = cached
                break
            if node in seen:
                return None
            seen.add(node)

            if state.work >= state.budget:
                state.bound_hit = True
                return None

            row = conn.execute(_LINEAGE_NODE_SQL, (node,)).fetchone()
            if row is None:
                return None
            state.work += 1

            current = str(row["id"])
            path.append(current)
            parent_id = row["parent_session_id"]

            if parent_id is None:
                root = current
                break
            parent_id = str(parent_id)

            # A dangling parent is malformed lineage state. Do not fabricate
            # the current node as a root because that would leak a duplicate
            # lineage into winner selection.
            if row["parent_exists"] is None:
                return None

            config_valid, branched_from, delegate_from = _lineage_markers(
                row["model_config"]
            )
            is_positive_compression_edge = (
                config_valid
                and row["parent_end_reason"] == "compression"
                and row["source"] != "tool"
                and branched_from != parent_id
                and delegate_from != parent_id
            )
            if not is_positive_compression_edge:
                root = current
                break

            node = parent_id

        for visited in path:
            state.memo[visited] = root
        return root

    def resolve_compression_lineage(
        self,
        session_id: str,
        *,
        work_budget: int = _LINEAGE_WORK_BUDGET,
    ) -> Optional[str]:
        """Resolve one session to its positive compression-continuation root.

        Generic parentage (branch/delegation/tool children) is not lineage.
        Missing rows, dangling parents, cycles, and budget exhaustion fail
        closed by returning ``None``.
        """
        if not session_id:
            return None
        with self._read_ctx() as conn:
            started_tx = not conn.in_transaction
            if started_tx:
                conn.execute("BEGIN")
            try:
                state = _LineageState(work_budget)
                return self._resolve_compression_lineage_on_conn(
                    conn, str(session_id), state
                )
            finally:
                if started_tx and conn.in_transaction:
                    conn.rollback()

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
    ) -> Dict[str, Any]:
        """Select session-search winners using lazy memoized lineage resolution.

        Candidate generation and ranking mirror the mature SQLite path. The
        recursive generic-parent CTE is replaced with ranked candidate-by-
        candidate compression-lineage resolution under one read snapshot.
        ``lineage_depth_cap`` remains in the signature for caller compatibility;
        safety is now governed by the global successful-row-lookup budget.
        """
        del lineage_depth_cap

        empty = {
            "winners": [],
            "stats": {
                "candidate_count": 0,
                "candidate_unique_sessions": 0,
                "lineage_count": 0,
                "winner_count": 0,
                "lineage_work": 0,
                "lineage_bound_hit": False,
                "route": "none",
            },
        }
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
        excluded_roots = {
            str(root) for root in (excluded_lineage_roots or ()) if root
        }
        if current_lineage_root:
            excluded_roots.add(str(current_lineage_root))

        sort_norm = sort.strip().lower() if isinstance(sort, str) else None
        if sort_norm not in ("newest", "oldest"):
            sort_norm = None

        if sort_norm == "newest":
            candidate_order = "timestamp DESC, fts_rank ASC, message_id ASC"
        elif sort_norm == "oldest":
            candidate_order = "timestamp ASC, fts_rank ASC, message_id ASC"
        else:
            candidate_order = "fts_rank ASC, message_id ASC"

        def _where(prefix: str, out_params: list) -> str:
            clauses = [f"{prefix} MATCH ?"]
            out_params.append(query)
            if not include_inactive:
                clauses.append("(m.active = 1 OR m.compacted = 1)")
            if source_filter:
                clauses.append(
                    f"s.source IN ({','.join('?' for _ in source_filter)})"
                )
                out_params.extend(source_filter)
            if exclude_sources:
                clauses.append(
                    f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})"
                )
                out_params.extend(exclude_sources)
            if role_filter:
                clauses.append(
                    f"m.role IN ({','.join('?' for _ in role_filter)})"
                )
                out_params.extend(role_filter)
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
            candidate_select = "NULL"
            candidate_from = "messages_fts_trigram"
            candidate_where = _where("messages_fts_trigram", params)
            params[0] = " ".join(parts)
        elif self._contains_cjk(query):
            route = "like"
            raw_query = query.strip('"').strip()
            tokens = [
                token
                for token in raw_query.split()
                if token.upper() not in {"AND", "OR", "NOT"}
            ] or [raw_query]
            token_clauses = []
            like_values = []
            for token in tokens:
                escaped = (
                    token.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
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
                clauses.append(
                    f"s.source IN ({','.join('?' for _ in source_filter)})"
                )
                like_values.extend(source_filter)
            if exclude_sources:
                clauses.append(
                    f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})"
                )
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
            candidate_select = "NULL"
            candidate_from = "messages_fts"
            candidate_where = _where("messages_fts", params)

        if route != "like":
            params.extend([candidate_limit, 0])

        source_priority = (
            "CASE WHEN COALESCE(source, '') IN ('cron') THEN 1 ELSE 0 END"
        )
        use_ranked_prelimit = route != "like" and sort_norm is None

        if route == "like":
            ranked_candidates = ""
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
                    {source_priority} AS source_priority
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
                    {source_priority} AS source_priority
                FROM {candidate_from}
                JOIN messages m ON m.id = {candidate_from}.rowid
                JOIN sessions s ON s.id = m.session_id
                WHERE {candidate_where}
            """
            if use_ranked_prelimit:
                ranked_candidates = f"""
            ranked_candidates AS (
                {fts_candidate}
                ORDER BY rank, m.id
                LIMIT {candidate_limit} OFFSET 0
            ),
            """
                candidate_base = "SELECT * FROM ranked_candidates"
            else:
                ranked_candidates = ""
                candidate_base = fts_candidate

        candidate_sql = f"""
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
            )
            SELECT *
            FROM candidate_hits
            ORDER BY source_priority, candidate_order
        """

        request_value = request_id or "-"
        started = time.perf_counter()

        with self._read_ctx() as conn:
            started_tx = not conn.in_transaction
            if started_tx:
                conn.execute("BEGIN")
            try:
                try:
                    execute_started = time.perf_counter()
                    candidates = [
                        dict(row) for row in conn.execute(candidate_sql, params).fetchall()
                    ]
                    execute_ms = int(
                        (time.perf_counter() - execute_started) * 1000
                    )
                except sqlite3.OperationalError as exc:
                    logger.warning(
                        "SESSION_WINNERS query failed request_id=%s route=%s error=%s",
                        request_value,
                        route,
                        type(exc).__name__,
                    )
                    failed = dict(empty)
                    failed["stats"] = dict(empty["stats"])
                    failed["stats"]["route"] = route
                    return failed

                state = _LineageState(_LINEAGE_WORK_BUDGET)
                winners: List[Dict[str, Any]] = []
                seen_roots: set[str] = set()

                for candidate in candidates:
                    if len(winners) >= result_limit:
                        break
                    root = self._resolve_compression_lineage_on_conn(
                        conn,
                        str(candidate["owning_session_id"]),
                        state,
                    )
                    if root is None:
                        if state.bound_hit:
                            break
                        continue
                    if root in excluded_roots or root in seen_roots:
                        continue

                    seen_roots.add(root)
                    winner = {
                        "id": candidate["message_id"],
                        "session_id": candidate["owning_session_id"],
                        "role": candidate["role"],
                        "snippet": candidate["snippet"],
                        "timestamp": candidate["timestamp"],
                        "source": candidate["source"],
                        "model": candidate["model"],
                        "session_started": candidate["session_started"],
                        "lineage_root_id": root,
                        "candidate_order": candidate["candidate_order"],
                        "source_priority": candidate["source_priority"],
                    }
                    winners.append(winner)

                if route != "like" and winners:
                    match_query = params[0]
                    snippet_sql = (
                        f"SELECT snippet({candidate_from}, 0, '>>>', '<<<', '...', 40) "
                        f"AS snippet FROM {candidate_from} "
                        f"WHERE rowid = ? AND {candidate_from} MATCH ?"
                    )
                    for winner in winners:
                        snippet_row = conn.execute(
                            snippet_sql, (winner["id"], match_query)
                        ).fetchone()
                        winner["snippet"] = (
                            snippet_row["snippet"] if snippet_row is not None else None
                        )

                stats = {
                    "candidate_count": len(candidates),
                    "candidate_unique_sessions": len(
                        {
                            str(candidate["owning_session_id"])
                            for candidate in candidates
                        }
                    ),
                    "lineage_count": len(seen_roots),
                    "winner_count": len(winners),
                    "lineage_work": state.work,
                    "lineage_bound_hit": state.bound_hit,
                    "route": route,
                }
            finally:
                if started_tx and conn.in_transaction:
                    conn.rollback()

        logger.info(
            "SESSION_WINNERS request_id=%s route=%s candidate_count=%d "
            "candidate_unique_sessions=%d lineage_count=%d winner_count=%d "
            "lineage_work=%d lineage_bound_hit=%s query_ms=%d execute_ms=%d",
            request_value,
            route,
            stats["candidate_count"],
            stats["candidate_unique_sessions"],
            stats["lineage_count"],
            stats["winner_count"],
            stats["lineage_work"],
            stats["lineage_bound_hit"],
            int((time.perf_counter() - started) * 1000),
            execute_ms,
        )
        return {"winners": winners, "stats": stats}
