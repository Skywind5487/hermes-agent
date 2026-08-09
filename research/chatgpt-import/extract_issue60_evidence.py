#!/usr/bin/env python3
"""Issue #60 read-only evidence extractor for ChatGPT -> Hermes import archaeology.

Safety:
- verifies the authoritative SHA-256;
- refuses symlinks and non-empty SQLite sidecars;
- opens SQLite with mode=ro&immutable=1 and PRAGMA query_only=ON;
- performs no TEMP/schema/journal/FTS/VACUUM writes;
- verifies database identity and SHA-256 again after extraction.

The output directory contains only derived evidence files and may be zipped/uploaded.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import stat
import textwrap
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

AUTHORITATIVE_PATH = Path(
    "/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db"
)
AUTHORITATIVE_SHA256 = "23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104"
EXPECTED_COUNTS = {"sessions": 7268, "messages": 231513, "gateway_routing": 78}

IMPORT_DATE_PREFIXES = tuple(f"202606{day:02d}_%" for day in range(14, 21))
PRIMARY_DATE_PREFIX = "20260616_"
ANOMALY_ROOT = "20260530_113929_d6a58f32"

KEYWORDS = {
    # Very specific importer/export structure.
    "current_node": 12,
    "chatgpt-export": 12,
    "conversation_asset": 12,
    "export_manifest": 12,
    "conversations-": 10,
    "zipfile": 9,
    "downloads": 7,
    "conversation_id": 7,
    "default_model_slug": 7,
    "content.parts": 7,
    "author.role": 7,
    "content_references": 7,
    "mapping": 7,
    # Merge/database implementation clues.
    "state.db": 8,
    "session.db": 8,
    "sqlite": 5,
    "merge": 5,
    "import": 4,
    "script": 3,
    "parent": 2,
}

SESSION_FIELDS = (
    "id",
    "title",
    "display_name",
    "started_at",
    "ended_at",
    "source",
    "model",
    "parent_session_id",
    "end_reason",
    "model_config",
    "cwd",
    "created_at",
    "updated_at",
)

MESSAGE_FIELDS = (
    "id",
    "session_id",
    "role",
    "content",
    "timestamp",
    "model",
    "tool_name",
    "tool_call_id",
    "metadata",
    "created_at",
    "updated_at",
)


def sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, int]:
    st = path.stat()
    return {
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "inode": st.st_ino,
        "device": st.st_dev,
        "mode": stat.S_IMODE(st.st_mode),
    }


def sidecar_receipt(path: Path) -> dict[str, dict[str, int | bool]]:
    result: dict[str, dict[str, int | bool]] = {}
    for suffix in ("-wal", "-shm", "-journal"):
        side = Path(str(path) + suffix)
        result[suffix] = {
            "exists": side.exists(),
            "size": side.stat().st_size if side.exists() else 0,
        }
    return result


def enforce_safe_source(path: Path, expected_sha: str) -> tuple[str, dict]:
    if path.is_symlink():
        raise SystemExit(f"REFUSE: database path is a symlink: {path}")
    if not path.is_file():
        raise SystemExit(f"REFUSE: database is not a regular file: {path}")
    sidecars = sidecar_receipt(path)
    dirty = {
        name: meta
        for name, meta in sidecars.items()
        if meta["exists"] and int(meta["size"]) > 0
    }
    if dirty:
        raise SystemExit(f"REFUSE: frozen source has non-empty SQLite sidecars: {dirty}")
    actual = sha256_file(path)
    if actual.lower() != expected_sha.lower():
        raise SystemExit(
            "REFUSE: SHA-256 mismatch\n"
            f" expected={expected_sha}\n actual={actual}\n path={path}"
        )
    return actual, sidecars


def open_immutable(path: Path) -> sqlite3.Connection:
    uri = (
        "file:"
        + quote(str(path.resolve()), safe="/:\\")
        + "?mode=ro&immutable=1"
    )
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
        conn.close()
        raise RuntimeError("failed to enable PRAGMA query_only")
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")]


def select_existing(columns: set[str], wanted: tuple[str, ...]) -> list[str]:
    return [name for name in wanted if name in columns]


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def clipped(text: str, limit: int = 700) -> str:
    text = text.replace("\x00", "\\0")
    if len(text) <= limit:
        return text
    return text[:limit] + f"… <{len(text) - limit} chars omitted>"


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:180] or "unnamed"


def write_tsv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            clean = {
                key: normalize_text(value).replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
                for key, value in row.items()
            }
            writer.writerow(clean)


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> object:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def session_date_scope_clause(column: str = "s.id") -> tuple[str, tuple[str, ...]]:
    terms = [f"{column} LIKE ?" for _ in IMPORT_DATE_PREFIXES]
    return "(" + " OR ".join(terms) + ")", IMPORT_DATE_PREFIXES


def export_schema(conn: sqlite3.Connection, out: Path) -> dict[str, list[str]]:
    schemas: dict[str, list[str]] = {}
    for table in ("sessions", "messages"):
        rows = [dict(row) for row in conn.execute(f"PRAGMA table_info({table})")]
        write_tsv(out / f"schema_{table}.tsv", rows)
        schemas[table] = [str(row["name"]) for row in rows]
    return schemas


def canonical_receipt(conn: sqlite3.Connection) -> dict:
    tables = {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        )
    }
    counts = {
        table: int(scalar(conn, f"SELECT COUNT(*) FROM {table}"))
        if table in tables
        else None
        for table in EXPECTED_COUNTS
    }
    return {
        "counts": counts,
        "expected_counts": EXPECTED_COUNTS,
        "counts_match": counts == EXPECTED_COUNTS,
        "quick_check": [row[0] for row in conn.execute("PRAGMA quick_check")],
        "foreign_key_violations": sum(
            1 for _ in conn.execute("PRAGMA foreign_key_check")
        ),
        "sqlite_version": sqlite3.sqlite_version,
        "python": os.sys.version,
        "page_size": int(scalar(conn, "PRAGMA page_size")),
        "page_count": int(scalar(conn, "PRAGMA page_count")),
        "freelist_count": int(scalar(conn, "PRAGMA freelist_count")),
    }


def export_import_session_inventory(
    conn: sqlite3.Connection, out: Path, session_columns: list[str]
) -> list[dict]:
    fields = select_existing(set(session_columns), SESSION_FIELDS)
    if "id" not in fields:
        raise RuntimeError("sessions.id is required")
    if "source" not in fields:
        raise RuntimeError("sessions.source is required for #60")
    sql = (
        "SELECT "
        + ", ".join(fields)
        + " FROM sessions "
        "WHERE source='chatgpt-export' AND id LIKE ? ORDER BY id"
    )
    rows = [dict(row) for row in conn.execute(sql, (PRIMARY_DATE_PREFIX + "%",))]
    write_tsv(out / "20260616_chatgpt_export_sessions.tsv", rows, fields)
    return rows


def export_chatgpt_field_profile(
    conn: sqlite3.Connection, out: Path, session_columns: list[str]
) -> list[dict]:
    columns = set(session_columns)
    total = int(
        scalar(conn, "SELECT COUNT(*) FROM sessions WHERE source='chatgpt-export'")
    )
    rows: list[dict] = []
    for field in SESSION_FIELDS:
        if field not in columns:
            continue
        nonnull = int(
            scalar(
                conn,
                f"SELECT COUNT(*) FROM sessions "
                f"WHERE source='chatgpt-export' AND {field} IS NOT NULL",
            )
        )
        nonempty = int(
            scalar(
                conn,
                f"SELECT COUNT(*) FROM sessions "
                f"WHERE source='chatgpt-export' "
                f"AND NULLIF(TRIM(CAST({field} AS TEXT)), '') IS NOT NULL",
            )
        )
        distinct = int(
            scalar(
                conn,
                f"SELECT COUNT(DISTINCT {field}) FROM sessions "
                "WHERE source='chatgpt-export'",
            )
        )
        samples = [
            normalize_text(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT {field} FROM sessions "
                f"WHERE source='chatgpt-export' AND {field} IS NOT NULL "
                f"LIMIT 5"
            )
        ]
        rows.append(
            {
                "field": field,
                "total_sessions": total,
                "nonnull": nonnull,
                "nonempty_text": nonempty,
                "distinct_values": distinct,
                "sample_values": json.dumps(samples, ensure_ascii=False),
            }
        )
    write_tsv(out / "chatgpt_export_session_field_profile.tsv", rows)
    return rows


def export_source_profile(conn: sqlite3.Connection, out: Path) -> list[dict]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              source,
              COUNT(*) AS session_count,
              SUM(CASE WHEN parent_session_id IS NOT NULL THEN 1 ELSE 0 END)
                  AS with_parent,
              SUM(CASE WHEN end_reason='compression' THEN 1 ELSE 0 END)
                  AS compression_ended,
              MIN(id) AS min_id,
              MAX(id) AS max_id
            FROM sessions
            GROUP BY source
            ORDER BY session_count DESC, source
            """
        )
    ]
    write_tsv(out / "session_source_profile.tsv", rows)
    return rows


def discover_keyword_hits(
    conn: sqlite3.Connection,
    out: Path,
    session_columns: list[str],
    message_columns: list[str],
) -> tuple[list[dict], list[str]]:
    if "content" not in message_columns or "session_id" not in message_columns:
        raise RuntimeError("messages.content and messages.session_id are required")
    sfields = select_existing(
        set(session_columns),
        ("id", "title", "started_at", "source", "model", "display_name"),
    )
    message_order = (
        "m.timestamp, m.id"
        if "timestamp" in message_columns and "id" in message_columns
        else "m.id"
        if "id" in message_columns
        else "m.rowid"
    )
    scope_sql, scope_params = session_date_scope_clause("s.id")
    sql = f"""
        SELECT {", ".join("s." + field for field in sfields)},
               m.id AS message_id,
               {("m.role" if "role" in message_columns else "NULL")} AS role,
               {("m.timestamp" if "timestamp" in message_columns else "NULL")} AS message_timestamp,
               m.content AS content
        FROM sessions AS s
        JOIN messages AS m ON m.session_id=s.id
        WHERE s.source='chatgpt-export'
          AND {scope_sql}
        ORDER BY s.id, {message_order}
    """
    hit_rows: list[dict] = []
    per_session_terms: dict[str, set[str]] = defaultdict(set)
    per_session_score: Counter[str] = Counter()
    per_session_hits: Counter[str] = Counter()
    per_session_meta: dict[str, dict] = {}

    for row in conn.execute(sql, scope_params):
        d = dict(row)
        sid = normalize_text(d.get("id"))
        per_session_meta.setdefault(
            sid,
            {key: d.get(key) for key in sfields if key != "id"} | {"id": sid},
        )
        content = normalize_text(d.get("content"))
        lowered = content.lower()
        matched = [term for term in KEYWORDS if term.lower() in lowered]
        if not matched:
            continue
        for term in matched:
            if term not in per_session_terms[sid]:
                per_session_score[sid] += KEYWORDS[term]
                per_session_terms[sid].add(term)
        per_session_hits[sid] += 1
        hit_rows.append(
            {
                "session_id": sid,
                "title": d.get("title"),
                "started_at": d.get("started_at"),
                "source": d.get("source"),
                "message_id": d.get("message_id"),
                "role": d.get("role"),
                "message_timestamp": d.get("message_timestamp"),
                "matched_terms": ",".join(matched),
                "content_excerpt": clipped(content, 1000),
            }
        )

    write_tsv(out / "import_keyword_hits.tsv", hit_rows)

    ranked: list[dict] = []
    for sid in sorted(
        per_session_meta,
        key=lambda value: (
            -per_session_score[value],
            -len(per_session_terms[value]),
            -per_session_hits[value],
            value,
        ),
    ):
        meta = per_session_meta[sid]
        ranked.append(
            {
                "session_id": sid,
                "title": meta.get("title"),
                "started_at": meta.get("started_at"),
                "source": meta.get("source"),
                "score": per_session_score[sid],
                "distinct_terms": len(per_session_terms[sid]),
                "hit_messages": per_session_hits[sid],
                "terms": ",".join(sorted(per_session_terms[sid])),
            }
        )
    write_tsv(out / "import_candidate_sessions.tsv", ranked)

    # Dump high-confidence candidates, then fill up to 12 strongest overall.
    # A score >= 12 means at least one very specific importer/export marker
    # (or several independent weaker clues) was present.
    ordered = sorted(
        ranked,
        key=lambda row: (
            -int(str(row["session_id"]).startswith(PRIMARY_DATE_PREFIX)),
            -int(row["score"]),
            -int(row["distinct_terms"]),
            str(row["session_id"]),
        ),
    )
    candidate_ids = [
        str(row["session_id"]) for row in ordered if int(row["score"]) >= 12
    ][:20]
    if len(candidate_ids) < 12:
        for row in ordered:
            sid = str(row["session_id"])
            if sid not in candidate_ids:
                candidate_ids.append(sid)
            if len(candidate_ids) >= 12:
                break
    return ranked, candidate_ids


def dump_session_transcript(
    conn: sqlite3.Connection,
    out_dir: Path,
    session_id: str,
    session_columns: list[str],
    message_columns: list[str],
) -> Path:
    sfields = select_existing(set(session_columns), SESSION_FIELDS)
    mfields = select_existing(set(message_columns), MESSAGE_FIELDS)
    session = conn.execute(
        "SELECT "
        + ", ".join(sfields)
        + " FROM sessions WHERE id=? LIMIT 1",
        (session_id,),
    ).fetchone()
    if session is None:
        raise RuntimeError(f"session not found: {session_id}")

    if "timestamp" in mfields and "id" in mfields:
        order = "timestamp, id"
    elif "id" in mfields:
        order = "id"
    else:
        order = "rowid"

    messages = [
        dict(row)
        for row in conn.execute(
            "SELECT "
            + ", ".join(mfields)
            + f" FROM messages WHERE session_id=? ORDER BY {order}",
            (session_id,),
        )
    ]

    path = out_dir / (safe_filename(session_id) + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Session {session_id}",
        "",
        "## Session row",
        "",
        "```json",
        json.dumps(dict(session), indent=2, ensure_ascii=False, default=str),
        "```",
        "",
        f"## Messages ({len(messages)})",
        "",
    ]
    for index, msg in enumerate(messages, 1):
        role = normalize_text(msg.get("role") or "unknown")
        msg_id = normalize_text(msg.get("id"))
        ts = normalize_text(msg.get("timestamp"))
        lines += [
            f"### {index}. {role} — id={msg_id} — timestamp={ts}",
            "",
        ]
        metadata = {
            key: value
            for key, value in msg.items()
            if key not in {"content", "role", "id", "session_id", "timestamp"}
            and value is not None
        }
        if metadata:
            lines += [
                "```json",
                json.dumps(metadata, indent=2, ensure_ascii=False, default=str),
                "```",
                "",
            ]
        lines += [normalize_text(msg.get("content")), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def anomaly_descendants(
    conn: sqlite3.Connection,
    out: Path,
    session_columns: list[str],
    message_columns: list[str],
) -> list[str]:
    if "parent_session_id" not in session_columns:
        return []
    sfields = select_existing(set(session_columns), SESSION_FIELDS)
    rows = [dict(row) for row in conn.execute(
        "SELECT " + ", ".join(sfields) + " FROM sessions"
    )]
    by_id = {normalize_text(row["id"]): row for row in rows}
    children: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        parent = normalize_text(row.get("parent_session_id"))
        if parent:
            children[parent].append(normalize_text(row["id"]))
    for values in children.values():
        values.sort()

    found: list[str] = []
    queue = [ANOMALY_ROOT]
    seen: set[str] = set()
    while queue:
        sid = queue.pop(0)
        if sid in seen:
            continue
        seen.add(sid)
        if sid in by_id:
            found.append(sid)
        queue.extend(children.get(sid, []))

    profile_rows: list[dict] = []
    for sid in found:
        row = dict(by_id[sid])
        msg_count = int(
            scalar(conn, "SELECT COUNT(*) FROM messages WHERE session_id=?", (sid,))
        )
        row["message_count"] = msg_count
        if "timestamp" in message_columns:
            row["message_min_timestamp"] = scalar(
                conn, "SELECT MIN(timestamp) FROM messages WHERE session_id=?", (sid,)
            )
            row["message_max_timestamp"] = scalar(
                conn, "SELECT MAX(timestamp) FROM messages WHERE session_id=?", (sid,)
            )
        profile_rows.append(row)
    columns = sfields + [
        key
        for key in ("message_count", "message_min_timestamp", "message_max_timestamp")
        if profile_rows and key in profile_rows[0]
    ]
    write_tsv(out / "prehermes_anomaly_lineage.tsv", profile_rows, columns)

    # Small content samples only: first/last 2 messages per session.
    sample_path = out / "prehermes_anomaly_message_samples.md"
    sample_lines = [
        f"# Pre-Hermes anomaly lineage samples",
        "",
        f"Root: `{ANOMALY_ROOT}`",
        "",
        "These are first/last message samples, not a full transcript dump.",
        "",
    ]
    mfields = select_existing(
        set(message_columns), ("id", "role", "content", "timestamp", "session_id")
    )
    if "timestamp" in mfields and "id" in mfields:
        asc = "timestamp, id"
        desc = "timestamp DESC, id DESC"
    elif "id" in mfields:
        asc = "id"
        desc = "id DESC"
    else:
        asc = "rowid"
        desc = "rowid DESC"
    for sid in found:
        first = [
            dict(row)
            for row in conn.execute(
                "SELECT "
                + ", ".join(mfields)
                + f" FROM messages WHERE session_id=? ORDER BY {asc} LIMIT 2",
                (sid,),
            )
        ]
        last = [
            dict(row)
            for row in conn.execute(
                "SELECT "
                + ", ".join(mfields)
                + f" FROM messages WHERE session_id=? ORDER BY {desc} LIMIT 2",
                (sid,),
            )
        ]
        sample_lines += [f"## {sid}", ""]
        for label, batch in (("first", first), ("last", list(reversed(last)))):
            sample_lines += [f"### {label}", ""]
            for msg in batch:
                sample_lines += [
                    f"- id={normalize_text(msg.get('id'))} role={normalize_text(msg.get('role'))} timestamp={normalize_text(msg.get('timestamp'))}",
                    "",
                    clipped(normalize_text(msg.get("content")), 1200),
                    "",
                ]
    sample_path.write_text("\n".join(sample_lines), encoding="utf-8")
    return found


def export_prehermes_nonchatgpt(
    conn: sqlite3.Connection,
    out: Path,
    session_columns: list[str],
) -> list[dict]:
    fields = select_existing(set(session_columns), SESSION_FIELDS)
    if "id" not in fields or "source" not in fields:
        return []
    sql = (
        "SELECT "
        + ", ".join(fields)
        + " FROM sessions "
        "WHERE id < '20260616_' AND COALESCE(source,'') <> 'chatgpt-export' "
        "ORDER BY id"
    )
    rows = [dict(row) for row in conn.execute(sql)]
    write_tsv(out / "prehermes_non_chatgpt_export_sessions.tsv", rows, fields)
    return rows


def write_manifest(
    out: Path,
    *,
    db_path: Path,
    sha: str,
    sidecars: dict,
    before_identity: dict,
    after_identity: dict,
    receipt: dict,
    candidate_ids: list[str],
    anomaly_ids: list[str],
    elapsed_s: float,
) -> None:
    manifest = {
        "issue": 60,
        "purpose": "ChatGPT -> Hermes import/merge provenance evidence extraction",
        "source": {
            "path": str(db_path),
            "sha256": sha,
            "sidecars": sidecars,
            "identity_before": before_identity,
            "identity_after": after_identity,
            "opened_mode": "mode=ro&immutable=1 + PRAGMA query_only=ON",
            "mutations_performed": False,
        },
        "canonical_receipt": receipt,
        "search_window": {
            "session_id_prefixes": list(IMPORT_DATE_PREFIXES),
            "primary_prefix": PRIMARY_DATE_PREFIX,
            "keywords": KEYWORDS,
        },
        "candidate_transcript_session_ids": candidate_ids,
        "prehermes_anomaly_root": ANOMALY_ROOT,
        "prehermes_anomaly_session_ids": anomaly_ids,
        "elapsed_s": elapsed_s,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_readme(out: Path) -> None:
    text = f"""\
# Hermes issue #60 evidence bundle

This directory was produced by `extract_issue60_evidence.py`.

Safety contract:

- canonical DB expected SHA-256: `{AUTHORITATIVE_SHA256}`
- SQLite open mode: `mode=ro&immutable=1`
- `PRAGMA query_only=ON`
- non-empty `-wal`, `-shm`, or `-journal` sidecars cause a hard refusal
- the DB identity and SHA-256 are checked again after extraction
- no TEMP/schema/FTS/VACUUM/journal writes are performed

High-value files:

- `20260616_chatgpt_export_sessions.tsv` — first target inventory from #60
- `import_keyword_hits.tsv` — exact message locators around importer/export terms
- `import_candidate_sessions.tsv` — ranked candidate historical discussions
- `transcripts/*.md` — full transcripts for the strongest candidates
- `chatgpt_export_session_field_profile.tsv` — NULL/default/distinct profile of importer-facing session fields
- `prehermes_anomaly_lineage.tsv` — the May-30 depth-14 lineage metadata + message ranges
- `prehermes_anomaly_message_samples.md` — bounded first/last message samples for that anomaly
- `prehermes_non_chatgpt_export_sessions.tsv` — locator set for pre-adoption rows whose final source is not `chatgpt-export`
- `manifest.json` — safety/runtime/count/hash receipt

The files are evidence only. They intentionally do not label any field mapping
PROVEN/INFERRED/OPEN; that judgment happens after transcript + DB comparison.
"""
    (out / "README.md").write_text(textwrap.dedent(text), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    parser.add_argument("--expected-sha", default=AUTHORITATIVE_SHA256)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Evidence directory. Default: ./issue60-evidence-<UTC timestamp>",
    )
    args = parser.parse_args()

    started = time.perf_counter()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    out = args.out or Path(f"issue60-evidence-{stamp}")
    out.mkdir(parents=True, exist_ok=False)

    db_path = args.db
    before_identity = file_identity(db_path)
    actual_sha, sidecars = enforce_safe_source(db_path, args.expected_sha)

    conn = open_immutable(db_path)
    try:
        schemas = export_schema(conn, out)
        receipt = canonical_receipt(conn)
        if db_path.resolve() == AUTHORITATIVE_PATH.resolve():
            if receipt["counts"] != EXPECTED_COUNTS:
                raise RuntimeError(
                    "authoritative row-count mismatch: "
                    f"expected={EXPECTED_COUNTS} actual={receipt['counts']}"
                )
        export_import_session_inventory(conn, out, schemas["sessions"])
        export_chatgpt_field_profile(conn, out, schemas["sessions"])
        export_source_profile(conn, out)
        export_prehermes_nonchatgpt(conn, out, schemas["sessions"])
        _ranked, candidate_ids = discover_keyword_hits(
            conn, out, schemas["sessions"], schemas["messages"]
        )
        transcript_dir = out / "transcripts"
        for sid in candidate_ids:
            dump_session_transcript(
                conn,
                transcript_dir,
                sid,
                schemas["sessions"],
                schemas["messages"],
            )
        anomaly_ids = anomaly_descendants(
            conn, out, schemas["sessions"], schemas["messages"]
        )
    finally:
        conn.close()

    after_identity = file_identity(db_path)
    after_sha = sha256_file(db_path)
    if before_identity != after_identity or actual_sha != after_sha:
        raise RuntimeError(
            "SAFETY FAILURE: frozen DB identity/hash changed during read-only extraction"
        )

    elapsed_s = time.perf_counter() - started
    write_manifest(
        out,
        db_path=db_path,
        sha=actual_sha,
        sidecars=sidecars,
        before_identity=before_identity,
        after_identity=after_identity,
        receipt=receipt,
        candidate_ids=candidate_ids,
        anomaly_ids=anomaly_ids,
        elapsed_s=elapsed_s,
    )
    write_readme(out)

    archive = out.with_suffix(".tar.gz")
    import tarfile

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)

    print(f"evidence_dir={out}")
    print(f"archive={archive}")
    print(f"sha256={actual_sha}")
    print(f"counts={json.dumps(receipt['counts'], ensure_ascii=False)}")
    print(f"candidate_transcripts={len(candidate_ids)}")
    print(f"anomaly_sessions={len(anomaly_ids)}")
    print(f"elapsed_s={elapsed_s:.3f}")


if __name__ == "__main__":
    main()
