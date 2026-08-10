# #21 — Production Gateway runtime repair and attestation runbook

Date: 2026-08-09 (+08:00)

Research basis: `research/recovery/issue-41-production-gateway-runtime-attestation.md`.

This is the execution artifact produced by #41. It is intentionally separate from the evidence note so the operational sequence can be reviewed as code-like material.

> **Corrections (from execution review, 2026-08-10).** Two findings were confirmed
> and fixed so the runbook runs to completion and proves the runtime that would
> actually launch: (1) the Phase 4 instrumentation checksum check now compares
> only the checksum column (a literal `diff` of the full `sha256sum` lines always
> fails because the path columns differ); (2) Phase 0 now uses `tmux kill-server`
> because tmux session environments come from the server's frozen global env, so a
> surviving server could pin the OLD runtime selector — and Phase 6b adds a
> non-Gateway E2E proof that the selector reaches the tmux pane. See
> `research/recovery/issue-21-production-runtime-execution.md` (execution record).

## Goal

Replace only the vulnerable SQLite-bearing **owner runtime** while preserving:

- the existing systemd → tmux watchdog → owner-wrapper topology;
- CPython 3.12.13;
- the small Hermes `_sqlite` instrumentation patch;
- the existing checkout and `.venv` dependencies.

Build a new sibling owner runtime, prove it with `:memory:` only, then select it for the dormant systemd service. **Do not start Gateway and do not open a recovered DB/candidate in #21.**

## Known production topology

```text
/etc/systemd/system/hermes-gateway.service
  -> ~/.hermes/scripts/hermes-tmux.sh
  -> ~/.hermes/scripts/hermes-owner-runtime.sh gateway run
  -> ~/.hermes/runtimes/cpython-3.12.13-owner/python
  -> ~/.hermes/hermes-agent/.venv/bin/hermes
```

The owner runtime's custom source delta is only the Hermes SQLite status instrumentation recorded in the #41 evidence note.

---

## Phase 0 — establish quiescence

```bash
set -euo pipefail

HOME_DIR=/home/skywind5487
HERMES_HOME="$HOME_DIR/.hermes"
REPO="$HERMES_HOME/hermes-agent"
OLD="$HERMES_HOME/runtimes/cpython-3.12.13-owner"
NEW="$HERMES_HOME/runtimes/cpython-3.12.13-owner-sqlite3513"
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN="$HERMES_HOME/runtime-repair-$STAMP"
mkdir -p "$RUN"

# Prevent watchdog recreation, then terminate the ENTIRE tmux server (all sessions).
# kill-server (not just `kill-session`) is required: a surviving tmux server keeps
# its global environment frozen from server start, and tmux only imports a fixed
# update-environment list (DISPLAY, SSH_*, ...) from the client — NOT
# HERMES_OWNER_RUNTIME_ROOT. A stale server would pin the OLD runtime selector at
# a later Gateway start. Fresh server => next session captures the systemd env.
printf '0\n' > "$HERMES_HOME/hermes-enabled"
tmux kill-server 2>/dev/null || true

# Stop and disable the system service. ExecStop can be non-zero if tmux is absent.
sudo systemctl disable hermes-gateway.service
sudo systemctl stop hermes-gateway.service || true

if sudo systemctl is-active --quiet hermes-gateway.service; then
  echo 'STOP: hermes-gateway.service is still active' >&2
  exit 1
fi
if tmux has-session -t hermes 2>/dev/null; then
  echo 'STOP: hermes tmux session still exists' >&2
  exit 1
fi
if pgrep -af '[h]ermes.*gateway run'; then
  echo 'STOP: a Hermes Gateway process still exists' >&2
  exit 1
fi
```

### Prove no state/recovery DB is already open

This inspects `/proc/*/fd` symlink targets only; it does not open a database.

```bash
OPEN_LOG="$RUN/open-fds.txt"
: > "$OPEN_LOG"

for fd in /proc/[0-9]*/fd/*; do
  target="$(readlink "$fd" 2>/dev/null || true)"
  case "$target" in
    *state.recovered.*|*state.db|*state.db-wal|*state.db-shm)
      printf '%s -> %s\n' "$fd" "$target" >> "$OPEN_LOG"
      ;;
  esac
done

cat "$OPEN_LOG"
if [ -s "$OPEN_LOG" ]; then
  echo 'STOP: a state/recovery database is still open' >&2
  exit 1
fi
```

Stop on any opener. Do not use SQLite against the recovery artifact to identify it.

---

## Phase 1 — refresh mutable identities

The topology is already researched; these are expected mutable identities that must be re-attested immediately before repair.

```bash
{
  date -Is
  echo '=== service ==='
  sudo systemctl cat hermes-gateway.service
  sudo systemctl show hermes-gateway.service \
    -p FragmentPath -p DropInPaths -p ExecStart -p Environment \
    -p User -p Group -p WorkingDirectory -p ActiveState -p UnitFileState

  echo '=== launcher scripts ==='
  sha256sum \
    "$HERMES_HOME/scripts/hermes-tmux.sh" \
    "$HERMES_HOME/scripts/hermes-owner-runtime.sh"
  sed -n '1,220p' "$HERMES_HOME/scripts/hermes-tmux.sh"
  sed -n '1,220p' "$HERMES_HOME/scripts/hermes-owner-runtime.sh"

  echo '=== checkout ==='
  git -C "$REPO" status --short --branch
  git -C "$REPO" rev-parse HEAD
  git -C "$REPO" remote -v
} | tee "$RUN/preflight.txt"
```

**Stop** if `ExecStart`, the watchdog command, owner-wrapper structure, checkout path, or owner runtime path materially differs from #41.

---

## Phase 2 — attest the old exact Python/SQLite runtime

Only `:memory:` is opened.

```bash
LD_LIBRARY_PATH="$OLD" "$OLD/python" -I - <<'PY' | tee "$RUN/old-runtime.json"
import json
import sqlite3
import sys
import _sqlite3

conn = sqlite3.connect(':memory:')
try:
    source_id = conn.execute('SELECT sqlite_source_id()').fetchone()[0]
    compile_options = [row[0] for row in conn.execute('PRAGMA compile_options')]
finally:
    conn.close()

print(json.dumps({
    'executable': sys.executable,
    'base_prefix': sys.base_prefix,
    'python_version': sys.version,
    'sqlite_version': sqlite3.sqlite_version,
    'sqlite_source_id': source_id,
    '_sqlite3': _sqlite3.__file__,
    'compile_options': compile_options,
}, indent=2))
PY
```

Expected historical result is SQLite `3.50.4`. If it is already different, stop and classify the change before rebuilding.

Preserve the owner instrumentation source identity:

```bash
sha256sum \
  "$OLD/Modules/_sqlite/connection.c" \
  "$OLD/Modules/_sqlite/cursor.c" \
  "$OLD/Modules/_sqlite/cursor.h" \
  | tee "$RUN/owner-sqlite-source.sha256"

grep -nE '_hermes_(db|stmt)_status|hermes_(vm|fullscan|sort|autoindex|reprepare)' \
  "$OLD/Modules/_sqlite/connection.c" \
  "$OLD/Modules/_sqlite/cursor.c" \
  "$OLD/Modules/_sqlite/cursor.h" \
  | tee "$RUN/owner-instrumentation.txt"
```

---

## Phase 3 — build pinned fixed SQLite privately

Pinned target: SQLite 3.51.3.

Official artifact:

```text
https://sqlite.org/2026/sqlite-autoconf-3510300.tar.gz
SHA3-256 581215771b32ea4c4062e6fb9842c4aa43d0a7fb2b6670ff6fa4ebb807781204
```

SQLite documents 3.51.3+ as fixed for the WAL-reset bug. Hermes' runtime predicate also accepts 3.50.7 and 3.44.6 backports, but 3.51.3 is pinned here so the execution is reproducible.

```bash
BUILD_ROOT="$HERMES_HOME/runtime-build/$STAMP"
SQLITE_TGZ="$BUILD_ROOT/sqlite-autoconf-3510300.tar.gz"
SQLITE_SRC="$BUILD_ROOT/sqlite-autoconf-3510300"
SQLITE_PREFIX="$BUILD_ROOT/sqlite-3510300-prefix"
mkdir -p "$BUILD_ROOT"

curl -fL \
  https://sqlite.org/2026/sqlite-autoconf-3510300.tar.gz \
  -o "$SQLITE_TGZ"

python3 - "$SQLITE_TGZ" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
actual = hashlib.sha3_256(path.read_bytes()).hexdigest()
expected = '581215771b32ea4c4062e6fb9842c4aa43d0a7fb2b6670ff6fa4ebb807781204'
print('sha3_256:', actual)
if actual != expected:
    raise SystemExit('STOP: SQLite archive SHA3-256 mismatch')
PY

tar -xzf "$SQLITE_TGZ" -C "$BUILD_ROOT"
cd "$SQLITE_SRC"

CFLAGS='-O2 -fPIC -DSQLITE_ENABLE_FTS5' \
  ./configure \
    --prefix="$SQLITE_PREFIX" \
    --disable-shared \
    --enable-static

make -j"$(nproc)"
make install
```

Do not substitute an unpinned distro SQLite.

---

## Phase 4 — build a new sibling owner CPython

Never rebuild `$OLD` in place. Copying the old CPython source tree preserves the Hermes instrumentation edits; `make distclean` removes generated configuration/build state while leaving those source edits.

```bash
if [ -e "$NEW" ]; then
  echo "STOP: candidate runtime already exists: $NEW" >&2
  exit 1
fi

cp -a "$OLD" "$NEW"
cd "$NEW"
make distclean || true

./configure \
  --prefix="$BUILD_ROOT/cpython-owner-prefix" \
  --with-ensurepip=no \
  --without-lto \
  --enable-shared \
  --enable-loadable-sqlite-extensions \
  CPPFLAGS="-I$SQLITE_PREFIX/include" \
  LDFLAGS="-L$SQLITE_PREFIX/lib" \
  LIBS='-lsqlite3 -lm'

make -j"$(nproc)"
test -x "$NEW/python"
```

Verify the instrumentation source survived unchanged:

```bash
sha256sum \
  "$NEW/Modules/_sqlite/connection.c" \
  "$NEW/Modules/_sqlite/cursor.c" \
  "$NEW/Modules/_sqlite/cursor.h" \
  | tee "$RUN/new-owner-sqlite-source.sha256"

# Compare ONLY the checksum column: the path columns differ between OLD and NEW by
# design, so a literal `diff` of the full lines always reports differences even
# when every hash is identical (which, under `set -e`, would abort the run).
diff -u \
  <(cut -d' ' -f1 "$RUN/owner-sqlite-source.sha256") \
  <(cut -d' ' -f1 "$RUN/new-owner-sqlite-source.sha256") \
  && echo 'OWNER-INSTRUMENTATION: source checksums UNCHANGED between OLD and NEW (OK)'
```

The file paths in the checksum output differ, so a literal textual `diff` on the
full lines ALWAYS differs (path-only); the command above compares only the
checksum column. Any content checksum change is a stop condition.

---

## Phase 5 — candidate runtime acceptance, memory only

### 5.1 SQLite identity + required/optional FTS capabilities + Hermes instrumentation

```bash
LD_LIBRARY_PATH="$NEW" "$NEW/python" -I - <<'PY' | tee "$RUN/new-runtime.json"
import json
import sqlite3
import sys
import _sqlite3

conn = sqlite3.connect(':memory:')
source_id = conn.execute('SELECT sqlite_source_id()').fetchone()[0]
compile_options = [row[0] for row in conn.execute('PRAGMA compile_options')]

conn.execute('CREATE VIRTUAL TABLE temp.__fts USING fts5(x)')
fts5 = True

try:
    conn.execute("CREATE VIRTUAL TABLE temp.__tri USING fts5(x, tokenize='trigram')")
    trigram = True
    trigram_error = None
except sqlite3.Error as exc:
    trigram = False
    trigram_error = str(exc)

try:
    conn.enable_load_extension(True)
    conn.enable_load_extension(False)
    load_extension = True
    load_extension_error = None
except Exception as exc:
    load_extension = False
    load_extension_error = repr(exc)

cursor = conn.execute('SELECT 1')
cursor.fetchall()
stmt_status = cursor._hermes_stmt_status()
db_status = conn._hermes_db_status()

payload = {
    'executable': sys.executable,
    'base_prefix': sys.base_prefix,
    'python_version': sys.version,
    'sqlite_version': sqlite3.sqlite_version,
    'sqlite_source_id': source_id,
    '_sqlite3': _sqlite3.__file__,
    'compile_options': compile_options,
    'fts5': fts5,
    'trigram': trigram,
    'trigram_error': trigram_error,
    'load_extension': load_extension,
    'load_extension_error': load_extension_error,
    'stmt_status': stmt_status,
    'db_status_keys': sorted(db_status),
}
print(json.dumps(payload, indent=2))

version = sqlite3.sqlite_version_info
safe = (
    version >= (3, 51, 3)
    or (3, 50, 7) <= version < (3, 51, 0)
    or (3, 44, 6) <= version < (3, 45, 0)
)
if not safe:
    raise SystemExit('STOP: candidate SQLite remains WAL-reset vulnerable')
if not fts5:
    raise SystemExit('STOP: FTS5 missing')
if not load_extension:
    raise SystemExit('STOP: loadable-extension support missing')

conn.close()
PY
```

Interpretation under the current/final #12 contract:

- **FTS5 missing:** hard failure.
- **loadable-extension API missing:** hard failure for this owner-runtime shape because the CJK extension cannot be loaded.
- **trigram missing:** record the degradation; current accepted message-search logic treats it as optional/fallback-capable.

### 5.2 Current Hermes import + CJK capability under an isolated home

This uses a temporary `HERMES_HOME` and an explicit `:memory:` connection.

```bash
PROBE_HOME="$(mktemp -d)"
trap 'rm -rf "$PROBE_HOME"' EXIT

LD_LIBRARY_PATH="$NEW" \
PYTHONPATH="$NEW/Lib:$NEW/build/lib.linux-x86_64-3.12:$REPO:$REPO/.venv/lib/python3.12/site-packages" \
HERMES_HOME="$PROBE_HOME" \
"$NEW/python" - <<'PY' | tee "$RUN/hermes-capabilities.txt"
import sqlite3
import hermes_state

conn = sqlite3.connect(':memory:')
loaded = bool(hermes_state.load_fts5_cjk_extension(conn))
print('cjk_loader_returned:', loaded)

if loaded:
    conn.execute("CREATE VIRTUAL TABLE temp.__cjk USING fts5(x, tokenize='cjk_unicode61')")
    conn.execute("INSERT INTO __cjk(x) VALUES ('中文測試')")
    count = conn.execute("SELECT count(*) FROM __cjk WHERE __cjk MATCH '中文'").fetchone()[0]
    print('cjk_match_rows:', count)
else:
    print('cjk_status: unavailable; optional degradation under final #12')

conn.close()
PY

rm -rf "$PROBE_HOME"
trap - EXIT
```

CJK unavailable is **not** equivalent to FTS5 unavailable. Final #12 explicitly models CJK tokenizer capability independently and requires safe degradation.

Stop if importing current Hermes under `$NEW` fails.

---

## Phase 6 — select the sibling runtime for the dormant system service

Do not edit the owner wrapper or delete the old runtime. A systemd drop-in changes only the production service environment and gives a small rollback surface.

```bash
DROPIN_DIR=/etc/systemd/system/hermes-gateway.service.d
DROPIN="$DROPIN_DIR/20-owner-runtime.conf"

sudo mkdir -p "$DROPIN_DIR"
if sudo test -e "$DROPIN"; then
  sudo cp -a "$DROPIN" "$RUN/20-owner-runtime.conf.before"
fi

printf '%s\n' \
  '[Service]' \
  "Environment=HERMES_OWNER_RUNTIME_ROOT=$NEW" \
  | sudo tee "$DROPIN" >/dev/null

sudo systemctl daemon-reload

sudo systemctl show hermes-gateway.service \
  -p DropInPaths -p Environment -p ExecStart -p ActiveState -p UnitFileState \
  | tee "$RUN/service-after-runtime-select.txt"

if sudo systemctl is-active --quiet hermes-gateway.service; then
  echo 'STOP: service unexpectedly active after runtime selection' >&2
  exit 1
fi
if tmux has-session -t hermes 2>/dev/null; then
  echo 'STOP: tmux unexpectedly exists after runtime selection' >&2
  exit 1
fi
```

Leave `~/.hermes/hermes-enabled` at `0` and the unit disabled. **#21 ends before Gateway start.**

### Phase 6b — E2E tmux selector proof (without starting Gateway)

Proves the selector reaches the tmux pane exactly as `hermes-tmux.sh` will create
the `hermes` session (fresh server after Phase 0 `kill-server`, env from the
systemd watchdog). Throwaway session; Gateway is not started.

```bash
export HERMES_OWNER_RUNTIME_ROOT="$NEW"

echo '=== systemd env selector (source) ==='
sudo systemctl show hermes-gateway.service -p Environment \
  | tr ' ' '\n' | grep '^HERMES_OWNER_RUNTIME_ROOT=' || echo '(not set)'

PROBE_SESSION=hermes-envprobe
PROBE_OUT="$RUN/tmux-e2e-env.txt"
rm -f "$PROBE_OUT"

tmux new-session -d -s "$PROBE_SESSION" \
  'printf "pane_hermes_owner_runtime_root=%s\n" "$HERMES_OWNER_RUNTIME_ROOT" > "$RUN/tmux-e2e-env.txt"'
sleep 1
cat "$PROBE_OUT"
tmux kill-server 2>/dev/null || true

if grep -q "pane_hermes_owner_runtime_root=$NEW" "$PROBE_OUT"; then
  echo 'E2E-TMUX: fresh-server pane sees HERMES_OWNER_RUNTIME_ROOT == NEW (OK)'
else
  echo 'STOP: HERMES_OWNER_RUNTIME_ROOT did not reach the pane as NEW' >&2
  exit 1
fi
```

---

## Phase 7 — final attestation

```bash
{
  date -Is

  echo '=== selected runtime ==='
  readlink -f "$NEW/python"
  sha256sum "$NEW/python"

  LD_LIBRARY_PATH="$NEW" "$NEW/python" -I - <<'PY'
import json
import sqlite3
import sys
import _sqlite3

conn = sqlite3.connect(':memory:')
print(json.dumps({
    'sys_executable': sys.executable,
    'sqlite_version': sqlite3.sqlite_version,
    'sqlite_source_id': conn.execute('SELECT sqlite_source_id()').fetchone()[0],
    '_sqlite3': _sqlite3.__file__,
}, indent=2))
conn.close()
PY

  echo '=== selected service topology ==='
  sudo systemctl cat hermes-gateway.service
  sudo systemctl show hermes-gateway.service \
    -p DropInPaths -p Environment -p ExecStart -p ActiveState -p UnitFileState

  echo '=== checkout identity; deliberately separate from runtime ==='
  git -C "$REPO" rev-parse HEAD
  git -C "$REPO" status --short --branch

  echo '=== disabled controls ==='
  printf 'hermes-enabled='; cat "$HERMES_HOME/hermes-enabled"
  sudo systemctl is-enabled hermes-gateway.service || true
  sudo systemctl is-active hermes-gateway.service || true
  tmux ls 2>&1 || true
} | tee "$RUN/final-attestation.txt"
```

Repeat the Phase 0 `/proc/*/fd` scan. It must still show no state/recovery DB opener.

## Success checklist

```text
[ ] service disabled/inactive; no tmux Gateway
[ ] no state/recovery DB open
[ ] systemd service selects NEW via HERMES_OWNER_RUNTIME_ROOT
[ ] tmux pane E2E sees NEW selector (Phase 6b)
[ ] OLD remains intact and available for rollback
[ ] exact NEW Python path/hash recorded
[ ] exact SQLite version and sqlite_source_id recorded
[ ] SQLite passes Hermes WAL-reset predicate
[ ] FTS5 passes in :memory:
[ ] loadable-extension support passes
[ ] Hermes `_sqlite` instrumentation still works
[ ] current Hermes imports under NEW
[ ] CJK recorded as available OR explicit optional degradation
[ ] trigram recorded as available OR explicit optional degradation
[ ] checkout HEAD recorded separately from runtime identity
```

Do not claim success from `hermes update`, `/usr/bin/sqlite3 --version`, or system Python alone.

---

## Rollback

Rollback only changes the dormant service selector. It does not touch any DB.

```bash
set -euo pipefail
printf '0\n' > "$HERMES_HOME/hermes-enabled"
tmux kill-server 2>/dev/null || true
sudo systemctl stop hermes-gateway.service || true

DROPIN=/etc/systemd/system/hermes-gateway.service.d/20-owner-runtime.conf
if [ -f "$RUN/20-owner-runtime.conf.before" ]; then
  sudo cp -a "$RUN/20-owner-runtime.conf.before" "$DROPIN"
else
  sudo rm -f "$DROPIN"
fi
sudo systemctl daemon-reload

# Keep both OLD and NEW for evidence; do not delete either in rollback.
```

Then repeat the service-disabled and `/proc/*/fd` proof.

## Stop conditions

Stop instead of improvising if:

- launcher topology materially differs from the #41 evidence note;
- any state/recovery DB already has an open fd;
- old owner runtime no longer matches the known CPython 3.12.13 instrumentation shape;
- SQLite source hash fails;
- candidate SQLite fails the WAL-reset safety predicate;
- FTS5 or loadable-extension support is absent;
- current Hermes cannot import under the candidate runtime;
- service/tmux/Gateway becomes active during repair;
- preserving the instrumentation would require modifying `$OLD` in place.

## Boundary with later recovery cutover

#21 leaves a fully attested production Gateway runtime **selected but dormant**. The later recovery/cutover task must re-run the bounded runtime + checkout identity checks after its exact `TARGET_COMMIT` is pinned, but it should not need to rediscover systemd/tmux/wrapper/Python/SQLite ownership.