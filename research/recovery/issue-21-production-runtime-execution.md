# #21 Production Gateway runtime repair and attestation — execution record

Date: 2026-08-10 (+08:00)

Runbook: `research/recovery/issue-21-production-runtime-runbook.md`.
Research basis: `research/recovery/issue-41-production-gateway-runtime-attestation.md`.

Scope: **production runtime repair only.** No Gateway was started, no recovered
DB/candidate was opened or mutated, and `~/.hermes/hermes-enabled` stayed `0`
throughout. The service remains disabled/inactive at the end of #21.

## Execution identity

| Item | Value |
|---|---|
| VM | `hermes` (hostname `hermes`, user `skywind5487`) |
| STAMP | `20260810-120025` |
| RUN evidence dir | `/home/skywind5487/.hermes/runtime-repair-20260810-120025/` |
| Build root | `/home/skywind5487/.hermes/runtime-build/20260810-120025/` |
| OLD runtime | `/home/skywind5487/.hermes/runtimes/cpython-3.12.13-owner` |
| NEW runtime | `/home/skywind5487/.hermes/runtimes/cpython-3.12.13-owner-sqlite3513` |
| Pinned SQLite | `sqlite-autoconf-3510300.tar.gz` (3.51.3), SHA3-256 `581215771b32ea4c4062e6fb9842c4aa43d0a7fb2b6670ff6fa4ebb807781204` |
| Checkout HEAD (VM) | `ad70864f0319495fb159a105cb13f085337523d0` (`dev`, ahead 6 / behind 19 of `fork/dev`) |

## Outcome

**SUCCESS.** All 14 success-checklist items pass (see below). The production
runtime that would launch the Gateway now links SQLite **3.51.3** (WAL-reset
fixed line) while preserving the Hermes `_sqlite` instrumentation byte-for-byte.
The new sibling runtime is **selected by the dormant systemd unit but NOT started**.

## Runbook corrections applied (review findings from "others2", verified on-host)

Two findings from review were confirmed and fixed during execution. Both fixes
are required for the runbook to run to completion and to prove the runtime that
would actually launch:

1. **F1 (blocker) — checksum `diff` could never pass.** The `sha256sum` outputs
   for OLD vs NEW differ only in the path column (e.g. `…/cpython-3.12.13-owner/…`
   vs `…/cpython-3.12.13-owner-sqlite3513/…`), so a literal `diff -u` returns
   non-zero even when the hashes are identical — under `set -e` this terminates
   the run before capability attestation. Fix: compare only the checksum column
   (`cut -d' ' -f1`). Result: OLD and NEW instrumentation source hashes match
   exactly (below).

2. **F2 (high) — systemd→tmux→owner-wrapper E2E selector gap.** Verified
   empirically on tmux 3.3a: the tmux **session environment is taken from the
   tmux server's global environment, frozen at server start**; client custom
   variables only reach a session via the fixed `update-environment` list
   (`DISPLAY KRB5CCNAME SSH_ASKPASS SSH_AUTH_SOCK SSH_AGENT_PID SSH_CONNECTION
   WINDOWID XAUTHORITY`), which does **not** include `HERMES_OWNER_RUNTIME_ROOT`.
   Probe: fresh server + client env → pane sees value (T1 `[/path/NEW]`);
   existing server started without the var + client with the var → pane sees
   empty (T2 `[]`). Therefore a surviving tmux server (any other session keeps
   the server alive) could pin the OLD runtime selector at a later Gateway start.
   Fixes:
   - Phase 0 now uses `tmux kill-server` (not just `kill-session -t hermes`),
     guaranteeing the next `tmux new-session` starts a fresh server whose env is
     captured from the systemd watchdog env (which carries the drop-in selector).
   - New E2E attestation (Phase 8b): a throwaway tmux session proves the selector
     reaches the pane exactly as the watchdog would create it, without starting
     Gateway (see below).

3. **F3 (medium) — `/proc/*/fd` evidence quality.** `readlink` permission errors
   are swallowed, so the scan proves "no DB open among readable fds" rather than
   "no process has the DB open". Accepted as evidence-quality note (all
   production processes are `skywind5487`; scan returned empty before and after).

4. **F4 (low-medium) — instrumentation verified alive, not semantically**
   re-validated. Accepted scope: #21 swaps the runtime and preserves the patch;
   source-hash equality guarantees the C files are unchanged.

## Phase evidence (abridged; full outputs in `$RUN/`)

### Phase 0 — quiescence
- `hermes-gateway.service` disabled (`UnitFileState=disabled`) and stopped
  (`ActiveState=failed` after stop); `hermes-enabled=0`; no tmux session; no
  `hermes … gateway run` process.
- `/proc/*/fd` scan (`open-fds.txt`, 0 bytes): **no state/recovery DB open.**

### Phase 1 — mutable identities re-attested
- systemd unit, watchdog `hermes-tmux.sh`
  (SHA-256 `7179386823172b5d6f0b998d51cd5321683602bfe4b1960adefa81dd9c8dbad0`,
  matches #41), and owner wrapper
  (SHA-256 `bf2024fe0ca0a672168f8deb29e34d484e33ab6cbdfcb62ba9f2e23bcbd70eb3`)
  all match the #41 topology. (Wrapper additionally injects jemalloc
  `LD_PRELOAD` — a heap-fragmentation mitigation orthogonal to runtime
  selection; noted, not material.)

### Phase 2 — OLD runtime attestation (`old-runtime.json`)
- SQLite **3.50.4**, source_id `2025-07-30 19:33:53 4d8adfb3…`
- `_sqlite3` at `…/cpython-3.12.13-owner/build/lib.linux-x86_64-3.12/`
- Instrumentation source hashes: `connection.c 1dc52f45…`, `cursor.c bd100b91…`,
  `cursor.h ad308eef…`; instrumentation symbols present (`_hermes_db_status`,
  `_hermes_stmt_status`, hermes_* counters).

### Phase 3 — pinned SQLite 3.51.3 built
- Archive SHA3-256 verified against the pinned value before extract/build;
  `CFLAGS='-O2 -fPIC -DSQLITE_ENABLE_FTS5'`, static lib installed to
  `…/sqlite-3510300-prefix/`.

### Phase 4 — sibling owner CPython built (never in place)
- `cp -a OLD NEW`, `make distclean`, reconfigured against pinned SQLite prefix
  (build log `phase07-build.log`). `NEW/python` produced (SHA-256
  `818c83d3a4afac946dee6eb3c5a051c35bf733178b0879f949235a2cf20b059d`).
- **F1 fix applied:** Phase 8 compares only the checksum column →
  `OWNER-INSTRUMENTATION: source checksums UNCHANGED between OLD and NEW (OK)`.

### Phase 5 — candidate acceptance (`:memory:` only)
- `new-runtime.json`: SQLite **3.51.3**, source_id
  `2026-03-13 10:38:09 737ae4a3…`; `fts5: true`; `trigram: true`;
  `load_extension: true`; WAL-reset predicate passes (`3.51.3 >= 3.51.3`).
- Hermes instrumentation alive: `stmt_status` = `{vm_steps:5, fullscan_steps:0,
  sort_operations:0, autoindex_operations:0, reprepare_count:0}`; `db_status_keys`
  = all 7 keys (`cache_hit/miss/spill/write/used_bytes`, `schema_used_bytes`,
  `stmt_used_bytes`).
- `hermes-capabilities.txt`: `import hermes_state` under NEW succeeds (isolated
  temp `HERMES_HOME`); `cjk_loader_returned: False` → **CJK recorded as
  unavailable (optional degradation under final #12)**; FTS5/trigram remain
  available independently.

### Phase 6 — dormant runtime selection
- systemd drop-in `/etc/systemd/system/hermes-gateway.service.d/20-owner-runtime.conf`:
  `Environment=HERMES_OWNER_RUNTIME_ROOT=/home/skywind5487/.hermes/runtimes/cpython-3.12.13-owner-sqlite3513`
- `daemon-reload`; service stays `failed`/`disabled`; no tmux; Gateway not started.

### Phase 8b — E2E tmux selector proof (F2 fix)
- systemd source: `HERMES_OWNER_RUNTIME_ROOT=…/cpython-3.12.13-owner-sqlite3513`
- throwaway pane observed: `pane_hermes_owner_runtime_root=…/cpython-3.12.13-owner-sqlite3513`
  → fresh-server tmux propagation of the selector confirmed without starting Gateway.

### Phase 7 — final attestation + repeated no-open proof
- `NEW/python` realpath + SHA-256 recorded; exact Python/SQLite/`_sqlite3`
  re-verified `:memory:`-only; selected service topology re-read (unit + drop-in);
  checkout HEAD recorded separately (`ad70864f…`); `hermes-enabled=0`, unit
  disabled/inactive, no tmux. Repeated `/proc/*/fd` scan: empty (exit 0).

## Success checklist

```
[x] service disabled/inactive; no tmux Gateway
[x] no state/recovery DB open (scan before and after)
[x] systemd service selects NEW via HERMES_OWNER_RUNTIME_ROOT (+ E2E tmux pane proof)
[x] OLD remains intact and available for rollback
[x] exact NEW Python path/hash recorded (818c83d3a4afac…b059d)
[x] exact SQLite version and sqlite_source_id recorded (3.51.3 / 737ae4a3…)
[x] SQLite passes Hermes WAL-reset predicate
[x] FTS5 passes in :memory:
[x] loadable-extension support passes
[x] Hermes `_sqlite` instrumentation still works
[x] current Hermes imports under NEW
[x] CJK recorded as available OR explicit optional degradation (recorded: unavailable)
[x] trigram recorded as available OR explicit optional degradation (recorded: available)
[x] checkout HEAD recorded separately from runtime identity (ad70864f…)
```

## Capability contract for the later recovery cutover

The cutover ticket must compare VM runtime identity against the pinned local
producer contract using the exact-runtime style above (never `hermes update`,
`/usr/bin/sqlite3 --version`, or system Python):

- Python: CPython 3.12.13 (owner build), `_sqlite3` linked from the owner build tree.
- SQLite: **3.51.3**, source_id `2026-03-13 10:38:09 737ae4a3…` — outside the
  WAL-reset vulnerable line; accepted by `hermes_cli/sqlite_runtime.py::is_sqlite_wal_reset_vulnerable`.
- FTS5: available. trigram: available. loadable-extension API: available.
- Hermes `_sqlite` instrumentation: present and callable.
- CJK `cjk_unicode61`: **unavailable under the isolated-home probe** → treated as
  optional degradation under final #12; must not make ordinary Unicode persistence
  unavailable.
- Runtime selector: systemd drop-in `20-owner-runtime.conf`; ensure the tmux
  server is (re)started fresh (Phase-0 `kill-server` invariant) so the selector
  reaches the owner wrapper.

## Rollback

Per runbook: rollback only changes the dormant service selector (restore/remove
the drop-in + `daemon-reload`); it never touches a database. OLD and NEW runtimes
are both kept as evidence; nothing was deleted in place.
