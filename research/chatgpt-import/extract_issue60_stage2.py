#!/usr/bin/env python3
"""Issue #60 second-pass evidence extractor.

Purpose:
- search the *whole* frozen DB for the actual ChatGPT import/merge discussion;
- quantify session-id / started_at / message-time alignment for chatgpt-export rows;
- test whether pre-Hermes Hermes-shaped rows duplicate content from chatgpt-export rows.

Safety:
- authoritative SHA-256 required;
- refuses non-empty SQLite sidecars;
- mode=ro&immutable=1 + PRAGMA query_only=ON;
- no TEMP/schema/FTS/VACUUM/journal writes;
- source identity + SHA verified again after extraction.
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
from collections import Counter, defaultdict
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
PRE_HERMES_CUTOFF = datetime(2026, 6, 16, 0, 0, 0, tzinfo=TAIPEI).timestamp()

ANOMALY_IDS = (
    "20260530_113929_d6a58f32",
    "20260530_115158_865460",
    "20260530_115227_bcc3af",
    "20260530_115257_615c41",
    "20260530_115429_f53e5e",
    "20260530_115503_45592e",
    "20260530_121329_ec5bb7",
    "20260530_122122_363260",
    "20260530_122213_8ed0b9",
    "20260530_122919_b1b82b",
    "20260530_123019_e8498d",
    "20260530_123052_5fc886",
    "20260530_123527_2efadd",
    "20260530_123618_de633f",
    "20260530_123708_7ddcf4",
)

KEYWORDS = {
    "current_node": 15,
    "chatgpt-export": 15,
    "conversation_asset": 15,
    "export_manifest": 15,
    "conversations-": 13,
    "conversation_id": 10,
    "default_model_slug": 10,
    "content.parts": 10,
    "author.role": 10,
    "content_references": 10,
    "zipfile": 10,
    "mapping": 8,
    "mapping.parent": 10,
    "parent": 2,
    "chatgpt export": 10,
    "state.db": 10,
    "session.db": 10,
    "sqlite": 6,
    "merge": 7,
    "import": 5,
    "export": 4,
    "script": 3,
    "parent_session_id": 6,
    "end_reason": 5,
    "model_config": 5,
    "downloads": 5,
}

SESSION_FIELDS = (
    "id", "title", "display_name", "started_at", "ended_at", "source", "model",
    "parent_session_id", "end_reason", "model_config", "cwd",
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
        "size": st.st_size, "mtime_ns": st.st_mtime_ns, "inode": st.st_ino,
        "device": st.st_dev, "mode": stat.S_IMODE(st.st_mode),
    }

def sidecar_receipt(path: Path) -> dict[str, dict[str, int | bool]]:
    out = {}
    for suffix in ("-wal", "-shm", "-journal"):
        side = Path(str(path) + suffix)
        out[suffix] = {
            "exists": side.exists(),
            "size": side.stat().st_size if side.exists() else 0,
        }
    return out

def enforce_source(path: Path) -> tuple[str, dict]:
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"REFUSE: not a regular non-symlink DB: {path}")
    sidecars = sidecar_receipt(path)
    dirty = {k: v for k, v in sidecars.items() if v["exists"] and int(v["size"]) > 0}
    if dirty:
        raise SystemExit(f"REFUSE: non-empty SQLite sidecars: {dirty}")
    actual = sha256_file(path)
    if actual.lower() != AUTHORITATIVE_SHA256.lower():
        raise SystemExit(
            f"REFUSE: SHA-256 mismatch\n expected={AUTHORITATIVE_SHA256}\n actual={actual}"
        )
    return actual, sidecars

def open_immutable(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(path.resolve()), safe="/:\\") + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
        conn.close()
        raise RuntimeError("failed to enable query_only")
    return conn

def norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)

def clip(text: str, n: int = 700) -> str:
    text = text.replace("\x00", "\\0")
    return text if len(text) <= n else text[:n] + f"… <{len(text)-n} chars omitted>"

def write_tsv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        seen = set()
        for row in rows:
            for k in row:
                if k not in seen:
                    seen.add(k)
                    fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({
                k: norm(v).replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
                for k, v in row.items()
            })

def iso(epoch) -> str:
    if epoch is None or epoch == "":
        return ""
    try:
        x = float(epoch)
    except Exception:
        return ""
    return datetime.fromtimestamp(x, timezone.utc).isoformat()

def iso_taipei(epoch) -> str:
    if epoch is None or epoch == "":
        return ""
    try:
        x = float(epoch)
    except Exception:
        return ""
    return datetime.fromtimestamp(x, TAIPEI).isoformat()

def session_id_date(session_id: str) -> str:
    m = re.match(r"^(\d{8})_", session_id or "")
    return m.group(1) if m else ""

def scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None

def dump_transcript(conn: sqlite3.Connection, session_id: str, out: Path) -> None:
    s = conn.execute(
        "SELECT id,title,display_name,started_at,ended_at,source,model,"
        "parent_session_id,end_reason,model_config,cwd FROM sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if not s:
        return
    msgs = conn.execute(
        "SELECT id,role,content,timestamp,tool_name,tool_call_id,tool_calls,"
        "finish_reason,display_kind,display_metadata "
        "FROM messages WHERE session_id=? ORDER BY timestamp,id",
        (session_id,),
    ).fetchall()
    lines = [
        f"# Session {session_id}", "", "## Session row", "", "```json",
        json.dumps(dict(s), ensure_ascii=False, indent=2), "```", "",
        f"## Messages ({len(msgs)})", "",
    ]
    for i, m in enumerate(msgs, 1):
        lines.append(
            f"### {i}. {norm(m['role'])} — id={m['id']} — "
            f"timestamp={m['timestamp']} ({iso_taipei(m['timestamp'])})"
        )
        lines.append("")
        lines.append(norm(m["content"]))
        extra = {k: m[k] for k in ("tool_name","tool_call_id","tool_calls","finish_reason",
                                    "display_kind","display_metadata") if m[k] not in (None, "")}
        if extra:
            lines += ["", "```json", json.dumps(extra, ensure_ascii=False, indent=2), "```"]
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    ap.add_argument("--output-parent", type=Path, default=Path("."))
    args = ap.parse_args()

    started = time.monotonic()
    actual_sha, sidecars = enforce_source(args.db)
    identity_before = file_identity(args.db)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.output_parent / f"issue60-stage2-evidence-{stamp}"
    out.mkdir(parents=True, exist_ok=False)

    conn = open_immutable(args.db)
    counts = {t: int(scalar(conn, f"SELECT COUNT(*) FROM {t}") or 0)
              for t in ("sessions","messages","gateway_routing")}
    if counts != EXPECTED_COUNTS:
        conn.close()
        raise SystemExit(f"REFUSE: canonical row counts mismatch: {counts}")

    # 1) Whole-DB keyword scan, one pass over messages.
    session_meta = {}
    for r in conn.execute(
        "SELECT id,title,source,started_at,ended_at,parent_session_id,end_reason FROM sessions"
    ):
        session_meta[r["id"]] = dict(r)

    session_scores = defaultdict(int)
    session_terms = defaultdict(set)
    session_hit_msgs = defaultdict(set)
    hits = []

    for m in conn.execute(
        "SELECT id,session_id,role,content,timestamp FROM messages ORDER BY id"
    ):
        text = norm(m["content"])
        low = text.lower()
        matched = [term for term in KEYWORDS if term in low]
        if not matched:
            continue
        sid = norm(m["session_id"])
        meta = session_meta.get(sid, {})
        score = sum(KEYWORDS[t] for t in matched)
        session_scores[sid] += score
        session_terms[sid].update(matched)
        session_hit_msgs[sid].add(m["id"])
        hits.append({
            "session_id": sid,
            "title": meta.get("title"),
            "source": meta.get("source"),
            "session_started_at": meta.get("started_at"),
            "message_id": m["id"],
            "role": m["role"],
            "message_timestamp": m["timestamp"],
            "message_time_taipei": iso_taipei(m["timestamp"]),
            "matched_terms": ",".join(matched),
            "score": score,
            "content_excerpt": clip(text, 1200),
        })

    ranked = []
    for sid, score in session_scores.items():
        meta = session_meta.get(sid, {})
        ranked.append({
            "session_id": sid,
            "title": meta.get("title"),
            "source": meta.get("source"),
            "started_at": meta.get("started_at"),
            "started_taipei": iso_taipei(meta.get("started_at")),
            "score": score,
            "distinct_terms": len(session_terms[sid]),
            "hit_messages": len(session_hit_msgs[sid]),
            "terms": ",".join(sorted(session_terms[sid])),
        })
    ranked.sort(key=lambda r: (-int(r["score"]), -int(r["distinct_terms"]),
                               -int(r["hit_messages"]), r["session_id"]))
    write_tsv(out/"all_source_import_keyword_hits.tsv", hits)
    write_tsv(out/"all_source_import_candidate_sessions.tsv", ranked)

    # 2) ChatGPT-export time alignment and import-time clustering.
    alignment = []
    minute_counts = Counter()
    role_counts = Counter()
    chatgpt_session_ids = {
        r[0] for r in conn.execute("SELECT id FROM sessions WHERE source='chatgpt-export'")
    }

    msg_range = {}
    for r in conn.execute(
        "SELECT session_id,MIN(timestamp) AS min_ts,MAX(timestamp) AS max_ts,COUNT(*) AS n "
        "FROM messages GROUP BY session_id"
    ):
        msg_range[r["session_id"]] = (r["min_ts"], r["max_ts"], r["n"])

    for r in conn.execute(
        "SELECT id,title,started_at,ended_at,source,model,model_config,"
        "parent_session_id,end_reason,cwd FROM sessions "
        "WHERE source='chatgpt-export' ORDER BY started_at,id"
    ):
        min_ts, max_ts, n = msg_range.get(r["id"], (None, None, 0))
        started_at = r["started_at"]
        if started_at is not None:
            minute_counts[int(float(started_at)//60)*60] += 1
        alignment.append({
            "session_id": r["id"],
            "id_date": session_id_date(r["id"]),
            "title": r["title"],
            "started_at": started_at,
            "started_taipei": iso_taipei(started_at),
            "ended_at": r["ended_at"],
            "ended_taipei": iso_taipei(r["ended_at"]),
            "message_count_actual": n,
            "message_min_timestamp": min_ts,
            "message_min_taipei": iso_taipei(min_ts),
            "message_max_timestamp": max_ts,
            "message_max_taipei": iso_taipei(max_ts),
            "started_minus_message_min_s": (
                float(started_at)-float(min_ts)
                if started_at is not None and min_ts is not None else ""
            ),
            "ended_minus_message_max_s": (
                float(r["ended_at"])-float(max_ts)
                if r["ended_at"] is not None and max_ts is not None else ""
            ),
            "model": r["model"],
            "model_config": r["model_config"],
        })
    write_tsv(out/"chatgpt_export_time_alignment.tsv", alignment)

    clusters = [{
        "minute_epoch": minute,
        "minute_taipei": iso_taipei(minute),
        "session_count": count,
    } for minute, count in minute_counts.most_common()]
    write_tsv(out/"chatgpt_export_started_at_minute_clusters.tsv", clusters)

    for r in conn.execute(
        "SELECT role,COUNT(*) AS n,"
        "SUM(CASE WHEN tool_name IS NOT NULL AND tool_name<>'' THEN 1 ELSE 0 END) AS with_tool_name,"
        "SUM(CASE WHEN tool_calls IS NOT NULL AND tool_calls<>'' THEN 1 ELSE 0 END) AS with_tool_calls,"
        "SUM(CASE WHEN tool_call_id IS NOT NULL AND tool_call_id<>'' THEN 1 ELSE 0 END) AS with_tool_call_id "
        "FROM messages WHERE session_id IN "
        "(SELECT id FROM sessions WHERE source='chatgpt-export') GROUP BY role ORDER BY n DESC"
    ):
        role_counts[r["role"]] = r["n"]
    role_profile = [dict(r) for r in conn.execute(
        "SELECT role,COUNT(*) AS message_count,"
        "SUM(CASE WHEN tool_name IS NOT NULL AND tool_name<>'' THEN 1 ELSE 0 END) AS with_tool_name,"
        "SUM(CASE WHEN tool_calls IS NOT NULL AND tool_calls<>'' THEN 1 ELSE 0 END) AS with_tool_calls,"
        "SUM(CASE WHEN tool_call_id IS NOT NULL AND tool_call_id<>'' THEN 1 ELSE 0 END) AS with_tool_call_id "
        "FROM messages WHERE session_id IN "
        "(SELECT id FROM sessions WHERE source='chatgpt-export') GROUP BY role ORDER BY message_count DESC"
    )]
    write_tsv(out/"chatgpt_export_message_role_profile.tsv", role_profile)

    # 3) Build exact content-hash index for chatgpt-export messages.
    chat_hash = defaultdict(list)
    for r in conn.execute(
        "SELECT m.id,m.session_id,m.role,m.content,m.timestamp "
        "FROM messages m JOIN sessions s ON s.id=m.session_id "
        "WHERE s.source='chatgpt-export'"
    ):
        content = norm(r["content"])
        # Ignore tiny boilerplate/common messages: they create noisy many-to-many
        # crossmatches and are weak provenance evidence.
        if len(content.strip()) < 40:
            continue
        h = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        chat_hash[h].append((r["session_id"], r["id"], r["role"], r["timestamp"]))

    # 4) Cross-match all pre-Hermes non-chatgpt rows against chatgpt-export exact content.
    match_rows = []
    summary = defaultdict(lambda: {"total": 0, "matched": 0, "targets": Counter()})
    anomaly_matches = []

    for r in conn.execute(
        "SELECT m.id AS message_id,m.session_id,m.role,m.content,m.timestamp,"
        "s.source,s.title,s.started_at,s.parent_session_id,s.end_reason "
        "FROM messages m JOIN sessions s ON s.id=m.session_id "
        "WHERE s.source<>'chatgpt-export' AND s.started_at < ? ORDER BY m.id",
        (PRE_HERMES_CUTOFF,),
    ):
        sid = r["session_id"]
        summary[sid]["total"] += 1
        content = norm(r["content"])
        if len(content.strip()) < 40:
            continue
        h = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        targets = chat_hash.get(h, ())
        # Very common repeated blobs are not useful provenance evidence.
        if len(targets) > 20:
            continue
        if not targets:
            continue
        summary[sid]["matched"] += 1
        for target_sid, target_mid, target_role, target_ts in targets[:20]:
            summary[sid]["targets"][target_sid] += 1
            row = {
                "prehermes_session_id": sid,
                "prehermes_source": r["source"],
                "prehermes_title": r["title"],
                "prehermes_message_id": r["message_id"],
                "prehermes_role": r["role"],
                "prehermes_timestamp": r["timestamp"],
                "prehermes_time_taipei": iso_taipei(r["timestamp"]),
                "chatgpt_session_id": target_sid,
                "chatgpt_message_id": target_mid,
                "chatgpt_role": target_role,
                "chatgpt_timestamp": target_ts,
                "chatgpt_time_taipei": iso_taipei(target_ts),
                "same_timestamp": int(
                    r["timestamp"] is not None and target_ts is not None
                    and abs(float(r["timestamp"])-float(target_ts)) < 1e-6
                ),
                "content_sha256": h,
                "content_excerpt": clip(content, 900),
            }
            match_rows.append(row)
            if sid in ANOMALY_IDS:
                anomaly_matches.append(row)
    write_tsv(out/"prehermes_chatgpt_exact_message_matches.tsv", match_rows)
    write_tsv(out/"anomaly_chatgpt_exact_message_matches.tsv", anomaly_matches)

    summary_rows = []
    for sid, data in summary.items():
        meta = session_meta.get(sid, {})
        top_sid, top_count = ("", 0)
        if data["targets"]:
            top_sid, top_count = data["targets"].most_common(1)[0]
        total = data["total"]
        matched = data["matched"]
        summary_rows.append({
            "prehermes_session_id": sid,
            "source": meta.get("source"),
            "title": meta.get("title"),
            "started_at": meta.get("started_at"),
            "started_taipei": iso_taipei(meta.get("started_at")),
            "parent_session_id": meta.get("parent_session_id"),
            "end_reason": meta.get("end_reason"),
            "message_count": total,
            "matched_message_count": matched,
            "matched_fraction": (matched/total if total else 0),
            "top_chatgpt_session_id": top_sid,
            "top_chatgpt_match_count": top_count,
        })
    summary_rows.sort(key=lambda r: (-float(r["matched_fraction"]),
                                     -int(r["matched_message_count"]),
                                     r["prehermes_session_id"]))
    write_tsv(out/"prehermes_chatgpt_crossmatch_summary.tsv", summary_rows)

    # 5) Dump strongest whole-DB keyword candidates + any ChatGPT source sessions
    # implicated by anomaly exact-content matches.
    dump_ids = []
    for r in ranked:
        if int(r["score"]) >= 12 or int(r["distinct_terms"]) >= 2:
            dump_ids.append(r["session_id"])
        if len(dump_ids) >= 24:
            break
    implicated = Counter(r["chatgpt_session_id"] for r in anomaly_matches)
    for sid, _ in implicated.most_common(12):
        if sid not in dump_ids:
            dump_ids.append(sid)
    dump_ids = dump_ids[:36]

    td = out/"transcripts"
    td.mkdir()
    for sid in dump_ids:
        dump_transcript(conn, sid, td/f"{sid}.md")

    # A small locator table for all sessions around the dominant import-start minute.
    around_rows = []
    if clusters:
        dominant = int(clusters[0]["minute_epoch"])
        lo, hi = dominant - 6*3600, dominant + 6*3600
        for r in conn.execute(
            "SELECT id,title,source,started_at,ended_at,model,parent_session_id,end_reason,model_config "
            "FROM sessions WHERE started_at BETWEEN ? AND ? ORDER BY started_at,id",
            (lo, hi),
        ):
            row = dict(r)
            row["started_taipei"] = iso_taipei(r["started_at"])
            row["ended_taipei"] = iso_taipei(r["ended_at"])
            around_rows.append(row)
    write_tsv(out/"sessions_around_dominant_chatgpt_import_cluster.tsv", around_rows)

    conn.close()

    identity_after = file_identity(args.db)
    sha_after = sha256_file(args.db)
    if identity_after != identity_before or sha_after != actual_sha:
        raise SystemExit("REFUSE: frozen DB identity/hash changed during extraction")

    manifest = {
        "issue": 60,
        "stage": 2,
        "source": {
            "path": str(args.db),
            "sha256": actual_sha,
            "sidecars": sidecars,
            "identity_before": identity_before,
            "identity_after": identity_after,
            "opened_mode": "mode=ro&immutable=1 + PRAGMA query_only=ON",
            "mutations_performed": False,
        },
        "counts": counts,
        "prehermes_cutoff_taipei": datetime.fromtimestamp(
            PRE_HERMES_CUTOFF, TAIPEI
        ).isoformat(),
        "whole_db_keyword_hit_rows": len(hits),
        "whole_db_candidate_sessions": len(ranked),
        "dumped_transcripts": dump_ids,
        "chatgpt_export_sessions": len(alignment),
        "prehermes_exact_match_rows": len(match_rows),
        "anomaly_exact_match_rows": len(anomaly_matches),
        "elapsed_s": time.monotonic()-started,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (out/"manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out/"README.md").write_text(
        "# Issue #60 stage-2 evidence\n\n"
        "This pass deliberately searches the whole frozen DB after stage 1 disproved "
        "the assumed `source='chatgpt-export' AND id LIKE '20260616_%'` target.\n\n"
        "High-value outputs:\n\n"
        "- `all_source_import_candidate_sessions.tsv` + `transcripts/`: importer/merge discussion candidates across every source.\n"
        "- `chatgpt_export_time_alignment.tsv`: session id/start/end vs actual message-time ranges.\n"
        "- `chatgpt_export_started_at_minute_clusters.tsv`: insertion/start-time clustering.\n"
        "- `prehermes_chatgpt_crossmatch_summary.tsv`: exact-content overlap between pre-Hermes non-chatgpt rows and chatgpt-export rows.\n"
        "- `anomaly_chatgpt_exact_message_matches.tsv`: exact overlap for the 15-session May-30 anomaly lineage.\n"
        "- `sessions_around_dominant_chatgpt_import_cluster.tsv`: all-source locator around the dominant cluster.\n\n"
        "Evidence only; do not interpret exact-content absence as proof of unrelated provenance.\n",
        encoding="utf-8",
    )

    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname=out.name)

    print(f"evidence_dir={out}")
    print(f"archive={archive}")
    print(f"sha256={actual_sha}")
    print(f"counts={json.dumps(counts, sort_keys=True)}")
    print(f"whole_db_candidate_sessions={len(ranked)}")
    print(f"dumped_transcripts={len(dump_ids)}")
    print(f"prehermes_exact_match_rows={len(match_rows)}")
    print(f"anomaly_exact_match_rows={len(anomaly_matches)}")
    print(f"elapsed_s={time.monotonic()-started:.3f}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
