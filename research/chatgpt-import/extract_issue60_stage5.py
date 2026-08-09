#!/usr/bin/env python3
"""Issue #60 stage-5 DB-only closure audit.

Narrow goals after Stage 4:
1. prove the 4,655 `user_id` mismatches are an importer-generation partition,
   not native collision fingerprints;
2. explain all `message_count` drift as repeated message insertion/replay;
3. classify duplicate ChatGPT message UUIDs as within-session replay vs
   cross-session exact clones/conflicts;
4. inspect the repaired v1 orphan without publishing raw private content;
5. summarize surviving imported message-field shape for the final fidelity note.

No original ChatGPT export is required. The canonical DB is opened immutable +
query_only and SHA/count/sidecar identity is checked before and after.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import tarfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

AUTHORITATIVE_PATH = Path(
    "/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db"
)
AUTHORITATIVE_SHA256 = "23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104"
EXPECTED_COUNTS = {"sessions": 7268, "messages": 231513, "gateway_routing": 78}
ORPHAN_SESSION = "20231013_125540_0041efa3"
FINAL_IMPORTER_KEYS = {
    "chatgpt_conversation_id", "chatgpt_gpt_id", "chatgpt_is_archived", "chatgpt_is_starred"
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha12(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, bytes):
        value = value.hex()
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:12]


def file_identity(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"dev": st.st_dev, "ino": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def sidecars(path: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for suffix in ("-wal", "-shm", "-journal"):
        p = Path(str(path) + suffix)
        out[suffix] = {
            "exists": p.exists(),
            "size": p.stat().st_size if p.exists() else 0,
            "mtime_ns": p.stat().st_mtime_ns if p.exists() else None,
        }
    return out


def enforce_safe_source(path: Path, expected_sha: str):
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"REFUSE: canonical DB path is not a regular file: {path}")
    dirty = {k: v for k, v in sidecars(path).items() if v["exists"] and int(v["size"]) > 0}
    if dirty:
        raise SystemExit(f"REFUSE: non-empty SQLite sidecar present: {dirty}")
    actual = sha256_file(path)
    if actual != expected_sha:
        raise SystemExit(f"REFUSE: SHA mismatch: expected={expected_sha} actual={actual}")
    return actual


def open_immutable(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def mc_obj(raw: Any) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        return {"__invalid__": True}
    return obj if isinstance(obj, dict) else {"__non_object__": True}


def normalize(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.hex()
    return str(v).replace("\r\n", "\n").replace("\r", "\n")


def write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields, seen = [], set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k); fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: normalize(row.get(k)) for k in fields})


def message_signature(row: dict[str, Any]) -> tuple:
    """Fields expected to remain identical when the same ChatGPT node is cloned/shared."""
    return (
        normalize(row.get("role")),
        normalize(row.get("timestamp")),
        sha12(row.get("content")),
        sha12(row.get("reasoning")),
        sha12(row.get("reasoning_content")),
        normalize(row.get("finish_reason")),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    ap.add_argument("--expected-sha256", default=AUTHORITATIVE_SHA256)
    ap.add_argument("--output-dir", type=Path)
    args = ap.parse_args()

    started = time.time()
    before_sha = enforce_safe_source(args.db, args.expected_sha256)
    before_identity = file_identity(args.db)
    before_sidecars = sidecars(args.db)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.output_dir or Path(f"issue60-stage5-evidence-{stamp}")
    out.mkdir(parents=True, exist_ok=False)

    conn = open_immutable(args.db)
    try:
        counts = {t: int(scalar(conn, f"SELECT COUNT(*) FROM {t}") or 0) for t in EXPECTED_COUNTS}
        if counts != EXPECTED_COUNTS:
            raise SystemExit(f"REFUSE: canonical row counts changed: {counts}")
        quick_check = normalize(scalar(conn, "PRAGMA quick_check"))
        fk_violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())

        # A) Surviving importer-generation partition.
        generation_counts = Counter()
        generation_profiles = Counter()
        generation_rows = []
        session_counts = {}
        stored_total = 0
        actual_total = 0
        drift_excess = 0
        drift_ratio_counts = Counter()

        for r in conn.execute("""
            SELECT s.id, s.user_id, s.model_config, s.message_count,
                   COUNT(m.id) AS actual_messages
            FROM sessions s LEFT JOIN messages m ON m.session_id=s.id
            WHERE s.source='chatgpt-export'
            GROUP BY s.id
            ORDER BY s.id
        """):
            mc = mc_obj(r["model_config"])
            cid = mc.get("chatgpt_conversation_id") if isinstance(mc.get("chatgpt_conversation_id"), str) else ""
            user_match = bool(cid) and r["user_id"] == cid
            legacy_keys = sorted(k for k in mc if k not in FINAL_IMPORTER_KEYS)
            generation = "final_user_id_shape" if user_match else "pre_final_user_id_shape"
            generation_counts[generation] += 1
            generation_profiles[(generation, ",".join(sorted(mc)), ",".join(legacy_keys))] += 1
            stored = int(r["message_count"] or 0)
            actual = int(r["actual_messages"] or 0)
            session_counts[r["id"]] = (stored, actual)
            stored_total += stored
            actual_total += actual
            if actual != stored:
                drift_excess += actual - stored
                if stored > 0 and actual % stored == 0:
                    drift_ratio_counts[actual // stored] += 1
                else:
                    drift_ratio_counts["non_integer"] += 1

        for (generation, profile, legacy), n in generation_profiles.most_common():
            generation_rows.append({
                "generation_shape": generation,
                "model_config_keys": profile,
                "legacy_extra_keys": legacy,
                "sessions": n,
            })
        write_tsv(out / "generation_partition.tsv", generation_rows)

        # B) Duplicate UUID members, exact-clone/conflict classification.
        dup_ids = [r[0] for r in conn.execute("""
            SELECT m.platform_message_id
            FROM messages m JOIN sessions s ON s.id=m.session_id
            WHERE s.source='chatgpt-export' AND COALESCE(m.platform_message_id,'')<>''
            GROUP BY m.platform_message_id HAVING COUNT(*) > 1
        """)]
        dup_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if dup_ids:
            # Avoid SQLite variable limits by scanning the already-bounded imported corpus once.
            dup_set = set(dup_ids)
            for r in conn.execute("""
                SELECT m.id AS message_id, m.session_id, s.title,
                       m.role, m.timestamp, m.platform_message_id,
                       m.content, m.reasoning, m.reasoning_content, m.finish_reason
                FROM messages m JOIN sessions s ON s.id=m.session_id
                WHERE s.source='chatgpt-export' AND COALESCE(m.platform_message_id,'')<>''
                ORDER BY m.id
            """):
                pid = r["platform_message_id"]
                if pid in dup_set:
                    dup_groups[pid].append(dict(r))

        dup_summary_rows = []
        sample_member_rows = []
        duplicate_class_counts = Counter()
        within_session_excess = 0
        cross_session_excess = 0
        conflicting_uuid_groups = 0

        for pid, members in dup_groups.items():
            sessions = {m["session_id"] for m in members}
            signatures = {message_signature(m) for m in members}
            copies = len(members)
            session_n = len(sessions)
            within_extra = copies - session_n
            cross_extra = session_n - 1
            within_session_excess += within_extra
            cross_session_excess += cross_extra
            if session_n == 1:
                cls = "within_session_replay"
            elif len(signatures) == 1:
                cls = "cross_session_exact_clone" if copies == session_n else "cross_session_exact_clone_plus_replay"
            else:
                cls = "cross_session_conflict"
                conflicting_uuid_groups += 1
            duplicate_class_counts[cls] += 1
            dup_summary_rows.append({
                "platform_message_id": pid,
                "class": cls,
                "copies": copies,
                "distinct_sessions": session_n,
                "distinct_signatures": len(signatures),
                "within_session_excess": within_extra,
                "cross_session_excess": cross_extra,
                "min_message_id": min(int(m["message_id"]) for m in members),
                "max_message_id": max(int(m["message_id"]) for m in members),
            })

        dup_summary_rows.sort(key=lambda x: (-int(x["copies"]), x["platform_message_id"]))
        write_tsv(out / "duplicate_uuid_classification.tsv", dup_summary_rows)

        # Publish only bounded hashes/metadata for representative multi-session duplicate groups.
        for group in [g for g in dup_summary_rows if int(g["distinct_sessions"]) > 1][:100]:
            pid = group["platform_message_id"]
            for m in dup_groups[pid]:
                sample_member_rows.append({
                    "platform_message_id": pid,
                    "class": group["class"],
                    "message_id": m["message_id"],
                    "session_id": m["session_id"],
                    "title": m["title"],
                    "role": m["role"],
                    "timestamp": m["timestamp"],
                    "content_sha12": sha12(m["content"]),
                    "reasoning_sha12": sha12(m["reasoning"]),
                    "reasoning_content_sha12": sha12(m["reasoning_content"]),
                    "finish_reason": m["finish_reason"],
                })
        write_tsv(out / "cross_session_duplicate_samples.tsv", sample_member_rows)

        # C) Orphan detailed shape, still privacy-safe.
        orphan_rows = []
        for r in conn.execute("""
            SELECT id AS message_id, role, timestamp, platform_message_id,
                   content, reasoning, reasoning_content, finish_reason,
                   tool_name, tool_calls
            FROM messages WHERE session_id=? ORDER BY id
        """, (ORPHAN_SESSION,)):
            orphan_rows.append({
                "message_id": r["message_id"],
                "role": r["role"],
                "timestamp": r["timestamp"],
                "platform_message_id": r["platform_message_id"],
                "content_sha12": sha12(r["content"]),
                "content_len": len(normalize(r["content"])),
                "reasoning_sha12": sha12(r["reasoning"]),
                "reasoning_content_sha12": sha12(r["reasoning_content"]),
                "finish_reason": r["finish_reason"],
                "tool_name_present": int(r["tool_name"] not in (None, "")),
                "tool_calls_present": int(r["tool_calls"] not in (None, "")),
            })
        write_tsv(out / "orphan_0041efa3_messages.tsv", orphan_rows)

        # D) Final imported message-field shape.
        field_row = dict(conn.execute("""
            SELECT COUNT(*) AS messages,
                   SUM(CASE WHEN COALESCE(content,'')='' THEN 1 ELSE 0 END) AS empty_content,
                   SUM(CASE WHEN reasoning IS NOT NULL AND reasoning<>'' THEN 1 ELSE 0 END) AS reasoning_nonempty,
                   SUM(CASE WHEN reasoning_content IS NOT NULL AND reasoning_content<>'' THEN 1 ELSE 0 END) AS reasoning_content_nonempty,
                   SUM(CASE WHEN COALESCE(finish_reason,'')<>'' THEN 1 ELSE 0 END) AS finish_reason_nonempty,
                   SUM(CASE WHEN COALESCE(tool_name,'')<>'' THEN 1 ELSE 0 END) AS tool_name_nonempty,
                   SUM(CASE WHEN COALESCE(tool_calls,'')<>'' THEN 1 ELSE 0 END) AS tool_calls_nonempty,
                   SUM(CASE WHEN COALESCE(platform_message_id,'')='' THEN 1 ELSE 0 END) AS platform_id_empty
            FROM messages m JOIN sessions s ON s.id=m.session_id
            WHERE s.source='chatgpt-export'
        """).fetchone())
        role_rows = [dict(r) for r in conn.execute("""
            SELECT m.role, COUNT(*) AS messages
            FROM messages m JOIN sessions s ON s.id=m.session_id
            WHERE s.source='chatgpt-export'
            GROUP BY m.role ORDER BY messages DESC, m.role
        """)]
        write_tsv(out / "imported_message_field_presence.tsv", [field_row])
        write_tsv(out / "imported_message_roles.tsv", role_rows)

        summary = {
            "canonical_db": {
                "path": str(args.db), "sha256": before_sha, "counts": counts,
                "quick_check": quick_check, "foreign_key_violations": fk_violations,
                "opened_mode": "mode=ro&immutable=1 + PRAGMA query_only=ON",
                "mutations_performed": False,
            },
            "generation_shape_counts": dict(generation_counts),
            "stored_message_count_sum": stored_total,
            "actual_imported_message_rows": actual_total,
            "message_count_drift_excess": drift_excess,
            "drift_integer_ratio_counts": {str(k): v for k, v in drift_ratio_counts.items()},
            "duplicate_uuid_groups": len(dup_groups),
            "duplicate_uuid_class_counts": dict(duplicate_class_counts),
            "within_session_duplicate_uuid_excess": within_session_excess,
            "cross_session_shared_uuid_excess": cross_session_excess,
            "conflicting_uuid_groups": conflicting_uuid_groups,
            "orphan_message_rows": len(orphan_rows),
            "message_field_presence": field_row,
            "interpretation_guards": {
                "generation_partition": "Exact counts matching the final-run 792 inserts vs 4,654 existing IDs + 1 repaired orphan are strong pass-provenance evidence; identity of every row remains an inference without a before-snapshot/list of inserted IDs.",
                "cross_session_uuid": "Same ChatGPT message UUID in multiple sessions may represent shared/cloned conversation ancestry. Exact signatures support that interpretation; conflicts would require separate investigation.",
                "stored_message_count": "Session-level stored message_count is historical importer state, not an authoritative deduplicated count after later replay inserts.",
            },
        }
        (out / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (out / "README.md").write_text("""# Issue #60 stage-5 evidence

Final DB-only closure pass for importer-generation and message-replay provenance.

Key outputs:
- `generation_partition.tsv`
- `duplicate_uuid_classification.tsv`
- `cross_session_duplicate_samples.tsv`
- `orphan_0041efa3_messages.tsv`
- `imported_message_field_presence.tsv`
- `imported_message_roles.tsv`

Raw private message content is not emitted; only hashes/lengths and bounded metadata are used.
""", encoding="utf-8")
    finally:
        conn.close()

    after_identity = file_identity(args.db)
    after_sha = sha256_file(args.db)
    after_sidecars = sidecars(args.db)
    if after_identity != before_identity or after_sha != before_sha:
        raise SystemExit("REFUSE: canonical DB identity/SHA changed during extraction")
    dirty = {k: v for k, v in after_sidecars.items() if v["exists"] and int(v["size"]) > 0}
    if dirty:
        raise SystemExit(f"REFUSE: non-empty SQLite sidecar appeared: {dirty}")

    mp = out / "manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["source_receipt"] = {
        "before_identity": before_identity, "after_identity": after_identity,
        "before_sha256": before_sha, "after_sha256": after_sha,
        "sidecars_before": before_sidecars, "sidecars_after": after_sidecars,
    }
    manifest["elapsed_s"] = round(time.time() - started, 3)
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname=out.name)
    print(f"evidence_dir={out}")
    print(f"archive={archive}")
    print(f"generation_shape_counts={json.dumps(manifest['generation_shape_counts'], sort_keys=True)}")
    print(f"drift_excess={manifest['message_count_drift_excess']}")
    print(f"duplicate_uuid_groups={manifest['duplicate_uuid_groups']}")
    print(f"conflicting_uuid_groups={manifest['conflicting_uuid_groups']}")
    print(f"elapsed_s={manifest['elapsed_s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
