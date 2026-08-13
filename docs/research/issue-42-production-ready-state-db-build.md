# Issue #42 — production-ready `state.db` build surface for #22

Status: **research complete; execution belongs to #22**.

This note freezes the exact revision, build seam, safety boundary, completion predicate, validation probes, artifact receipt, and retry rules for producing the first production-ready database from the recovered patched-canonical master. It intentionally does **not** run the production-scale build.

## 1. Frozen decisions

| Item | Pinned value |
|---|---|
| Target commit | `4e5ad5c2230300d1ffae84b089ffc70e368c8a23` |
| Accepted six-index/storage-v2 core ancestor | `276d497764feb7d4a71f1424ed44b8958da63b16` |
| Canonical master | `/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db` |
| Canonical SHA-256 | `23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104` |
| Canonical size | `1,675,415,552` bytes |
| Canonical counts | `sessions=7,268`, `messages=231,513`, `gateway_routing=78` |
| Schema version at target | `25` |
| FTS storage version at target | `2` |
| Build entrypoint | `hermes sessions optimize-storage --yes` |
| DB-path isolation | fresh `$HERMES_HOME/state.db`; **there is no `--db` option on `optimize-storage`** |

Final #79 closed the remaining #12 audit, and the pinned target is five commits ahead of `276d497...` with that accepted core as its merge-base. #22 must pin the full SHA above; if `dev` moves later, do not silently float the production target.

The frozen patched-canonical master is immutable input. Never run writable Hermes/SQLite, migration, FTS rebuild, `VACUUM`, marker repair, or ad-hoc probes directly on it. Every build or retry begins from a fresh writable copy unless the retry rule below explicitly says the current candidate is resumable.

## 2. Primary-source build surface

The command that actually drives the build is `hermes sessions optimize-storage`:

- [`hermes_cli/main.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/main.py#L12154-L12190) defines `optimize-storage`, `--yes`, and `--no-vacuum`.
- [`hermes_cli/sessions_cmd.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/sessions_cmd.py#L1032-L1145) constructs `SessionDB()`, performs disk-space preflight, emits foreground progress, and calls `db.optimize_fts_storage(...)`.
- [`hermes_state_search.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_search.py#L1460-L1685) repairs migration bookkeeping, drains every pending rebuild lane, tears down legacy trash, refuses incomplete storage-v2 state, VACUUMs by default, checkpoints WAL best-effort, re-evaluates completion transactionally, and stamps `fts_storage_version=2` only if the shared evaluator has no blocker.
- The rebuild registry in [`hermes_state_search.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_search.py#L150-L315) has five lanes covering six indexes:
  1. `messages` → `messages_fts` + `messages_fts_trigram`
  2. `messages_cjk` → `messages_fts_cjk`
  3. `sessions` → `sessions_fts`
  4. `sessions_trigram` → `sessions_fts_trigram`
  5. `sessions_cjk` → `sessions_fts_cjk`

A plain `SessionDB()` open is therefore not the #22 build contract. It may reconcile/seed state, but the production executor must drive the foreground optimizer and then independently prove settlement.

### Why `HERMES_HOME` is the DB selector

`optimize-storage` has no `--db`. [`hermes_state.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state.py#L490-L525) resolves the default database at call time from `get_hermes_home() / "state.db"`; [`hermes_constants.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_constants.py#L84-L145) honors `HERMES_HOME`.

Therefore the isolation boundary is a dedicated build home containing a fresh copy named exactly `state.db`.

## 3. Production recipe

### 3.1 Pin source and prove frozen-master provenance

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

test -f "$MASTER"
test "$(stat -c %s "$MASTER")" -eq "$MASTER_SIZE"
test "$(sha256sum "$MASTER" | awk '{print $1}')" = "$MASTER_SHA"
test "$(stat -c %a "$MASTER")" = 400
test ! -e "${MASTER}-wal"
test ! -e "${MASTER}-shm"
test ! -e "${MASTER}-journal"
```

**Hard stop:** any master path/size/hash/mode/sidecar mismatch. Resolve provenance under #20. Do not chmod or repair the master in place.

### 3.2 Create one isolated attempt

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

The candidate is byte-identical to the master at birth but writable. The source master stays untouched.

### 3.3 Resolve and hard-gate the producer runtime

The project requires Python `>=3.11`. The relevant SQLite is the one linked into the Python that runs Hermes, not the standalone `sqlite3` executable.

```bash
cd "$SOURCE"
uv sync --frozen

PY="$SOURCE/.venv/bin/python"
HERMES="$SOURCE/.venv/bin/hermes"
test -x "$PY"
test -x "$HERMES"

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
print(json.dumps({
    "python_executable": sys.executable,
    "python_version": list(sys.version_info[:3]),
    "sqlite_version": sqlite3.sqlite_version,
    "sqlite_source_id": source_id,
    "wal_reset_vulnerable": vulnerable,
    "fts5": True,
    "trigram": True,
}, indent=2, sort_keys=True))

if vulnerable:
    raise SystemExit("STOP: selected Python is linked to a WAL-reset-vulnerable SQLite")
PY
```

Use the target helper rather than reimplementing the vulnerability rule; [`hermes_cli/sqlite_runtime.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/sqlite_runtime.py) recognizes upstream-safe `>=3.51.3` and the known backport windows.

**Hard stop:** vulnerable SQLite, missing FTS5, or missing built-in trigram. Pick another safe Python runtime before any Hermes open of the candidate.

CJK is an optional loadable capability. Do not manufacture CJK state. The target's own schema/settlement logic decides whether CJK work exists and is actionable; the receipt records what this producer actually loaded/built.

### 3.4 Pre-build candidate invariant check

Run this only on the writable candidate copy:

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

### 3.5 Run the actual build

```bash
set -o pipefail
HERMES_HOME="$BUILD_HOME" \
  "$HERMES" sessions optimize-storage --yes \
  2>&1 | tee "$BUILD_ROOT/optimize-storage.log"
```

Keep the default VACUUM for the production path. The CLI preflights free space. `--no-vacuum` is an explicitly documented deviation, not the default recipe.

Expected foreground phases include rebuild/backfill, teardown, VACUUM, and done. An ordinary interruption is resumable by re-running the same pinned command on the same candidate.

Do **not** accept any of these by themselves:

- a plain DB open;
- `Already compact; nothing to do`;
- table names alone;
- `fts_storage_version=2` alone;
- a table named `sessions_fts_trigram` without schema identity;
- hand-edited state markers.

## 4. Authoritative artifact verifier

The verifier has three layers: durable relational state, the pinned target's own storage-v2/schema classifier, then artifact-specific FTS integrity + MATCH probes.

### 4.1 Durable state + exact target predicates

```bash
WORK_DB="$WORK_DB" "$PY" - <<'PY'
import os
import sqlite3
from pathlib import Path

from hermes_state import SessionDB
from hermes_state_common import FTS_STORAGE_VERSION, SCHEMA_VERSION

path = Path(os.environ["WORK_DB"])
expected_counts = {"sessions": 7268, "messages": 231513, "gateway_routing": 78}

conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
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
        raise SystemExit(f"FAIL schema_version={schema_row!r}, expected {SCHEMA_VERSION}")

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

    present = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required = {"messages_fts", "messages_fts_trigram", "sessions_fts", "sessions_fts_trigram"}
    missing = sorted(required - present)
    if missing:
        raise SystemExit(f"FAIL required FTS table(s) missing: {missing}")

    cjk = present.intersection({"messages_fts_cjk", "sessions_fts_cjk"})
    if cjk and cjk != {"messages_fts_cjk", "sessions_fts_cjk"}:
        raise SystemExit(f"FAIL partial CJK surface: {sorted(cjk)}")
finally:
    conn.close()

# Exact target-owned acceptance predicates. The same storage-v2 evaluator is
# used by startup settlement, optimize availability, pre-VACUUM refusal, and
# the final transactional stamp.
db = SessionDB(path, read_only=True)
try:
    blockers = list(db._fts_storage_v2_blockers(db._conn))
    if blockers:
        raise SystemExit(f"FAIL storage-v2 blocker(s): {blockers!r}")

    trigram_shape = db._classify_sessions_fts_trigram(db._conn)
    if trigram_shape != "modern_trigram":
        raise SystemExit(f"FAIL sessions_fts_trigram shape={trigram_shape!r}")

    cjk_runtime = {
        "messages_cjk_loaded": bool(getattr(db, "_fts_cjk_loaded", False)),
        "messages_cjk_available": bool(getattr(db, "_fts_cjk_available", False)),
        "sessions_cjk_available": bool(getattr(db, "_sessions_cjk_available", False)),
    }
    print("counts=", counts)
    print("schema_version=", schema_row[0])
    print("fts_storage_version=", meta["fts_storage_version"])
    print("storage_v2_blockers=", blockers)
    print("sessions_trigram_shape=", trigram_shape)
    print("cjk_tables=", sorted(cjk))
    print("cjk_runtime=", cjk_runtime)
finally:
    db.close()
PY
```

The private calls are deliberate: #22 is pinned to this exact commit, and these are the target's canonical completion/schema predicates. The #30 classifier explicitly distinguishes `modern_trigram` from `unknown_same_name`; historical same-name `tokenize='simple'` residue fails closed instead of being blessed by name.

### 4.2 FTS5 external-content integrity + title/ID/display-name MATCH smoke

Run before freeze, on the candidate only, with the same producer runtime. This validates every actually present modern FTS table against its external content and proves that all three session metadata fields are searchable on every present session index.

```bash
WORK_DB="$WORK_DB" "$PY" - <<'PY'
import os
from pathlib import Path

from hermes_state import SessionDB
from hermes_state_common import compact_session_metadata_text

path = Path(os.environ["WORK_DB"])
db = SessionDB(path)
try:
    conn = db._conn
    present = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    indexes = [
        name for name in (
            "messages_fts", "messages_fts_trigram", "messages_fts_cjk",
            "sessions_fts", "sessions_fts_trigram", "sessions_fts_cjk",
        ) if name in present
    ]

    # FTS5 rank=1 integrity-check also cross-checks external content.
    for table in indexes:
        conn.execute(
            f"INSERT INTO {table}({table}, rank) VALUES('integrity-check', 1)"
        )
        print(f"fts_integrity={table}:ok")
    # The special command is validation, not canonical data mutation.
    conn.rollback()

    def quote_fts(text: str) -> str:
        return '"' + text.replace('"', '""') + '"'

    def probe_session_field(table: str, field: str) -> None:
        rows = conn.execute(
            f"SELECT row_id, {field} FROM sessions "
            f"WHERE {field} IS NOT NULL AND TRIM({field}) <> '' "
            "ORDER BY row_id LIMIT 1000"
        ).fetchall()
        for row in rows:
            rowid = int(row[0])
            raw = str(row[1])
            value = (
                compact_session_metadata_text(raw)
                if table == "sessions_fts_trigram" and field in {"title", "display_name"}
                else raw
            )
            # Built-in trigram requires >=3 characters to produce useful grams.
            if table == "sessions_fts_trigram" and len(value) < 3:
                continue
            query = f"{field} : {quote_fts(value)}"
            try:
                hit = conn.execute(
                    f"SELECT 1 FROM {table} WHERE {table} MATCH ? AND rowid = ? LIMIT 1",
                    (query, rowid),
                ).fetchone()
            except Exception:
                hit = None
            if hit is not None:
                print(f"match_probe={table}.{field}:ok rowid={rowid}")
                return
        raise SystemExit(f"FAIL no passing MATCH probe for {table}.{field}")

    for table in ("sessions_fts", "sessions_fts_trigram", "sessions_fts_cjk"):
        if table not in present:
            continue
        for field in ("title", "id", "display_name"):
            probe_session_field(table, field)
finally:
    db.close()
PY
```

The production artifact is not modified in canonical rows by these probes. The FTS special integrity command is run before freeze and rolled back; the MATCH probes are reads.

If optional CJK was never established and the producer lacks the tokenizer, absence is acceptable only when the target storage-v2 evaluator returns **zero blockers**. If CJK exists, it must be internally/external-content clean and the session CJK index must pass the same three-field MATCH smoke.

## 5. Six-index acceptance state

| Surface | Required production meaning |
|---|---|
| `messages_fts` | modern external-content Unicode message FTS, complete |
| `messages_fts_trigram` | modern derived-source trigram message FTS, complete |
| `messages_fts_cjk` | complete when optional CJK capability/work is established; otherwise valid absence only if target evaluator has no blocker |
| `sessions_fts` | external-content `title`, logical `id`, `display_name` over `sessions.row_id`, complete |
| `sessions_fts_trigram` | **exact** #30 modern identity: derived `sessions_fts_trigram_src`, `content_rowid='row_id'`, `tokenize='trigram'`, compacted title/display-name + raw id, exact modern triggers |
| `sessions_fts_cjk` | complete CJK metadata surface when capability/work is established; otherwise valid absence only if target evaluator has no blocker |

At this target, storage-v2 acceptance additionally means no required H/P/stale breadcrumb remains, no `fts_v22_trash_%` object remains, no `fts_optimize_available` residue remains, and the shared evaluator has no blocker. See [`docs/research/issue-31-storage-v2-settlement.md`](./issue-31-storage-v2-settlement.md).

The same-name trigram rule comes from [`docs/research/issue-30-normalized-session-metadata-trigram-fts.md`](./issue-30-normalized-session-metadata-trigram-fts.md): name alone is never identity. `tokenize='simple'`, source collisions, incomplete modern trigger ownership, or another near-match is `unknown_same_name` and must remain fail-closed.

## 6. VACUUM result and retry semantics

`optimize_fts_storage()` treats FTS/storage settlement and physical VACUUM as separate outcomes. A VACUUM failure can still leave logically complete storage-v2 state and stamp v2. In that state **re-running `optimize-storage` may correctly say “already compact” and will not necessarily retry VACUUM**.

The CLI itself points physical reclaim to `hermes sessions optimize`; see [`sessions_cmd.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/sessions_cmd.py#L970-L1045) and its post-`optimize-storage` VACUUM-failure message.

Therefore:

- If the default `optimize-storage` VACUUM succeeds: continue.
- If it reports `VACUUM was skipped or failed`: record that fact, investigate/free disk, then run **on the same isolated candidate**:

```bash
HERMES_HOME="$BUILD_HOME" "$HERMES" sessions optimize \
  2>&1 | tee "$BUILD_ROOT/post-storage-optimize.log"
```

Then re-run all artifact verifiers. If the failure indicates I/O/corruption rather than simple space pressure, stop and investigate instead of treating it as a reclaim-only problem.

## 7. Freeze and receipt

After every verifier passes, close build connections and ensure no process is using the isolated build home. A final checkpoint is allowed on the **candidate only**:

```bash
WORK_DB="$WORK_DB" "$PY" - <<'PY'
import os
import sqlite3
from pathlib import Path

p = Path(os.environ["WORK_DB"])
conn = sqlite3.connect(p)
try:
    result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    print("wal_checkpoint=", result)
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

# Re-prove the master was never touched.
test "$(stat -c %s "$MASTER")" -eq "$MASTER_SIZE"
test "$(sha256sum "$MASTER" | awk '{print $1}')" = "$MASTER_SHA"
test "$(stat -c %a "$MASTER")" = 400
test ! -e "${MASTER}-wal"
test ! -e "${MASTER}-shm"
test ! -e "${MASTER}-journal"
```

If checkpoint reports busy or leaves a non-empty WAL, do not freeze/promote. Find the unexpected connection to the isolated build home, close it, and retry the candidate checkpoint. Never copy only a main DB while ignoring a live WAL.

The receipt beside the artifact must record at least:

- `run_id` and UTC producer timestamp;
- canonical master path / size / SHA-256 / mode before and after build;
- target full SHA, short SHA, accepted-core ancestry result, detached-worktree cleanliness;
- exact `HERMES_HOME` and build command/log path;
- Python executable/version;
- Python-linked SQLite version and `sqlite_source_id()`;
- target WAL-reset classifier verdict;
- FTS5/trigram probes and observed CJK loaded/schema/availability state;
- `schema_version` and `fts_storage_version`;
- active H/P/stale/optimize markers (must be empty);
- target storage-v2 blocker list (must be empty);
- session trigram classifier result (must be `modern_trigram`);
- FTS5 external-content integrity results for every present index;
- title/ID/display-name MATCH smoke results for every present session index;
- canonical post-build counts;
- `PRAGMA integrity_check` and FK violation count;
- VACUUM result, including any `sessions optimize` reclaim retry;
- final artifact path / size / SHA-256 / mode and sidecar state.

The output artifact SHA is expected to differ from the recovered master: derived schema/FTS/VACUUM state changed. The protected invariant is that the **source master SHA never changes**, while canonical relational counts/relationships and integrity remain accepted.

## 8. Stop / rollback / retry matrix

| Failure point | Correct action |
|---|---|
| Master path/hash/size/mode/sidecar mismatch | hard stop; resolve under #20; never repair master |
| Target/ancestry mismatch | hard stop; do not float `dev` |
| Unsafe Python-linked SQLite or missing FTS5/trigram | select safe runtime; if candidate was already opened by rejected runtime, discard it and copy master again |
| Fresh-copy hash/count/integrity/FK mismatch | discard candidate; investigate provenance |
| Normal interruption during rebuild | re-run exact pinned `optimize-storage` on the **same** candidate; durable H/P state is designed to resume |
| Unknown same-name trigram / source collision / non-actionable durable blocker | do not edit `sqlite_master` or markers; investigate; after code/environment correction start from fresh master copy |
| `optimize-storage` VACUUM-only failure | if logical validators settle cleanly, fix space/cause and use `hermes sessions optimize` on same candidate, then re-verify |
| FTS integrity/MATCH probe failure | reject candidate; do not freeze; investigate index/schema/runtime cause |
| Canonical post-build count/integrity/FK failure | reject candidate; rebuild only after root cause is understood |
| Frozen artifact later found incomplete | quarantine it; never resume migration on the frozen artifact; rebuild from frozen canonical master |

Promotion/restart is outside this research/build handoff. #23 owns cutover after #22 has a frozen artifact and complete receipt.

## 9. Primary-source map

- #42 research contract: https://github.com/Skywind5487/hermes-agent/issues/42
- #22 execution contract: https://github.com/Skywind5487/hermes-agent/issues/22
- #20 canonical recovery source: https://github.com/Skywind5487/hermes-agent/issues/20
- #79 final #12 closure audit: https://github.com/Skywind5487/hermes-agent/issues/79
- optimizer CLI parser: [`hermes_cli/main.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/main.py#L12154-L12190)
- optimizer CLI execution: [`hermes_cli/sessions_cmd.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/sessions_cmd.py#L1032-L1145)
- storage optimizer/settlement: [`hermes_state_search.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_search.py#L1460-L1685)
- rebuild-lane registry: [`hermes_state_search.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_search.py#L150-L315)
- DB-path resolution: [`hermes_state.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state.py#L490-L525)
- schema/index descriptors and version constants: [`hermes_state_common.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_state_common.py)
- SQLite producer-runtime classifier: [`hermes_cli/sqlite_runtime.py`](https://github.com/Skywind5487/hermes-agent/blob/4e5ad5c2230300d1ffae84b089ffc70e368c8a23/hermes_cli/sqlite_runtime.py)
- modern normalized session trigram design: [`docs/research/issue-30-normalized-session-metadata-trigram-fts.md`](./issue-30-normalized-session-metadata-trigram-fts.md)
- storage-v2 settlement contract: [`docs/research/issue-31-storage-v2-settlement.md`](./issue-31-storage-v2-settlement.md)
- final negative-space/marker audit: [`docs/research/issue-79-session-recovery-trigram-markers.md`](./issue-79-session-recovery-trigram-markers.md)

## 10. Handoff verdict

The research surface is stable enough to hand #22 to an execution agent **after this note is merged and the distilled #22 comment is posted**. The executor should preserve the pin, isolation boundary, exact target predicates, and stop conditions rather than simplifying them away.