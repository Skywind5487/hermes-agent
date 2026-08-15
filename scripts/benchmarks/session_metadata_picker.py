#!/usr/bin/env python3
"""Disposable benchmark for the #14 routed session-metadata candidate path.

Builds a throwaway corpus in a temp directory (never touches the operator's
live state DB), then measures the routed
``SessionDB.list_sessions_rich(search_query=..., order_by_last_active=True)``
path against a legacy broad-LIKE reference and reports warm p50/p95, the
routed lane (path/status), the candidate/final row counts, and how often the
canonical LIKE fallback ran.

Query classes mirror the #14 routing table: token, interior infix, compact-id,
display-name, CJK 1-char (direct LIKE), CJK 2+ (CJK+Unicode union), mixed
CJK/Latin, wildcard literal, zero-hit, and high-cardinality.

Usage:
    python scripts/benchmarks/session_metadata_picker.py \
        --sizes 1000 10000 100000 --iterations 30 --output /tmp/smp.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from hermes_state import SessionDB
from hermes_state_common import MAX_FTS5_QUERY_CHARS, _compression_edge_sql

#: (label, query) — each class exercises a distinct route.
QUERY_CLASSES: dict[str, str] = {
    "token": "Alpha Project",
    "infix": "stige Bar",
    "compact-id": "an94",
    "display": "an94ops",
    "cjk1": "中",
    "cjk2": "中文",
    "mixed": "中文 alpha",
    "wildcard": "%",
    "zero": "zzzzzzzz",
    "high-cardinality": "needle",
}


def _build_corpus(db: SessionDB, n: int) -> None:
    """Bulk-insert ``n`` sessions (a few matching every query class) plus a
    handful of compression lineages. FTS triggers index each row as it lands."""
    t0 = time.time()
    rows = []
    for i in range(n):
        if i % 5 == 0:
            title = "Needle Session %d" % i
        elif i % 5 == 1:
            title = "Alpha Project Beta %d" % i
        elif i % 5 == 2:
            title = "AN-94 Prestige Barrel %d" % i
        elif i % 5 == 3:
            title = "中文測試%d" % i
        else:
            title = "Ordinary Session %d" % i
        rows.append((f"s{i}", t0 + i, title))
    db._conn.executemany(
        "INSERT INTO sessions (id, source, started_at, title) VALUES (?, 'cli', ?, ?)",
        rows,
    )
    db._conn.execute(
        "UPDATE sessions SET display_name = 'Acme / #an-94-ops' "
        "WHERE id = 's0'"
    )
    db._conn.commit()

    # A couple of compression chains so reverse-closure/tip projection is real.
    root = "s0"
    db.end_session(root, "compression")
    db.create_session("lineage_tip", "cli", parent_session_id=root)
    db.set_session_title("lineage_tip", "Needle Lineage Tip")
    db._conn.commit()


def _warm_p50_p95(samples: list[float]) -> dict:
    if not samples:
        return {"count": 0}
    return {
        "count": len(samples),
        "p50_ms": round(statistics.median(samples) * 1000, 3),
        "p95_ms": round(
            sorted(samples)[int(len(samples) * 0.95) - 1] * 1000, 3
        ),
    }


def _legacy_broad_like_ms(db: SessionDB, needle: str, iterations: int) -> list[float]:
    """Reference for the OLD listing path: a whole-store recursive chain CTE
    seeded from EVERY eligible row, then a leading-wildcard LIKE filter on the
    outer select (the pre-#14 seam paid this broad scan + lineage before any
    candidate narrowing)."""
    escaped = (
        needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    pattern = f"%{escaped}%"
    sql = """
        WITH RECURSIVE chain(root_id, cur_id) AS (
            SELECT s.id, s.id FROM sessions s
            UNION ALL
            SELECT c.root_id, child.id
            FROM chain c
            JOIN sessions parent ON parent.id = c.cur_id
            JOIN sessions child ON child.parent_session_id = c.cur_id
            WHERE {edge}
        ),
        chain_max AS (
            SELECT root_id, MAX(COALESCE(sm.started_at, 0)) AS eff
            FROM chain
            JOIN sessions sm ON sm.id = chain.cur_id
            GROUP BY root_id
        )
        SELECT s.id FROM sessions s
        LEFT JOIN chain_max cm ON cm.root_id = s.id
        WHERE LOWER(COALESCE(s.title, '')) LIKE ? ESCAPE '\\'
           OR LOWER(COALESCE(s.id, '')) LIKE ? ESCAPE '\\'
           OR LOWER(COALESCE(s.display_name, '')) LIKE ? ESCAPE '\\'
        ORDER BY COALESCE(cm.eff, s.started_at) DESC
        LIMIT 10
    """.format(edge=_compression_edge_sql("parent", "child"))
    params = (pattern, pattern, pattern)
    # warm the page cache once
    with db._read_ctx() as conn:
        conn.execute(sql, params).fetchall()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        with db._read_ctx() as conn:
            conn.execute(sql, params).fetchall()
        samples.append(time.perf_counter() - start)
    return samples


def _benchmark_size(db: SessionDB, size: int, iterations: int) -> dict:
    results: dict[str, dict] = {}

    def _counted_fallback(self, needle, *, conn=None):
        _counted_fallback.calls += 1  # type: ignore[attr-defined]
        return _orig_fallback(self, needle, conn=conn)

    _orig_fallback = SessionDB._metadata_like_fallback_row_ids
    SessionDB._metadata_like_fallback_row_ids = _counted_fallback  # type: ignore[method-assign]
    try:
        for label, query in QUERY_CLASSES.items():
            _counted_fallback.calls = 0  # type: ignore[attr-defined]
            # COLD-OPEN: the first run after corpus build is recorded separately
            # and never mixed into the warm percentiles (spec: record cold-open
            # samples separately).
            start = time.perf_counter()
            db.list_sessions_rich(
                search_query=query, order_by_last_active=True, limit=10
            )
            cold_ms = round((time.perf_counter() - start) * 1000, 3)
            _counted_fallback.calls = 0  # type: ignore[attr-defined]
            # Warm the routed path once so both lanes share a warm page cache.
            db.list_sessions_rich(
                search_query=query, order_by_last_active=True, limit=10
            )
            routed_samples: list[float] = []
            final_counts: list[int] = []
            for _ in range(iterations):
                start = time.perf_counter()
                rows = db.list_sessions_rich(
                    search_query=query, order_by_last_active=True, limit=10
                )
                routed_samples.append(time.perf_counter() - start)
                final_counts.append(len(rows))
            fallback_calls = _counted_fallback.calls
            route = db._metadata_candidate_row_ids(query)
            # Self-check (spec's correctness gate): FTS-hit lanes never run
            # LIKE (0 calls); direct-LIKE lanes run exactly warmup+iterations
            # (one per executed search). Known-hit classes must return rows.
            expected_fallback = (
                0
                if route.path in ("unicode", "cjk+unicode", "trigram")
                else iterations + 1
            )
            check = "ok"
            if fallback_calls != expected_fallback:
                check = (
                    f"fallback={fallback_calls} expected={expected_fallback}"
                )
            if (
                route.path in ("unicode", "cjk+unicode", "trigram")
                and not final_counts[-1]
            ):
                check += " no-rows"
            results[label] = {
                "query": query[:MAX_FTS5_QUERY_CHARS],
                "route": route.path,
                "status": route.status,
                "candidates": len(route.row_ids),
                "final_rows": final_counts[-1] if final_counts else 0,
                "fallback_calls": fallback_calls,
                "check": check,
                "cold_ms": cold_ms,
                "routed": _warm_p50_p95(routed_samples),
                "legacy_broad_like": _warm_p50_p95(
                    _legacy_broad_like_ms(db, query, iterations)
                ),
            }
    finally:
        SessionDB._metadata_like_fallback_row_ids = _orig_fallback
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000])
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report: dict = {
        "iterations": args.iterations,
        "query_classes": list(QUERY_CLASSES),
        "sizes": {},
    }
    for size in args.sizes:
        with tempfile.TemporaryDirectory(prefix="smp-") as tmp:
            db = SessionDB(db_path=Path(tmp) / "state.db")
            try:
                _build_corpus(db, size)
                report["sizes"][str(size)] = _benchmark_size(db, size, args.iterations)
            finally:
                db.close()

    output = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(output)
        print(f"wrote {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
