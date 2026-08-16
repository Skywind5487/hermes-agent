# Research #39 — read-only search / FTS paths onto `_read_ctx()` + upstream cherry-pick audit

Date: 2026-08-16

## Scope

Research-only follow-up for fork issue #39 / implementation issue #18. This document does **not** implement #18.

Pinned trees:

- fork integration target: `Skywind5487/hermes-agent@dev`
- fork BASE_SHA: `35c8564c9c0af3d75bcbdf1d793e7207e5528f06` (the #14 merge commit)
- upstream reference: `NousResearch/hermes-agent@main`
- upstream reference SHA at audit time: `d5773bfc3ad32148f0ff2e1de975fc94e37a0335`

Questions answered:

1. Which read-only search/FTS paths still serialize behind `SessionDB._lock` for no correctness reason?
2. Which reads must remain on the writer connection because they are transaction/schema-lifecycle coupled?
3. Are the tokenizer and capability flags correct on every read connection for all six fork indexes?
4. Which merged upstream implementation should #18 cherry-pick rather than reimplement?

## Executive result

The main FTS MATCH lanes are **already** routed through `_read_ctx()` on the pinned fork tree. #18 should therefore not be implemented as a broad “replace every FTS query” sweep.

The remaining useful work is narrower and more concrete:

1. **Import upstream's accepted bounded read-pool implementation first.** The current fork still retains one read-only connection per `(SessionDB × thread)`. Moving more reads to `_read_ctx()` before bounding this makes the FD-exhaustion exposure worse.
2. **Move the remaining pure lookup/projection reads** that still take `self._lock`, especially `get_compression_tip()`. `list_sessions_rich()` already runs its main candidate/list query on `_read_ctx()`, then re-enters the writer lock once per compression-chain hop during tip projection.
3. **Fix fork-only read-only capability parity:** `SessionDB(read_only=True)` loads `cjk_unicode61` for session CJK discovery but leaves message `_fts_cjk_available == False`, so cross-profile read-only message search cannot use `messages_fts_cjk` even when the index exists and is healthy.
4. Preserve the conservative non-WAL fallback and transaction-coupled writer reads.

---

## Upstream cherry-pick audit

### Already present in `dev` — do not cherry-pick again

| upstream PR | merged commit | purpose | fork ancestry result |
|---|---|---|---|
| NousResearch/hermes-agent#73344 | `f228e145ba35cbbf785eded2021ae6682285b91b` | original WAL read-path split (`_get_read_conn` / `_read_ctx`) and main recall/search conversions | ancestor of `35c8564c`; already absorbed |
| #76895 | `e38055a85e242dd999809155bf4f7d472508102d` | `SessionDB(read_only=True)` / dashboard read-only behavior and FTS capability probing | ancestor of `35c8564c`; already absorbed |
| #77803 | `67d4bbb812cca491cde220b1e571cbaecc412681` | routes four session-resume/history reads through `_read_ctx()` | ancestor of `35c8564c`; already absorbed |

These are design baseline / regression history, not pending imports.

### Merged upstream and absent from fork — cherry-pick these first

Upstream PR #83406 is merged, but the PR is a **bundle** containing unrelated Desktop orphan-reap and runtime `nofile` work. Do **not** cherry-pick the whole PR merge.

The relevant accepted contributor commits are the first two commits of #83406, in this order:

1. `9cc5c463404d18fc3c9628363a44c4e7d7cacd2c` — `state: pool SessionDB read connections instead of leaking one per (SessionDB x thread)`
2. `5eaabc9e14d8784e7faf26b7491f9da5a73bc94a` — `state: bound PEAK read connections with a permit, not just pooled returns`

Both are required. The first replaces `threading.local()` + the unbounded strong set with a reusable LIFO pool. The second fixes an important flaw in the first revision: queue `maxsize=8` only bounded *returned idle* connections, while a cold burst could still open N simultaneous readers. The follow-up adds a lifetime `BoundedSemaphore`, so open + checked-out readers together are capped at 8; saturation degrades to the existing locked-writer fallback instead of risking `EMFILE`.

The second commit also closes two failure-path leaks relevant to this fork:

- if loading the CJK extension fails after opening a read connection, close that partial connection;
- release the read permit on non-`sqlite3.Error` failures so capacity cannot ratchet down permanently.

Recommended import attempt:

```bash
git cherry-pick 9cc5c463404d18fc3c9628363a44c4e7d7cacd2c
git cherry-pick 5eaabc9e14d8784e7faf26b7491f9da5a73bc94a
```

Expected conflict surface: `hermes_state.py` and read-path tests, because the fork has additional session-FTS capability state after the upstream commits diverged.

Conflict-resolution constraints:

- preserve the fork's `apply_database_pragmas(conn, db_label="state.db")` on every newly opened read connection;
- preserve all fork session-index capability fields (`_sessions_fts_available`, `_sessions_trigram_available`, `_sessions_cjk_worker_operable`, `_sessions_cjk_available`);
- preserve the fork's CJK extension loading on every pooled read connection;
- preserve attribution and the upstream two-commit sequence;
- do not pull the later unrelated #83406 commits merely to make the cherry-pick easier.

### Not accepted upstream — evidence only, not cherry-pick candidates

- #73803 remains open. It is useful prior art for broader handoff/read routing but is not an accepted upstream implementation.
- #86608 (`discard poisoned read-conn on 'file is not a database'`) was closed without merge on 2026-08-15. Do not import it as “official”.
- upstream issues #86515 and #86516 expose adjacent read-path hazards, but they are open issues, not merged fixes. Keep their cases as robustness test ideas / follow-ups rather than widening #18.

---

## Pinned fork read-path baseline

### Current acquisition seam

`hermes_state.py:2771` — `_get_read_conn()`:

- only creates a separate read connection when `_wal_active` and the instance itself is writable;
- current implementation caches one connection in `threading.local()` and pins it in `self._read_conns` until `close()`;
- opens `mode=ro` and applies `apply_database_pragmas()`;
- loads `cjk_unicode61` when `_fts_cjk_loaded` is true.

`hermes_state.py:2824` — `_read_ctx()`:

- WAL + available read connection: no `self._lock`;
- non-WAL / read-open failure: shared writer connection under `self._lock`;
- `SessionDB(read_only=True)`: `_get_read_conn()` deliberately returns `None`, so `_read_ctx()` uses that instance's already-read-only `_conn` under its own lock. This is fine; a read-only attach does not need a second read pool.

The non-WAL fallback is a correctness boundary and must remain conservative.

---

## Search/FTS inventory

### Already on `_read_ctx()` — no #18 conversion needed

On the pinned fork, the principal search lanes already use `_read_ctx()`:

- ordinary `messages_fts` MATCH reads;
- `messages_fts_trigram` reads;
- `messages_fts_cjk` reads;
- message LIKE fallback;
- deferred message rebuild-gap supplement (`_search_unindexed_gap`);
- search context enrichment reads;
- session Unicode metadata candidates (`sessions_fts`);
- session normalized trigram candidates (`sessions_fts_trigram`);
- session CJK candidates (`sessions_fts_cjk`);
- metadata canonical-LIKE fallback / routed row-id candidate lookup;
- `get_session()` / `get_session_by_title()` and the primary `list_sessions_rich()` SQL;
- resume/history paths previously converted by upstream #77803.

Therefore the remaining convoy is mostly in helpers wrapped around these lanes, not in the MATCH statements themselves.

### Safe-to-move remaining reads

| priority | pinned source | function / read | current behavior | why safe / why it matters | recommended seam |
|---|---|---|---|---|---|
| P0 | `hermes_state.py:7678` | `get_compression_tip()` | each chain hop takes `self._lock` and queries writer `_conn` | pure SELECT; `list_sessions_rich()` calls it once per compression root and potentially multiple hops, so an otherwise lock-free picker/search re-enters the writer convoy in projection | run on `_read_ctx()`; preferably allow caller-supplied `conn` / `_get_compression_tip_on_conn()` so one projection snapshot can service all hops |
| P1 | `hermes_state_search.py:1802` | `list_recent_user_messages()` | `self._lock` + writer SELECT | pure SELECT used by `/rewind` / `/undo` picker; interactive read can serialize behind unrelated writes | `_read_ctx()` |
| P1 | `hermes_state.py:6752` | `resolve_session_id()` prefix fallback | exact lookup uses `get_session()` (`_read_ctx`), but prefix fallback returns to writer lock | pure bounded SELECT; inconsistent seam inside one resolver | `_read_ctx()` |
| P1 | `hermes_state.py:7099` | `_like_numbered_variants()` | fallback title LIKE uses writer lock | pure SELECT and specifically runs when FTS is unavailable/failed; fallback should not reintroduce the convoy the FTS path avoided | `_read_ctx()`; optional caller `conn` if resolver wants one snapshot |
| P2 | `hermes_state.py:6945` | `get_session_title()` | writer lock | pure point lookup | `_read_ctx()` |
| P2 | `hermes_state.py:7635` | `get_next_title_in_lineage()` read half | writer lock around existing-title SELECT | pure SELECT. Existing API already releases the lock before any later title write, so moving this read does not weaken an atomicity guarantee that exists today | `_read_ctx()`; allocation+write atomicity would be a separate issue |

Integrated hot spot: in `list_sessions_rich()` around the compression-tip projection block, the primary/pinned list queries already use `_read_ctx()`, then every compression root calls `get_compression_tip()`. This is the most important #39 finding because merely auditing explicit FTS MATCH statements would miss it.

### Must stay on writer / transaction-coupled seam

| surface | reason |
|---|---|
| `get_meta()` | accepted upstream design intentionally keeps this on `self._lock`: rebuild/write callers can need the writer connection's transaction-local/uncommitted state; a WAL reader sees committed state only |
| `_fts_table_exists()` when invoked inside optimize/rebuild/teardown | capability/table-existence check is coupled to schema mutation under the same writer critical section; moving the probe to a different read snapshot can race the lifecycle it is validating |
| SELECTs inside `_execute_write()` callbacks | they are read-before-write transaction logic (title uniqueness, session metadata update, deletion selection, rebuild state, etc.); keep them on the transaction connection |
| FTS `optimize` / `rebuild` / `merge`, trigger/table DDL, `VACUUM`, WAL checkpoint | mutations, not read-path work |
| non-WAL fallback | `_read_ctx()` must continue serializing on writer lock when WAL is not known active |

Other pure locked reads exist elsewhere in `SessionDB` (Telegram bindings, cleanup counts, etc.), but they are outside #18's search/FTS scope. Do not turn #18 into a repository-wide SQLite lock refactor.

---

## Six-index capability / tokenizer audit

Fork-owned search surfaces:

1. message Unicode: `messages_fts`
2. message trigram: `messages_fts_trigram`
3. message CJK: `messages_fts_cjk` (`cjk_unicode61`, connection-local extension)
4. session Unicode: `sessions_fts`
5. session normalized trigram: `sessions_fts_trigram`
6. session CJK: `sessions_fts_cjk` (`cjk_unicode61`, same connection-local extension)

### Writable `SessionDB` + `_read_ctx()`

Current `_get_read_conn()` is mostly correct for tokenizer parity: each new mode=ro reader applies DB pragmas and, when the writer has `_fts_cjk_loaded`, loads `cjk_unicode61` onto that **specific** read connection. This requirement must survive the upstream pool cherry-pick because loadable FTS tokenizers are connection-local.

Unicode61 and trigram do not require a separate loadable extension.

### `SessionDB(read_only=True)` gap

The fork's read-only constructor currently:

- probes `messages_fts` → `_fts_enabled`;
- probes `messages_fts_trigram` → `_trigram_available`;
- probes `sessions_fts`;
- classifies/probes `sessions_fts_trigram` and its stale state;
- loads `cjk_unicode61` into the read-only connection;
- uses that local `cjk_loaded` to probe `sessions_fts_cjk`, while explicitly keeping `_sessions_cjk_worker_operable = False`.

But it never promotes the **message** CJK serving flags. `_fts_cjk_loaded` / `_fts_cjk_available` start False and remain False on this branch. Search routing later gates `messages_fts_cjk` on `_fts_cjk_available`, so cross-profile `SessionDB(read_only=True)` silently loses the message-CJK lane even if the same connection successfully loaded the tokenizer and the table is healthy.

Required #18 behavior:

- distinguish **can serve this index on this read-only connection** from **can mutate/build/repair it**;
- allow a healthy read-only connection to advertise message CJK *serving* capability after checking table + durable pending/stale state;
- never mark a read-only attach as a CJK rebuild worker merely because it loaded the tokenizer;
- preserve the existing `_sessions_cjk_worker_operable = False` discipline for read-only instances.

This is fork-specific; upstream main cannot be cherry-picked to solve it because upstream does not have the fork's three session metadata FTS surfaces.

---

## Ordered implementation plan for #18

### Commit 1 — import the accepted bounded read pool

Cherry-pick, in order:

- `9cc5c463404d18fc3c9628363a44c4e7d7cacd2c`
- `5eaabc9e14d8784e7faf26b7491f9da5a73bc94a`

Resolve only fork-divergence conflicts. Preserve fork pragmas and six-index state. Bring the upstream pool/peak-bound regression tests with the commits rather than rewriting equivalent tests from scratch.

Rationale for ordering: #18 increases `_read_ctx()` usage. The fork's current per-thread strong-set design leaks retained reader connections by historical worker-thread count; fix the acquisition lifetime before sending more call sites through it.

### Commit 2 — remove the remaining search/lookup writer convoys

Convert the P0/P1/P2 pure reads in the table above. For compression projection, prefer a connection-aware helper rather than opening a fresh checkout on each hop:

- `_get_compression_tip_on_conn(conn, session_id)` or `get_compression_tip(..., conn=None)`;
- `list_sessions_rich()` may hold one read context / explicit snapshot for the projection group where practical;
- avoid holding a read connection across unrelated Python work longer than needed.

Do not touch transaction-coupled or non-search reads merely because they also use `self._lock`.

### Commit 3 — complete read-only six-index serving parity

Fix `SessionDB(read_only=True)` so message CJK capability is discovered and served safely, using the same durable stale / rebuild-pending semantics as the writable search surface. Keep read-only worker-operability false.

If a shared helper can express “probe serving capability on this connection” without mutating schema, use it to prevent writable/read-only capability rules from drifting again.

### Commit 4 — contention + semantic regressions / cleanup

Add integrated tests, validation docs, and only then any small helper cleanup exposed by the preceding commits.

---

## RED tests before implementation

### A. Accepted upstream read-pool contract

Carry upstream tests that prove:

- 150 short-lived reader threads do not leave one reader pinned per historical thread;
- 64 simultaneous readers peak at `_READ_POOL_MAX`, not 64;
- pool saturation falls back to the writer connection rather than opening reader N+1;
- `close()` drains idle readers and in-flight returns cannot repopulate a closed pool;
- a failed read-only open backs off then self-heals;
- CJK extension-load failure after open does not leak the connection / permit;
- an unexpected exception does not strand a permit.

### B. Writer-convoy RED unit tests

Under WAL, manually hold `db._lock` from the test thread, launch another thread for each pure read, and assert the read finishes **while the writer lock is still held**:

- `resolve_session_id()` prefix case;
- `_like_numbered_variants()`;
- `get_session_title()`;
- `list_recent_user_messages()`;
- `get_next_title_in_lineage()`;
- `get_compression_tip()`.

The current pinned tree should block on these paths; the #18 implementation should not.

### C. Integrated picker/projection convoy test

Construct a surfaced compression root with a continuation tip and metadata that matches the picker query. Hold `db._lock`, call the searched `list_sessions_rich()` path from another thread, and assert it can complete and return the projected tip before releasing the writer lock.

This catches the real regression #39 found: FTS candidates can already be lock-free while `get_compression_tip()` makes the end-to-end picker block anyway.

### D. Semantic parity tests

For each converted helper, pin output semantics:

- exact / unique-prefix / ambiguous-prefix session ID resolution;
- numbered-title fallback rejects `"foo #bar"` and accepts integer `#N` continuations;
- compression-tip child precedence remains unchanged;
- `/rewind` recent-user list continues excluding bookkeeping `display_kind` rows and respecting `include_inactive`;
- `get_next_title_in_lineage()` numbering is byte-for-byte unchanged.

### E. Six-index / connection-local CJK tests

On a host where `cjk_unicode61` is available:

1. create/populate the writable database and all applicable message/session indexes;
2. reopen with `SessionDB(read_only=True)`;
3. assert the six search-serving flags reflect healthy present indexes;
4. run a CJK message query and compare returned IDs with the writable instance;
5. borrow more than one pooled read connection and verify CJK MATCH works on each connection (tokenizer registration is connection-local).

The CJK-specific test must skip cleanly when the extension is unavailable; absence of the optional extension is a supported degraded environment.

### F. Non-WAL fallback guard

Force / construct a non-WAL case. Holding the writer lock should still block `_read_ctx()` consumers; after release, results must match the WAL case. #18 must not turn “read-only” into “lock-free under every journal mode”.

---

## Validation commands

At minimum after implementation:

```bash
scripts/run_tests.sh tests/test_session_db_read_conn_pool.py
scripts/run_tests.sh tests/test_session_db_read_path_split.py
scripts/run_tests.sh tests/test_hermes_state.py
scripts/run_tests.sh tests/tools/test_session_search.py
python scripts/check-windows-footguns.py hermes_state.py hermes_state_search.py tests/test_session_db_read_conn_pool.py tests/test_session_db_read_path_split.py
git diff --check
```

Also run the fork's issue-specific session metadata FTS / picker suites touched by #14/#25/#26/#30, plus the new #18 regression file(s). Do not declare parity from upstream tests alone: upstream does not exercise the fork-only three session indexes.

---

## Explicit non-goals

- no broad replacement of every `self._lock` in `SessionDB`;
- no change to write-transaction reads;
- no weakening of non-WAL fallback;
- no resurrection of retired `simple` tokenizer paths;
- no redesign of title-allocation atomicity;
- no import of unrelated #83406 Desktop/runtime changes;
- no cherry-pick of open or closed-unmerged upstream PRs merely because their patches look useful;
- no handling of upstream #86515/#86516 beyond noting their risks / possible follow-up tests.

## Bottom line

#18 should be a **small seam-completion change built on a newer accepted upstream read-pool**, not a new FTS architecture. The fork already routes its six candidate/search lanes through `_read_ctx()`; the work left is (a) make `_read_ctx()` safe to use more broadly under long-running thread pools, (b) remove the lookup/projection convoy around those lanes, and (c) finish read-only CJK capability parity for the fork-only index set.
