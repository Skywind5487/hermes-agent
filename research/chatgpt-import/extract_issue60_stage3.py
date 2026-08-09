#!/usr/bin/env python3
"""Issue #60 stage-3 read-only merge-boundary explorer.

Corrected ground truth:
- Hermes runtime was already active from 2026-05-29.
- 2026-06-16 is the ChatGPT export import/merge date, NOT Hermes adoption.

This stage therefore does NOT treat May-29..Jun-15 Hermes rows as anomalous.
It targets the actual remaining provenance questions:
1. dump the June-15 "GPT記憶與偏好遷移" neighborhood that sits beside a
   23-row chatgpt-export import-time cluster;
2. profile those early/partial-import rows and all final chatgpt-export rows
   for time-shape and fields the final importer does not write;
3. use messages.id as an insertion-order proxy around the first imported row;
4. find native-only fingerprints inside rows whose final source is
   chatgpt-export (possible earlier importer behavior or source-rewrite collision);
5. list only genuinely pre-2026-05-29 non-chatgpt rows as provenance anomalies.

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
UTC = timezone.utc
HERMES_START = datetime(2026, 5, 29, 0, 0, 0, tzinfo=TAIPEI).timestamp()
IMPORT_DATE = datetime(2026, 6, 16, 0, 0, 0, tzinfo=TAIPEI).timestamp()

TARGET_SESSIONS = (
    "20260615_045753_a3ff257e",  # immediate pre-cluster live Hermes context
    "20260615_050051_06bd07d3",  # GPT記憶與偏好遷移 — highest-value target
    "20260615_050419_d651d799",  # nearby follow-up / recap candidate
)

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DECIMAL_ID_RE = re.compile(r"^\d{15,22}$")


def sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
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
        out[suffix] = {
            "exists": p.exists(),
            "size": p.stat().st_size if p.exists() else 0,
        }
    return out


def enforce_safe_source(path: Path, expected_sha: str) -> tuple[str, dict]:
    if path.is_symlink():
        raise SystemExit(f"REFUSE: database path is a symlink: {path}")
    if not path.is_file():
        raise SystemExit(f"REFUSE: database is not a regular file: {path}")
    sidecars = sidecar_receipt(path)
    dirty = {
        k: v
        for k, v in sidecars.items()
        if v["exists"] and int(v["size"]) > 0
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
    uri = "file:" + quote(str(path.resolve()), safe="/:\\") + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
        conn.close()
        raise RuntimeError("failed to enable PRAGMA query_only")
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})")}


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
        return datetime.fromtimestamp(float(epoch), tz=UTC).astimezone(TAIPEI).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


def utc_stamp(epoch: object) -> str:
    if epoch is None or epoch == "":
        return ""
    try:
        return datetime.fromtimestamp(float(epoch), tz=UTC).strftime("%Y%m%d_%H%M%S")
    except (ValueError, TypeError, OSError):
        return ""


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> object:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def json_obj(value: object) -> dict:
    text = normalize(value).strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def conv_id_from_model_config(value: object) -> str:
    obj = json_obj(value)
    v = obj.get("chatgpt_conversation_id")
    return normalize(v).strip()


def platform_shape(value: object) -> str:
    text = normalize(value).strip()
    if not text:
        return "empty"
    if UUID_RE.fullmatch(text):
        return "uuid"
    if DECIMAL_ID_RE.fullmatch(text):
        return "discord_snowflake_like"
    if text.startswith("call_"):
        return "call_id"
    return f"other_len_{len(text)}"


def sha12(value: object) -> str:
    text = normalize(value)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def safe_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)[:180] or "unnamed"


def write_tsv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    k: normalize(v)
                    .replace("\t", "\\t")
                    .replace("\r", "\\r")
                    .replace("\n", "\\n")
                    for k, v in row.items()
                }
            )


def dump_transcript(
    conn: sqlite3.Connection,
    out: Path,
    session_id: str,
    session_cols: set[str],
    message_cols: set[str],
) -> bool:
    wanted_s = [
        c
        for c in (
            "id", "title", "display_name", "source", "model", "started_at",
            "ended_at", "end_reason", "parent_session_id", "message_count",
            "model_config", "cwd", "session_key", "chat_id", "chat_type",
            "thread_id", "origin_json",
        )
        if c in session_cols
    ]
    row = conn.execute(
        f"SELECT {', '.join(wanted_s)} FROM sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if not row:
        return False

    wanted_m = [
        c
        for c in (
            "id", "role", "content", "timestamp", "model", "tool_name",
            "tool_call_id", "tool_calls", "reasoning", "reasoning_content",
            "platform_message_id", "finish_reason", "metadata",
        )
        if c in message_cols
    ]
    messages = conn.execute(
        f"SELECT {', '.join(wanted_m)} FROM messages "
        "WHERE session_id=? ORDER BY id",
        (session_id,),
    ).fetchall()

    title = normalize(row["title"]) if "title" in row.keys() else ""
    p = out / "transcripts" / f"{safe_filename(session_id)}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        f.write(f"# Session {session_id}\n\n")
        f.write(f"- title: {title}\n")
        for c in wanted_s:
            if c in ("id", "title"):
                continue
            value = row[c]
            if c in ("started_at", "ended_at") and value not in (None, ""):
                f.write(f"- {c}: {normalize(value)} ({taipei(value)})\n")
            else:
                f.write(f"- {c}: {normalize(value)}\n")
        f.write(f"- actual_message_rows: {len(messages)}\n\n")
        for i, m in enumerate(messages, 1):
            role = normalize(m["role"]) if "role" in m.keys() else ""
            mid = normalize(m["id"]) if "id" in m.keys() else ""
            ts = m["timestamp"] if "timestamp" in m.keys() else None
            f.write(f"## {i}. {role} — id={mid} — timestamp={normalize(ts)}")
            if ts not in (None, ""):
                f.write(f" ({taipei(ts)})")
            f.write("\n\n")
            for c in wanted_m:
                if c in ("id", "role", "timestamp", "content"):
                    continue
                value = m[c]
                if value not in (None, ""):
                    f.write(f"**{c}:**\n\n```text\n{normalize(value)}\n```\n\n")
            if "content" in m.keys():
                f.write(normalize(m["content"]))
                f.write("\n\n")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    ap.add_argument("--expected-sha256", default=AUTHORITATIVE_SHA256)
    ap.add_argument("--output-dir", type=Path)
    args = ap.parse_args()

    started = time.time()
    before_sha, sidecars = enforce_safe_source(args.db, args.expected_sha256)
    before_identity = file_identity(args.db)

    stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
    out = args.output_dir or Path(f"issue60-stage3-evidence-{stamp}")
    out.mkdir(parents=True, exist_ok=False)

    conn = open_immutable(args.db)
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
        mcols = table_columns(conn, "messages")

        # A) Dump the high-value June-15 live-Hermes transcripts.
        dumped = []
        for sid in TARGET_SESSIONS:
            if dump_transcript(conn, out, sid, scols, mcols):
                dumped.append(sid)

        # Also list the ±15 minute neighborhood around the key session.
        key_started = scalar(
            conn, "SELECT started_at FROM sessions WHERE id=?",
            ("20260615_050051_06bd07d3",),
        )
        neighborhood = []
        if key_started not in (None, ""):
            for r in conn.execute(
                """
                SELECT id, title, source, model, started_at, ended_at,
                       parent_session_id, end_reason, message_count
                FROM sessions
                WHERE started_at BETWEEN ? AND ?
                ORDER BY started_at, id
                """,
                (float(key_started) - 900, float(key_started) + 900),
            ):
                d = dict(r)
                d["started_taipei"] = taipei(d.get("started_at"))
                d["ended_taipei"] = taipei(d.get("ended_at"))
                neighborhood.append(d)
        write_tsv(out / "june15_key_session_neighborhood.tsv", neighborhood)

        # B) June-15 05:00–05:01 UTC chatgpt-export cluster.
        session_fields = [
            c for c in (
                "id", "title", "display_name", "source", "model", "started_at",
                "ended_at", "parent_session_id", "end_reason", "message_count",
                "model_config", "user_id", "cwd", "session_key", "chat_id",
                "chat_type", "thread_id", "origin_json",
            ) if c in scols
        ]
        sel = ", ".join(f"s.{c}" for c in session_fields)
        cluster_rows = []
        for r in conn.execute(
            f"""
            SELECT {sel},
                   COUNT(m.id) AS actual_messages,
                   MIN(m.id) AS min_message_id,
                   MAX(m.id) AS max_message_id,
                   MIN(m.timestamp) AS min_message_timestamp,
                   MAX(m.timestamp) AS max_message_timestamp,
                   SUM(CASE WHEN COALESCE(m.tool_name,'') <> '' THEN 1 ELSE 0 END) AS tool_name_rows,
                   SUM(CASE WHEN COALESCE(m.tool_calls,'') <> '' THEN 1 ELSE 0 END) AS tool_call_rows,
                   SUM(CASE WHEN COALESCE(m.platform_message_id,'') <> '' THEN 1 ELSE 0 END) AS platform_id_rows
            FROM sessions s
            LEFT JOIN messages m ON m.session_id=s.id
            WHERE s.source='chatgpt-export'
              AND s.id >= '20260615_050000_'
              AND s.id <  '20260615_050200_'
            GROUP BY s.id
            ORDER BY s.id
            """
        ):
            d = dict(r)
            cid = conv_id_from_model_config(d.get("model_config"))
            d["conversation_id"] = cid
            d["conversation_prefix_matches_id_suffix"] = int(
                bool(cid) and d["id"].split("_")[-1] == cid[:8]
            )
            d["started_taipei"] = taipei(d.get("started_at"))
            d["ended_taipei"] = taipei(d.get("ended_at"))
            d["message_min_taipei"] = taipei(d.get("min_message_timestamp"))
            d["message_max_taipei"] = taipei(d.get("max_message_timestamp"))
            if d.get("started_at") not in (None, "") and d.get("min_message_timestamp") not in (None, ""):
                d["started_minus_message_min_s"] = (
                    float(d["started_at"]) - float(d["min_message_timestamp"])
                )
            else:
                d["started_minus_message_min_s"] = ""
            cluster_rows.append(d)
        write_tsv(out / "june15_chatgpt_import_time_cluster.tsv", cluster_rows)

        # C) Time-shape anomalies across all final chatgpt-export rows.
        # First-message time is an observable proxy, not a claim about create_time.
        time_rows = []
        all_chatgpt_rows = conn.execute(
            f"""
            SELECT {sel},
                   COUNT(m.id) AS actual_messages,
                   MIN(m.id) AS min_message_id,
                   MAX(m.id) AS max_message_id,
                   MIN(m.timestamp) AS min_message_timestamp,
                   MAX(m.timestamp) AS max_message_timestamp
            FROM sessions s
            LEFT JOIN messages m ON m.session_id=s.id
            WHERE s.source='chatgpt-export'
            GROUP BY s.id
            ORDER BY s.id
            """
        ).fetchall()

        for r in all_chatgpt_rows:
            d = dict(r)
            cid = conv_id_from_model_config(d.get("model_config"))
            started_at = d.get("started_at")
            min_ts = d.get("min_message_timestamp")
            delta = None
            if started_at not in (None, "") and min_ts not in (None, ""):
                delta = float(started_at) - float(min_ts)
            expected_from_first_message = ""
            if cid and min_ts not in (None, ""):
                expected_from_first_message = f"{utc_stamp(min_ts)}_{cid[:8]}"
            collision = None
            if expected_from_first_message and expected_from_first_message != d["id"]:
                collision = conn.execute(
                    "SELECT id, source, title, started_at FROM sessions WHERE id=?",
                    (expected_from_first_message,),
                ).fetchone()

            if delta is not None and abs(delta) >= 86400:
                time_rows.append(
                    {
                        "session_id": d["id"],
                        "title": d.get("title"),
                        "started_at": started_at,
                        "started_taipei": taipei(started_at),
                        "min_message_timestamp": min_ts,
                        "min_message_taipei": taipei(min_ts),
                        "started_minus_message_min_s": delta,
                        "conversation_id": cid,
                        "actual_id_suffix": d["id"].split("_")[-1],
                        "conv_id_prefix": cid[:8] if cid else "",
                        "suffix_matches_conv_prefix": int(
                            bool(cid) and d["id"].split("_")[-1] == cid[:8]
                        ),
                        "first_message_proxy_expected_id": expected_from_first_message,
                        "proxy_expected_id_exists": int(collision is not None),
                        "proxy_expected_id_source": collision["source"] if collision else "",
                        "proxy_expected_id_title": collision["title"] if collision else "",
                    }
                )
        time_rows.sort(
            key=lambda r: abs(float(r["started_minus_message_min_s"])),
            reverse=True,
        )
        write_tsv(out / "chatgpt_export_time_anomalies.tsv", time_rows)

        # D) Field/fingerprint audit for rows whose final source is chatgpt-export.
        field_counts = Counter()
        fingerprint_rows = []
        platform_shape_counts = Counter()
        chatgpt_message_min_id = None

        agg = {}
        for r in conn.execute(
            """
            SELECT s.id AS session_id,
                   COUNT(m.id) AS actual_messages,
                   MIN(m.id) AS min_message_id,
                   MAX(m.id) AS max_message_id,
                   SUM(CASE WHEN COALESCE(m.tool_name,'') <> '' THEN 1 ELSE 0 END) AS tool_name_rows,
                   SUM(CASE WHEN COALESCE(m.tool_calls,'') <> '' THEN 1 ELSE 0 END) AS tool_call_rows
            FROM sessions s
            LEFT JOIN messages m ON m.session_id=s.id
            WHERE s.source='chatgpt-export'
            GROUP BY s.id
            """
        ):
            agg[r["session_id"]] = dict(r)
            mid = r["min_message_id"]
            if mid is not None:
                mid = int(mid)
                chatgpt_message_min_id = (
                    mid if chatgpt_message_min_id is None
                    else min(chatgpt_message_min_id, mid)
                )

        suspicious_platform = {}
        for r in conn.execute(
            """
            SELECT m.id AS message_id, m.session_id, m.role, m.platform_message_id
            FROM messages m
            JOIN sessions s ON s.id=m.session_id
            WHERE s.source='chatgpt-export'
              AND COALESCE(m.platform_message_id,'') <> ''
            ORDER BY m.id
            """
        ):
            shape = platform_shape(r["platform_message_id"])
            platform_shape_counts[shape] += 1
            if shape != "uuid" and r["session_id"] not in suspicious_platform:
                suspicious_platform[r["session_id"]] = {
                    "message_id": r["message_id"],
                    "shape": shape,
                    "platform_id_sha12": sha12(r["platform_message_id"]),
                }

        for r in all_chatgpt_rows:
            d = dict(r)
            sid = d["id"]
            a = agg.get(sid, {})
            cid = conv_id_from_model_config(d.get("model_config"))
            native_fields = {}
            for c in (
                "display_name", "parent_session_id", "end_reason", "cwd",
                "session_key", "chat_id", "chat_type", "thread_id", "origin_json",
            ):
                if c in d and d[c] not in (None, ""):
                    field_counts[c] += 1
                    native_fields[c] = d[c]

            score = 0
            reasons = []
            if native_fields:
                score += len(native_fields) * 2
                reasons.extend(f"session_field:{c}" for c in native_fields)
            if int(a.get("tool_name_rows") or 0):
                score += 5
                reasons.append("message_tool_name")
            if int(a.get("tool_call_rows") or 0):
                score += 5
                reasons.append("message_tool_calls")
            if sid in suspicious_platform:
                score += 6
                reasons.append(f"platform_id:{suspicious_platform[sid]['shape']}")
            if not cid:
                score += 4
                reasons.append("missing_chatgpt_conversation_id")
            elif sid.split("_")[-1] != cid[:8]:
                score += 4
                reasons.append("id_suffix_mismatch_conversation_id")

            if score:
                row = {
                    "score": score,
                    "reasons": ",".join(reasons),
                    "session_id": sid,
                    "title": d.get("title"),
                    "started_at": d.get("started_at"),
                    "started_taipei": taipei(d.get("started_at")),
                    "ended_at": d.get("ended_at"),
                    "ended_taipei": taipei(d.get("ended_at")),
                    "model": d.get("model"),
                    "conversation_id": cid,
                    "actual_messages": a.get("actual_messages"),
                    "min_message_id": a.get("min_message_id"),
                    "max_message_id": a.get("max_message_id"),
                    "tool_name_rows": a.get("tool_name_rows"),
                    "tool_call_rows": a.get("tool_call_rows"),
                    "suspicious_platform_shape": suspicious_platform.get(sid, {}).get("shape", ""),
                    "suspicious_platform_message_id": suspicious_platform.get(sid, {}).get("message_id", ""),
                }
                for c, v in native_fields.items():
                    if c in ("session_key", "chat_id", "thread_id", "origin_json"):
                        row[f"{c}_present"] = 1
                        row[f"{c}_sha12"] = sha12(v)
                    else:
                        row[c] = v
                fingerprint_rows.append(row)

        fingerprint_rows.sort(
            key=lambda r: (-int(r["score"]), int(r.get("min_message_id") or 10**18))
        )
        write_tsv(out / "chatgpt_export_native_fingerprint_candidates.tsv", fingerprint_rows)
        write_tsv(
            out / "chatgpt_export_field_presence.tsv",
            [
                {"field": field, "nonempty_sessions": count}
                for field, count in sorted(field_counts.items())
            ],
        )
        write_tsv(
            out / "chatgpt_export_platform_id_shapes.tsv",
            [
                {"shape": shape, "message_rows": count}
                for shape, count in platform_shape_counts.most_common()
            ],
        )

        # E) messages.id insertion-order proxy around the first ChatGPT-imported row.
        boundary_rows = []
        native_before_counts = []
        if chatgpt_message_min_id is not None:
            low = max(1, chatgpt_message_min_id - 75)
            high = chatgpt_message_min_id + 75
            for r in conn.execute(
                """
                SELECT m.id AS message_id, m.session_id, s.source, s.title,
                       s.started_at, m.role, m.timestamp, m.tool_name,
                       m.platform_message_id
                FROM messages m
                JOIN sessions s ON s.id=m.session_id
                WHERE m.id BETWEEN ? AND ?
                ORDER BY m.id
                """,
                (low, high),
            ):
                d = dict(r)
                d["session_started_taipei"] = taipei(d.get("started_at"))
                d["message_taipei"] = taipei(d.get("timestamp"))
                d["platform_id_shape"] = platform_shape(d.get("platform_message_id"))
                d["platform_id_sha12"] = sha12(d.get("platform_message_id"))
                d.pop("platform_message_id", None)
                boundary_rows.append(d)

            for r in conn.execute(
                """
                SELECT s.source, COUNT(*) AS message_rows,
                       COUNT(DISTINCT s.id) AS distinct_sessions
                FROM messages m
                JOIN sessions s ON s.id=m.session_id
                WHERE m.id < ?
                GROUP BY s.source
                ORDER BY message_rows DESC, s.source
                """,
                (chatgpt_message_min_id,),
            ):
                native_before_counts.append(dict(r))
        write_tsv(out / "first_chatgpt_message_id_boundary.tsv", boundary_rows)
        write_tsv(out / "messages_before_first_chatgpt_by_source.tsv", native_before_counts)

        # F) Three time strata with the corrected May-29 boundary.
        strata = [
            ("true_pre_hermes", 0, HERMES_START),
            ("hermes_before_chatgpt_merge", HERMES_START, IMPORT_DATE),
            ("post_import_date", IMPORT_DATE, float("inf")),
        ]
        stratum_rows = []
        for name, lo, hi in strata:
            if hi == float("inf"):
                sql = """
                    SELECT source, COUNT(*) AS sessions,
                           MIN(started_at) AS min_started_at,
                           MAX(started_at) AS max_started_at,
                           SUM(CASE WHEN COALESCE(parent_session_id,'') <> '' THEN 1 ELSE 0 END) AS with_parent,
                           SUM(CASE WHEN end_reason='compression' THEN 1 ELSE 0 END) AS compression_ended
                    FROM sessions
                    WHERE started_at >= ?
                    GROUP BY source
                """
                params = (lo,)
            else:
                sql = """
                    SELECT source, COUNT(*) AS sessions,
                           MIN(started_at) AS min_started_at,
                           MAX(started_at) AS max_started_at,
                           SUM(CASE WHEN COALESCE(parent_session_id,'') <> '' THEN 1 ELSE 0 END) AS with_parent,
                           SUM(CASE WHEN end_reason='compression' THEN 1 ELSE 0 END) AS compression_ended
                    FROM sessions
                    WHERE started_at >= ? AND started_at < ?
                    GROUP BY source
                """
                params = (lo, hi)
            for r in conn.execute(sql, params):
                d = dict(r)
                d["stratum"] = name
                d["min_started_taipei"] = taipei(d.get("min_started_at"))
                d["max_started_taipei"] = taipei(d.get("max_started_at"))
                stratum_rows.append(d)
        write_tsv(out / "corrected_time_strata_source_profile.tsv", stratum_rows)

        # True pre-Hermes non-chatgpt rows only.
        true_pre_rows = []
        for r in conn.execute(
            """
            SELECT id, title, source, model, started_at, ended_at,
                   parent_session_id, end_reason, message_count,
                   display_name, cwd
            FROM sessions
            WHERE started_at < ?
              AND source <> 'chatgpt-export'
            ORDER BY started_at, id
            """,
            (HERMES_START,),
        ):
            d = dict(r)
            d["started_taipei"] = taipei(d.get("started_at"))
            d["ended_taipei"] = taipei(d.get("ended_at"))
            true_pre_rows.append(d)
        write_tsv(out / "true_prehermes_non_chatgpt_sessions.tsv", true_pre_rows)

        summary = {
            "ground_truth": {
                "hermes_runtime_start_taipei": "2026-05-29",
                "chatgpt_import_merge_date_taipei": "2026-06-16",
                "note": "June 16 is import/merge date, not Hermes adoption",
            },
            "canonical_db": {
                "path": str(args.db),
                "sha256": before_sha,
                "counts": counts,
                "quick_check": quick_check,
                "foreign_key_violations": fk_violations,
                "opened_mode": "mode=ro&immutable=1 + PRAGMA query_only=ON",
                "mutations_performed": False,
            },
            "target_transcripts_dumped": dumped,
            "june15_cluster_sessions": len(cluster_rows),
            "chatgpt_export_time_anomalies_ge_1d": len(time_rows),
            "chatgpt_export_native_fingerprint_candidates": len(fingerprint_rows),
            "chatgpt_export_field_presence": dict(field_counts),
            "chatgpt_export_platform_id_shapes": dict(platform_shape_counts),
            "first_chatgpt_message_id": chatgpt_message_min_id,
            "true_prehermes_non_chatgpt_sessions": len(true_pre_rows),
            "output_files": sorted(
                str(p.relative_to(out))
                for p in out.rglob("*")
                if p.is_file()
            ),
        }
        (out / "manifest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        readme = """# Issue #60 stage-3 evidence

Corrected ground truth:

```text
2026-05-29 = Hermes runtime start
2026-06-16 = ChatGPT history import/merge into existing Hermes DB
```

This stage does **not** treat May-29..Jun-15 Hermes rows as anomalous.

High-value outputs:

- `transcripts/20260615_050051_06bd07d3.md` — full `GPT記憶與偏好遷移` session.
- `june15_chatgpt_import_time_cluster.tsv` — the nearby early/partial-import cluster.
- `chatgpt_export_time_anomalies.tsv` — final imported rows whose session start differs from first-message time by >= 1 day.
- `chatgpt_export_native_fingerprint_candidates.tsv` — rows whose final source is `chatgpt-export` but contain fields/message shapes not written by the final importer.
- `first_chatgpt_message_id_boundary.tsv` and `messages_before_first_chatgpt_by_source.tsv` — insertion-order proxy around the first imported message.
- `corrected_time_strata_source_profile.tsv` — source/lineage shape using the real May-29 boundary.
- `true_prehermes_non_chatgpt_sessions.tsv` — only rows that genuinely precede May 29.

Interpretation discipline:

- `messages.id` is an insertion-order proxy, not a wall-clock timestamp.
- first-message timestamp is a useful observable proxy for conversation start, not proof of ChatGPT `create_time`.
- a native fingerprint inside `source='chatgpt-export'` is a candidate for earlier-import behavior or source-rewrite collision; it is not proof by itself.
"""
        (out / "README.md").write_text(readme, encoding="utf-8")

    finally:
        conn.close()

    after_identity = file_identity(args.db)
    after_sha = sha256_file(args.db)
    after_sidecars = sidecar_receipt(args.db)
    if after_identity != before_identity:
        raise SystemExit(
            f"REFUSE: DB identity changed during extraction: "
            f"before={before_identity} after={after_identity}"
        )
    if after_sha != before_sha:
        raise SystemExit(
            f"REFUSE: DB SHA changed during extraction: before={before_sha} after={after_sha}"
        )
    dirty_after = {
        k: v
        for k, v in after_sidecars.items()
        if v["exists"] and int(v["size"]) > 0
    }
    if dirty_after:
        raise SystemExit(f"REFUSE: non-empty SQLite sidecar appeared: {dirty_after}")

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_receipt"] = {
        "before_identity": before_identity,
        "after_identity": after_identity,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "sidecars_before": sidecars,
        "sidecars_after": after_sidecars,
    }
    manifest["elapsed_s"] = round(time.time() - started, 3)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname=out.name)

    print(f"evidence_dir={out}")
    print(f"archive={archive}")
    print(f"sha256={after_sha}")
    print(f"counts={json.dumps(EXPECTED_COUNTS, sort_keys=True)}")
    print(f"target_transcripts={len(manifest['target_transcripts_dumped'])}")
    print(f"june15_cluster_sessions={manifest['june15_cluster_sessions']}")
    print(f"time_anomalies={manifest['chatgpt_export_time_anomalies_ge_1d']}")
    print(f"native_fingerprint_candidates={manifest['chatgpt_export_native_fingerprint_candidates']}")
    print(f"true_prehermes_non_chatgpt={manifest['true_prehermes_non_chatgpt_sessions']}")
    print(f"elapsed_s={manifest['elapsed_s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
