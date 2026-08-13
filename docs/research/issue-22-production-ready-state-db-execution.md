# Issue #22 — Production-ready `state.db` build and freeze (execution report)

Status: **executed 2026-08-13; artifact frozen**.

Run ID: `20260813T081559Z-4e5ad5c22303`
Target: `4e5ad5c2230300d1ffae84b089ffc70e368c8a23` (origin/dev), accepted core `276d4977…` ancestor (5 ahead).

## Phase 0 preflight

- **P0-A** — native WSL fork checkout: `Skywind5487/hermes-agent` cloned to
  `/home/skywind/hermes-recovery/production-builds/repo` (ext4), detached worktree
  pinned to the target. `~/.hermes/hermes-agent` (upstream install) untouched.
- **P0-B** — safe producer runtime: no pre-existing WSL runtime passed the WAL-reset
  gate (system 3.46.1, uv 3.50.4). Provisioned a conda-forge CPython `3.12.13`
  producer linking **SQLite 3.53.4** (`prod-tools/conda/producer`); passes target
  `is_sqlite_wal_reset_vulnerable`, FTS5, trigram, and loadable-extension probes.
  This reuses the "use an available safe runtime" rule instead of the #21 private
  3.51.3 source build; exact identity is in the receipt.
- Frozen canonical master re-attested before/after (size `1675415552`, SHA-256
  `23cfa3c8…48104`, mode `0400`, no sidecars) — unchanged.

## Build

- Fresh writable copy of the master in a dedicated `HERMES_HOME` (`state.db`);
  byte-identical at birth; pre-build counts/integrity/FK verified.
- `hermes sessions optimize-storage --yes` at the pinned target: rebuilt FTS,
  reclaimed legacy index, default VACUUM succeeded — `1597.8 MB -> 1475.9 MB`
  (reclaimed 121.9 MB). Log: `…/optimize-storage.log`.

## Verification (all pass)

- `schema_version=25`, `fts_storage_version=2`; no H/P/stale/optimize markers;
  no `fts_v22_trash_%`; `_fts_storage_v2_blockers()` = `[]`.
- `_classify_sessions_fts_trigram(...)` = `modern_trigram` (schema identity).
- FTS5 external-content `integrity-check`: ok on `messages_fts`,
  `messages_fts_trigram`, `sessions_fts`, `sessions_fts_trigram`.
- MATCH probes pass for title / id / display_name on both `sessions_fts` and
  `sessions_fts_trigram` (trigram via `compact_session_metadata_text()`).
- CJK: valid absence (producer lacks CJK tokenizer; zero blockers).
- Canonical counts intact: `sessions=7268`, `messages=231513`, `gateway_routing=78`;
  `PRAGMA integrity_check` ok; `foreign_key_check` 0.

## Frozen artifact

```text
path   /home/skywind/hermes-recovery/production-builds/20260813T081559Z-4e5ad5c22303/state.production-ready.4e5ad5c22303.db
size   1547636736
sha256 8bf88a19d64bc42e9dda0236d4a6ed92c40e7c0952dac3d78941fe1755a03d99
mode   0400
```

Full receipt (all #81 §7 fields): `…/20260813T081559Z-4e5ad5c22303/RECEIPT.md`.

## Follow-ups

- #23 owns cutover/promotion of the frozen artifact.
- If later evidence invalidates the artifact, quarantine it and rebuild from the
  frozen canonical master (never resume migration on the frozen artifact).
- #81 (research note) remains open for the #42 record.
