# #41 — Production Gateway runtime attestation

Date: 2026-08-09 (+08:00)

Scope: research only for #41 / execution input for #21. **No production runtime repair, Gateway start, database open, recovered-master open, or candidate cutover was performed while producing this note.**

## 1. Answer first

The production launcher is not the checkout's managed `.venv` by itself. The last observed production chain is:

```text
/etc/systemd/system/hermes-gateway.service
  -> /home/skywind5487/.hermes/scripts/hermes-tmux.sh
  -> /home/skywind5487/.hermes/scripts/hermes-owner-runtime.sh gateway run
  -> /home/skywind5487/.hermes/runtimes/cpython-3.12.13-owner/python
  -> /home/skywind5487/.hermes/hermes-agent/.venv/bin/hermes
```

`hermes-owner-runtime.sh` deliberately executes the Hermes console script *with the owner Python*, so the console script's `.venv` shebang does not choose the Python runtime. The wrapper also injects the owner runtime `Lib` / `build` tree plus the checkout and `.venv` site-packages.

Therefore the exact component that owns the SQLite linked by production Gateway is the owner CPython's `_sqlite3` extension, not `/usr/bin/python3`, not the checkout Git commit, and not whatever interpreter `.venv/bin/python` happens to point at.

The old owner runtime was observed with SQLite **3.50.4**, which is in Hermes' and SQLite upstream's WAL-reset vulnerable range. It must not open the recovered candidate.

## 2. Primary machine observations

These observations come from captured commands on the VM. Mutable facts are explicitly marked for re-attestation at #21 execution time rather than silently treated as timeless.

| Surface | Observed fact | Status for #21 |
|---|---|---|
| systemd scope | system unit `hermes-gateway.service` at `/etc/systemd/system/hermes-gateway.service` | launcher identity established; re-read before mutation |
| service user | `User=skywind5487` | established |
| service ExecStart | `/home/skywind5487/.hermes/scripts/hermes-tmux.sh` | established |
| service working directory | no `WorkingDirectory=` in unit; watchdog explicitly `cd "$HOME"` before `tmux new-session` | established |
| service environment | PATH includes `/home/skywind5487/.local/bin`; HOME is `/home/skywind5487` | established |
| watchdog | `/home/skywind5487/.hermes/scripts/hermes-tmux.sh`, observed SHA-256 `7179386823172b5d6f0b998d51cd5321683602bfe4b1960adefa81dd9c8dbad0` | re-hash before #21 |
| watchdog command | default `HERMES_COMMAND=$HOME/.hermes/scripts/hermes-owner-runtime.sh gateway run` | established |
| watchdog control | `~/.hermes/hermes-enabled`; `0` prevents *new* tmux creation but does **not** itself kill an already-existing tmux Gateway | important stop invariant |
| systemd liveness meaning | systemd can remain `active (running)` while only the watchdog shell exists and no tmux/Gateway exists | do not use `systemctl is-active` alone as Gateway proof |
| owner wrapper | `/home/skywind5487/.hermes/scripts/hermes-owner-runtime.sh` | established; re-hash before #21 |
| owner runtime | `/home/skywind5487/.hermes/runtimes/cpython-3.12.13-owner/python` | established |
| Hermes checkout | `/home/skywind5487/.hermes/hermes-agent` | established; exact HEAD is mutable and must be re-read |
| dependency source | `.venv/lib/python3.12/site-packages` plus `.venv/bin/hermes` script | established |
| linked SQLite | owner runtime observed as SQLite `3.50.4` | unsafe; repair gate |
| recovery state | latest recovery handoff says Gateway disabled, no candidate swapped, old owner 3.50.4 must not open candidate | preserve throughout #21 |

### systemd unit shape observed

```ini
[Unit]
Description=Hermes Agent Gateway (tmux)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=skywind5487
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/skywind5487/.local/bin
ExecStart=/home/skywind5487/.hermes/scripts/hermes-tmux.sh
ExecStop=/usr/bin/tmux kill-session -t hermes
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### watchdog launch seam observed

```bash
SESSION="${HERMES_SESSION:-hermes}"
COMMAND="${HERMES_COMMAND:-$HOME/.hermes/scripts/hermes-owner-runtime.sh gateway run}"
CHECK_INTERVAL="${HERMES_CHECK_INTERVAL:-5}"
CONTROL_FILE="${HERMES_CONTROL_FILE:-$HOME/.hermes/hermes-enabled}"
```

### owner wrapper launch seam observed

```bash
RUNTIME_ROOT="${HERMES_OWNER_RUNTIME_ROOT:-$HOME/.hermes/runtimes/cpython-3.12.13-owner}"
HERMES_REPO="${HERMES_REPO:-$HOME/.hermes/hermes-agent}"
VENV_SITE_PACKAGES="$HERMES_REPO/.venv/lib/python3.12/site-packages"

export LD_LIBRARY_PATH="$RUNTIME_ROOT${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$RUNTIME_ROOT/Lib:$RUNTIME_ROOT/build/lib.linux-x86_64-3.12:$HERMES_REPO:$VENV_SITE_PACKAGES${PYTHONPATH:+:$PYTHONPATH}"

exec "$RUNTIME_ROOT/python" "$HERMES_REPO/.venv/bin/hermes" "$@"
```

## 3. What is actually custom in the owner CPython

A direct diff against a clean CPython 3.12.13 tree found a 187-line owner patch, SHA-256:

```text
23147684e641f43c71dcaeb954b98432d0fc6cb38231aed8774b1a961415dbb4
```

The changed files are only:

```text
Modules/_sqlite/connection.c
Modules/_sqlite/cursor.c
Modules/_sqlite/cursor.h
```

The additions expose payload-free SQLite instrumentation:

```text
Connection._hermes_db_status()
  cache_hit
  cache_miss
  cache_write
  cache_spill
  cache_used_bytes
  schema_used_bytes
  stmt_used_bytes

Cursor._hermes_stmt_status()
  vm_steps
  fullscan_steps
  sort_operations
  autoindex_operations
  reprepare_count
```

No WAL-reset workaround, transaction semantic change, tokenizer implementation, or recovery mutation was found in this owner patch. This sharply reduces the repair problem: preserve/rebase the instrumentation, but replace the vulnerable SQLite linked into the owner runtime.

The old CPython configure record is also preserved in `config.log` and shows:

```text
./configure \
  --prefix=/tmp/cpython-owner \
  --with-ensurepip=no \
  --without-lto \
  --enable-shared \
  --enable-loadable-sqlite-extensions \
  CPPFLAGS=-I/tmp/sqlite-3500400 \
  LDFLAGS=-L/tmp/sqlite-3500400 \
  'LIBS=-lsqlite3 -lm'
```

So the old runtime was intentionally built against a private SQLite 3.50.4 build and with loadable SQLite extensions enabled.

## 4. Least-risk runtime repair path

Do **not** make `hermes update` the #21 repair primitive for this production launcher.

Current `dev` `hermes_cli/managed_uv.py` can repair a checkout-owned `venv/.venv` by provisioning a new Python generation, syncing a candidate venv, smoke-testing it, and rename-cutting it over. That is good machinery for a managed install, but the custom production wrapper above can still force `cpython-3.12.13-owner/python` afterward. A successful managed update therefore does not attest the production Gateway runtime.

The smallest-change production repair is:

```text
old owner CPython 3.12.13 + Hermes SQLite counters + SQLite 3.50.4
    |
    | preserve the 187-line instrumentation patch
    | rebuild as a NEW sibling runtime, never in place
    v
new owner CPython 3.12.13 + same Hermes counters + fixed SQLite
    |
    | memory-only capability probes + Hermes import smoke
    v
atomically change owner-wrapper RUNTIME_ROOT / target
    |
    | service remains disabled; Gateway still not started
    v
repeat exact-runtime attestation
```

For minimum semantic drift, SQLite **3.51.3** is a good pinned build target: it is the first ordinary SQLite patch release that upstream explicitly states fixes the WAL-reset bug. Hermes' current safety predicate accepts `>=3.51.3`, and also accepts the 3.50.7 / 3.44.6 backports. Pin the source archive and hash in the #21 execution log rather than using an unversioned system library.

## 5. Current Hermes runtime contract relevant to #21

### SQLite version gate

`hermes_cli/sqlite_runtime.py` probes an *exact Python executable* with an isolated `:memory:` database and records:

```text
sys.executable
sys.base_prefix
Python version
sqlite3.sqlite_version
sqlite_source_id()
```

Its accepted WAL-reset lines are:

```text
SQLite >= 3.51.3
or 3.50.7 <= SQLite < 3.51.0
or 3.44.6 <= SQLite < 3.45.0
```

Use this exact-runtime style for #21. Do not infer SQLite from `sqlite3 --version` or `/usr/bin/python3`.

### FTS / tokenizer gate under final #12

Required to pass #21:

- SQLite FTS5 itself.
- Base Unicode FTS behavior used by the canonical message/session-title indexes.
- Loadable-extension support in the exact production Python when the CJK extension is to be used.

Optional/degrading capability:

- `cjk_unicode61`: final #12 explicitly treats CJK capability independently; missing tokenizer must degrade safely rather than make ordinary Unicode persistence unavailable.
- SQLite FTS5 `trigram`: current accepted message-search code treats trigram as optional and falls back rather than making persistence unavailable.

Not a #21 gate:

- legacy `simple` session tokenizer residue (#19 cleanup).
- a future modern session-metadata trigram design from #16 unless the eventually pinned production TARGET_COMMIT actually includes it.

## 6. Safety proof for #41

This research did not execute any command against:

```text
state.recovered.nofts.db
state.recovered.patched.db
any production-ready recovered candidate
```

No `SessionDB`, SQLite backup, integrity check, schema probe, FTS rebuild, `hermes doctor`, or `gateway run` was executed against the recovery artifacts as part of #41. Runtime capability probes specified for #21 are deliberately `:memory:` only, and the runbook begins by proving no process has a file descriptor into the recovery tree.

## 7. Mutable identity that #21 must refresh, not rediscover

The launcher/runtime topology is established. Two facts must still be refreshed at execution time because they are expected to change:

1. the exact checkout HEAD at `/home/skywind5487/.hermes/hermes-agent`;
2. hashes/realpaths of the launcher scripts and candidate owner runtime.

That refresh is attestation, not architecture discovery. #21 should fail closed if the live topology no longer matches this note.

## 8. Source pointers

Repo (`dev` at time of research):

- `hermes_cli/sqlite_runtime.py` — exact-interpreter SQLite probe and WAL-reset safety predicate.
- `hermes_cli/managed_uv.py` — managed checkout runtime repair; useful contrast, but does not own the custom systemd wrapper.
- `hermes_state.py` — runtime WAL-reset guard and FTS extension loaders.
- `hermes_state_schema.py` / `hermes_state_search.py` — FTS5 required vs optional trigram/CJK degradation behavior.
- #12 — final session-title FTS storage/lifecycle contract.
- #20 — recovery/cutover contract.
- #21 — runtime repair execution task.
- #41 — this research task.

Upstream SQLite primary source:

- WAL documentation section “The WAL-Reset Bug”: fix in 3.51.3+, with 3.50.7 / 3.44.6 backports.
- SQLite 3.51.3 release/download artifact `sqlite-autoconf-3510300.tar.gz`, SHA3-256 `581215771b32ea4c4062e6fb9842c4aa43d0a7fb2b6670ff6fa4ebb807781204`.
