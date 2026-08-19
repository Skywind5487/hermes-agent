# Issue #129 recon — internal salvage map for compression-aware Session Search lineage

Status: **READY FOR IMPLEMENTATION**  
Recon date: **2026-08-19**  
Target: fork issue #129, under #109  
External baseline: PR #136 (`docs/research/issue-129-session-search-lineage.md`)

## Evidence policy

This is the **internal-only** half of the #129 preflight.

- External/current-upstream facts are accepted from PR #136 and are not re-researched here.
- Internal authority is the fork's accepted #68 contract, its final review history, the completed implementation in PR #72, the #70 implementation-map research in PR #71, and current frozen fork `dev`.
- Prototype PR #63 remains donor evidence only.
- The goal is not to recover historical architecture. The goal is to identify the smallest accepted fork behavior worth reconstructing on the current-upstream seam established by PR #136.

## Pinned receipt

| Item | Pin / state |
|---|---|
| fork clean `main` / Phase-2 base | `243352e7b8bddc9f33eba1b6506810f8dd88beaa` |
| fork `dev` | `fa5ed679cc6559c619038f327e6276f4b7e8d735` |
| accepted contract | issue #68 |
| accepted implementation PR | #72, merged |
| #72 accepted head | `b2710666a29eed6fc46f3b6ce23c72f4dc766181` |
| #72 merge commit on `dev` | `2f90c254d524d618b1c866a10bb519f2c1b6f5dc` |
| #70 implementation map | PR #71 head `e0f518032d501c79ff912612d2271ce39b3c8dbe` |
| current `dev` `hermes_state_search.py` blob | `a9915780b257ee6ccc89cb09e67233fc0fb75e1e` |
| external current-upstream baseline | PR #136 head `3856ff1eece4fc89219fa69226fe4c1df8ccda30` |
| upstream pin accepted from PR #136 | `63565fa26b00a2096247064785c4380aafab2303` |

Refresh moving upstream refs again immediately before production coding, but do **not** reopen the #68 resolver design unless contradictory production evidence appears.

---

## 0. Executive result

**#129 is a real residual, and the accepted fork donor is much smaller than the historical #72 diff makes it look.**

PR #136 establishes that current upstream already owns most surrounding Session Search behavior:

- message candidate generation / FTS routing;
- ranking and source-priority behavior;
- compacted/compression-history recall;
- `/new` reset recall;
- late/bounded result hydration;
- Desktop compression-lineage search composition substantially better than the agent tool;
- list/resume compression-tip projection.

The internal accepted implementation shows that the part still worth reconstructing is the **query-scoped resolver state machine plus the narrow winner-composition rules around it**:

```text
bounded ranked raw message hits
  -> resolve each distinct owner at most once
  -> positive compression-continuation root only
  -> query-local memo/path compression
  -> exact B=2000 successful uncached row-fetch budget
  -> first displayable anchor in original ranked order
  -> root dedupe
  -> early K
  -> safe-prefix truncation on B hit
  -> deferred hydration
```

Do **not** port the old fork's whole `search_session_winners()` candidate SQL. Current upstream's `search_messages()` stack is now the authority for candidate generation.

### The correction to the first recon pass

Three boundaries are now explicit:

1. **Desktop is not a primary reconstruction target.** Per PR #136, merged upstream already has compression-aware Desktop search dedupe/composition. Treat Desktop as a parity/sabotage gate and tighten only a demonstrated residual.
2. **Exact-title parity means same root semantics, not shared query state.** #68 round-5 deliberately removed the title/current cross-lane proof protocol. The title helper may resolve separately; do not force title lookup into the winner query's memo/B/snapshot.
3. **Search identity is not the same thing as live-context ancestry.** Compression-root dedupe is strict; current-session visibility may still need generic parent ancestry to hide live branch/delegation context. Do not globally replace every parent walk with compression-only traversal.

Disposition: **READY. No new design split.**

---

## 1. What #68 actually accepted

Issue #68 froze the production resolver contract. PR #72 then survived five review rounds and merged.

The final accepted behavior is:

- generic `parent_session_id` ancestry is **not** logical conversation identity;
- only a proven positive compression-continuation edge may traverse upward;
- one query-local memo starts from candidate 1;
- successful roots and proven semantic-unresolved paths are memoized/path-compressed;
- missing parent and positive cycle fail closed;
- cycle correctness uses a traversal-local seen set, never a semantic depth cap;
- `B=2000` counts exactly successful uncached lineage-node row fetches;
- memo hits, absent-row lookup results, and local cycle checks cost zero work units;
- lookup #2000 may complete successfully;
- a required #2001 lookup is refused before it is issued;
- after B is consumed, memo-only completion and a cycle already provable from local state remain valid;
- B exhaustion is operational uncertainty, not semantic unresolved evidence;
- a B-exhausted partial path is never memoized as unresolved;
- B hit stops the ranked scan, rather than skipping the uncertain higher-ranked candidate;
- already-proven winners form a safe prefix and are retained;
- B hit is observable as truncation and must never look like a complete top-K or a confident `No matching sessions found`;
- repeated raw message hits from one owner do not cause repeated lineage resolution;
- original ranked match anchors are preserved;
- early-K is mandatory;
- current-session and exact-title exclusion use the same **root meaning** as winner dedupe;
- candidate selection plus ranking-sensitive lineage resolution use one coherent logical read snapshot;
- expensive context hydration happens after winners are chosen.

### Final round-5 scope correction

The final #68 reviewer explicitly removed a more complicated cross-lane proof protocol.

Accepted final shape:

```text
exact title
  -> resolve_compression_lineage() separately
  -> pass resolved root as exclusion semantic key

content winner query
  -> its own query-local memo/B/snapshot
  -> current raw session is re-resolved in this winner snapshot
```

The exact-title lane does **not** need to share the winner query's memo, B counter, or snapshot. Strict proof coupling across title/content when FTS fails, B is exceeded, or concurrent mutation occurs was deliberately de-scoped.

**Implementation guard:** do not resurrect `lineage_snapshot_ran`, `lineage_title_root`, `lineage_current_root`, a title/current proof gate, or equivalent machinery merely to make both lanes share one transaction.

---

## 2. Accepted donor anatomy on fork `dev`

At `dev@fa5ed679...`, the accepted #68 implementation still lives in `hermes_state_search.py` and is easy to separate into kernel, composition, and historical shell.

### 2.1 Resolver kernel — high-value direct donor

Current fork `dev` has these module-level pieces near the top of `hermes_state_search.py`:

```text
_LINEAGE_WORK_BUDGET = 2000
_UNRESOLVED
_BUDGET_EXHAUSTED
_LINEAGE_NODE_SQL
_lineage_markers(...)
_LineageResolutionState
```

`_LINEAGE_NODE_SQL` fetches enough evidence for exactly one possible child -> parent transition in one indexed lookup:

```text
child.id
child.parent_session_id
child.source
child.model_config
parent exists?
parent.end_reason
```

This narrow point lookup is still the best donor shape for current upstream. It avoids `get_session(child)` + `get_session(parent)` double reads and makes B accounting exact.

### 2.2 Positive-edge predicate — direct donor, semantics frozen

For child `c` and `p = c.parent_session_id`, the accepted resolver traverses `c -> p` only when:

1. `p` is non-null;
2. the parent row exists;
3. `p.end_reason == 'compression'`;
4. `c.source != 'tool'`;
5. `c.model_config._branched_from != p.id`;
6. `c.model_config._delegate_from != p.id`.

Important nuance: a stale/foreign marker pointing **somewhere else** does not block a valid compression continuation.

Malformed/non-object `model_config` is conservative: the positive edge is not proven, so traversal stops at the current child. It does not invent a continuation.

### 2.3 Resolver control order — direct donor, correctness-significant

`SessionSearchMixin._resolve_compression_lineage_on_conn()` uses this order inside each traversal:

```text
memo lookup
  -> local seen/cycle proof
  -> if another uncached lookup is needed, check B
  -> one indexed point lookup
  -> increment work only if a row was actually fetched
  -> decide root / positive edge / fail-closed path
```

This order is part of the contract, not an implementation preference.

It is what makes all of these simultaneously true:

- a root proven by successful lookup #2000 succeeds;
- a memo hit at work==2000 succeeds;
- a cycle already visible in the local seen set at work==2000 is classified as cycle/unresolved;
- a path needing another lookup at work==2000 is bound-hit/truncated;
- an absent child row consumes zero successful-fetch work;
- a B-limited partial path cannot poison memo state.

### 2.4 Public semantic helper — donor, but keep it small

Fork #72 exposes `resolve_compression_lineage(session_id, work_budget=...)` as a public-ish SessionDB semantic helper around the on-connection kernel.

Its value for Phase 2 is **semantic reuse**, especially exact-title exclusion and other narrow callers. Do not turn it into a second orchestration engine.

The current fork implementation returns `None` for both semantic unresolved and standalone-wrapper budget exhaustion. The winner query has richer internal state and must retain explicit bound-hit observability.

### 2.5 Current-session live-context helper — salvage concept, not universal identity

Fork #72 also added `_current_lineage_ancestors_on_conn()`.

This helper intentionally walks generic parent ancestry for **live-context visibility**, not for search identity. The reason is subtle but important:

- a branch/delegation child is a distinct search lineage;
- nevertheless, its parent's live content can still be visible to the current agent context;
- therefore current-session exclusion may need to hide live ancestors that are not in the same compression root.

This is exactly why a global `generic parent walk -> compression-only walk` replacement would be wrong.

Port this concept only if current upstream's already-merged current/reset/delegation visibility logic still needs it after the new winner seam is composed. Preserve the current upstream visibility authority first.

---

## 3. The most important review-derived winner rule: first displayable anchor

A naive owner-dedupe implementation is wrong.

During #68 review, this concrete ordering bug was found:

```text
candidate #1: current owner, live hit          -> not displayable
candidate #2: other owner, valid hit           -> displayable
candidate #3: current owner, compacted old hit -> displayable
K = 1
```

If the implementation groups by owner first and lets the current owner borrow candidate #1's rank, it may choose candidate #3 ahead of candidate #2.

The accepted fix is:

```text
iterate bounded raw hits in their actual ranked/source-priority order
  -> lazily resolve an owner only on its first encounter
  -> cache owner -> resolved root/unresolved
  -> for every later hit from that owner, reuse the owner result at zero lookup work
  -> skip non-displayable live-current hits
  -> accept the owner only when its first displayable hit is actually reached
  -> then root-dedupe and early-K
```

Therefore "distinct owning-session candidates" means **distinct resolver work**, not "throw away every repeated raw hit before visibility is known".

This preserves both:

- one resolver invocation per owner; and
- the true rank of the first eligible/displayable anchor.

This rule is essential because PR #136 says current upstream already owns compacted-history recall. #129 must not regress that merged behavior while fixing lineage identity.

---

## 4. Salvage / adapt / drop matrix

### 4.1 SALVAGE nearly verbatim

| Fork donor | Why it survives current-upstream architecture |
|---|---|
| `_LINEAGE_WORK_BUDGET = 2000` | frozen #68 contract |
| `_UNRESOLVED` / `_BUDGET_EXHAUSTED` distinction | semantic failure vs resource uncertainty |
| `_LINEAGE_NODE_SQL` narrow child+parent lookup | exact B accounting; indexed point lookup |
| `_lineage_markers()` semantics | parent-bound branch/delegate markers + foreign-marker allowance |
| `_LineageResolutionState` memo/work/bound bookkeeping | query-local state machine |
| `_resolve_compression_lineage_on_conn()` control order | exact 1999/2000/2001 semantics |
| successful path compression | repeated ancestry becomes unique-node work |
| unresolved memo for missing/cycle | fail-closed paths also reuse work |
| no memoization on B exhaustion | resource limit never becomes identity evidence |
| stop entire ranked scan on B hit | preserves ranking / safe prefix |
| user-visible truncation concept | partial result cannot masquerade as complete |

### 4.2 ADAPT to current upstream seam

| Fork donor | Adaptation required |
|---|---|
| `resolve_compression_lineage()` | retain semantic helper; fit current `SessionSearchMixin` / read-connection conventions |
| current-session re-resolution inside winner query | preserve same memo/state/snapshot for current raw id, but compose with current upstream's merged reset/delegation visibility rules |
| owner-once resolution | current upstream starts from `search_messages()` raw hits rather than fork `candidate_hits` SQL |
| first-displayable-anchor loop | use current upstream result dictionaries/order; do not port old candidate SQL |
| source-priority before early-K | preserve current `_order_for_recall()` semantics exactly, either through a tiny shared priority primitive or equivalent state-layer ordering |
| stats (`lineage_work`, memo hits, bound hit, etc.) | expose only what #129 tests/diagnostics need; names may follow current conventions |
| exact-title `_resolve_lineage()` | route to shared compression-root semantics, but remain a separate resolver call per round-5 |
| bounded first-message title anchor | salvage only if current upstream still loads a full transcript merely to construct a title result; useful but not a reason to widen #129 |

### 4.3 SALVAGE TEST INVARIANTS, NOT OLD APIs

These #72 tests are valuable as behavioral fixtures, but should be rewritten against the current upstream seam rather than preserving `search_session_winners()` merely to keep old tests unchanged:

- branch marker to parent remains distinct;
- delegate marker to parent remains distinct;
- tool child remains distinct;
- foreign marker pointing elsewhere still permits a real compression continuation;
- generic non-compression parent remains distinct;
- missing parent fails closed;
- 2-node cycle, long cycle, tail-entering-cycle fail closed;
- positive-lineage memo reuse;
- unresolved memo reuse;
- exact B 1999 / 2000 / 2001;
- cycle-at-B vs need-one-more-lookup-after-B;
- B exhaustion does not poison memo;
- 10k acyclic chain is cut by B, not depth;
- current live hit -> later compacted-history fallback;
- competing-owner displayable-anchor ordering;
- K=1/3/10 early stop;
- one logical read transaction / concurrent-writer snapshot;
- B-hit zero-winner response is incomplete, not "no matches";
- B-hit with prior safe winners returns the safe prefix.

### 4.4 DROP / DO NOT PORT

| Historical fork shape | Why to drop |
|---|---|
| whole fork `search_session_winners()` candidate/routing SQL | current upstream `search_messages()` owns content search now |
| fork Unicode/CJK/trigram/LIKE route implementation | upstream authority; #129 is not message FTS reconstruction |
| fork session-metadata FTS integrations | #128 sibling territory |
| old recursive generic-parent CTE/depth-cap machinery | exactly what #68 replaced |
| PR #63 `hermes_state_lineage.py` parallel search stack | stale duplicated architecture |
| PR #63 `B=1500` | retired by #68 |
| any TEMP-table/shared-SQL strategy framework | algorithm selection is closed |
| persistent lineage-root schema/materialization | explicitly out of scope |
| round-3/4 title/current proof protocol removed by round-5 | accepted implementation intentionally deleted it |
| global replacement of `_resolve_to_parent()` in scroll/read code | live-context ancestry has different semantics |
| Desktop pipeline rewrite | PR #136 says merged upstream already owns most Desktop composition |

---

## 5. Current-upstream target seam, using PR #136 as authority

PR #136 pins upstream `main@63565fa26...` and establishes this live agent path:

```text
_tools/session_search_tool.py::_discover
  -> db.search_messages(...)
  -> _order_for_recall(...)
  -> generic _resolve_to_parent(...) per raw owner today
  -> seen_sessions generic-root dedupe
  -> current/reset/compaction/delegation visibility exceptions
  -> anchored-view hydration
```

The residual is **not** candidate discovery. The residual is the boundary between the bounded ranked raw hits and final distinct logical winners.

### 5.1 Recommended state-layer seam

Do not let the tool perform lineage DB point lookups after `search_messages()` has already closed its read context. That would preserve the semantic fix but violate #129's coherent-snapshot contract.

The smallest robust shape is:

```text
hermes_state_search.py

search_messages(...)                # existing public API remains
  -> shared private on-connection candidate executor

new/thin winner-composition method  # name can follow current conventions
  -> with _read_ctx() as conn
  -> explicit BEGIN when needed
  -> call the SAME on-connection candidate executor
  -> preserve current source-priority ordering
  -> run query-local lineage state
  -> resolve each owner once
  -> choose first displayable anchor in actual ranked order
  -> root dedupe + early K
  -> return lightweight winners + lineage stats/truncation
```

The important architectural decision is **shared candidate execution**, not the exact helper name.

Avoid two bad alternatives:

1. `search_messages()` first, then separate `resolve_compression_lineage()` calls in the tool — snapshot split.
2. copy the current `search_messages()` FTS/ranking code into a fork-style `search_session_winners()` — duplicate search architecture.

A small extraction such as an internal `_search_messages_on_conn(...)` / equivalent is the expected first production edit if current source inspection confirms that `search_messages()` owns its own `_read_ctx()` internally.

### 5.2 Preserve `_order_for_recall()` semantics before early-K

Current upstream demotes automation/cron results before lineage dedupe. Early-K must operate on that effective order, otherwise the new resolver can change recall even when lineage semantics are correct.

Do one of:

- move the tiny stable source-priority rule into a shared pure helper usable by state + tool; or
- encode exactly the same stable priority in the thin state-layer composition.

Do **not** redesign ranking or introduce a new scoring model.

### 5.3 Tool seam

`tools/session_search_tool.py` should become orchestration, not a DB graph walker.

Expected changes:

- `_resolve_lineage()` uses the shared compression-root semantic helper for narrow standalone uses such as exact-title matching;
- `_discover()` calls the bounded winner-composition seam instead of `search_messages()` + generic root resolution itself;
- existing current/reset/compaction/delegation behavior is retained or translated into the new winner seam without semantic broadening;
- `truncated` / warning is surfaced when the DB seam reports a bound hit;
- if bound hit occurs before any winner, do not emit `No matching sessions found.`;
- hydration remains after winner selection and stays anchored to the winning raw `(session_id, message_id)`.

### 5.4 Do not rewrite `_scroll()` merely for semantic uniformity

Fork #72 ultimately kept generic-parent behavior in scroll/rebind guards because scroll is about **live context**, not search-lineage identity.

That distinction remains valid for Phase 2.

If current upstream has a generic-parent helper used by both discovery and scroll today, split the semantics at the call sites rather than changing the helper's meaning globally and silently breaking scroll behavior.

---

## 6. Current-session and exact-title parity: exact accepted boundary

### 6.1 Current session

Current-session exclusion is ranking-sensitive and belongs inside the winner query's logical snapshot.

Accepted fork behavior:

- pass the raw `current_session_id` into the state-layer winner seam;
- resolve it with the **same `_LineageResolutionState`** used by candidate owners;
- if current root is proven, candidate root comparison uses that same root meaning;
- preserve upstream live-context exceptions for compacted/ended history;
- where needed, generic current ancestors may be collected separately for live-context hiding without redefining dedupe identity;
- if current resolution itself consumes B, the winner query becomes truncated rather than pretending completeness.

### 6.2 Exact title

Exact-title matching needs semantic parity, not transaction coupling.

Accepted final behavior:

```text
_title_match_result
  -> resolve title session with resolve_compression_lineage() semantic helper
  -> if title survives current-context rules, return title slot

winner query
  -> receives the title's resolved root as an exclusion key
```

Do not require title lookup to share the content winner's memo, work counter, or snapshot.

If title resolution independently returns no proven root, keep the current fail-safe/compatibility behavior rather than inventing a generic-parent root.

### 6.3 FTS/title independence

Round-5 explicitly preserved title-only discovery when the content FTS lane is unavailable or cannot execute.

Therefore:

- content search failure must not automatically delete a valid exact-title result;
- do not make title visibility depend on winner-query proof metadata that only exists when FTS ran successfully;
- B/truncation from the content lane may coexist with an already-valid title slot.

---

## 7. Desktop disposition after the external baseline

PR #136 changes the Desktop recommendation from the first recon pass.

Merged upstream already has:

- compression-lineage dedupe in Desktop search;
- branch-specific search hits staying distinct;
- ID/content composition through one dedupe keyspace;
- live-tip/result projection.

Therefore Desktop is **not** where #129 should reconstruct the whole #68 resolver first.

### Required treatment

Add or retain sabotage/parity tests proving that current Desktop behavior does not regress when the shared semantic helper lands:

- a parent-bound branch remains a distinct search result;
- a parent-bound delegation child remains distinct if the current Desktop seam can represent it;
- a tool child is not silently folded into the parent;
- a stale/foreign marker pointing elsewhere does not incorrectly split a valid compression continuation;
- direct ID + content matches still dedupe to one compression lineage where appropriate.

Only change `hermes_cli/web_routers/sessions.py` if one of those tests goes RED on the refreshed implementation base.

### Explicit non-goal

Desktop does **not** need the agent tool's query-global B=2000 orchestration unless production evidence proves that Desktop itself performs the same unbounded ranked resolver workload. Sharing root semantics is sufficient by default.

---

## 8. RED plan — smallest useful failure sequence

A fresh implementation agent should not begin with the 10k/B fixtures. First prove the live semantic defect with tiny fixtures.

### RED 1 — generic branch must remain distinct in agent discovery

Fixture:

```text
parent.end_reason = compression
child.parent_session_id = parent
child.model_config._branched_from = parent
both contain query "needle"
```

Expected: two logical results/roots.

Current generic `_resolve_to_parent()` agent dedupe should collapse them, proving the #129 residual.

### RED 2 — generic non-compression parent must remain distinct

Fixture:

```text
parent.end_reason != compression
child.parent_session_id = parent
```

Expected: parent and child remain separate logical search identities.

### RED 3 — delegate/tool boundaries

Two fixtures:

```text
_delegate_from == parent
```

and

```text
child.source == tool
```

Expected: distinct roots.

### RED 4 — foreign marker allowance

Fixture:

```text
parent.end_reason = compression
child.parent_session_id = parent
child._branched_from = somewhere_else
```

Expected: child continues to parent root.

This prevents an over-conservative `marker exists => stop` implementation.

### RED 5 — missing parent fail closed

A candidate points to a missing parent under an otherwise continuation-looking shape.

Expected: candidate is unresolved/dropped; no fabricated root.

### RED 6 — direct positive cycle fail closed

Two nodes whose every edge otherwise qualifies as positive compression continuation point at each other.

Expected: no fabricated winner; no hang.

Once these are red, implement the kernel. Then add the exact work-bound matrix.

---

## 9. GREEN implementation sequence

### Commit 1 — RED semantic contract tests

Add current-seam agent tests for:

- branch;
- generic parent;
- delegate;
- tool;
- foreign marker;
- missing parent;
- direct cycle.

If Desktop current-main already passes equivalent branch tests, retain them as sabotage tests rather than forcing a production change.

### Commit 2 — resolver kernel + same-snapshot winner composition

Implement/adapt:

```text
_LINEAGE_WORK_BUDGET
sentinels
_LINEAGE_NODE_SQL
_lineage_markers
_LineageResolutionState
_resolve_compression_lineage_on_conn
resolve_compression_lineage
```

Then create the thin current-upstream winner seam by extracting/reusing the existing message candidate executor on one connection.

Requirements in this commit:

- one `_read_ctx()`;
- one explicit logical read transaction where the connection model requires it;
- current `search_messages` route/filter/rank behavior preserved;
- current source-priority ordering preserved;
- owner resolved once;
- repeated raw hits remain available for first-displayable-anchor selection;
- root dedupe;
- early-K;
- bound-hit stops scan;
- exact work/memo/bound stats available for tests.

### Commit 3 — tool parity + truncation

Update the agent tool:

- use winner seam;
- current raw session re-resolved in winner state;
- exact title uses same compression-root semantic helper but remains separate per round-5;
- existing compacted/reset/delegation visibility preserved;
- explicit `truncated` + warning;
- zero-winner B hit is incomplete, not no-match;
- winner hydration continues from original raw anchor.

If current exact-title code still full-loads a transcript only to discover one anchor, port the bounded `get_first_message_id()` idea here only if it stays a tiny directly coupled cleanup.

### Commit 4 — exact B / ordering / concurrency acceptance

Add the larger acceptance matrix and only the observability/cleanup needed to make those assertions precise.

No algorithm tuning in this commit.

---

## 10. Acceptance / sabotage matrix

### Positive lineage

- shallow compression continuation -> one root;
- observed real depth-14 / 15-node shape -> memo reuse;
- multiple owners entering the same positive lineage -> one logical winner;
- foreign marker pointing elsewhere -> continuation remains valid.

### Distinct boundaries

- ordinary branch;
- delegation child;
- tool child;
- generic non-compression parent;
- reset/non-compression parent behavior already merged upstream remains visible.

### Malformed graph

- missing current row;
- dangling parent;
- direct 2-node positive cycle;
- longer cycle;
- tail entering cycle;
- malformed/non-object marker config stops unproven traversal conservatively.

### Memo semantics

- successful path compression -> later owner reuse costs zero new row fetches;
- missing-parent path memoized unresolved;
- cycle path memoized unresolved;
- B-exhausted partial path not memoized;
- fresh query after a B-hit can resolve normally with a sufficient budget.

### Exact work boundary

- 1999 successful uncached row fetches -> success;
- 2000 -> success;
- would require 2001 -> stop before lookup, bound-hit;
- work==B + memo hit -> succeeds without bound-hit;
- work==B + cycle already provable from local seen -> cycle/unresolved, not bound-hit;
- work==B + one more row required to prove cycle -> bound-hit.

### Ranking / owner handling

- repeated raw hits from one owner cause one lineage resolution;
- first raw hit remains anchor when displayable;
- live-current first hit can be skipped and later compacted hit can surface;
- `cur-live #1 -> other #2 -> cur-compacted #3`, K=1 returns `other #2`;
- source-priority behavior remains identical to current upstream;
- K=1, K=3, K=10 early stop;
- larger defensive internal K only if the new state-layer seam still exposes one.

### Snapshot

- candidate selection and lineage lookups run while one explicit read transaction is active;
- concurrent writer cannot produce a torn candidate/root view;
- on non-WAL fallback, on-connection resolver never calls nested `get_session()` / `_read_ctx()` while a non-reentrant lock is held.

### Tool response

- normal search => `truncated: false` or equivalent complete-state default;
- B hit after safe winner(s) => safe prefix + `truncated: true` + warning;
- B hit before any winner => zero results + truncation warning, **no** confident no-match message;
- exact-title-only result survives an unavailable content lane per accepted round-5 compatibility;
- current-session live content remains excluded while accepted compacted/ended history stays recallable.

### Desktop parity

- branch-specific hit stays distinct;
- compression root/tip dedupe remains intact;
- ID/content composition does not regress;
- only modify Desktop production code for an actual failing strict-semantic fixture.

---

## 11. Implementation traps

### Trap 1 — "just fix `_resolve_to_parent()`"

Wrong because discovery candidate selection and later DB lineage lookups would still observe separate read snapshots, and scroll/live-context callers may rely on generic ancestry.

### Trap 2 — "port #72 `search_session_winners()`"

Wrong because it would restore a stale fork-owned FTS/ranking stack over current upstream's `search_messages()` authority.

### Trap 3 — "dedupe owners into one raw row first"

Wrong because a first live-current hit may be ineligible while a later compacted hit from the same owner is the correct displayable anchor.

### Trap 4 — "any branch/delegate marker stops traversal"

Wrong. Only a marker explicitly bound to the actual parent blocks that parent edge. Foreign/stale markers must not create false boundaries.

### Trap 5 — "at B, return the current node as root"

Wrong. Resource exhaustion is not identity evidence.

### Trap 6 — "at B, skip this candidate and keep scanning"

Wrong. The uncertain candidate is higher ranked than later candidates and might have produced a new root. Skipping it silently corrupts ranking.

### Trap 7 — "put title lookup in the same query transaction for correctness"

Rejected by #68 round-5. Same root semantics are required; cross-lane proof/memo/B coupling is not.

### Trap 8 — "make every parent walk compression-only"

Wrong. Search identity, live-context visibility, scroll/rebind behavior, list/resume projection, and Desktop routing are distinct policies even when they share compression concepts.

### Trap 9 — "Desktop needs B=2000 too"

Not established. PR #136 says Desktop already has a bounded/deduped compression-aware composition substrate. Use parity tests; do not force the agent resolver architecture into Desktop without a failing production contract.

### Trap 10 — "while here, reconstruct metadata/title FTS"

Out of scope. #128 and #130 are independent Phase-2 lines.

---

## 12. Validation commands / expected focus

The exact current-upstream test filenames should be rechecked after rebasing, but PR #136 identifies these maintained surfaces:

```text
tests/tools/test_session_search.py
tests/hermes_cli/test_web_server_session_search.py
```

Internal donor coverage also lives in:

```text
tests/test_session_search_sql_winners.py   # fork #72 donor fixtures; adapt, do not preserve API for its own sake
```

Minimum implementation validation should include:

```text
focused agent Session Search tests
focused state/search tests for the new winner seam
Desktop web-router search tests only where parity was touched
read-connection / snapshot regression if shared search execution was extracted
ruff/lint for touched Python files
git diff --check
```

The large 1999/2000/2001 and 10k-chain fixtures should be targeted tests, not a reason to broaden ordinary test-suite runtime unnecessarily.

---

## 13. Final implementation brief

A fresh implementation agent should proceed as follows:

> Start from the current upstream source pinned/refreshed from PR #136, not fork `dev`. Prove the live agent-tool defect first with a parent-bound branch fixture. Preserve current upstream `search_messages()` candidate generation, FTS routing, ranking/source priority, merged compaction/reset/delegation visibility, Desktop composition, and late hydration. Extract the smallest on-connection candidate executor needed so one new thin state-layer winner seam can run candidate selection plus lineage point lookups in one logical read snapshot. Salvage the accepted #72 resolver kernel: positive compression-only edges, parent-bound marker semantics, query-local root/unresolved memo with path compression, exact `B=2000` successful uncached row-fetch accounting, local cycle proof, no memo poisoning on B exhaustion, scan-stop safe-prefix truncation, and early-K. Resolve each owner at most once but iterate raw hits in true ranked order so the first **displayable** anchor wins. Re-resolve the raw current session inside the winner query's same memo/state/snapshot; preserve generic ancestry only where current live-context visibility requires it. Exact-title matching must use the same compression-root semantics but stays a separate resolver call per the final #68 round-5 scope. Treat Desktop as a parity gate and change it only for a demonstrated residual. Do not port the old fork candidate SQL, PR #63's parallel module/B=1500, depth caps, persistent lineage state, or metadata/title-search sibling work.

**READY FOR RED -> GREEN. No additional research or algorithm selection is required.**
