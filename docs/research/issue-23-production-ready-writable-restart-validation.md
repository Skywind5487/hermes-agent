# Issue #23 — Production-ready `state.db` survives writes and restart (execution report)

Status: **executed 2026-08-13; frozen artifact proven writable across a real process boundary; frozen source unchanged**.

Run ID: `20260813T151013Z-23-restart-2132`
Source artifact: `/home/skywind/hermes-recovery/production-builds/20260813T083500Z-4e5ad5c22303-cjk3513/state.production-ready.4e5ad5c22303-cjk3513.db`
Target: `4e5ad5c2230300d1ffae84b089ffc70e368c8a23` (origin/dev, the #22 pin).

This is the #23 execution of the #43 runbook
(`docs/research/issue-43-writable-restart-validation.md`, `cbb8ca62`). All #23
acceptance criteria pass. The frozen #22 artifact itself was never opened
writably; every write landed on a separate disposable clone.

## 1. Source identity (pinned #22 final artifact)

```text
run_id        20260813T083500Z-4e5ad5c22303-cjk3513
target_commit 4e5ad5c2230300d1ffae84b089ffc70e368c8a23
artifact      …/production-builds/20260813T083500Z-4e5ad5c22303-cjk3513/state.production-ready.4e5ad5c22303-cjk3513.db
size          1667649536
sha256        3a3a410f62ada5e32fc2375e657248ebb4308b688da0e65f7c85c70ceaa6818f
mode          0400
sidecars      none
producer      prod-tools/conda/producer-sqlite3513 — CPython 3.12.13 + #21-pinned SQLite 3.51.3
```

The superseded `20260813T081559Z-4e5ad5c22303` / SHA `8bf88a19…03d99` was
rejected as required by the runbook.

## 2. Disposable-clone layout (all on native WSL/ext4)

```text
RUN   /home/skywind/hermes-recovery/restart-validations/20260813T151013Z-23-restart-2132
WT    $RUN/source                        detached worktree @ 4e5ad5c2… (byte-clean)
HERMES_HOME $RUN/hermes-home             isolated run home
DB    $HERMES_HOME/state.db              install -m 0600 of the frozen artifact
```

- Frozen-source pre-attestation recorded (`source.before.txt`): SHA/size/mode,
  `stat` identity, and directory listing — all matched the pinned values.
- `git worktree add --detach "$WT" 4e5ad5c2…`; HEAD verified and worktree clean.
- Clone born via `install -m 0600`; `clone.birth.sha256` equals the frozen SHA.
- CJK extension built from the pinned worktree source into
  `$HERMES_HOME/lib/libfts5_cjk.so` (`native/fts5_cjk/build.sh`).
- Producer resolved **uniquely** (1 match: `producer-sqlite3513/bin/python`,
  a symlink to `python3.12`).

## 3. Runtime gate (before opening the clone)

`:memory:` gate on the same producer passed:

```json
{"python": "3.12.13", "sqlite_version": "3.51.3",
 "sqlite_source_id": "2026-03-13 10:38:09 737ae4a3…d618",
 "wal_reset_vulnerable": false, "fts5": true, "trigram": true, "cjk_unicode61": true}
```

`is_sqlite_wal_reset_vulnerable()` = `False`; FTS5, built-in trigram, loadable
extension, and `cjk_unicode61` all probe clean in `:memory:`.

## 4. Process A — real Hermes writes + immediate search

Fresh producer interpreter, `PYTHONPATH=$WT`, `HERMES_HOME` isolated.

Unique tag: `rv20260813T151035Z-484f27`

- session id: `restart-validation-rv20260813T151035Z-484f27`
- session_key: `restart-validation:rv20260813T151035Z-484f27`
- title: `QuartzNeedle 重啟龍門 rv20260813T151035Z-484f27`
- display_name: `PortalNeedle 顯示龍門 rv20260813T151035Z-484f27`
- message: `RestartMessageNeedle rv20260813T151035Z-484f27 重啟訊息龍門` (row id 287815)

Writes went through real `SessionDB` APIs only: `create_session()`,
`record_gateway_session_peer()`, `set_session_title()`, `append_message()`.

Immediate canonical readback + all probes passed:

- `get_session()` returns exact title/display_name; `get_messages()` contains the message.
- Session-metadata lanes: Unicode title (`QuartzNeedle`), Unicode display
  (`PortalNeedle`), trigram (`Needle`), CJK title (`重啟龍門`), CJK display
  (`顯示龍門`) — all hit the new session.
- Public/router: `resolve_session_by_title(full_title) == session_id`;
  `search_sessions_by_id(TAG)` contains it; ASCII message search route `fts5`;
  CJK message search route `fts_cjk`, both return the new message.

Deltas vs baseline: `sessions +1`, `messages +1`, `gateway_routing +0`.

`write-evidence.json` + `baseline.json` saved; `db.close()`; exit 0.

## 5. Process B — fresh interpreter / restart proof

A **new** producer interpreter reopens the same clone. All passed:

- New session still has the exact title/display_name; message still present.
- All five session-metadata lanes re-hit after restart.
- `resolve_session_by_title`, `search_sessions_by_id`, ASCII (`fts5`) and CJK
  (`fts_cjk`) message searches all resolve the new data.
- Pre-existing data still readable: old session `20260429_125404_55806e` and
  message id `1` captured in baseline are both readable after restart.
- Deltas still exactly `sessions +1 / messages +1 / gateway_routing +0`.
- FTS settlement stable: `schema_version=25`, `fts_storage_version=2`, no
  active H/P/stale/optimize markers, `_classify_sessions_fts_trigram()`
  = `modern_trigram`, `_fts_storage_v2_blockers()` = `[]`.
- `PRAGMA integrity_check` → `["ok"]`; `PRAGMA foreign_key_check` → 0 rows.

`restart-evidence.json` saved; `db.close()`; exit 0.

## 6. Forbidden warnings + frozen-source post-proof

- Full stdout/stderr from the gate and both processes captured in
  `restart-validation.log` under `set -o pipefail`.
- Forbidden-signature scan (malformed database / `SQLITE_CORRUPT` / FTS corrupt
  / WAL-reset vulnerable / locking protocol / disk I/O error):
  **zero matches** (`forbidden-warnings.txt` is empty).
- Frozen-source post-attestation (`source.after.txt`) equals
  `source.before.txt` **byte-for-byte** (`cmp -s` passes): SHA `3a3a410f…6818f`,
  size `1667649536`, mode `0400`, no `-wal/-shm/-journal` sidecars.

## 6.5 Supplementary direct-MATCH coverage (all six FTS lanes)

Process A/B drove the public router, which on this CJK-capable runtime routes
message search through `fts5` (ASCII) / `fts_cjk` (CJK) and never through the
`messages_fts_trigram` fallback. A follow-up **read-only** probe
(`supplemental-coverage.json`, `SessionDB(read_only=True)` + direct `MATCH`
against every FTS table) closes that gap — all six lanes hit the new row:

```text
messages_fts           "RestartMessageNeedle"  → hit 287815
messages_fts_trigram   "RestartMessageNeedle"  → hit 287815   (router-fallback lane)
messages_fts_cjk       "重啟訊息龍門"            → hit 287815
sessions_fts           "QuartzNeedle"          → hit 7326
sessions_fts_trigram   "Needle"                → hit 7326
sessions_fts_cjk       "重啟龍門"               → hit 7326
```

The `like_scan` fallback route was also exercised via the public router with a
lone single CJK character (`"龍"`): route `like_scan`, hit. Note: on a
read-only open `_fts_cjk_available` stays `False` (read-only skips
`_probe_fts_cjk`), so `_describe_search_path` reports `trigram` for the CJK
query even though the public CJK search still returns the message
(`returned=1`); the writable Process A/B used the real `fts_cjk` route. This is
a descriptor-flag difference, not a data problem.

## 7. Evidence directory

```text
source.before.txt / source.after.txt     before/after attestation (cmp identical)
clone.birth.sha256                        clone born from exact frozen bytes
tag.txt                                   rv20260813T151035Z-484f27
run-id.txt                                run + artifact + target + producer identity
baseline.json                             baseline counts + old session/message anchors
write-evidence.json                       Process-A writes + immediate search proofs
restart-evidence.json                     Process-B persistence/search/integrity proofs
restart-validation.log                    full gate + A + B stdout/stderr
forbidden-warnings.txt                    empty (clean)
supplemental-coverage.json                read-only direct MATCH on all six FTS lanes + like_scan
```

## Acceptance criteria

- [x] Validation starts from a separate copy of the frozen #22 artifact; source SHA-256 unchanged.
- [x] Same pinned `TARGET_COMMIT` `4e5ad5c2…` and producer Python/SQLite/FTS capabilities as #22.
- [x] New session + persisted message written to the disposable clone.
- [x] Session-title and message search reflect the validation writes per the pinned FTS contract.
- [x] Clone cleanly closed (Process A) and reopened by a fresh interpreter (Process B) without loss.
- [x] Post-restart search resolves validation data; old recovered data still readable.
- [x] `PRAGMA integrity_check` = `ok`; `foreign_key_check` = 0 rows.
- [x] No malformed-database / FTS-corruption / vulnerable-SQLite warnings in the captured log.
- [x] Frozen production-ready artifact untouched and transfer-ready after validation.

## Follow-ups

- #23 closes; cutover/promotion of the frozen artifact to the production target
  is the remaining rollout step (owned per #20/#13 roadmap).
- If later evidence invalidates the clone or the artifact, quarantine the run
  directory and rebuild from the frozen canonical master — never resume on the
  frozen source.
