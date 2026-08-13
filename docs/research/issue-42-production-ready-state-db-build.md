# Issue #42 — production-ready `state.db` build surface for #22

Status: research complete; execution belongs to #22.

This note freezes the exact source revision, build seam, safety boundary, completion evidence, and retry rules for producing the first production-ready database from the recovered patched-canonical master. It intentionally does **not** perform the production-scale build.

## Decision summary

- **Pinned target:** `4e5ad5c2230300d1ffae84b089ffc70e368c8a23` (`dev` when this research was finalized).
- **#12 gate:** clear. Final #79 closed after the remaining recovery-marker audit, and the pinned target is a descendant of the accepted six-index/storage-v2 core at `276d497764feb7d4a71f1424ed44b8958da63b16`.
- **Canonical input:** `/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db`
- **Canonical SHA-256:** `23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104`
- **Canonical size:** `1,675,415,552` bytes.
- **Canonical row counts:** `sessions=7,268`, `messages=231,513`, `gateway_routing=78`.
- **Build seam:** `hermes sessions optimize-storage --yes`, executed from the pinned checkout with an **isolated `HERMES_HOME` whose `state.db` is a fresh writable copy of the canonical master**.
- **Default production path keeps VACUUM enabled.** `--no-vacuum` is a deviation, not the default recipe.
- **Success is not the CLI exit alone.** Acceptance requires the pinned target's shared storage-v2 evaluator to report zero blockers, `fts_storage_version=2`, settled rebuild/stale markers, six-index schema/capability semantics, canonical counts, integrity/FK checks, and a frozen output receipt.
- **Never open the frozen master with writable Hermes/SQLite.** Never run schema/FTS migration, `VACUUM`, manual marker edits, or build probes directly against it. Every attempt starts from a fresh copy.

If `dev` advances after this note, #22 must continue using the pinned SHA above unless the new revision is explicitly re-reviewed and re-pinned. Do not silently float the target.

## Why this command actually builds the database

`hermes sessions optimize-storage` is not a mere open/probe command.

CLI definition:

- [`hermes_cli/main.py` at the pinned target](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/main.py#L12154-L12190) defines `sessions optimize-storage`, `--yes`, and `--no-vacuum`.
- [`hermes_cli/sessions_cmd.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/sessions_cmd.py#L1032-L1135) constructs `SessionDB()` and calls `db.optimize_fts_storage(...)`, with disk-space preflight, progress output, and resumable error handling.
- [`SessionSearchMixin.optimize_fts_storage()`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_search.py#L1460-L1685) repairs bookkeeping, drains every pending rebuild lane, removes legacy trash, refuses incomplete storage-v2 state, VACUUMs by default, checkpoints WAL best-effort, re-checks completeness inside the final write transaction, then stamps storage layout v2.

The foreground rebuild lanes are declared in [`hermes_state_search.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_search.py#L150-L315):

1. `messages` → `messages_fts` + `messages_fts_trigram`
2. `messages_cjk` → `messages_fts_cjk`
3. `sessions` → `sessions_fts`
4. `sessions_trigram` → `sessions_fts_trigram`
5. `sessions_cjk` → `sessions_fts_cjk`

That is five rebuild lanes covering the six modern FTS indexes.

A plain `SessionDB()` open is therefore **not** the production build contract. It can reconcile/seed schema state, but #22 must drive `optimize-storage` to foreground completion and then independently verify settlement.

### Important CLI path fact

`optimize-storage` has no `--db` option. It uses `SessionDB()`.

[`hermes_state.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state.py#L490-L525) resolves the default DB at call time from `get_hermes_home() / "state.db"`; [`hermes_constants.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_constants.py#L84-L145) honors the `HERMES_HOME` environment override. Therefore isolation is performed by creating a dedicated build home and placing the candidate at `$HERMES_HOME/state.db`, not by attempting to pass a database path to the CLI.

## Pinned target and ancestry gate

The production executor should record these exact values before touching a candidate:

```bash
set -Eeuo pipefail

TARGET_COMMIT=4e5ad5c2230300d1ffae84b089ffc70e368c8a23
ACCEPTED_CORE=276d497764feb7d4a71f1424ed44b8958da63b16
MASTER=/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db
MASTER_SHA=23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104
MASTER_SIZE=1675415552

: "${REPO:?export REPO=/absolute/path/to/Skywind5487/hermes-agent}"

git -C "$REPO" cat-file -e "${TARGET_COMMIT}^{commit}"
test "$(git -C "$REPO" rev-parse "$TARGET_COMMIT")" = "$TARGET_COMMIT"
git -C "$REPO" merge-base --is-ancestor "$ACCEPTED_CORE" "$TARGET_COMMIT"
```

If the target object is absent locally, fetching that exact repository/ref is allowed **before** the build. After the object exists, the build worktree is detached at the pinned SHA; do not substitute current `dev`.

## Frozen-master gate

Do not open the canonical master in SQLite during this gate. Verify it as a file only:

```bash
test -f "$MASTER"
test "$(stat -c %s "$MASTER")" -eq "$MASTER_SIZE"
test "$(sha256sum "$MASTER" | awk '{print $1}')" = "$MASTER_SHA"
test "$(stat -c %a "$MASTER")" = 400

test ! -e "${MASTER}-wal"
test ! -e "${MASTER}-shm"
test ! -e "${MASTER}-journal"
```

Any mismatch is a **hard stop**. Do not chmod, checkpoint, VACUUM, repair, or otherwise “fix” the master in place. Resolve the provenance problem under #20 first.

## Isolated source and build home

Create one attempt directory. A failed/rejected attempt is never reused as the source of a later attempt.

```bash
TARGET_SHORT=${TARGET_COMMIT:0:12}
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${TARGET_SHORT}"
BUILD_ROOT="/home/skywind/hermes-recovery/production-builds/${RUN_ID}"
SOURCE="$BUILD_ROOT/source"
BUILD_HOME="$BUILD_ROOT/hermes-home"
WORK_DB="$BUILD_HOME/state.db"

mkdir -p "$BUILD_ROOT" "$BUILD_HOME"
git -C "$REPO" worktree add --detach "$SOURCE" "$TARGET_COMMIT"

test "$(git -C "$SOURCE" rev-parse HEAD)" = "$TARGET_COMMIT"
test -z "$(git -C "$SOURCE" status --porcelain)"

cp --reflink=auto -- "$MASTER" "$WORK_DB"
chmod 0600 "$WORK_DB"

test "$(stat -c %s "$WORK_DB")" -eq "$MASTER_SIZE"
test "$(sha256sum "$WORK_DB" | awk '{print $1}')" = "$MASTER_SHA"

export HERMES_HOME="$BUILD_HOME"
```

The candidate starts byte-identical to the master but is writable. The source master remains read-only and untouched.

## Producer runtime gate

The project requires Python `>=3.11`. Resolve the pinned checkout first, then record the **actual Python-linked SQLite runtime** used by Hermes; the system `sqlite3` executable is not sufficient evidence.

```bash
cd "$SOURCE"
uv sync --frozen

PY="$SOURCE/.venv/bin/python"
HERMES="$SOURCE/.venv/bin/hermes"

test -x "$PY"
test -x "$HERMES"
```

Run this hard gate before opening the candidate with Hermes:

```bash
"$PY" - <<'PY'
import json
import sqlite3
import sys
from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable

conn = sqlite3.connect(":memory:")
try:
    source_id = conn.execute("SELECT sqlite_source_id()").fetchone()[0]
    conn.execute("CREATE VIRTUAL TABLE fts5_probe USING fts5(x)")
    conn.execute("CREATE VIRTUAL TABLE trigram_probe USING fts5(x, tokenize='trigram')")
finally:
    conn.close()

vulnerable = is_sqlite_wal_reset_vulnerable(sqlite3.sqlite_version_info)
record = {
    "python_executable": sys.executable,
    "python_version": list(sys.version_info[:3]),
    "sqlite_version": sqlite3.sqlite_version,
    "sqlite_source_id": source_id,
    "wal_reset_vulnerable": vulnerable,
    "fts5": True,
    "trigram": True,
}
print(json.dumps(record, indent=2, sort_keys=True))
if vulnerable:
    raise SystemExit("STOP: selected Python is linked to a WAL-reset-vulnerable SQLite")
PY
```

The target's WAL-reset classifier treats SQLite `>=3.51.3` as safe and also recognizes the known safe backport windows; use the target helper instead of re-implementing the version rule. See [`hermes_cli/sqlite_runtime.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/sqlite_runtime.py).

If FTS5/trigram creation fails or the helper reports a vulnerable runtime, stop **before** the build and select a safe Python runtime. Do not “accept” a DB produced by a known-vulnerable runtime.

CJK is optional-capability work: the loadable `cjk_unicode61` tokenizer may or may not be available on the producer. Do not fake the capability with markers. The target's schema/settlement logic determines whether CJK work is actionable on that host; the final receipt must record the observed CJK schema/availability outcome.

## Pre-build candidate checkpoint

Before the first writable Hermes open, preserve the canonical invariants on the fresh copy:

```bash
WORK_DB="$WORK_DB" "$PY" - <<'PY'
import os
import sqlite3
from pathlib import Path

path = Path(os.environ["WORK_DB"])
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    expected = {"sessions": 7268, "messages": 231513, "gateway_routing": 78}
    for table, want in expected.items():
        got = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}={got}")
        if got != want:
            raise SystemExit(f"STOP: {table} count {got} != {want}")
    integrity = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise SystemExit(f"STOP: integrity_check={integrity!r}")
    fk = list(conn.execute("PRAGMA foreign_key_check"))
    if fk:
        raise SystemExit(f"STOP: foreign_key_check returned {len(fk)} row(s)")
finally:
    conn.close()
PY
```

These checks are against the writable candidate, never the frozen master.

## Production build command

```bash
set -o pipefail
HERMES_HOME="$BUILD_HOME" \
  "$HERMES" sessions optimize-storage --yes \
  2>&1 | tee "$BUILD_ROOT/optimize-storage.log"
```

Keep the default VACUUM. The CLI has its own disk-space preflight; insufficient space is a hard stop before migration. `--no-vacuum` may be used only as an explicitly reviewed deviation with the receipt noting that the physical-space reclaim step was omitted.

Expected progress surfaces include `[backfill]`, `[teardown]`, `[vacuum]`, and `[done]`. Rebuild work is resumable on the same candidate after a normal interruption.

### Do not treat these as success

- A plain `SessionDB()` open.
- CLI output saying `Already compact; nothing to do.`
- `fts_storage_version=2` by itself.
- Six table names by themselves.
- A table named `sessions_fts_trigram` without proving its DDL identity.
- A manually edited `state_meta` row.

The command can legitimately find no actionable work; final acceptance still comes from the verifier below.

## Authoritative completion gate

Run this verifier with the **same pinned target runtime** and the candidate still at `$BUILD_HOME/state.db`:

```bash
WORK_DB="$WORK_DB" "$PY" - <<'PY'
import os
import re
import sqlite3
from pathlib import Path

from hermes_state import SessionDB
from hermes_state_common import FTS_STORAGE_VERSION, SCHEMA_VERSION

path = Path(os.environ["WORK_DB"])

# 1. Raw durable invariants.
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    expected_counts = {"sessions": 7268, "messages": 231513, "gateway_routing": 78}
    counts = {}
    for table, want in expected_counts.items():
        got = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        counts[table] = got
        if got != want:
            raise SystemExit(f"FAIL count {table}: {got} != {want}")

    integrity = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise SystemExit(f"FAIL integrity_check: {integrity!r}")
    fk = list(conn.execute("PRAGMA foreign_key_check"))
    if fk:
        raise SystemExit(f"FAIL foreign_key_check: {len(fk)} violation(s)")

    schema_row = conn.execute("SELECT version FROM schema_version").fetchone()
    if schema_row is None or int(schema_row[0]) != int(SCHEMA_VERSION):
        raise SystemExit(f"FAIL schema_version: {schema_row!r}, expected {SCHEMA_VERSION}")

    meta = dict(conn.execute("SELECT key, value FROM state_meta"))
    if meta.get("fts_storage_version") != str(FTS_STORAGE_VERSION):
        raise SystemExit(
            f"FAIL fts_storage_version={meta.get('fts_storage_version')!r}, "
            f"expected {FTS_STORAGE_VERSION}"
        )

    forbidden_markers = {
        "fts_rebuild_high_water", "fts_rebuild_progress",
        "fts_cjk_rebuild_high_water", "fts_cjk_rebuild_progress", "fts_cjk_stale",
        "fts_session_rebuild_high_water", "fts_session_rebuild_progress",
        "fts_session_trigram_rebuild_high_water", "fts_session_trigram_rebuild_progress",
        "fts_session_trigram_stale",
        "fts_session_cjk_rebuild_high_water", "fts_session_cjk_rebuild_progress",
        "fts_session_cjk_stale",
        "fts_optimize_available",
    }
    active = sorted(forbidden_markers.intersection(meta))
    if active:
        raise SystemExit(f"FAIL active FTS marker(s): {active}")

    trash = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name LIKE 'fts_v22_trash_%' ORDER BY name"
    ).fetchall()
    if trash:
        raise SystemExit(f"FAIL legacy FTS trash remains: {trash!r}")

    required = {
        "messages_fts", "messages_fts_trigram",
        "sessions_fts", "sessions_fts_trigram",
    }
    present_tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required - present_tables)
    if missing:
        raise SystemExit(f"FAIL required FTS table(s) missing: {missing}")

    # Same-name legacy protection: identity is DDL, never the table name.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='sessions_fts_trigram'"
    ).fetchone()
    if not row or not row[0]:
        raise SystemExit("FAIL sessions_fts_trigram DDL missing")
    tri_sql = row[0]
    for pattern, label in (
        (r"tokenize\s*=\s*['\"]trigram['\"]", "tokenize='trigram'"),
        (r"content\s*=\s*['\"]sessions_fts_trigram_src['\"]", "modern source view"),
        (r"content_rowid\s*=\s*['\"]row_id['\"]", "content_rowid='row_id'"),
    ):
        if re.search(pattern, tri_sql, re.I) is None:
            raise SystemExit(f"FAIL sessions_fts_trigram lacks {label}: {tri_sql}")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions_fts_trigram)")]
    if cols[:3] != ["title", "id", "display_name"]:
        raise SystemExit(f"FAIL sessions_fts_trigram columns: {cols!r}")
    view = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' "
        "AND name='sessions_fts_trigram_src'"
    ).fetchone()
    if not view or not view[0]:
        raise SystemExit("FAIL sessions_fts_trigram_src view missing")
    modern_trigger_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='sessions' AND sql LIKE '%sessions_fts_trigram%'"
    ).fetchone()[0]
    if modern_trigger_count != 4:
        raise SystemExit(
            f"FAIL sessions trigram trigger count={modern_trigger_count}, expected 4"
        )

    # If CJK surfaces exist, both message + session sides must be coherent.
    cjk_tables = present_tables.intersection({"messages_fts_cjk", "sessions_fts_cjk"})
    if cjk_tables and cjk_tables != {"messages_fts_cjk", "sessions_fts_cjk"}:
        raise SystemExit(f"FAIL partial CJK table set: {sorted(cjk_tables)}")

    print(f"counts={counts}")
    print(f"schema_version={schema_row[0]}")
    print(f"fts_storage_version={meta['fts_storage_version']}")
    print(f"cjk_tables={sorted(cjk_tables)}")
finally:
    conn.close()

# 2. Reuse the pinned target's own authoritative storage-v2 evaluator.
db = SessionDB(path, read_only=True)
try:
    blockers = list(db._fts_storage_v2_blockers(db._conn))
    cjk_runtime = {
        "messages_cjk_loaded": bool(getattr(db, "_fts_cjk_loaded", False)),
        "messages_cjk_available": bool(getattr(db, "_fts_cjk_available", False)),
        "sessions_cjk_available": bool(getattr(db, "_sessions_cjk_available", False)),
    }
    if blockers:
        raise SystemExit(f"FAIL storage-v2 blocker(s): {blockers!r}")
    print(f"storage_v2_blockers={blockers}")
    print(f"cjk_runtime={cjk_runtime}")
finally:
    db.close()
PY
```

The private evaluator call is intentional here: #22 is pinned to this exact source revision, and this is the same target-owned predicate used by startup settlement, `fts_optimize_available()`, the pre-VACUUM refusal, and the final storage-v2 stamp. It is stronger than maintaining a second hand-written approximation.

### Six-index acceptance table

| Surface | Durable identity | Completion rule |
|---|---|---|
| `messages_fts` | modern external-content message FTS | target evaluator accepts; no message Unicode H/P markers |
| `messages_fts_trigram` | modern derived-source trigram message FTS | target evaluator accepts; same message rebuild lane complete |
| `messages_fts_cjk` | CJK external-content message FTS | required when CJK capability/work is present; otherwise absence is allowed only when target evaluator has no blocker |
| `sessions_fts` | `title`, `id`, `display_name`, source `sessions`, rowid `row_id` | no session Unicode H/P markers; target evaluator accepts |
| `sessions_fts_trigram` | source `sessions_fts_trigram_src`, rowid `row_id`, `tokenize='trigram'`, columns `title/id/display_name`, four modern triggers | no trigram H/P/stale markers; exact modern identity; target evaluator accepts |
| `sessions_fts_cjk` | CJK metadata FTS over `title/id/display_name` | required when CJK capability/work is present; otherwise absence is allowed only when target evaluator has no blocker |

At this target `SCHEMA_VERSION=25` and `FTS_STORAGE_VERSION=2`; see [`hermes_state_common.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_common.py#L145-L180) and the six descriptors in the same file.

The final session trigram representation is the #16/#30 normalized metadata design: compact separator forms for title/display-name, raw `id`, and external content through `sessions_fts_trigram_src`. The historical same-name `sessions_fts_trigram` using `tokenize='simple'` is **not** compatible and must fail closed; see [`docs/research/issue-30-normalized-session-metadata-trigram-fts.md`](./issue-30-normalized-session-metadata-trigram-fts.md).

## Checkpoint / stop matrix

| Checkpoint | Continue when | Stop / retry rule |
|---|---|---|
| Source provenance | master path, size, SHA, mode, no sidecars all match | stop; resolve under #20; never repair master in place |
| Target provenance | exact target object exists; accepted core is ancestor; detached worktree clean | stop; do not substitute floating `dev` |
| Producer runtime | pinned environment resolves; Python-linked SQLite safe; FTS5 + trigram probes succeed | stop and choose a safe runtime; discard any DB already opened by a rejected runtime |
| Candidate birth | fresh copy is byte-identical to master before open | discard candidate and copy master again |
| Canonical pre-check | counts 7,268 / 231,513 / 78; integrity `ok`; FK 0 | discard candidate; investigate provenance |
| Build | `optimize-storage --yes` reaches completion or reports resumable interruption | interruption: re-run exact command on same candidate; semantic/schema refusal: do not hand-edit markers, investigate/fix then restart from fresh copy |
| Settlement | zero target evaluator blockers; v2 stamp; no active H/P/stale/optimize markers; no trash | reject artifact; re-run if target reports actionable resumable work, otherwise investigate before any retry |
| Trigram identity | exact modern `tokenize='trigram'` + derived view/rowid/columns/triggers | hard reject; same-name `simple`/unknown schema must never be mutated or blessed manually |
| Canonical post-check | counts unchanged; integrity `ok`; FK 0 | reject artifact |
| Freeze | no live writer, sidecars settled, final file hashed and chmod 0400 | do not promote until receipt is complete |

## Freeze and artifact receipt

After all acceptance checks pass, close every build connection. Ensure no process is using the isolated build home. A final writable checkpoint of the **candidate only** is allowed before freeze if a non-empty WAL remains; never do this to the master.

Recommended final artifact name:

```text
/home/skywind/hermes-recovery/production-builds/<RUN_ID>/state.production-ready.<TARGET_SHORT>.db
```

Recommended final sequence:

```bash
# Candidate only; run after verifier, before chmod/freeze.
WORK_DB="$WORK_DB" "$PY" - <<'PY'
import os
import sqlite3
from pathlib import Path
p = Path(os.environ["WORK_DB"])
conn = sqlite3.connect(p)
try:
    print("wal_checkpoint=", conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
finally:
    conn.close()
PY

ARTIFACT="$BUILD_ROOT/state.production-ready.${TARGET_SHORT}.db"
test ! -s "${WORK_DB}-wal"
mv -- "$WORK_DB" "$ARTIFACT"
chmod 0400 "$ARTIFACT"

ARTIFACT_SIZE=$(stat -c %s "$ARTIFACT")
ARTIFACT_SHA=$(sha256sum "$ARTIFACT" | awk '{print $1}')
printf 'artifact=%s\nsize=%s\nsha256=%s\n' "$ARTIFACT" "$ARTIFACT_SIZE" "$ARTIFACT_SHA"

# Re-prove the source master remained untouched.
test "$(stat -c %s "$MASTER")" -eq "$MASTER_SIZE"
test "$(sha256sum "$MASTER" | awk '{print $1}')" = "$MASTER_SHA"
test "$(stat -c %a "$MASTER")" = 400
test ! -e "${MASTER}-wal"
test ! -e "${MASTER}-shm"
test ! -e "${MASTER}-journal"
```

If `wal_checkpoint(TRUNCATE)` reports busy or leaves a non-empty WAL, **do not freeze/promote**. Find the unexpected connection to the isolated build home, close it, and retry the candidate checkpoint. Do not copy only the main DB while ignoring a live WAL.

The receipt stored beside the artifact should contain at least:

- `run_id`
- canonical master path / size / SHA-256 / mode, plus post-build re-verification
- target full SHA and `TARGET_SHORT`
- accepted-core ancestry result
- source worktree clean-state result
- exact build command and `optimize-storage.log` path
- `HERMES_HOME` used for the build
- Python executable + Python version
- Python-linked SQLite version + `sqlite_source_id()`
- target WAL-reset classifier result
- FTS5 and trigram probe results
- observed CJK capability/schema/availability result
- `schema_version`
- `fts_storage_version`
- active rebuild/stale/optimize marker list (must be empty)
- storage-v2 blocker list (must be empty)
- six-index/schema identity verdict, including modern session trigram proof
- canonical post-build row counts
- `PRAGMA integrity_check` result
- `PRAGMA foreign_key_check` violation count
- final artifact path / size / SHA-256 / mode
- final sidecar state
- producer timestamp in UTC

The output artifact SHA is expected to differ from the recovered master because schema/FTS/VACUUM work changes the file. The invariant is that the **source master SHA never changes** and the canonical relational counts/integrity remain accepted.

## Rollback / retry policy

1. **Before any candidate open:** any provenance/runtime failure → fix the environment and make a new fresh copy.
2. **Normal interruption during `optimize-storage`:** keep the same isolated candidate and re-run the exact pinned command; the rebuild lanes are designed to resume from durable markers.
3. **Unknown same-name trigram schema, non-actionable storage blocker, integrity/FK/count failure, or unexplained schema state:** reject the candidate. Do **not** edit `sqlite_master`, FTS H/P/stale markers, or `fts_storage_version` to force acceptance. Investigate first; after a code/environment fix, start again from a fresh canonical copy.
4. **VACUUM alone fails for space:** the optimizer treats physical reclaim separately from logical migration. For #22 production acceptance, free enough disk and re-run with the default VACUUM path rather than silently downgrading to `--no-vacuum`.
5. **After freeze:** never resume migration on the frozen production artifact. If acceptance evidence is later found incomplete, quarantine it and rebuild from the frozen recovered canonical master.
6. **Promotion/restart is out of scope for #42/#22 build production.** #23 owns the rollout/cutover after the frozen artifact and receipt exist.

## Primary-source map

- #42 research contract: https://github.com/Skywind5487/hermes-agent/issues/42
- #22 execution contract: https://github.com/Skywind5487/hermes-agent/issues/22
- #20 recovered canonical source: https://github.com/Skywind5487/hermes-agent/issues/20
- Final #12 closure audit / #79: https://github.com/Skywind5487/hermes-agent/issues/79
- CLI parser: [`hermes_cli/main.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/main.py#L12154-L12190)
- CLI execution seam: [`hermes_cli/sessions_cmd.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/sessions_cmd.py#L1032-L1135)
- Foreground optimizer / settlement: [`hermes_state_search.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_search.py#L1460-L1685)
- Rebuild lane registry: [`hermes_state_search.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_search.py#L150-L315)
- Runtime DB-path resolution: [`hermes_state.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state.py#L490-L525)
- Six FTS descriptors / schema constants: [`hermes_state_common.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_common.py)
- SQLite runtime classifier: [`hermes_cli/sqlite_runtime.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/sqlite_runtime.py)
- Modern same-name trigram design / fail-closed classifier: [`docs/research/issue-30-normalized-session-metadata-trigram-fts.md`](./issue-30-normalized-session-metadata-trigram-fts.md)
- Storage-v2 settlement audit: [`docs/research/issue-31-storage-v2-settlement.md`](./issue-31-storage-v2-settlement.md)
- Final negative-space / marker audit: [`docs/research/issue-79-session-recovery-trigram-markers.md`](./issue-79-session-recovery-trigram-markers.md)

## Handoff to #22

#22 is ready for an execution agent once this research note is merged and the distilled issue handoff is posted. The executor should treat the command blocks above as the production recipe, preserving the stop conditions rather than optimizing them away.