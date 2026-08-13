# Issue #22 — Production-ready `state.db` build and freeze (execution report)

Status: **executed 2026-08-13; final artifact frozen (SQLite 3.51.3 + CJK)**.

Run ID: `20260813T083500Z-4e5ad5c22303-cjk3513`
Target: `4e5ad5c2230300d1ffae84b089ffc70e368c8a23` (origin/dev), accepted core `276d4977…` ancestor (5 ahead).

Supersedes the first attempt (`20260813T081559Z-4e5ad5c22303`, SQLite 3.53.4,
no CJK) per review: use the #21-pinned SQLite 3.51.3, build CJK from the script,
and fuzz-test every path.

## Phase 0 preflight

- **P0-A** — native WSL fork checkout: `Skywind5487/hermes-agent` cloned to
  `/home/skywind/hermes-recovery/production-builds/repo` (ext4), detached worktree
  pinned to the target. `~/.hermes/hermes-agent` (upstream install) untouched.
- **P0-B** — safe producer runtime on the **#21-pinned SQLite 3.51.3**: no
  pre-existing WSL runtime passed the WAL-reset gate (system 3.46.1, uv 3.50.4).
  Built `sqlite-autoconf-3510300` privately (SHA3-256 `581215771b32ea4c4062e6fb
  9842c4aa43d0a7fb2b6670ff6fa4ebb807781204` verified) and linked it into a
  conda-forge CPython `3.12.13` (`prod-tools/conda/producer-sqlite3513`; env
  `libsqlite3.so.0` swapped, original backed up). Passes target
  `is_sqlite_wal_reset_vulnerable`, FTS5, trigram, and loadable-extension probes.
- **CJK tokenizer** — built `native/fts5_cjk/build.sh` → `libfts5_cjk.so`,
  installed to the isolated `$HERMES_HOME/lib/`; load + `cjk_unicode61` MATCH
  verified in `:memory:` before the build.
- Frozen canonical master re-attested before/after (size `1675415552`, SHA-256
  `23cfa3c8…48104`, mode `0400`, no sidecars) — unchanged.

## Build

- Fresh writable copy of the master in a dedicated `HERMES_HOME` (`state.db`);
  byte-identical at birth; pre-build counts/integrity/FK verified.
- `hermes sessions optimize-storage --yes` at the pinned target: rebuilt FTS,
  reclaimed legacy index, default VACUUM succeeded — `1597.8 MB -> 1590.4 MB`
  (CJK indexes add back size vs the no-CJK build). Log: `…/optimize-storage.log`.

## Verification (all pass)

- `schema_version=25`, `fts_storage_version=2`; no H/P/stale/optimize markers;
  no `fts_v22_trash_%`; `_fts_storage_v2_blockers()` = `[]`.
- `_classify_sessions_fts_trigram(...)` = `modern_trigram` (schema identity).
- Six-index surface: **`messages_fts`, `messages_fts_trigram`,
  `messages_fts_cjk`, `sessions_fts`, `sessions_fts_trigram`, `sessions_fts_cjk`
  all present**; FTS5 external-content `integrity-check` ok on all six.
- MATCH probes pass for title / id / display_name on `sessions_fts`,
  `sessions_fts_trigram` (via `compact_session_metadata_text()`), and
  `sessions_fts_cjk`.
- Canonical counts intact: `sessions=7268`, `messages=231513`, `gateway_routing=78`;
  `PRAGMA integrity_check` ok; `foreign_key_check` 0.

## Fuzz / recall testing (every path)

Harness `fuzz_all_paths.py` (seed `20260813`), all six indexes, on the candidate:

- Recall: every sampled real row findable by its own terms — no misses.
- Fuzz MATCH: 15,000 randomized queries (2,500 × 6) — zero crashes; only graceful
  SQLite parse errors.
- Stability post-fuzz: known-good recall still hits.
- Contracts: trigram substring 200/200; CJK bigram `sessions_fts_cjk` 294/294;
  `messages_fts_cjk` (non-tool) 299/299; unicode61 punct-word 298/300.
- Verifiers 4.1 + 4.2 re-run after fuzzing: PASS (no corruption).

## Frozen artifact

```text
path   /home/skywind/hermes-recovery/production-builds/20260813T083500Z-4e5ad5c22303-cjk3513/state.production-ready.4e5ad5c22303-cjk3513.db
size   1667649536
sha256 3a3a410f62ada5e32fc2375e657248ebb4308b688da0e65f7c85c70ceaa6818f
mode   0400
```

Full receipt (all #81 §7 fields): `…/20260813T083500Z-4e5ad5c22303-cjk3513/RECEIPT.md`.

## Follow-ups

- #23 owns cutover/promotion of the frozen artifact.
- If later evidence invalidates the artifact, quarantine it and rebuild from the
  frozen canonical master (never resume migration on the frozen artifact).
- #81 (research note) remains open for the #42 record.
