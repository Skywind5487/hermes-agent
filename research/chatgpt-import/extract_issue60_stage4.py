#!/usr/bin/env python3
"""Issue #60 stage-4 read-only provenance classifier.

Purpose:
- classify final chatgpt-export rows by model_config field shape;
- measure stored message_count drift versus actual message rows;
- check full generated-session-ID consistency against started_at + conversation_id;
- locate the two known empty/non-UUID platform_message_id cases;
- further reduce the residual native-session collision/relabel hypothesis.

This extractor is deliberately DB-only. It does not need the original ChatGPT ZIP.
It opens the canonical recovered DB immutable + query_only and fails closed on
SHA/count/sidecar changes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import tarfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUTHORITATIVE_PATH = Path(
    "/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db"
)
AUTHORITATIVE_SHA256 = "23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104"
EXPECTED_COUNTS = {
    "sessions": 7268,
    "messages": 231513,
    "gateway_routing": 78,
}

FINAL_IMPORTER_KEYS = {
    "chatgpt_conversation_id",
    "chatgpt_gpt_id",
    "chatgpt_is_archived",
    "chatgpt_is_starred",
}
# Historical transcript explicitly records the first 57 test imports writing
# chatgpt_memory_scope, then backfilling chatgpt_conversation_id.
EARLY_TEST_SIGNATURE_KEY = "chatgpt_memory_scope"

JUNE15_CLUSTER_LO = "20260615_050000_"
JUNE15_CLUSTER_HI = "20260615_050200_"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "dev": st.st_dev,
        "ino": st.st_ino,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }


def sidecar_receipt(path: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for suffix in ("-wal", "-shm", "-journal"):
        p = Path(str(path) + suffix)
        if p.exists():
            st = p.stat()
            out[suffix] = {"exists": True, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        else:
            out[suffix] = {"exists": False, "size": 0, "mtime_ns": None}
    return out


def enforce_safe_source(path: Path, expected_sha: str):
    if not path.is_file():
        raise SystemExit(f"REFUSE: canonical DB missing: {path}")
    before_sha = sha256_file(path)
    if before_sha != expected_sha:
        raise SystemExit(
            f"REFUSE: canonical SHA mismatch: expected={expected_sha} actual={before_sha}"
        )
    sidecars = sidecar_receipt(path)
    dirty = {k: v for k, v in sidecars.items() if v["exists"] and int(v["size"]) > 0}
    if dirty:
        raise SystemExit(f"REFUSE: non-empty SQLite sidecar present: {dirty}")
    return before_sha, sidecars


def open_immutable(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


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
    fields = []
    seen = set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: normalize(row.get(k)) for k in fields})


def model_config_obj(raw: Any) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    try:
        obj = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"__invalid_json__": True}
    return obj if isinstance(obj, dict) else {"__non_object_json__": True}


def conv_id(mc: dict[str, Any]) -> str:
    v = mc.get("chatgpt_conversation_id")
    return v if isinstance(v, str) else ""


def utc_session_prefix(ts: Any) -> str:
    if ts in (None, ""):
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    except (TypeError, ValueError, OSError):
        return ""


def uuid_shape(value: Any) -> str:
    s = normalize(value)
    if not s:
        return "empty"
    parts = s.split("-")
    if len(parts) == 5 and [len(x) for x in parts] == [8, 4, 4, 4, 12]:
        try:
            int("".join(parts), 16)
            return "uuid"
        except ValueError:
            pass
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    ap.add_argument("--expected-sha256", default=AUTHORITATIVE_SHA256)
    ap.add_argument("--output-dir", type=Path)
    args = ap.parse_args()

    started = time.time()
    before_sha, sidecars_before = enforce_safe_source(args.db, args.expected_sha256)
    identity_before = file_identity(args.db)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.output_dir or Path(f"issue60-stage4-evidence-{stamp}")
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

        msg_agg = {}
        for r in conn.execute(
            """
            SELECT s.id AS session_id,
                   COUNT(m.id) AS actual_messages,
                   MIN(m.id) AS min_message_id,
                   MAX(m.id) AS max_message_id,
                   MIN(m.timestamp) AS min_message_timestamp,
                   MAX(m.timestamp) AS max_message_timestamp,
                   SUM(CASE WHEN COALESCE(m.platform_message_id,'')='' THEN 1 ELSE 0 END)
                       AS empty_platform_ids,
                   SUM(CASE WHEN COALESCE(m.tool_name,'')<>'' THEN 1 ELSE 0 END)
                       AS tool_name_rows,
                   SUM(CASE WHEN COALESCE(m.tool_calls,'')<>'' THEN 1 ELSE 0 END)
                       AS tool_call_rows
            FROM sessions s
            LEFT JOIN messages m ON m.session_id=s.id
            WHERE s.source='chatgpt-export'
            GROUP BY s.id
            """
        ):
            msg_agg[r["session_id"]] = dict(r)

        sessions = conn.execute(
            """
            SELECT id, title, source, user_id, model, model_config,
                   started_at, ended_at, message_count,
                   display_name, parent_session_id, end_reason, cwd,
                   session_key, chat_id, chat_type, thread_id, origin_json
            FROM sessions
            WHERE source='chatgpt-export'
            ORDER BY id
            """
        ).fetchall()

        key_counts = Counter()
        profile_counts = Counter()
        legacy_extra_key_counts = Counter()
        profile_examples: dict[str, list[str]] = {}
        early_signature_rows = []
        drift_rows = []
        id_mismatch_rows = []
        residual_rows = []
        june15_rows = []

        for r in sessions:
            d = dict(r)
            sid = d["id"]
            mc = model_config_obj(d.get("model_config"))
            keys = sorted(mc.keys())
            key_profile = ",".join(keys)
            profile_counts[key_profile] += 1
            profile_examples.setdefault(key_profile, [])
            if len(profile_examples[key_profile]) < 5:
                profile_examples[key_profile].append(sid)
            for k in keys:
                key_counts[k] += 1

            extra_keys = sorted(k for k in keys if k not in FINAL_IMPORTER_KEYS)
            for k in extra_keys:
                legacy_extra_key_counts[k] += 1

            cid = conv_id(mc)
            prefix = utc_session_prefix(d.get("started_at"))
            expected_id = f"{prefix}_{cid[:8]}" if cid and prefix else ""
            full_id_matches = bool(expected_id) and sid == expected_id
            user_id_matches = bool(cid) and d.get("user_id") == cid
            a = msg_agg.get(sid, {})
            stored_count = int(d.get("message_count") or 0)
            actual_count = int(a.get("actual_messages") or 0)
            drift = actual_count - stored_count

            if EARLY_TEST_SIGNATURE_KEY in mc:
                early_signature_rows.append(
                    {
                        "session_id": sid,
                        "title": d.get("title"),
                        "model_config_keys": key_profile,
                        "stored_message_count": stored_count,
                        "actual_messages": actual_count,
                        "message_count_delta": drift,
                        "min_message_id": a.get("min_message_id"),
                        "max_message_id": a.get("max_message_id"),
                        "full_generated_id_matches": int(full_id_matches),
                        "user_id_matches_conv_id": int(user_id_matches),
                    }
                )

            if drift != 0:
                drift_rows.append(
                    {
                        "session_id": sid,
                        "title": d.get("title"),
                        "stored_message_count": stored_count,
                        "actual_messages": actual_count,
                        "message_count_delta": drift,
                        "min_message_id": a.get("min_message_id"),
                        "max_message_id": a.get("max_message_id"),
                        "model_config_keys": key_profile,
                        "legacy_extra_keys": ",".join(extra_keys),
                        "full_generated_id_matches": int(full_id_matches),
                    }
                )

            if not full_id_matches:
                id_mismatch_rows.append(
                    {
                        "session_id": sid,
                        "title": d.get("title"),
                        "started_at": d.get("started_at"),
                        "conversation_id": cid,
                        "expected_generated_id": expected_id,
                        "model_config_keys": key_profile,
                    }
                )

            native_fields = [
                name for name in (
                    "display_name", "parent_session_id", "end_reason", "cwd",
                    "session_key", "chat_id", "chat_type", "thread_id", "origin_json",
                )
                if d.get(name) not in (None, "")
            ]
            residual_reasons = []
            if not cid:
                residual_reasons.append("missing_conversation_id")
            if not full_id_matches:
                residual_reasons.append("generated_id_mismatch")
            if cid and not user_id_matches:
                residual_reasons.append("user_id_mismatch")
            if native_fields:
                residual_reasons.append("native_session_fields:" + ",".join(native_fields))
            if int(a.get("tool_name_rows") or 0):
                residual_reasons.append("message_tool_name")
            if int(a.get("tool_call_rows") or 0):
                residual_reasons.append("message_tool_calls")
            if residual_reasons:
                residual_rows.append(
                    {
                        "session_id": sid,
                        "title": d.get("title"),
                        "reasons": ";".join(residual_reasons),
                        "conversation_id": cid,
                        "expected_generated_id": expected_id,
                        "model_config_keys": key_profile,
                        "stored_message_count": stored_count,
                        "actual_messages": actual_count,
                        "min_message_id": a.get("min_message_id"),
                        "max_message_id": a.get("max_message_id"),
                    }
                )

            if JUNE15_CLUSTER_LO <= sid < JUNE15_CLUSTER_HI:
                june15_rows.append(
                    {
                        "session_id": sid,
                        "title": d.get("title"),
                        "model_config_keys": key_profile,
                        "legacy_extra_keys": ",".join(extra_keys),
                        "has_memory_scope_signature": int(EARLY_TEST_SIGNATURE_KEY in mc),
                        "stored_message_count": stored_count,
                        "actual_messages": actual_count,
                        "message_count_delta": drift,
                        "min_message_id": a.get("min_message_id"),
                        "max_message_id": a.get("max_message_id"),
                        "full_generated_id_matches": int(full_id_matches),
                        "user_id_matches_conv_id": int(user_id_matches),
                    }
                )

        profile_rows = []
        for profile, n in profile_counts.most_common():
            keys = [k for k in profile.split(",") if k]
            profile_rows.append(
                {
                    "model_config_keys": profile,
                    "sessions": n,
                    "legacy_extra_keys": ",".join(
                        k for k in keys if k not in FINAL_IMPORTER_KEYS
                    ),
                    "example_session_ids": ",".join(profile_examples.get(profile, [])),
                }
            )

        write_tsv(
            out / "chatgpt_model_config_key_counts.tsv",
            [{"key": k, "sessions": n} for k, n in key_counts.most_common()],
        )
        write_tsv(out / "chatgpt_model_config_profiles.tsv", profile_rows)
        write_tsv(
            out / "legacy_extra_key_counts.tsv",
            [{"key": k, "sessions": n} for k, n in legacy_extra_key_counts.most_common()],
        )
        write_tsv(out / "memory_scope_signature_sessions.tsv", early_signature_rows)
        drift_rows.sort(key=lambda x: (-abs(int(x["message_count_delta"])), x["session_id"]))
        write_tsv(out / "message_count_drift.tsv", drift_rows)
        write_tsv(out / "generated_session_id_mismatches.tsv", id_mismatch_rows)
        write_tsv(out / "collision_relabel_residual_candidates.tsv", residual_rows)
        write_tsv(out / "june15_cluster_pass_profile.tsv", june15_rows)

        platform_rows = []
        platform_shape_counts = Counter()
        for r in conn.execute(
            """
            SELECT m.id AS message_id, m.session_id, m.role, m.timestamp,
                   m.platform_message_id
            FROM messages m
            JOIN sessions s ON s.id=m.session_id
            WHERE s.source='chatgpt-export'
            ORDER BY m.id
            """
        ):
            shape = uuid_shape(r["platform_message_id"])
            platform_shape_counts[shape] += 1
            if shape != "uuid":
                platform_rows.append(
                    {
                        "message_id": r["message_id"],
                        "session_id": r["session_id"],
                        "role": r["role"],
                        "timestamp": r["timestamp"],
                        "platform_id_shape": shape,
                        "platform_message_id": r["platform_message_id"],
                    }
                )
        write_tsv(
            out / "platform_message_id_shapes.tsv",
            [{"shape": k, "message_rows": n} for k, n in platform_shape_counts.most_common()],
        )
        write_tsv(out / "non_uuid_or_empty_platform_message_ids.tsv", platform_rows)

        duplicate_platform_rows = []
        for r in conn.execute(
            """
            SELECT m.platform_message_id, COUNT(*) AS copies,
                   COUNT(DISTINCT m.session_id) AS sessions,
                   MIN(m.id) AS min_message_id, MAX(m.id) AS max_message_id
            FROM messages m
            JOIN sessions s ON s.id=m.session_id
            WHERE s.source='chatgpt-export'
              AND COALESCE(m.platform_message_id,'')<>''
            GROUP BY m.platform_message_id
            HAVING COUNT(*) > 1
            ORDER BY copies DESC, min_message_id
            """
        ):
            duplicate_platform_rows.append(dict(r))
        write_tsv(out / "duplicate_platform_message_ids.tsv", duplicate_platform_rows)

        summary = {
            "canonical_db": {
                "path": str(args.db),
                "sha256": before_sha,
                "counts": counts,
                "quick_check": quick_check,
                "foreign_key_violations": fk_violations,
                "opened_mode": "mode=ro&immutable=1 + PRAGMA query_only=ON",
                "mutations_performed": False,
            },
            "chatgpt_export_sessions": len(sessions),
            "model_config_key_counts": dict(key_counts),
            "model_config_profile_count": len(profile_counts),
            "legacy_extra_key_counts": dict(legacy_extra_key_counts),
            "memory_scope_signature_sessions": len(early_signature_rows),
            "message_count_drift_sessions": len(drift_rows),
            "generated_session_id_mismatches": len(id_mismatch_rows),
            "collision_relabel_residual_candidates": len(residual_rows),
            "june15_cluster_sessions": len(june15_rows),
            "platform_message_id_shapes": dict(platform_shape_counts),
            "non_uuid_or_empty_platform_message_ids": len(platform_rows),
            "duplicate_platform_message_ids": len(duplicate_platform_rows),
            "interpretation": {
                "memory_scope_signature": (
                    "Historical transcript proves the first 57 test imports wrote "
                    "chatgpt_memory_scope and later backfilled conversation_id. "
                    "Presence is a pass-shape fingerprint, not by itself unique proof "
                    "that a row is one of those exact 57."
                ),
                "final_importer_keys": sorted(FINAL_IMPORTER_KEYS),
                "generated_id_check": (
                    "Uses UTC started_at because the historical importer ran on the VM "
                    "whose timestamp-prefixed session IDs correspond to UTC."
                ),
                "collision_residual": (
                    "A zero residual count would strongly reduce, not logically eliminate, "
                    "the possibility that IntegrityError->UPDATE source hit a native row."
                ),
            },
        }
        (out / "manifest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out / "README.md").write_text(
            """# Issue #60 stage-4 evidence

This is a narrow pass-classification/collision audit over the canonical recovered DB.

High-value outputs:

- `chatgpt_model_config_key_counts.tsv` / `chatgpt_model_config_profiles.tsv`
  classify surviving importer generations by field shape.
- `memory_scope_signature_sessions.tsv` locates the historical early-test signature
  documented in the June-16 transcript.
- `message_count_drift.tsv` identifies sessions later appended/changed without the
  stored session-level count being refreshed.
- `generated_session_id_mismatches.tsv` checks the complete
  `UTC(started_at) + conversation_id[:8]` identity relation.
- `collision_relabel_residual_candidates.tsv` combines ID/user/native-field/tool
  fingerprints into the remaining source-relabel collision candidates.
- `non_uuid_or_empty_platform_message_ids.tsv` explains the tiny gap between total
  imported messages and non-empty UUID platform IDs.
- `duplicate_platform_message_ids.tsv` checks whether a ChatGPT message UUID appears
  more than once in the final imported corpus.
- `june15_cluster_pass_profile.tsv` revisits the 23-row June-15 cluster using pass
  fingerprints instead of treating `started_at` as insertion time.

`messages.id` remains only an insertion-order proxy.
No write/migration/rebuild/VACUUM is performed.
""",
            encoding="utf-8",
        )
    finally:
        conn.close()

    identity_after = file_identity(args.db)
    after_sha = sha256_file(args.db)
    sidecars_after = sidecar_receipt(args.db)
    if identity_after != identity_before:
        raise SystemExit(
            f"REFUSE: DB identity changed: before={identity_before} after={identity_after}"
        )
    if after_sha != before_sha:
        raise SystemExit(f"REFUSE: DB SHA changed: before={before_sha} after={after_sha}")
    dirty_after = {
        k: v for k, v in sidecars_after.items() if v["exists"] and int(v["size"]) > 0
    }
    if dirty_after:
        raise SystemExit(f"REFUSE: non-empty SQLite sidecar appeared: {dirty_after}")

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_receipt"] = {
        "before_identity": identity_before,
        "after_identity": identity_after,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "sidecars_before": sidecars_before,
        "sidecars_after": sidecars_after,
    }
    manifest["elapsed_s"] = round(time.time() - started, 3)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname=out.name)

    print(f"evidence_dir={out}")
    print(f"archive={archive}")
    print(f"sha256={after_sha}")
    print(f"counts={json.dumps(EXPECTED_COUNTS, sort_keys=True)}")
    print(f"memory_scope_signature_sessions={manifest['memory_scope_signature_sessions']}")
    print(f"message_count_drift_sessions={manifest['message_count_drift_sessions']}")
    print(f"generated_id_mismatches={manifest['generated_session_id_mismatches']}")
    print(f"collision_residual_candidates={manifest['collision_relabel_residual_candidates']}")
    print(f"non_uuid_or_empty_platform_ids={manifest['non_uuid_or_empty_platform_message_ids']}")
    print(f"duplicate_platform_message_ids={manifest['duplicate_platform_message_ids']}")
    print(f"elapsed_s={manifest['elapsed_s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
