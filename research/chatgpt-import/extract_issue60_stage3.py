#!/usr/bin/env python3
"""Issue #60 stage-3 read-only provenance shape explorer.

Goal:
- distinguish pre-2026-06-16 Hermes-shaped rows from the later ChatGPT import;
- use insertion-order proxies (messages.id), tool-call fingerprints, platform ID
  shapes, and session field distributions;
- recover cross-session-search edges that demonstrate live session-store use.

Safety:
- pins the authoritative frozen DB SHA-256;
- refuses symlinks and non-empty SQLite sidecars;
- opens mode=ro&immutable=1 with PRAGMA query_only=ON;
- performs no schema/temp/journal/FTS/VACUUM writes;
- verifies file identity + SHA-256 again after extraction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import stat
import tarfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

AUTHORITATIVE_PATH = Path(
    "/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db"
)
AUTHORITATIVE_SHA256 = "23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104"
EXPECTED_COUNTS = {"sessions": 7268, "messages": 231513, "gateway_routing": 78}
TAIPEI = ZoneInfo("Asia/Taipei")
CUTOFF = datetime(2026, 6, 16, 0, 0, 0, tzinfo=TAIPEI).timestamp()

SESSION_ID_RE = re.compile(r"\b(?:\d{8}_\d{6}_[A-Za-z0-9]+|cron_[A-Za-z0-9_]+)\b")
SNOWFLAKE_RE = re.compile(r"^\d{15,22}$")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


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
    out = {}
    for suffix in ("-wal", "-shm", "-journal"):
        p = Path(str(path) + suffix)
        out[suffix] = {"exists": p.exists(), "size": p.stat().st_size if p.exists() else 0}
    return out


def enforce_safe_source(path: Path, expected_sha: str) -> tuple[str, dict]:
    if path.is_symlink():
        raise SystemExit(f"REFUSE: database path is a symlink: {path}")
    if not path.is_file():
        raise SystemExit(f"REFUSE: database is not a regular file: {path}")
    sidecars = sidecar_receipt(path)
    dirty = {k: v for k, v in sidecars.items() if v["exists"] and int(v["size"]) > 0}
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
    uri = "file:" + quote(str(path.resolve()), safe="/:\\") + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
        conn.close()
        raise RuntimeError("failed to enable PRAGMA query_only")
    return conn


def normalize(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def taipei(epoch: object) -> str:
    if epoch is None or epoch == "":
        return ""
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(TAIPEI).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def write_tsv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        seen = set()
        for row in rows:
            for k in row:
                if k not in seen:
                    seen.add(k)
                    fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(
                {
                    k: normalize(v).replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
                    for k, v in row.items()
                }
            )


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})")}


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> object:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def percentile_int(values: list[int], q: float) -> int | None:
    if not values:
        return None
    xs = sorted(values)
    idx = round((len(xs) - 1) * q)
    return xs[idx]


def extract_tool_names(raw: object) -> list[str]:
    text = normalize(raw).strip()
    if not text:
        return []
    try:
        value = json.loads(text)
        if isinstance(value, str):
            value = json.loads(value)
    except Exception:
        return []
    if isinstance(value, dict) and "tool_calls" in value:
        value = value["tool_calls"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return []
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        if not isinstance(item, dict):
            continue
        fn = item.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            names.append(str(fn["name"]))
        elif item.get("name"):
            names.append(str(item["name"]))
    return names


def platform_shape(value: object) -> str:
    s = normalize(value).strip()
    if not s:
        return "empty"
    if SNOWFLAKE_RE.fullmatch(s):
        return "decimal_15_22"
    if UUID_RE.fullmatch(s):
        return "uuid"
    if s.startswith("call_"):
        return "call_id"
    return f"other_len_{len(s)}"


def sha_prefix(value: object) -> str:
    s = normalize(value)
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()[:12] if s else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    ap.add_argument("--expected-sha256", default=AUTHORITATIVE_SHA256)
    ap.add_argument("--output-dir", type=Path)
    args = ap.parse_args()

    started = time.time()
    db = args.db
    before_sha, sidecars = enforce_safe_source(db, args.expected_sha256)
    before_identity = file_identity(db)

    stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
    out = args.output_dir or Path(f"issue60-stage3-evidence-{stamp}")
    out.mkdir(parents=True, exist_ok=False)

    conn = open_immutable(db)
    try:
        counts = {
            table: int(scalar(conn, f"SELECT COUNT(*) FROM {table}") or 0)
            for table in EXPECTED_COUNTS
        }
        if counts != EXPECTED_COUNTS:
            raise SystemExit(f"REFUSE: canonical row counts changed: {counts}")

        quick_check = normalize(scalar(conn, "PRAGMA quick_check"))
        fk_violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())

        scols = table_columns(conn, "sessions")

        # 1) Pre-cutoff source/model/end_reason/lineage distribution.
        source_rows = []
        for r in conn.execute(
            """
            SELECT s.source,
                   COUNT(*) AS session_count,
                   MIN(s.started_at) AS min_started_at,
                   MAX(s.started_at) AS max_started_at,
                   SUM(CASE WHEN s.parent_session_id IS NOT NULL AND s.parent_session_id <> '' THEN 1 ELSE 0 END) AS with_parent,
                   SUM(CASE WHEN s.end_reason = 'compression' THEN 1 ELSE 0 END) AS compression_ended,
                   SUM(COALESCE(s.message_count, 0)) AS declared_message_count
            FROM sessions s
            WHERE s.started_at < ?
            GROUP BY s.source
            ORDER BY session_count DESC, s.source
            """,
            (CUTOFF,),
        ):
            d = dict(r)
            d["min_started_taipei"] = taipei(d["min_started_at"])
            d["max_started_taipei"] = taipei(d["max_started_at"])
            source_rows.append(d)
        write_tsv(out / "precutoff_source_profile.tsv", source_rows)

        model_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT source, COALESCE(model, '') AS model, COUNT(*) AS session_count
                FROM sessions
                WHERE started_at < ?
                GROUP BY source, COALESCE(model, '')
                ORDER BY session_count DESC, source, model
                """,
                (CUTOFF,),
            )
        ]
        write_tsv(out / "precutoff_model_profile.tsv", model_rows)

        end_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT source, COALESCE(end_reason, '') AS end_reason, COUNT(*) AS session_count
                FROM sessions
                WHERE started_at < ?
                GROUP BY source, COALESCE(end_reason, '')
                ORDER BY session_count DESC, source, end_reason
                """,
                (CUTOFF,),
            )
        ]
        write_tsv(out / "precutoff_end_reason_profile.tsv", end_rows)

        # 2) Message INTEGER PK as an insertion-order proxy.
        groups = [
            ("chatgpt_export_all", "s.source = 'chatgpt-export'", ()),
            ("precutoff_non_chatgpt", "s.started_at < ? AND s.source <> 'chatgpt-export'", (CUTOFF,)),
            ("precutoff_discord", "s.started_at < ? AND s.source = 'discord'", (CUTOFF,)),
            ("precutoff_cli", "s.started_at < ? AND s.source = 'cli'", (CUTOFF,)),
            ("precutoff_cron", "s.started_at < ? AND s.source = 'cron'", (CUTOFF,)),
        ]
        insertion_rows = []
        group_ids: dict[str, list[int]] = {}
        for name, clause, params in groups:
            ids = [
                int(r[0])
                for r in conn.execute(
                    f"""
                    SELECT m.id
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE {clause}
                    ORDER BY m.id
                    """,
                    params,
                )
            ]
            group_ids[name] = ids
            insertion_rows.append(
                {
                    "group": name,
                    "message_rows": len(ids),
                    "min_id": min(ids) if ids else "",
                    "p05_id": percentile_int(ids, 0.05) or "",
                    "p25_id": percentile_int(ids, 0.25) or "",
                    "median_id": percentile_int(ids, 0.50) or "",
                    "p75_id": percentile_int(ids, 0.75) or "",
                    "p95_id": percentile_int(ids, 0.95) or "",
                    "max_id": max(ids) if ids else "",
                }
            )
        chatgpt_min_id = min(group_ids["chatgpt_export_all"]) if group_ids["chatgpt_export_all"] else None
        if chatgpt_min_id is not None:
            pre_ids = group_ids["precutoff_non_chatgpt"]
            insertion_rows.append(
                {
                    "group": "precutoff_non_chatgpt_vs_first_chatgpt",
                    "message_rows": len(pre_ids),
                    "min_id": "",
                    "p05_id": "",
                    "p25_id": "",
                    "median_id": "",
                    "p75_id": "",
                    "p95_id": "",
                    "max_id": "",
                    "before_first_chatgpt_id": sum(i < chatgpt_min_id for i in pre_ids),
                    "at_or_after_first_chatgpt_id": sum(i >= chatgpt_min_id for i in pre_ids),
                    "first_chatgpt_message_id": chatgpt_min_id,
                }
            )
        write_tsv(out / "message_id_insertion_profile.tsv", insertion_rows)

        boundary_rows = []
        if chatgpt_min_id is not None:
            for r in conn.execute(
                """
                SELECT m.id AS message_id, m.session_id, s.source, s.title, s.started_at,
                       m.role, m.timestamp, m.tool_name, m.platform_message_id
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE m.id BETWEEN ? AND ?
                ORDER BY m.id
                """,
                (max(1, chatgpt_min_id - 25), chatgpt_min_id + 25),
            ):
                d = dict(r)
                d["session_started_taipei"] = taipei(d["started_at"])
                d["message_taipei"] = taipei(d["timestamp"])
                d["platform_id_shape"] = platform_shape(d["platform_message_id"])
                d["platform_id_sha12"] = sha_prefix(d["platform_message_id"])
                d.pop("platform_message_id", None)
                boundary_rows.append(d)
        write_tsv(out / "first_chatgpt_message_id_boundary.tsv", boundary_rows)

        # 3) Per-session shape/fingerprint table for pre-cutoff non-ChatGPT rows.
        selected_session_cols = [
            c
            for c in (
                "id", "source", "title", "model", "started_at", "ended_at", "end_reason",
                "parent_session_id", "model_config", "session_key", "chat_id", "chat_type",
                "thread_id", "origin_json", "display_name", "cwd"
            )
            if c in scols
        ]
        session_sql = ", ".join(f"s.{c}" for c in selected_session_cols)
        session_rows = []
        sessions = conn.execute(
            f"""
            SELECT {session_sql},
                   COUNT(m.id) AS actual_messages,
                   MIN(m.id) AS min_message_id,
                   MAX(m.id) AS max_message_id,
                   MIN(m.timestamp) AS min_message_timestamp,
                   MAX(m.timestamp) AS max_message_timestamp,
                   SUM(CASE WHEN m.tool_calls IS NOT NULL AND m.tool_calls <> '' THEN 1 ELSE 0 END) AS tool_call_rows,
                   SUM(CASE WHEN m.tool_name IS NOT NULL AND m.tool_name <> '' THEN 1 ELSE 0 END) AS named_tool_rows,
                   SUM(CASE WHEN m.platform_message_id IS NOT NULL AND m.platform_message_id <> '' THEN 1 ELSE 0 END) AS platform_id_rows
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.started_at < ? AND s.source <> 'chatgpt-export'
            GROUP BY s.id
            ORDER BY s.started_at, s.id
            """,
            (CUTOFF,),
        ).fetchall()
        for r in sessions:
            d = dict(r)
            d["started_taipei"] = taipei(d.get("started_at"))
            d["ended_taipei"] = taipei(d.get("ended_at"))
            d["min_message_taipei"] = taipei(d.get("min_message_timestamp"))
            d["max_message_taipei"] = taipei(d.get("max_message_timestamp"))
            for sensitive in ("chat_id", "thread_id", "session_key", "origin_json"):
                if sensitive in d:
                    raw = normalize(d[sensitive])
                    d[f"{sensitive}_present"] = int(bool(raw))
                    d[f"{sensitive}_sha12"] = sha_prefix(raw)
                    d.pop(sensitive, None)
            session_rows.append(d)
        write_tsv(out / "precutoff_session_fingerprints.tsv", session_rows)

        # 4) Tool-call fingerprint distribution and earliest occurrences.
        tool_counter: Counter[tuple[str, str]] = Counter()
        occurrences = []
        for r in conn.execute(
            """
            SELECT m.id AS message_id, m.session_id, s.source, s.title, s.started_at,
                   m.timestamp, m.role, m.tool_calls, m.tool_name
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE s.started_at < ?
              AND s.source <> 'chatgpt-export'
              AND (
                    (m.tool_calls IS NOT NULL AND m.tool_calls <> '')
                 OR (m.tool_name IS NOT NULL AND m.tool_name <> '')
              )
            ORDER BY m.id
            """,
            (CUTOFF,),
        ):
            d = dict(r)
            names = extract_tool_names(d["tool_calls"])
            if d["tool_name"]:
                names.append(normalize(d["tool_name"]))
            names = sorted(set(n for n in names if n))
            for name in names:
                tool_counter[(normalize(d["source"]), name)] += 1
                occurrences.append(
                    {
                        "message_id": d["message_id"],
                        "session_id": d["session_id"],
                        "source": d["source"],
                        "title": d["title"],
                        "session_started_taipei": taipei(d["started_at"]),
                        "message_taipei": taipei(d["timestamp"]),
                        "role": d["role"],
                        "tool_name": name,
                    }
                )
        tool_profile = [
            {"source": src, "tool_name": name, "occurrences": count}
            for (src, name), count in sorted(
                tool_counter.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1])
            )
        ]
        write_tsv(out / "precutoff_tool_name_profile.tsv", tool_profile)
        write_tsv(out / "precutoff_tool_occurrences.tsv", occurrences[:2000])

        # 5) Platform message ID *shape* only; never emit raw platform IDs.
        shape_counter: Counter[tuple[str, str, str]] = Counter()
        shape_examples = {}
        for r in conn.execute(
            """
            SELECT m.id AS message_id, m.session_id, s.source, s.title, s.started_at,
                   m.role, m.timestamp, m.platform_message_id
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE s.started_at < ?
              AND s.source <> 'chatgpt-export'
              AND m.platform_message_id IS NOT NULL
              AND m.platform_message_id <> ''
            ORDER BY m.id
            """,
            (CUTOFF,),
        ):
            d = dict(r)
            shape = platform_shape(d["platform_message_id"])
            key = (normalize(d["source"]), normalize(d["role"]), shape)
            shape_counter[key] += 1
            shape_examples.setdefault(
                key,
                {
                    "source": d["source"],
                    "role": d["role"],
                    "shape": shape,
                    "message_id": d["message_id"],
                    "session_id": d["session_id"],
                    "title": d["title"],
                    "session_started_taipei": taipei(d["started_at"]),
                    "message_taipei": taipei(d["timestamp"]),
                    "platform_id_sha12": sha_prefix(d["platform_message_id"]),
                    "platform_id_length": len(normalize(d["platform_message_id"])),
                },
            )
        shape_profile = [
            {"source": src, "role": role, "shape": shape, "count": count}
            for (src, role, shape), count in sorted(
                shape_counter.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ]
        write_tsv(out / "platform_message_id_shape_profile.tsv", shape_profile)
        write_tsv(out / "platform_message_id_shape_examples.tsv", list(shape_examples.values()))

        # 6) Cross-session search edges. This is deliberately narrow: tool rows
        # whose tool_name names session_search, plus JSON-ish outputs containing
        # "session_id". Emit session IDs but not message contents.
        known_sessions = {r[0] for r in conn.execute("SELECT id FROM sessions")}
        edges = []
        edge_seen = set()
        for r in conn.execute(
            """
            SELECT m.id AS message_id, m.session_id, s.source, s.title, s.started_at,
                   m.timestamp, m.tool_name, m.content
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE s.started_at < ?
              AND s.source <> 'chatgpt-export'
              AND (
                    LOWER(COALESCE(m.tool_name, '')) LIKE '%session%search%'
                 OR m.content LIKE '%"session_id"%'
              )
            ORDER BY m.id
            """,
            (CUTOFF,),
        ):
            d = dict(r)
            refs = sorted(set(SESSION_ID_RE.findall(normalize(d["content"]))))
            for ref in refs:
                if ref == d["session_id"] or ref not in known_sessions:
                    continue
                key = (d["message_id"], d["session_id"], ref)
                if key in edge_seen:
                    continue
                edge_seen.add(key)
                target = conn.execute(
                    "SELECT source, title, started_at FROM sessions WHERE id = ?",
                    (ref,),
                ).fetchone()
                edges.append(
                    {
                        "message_id": d["message_id"],
                        "from_session_id": d["session_id"],
                        "from_source": d["source"],
                        "from_title": d["title"],
                        "from_started_taipei": taipei(d["started_at"]),
                        "message_taipei": taipei(d["timestamp"]),
                        "tool_name": d["tool_name"],
                        "to_session_id": ref,
                        "to_source": target["source"] if target else "",
                        "to_title": target["title"] if target else "",
                        "to_started_taipei": taipei(target["started_at"]) if target else "",
                    }
                )
        write_tsv(out / "precutoff_session_search_edges.tsv", edges)

        # 7) Earliest high-signal rows: tool usage and lineage.
        write_tsv(out / "earliest_precutoff_tool_fingerprints.tsv", occurrences[:100])

        parent_rows = []
        for r in conn.execute(
            """
            SELECT s.id, s.source, s.title, s.started_at, s.parent_session_id, s.end_reason,
                   p.source AS parent_source, p.title AS parent_title, p.started_at AS parent_started_at
            FROM sessions s
            LEFT JOIN sessions p ON p.id = s.parent_session_id
            WHERE s.started_at < ?
              AND s.parent_session_id IS NOT NULL
              AND s.parent_session_id <> ''
            ORDER BY s.started_at, s.id
            """,
            (CUTOFF,),
        ):
            d = dict(r)
            d["started_taipei"] = taipei(d["started_at"])
            d["parent_started_taipei"] = taipei(d["parent_started_at"])
            parent_rows.append(d)
        write_tsv(out / "precutoff_parent_edges.tsv", parent_rows)

        first_chatgpt = None
        if chatgpt_min_id is not None:
            r = conn.execute(
                """
                SELECT m.id AS message_id, m.session_id, s.title, s.started_at,
                       m.timestamp, m.role, m.platform_message_id
                FROM messages m JOIN sessions s ON s.id=m.session_id
                WHERE m.id=?
                """,
                (chatgpt_min_id,),
            ).fetchone()
            if r:
                first_chatgpt = dict(r)
                first_chatgpt["session_started_taipei"] = taipei(first_chatgpt["started_at"])
                first_chatgpt["message_taipei"] = taipei(first_chatgpt["timestamp"])
                first_chatgpt["platform_id_shape"] = platform_shape(first_chatgpt["platform_message_id"])
                first_chatgpt["platform_id_sha12"] = sha_prefix(first_chatgpt["platform_message_id"])
                first_chatgpt.pop("platform_message_id", None)

        summary = {
            "cutoff_taipei": datetime.fromtimestamp(CUTOFF, tz=timezone.utc).astimezone(TAIPEI).isoformat(),
            "chatgpt_min_message_id": chatgpt_min_id,
            "precutoff_non_chatgpt_messages": len(group_ids["precutoff_non_chatgpt"]),
            "precutoff_non_chatgpt_before_first_chatgpt_message_id": (
                sum(i < chatgpt_min_id for i in group_ids["precutoff_non_chatgpt"])
                if chatgpt_min_id is not None else None
            ),
            "precutoff_non_chatgpt_at_or_after_first_chatgpt_message_id": (
                sum(i >= chatgpt_min_id for i in group_ids["precutoff_non_chatgpt"])
                if chatgpt_min_id is not None else None
            ),
            "distinct_precutoff_tool_names": len({name for _, name in tool_counter}),
            "precutoff_tool_occurrences": len(occurrences),
            "cross_session_edges": len(edges),
            "precutoff_parent_edges": len(parent_rows),
            "first_chatgpt_message": first_chatgpt,
        }

        (out / "README.md").write_text(
            "# Issue #60 stage-3 provenance-shape evidence\n\n"
            "Purpose: distinguish pre-2026-06-16 Hermes-shaped rows from the later "
            "ChatGPT import using insertion-order proxies and runtime fingerprints.\n\n"
            "Key outputs:\n\n"
            "- `message_id_insertion_profile.tsv`: SQLite message PK distributions; "
            "`messages.id` is used only as an insertion-order proxy, not a historical timestamp.\n"
            "- `first_chatgpt_message_id_boundary.tsv`: rows around the first final "
            "`chatgpt-export` message ID.\n"
            "- `precutoff_tool_name_profile.tsv` / `precutoff_tool_occurrences.tsv`: "
            "tool-call fingerprints before the cutoff.\n"
            "- `platform_message_id_shape_profile.tsv`: only shape categories; raw "
            "platform IDs are never emitted.\n"
            "- `precutoff_session_search_edges.tsv`: cross-session references emitted "
            "without message bodies.\n"
            "- `precutoff_parent_edges.tsv`: session lineage already present before cutoff.\n"
            "- `precutoff_session_fingerprints.tsv`: bounded field/message summaries.\n\n"
            "Interpretation discipline:\n\n"
            "- Low message IDs relative to the first `chatgpt-export` row are evidence "
            "about insertion order only.\n"
            "- Tool names / platform-ID shapes are fingerprints, not by themselves proof "
            "of which program produced a row.\n"
            "- Cross-session references and multiple independent fingerprints together "
            "can support a stronger provenance conclusion.\n",
            encoding="utf-8",
        )

        after_identity = file_identity(db)
        after_sha = sha256_file(db)
        if after_identity != before_identity:
            raise SystemExit(f"REFUSE: source identity changed during extraction: {before_identity} -> {after_identity}")
        if after_sha != before_sha:
            raise SystemExit(f"REFUSE: source SHA changed during extraction: {before_sha} -> {after_sha}")

        manifest = {
            "source": {
                "path": str(db),
                "sha256": before_sha,
                "before_identity": before_identity,
                "after_identity": after_identity,
                "sidecars": sidecars,
                "opened_mode": "mode=ro&immutable=1; PRAGMA query_only=ON",
                "mutations_performed": 0,
            },
            "counts": counts,
            "quick_check": quick_check,
            "foreign_key_violations": fk_violations,
            "cutoff_epoch": CUTOFF,
            "cutoff_taipei": datetime.fromtimestamp(CUTOFF, tz=timezone.utc).astimezone(TAIPEI).isoformat(),
            "summary": summary,
            "elapsed_s": round(time.time() - started, 3),
        }
        (out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        conn.close()

    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname=out.name)

    print(f"evidence_dir={out}")
    print(f"archive={archive}")
    print(f"sha256={before_sha}")
    print(f"counts={json.dumps(counts, sort_keys=True)}")
    print(f"chatgpt_min_message_id={summary['chatgpt_min_message_id']}")
    print(
        "precutoff_non_chatgpt_before_first_chatgpt="
        f"{summary['precutoff_non_chatgpt_before_first_chatgpt_message_id']}"
    )
    print(
        "precutoff_non_chatgpt_at_or_after_first_chatgpt="
        f"{summary['precutoff_non_chatgpt_at_or_after_first_chatgpt_message_id']}"
    )
    print(f"distinct_precutoff_tool_names={summary['distinct_precutoff_tool_names']}")
    print(f"cross_session_edges={summary['cross_session_edges']}")
    print(f"precutoff_parent_edges={summary['precutoff_parent_edges']}")
    print(f"elapsed_s={time.time() - started:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
