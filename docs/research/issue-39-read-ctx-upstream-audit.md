# Research #18 / #39 — read-only FTS/search paths onto `_read_ctx()`

Date: **2026-08-21**  
Status: **research complete; ready for `/recon` handoff**  
Canonical research artifact: this file (the #39 exploration artifact is refreshed in place rather than duplicating facts under a second #18 note).

Pinned trees:

- fork integration target: `Skywind5487/hermes-agent@dev`
- fork source SHA: **`fa5ed679cc6559c619038f327e6276f4b7e8d735`**
- fork `hermes_state.py` blob: **`ff2e072890a3879b3cfda09853a2068aa5766490`**
- fork `hermes_state_search.py` blob: **`a9915780b257ee6ccc89cb09e67233fc0fb75e1e`**
- upstream reference: `NousResearch/hermes-agent@main`
- upstream SHA at this audit: **`fc9cbc872d8050c22f1192b16bc5ff4aed471e10`**

Requesting issue: #18 — Move read-only FTS search paths onto SessionDB read connections.  
Exploration issue: #39.  
Related fork architecture: #12, #14, #27, #30.

---

## 查到什麼

### 1. Executive conclusion

**Verified:** #18 is no longer a broad “move FTS MATCH statements off the writer” task. The fork already absorbed the original upstream read-path split, and the principal message/session FTS candidate lanes already use `_read_ctx()`.

The implementation-ready delta is four ordered nodes:

1. **A — import the accepted bounded read-connection pool first.** The fork still uses one permanent reader per `(SessionDB × thread)`. Sending more call sites through `_read_ctx()` before fixing lifetime increases descriptor exposure.
2. **B — move the remaining pure search/lookup projection reads** off `self._lock`/`self._conn`, especially `get_compression_tip()` which reintroduces writer contention after the main session picker query has already used `_read_ctx()`.
3. **C — fix fork-only `SessionDB(read_only=True)` message-CJK serving parity.** The read-only constructor can load `cjk_unicode61` and discovers session-CJK serving state, but never sets message `_fts_cjk_available`, so a healthy cross-profile read-only DB silently loses `messages_fts_cjk`.
4. **D — add RED-first contention, lifecycle, and semantic regression coverage.** This must prove the target reads do not require the writer lock under WAL, while preserving the conservative non-WAL fallback and exact result semantics.

Dependency: **A → B/C → D**. B/C may be implemented independently after A; D closes both.

### 2. Current fork read-path baseline

**Verified from `dev@fa5ed679…`:**

- `SessionDB.__init__` still creates `threading.local()` plus a strong `_read_conns` set.
- `_get_read_conn()` (`hermes_state.py:L2771…`) opens a `mode=ro` connection only when WAL is active and caches it for the life of that worker thread; failures become a sticky per-thread `failed` flag.
- `_read_ctx()` (`hermes_state.py:L2824…`) yields that independent reader without `self._lock`, otherwise falls back to `with self._lock: yield self._conn`.
- every writable-mode read connection applies the fork DB pragmas and loads `cjk_unicode61` when the writer loaded it.
- `tests/test_session_db_read_path_split.py` still explicitly asserts the old per-thread contract (`test_read_conn_is_per_thread`, `test_read_conn_reused_within_thread`).
- `tests/test_session_db_read_conn_pool.py` does **not** exist on fork `dev`.

This means the fork has the concurrency split but not the accepted lifecycle bound.

### 3. Search/FTS inventory

#### Already on `_read_ctx()` — no conversion required

**Verified:** the main read-only search paths already use the split connection, including:

- `messages_fts` Unicode MATCH;
- message trigram and CJK candidate reads;
- message LIKE fallback and the unindexed rebuild-gap supplement;
- search-context enrichment reads;
- `sessions_fts` Unicode metadata candidates;
- `sessions_fts_trigram` normalized candidates;
- `sessions_fts_cjk` candidates;
- canonical metadata LIKE fallback / routed row-id candidate lookup;
- `get_session()`, `get_session_by_title()`, and primary `list_sessions_rich()` SQL;
- resume/history reads brought in by upstream #77803.

So #18 should **not** touch these merely to produce churn.

#### Remaining pure reads that are safe to move

All line numbers below are pinned to fork source SHA `fa5ed679…` / the blobs above.

| Priority | Seam | Current behavior | Why it belongs in #18 | Target |
|---|---|---|---|---|
| P0 | `hermes_state.py:L7678-L7737 :: SessionDB.get_compression_tip` | takes `self._lock` for every chain hop and reads `self._conn` | session picker/list projection already got candidates via `_read_ctx()`, then this helper re-enters the global writer convoy once per root/hop | one `_read_ctx()` lease for the walk; optional caller-owned `conn` helper if projection can share the snapshot |
| P1 | `hermes_state_search.py:L1802… :: SessionDB.list_recent_user_messages` | writer lock + SELECT | `/rewind`/`/undo` are interactive picker/search-adjacent reads | `_read_ctx()` |
| P1 | `hermes_state.py:L6752-L6777 :: SessionDB.resolve_session_id` prefix fallback | exact branch uses `get_session()` → `_read_ctx()`, prefix branch falls back to writer lock | one resolver currently has two different concurrency semantics | `_read_ctx()` |
| P1 | `hermes_state.py:L7099… :: SessionDB._like_numbered_variants` | writer-lock LIKE fallback | fallback is entered exactly when FTS is unavailable/unsuitable; fallback must not reintroduce the convoy the FTS lane avoids | `_read_ctx()` |
| P2 | `hermes_state.py:L6945-L6952 :: SessionDB.get_session_title` | writer-lock point lookup | pure projection read | `_read_ctx()` |
| P2 | `hermes_state.py:L7635-L7676 :: SessionDB.get_next_title_in_lineage` read half | writer-lock SELECT of existing titles | pure read; the API already releases the lock before any later write, so moving it does not weaken an existing atomicity guarantee | `_read_ctx()`; atomic allocation+write is a different concern |

The P0 finding is the high-value seam: an audit that only greps explicit `MATCH` SQL misses the convoy because the expensive re-lock happens in **projection after search**.

#### Must remain writer/transaction coupled

Do **not** mechanically replace every `self._lock`:

- `get_meta()` when used by rebuild/write flows may need transaction-local/uncommitted state; a WAL reader only sees committed state.
- `_fts_table_exists()` and schema/capability probes that run inside optimize/rebuild/teardown are coupled to mutation under the same writer critical section.
- SELECTs inside `_execute_write()` callbacks are read-before-write transaction logic.
- FTS `optimize`/`rebuild`/`merge`, DDL, `VACUUM`, and checkpoints are writes.
- non-WAL, unknown-WAL, read-open failure, and pool-saturation paths must retain the locked writer fallback.
- unrelated pure reads elsewhere in SessionDB are outside #18; do not turn this into a repository-wide lock refactor.

### 4. Fork-only read-only CJK capability gap

**Verified in `hermes_state.py:L2504…` (`SessionDB(read_only=True)` branch):**

The constructor SELECT-only probes:

- `messages_fts` → `_fts_enabled`;
- `messages_fts_trigram` → `_trigram_available`;
- `sessions_fts`;
- canonical/owned/non-stale `sessions_fts_trigram`;
- then loads `cjk_unicode61` onto **this read-only connection** and computes session-CJK serving availability, while correctly keeping `_sessions_cjk_worker_operable = False`.

But `_fts_cjk_loaded` and `_fts_cjk_available` are initialized False and are never promoted for this read-only attach. Message CJK search gates on `_fts_cjk_available`, therefore cross-profile `SessionDB(read_only=True)` cannot serve a healthy `messages_fts_cjk` lane even when tokenizer loading succeeded.

Required contract:

- “can serve CJK from this connection” is separate from “can mutate/build CJK indexes”;
- read-only attach may advertise **message CJK serving** only after tokenizer + table + durable pending/stale checks succeed;
- it must never advertise worker operability merely because the tokenizer loaded;
- failure is fallback, not crash and not a false-positive capability;
- ideally share a SELECT-only capability helper so writable pooled readers and read-only attach do not drift again.

This is fork-specific; upstream cannot directly solve it because upstream does not own the fork's three session-metadata FTS lanes.

### 5. Upstream prior art — accepted, superseded, and still open

#### Already merged upstream **and already present in the fork**

| PR | State | Meaning for #18 |
|---|---|---|
| NousResearch/hermes-agent#73344 | merged | original `_get_read_conn()` / `_read_ctx()` split and major recall/search conversions; fork already absorbed it |
| #76895 | merged | `SessionDB(read_only=True)`, read-only FTS probing, no read-only checkpoint; fork already absorbed and then extended it |
| #77803 | merged | resume/history reads moved to `_read_ctx()`; fork already absorbed it |

No cherry-pick.

#### Accepted upstream **but absent from the fork**

**#83406 is merged and its bounded pool is present in current upstream main `fc9cbc872…`.** Current main constructs `_read_pool = queue.LifoQueue(maxsize=_READ_POOL_MAX)`, a lifetime `BoundedSemaphore`, an instance-wide read-open backoff, and a checkout/return lifecycle. Past the cap, reads intentionally degrade to the locked writer connection instead of opening descriptor N+1.

The SessionDB part of the merged bundle preserves contributor commits that can be imported without dragging unrelated Desktop orphan/nofile work:

1. `9cc5c463404d18fc3c9628363a44c4e7d7cacd2c` — pool SessionDB readers instead of one permanent reader per thread.
2. `5eaabc9e14d8784e7faf26b7491f9da5a73bc94a` — bound **peak live** readers with a permit; fixes partial-open/permit leak paths.

Both are required. Commit 1 alone bounds only returned idle connections; a cold N-reader burst can still open N simultaneously. Commit 2 is the load-bearing peak bound.

**Recommended import:** cherry-pick those two contributor commits in order, resolve fork-only FTS conflicts, and do not import the unrelated remainder of the #83406 bundle.

Preserve during conflict resolution:

- `apply_database_pragmas(..., db_label="state.db")` on every reader;
- all fork six-index capability state;
- connection-local CJK tokenizer load;
- non-WAL/read-open/pool-saturation locked-writer fallback;
- contributor attribution and commit order.

#### Closed unmerged / superseded

| PR | Classification | Why not import directly |
|---|---|---|
| #76700 | closed, unmerged; **salvaged into merged #83406** | original pool implementation is provenance; #83406 is the accepted integration |
| #81082 | closed, unmerged; explicitly **closed as superseded** | author states current main independently landed the same lifecycle fix and peak bound; no unique diff remained |

These are useful design/test history, not current transplant targets.

#### Open / not accepted — evidence only

| PR | Current state at audit | Relevance |
|---|---|---|
| #73803 | open, unmerged | missed handoff reads and shared-writer error-state race; validates the structural rule but is outside #18 search scope |
| #90734 | open, unmerged (2026-08-20) | fresher broader evidence: four unlocked reads on the shared writer can surface bare `SystemError` and kill a persisted turn; proposes `_read_ctx()` + a scoped retry defense |
| #85255 | open, unmerged | pooled read connection poisoning (`file is not a database`) eviction; adjacent lifecycle robustness after the pool, not part of #18 |
| #87044 | open, unmerged | conservative rule: unknown journal mode must not enable lock-free WAL pool; useful test idea, not accepted upstream yet |

Do not cherry-pick an open PR merely because it is newer. For #18, current merged main plus the two preserved #83406 contributor commits are the authority.

### 6. RED-first validation map

Implementation must begin with tests that fail on `dev@fa5ed679…` for the intended reason.

#### A. Bounded-pool contract

Carry/adapt the accepted upstream regression suite rather than writing weaker lookalikes:

- 150 short-lived threads do not retain one reader per historical thread;
- a simultaneous burst above `_READ_POOL_MAX` peaks at the cap, not N;
- saturation uses locked-writer fallback instead of opening N+1;
- pooled readers are exclusively leased and reusable cross-thread;
- close drains idle readers; an in-flight return cannot repopulate a closed pool;
- open failure backs off but can recover later;
- CJK extension failure after open does not leak the connection/permit;
- unexpected exceptions do not strand a permit;
- fallback test patches the single checkout seam, not a helper that a warm pool can bypass.

The existing `test_read_conn_reused_within_thread` must be replaced with the post-pool contract (“successive `_read_ctx()` leases can reuse a connection”), not merely deleted.

#### B. Remaining convoy tests

Under WAL, hold `db._lock` from thread A, execute each target read in thread B, and require completion **before releasing** the writer lock:

- `resolve_session_id()` prefix case;
- `_like_numbered_variants()` fallback;
- `get_session_title()`;
- `get_next_title_in_lineage()` read phase;
- `list_recent_user_messages()`;
- `get_compression_tip()` including multi-hop chain.

For P0, add an integrated session-list/search projection test so a future edit cannot leave the explicit FTS query lock-free while reintroducing a lock in the compression-tip projection.

#### C. Read-only capability parity

Build/seed a database with healthy message Unicode/trigram/CJK and session Unicode/trigram/CJK surfaces, reopen with `SessionDB(read_only=True)`, and assert:

- message CJK serving is available when tokenizer/table/state permit it;
- message CJK search actually returns the CJK row (not merely a flag assertion);
- read-only attach never marks session CJK worker operable;
- missing tokenizer, pending rebuild, or stale breadcrumb fail closed to fallback;
- no DDL/mutation occurs on the mode=ro connection.

#### D. Semantics and measurement

Pin result equivalence before/after migration:

- exact/ambiguous prefix resolution;
- numbered variant filtering (`#N`, not `#bar`), escaping `%`, `_`, `\\`;
- compression-chain tip choice;
- recent-user-message ordering/filtering;
- read-your-committed-writes;
- non-WAL locked fallback.

For the issue's performance criterion, measure writer latency/contention under concurrent search. A useful acceptance comparison is writer p50/p95/p99 with the target reads hammering in parallel, plus the pool-exhausted fallback arm. The research environment cannot execute the fork, so no new local benchmark number is claimed here.

---

## 查不到什麼

1. **No fresh local runtime benchmark was produced.** This research pass has repository/PR access but not a checked-out GitHub worktree connected to the fork runtime. Upstream PR measurements are therefore treated as author-reported evidence, not re-labeled as our measurement.
2. **Open upstream proposals are not authority.** #73803, #90734, #85255, and #87044 may change or close; their current bodies are useful failure evidence only.
3. **No repository-wide `docs/research/README.md` / catalog exists on current `dev`.** `docs/research/` itself is the navigation surface. Therefore there is no canonical catalog file to update without inventing a new convention during this ticket.

## 為什麼查不到

- Executing contention/load tests requires a runnable checkout and platform SQLite behavior; the GitHub connector exposes source/history/PR state, not a runtime process.
- Merge status is knowable and was checked directly; future disposition of open PRs is not.
- The repo currently has research notes but no dedicated research index file, so “update catalog” is **not applicable** rather than silently skipped.

---

## 研究者自我檢驗

- **Primary-source first:** current fork source, current upstream source, merged PRs/commits, and PR closure comments were checked directly.
- **Freshness:** the upstream pin was refreshed on 2026-08-21 to `fc9cbc872…`; the old 2026-08-16 research pin is no longer used for upstream status.
- **Absorption check:** #73344/#76895/#77803 are baseline, not work items; #83406 pool is absent from fork source; #76700/#81082 are not mistaken for merged authority.
- **Scope check:** this does not broaden #18 into every locked SQLite read. Transaction/schema reads and unrelated surfaces remain out of scope.
- **Fork-specific check:** six-index lifecycle research (#27/#33/#34 lineage) was read before deciding the read-only CJK capability rule; session-CJK worker-vs-serving separation is preserved.
- **Test-quality check:** REDs assert behavior at the public/seam boundary (completion while writer lock is held, real CJK search result, peak simultaneous reader count), not tautological implementation details alone.
- **Unknowns are explicit:** no local performance number is invented; open upstream work is marked unaccepted.

---

## 結論與下一步

#18 is implementation-ready after `/recon` turns this research into pinned edit seams.

Recommended change tree:

```text
A. Accepted bounded read pool (#83406 contributor commits)
├── preserves six-index connection-local capability setup
├── rewrites old per-thread tests into pool/peak tests
└── enables safe expansion of _read_ctx usage
    ├── B. Remaining pure search/projection reads
    │   └── highest value: get_compression_tip projection convoy
    └── C. read_only=True message-CJK serving parity
        └── preserve worker-operable=false
D. Integrated semantic + contention regressions
```

Implementation should proceed RED → GREEN in that order. Do **not** start by mass-replacing locks, and do **not** import open upstream proposals as if merged.

### Backlinks / handoff

- requesting issue: https://github.com/Skywind5487/hermes-agent/issues/18
- exploration issue: https://github.com/Skywind5487/hermes-agent/issues/39
- research PR: https://github.com/Skywind5487/hermes-agent/pull/94
- spec path when requested: `/to-spec` should consume this file + `RECON FINAL @ fa5ed679cc6559c619038f327e6276f4b7e8d735`
- ticket path when requested: `/to-tickets` should consume the same pinned recon; no additional research split is recommended
- recon backlink: to be added as the #18 issue comment labelled `RECON FINAL @ fa5ed679cc6559c619038f327e6276f4b7e8d735`
