# Issue #43 — Writable restart validation plan for #23

Status: **research complete; execution belongs to #23**. This note does **not** mutate the frozen #22 artifact and does **not** perform #23 writes.

## 1. Source of truth

Issue #43 asks for the exact disposable-clone write/restart surface to hand to #23 after #22 freezes a production-ready database. The final #22 execution report is the authoritative artifact record; its first SQLite 3.53.4/no-CJK candidate was superseded and must not be selected.

Final #22 artifact:

```text
run_id        20260813T083500Z-4e5ad5c22303-cjk3513
target_commit 4e5ad5c2230300d1ffae84b089ffc70e368c8a23
path          /home/skywind/hermes-recovery/production-builds/20260813T083500Z-4e5ad5c22303-cjk3513/state.production-ready.4e5ad5c22303-cjk3513.db
size          1667649536
sha256        3a3a410f62ada5e32fc2375e657248ebb4308b688da0e65f7c85c70ceaa6818f
mode          0400
sidecars      none
schema        schema_version=25, fts_storage_version=2
indexes       messages_fts, messages_fts_trigram, messages_fts_cjk,
              sessions_fts, sessions_fts_trigram, sessions_fts_cjk
producer      CPython 3.12.13 + #21-pinned SQLite 3.51.3
```

Do **not** use the superseded run `20260813T081559Z-4e5ad5c22303` / artifact SHA-256 `8bf88a19d64bc42e9dda0236d4a6ed92c40e7c0952dac3d78941fe1755a03d99`; #22 explicitly replaced it because it used SQLite 3.53.4 and did not build the CJK surfaces.

Primary sources:

- [#43](https://github.com/Skywind5487/hermes-agent/issues/43) — research mission and required handoff.
- [#23](https://github.com/Skywind5487/hermes-agent/issues/23) — execution acceptance contract.
- [#22](https://github.com/Skywind5487/hermes-agent/issues/22) and [its final execution report](./issue-22-production-ready-state-db-execution.md) — final artifact identity, producer, indexes, integrity/path proof.
- [`hermes_state.py` at the pinned target](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state.py) — `SessionDB`, DB selection, write APIs, session metadata lanes, title resolution, runtime WAL-reset gate.
- [`hermes_state_search.py` at the pinned target](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_search.py) — message search router / APIs.
- [`native/fts5_cjk/build.sh` at the pinned target](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/native/fts5_cjk/build.sh) — builds `libfts5_cjk.so` and accepts an explicit install directory.

## 2. Why the seam must be two processes

A same-process `close()` / reopen test is weaker than the #23 claim. It can accidentally retain process-local state, loaded extension state, or assumptions attached to the first interpreter. The validation should therefore cross a real **process boundary**:

1. **Process A** opens a writable disposable clone, performs real Hermes writes, proves immediate search visibility, closes, and exits.
2. **Process B** is a fresh Python interpreter. It opens the same clone from the same isolated `HERMES_HOME`, proves persistence and search again, reads pre-existing data, runs integrity/FK checks, closes, and exits.

This tests the durable SQLite/FTS state rather than merely the lifetime of one `SessionDB` object.

## 3. Exact isolated layout

Use a native-WSL/ext4 run directory, never the frozen source directory itself:

```bash
set -euo pipefail

PROD_ROOT=/home/skywind/hermes-recovery/production-builds
ARTIFACT_RUN="$PROD_ROOT/20260813T083500Z-4e5ad5c22303-cjk3513"
ARTIFACT="$ARTIFACT_RUN/state.production-ready.4e5ad5c22303-cjk3513.db"
ARTIFACT_SHA=3a3a410f62ada5e32fc2375e657248ebb4308b688da0e65f7c85c70ceaa6818f
ARTIFACT_SIZE=1667649536
TARGET_COMMIT=4e5ad5c2230300d1ffae84b089ffc70e368c8a23
REPO="$PROD_ROOT/repo"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-43-restart-$RANDOM"
RUN="/home/skywind/hermes-recovery/restart-validations/$RUN_ID"
WT="$RUN/source"
export HERMES_HOME="$RUN/hermes-home"
DB="$HERMES_HOME/state.db"
LOG="$RUN/restart-validation.log"
mkdir -p "$RUN" "$HERMES_HOME"
```

### 3.1 Freeze-source attestation before touching the clone

```bash
actual_sha="$(sha256sum "$ARTIFACT" | awk '{print $1}')"
actual_size="$(stat -c '%s' "$ARTIFACT")"
actual_mode="$(stat -c '%a' "$ARTIFACT")"
[[ "$actual_sha" == "$ARTIFACT_SHA" ]]
[[ "$actual_size" == "$ARTIFACT_SIZE" ]]
[[ "$actual_mode" == 400 ]]
for sidecar in "$ARTIFACT-wal" "$ARTIFACT-shm" "$ARTIFACT-journal"; do
  [[ ! -e "$sidecar" ]]
done
{
  sha256sum "$ARTIFACT"
  stat -c 'size=%s mode=%a inode=%i mtime=%Y path=%n' "$ARTIFACT"
  ls -la "$ARTIFACT_RUN"
} > "$RUN/source.before.txt"
```

If any assertion fails, **STOP**. Do not “repair” or normalize the frozen source.

### 3.2 Pin source code and create the writable clone

```bash
git -C "$REPO" worktree add --detach "$WT" "$TARGET_COMMIT"
[[ "$(git -C "$WT" rev-parse HEAD)" == "$TARGET_COMMIT" ]]
[[ -z "$(git -C "$WT" status --porcelain)" ]]

install -m 0600 "$ARTIFACT" "$DB"
[[ "$(sha256sum "$DB" | awk '{print $1}')" == "$ARTIFACT_SHA" ]]
sha256sum "$DB" > "$RUN/clone.birth.sha256"
```

`install` reads the immutable source and creates a separate writable file. Every subsequent Hermes operation must use `HERMES_HOME=$RUN/hermes-home`; the frozen source path is never passed to `SessionDB`.

## 4. Runtime gate — reuse #22's SQLite 3.51.3 producer, never substitute

#22's final receipt records the producer environment as `prod-tools/conda/producer-sqlite3513`. Resolve that retained environment strictly; **0 or >1 matches is a stop condition**, not permission to use `/usr/bin/python`, uv, or the earlier 3.53.4 producer.

```bash
mapfile -t PY_CANDIDATES < <(
  find "$PROD_ROOT" -type f \
    -path '*/prod-tools/conda/producer-sqlite3513/bin/python' -print
)
[[ ${#PY_CANDIDATES[@]} -eq 1 ]]
PY="${PY_CANDIDATES[0]}"
```

Build the pinned CJK extension into this run's own home. The target's build script explicitly accepts the destination directory:

```bash
mkdir -p "$HERMES_HOME/lib"
bash "$WT/native/fts5_cjk/build.sh" "$HERMES_HOME/lib" \
  2>&1 | tee -a "$LOG"
[[ -r "$HERMES_HOME/lib/libfts5_cjk.so" ]]
```

Before opening the copied production DB, gate the interpreter entirely against `:memory:`:

```bash
PYTHONPATH="$WT" "$PY" - <<'PY' 2>&1 | tee -a "$LOG"
import json, sqlite3
from pathlib import Path
from hermes_state import is_sqlite_wal_reset_vulnerable

assert tuple(__import__('sys').version_info[:3]) == (3, 12, 13)
assert sqlite3.sqlite_version == '3.51.3'
assert is_sqlite_wal_reset_vulnerable() is False

c = sqlite3.connect(':memory:')
opts = {r[0] for r in c.execute('pragma compile_options')}
assert 'ENABLE_FTS5' in opts
c.execute("create virtual table tri using fts5(x, tokenize='trigram')")
c.execute("insert into tri values ('abcdef')")
assert c.execute("select count(*) from tri where tri match 'cde'").fetchone()[0] == 1
c.enable_load_extension(True)
c.load_extension(str(Path(__import__('os').environ['HERMES_HOME']) / 'lib/libfts5_cjk.so'))
c.execute("create virtual table cj using fts5(x, tokenize='cjk_unicode61')")
c.execute("insert into cj values ('重啟龍門')")
assert c.execute("select count(*) from cj where cj match '重啟'").fetchone()[0] == 1
print(json.dumps({
    'python': __import__('sys').version,
    'sqlite_version': sqlite3.sqlite_version,
    'sqlite_source_id': c.execute('select sqlite_source_id()').fetchone()[0],
    'wal_reset_vulnerable': False,
    'fts5': True,
    'trigram': True,
    'cjk_unicode61': True,
}, ensure_ascii=False))
PY
```

Expected: CPython `3.12.13`, SQLite `3.51.3`, `wal_reset_vulnerable=false`, and all FTS5/trigram/CJK probes pass. A different SQLite identity or failed capability probe is a **STOP**.

## 5. Scenario: Process A — real Hermes writes + immediate search

Use the pinned `SessionDB` APIs, not hand-written INSERTs. This exercises the same write transactions and FTS triggers that production depends on:

- `create_session(...)` for the canonical session row;
- `record_gateway_session_peer(...)` to update `display_name` through Hermes' metadata write path;
- `set_session_title(...)` for title mutation / trigger maintenance;
- `append_message(...)` for the canonical message write / message-count path.

Generate a unique tag once and save it so Process B can consume it:

```bash
TAG="rv$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
printf '%s\n' "$TAG" > "$RUN/tag.txt"
```

Process A should:

1. Open `SessionDB()` with `HERMES_HOME` pointing at the disposable clone.
2. Record baseline counts (`sessions`, `messages`, `gateway_routing`) plus one pre-existing session and message anchor for the restart readback.
3. Create the following data (with `$TAG` substituted):

```text
session id    restart-validation-$TAG
session_key   restart-validation:$TAG
title         QuartzNeedle 重啟龍門 $TAG
display_name  PortalNeedle 顯示龍門 $TAG
message       RestartMessageNeedle $TAG 重啟訊息龍門
```

4. Assert canonical readback with `get_session()` and `get_messages()`.
5. Assert **session metadata search through every intended lane**:
   - Unicode lane: `_fts_metadata_candidates("QuartzNeedle")` returns the new session.
   - Unicode/display-name lane: `_fts_metadata_candidates("PortalNeedle")` returns it.
   - normalized trigram lane: `_fts_session_trigram_candidates("Needle")` returns it (`Needle` is >=3 characters, so the trigram lower bound is satisfied).
   - CJK bigram lane: `_fts_cjk_metadata_candidates("重啟龍門")` is servable and returns it.
   - CJK display-name lane: `_fts_cjk_metadata_candidates("顯示龍門")` is servable and returns it.
6. Assert the public/session-facing search surfaces:
   - `resolve_session_by_title(full_title) == session_id`;
   - `search_sessions_by_id($TAG)` contains the new session.
7. Assert message search through both production-relevant routes:
   - `search_messages("RestartMessageNeedle", ...)` contains the new session/message;
   - `search_messages("重啟訊息龍門", ...)` contains the new session/message.
8. Save `baseline.json` and `write-evidence.json`, call `db.close()`, and exit the interpreter with status 0.

The private lane calls above are intentional validation probes: #22 already used these exact low-level session lanes to prove each index can execute. Pairing them with the public title/id/message searches catches both **index maintenance** and **router/API** regressions.

### Expected Process-A deltas

Relative to the captured baseline:

```text
sessions        +1
messages        +1
gateway_routing +0
```

`record_gateway_session_peer()` mutates the `sessions` metadata row; it does not create a `gateway_routing` record. Do not hard-code the historical 7268/231513/78 values as the oracle when a baseline delta is stronger and self-contained.

## 6. Scenario: Process B — fresh interpreter / restart proof

Only after Process A exits successfully, launch a **new** `$PY` process with the same `PYTHONPATH=$WT` and `HERMES_HOME=$RUN/hermes-home`.

Process B must:

1. Open a new `SessionDB()` instance.
2. Load `$RUN/tag.txt` / `baseline.json`.
3. Prove the newly written session still has the exact `title` and `display_name` and the new message still exists.
4. Re-run all five session-metadata probes (Unicode title, Unicode display name, trigram, CJK title, CJK display name).
5. Re-run `resolve_session_by_title`, `search_sessions_by_id`, ASCII message search, and CJK message search.
6. Prove one **pre-existing** session and one **pre-existing** message captured before Process A are still readable from the clone.
7. Assert the baseline deltas are still exactly `sessions +1`, `messages +1`, `gateway_routing +0`.
8. Assert FTS settlement remains stable (`schema_version=25`, `fts_storage_version=2`; no rebuild/stale/optimize marker unexpectedly appears).
9. Run:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

`integrity_check` must yield exactly `ok`; `foreign_key_check` must yield zero rows.
10. Save `restart-evidence.json`, call `db.close()`, exit 0.

This is the actual restart seam: Process B must succeed without relying on any object, connection, extension registration, or in-memory flag owned by Process A.

## 7. Log capture and forbidden warnings

Run both processes under `set -o pipefail` and append stdout/stderr to one log:

```bash
... 2>&1 | tee -a "$LOG"
```

After Process B, fail the validation if the log contains any corruption/runtime-safety signature, including the pinned runtime's own WAL-reset warning text:

```bash
FORBIDDEN='database disk image is malformed|malformed database schema|SQLITE_CORRUPT|FTS[^[:cntrl:]]*corrupt|corrupt[^[:cntrl:]]*FTS|vulnerable to the WAL-reset corruption bug|locking protocol|disk I/O error'
if grep -Ein "$FORBIDDEN" "$LOG" > "$RUN/forbidden-warnings.txt"; then
  echo 'STOP: forbidden warning found' >&2
  exit 1
fi
: > "$RUN/forbidden-warnings.txt"
```

The exact runtime warning string is owned by `hermes_state.is_sqlite_wal_reset_vulnerable` / WAL fallback handling at the target, so this catches accidental execution under a vulnerable SQLite even if the preflight gate were somehow bypassed.

## 8. Re-attest the frozen source after all writes/restart checks

The most important negative proof is that every mutation landed only on the clone:

```bash
{
  sha256sum "$ARTIFACT"
  stat -c 'size=%s mode=%a inode=%i mtime=%Y path=%n' "$ARTIFACT"
  ls -la "$ARTIFACT_RUN"
} > "$RUN/source.after.txt"

[[ "$(sha256sum "$ARTIFACT" | awk '{print $1}')" == "$ARTIFACT_SHA" ]]
[[ "$(stat -c '%s' "$ARTIFACT")" == "$ARTIFACT_SIZE" ]]
[[ "$(stat -c '%a' "$ARTIFACT")" == 400 ]]
for sidecar in "$ARTIFACT-wal" "$ARTIFACT-shm" "$ARTIFACT-journal"; do
  [[ ! -e "$sidecar" ]]
done
cmp -s "$RUN/source.before.txt" "$RUN/source.after.txt"
```

The `cmp` is deliberately strict: hash, stat identity/mtime, mode, and directory listing should all remain unchanged. If it fails, #23 fails even if the disposable clone is healthy.

## 9. Hard stop rules

Abort #23 immediately and preserve the run directory as evidence if **any** of these occurs:

1. The selected source is not the final `...cjk3513.db` artifact above, or its SHA/size/mode/sidecar state differs.
2. The worktree is not exactly `4e5ad5c2230300d1ffae84b089ffc70e368c8a23` or is dirty before the test.
3. The retained `producer-sqlite3513` cannot be resolved uniquely; Python is not 3.12.13; SQLite is not 3.51.3; the WAL-reset classifier says vulnerable; FTS5/trigram/load-extension/CJK probes fail.
4. The clone's birth hash does not equal the source artifact hash.
5. Any Hermes write/read/search assertion fails before restart.
6. Process A does not close and exit 0 before Process B begins.
7. Any post-restart persistence/search/pre-existing-read assertion fails.
8. Count deltas are not exactly `+1/+1/+0`.
9. `PRAGMA integrity_check` is not exactly `ok` or `foreign_key_check` returns a row.
10. Any forbidden corruption/WAL-runtime signature appears in the captured log.
11. The frozen source's final attestation differs from its initial attestation or any source sidecar appears.

On failure: **do not repair the source artifact, do not resume against it, and do not proceed to cutover/promotion**. Quarantine/preserve the disposable run directory and investigate from its evidence.

## 10. Evidence checklist for #23 completion

A successful run should leave one directory containing at least:

```text
source.before.txt                 frozen-source pre-attestation
source.after.txt                  frozen-source post-attestation (byte-for-byte cmp equal)
clone.birth.sha256                clone born from exact frozen bytes
tag.txt                           unique validation identity
runtime.json / runtime log line   Python/SQLite/source-id/capability gate
baseline.json                     baseline counts + old session/message anchors
write-evidence.json               Process-A writes + immediate search proofs
restart-evidence.json             Process-B persistence/search/integrity proofs
restart-validation.log            full stdout/stderr across both processes
forbidden-warnings.txt            empty on success
```

The #23 completion comment should report the run directory, exact source artifact + SHA, TARGET_COMMIT, producer identity, unique tag/session/message IDs, before/after count deltas, lane/API results, integrity/FK result, forbidden-warning result, and the frozen-source before/after attestation result.

## 11. Handoff conclusion

#22 is now frozen and the blocker that originally held #43 is gone. The execution surface is therefore ready for #23 **provided #23 treats this note as a runbook, keeps every write inside the disposable `HERMES_HOME`, and honors the hard stop rules above**.

The key invariant is simple: **prove a production-identical clone can accept real Hermes writes, lose the entire Python process, reopen cleanly with the same pinned runtime, and still search both new and old data — while the frozen source remains provably untouched.**
