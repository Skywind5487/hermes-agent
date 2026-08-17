# Issue #44 — production cutover runbook for #24 (one-shot swap + persistence proof)

Status: **research complete; execution belongs to #24. This note pins identities and commands only — no production cutover was performed.**

Date: 2026-08-14 (+08:00)

## 1. Scope and ground rules

This is the research handoff for the #24 execution ticket. It consumes the completed #21
runtime attestation and #23 disposable write/restart validation, and freezes the exact VM
service/runtime paths, `TARGET_COMMIT`, production/frozen artifact hashes, quiescent swap
commands, first-start evidence, and rollback triggers.

The production VM must **not** become a migration laboratory. Every irreversible boundary in
this runbook is preceded by an explicit gate and a required evidence capture. The frozen
canonical master and the frozen production-ready artifact are immutable inputs; all mutations
land only on the production `state.db` after quiescence and a hash-verified swap.

Primary sources used (each claim below traces to one of these):

- #41 attestation: `research/recovery/issue-41-production-gateway-runtime-attestation.md` (on `dev`)
- #21 runbook: `research/recovery/issue-21-production-runtime-runbook.md` (on `dev`)
- #21 execution: `research/recovery/issue-21-production-runtime-execution.md` @ `docs/issue-21-runtime-repair-evidence` (`090b724dd`)
- #42 build surface: `docs/research/issue-42-production-ready-state-db-build.md` @ `research/issue-42-production-build`
- #22 execution: `docs/research/issue-22-production-ready-state-db-execution.md` (on `dev`)
- #43 validation surface: `docs/research/issue-43-writable-restart-validation.md` @ `research/issue-43-writable-restart-validation`
- Issues #20, #21, #22, #23, #24, #41, #44 bodies + their comments (ids cited inline)
- Target code @ `4e5ad5c2230300d1ffae84b089ffc70e368c8a23` (line anchors cited inline)

## 2. Pinned evidence identities (pinned #21 / #22 / #23)

| Ticket | Evidence identity | Value |
|---|---|---|
| #21 runtime attestation | VM | `hermes` (hostname `hermes`, user `skywind5487`) |
| #21 | NEW owner runtime | `/home/skywind5487/.hermes/runtimes/cpython-3.12.13-owner-sqlite3513` |
| #21 | NEW `python` SHA-256 | `818c83d3a4afac946dee6eb3c5a051c35bf733178b0879f949235a2cf20b059d` |
| #21 | SQLite linked by NEW | `3.51.3`, source_id `2026-03-13 10:38:09 737ae4a3…6d618` |
| #21 | WAL-reset predicate | `is_sqlite_wal_reset_vulnerable` → `False` (accepted lines `>=3.51.3`, `3.50.7–3.50.x`, `3.44.6–3.44.x`) |
| #21 | systemd selector drop-in | `/etc/systemd/system/hermes-gateway.service.d/20-owner-runtime.conf` → `Environment=HERMES_OWNER_RUNTIME_ROOT=…/cpython-3.12.13-owner-sqlite3513` |
| #21 | launcher hashes | `hermes-tmux.sh` `7179386823172b5d6f0b998d51cd5321683602bfe4b1960adefa81dd9c8dbad0`; `hermes-owner-runtime.sh` `bf2024fe0ca0a672168f8deb29e34d484e33ab6cbdfcb62ba9f2e23bcbd70eb3` |
| #21 | VM checkout at #21 time | `/home/skywind5487/.hermes/hermes-agent` @ `ad70864f0319495fb159a105cb13f085337523d0` (`dev`, ahead 6 / behind 19 of fork/dev) — **must be re-pinned to `TARGET_COMMIT` at cutover** |
| #21 | evidence dir | `~/.hermes/runtime-repair-20260810-120025/` |
| #22 | frozen artifact | `/home/skywind/hermes-recovery/production-builds/20260813T083500Z-4e5ad5c22303-cjk3513/state.production-ready.4e5ad5c22303-cjk3513.db` |
| #22 | artifact SHA-256 / size / mode | `3a3a410f62ada5e32fc2375e657248ebb4308b688da0e65f7c85c70ceaa6818f` / `1667649536` / `0400` / no sidecars |
| #22 | receipt | `…/20260813T083500Z-4e5ad5c22303-cjk3513/RECEIPT.md` (all #81 §7 fields) |
| #22 | producer | `prod-tools/conda/producer-sqlite3513` = CPython `3.12.13` + #21-pinned SQLite `3.51.3` |
| #22 | canonical master (immutable) | `/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db`, SHA-256 `23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104`, size `1675415552`, mode `0400` |
| #23 | disposable validation run | `/home/skywind/hermes-recovery/restart-validations/20260813T151013Z-23-restart-2132` |
| #23 | deltas / integrity | `sessions +1 / messages +1 / gateway_routing +0`; `integrity_check ok`; `foreign_key_check 0` |
| pinned | `TARGET_COMMIT` | `4e5ad5c2230300d1ffae84b089ffc70e368c8a23` (origin/dev; accepted core `276d497764feb7d4a71f1424ed44b8958da63b16` ancestor, exactly 5 ahead) |
| pinned | expected schema / storage | `schema_version=25`, `fts_storage_version=2` |

Sources: #21 comment `5240896735`; #21 exec doc; #22 comments `5278112518` / `5280889330`; #42 note §1; #43 note §1; #23 comment `5282280504`.

**Superseded candidate — never select:** run `20260813T081559Z-4e5ad5c22303` / artifact SHA-256
`8bf88a19d64bc42e9dda0236d4a6ed92c40e7c0952dac3d78941fe1755a03d99` (SQLite 3.53.4, no CJK surface).

## 3. Production VM service / runtime inventory (from #41 + #21)

Exact production launch chain (observed on the VM, `#41` §1, §2):

```text
/etc/systemd/system/hermes-gateway.service
  -> /home/skywind5487/.hermes/scripts/hermes-tmux.sh
  -> /home/skywind5487/.hermes/scripts/hermes-owner-runtime.sh gateway run
  -> /home/skywind5487/.hermes/runtimes/cpython-3.12.13-owner-sqlite3513/python
  -> /home/skywind5487/.hermes/hermes-agent/.venv/bin/hermes
```

| Surface | Value |
|---|---|
| systemd unit | `/etc/systemd/system/hermes-gateway.service` (system, `User=skywind5487`, `Type=simple`, `Restart=always`, `RestartSec=10`, `WantedBy=multi-user.target`) |
| watchdog | `~/.hermes/scripts/hermes-tmux.sh`; default `HERMES_COMMAND=$HOME/.hermes/scripts/hermes-owner-runtime.sh gateway run`; `CHECK_INTERVAL=5` |
| control file | `~/.hermes/hermes-enabled` — `0` prevents **new** tmux creation but does **not** kill an existing tmux Gateway |
| owner wrapper | `~/.hermes/scripts/hermes-owner-runtime.sh` — injects `LD_LIBRARY_PATH` (`$RUNTIME_ROOT`), `PYTHONPATH` (`$RUNTIME_ROOT/Lib:$RUNTIME_ROOT/build/lib.linux-x86_64-3.12:$HERMES_REPO:$VENV_SITE_PACKAGES`), then `exec "$RUNTIME_ROOT/python" "$HERMES_REPO/.venv/bin/hermes" "$@"` |
| runtime selector | env `HERMES_OWNER_RUNTIME_ROOT` (drop-in `20-owner-runtime.conf` currently selects the NEW runtime) |
| Hermes checkout | `~/.hermes/hermes-agent` + `.venv/lib/python3.12/site-packages` |
| production `state.db` | `~/.hermes/state.db` = `get_hermes_home()/state.db` (`hermes_state.py::_default_db_path`, no config override at target) |
| logs | `~/.hermes/logs/gateway.log` (INFO+, gateway-component only, `mode="gateway"`); `~/.hermes/logs/errors.log` (WARNING+) |

**Stop invariant (from #41):** `systemctl is-active` can be `active (running)` while only the
watchdog shell exists — never use it alone as Gateway proof. Always verify all three: service
state, `tmux has-session -t hermes`, and `pgrep -af '[h]ermes.*gateway run'`.

**tmux server invariant (F2 fix from #21 exec):** the tmux **server** env is frozen at server
start and does **not** propagate client vars (`HERMES_OWNER_RUNTIME_ROOT` is not in the
`update-environment` list). To guarantee the next Gateway start uses the NEW runtime selector,
quiescence must use `tmux kill-server` (not just `kill-session`), so the watchdog's next
`tmux new-session` starts a fresh server carrying the drop-in env.

## 4. Runtime identity gate (re-attest on the VM before any state.db open)

Reuse the exact-runtime style from #21 Phase 5 / #43 §4, on the **NEW owner runtime** only —
never `/usr/bin/python3`, never `sqlite3 --version`, never `hermes update`:

```bash
set -euo pipefail
HOME_DIR=/home/skywind5487
NEW="$HOME_DIR/.hermes/runtimes/cpython-3.12.13-owner-sqlite3513"
REPO="$HOME_DIR/.hermes/hermes-agent"

LD_LIBRARY_PATH="$NEW" "$NEW/python" -I - <<'PY'
import json, sqlite3, sys
from hermes_state import is_sqlite_wal_reset_vulnerable
c = sqlite3.connect(':memory:')
c.execute("create virtual table __t using fts5(x)")
c.execute("create virtual table __g using fts5(x, tokenize='trigram')")
print(json.dumps({
  'python_version': list(sys.version_info[:3]),
  'sqlite_version': sqlite3.sqlite_version,
  'sqlite_source_id': c.execute('select sqlite_source_id()').fetchone()[0],
  'wal_reset_vulnerable': is_sqlite_wal_reset_vulnerable(),
  'fts5': True, 'trigram': True,
}, indent=2))
c.close()
PY
```

Gates (all mandatory): Python `3.12.13`; SQLite `3.51.3`; `wal_reset_vulnerable=False`; FTS5 +
trigram present. **CJK is optional degradation on the VM** (`cjk_loader_returned: False` in #21) —
absence is valid only when the target evaluator reports zero storage-v2 blockers.

Then verify the selector still reaches the wrapper E2E (fresh tmux server, throwaway pane), the
launcher script hashes still match §2, and `git -C "$REPO" rev-parse HEAD` is recorded.

## 5. Cutover sequence (one-shot; each phase gates on the previous)

All shell is bash on the VM (`set -Eeuo pipefail`). Artifact lives on WSL
(`/home/skywind/hermes-recovery/production-builds/…`) and is transferred to the VM below.

### Phase 0 — preflight and quiescence (STOP-able, nothing mutated)

```bash
HOME_DIR=/home/skywind5487
HERMES_HOME="$HOME_DIR/.hermes"
REPO="$HERMES_HOME/hermes-agent"
NEW="$HERMES_HOME/runtimes/cpython-3.12.13-owner-sqlite3513"
STAMP="$(date +%Y%m%dT%H%M%SZ)"
RUN="$HERMES_HOME/cutover-$STAMP"
mkdir -p "$RUN"

# 0.1 quiesce Gateway: prevent watchdog recreation, kill tmux server (fresh-server invariant),
#     stop+disable the unit
printf '0\n' > "$HERMES_HOME/hermes-enabled"
tmux kill-server 2>/dev/null || true
sudo systemctl disable hermes-gateway.service
sudo systemctl stop hermes-gateway.service || true

if sudo systemctl is-active --quiet hermes-gateway.service; then echo 'STOP: service active'; exit 1; fi
if tmux has-session -t hermes 2>/dev/null; then echo 'STOP: tmux exists'; exit 1; fi
if pgrep -af '[h]ermes.*gateway run'; then echo 'STOP: gateway process'; exit 1; fi

# 0.2 prove no state/recovery DB is open anywhere (readlink scan; never open SQLite)
: > "$RUN/open-fds.txt"
for fd in /proc/[0-9]*/fd/*; do
  t="$(readlink "$fd" 2>/dev/null || true)"
  case "$t" in *state.recovered.*|*state.db*|*cutover-*) printf '%s -> %s\n' "$fd" "$t" >> "$RUN/open-fds.txt";; esac
done
[ -s "$RUN/open-fds.txt" ] && { echo 'STOP: DB open'; exit 1; }
```

Evidence captured: `hermes-enabled=0`, unit disabled, `open-fds.txt` empty, launcher hashes,
drop-in contents, checkout HEAD, NEW runtime probe (Phase-0 output goes to `$RUN/preflight.txt`).

### Phase 1 — re-attest frozen artifact on WSL source (before transfer)

```bash
ARTIFACT=/home/skywind/hermes-recovery/production-builds/20260813T083500Z-4e5ad5c22303-cjk3513/state.production-ready.4e5ad5c22303-cjk3513.db
ARTIFACT_SHA=3a3a410f62ada5e32fc2375e657248ebb4308b688da0e65f7c85c70ceaa6818f
ARTIFACT_SIZE=1667649536
[[ "$(sha256sum "$ARTIFACT" | awk '{print $1}')" == "$ARTIFACT_SHA" ]]
[[ "$(stat -c '%s' "$ARTIFACT")" == "$ARTIFACT_SIZE" ]]
[[ "$(stat -c '%a' "$ARTIFACT")" == 400 ]]
for s in "$ARTIFACT-wal" "$ARTIFACT-shm" "$ARTIFACT-journal"; do [[ ! -e "$s" ]]; done
```

Also re-verify the frozen canonical master is unchanged (size `1675415552`, SHA-256
`23cfa3c8…48104`, mode `0400`, no sidecars) — the protected invariant per #42 §7.

### Phase 2 — align the VM checkout to TARGET_COMMIT

```bash
TARGET_COMMIT=4e5ad5c2230300d1ffae84b089ffc70e368c8a23
# VM remotes (live preflight 2026-08-15): fork=Skywind5487/hermes-agent, origin=NousResearch(upstream)
git -C "$REPO" fetch fork dev
git -C "$REPO" checkout --detach "$TARGET_COMMIT"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$TARGET_COMMIT" ]]
# record: git -C "$REPO" status --porcelain; also record the pre-checkout branch/HEAD (was
# research/external-chat-import @ 63aeddc9…, ahead 1 of fork/dev — drift from #21's ad70864f…)
```

Then reconcile dependencies (decision point — see §9). Before opening any DB, prove Hermes
imports under the owner wrapper's PYTHONPATH (owner-runtime style, `:memory:` only):

```bash
LD_LIBRARY_PATH="$NEW" \
PYTHONPATH="$NEW/Lib:$NEW/build/lib.linux-x86_64-3.12:$REPO:$REPO/.venv/lib/python3.12/site-packages" \
HERMES_HOME="$(mktemp -d)" "$NEW/python" -c 'import hermes_state, hermes_state_search; print("import-ok")'
```

If the import fails on missing/stale `.venv` deps, reconcile before proceeding (see §9) and
re-run this smoke until it passes. **No state.db open until this passes.**

### Phase 3 — transfer quiescently and hash-verify

```bash
# From WSL (source): scp (rsync NOT installed on the VM — live preflight 2026-08-15)
INCOMING="$HERMES_HOME/cutover-$STAMP/state.db.candidate"
# scp -p "$ARTIFACT" hermes:/.hermes/cutover-$STAMP/state.db.candidate  (then on the VM:)
[[ "$(sha256sum "$INCOMING" | awk '{print $1}')" == "$ARTIFACT_SHA" ]]
[[ "$(stat -c '%s' "$INCOMING")" == "$ARTIFACT_SIZE" ]]
[[ "$(stat -c '%a' "$INCOMING")" == 400 ]]
for s in "$INCOMING-wal" "$INCOMING-shm" "$INCOMING-journal"; do [[ ! -e "$s" ]]; done
```

The transfer itself must not create sidecars (verify absence of `-wal/-shm/-journal`). Record
`sha256sum` in `$RUN/transfer-evidence.txt`. A hash mismatch is a hard STOP — do not repair the
file, re-transfer and re-verify.

### Phase 4 — preserve rollback state, then swap

```bash
# 4.1 preserve the current production state.db as a COMPRESSED rollback archive (FINAL).
#     Raw original is KEPT in place until swap (4.1b "delete raw" NOT adopted); the install in
#     4.2 overwrites it, so the verified gz below is the only preserved copy of the old DB.
#     Measured gzip -c = 1,590,522,967 bytes (~1.59 GB). Disk after 2026-08-15 cleanup: 73%
#     used, ~7.8 G free → compressed rollback + 1.66 G incoming fits with large margin.
ROLLBACK_GZ="$HERMES_HOME/state.db.rollback-$STAMP.gz"
gzip -c "$HERMES_HOME/state.db" > "$ROLLBACK_GZ"
for s in -wal -shm -journal; do
  [ -e "$HERMES_HOME/state.db$s" ] && cp -a "$HERMES_HOME/state.db$s" "$ROLLBACK_GZ$s"
done
sha256sum "$ROLLBACK_GZ" > "$RUN/rollback.sha256"
sha256sum "$HERMES_HOME/state.db" >> "$RUN/rollback.sha256"   # uncompressed hash for restore verify
ls -la "$HERMES_HOME/state.db"* >> "$RUN/rollback.sha256"

# 4.2 quiescent swap: install the candidate as state.db (byte-identical, writable)
install -m 0600 "$INCOMING" "$HERMES_HOME/state.db"
[[ "$(sha256sum "$HERMES_HOME/state.db" | awk '{print $1}')" == "$ARTIFACT_SHA" ]]
for s in -wal -shm -journal; do [[ ! -e "$HERMES_HOME/state.db$s" ]]; done
```

**Irreversible boundary 1** (old DB replaced): the rollback copy and its hash must already be
captured before `install`. Evidence: `rollback.sha256` + birth-hash equality of the new
`state.db` == `ARTIFACT_SHA`.

### Phase 5 — pre-first-start verification (read-only, on the installed state.db)

Capture **all** of the following BEFORE the single controlled Gateway start. This is the
acceptance gate "evidence must be captured before the first and only controlled Gateway start":

1. Runtime identity gate (§4) output.
2. `PRAGMA integrity_check` = `ok`; `PRAGMA foreign_key_check` = 0 rows (read-only URI open).
3. `schema_version=25`, `fts_storage_version=2`; no active H/P/stale/optimize markers;
   `_fts_storage_v2_blockers()` = `[]`; `_classify_sessions_fts_trigram` = `modern_trigram`.
4. Six-index presence: `messages_fts`, `messages_fts_trigram`, `messages_fts_cjk`,
   `sessions_fts`, `sessions_fts_trigram`, `sessions_fts_cjk`.
5. Canonical counts: `sessions=7268`, `messages=231513`, `gateway_routing=78`.
6. **Routing entries**: the two previously-missing Discord thread entries are present with
   non-empty payloads (via `SessionDB.load_gateway_routing_entries()` at
   `hermes_state.py:4663`, or direct read-only SQL):

```sql
SELECT key, length(payload) FROM gateway_routing
WHERE key IN (
  'agent:main:discord:thread:1534748167571243208:1534748167571243208',
  'agent:main:discord:thread:1534748186223181824:1534748186223181824'
);
-- expect exactly 2 rows, each with non-empty payload
```

7. FTS MATCH smoke on session metadata (title/id/display_name) and a message-search route
   (`search_messages` / `_describe_search_path` at `hermes_state_search.py:2764` / `:2814`).

Use the #42 §4.1/§4.2 verifier bodies verbatim, in `mode=ro` (read-only), from the same `$RUN`
directory. **Any failure here = STOP: do not start Gateway; restore the rollback copy (§7).**

### Phase 6 — single controlled first Gateway start + startup health

```bash
printf '1\n' > "$HERMES_HOME/hermes-enabled"
sudo systemctl enable hermes-gateway.service
sudo systemctl start hermes-gateway.service
```

Health checks (all must pass):

- `sudo systemctl is-active hermes-gateway.service` is `active`;
- `tmux has-session -t hermes` true; `pgrep -af '[h]ermes.*gateway run'` shows the process;
- `~/.hermes/logs/gateway.log` and `errors.log` contain **no** forbidden signatures
  (`database disk image is malformed|malformed database schema|SQLITE_CORRUPT|FTS.*corrupt|corrupt.*FTS|vulnerable to the WAL-reset corruption bug|locking protocol|disk I/O error`) and **no**
  `state.db schema repaired` / `FTS indexes rebuilt in place` mutation lines (an in-place
  repair on the fresh artifact would itself be a STOP signal);
- Discord connectivity established; the two thread keys resolve through the gateway
  `RoutingStore` (`gateway/session.py` loads via `load_gateway_routing_entries`,
  `gateway/session.py:1316`).

Evidence: full startup log captured to `$RUN/first-start.log` + gateway.log tail + routing
resolution proof.

**Failure at any health check = immediate rollback (§7). No second speculative start.**

### Phase 7 — old-history + new-write persistence proof

1. **Old recovered history readable:** list/resume an old session (the #23 run used
   `20260429_125404_55806e`, message id `1`) and run session-title + message search that
   resolves pre-existing data.
2. **Real production write:** send one real message through production (Discord channel/thread)
   so a new session + message mutation persists via `SessionDB` (`create_session`
   `hermes_state.py:4504`, `append_message` `:8060`). Record the session/message identity and
   the count delta (expect `sessions +1 / messages +1 / gateway_routing +0` style delta relative
   to a pre-write baseline).
3. **Controlled restart:** `sudo systemctl restart hermes-gateway.service` (or stop+start).
4. **After restart:** the new write still exists; search (session-title + message, both ASCII
   `fts5` route and CJK `fts_cjk` when applicable) still resolves new and old data; routing
   still resolves; `PRAGMA integrity_check` = `ok`; `foreign_key_check` = 0; logs clean.

Evidence: `$RUN/write-evidence.json`, `$RUN/restart-evidence.json`, restart log capture.

## 6. Answers to the #44 questions

1. **What exact sequence guarantees no writer is alive during the swap?** Phase 0 quiescence
   (hermes-enabled=0, `tmux kill-server`, `systemctl stop/disable`, plus the three-state proof:
   service inactive + no tmux + no `gateway run` process) AND the `/proc/*/fd` scan showing no
   open `state.db`. The swap (Phase 4) runs only after both hold.
2. **What evidence must be captured before the first and only controlled Gateway start?** Phase 5
   full set: runtime identity gate, integrity/FK, schema/storage-v2 markers, six-index presence,
   canonical counts, the two routing entries with non-empty payloads, FTS MATCH + message-search
   smoke — plus rollback hash and birth-hash equality from Phase 4.
3. **Which failures require immediate rollback rather than another speculative start?** Any
   post-swap failure at Phase 5 (verification), Phase 6 (first-start health, routing, Discord),
   or Phase 7 (write/restart persistence, integrity, logs). The single-controlled-start contract
   means a failed first start is rolled back and investigated — never re-attempted blindly.
4. **How do we prove old recovered history, the previously missing routing entries, all required
   FTS routes, and a new production write survive restart?** Phase 7: read an old session + old
   message after restart; query the two routing keys through `load_gateway_routing_entries()` and
   the live gateway RoutingStore; run session-title/message search on both ASCII `fts5` and CJK
   `fts_cjk` routes; send a real production write before the controlled restart and re-assert it
   plus counts/integrity/FK afterward.

## 7. Rollback

Rollback is only ever triggered at an explicit STOP (Phase 5/6/7 failure) and is fully
reversible because Phase 4 preserved the old DB.

```bash
STAMP=... # the cutover stamp from Phase 0
HERMES_HOME=/home/skywind5487/.hermes
ROLLBACK_GZ="$HERMES_HOME/state.db.rollback-$STAMP.gz"

# stop Gateway first
printf '0\n' > "$HERMES_HOME/hermes-enabled"
tmux kill-server 2>/dev/null || true
sudo systemctl stop hermes-gateway.service || true

# restore the previous production state.db (decompress the preserved archive)
gunzip -c "$ROLLBACK_GZ" > "$HERMES_HOME/state.db"
chmod 0600 "$HERMES_HOME/state.db"
for s in -wal -shm -journal; do
  if [ -e "$ROLLBACK_GZ$s" ]; then cp -a "$ROLLBACK_GZ$s" "$HERMES_HOME/state.db$s"; fi
done
sha256sum "$HERMES_HOME/state.db"   # must equal the uncompressed hash in cutover-$STAMP/rollback.sha256
```

**Retention rule:** the compressed rollback archive (`state.db.rollback-$STAMP.gz` + sidecars +
`rollback.sha256`) is retained until the #24 acceptance gates (cutover + restart-persistence) are
explicitly declared complete. The candidate/swap/rollback evidence directory
`~/.hermes/cutover-$STAMP/` is the single evidence bundle for the ticket.

If the failure is confined to the checkout alignment (Phase 2), the lowest-risk rollback is to
restore the previous checkout HEAD (`git -C "$REPO" checkout --detach <previous HEAD>`) and
re-verify — no DB is touched before Phase 4.

## 8. Hard stop rules (abort, preserve run dir, do not proceed)

1. VM runtime identity differs from §2/§4 (Python ≠ 3.12.13, SQLite ≠ 3.51.3, WAL-reset
   vulnerable, FTS5/trigram missing, selector drop-in missing/wrong).
2. Launcher script hashes differ from §2, or the service topology materially differs from §3.
3. Frozen artifact (WSL) or transferred candidate hash/size/mode/sidecars mismatch.
4. VM checkout cannot be pinned exactly to `TARGET_COMMIT`, or the Hermes import smoke fails
   and cannot be reconciled.
5. Any open `state.db` fd detected before the swap.
6. Pre-first-start verification (Phase 5) fails on any item.
7. First Gateway start fails any health check, or logs any forbidden/mutation signature.
8. Post-write restart fails persistence/search/routing/integrity/FK/log checks.
9. Rollback hash does not verify after restore.

On any stop: preserve `~/.hermes/cutover-$STAMP/` as evidence, quarantine, investigate from the
run directory. **Do not resume against the frozen artifact or re-attempt speculative starts.**

## 9. Open questions / decision points (must be resolved at #24 execution, not invented here)

1. **Checkout/.venv reconciliation method on the VM — RESOLVED (live preflight 2026-08-15).**
   VM remotes: `fork` = Skywind5487 (use this for `fetch fork dev`), `origin` = NousResearch
   upstream. Current checkout: `research/external-chat-import` @ `63aeddc9…` (ahead 1 of
   fork/dev) — drifted from #21's `ad70864f…`. **`uv` IS installed** at
   `/home/skywind5487/.local/bin/uv` (and `~/.hermes/bin/uv`); it was "not on PATH" only in the
   SSH non-login shell — the **systemd unit PATH includes `/home/skywind5487/.local/bin`**, so
   the Gateway runtime sees it. **`pip` is NOT viable** (`.venv/bin/` has no pip; owner python
   `-m pip` → "No module named pip"). If the owner-runtime import smoke fails after checkout
   alignment, reconcile with `/home/skywind5487/.local/bin/uv sync --frozen`, then re-run the
   runtime gate + import smoke (confirm `.venv` interpreter stays the owner runtime; compiled
   deps must match CPython 3.12). The owner-runtime Hermes import smoke remains the hard gate
   before any state.db open. (Note: `.venv/bin/python` standalone fails to load
   `libpython3.12.so.1.0` — expected; the owner wrapper's `LD_LIBRARY_PATH` is the exact
   runtime.)
2. **Actual `gateway_routing` payloads for the two Discord threads — RESOLVED (2026-08-15).**
   Read-only verification on the frozen artifact (WSL producer runtime, `mode=ro`) confirmed both
   keys present with non-empty payloads: `…1534748167571243208:…` → `entry_json` 1410 B;
   `…1534748186223181824:…` → `entry_json` 1479 B (`gateway_routing` cols = `scope`,
   `session_key`, `entry_json`, `updated_at`). Reconstruction provenance was #20's "trusted
   surviving Hermes state". §9.2 CLOSED.
3. **Transfer transport — RESOLVED (live preflight 2026-08-15): `rsync` is NOT on the VM PATH,
   use `scp`** (source WSL → VM). The quiescent hash check on the VM keeps the transport
   irrelevant to integrity; ensure the artifact arrives with mode `0400` and no sidecars.

## 10. Acceptance checklist mapping (#24 → this runbook)

| #24 acceptance criterion | Runbook phase |
|---|---|
| VM repo on exact pinned `TARGET_COMMIT` | Phase 2 |
| Gateway launcher/runtime passes #21 attestation (non-vuln SQLite, FTS/tokenizer) | Phase 0/1/4 (§4 gate) |
| Artifact transferred quiescently, SHA-256 matches frozen local source before swap | Phase 3 + 4.2 |
| All Hermes writers/Gateway stopped before swap | Phase 0 |
| Previous VM `state.db` preserved under explicit rollback path before replacement | Phase 4.1 |
| Exactly one controlled first Gateway start after swap, startup logs captured/checked | Phase 6 |
| Gateway/Discord connectivity works; two previously missing Discord thread routing entries resolve | Phase 5.6 + Phase 6 |
| Old and recent sessions listable/resumable; session-title + message search work under pinned runtime | Phase 7.1 |
| One real production message/session mutation persists | Phase 7.2 |
| After controlled Gateway restart, new write still exists; search/routing functional | Phase 7.3–7.4 |
| Post-cutover `integrity_check ok`, `foreign_key_check 0`, no malformed/FTS/vulnerable-runtime warnings | Phase 7.4 |
| Rollback retained until acceptance gates declared complete | §7 retention rule |

## 11. Addendum — live read-only VM preflight (2026-08-15)

A read-only preflight was run on the VM `hermes` (no state.db opened, no Gateway start, no
mutation; `/proc/*/fd` scan = 0 open DB fds). Results below are live facts, not #21 snapshots.

### Confirmed identical to the pinned identities

- Service unit `hermes-gateway.service` + drop-in `20-owner-runtime.conf` → `HERMES_OWNER_RUNTIME_ROOT=…/cpython-3.12.13-owner-sqlite3513`; unit `disabled`/`failed` (stopped).
- Quiescent: `hermes-enabled=0`, no tmux server, no `gateway run` process.
- Launcher hashes: `hermes-tmux.sh` `7179386823…dbad0`, `hermes-owner-runtime.sh` `bf2024fe…70eb3`.
- NEW runtime: python SHA-256 `818c83d3…059d`; `:memory:` probe = CPython 3.12.13 / SQLite 3.51.3 / source_id `2026-03-13 10:38:09 737ae4a3…6d618` / `wal_reset_vulnerable=false` / FTS5+trigram / compile `ENABLE_FTS5`. OLD runtime retained (rollback surface).
- Current production `state.db`: `3,358,007,296` bytes, mode `0644`, no `-wal/-shm/-journal` sidecars, SHA-256 `c1c5beacfae2931f687aa20d79e25370114d4a2c54a0a4a9f6a7031d522d5ca6`. Gateway last active Aug 6 (log shows the `disk I/O error` era).

### Drift / corrections applied to this runbook

| # | Finding | Runbook impact |
|---|---|---|
| 1 | **VM git remotes:** `fork` = Skywind5487, `origin` = NousResearch upstream; checkout now on `research/external-chat-import` @ `63aeddc9…` (ahead 1), not `ad70864f…`/`dev` as #21 recorded | Phase 2 must `git fetch fork dev` (NOT `origin dev`); detach to TARGET_COMMIT is safe |
| 2 | **Disk was 86% full (~4.2 G free) — resolved by cleanup.** Removed `~/hermes_worktrees`, `~/.cache/uv`, `~/.cache/ohy`, `~/hermes-benchmark` → **73% used, ~7.8 G free**. Raw `state.db` stays in place until swap (no early delete) | Phase 4.1 FINAL: compressed rollback (measured `gzip -c` = 1,590,522,967 B ≈ 1.59 GB) + keep raw until swap; ~3.25 GB peak vs 7.8 G free → comfortable |
| 3 | **`uv` installed but invisible to SSH non-login shell**: `/home/skywind5487/.local/bin/uv` + `~/.hermes/bin/uv`; systemd unit PATH includes `~/.local/bin` so the Gateway runtime sees it; **pip unavailable** (no `.venv/bin/pip`, owner python `-m pip` → "No module named pip") | §9.1: reconcile (only if import smoke fails) with `~/.local/bin/uv sync --frozen`; re-run runtime gate + import smoke |
| 4 | **`rsync` absent on VM PATH** | §9.3: use `scp` for transfer |

### Disk cleanup executed (2026-08-15, user-authorized)

Removed on the VM: `~/hermes_worktrees` (~1.45 GB, incl. fix/title-fts5 etc.), `~/.cache/uv`
(~0.85 GB), `~/.cache/ohy` (~0.91 GB), `~/hermes-benchmark` (~1.3 GB, Aug-9 benchmark
sandbox: fork clone `lineage-gate-9425ffa2` + its worktree `session-lineage-impl` + result
archives). `free`: 4.2 GB → 7.8 GB (86% → 73%). Kept: `~/.hermes/hermes-agent` repo, both
owner runtimes, `~/.hermes/node`, `~/.hermes/lcm.db`, `~/obsidian`. No stale git worktree refs
(main repo `~/.hermes/hermes-agent` registers only itself; `git worktree prune` clean).

### Read-only artifact verification (2026-08-15) — all green, §9.2 CLOSED

Ran the Phase-5-style read-only checks on the frozen artifact with the WSL producer runtime
(CPython 3.12.13 + SQLite 3.51.3, `mode=ro`; artifact never opened writable):
`counts` sessions=7268 / messages=231513 / gateway_routing=78; `integrity_check`=`ok`;
`foreign_key_check`=0; `schema_version`=25; `fts_storage_version`=2; active markers=`[]`;
all six indexes present; **both Discord routing keys present with non-empty `entry_json`
(1410 B / 1479 B)** → §9.2 closed.

No gate semantics changed; the runbook's fail-closed boundaries are unchanged.

## 12. Execution record — cutover completed (2026-08-15)

Executed end-to-end on the VM `hermes` (user `skywind5487`). **All #24 acceptance criteria pass.**
Result posted on #24 (comment `5301718492`).

- **Phase 2**: VM checkout aligned on new branch `deploy/24-production-cutover` @ TARGET
  (`4e5ad5c22`), pushed to fork; `research/external-chat-import` & `fork/dev` untouched.
  Existing `.venv` owner-runtime import smoke passed — no `uv sync` needed (§9.1 closed).
- **Phase 3**: artifact transferred via `scp` (UNC from WSL); quiescent hash `3a3a410f…`
  verified on VM; candidate chmod 400.
- **Phase 4**: compressed rollback `state.db.rollback-20260815T065240Z.gz`
  (1,590,522,967 B; `gzip -t` ok; gunzip sha == old `c1c5beac…`); swap via `install -m 0600`;
  new `state.db` sha `3a3a410f…`, mode 600, no sidecars.
- **Phase 5**: pre-first-start verification on VM — all green (counts 7268/231513/78,
  integrity ok, FK 0, schema 25 / storage-v2 2, 6 indexes, both recovery routing keys
  non-empty, `modern_trigram`, search smoke).
- **Runtime/CJK**: VM gcc 12 present; built `libfts5_cjk.so` from TARGET source →
  `~/.hermes/lib/`; `load_fts5_cjk_extension` True (`:memory:` + cjk_unicode61 MATCH).
- **Discord fix**: `.venv` lacked discord deps (`tools.lazy_deps` failed on uv 0.11.8 venv
  resolution) → `uv pip install --target` into `.venv/lib/python3.12/site-packages`:
  `discord.py[voice]==2.7.1`, `brotlicffi==1.2.0.1`.
- **Phase 6**: single controlled first start; Discord connected as `hermes#7867`; routing
  resolves (79 rows, both recovery threads present).
- **Phase 7**: old history readable (oldest session `20260429_125404_55806e`, message id 1);
  ASCII (`fts5`) + CJK (`fts_cjk`) search works; real Discord write `20260815_172749_d5b0a3c8`
  (67 msgs) + its routing row; controlled restart → persists; post-cutover `integrity_check ok`,
  `foreign_key_check 0`, logs clean.
- **Cold-boot test**: full VM reboot → gateway auto-started (systemd enabled), Discord
  auto-reconnected, new session survived, DB opened clean.
- **Rollback disposition**: acceptance declared complete; `state.db.rollback-…gz` deleted
  (freed ~1.59 G; df 70% / 8.5 G free).

Pre-existing non-blocking observations: 3 of 79 routing rows point to sessions that no longer
exist; cron subprocess tools hit `libpython3.12.so.1.0` `LD_LIBRARY_PATH` errors (unrelated to
the cutover; owner-runtime only).
