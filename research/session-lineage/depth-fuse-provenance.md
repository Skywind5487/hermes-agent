# Session-search lineage depth fuse: provenance and failure intent

Research ticket: #51  
Parent decision ticket: #45  
Builds on: #46 / PR #49 (`code-map.md`) and #47 / PR #48 (`prior-art.md`)  
Scope: archaeology only; **no production guard change**.

## Executive result

The surviving numeric guard is **fork-local**. The current `lineage_depth_cap=64` first appears in commit [`2732c47e28fbf7aaea97bd8c5cf82045a4c34159`](https://github.com/Skywind5487/hermes-agent/commit/2732c47e28fbf7aaea97bd8c5cf82045a4c34159), `wip(session-search): move winner selection into SQL` (2026-07-22). That patch did not carry a numeric cap forward from the Python dedupe path it replaced: the old path called `_resolve_to_parent()`, whose traversal used a visited set and had **no numeric depth bound**. The same introducing patch added all of the following together:

- `lineage_depth_cap: int = 64`;
- a defensive clamp to `1..256`;
- a recursive CTE `depth` column and `walk.depth < lineage_depth_cap` predicate;
- a separate delimiter-safe path/cycle predicate;
- one edge-case test covering missing parent, a cycle, and an injected `lineage_depth_cap=1`.

No primary source recovered in this pass states why the literal **64** was selected. The introducing commit message and patch contain no user-visible “64 ancestors define a lineage” claim, no SQLite-compatibility rationale, and no incident reference for a 64-deep real lineage.

The strongest evidence supports this narrower conclusion:

> **Source fact:** `64` was introduced when parent resolution moved into recursive SQL, independently of the cycle guard and independently of the pre-existing Python lineage semantics.  
> **Inference (high confidence):** its intended role is a bounded-work / sanity fuse for pathological but acyclic ancestry (and possibly generic defensive hardening), not the definition of lineage identity. The exact number remains an unrecovered implementation choice.

That distinction matters to #45. Future contenders must preserve: (1) cycle termination, (2) deterministic safe behavior for malformed/missing ancestry, and (3) a bounded-runaway-work failure mode. There is **no recovered evidence requiring every candidate to be semantically re-rooted at exactly its 64th ancestor**.

---

## 1. Pinned receipt

Research fixed point:

- fork repository: `Skywind5487/hermes-agent`
- pinned `dev`: [`311bf7d6d28b204f0aa977ddcd05d44141d2d4ba`](https://github.com/Skywind5487/hermes-agent/commit/311bf7d6d28b204f0aa977ddcd05d44141d2d4ba)
- `dev` was verified identical to that SHA at research time
- `hermes_state_search.py` blob: `15daf505aad40017b0cc7c85c94ec928e8af6684`
- `tools/session_search_tool.py` blob: `24f4d077c3bda862ba6ca74d1f14000527f8f866`
- `tests/test_session_search_sql_winners.py` blob: `9548737ae64501de33c0acb4a138a8806ae96fb6`

The earlier #46 artifact was pinned to `d72f99eb1b897dd29a46692a310aa15b1bfd77e8`. Between that pin and this research fixed point, `dev` advanced only by two unrelated recovery-research document commits; the inspected session-search blobs did not change. #51 nevertheless re-pins the current files above rather than inheriting #46's moving-branch assumptions.

### Provenance labels used here

- **UPSTREAM-MERGED-IN-BASE** — accepted upstream behavior already in the fork base.
- **FORK-DEV** — fork-only behavior surviving on `dev`.
- **FORK-HISTORICAL** — historical donor/prototype evidence, not current behavior.
- **UPSTREAM-OPEN** — unmerged upstream evidence only.

---

## 2. Every current depth-fuse code point

| Path / exact range at pinned SHA | Symbol / behavior | Numeric bound? | Relationship | Provenance |
|---|---|---:|---|---|
| `hermes_state_search.py:1307-1345` | `search_session_winners()` declares `lineage_depth_cap: int = 64`; normalizes with `max(1, min(int(...), 256))` | default `64`, accepted runtime `1..256` | **the surviving numeric guard** | **FORK-DEV** |
| `hermes_state_search.py:1576-1627` | `lineage_walk(..., depth, path)` seeds at depth 0, recurses with `walk.depth + 1`, and permits the next parent only while `walk.depth < lineage_depth_cap` | inherited from argument | same guard | **FORK-DEV** |
| `hermes_state_search.py:1576-1627` | same recursive term also requires `instr(walk.path, printf('|%s|', parent.id)) = 0` | none | **independent cycle guard**, not another use of 64 | **FORK-DEV** |
| `hermes_state_search.py:1601-1627` | `lineage_resolution` returns a repeated parent on detected cycle; otherwise deepest reached `session_id`; otherwise seed | none | fallback makes cap-hit observable as a lineage key | **FORK-DEV** |
| `hermes_state_search.py:1660-1734` | query error + stats/logging | none | **no cap-hit flag/error/telemetry**; cap exhaustion is silent | **FORK-DEV** |
| `tools/session_search_tool.py:116-148` | `_resolve_to_parent()` uses `visited: set[str]` and `while cur and cur not in visited` | **none** | independent tool-layer parent traversal; cycle-bounded but not depth-bounded | **UPSTREAM-MERGED-IN-BASE semantics**, with current fork file state |
| `tools/session_search_tool.py:692-740` | `_discover()` calls `db.search_session_winners(...)` without `lineage_depth_cap=` | none | production discovery **inherits default 64** | **FORK-DEV bridge to guard** |
| `tests/test_session_search_sql_winners.py:156-199` | `test_sql_winners_handle_missing_parent_cycle_and_depth_cap` injects `lineage_depth_cap=1` | test value `1` | tests mechanism/fallback, not literal 64 | **FORK-DEV** |

### Literal `64` versus depth semantics

At the pinned source surface, the material literal `64` is the `search_session_winners()` default. The tool layer does not contain a second hard-coded lineage depth of 64. Production `_discover()` does not override the argument, so all normal discovery calls receive the default indirectly.

The test suite's edge fixture deliberately supplies `1`; there is no regression asserting that `64` itself is a product-contract value.

### Exact hop semantics (off-by-one pinned)

The seed row has `depth=0`. A recursive step is generated when the **current** row satisfies `walk.depth < cap`, and the generated parent has `depth + 1`. Therefore a cap of `N` allows at most **N parent hops** and includes the node at depth `N`.

The test makes this explicit for `cap=1`:

```text
depth-grandchild -> depth-child -> depth-root
seed depth 0         depth 1
```

Resolution returns `depth-child`, not `depth-root`.

### Cap-hit observability

There is no `cap_hit`, `truncated_lineage`, reason code, warning, or telemetry dimension. The regular winner stats only report candidate count, unique candidate sessions, lineage count, winner count, route, and total query time. Consequently, an acyclic cap hit is currently indistinguishable to callers from an ordinary resolved lineage whose root happens to be the deepest reached node.

That is important: the cap affects externally consumed dedupe keys, but the code does **not** describe that effect as a successful semantic root computation.

---

## 3. Introduction history

### 3.1 First surviving numeric guard — `2732c47...`

**FORK-DEV**  
Commit: [`2732c47e28fbf7aaea97bd8c5cf82045a4c34159`](https://github.com/Skywind5487/hermes-agent/commit/2732c47e28fbf7aaea97bd8c5cf82045a4c34159)  
Message: `wip(session-search): move winner selection into SQL`

The patch adds `search_session_winners()` and the current recursive CTE architecture. In that same addition it introduces:

```python
lineage_depth_cap: int = 64
...
lineage_depth_cap = max(1, min(int(lineage_depth_cap), 256))
```

and:

```sql
lineage_walk(..., depth, path) AS (...)
...
WHERE walk.depth < {lineage_depth_cap}
  AND instr(walk.path, printf('|%s|', parent.id)) = 0
```

It also adds `test_sql_winners_handle_missing_parent_cycle_and_depth_cap` with `lineage_depth_cap=1`.

### 3.2 What behavior did `2732c47...` replace?

This is the strongest provenance result of #51.

The deleted `_discover()` path iterated ranked raw hits and resolved each owning session using the pre-existing Python helper:

```python
for r in raw_results:
    ...
    resolved_sid = _resolve_to_parent(db, raw_sid)
    ...
```

The helper itself walked `parent_session_id` with a `visited` set:

```python
visited = set()
cur = session_id
while cur and cur not in visited:
    visited.add(cur)
    ...
    cur = parent
return cur
```

There was **no numeric depth limit** in that replaced traversal. The patch also removed a module-global `_PARENT_CACHE`; that cache removal is separate from the new depth guard.

Therefore:

- the current SQL guard was **not** copied from the immediately replaced production parent walk;
- the old production lineage semantics were “walk until boundary/missing row/error/cycle”, not “only 64 ancestors count”;
- `2732c47...` introduced a new resource/failure mechanism while changing the execution substrate to recursive SQL.

This does not prove the author invented the number without inspiration from any other code in the world. It does prove that **the current production predecessor did not supply that policy**.

### 3.3 Later known modifiers

Two #46-pinned fork commits later changed performance-sensitive portions of `search_session_winners()` without modifying the depth guard:

- [`2d2bad204ec644455ad1273f2934f388eb4111dd`](https://github.com/Skywind5487/hermes-agent/commit/2d2bad204ec644455ad1273f2934f388eb4111dd) — `fix(session-search): defer winner snippets`; changes when FTS snippets are hydrated.
- [`9a1f477df5c8f25fd7ba4f57318e9f5ffcb2fc32`](https://github.com/Skywind5487/hermes-agent/commit/9a1f477df5c8f25fd7ba4f57318e9f5ffcb2fc32) — `fix(session-search): ranked Top-N pre-limit for relevance sort`; changes candidate limiting/planner shape.

Neither patch changes the signature default, clamp, recursive depth predicate, cycle predicate, or cap-hit resolution.

The later module split that places the method in `hermes_state_search.py` is a source-location refactor; #51 found no evidence that it altered the guard policy. The policy's semantic origin remains `2732c47...`.

### 3.4 Accompanying discussion

A narrow search in the fork found no PR attached to the exact commit message and no pre-#51 issue discussion that states why **64** was selected. The commit message itself describes moving winner selection into SQL, not introducing a 64-ancestor product rule.

**Negative result:** no primary discussion recovered a rationale such as “64 is the maximum valid lineage depth”, “SQLite only supports 64 recursive levels”, or “a real 65-hop lineage incident motivated this threshold”.

---

## 4. What failure was the guard intended to stop?

The table deliberately separates direct evidence from inference.

| Candidate failure intent | Direct source evidence | Counter-evidence | Confidence |
|---|---|---|---|
| **Cycle safety** | The same SQL patch contains a cycle fixture and explicit path-cycle predicate. Separate session-import hardening commit `ac705b52c90e114342370c3637e49c8d78b5afe6` says it detaches cyclic lineage links and guards pre-existing corrupt cycles. | Both implementations solve cycles with `visited`/`ancestors`/`seen` or path membership **without a numeric cap**. Current SQL has both mechanisms simultaneously. | **High confidence: 64 is not required for cycle termination.** |
| **Malformed/imported cyclic lineage** | `ac705b52...` explicitly treats imported/pre-existing corrupt cycles as a real defensive case. | Its solution is structural cycle detection and import detachment, not 64. | **High confidence: corruption motivates cycle guards, not the literal cap.** |
| **Missing parent** | Introducing test includes a missing-parent fixture; recursive `JOIN sessions parent` naturally stops when the parent row does not exist. | No numeric cap is needed to stop a missing edge. | **High confidence: not the reason for 64.** |
| **Pathologically deep but acyclic chain / runaway traversal work** | `2732c47...` introduces the numeric bound exactly when traversal moves from a Python loop into a recursive SQL query; clamp is defensive (`1..256`), and the test separately exercises depth. #45/#29 performance work treats parent expansions as a bounded resource. | No introducing comment explicitly says “runaway work” or explains why 64/256 were chosen. | **High-confidence inference for guard family; low confidence for literal number.** |
| **SQLite recursion constraint** | None recovered. | SQLite's official recursive-CTE documentation describes recursion termination through predicates/recursive-table `LIMIT`; SQLite's published implementation limits include expression/trigger/compound-select limits, but no 64-level recursive-CTE lineage rule. | **High confidence: no evidence for SQLite-imposed 64.** |
| **User-visible lineage semantics: only N ancestors count** | None recovered. | The immediately replaced Python resolver had no numeric depth bound; accepted/upstream parent resolution uses visited-set traversal; current cap hit is silent and unlabelled; test injects 1 rather than asserting literal 64. | **High confidence: no evidence this is a product contract.** |
| **Generic defensive hardening / sanity fuse** | The default + clamp + independent cycle predicate are a classic defensive-bounding shape; introduced with SQL execution-substrate change. | No comment literally calls it a sanity fuse. | **High-confidence inference.** |

### 4.1 Cycle hardening is demonstrably independent

Fork commit [`ac705b52c90e114342370c3637e49c8d78b5afe6`](https://github.com/Skywind5487/hermes-agent/commit/ac705b52c90e114342370c3637e49c8d78b5afe6), `fix(sessions): validate imported session payloads`, explicitly says:

- detach cyclic lineage links during import;
- guard lineage traversal against pre-existing corrupt cycles.

Its `get_compression_lineage()` changes add `ancestors` and `seen` sets. Again, there is no depth-64 mechanism.

This is unusually strong counter-evidence to collapsing the two concerns into one: Hermes already expresses “corrupt cycle” as a **visited-node invariant** elsewhere.

### 4.2 SQLite does not explain 64

Relevant official SQLite references:

- recursive CTE execution and termination: <https://sqlite.org/lang_with.html>
- documented implementation limits: <https://sqlite.org/limits.html>

SQLite documents that a `LIMIT` inside the recursive SELECT can bound how many rows enter the recursive table, and recommends explicit termination for recursive queries. Its documented general limits include values such as expression depth and trigger recursion depth, but there is no published “recursive CTE stops at 64 ancestors” constraint that would explain Hermes' number.

Therefore “64 exists because SQLite requires 64” is unsupported.

### 4.3 Upstream-open bounded traversal evidence does not establish provenance

#47 pointed to upstream-open PR [`NousResearch/hermes-agent#55640`](https://github.com/NousResearch/hermes-agent/pull/55640). Its **body** still claims lineage traversal has “depth and lookup budgets plus path compression”, but its current head (`d0e5a364584a820154c8c72f9bdd4b8d08ff6136`) no longer contains those traversal changes. A later research note on the PR explicitly warns not to treat the current head as an implementation of that historical contender.

New contribution beyond #47: this source establishes only that an independent contributor also considered **bounded lineage work** a useful defensive/performance property. It does **not** prove that fork `2732c47...` copied `64`, and its current head cannot be used as code provenance for the surviving guard.

### 4.4 Older parent-walk prior art also favors explicit cycle detection

Closed/unmerged upstream PR [`NousResearch/hermes-agent#3531`](https://github.com/NousResearch/hermes-agent/pull/3531) proposed excluding an entire current lineage in recent-session browsing. Its example parent walk keeps a `current_lineage` set and stops when the next parent is already present; it has no numeric depth bound.

New contribution beyond #47: even older structural parent-walk prior art treated cycle avoidance as a visited-set concern, not a fixed-hop lineage semantic.

---

## 5. Fixture-level behavior: cycle guard versus depth/work guard

The examples below describe the pinned SQL literally. `cap=N` means at most `N` parent hops are materialized from a seed.

| Fixture | What stops traversal? | Returned `lineage_root_id` today | Seed-distance dependent? | Is plain query-local `node -> root` memo safe if reproducing current behavior exactly? | Contract status |
|---|---|---|---|---|---|
| **Well-founded chain shorter than cap**: `C -> B -> A -> NULL` | no next parent (`JOIN` cannot continue / root has NULL parent) | `A` | no, for ordinary seeds whose remaining path fits under cap | **Yes** for completed well-founded resolution | normal semantics; covered indirectly by winner/oracle tests |
| **Well-founded chain longer than cap**: seed has more than N ancestors | `walk.depth < cap` becomes false at depth N | the **Nth ancestor**, i.e. deepest materialized node, even though it still has a parent | **Yes** — different seeds can receive different pseudo-roots | **No**, not as an unconditional true-root cache if exact per-seed cap behavior must be preserved. Remaining budget/distance matters. | mechanism tested with injected `cap=1`; literal 64 not contract-tested |
| **Direct 2-cycle**: `A -> B -> A` | path predicate refuses to re-add `A` | starting from A, resolution notices B's parent A already in path and returns `A`; starting from B analogously returns `B` | **Yes** | **No** as a path-independent component root if exact fallback is required; cycle fallback depends on traversal history/seed | cycle termination/fallback shape tested loosely (`in {A,B}`), exact canonical root intentionally not pinned |
| **Tail into cycle**: `T -> A -> B -> A` | path predicate stops before revisiting A | for seed `T`, repeated parent `A` is returned; seeds inside the cycle can produce a different member | partially/path dependent | **Do not cache as a normal resolved root** unless the algorithm defines a separate deterministic corrupt-lineage outcome | not explicitly pinned by current fixture; observed from SQL shape |
| **Missing parent before cap**: `C -> missing-row` | recursive inner join cannot find parent row | deepest existing node (`C` here) | ordinary tail to same missing edge is deterministic | generally safe as a “stopped-at-missing-edge” result if represented as such, but current API does not label it | missing-parent fallback explicitly tested |

### 5.1 Why cap-hit memoization is the trap

Suppose a long acyclic chain is:

```text
S0 -> S1 -> S2 -> ... -> S100 -> NULL
```

With `cap=64`:

- resolving `S0` returns `S64`;
- resolving `S10` returns `S74`;
- neither is the actual root `S100`.

A conventional path-compression memo wants a stable statement such as `S64 -> root=S100`, then every younger node can reuse it. But reproducing the current per-seed cap mechanism exactly would require knowing **how much budget remained when the cached node was reached**. That turns a simple `node -> root` cache into distance-aware state.

No recovered source says that complexity protects a user-visible requirement. It is therefore dangerous to promote it from accidental fallback semantics into the #45 design contract.

### 5.2 Corrupt cycles are also a poor normal-memo value

For a cycle, the current SQL does not compute a canonical connected-component representative. It returns a repeated member encountered through the current seed/path. Caching that as if it were a genuine lineage root can make later resolutions depend on which corrupt seed happened to populate the memo first.

A future contender can avoid that semantic contamination by representing cycle detection as an **exceptional / non-cacheable / explicitly classified outcome**, while still terminating deterministically. #51 does not choose that design; it only records that the current fallback is not evidence for a canonical cycle-root product concept.

---

## 6. Source fact versus inference

### Directly established source facts

1. `lineage_depth_cap=64` survives on pinned fork `dev`; clamp is `1..256`.
2. The recursive SQL contains a separate path-based cycle predicate.
3. Normal `_discover()` does not override the cap.
4. The tool-layer `_resolve_to_parent()` has cycle protection but **no numeric depth bound**.
5. `2732c47...` introduced the current SQL winner architecture and the numeric cap in the same patch.
6. The Python dedupe path removed by that patch called an unbounded-by-depth visited-set resolver.
7. The cap edge test was introduced with the SQL architecture and injects `cap=1`.
8. Current cap exhaustion is silent: resolution simply uses the deepest reached session when no cycle fallback wins.
9. Import/corrupt-lineage hardening `ac705b52...` uses explicit visited sets for cycles, without a 64 cap.
10. The checked later winner-performance commits changed candidate/snippet work, not the guard.
11. No primary source recovered here states the rationale for the literal number 64.
12. No checked upstream-accepted lineage source establishes 64 ancestors as the lineage definition.

### Inferences, clearly bounded

- **High confidence:** the numeric guard is a **resource/sanity fuse**, not the primary cycle detector.
- **High confidence:** its most plausible unique failure class is pathological but acyclic ancestry causing excessive recursive work, because cycle and missing-parent termination are already independently handled.
- **High confidence:** exact per-seed 64-hop fallback is an implementation artifact unless future evidence proves otherwise.
- **Low confidence / unrecovered:** why 64 rather than 32, 100, 128, or another value; why clamp maximum is 256.

---

## 7. Minimal safety contract for #45

This ticket deliberately does **not** choose SQL vs Python vs hybrid traversal. It narrows what any contender must preserve.

### Must preserve

1. **Cycle termination independent of a lucky depth threshold.** A corrupt loop must not recurse forever or explode work. Explicit visited sets, duplicate suppression with correct row identity, or equivalent cycle detection are admissible.
2. **Bound pathological acyclic work.** A malformed/legacy/extreme chain must not turn one search into unbounded parent expansion, latency, or memory growth. A per-path depth bound remains admissible; so does a query-wide expansion/work budget or another explicit resource fuse if it provides an equivalent or stronger failure bound.
3. **Deterministic missing-parent stop.** A broken edge must fail/stop safely rather than crash or continue through invented structure.
4. **Do not cache corrupt/incomplete resolution as an unquestioned true root.** If a contender hits a cycle, missing edge, or work fuse, its reuse layer must preserve enough outcome information to avoid poisoning later normal lineage results.
5. **Preserve normal well-founded lineage semantics.** When traversal completes within safety bounds, dedupe must use the actual lineage root under the traversal policy #45 eventually selects.
6. **Keep safety intent separate from lineage-edge semantics.** Whether generic parents or positive compression-continuation edges define the dedupe graph is a separate correctness question; a work fuse must not silently decide that policy.

### Not established as mandatory

The following are **not** supported as contracts by the recovered evidence:

- the literal number `64`;
- exactly one per-seed depth counter;
- interpreting the 64th ancestor as a genuine semantic root;
- making cycle safety depend on the depth cap;
- distance-aware memo solely to reproduce cap-hit pseudo-roots;
- any SQLite-specific 64-level recursion requirement.

### Semantically admissible guard families after #51

Without selecting among them, #45 can still evaluate:

- explicit cycle detection / visited-set protection;
- a global recursive expansion or unique-node work budget;
- the current per-path depth bound;
- deterministic exceptional/non-cacheable outcomes for corrupt or fuse-hit ancestry;
- a simpler equivalent mechanism if benchmark/correctness evidence supports it.

The selection should optimize clarity and work while preserving the failure classes above — not preserve the current counter merely because it is observable.

---

## 8. Consequences worth posting back to #45

Only three findings materially change #45's decision space:

1. **`2732c47...` originated the surviving numeric policy relative to the production path it replaced.** The predecessor Python resolver had no numeric depth bound, so exact-64 behavior is not inherited lineage semantics.
2. **Cycle safety has independent first-class evidence.** Both current SQL and import hardening use explicit path/visited detection, so a future algorithm must preserve cycle termination but need not use the depth fuse to do so.
3. **Cap-hit pseudo-root semantics should not force distance-aware memo by default.** No source proves “Nth ancestor is the product root”; bounded acyclic work is the safety contract to preserve unless new evidence surfaces.

Everything else in this artifact is supporting provenance/fixture detail and need not be duplicated into #45.

---

## 9. Answer to #51 exit questions

**Who introduced the surviving guard?**  
Fork commit `2732c47e28fbf7aaea97bd8c5cf82045a4c34159` introduced the current SQL guard.

**Upstream or fork-local?**  
**FORK-DEV.** Accepted upstream lineage semantics and the immediately replaced fork Python path do not contain the numeric 64 guard.

**Where does it live now?**  
`hermes_state_search.py:1307-1345` (default/clamp), `:1576-1627` (recursive depth predicate + fallback); production bridge at `tools/session_search_tool.py:692-740` inherits the default.

**Why 64?**  
The exact literal rationale is **unrecovered**. Primary history supports a bounded-work/sanity role but does not explain the chosen number.

**What happens on hit?**  
For an acyclic path, traversal stops after N parent hops and silently treats the deepest reached node as `lineage_root_id`; this is seed-distance-dependent.

**Product contract or defensive detail?**  
There is no evidence that exactly N ancestors define lineage. The predecessor had no numeric cap, the current tool helper has no numeric cap, cycle safety is separately implemented, and cap hits are not surfaced as a semantic condition. Treat exact-64 root truncation as a **defensive implementation detail unless contrary source evidence appears**.

**What must future algorithms prevent?**  
Infinite/repeated cycle work, unsafe malformed/missing ancestry behavior, and pathological acyclic runaway work — while preserving normal well-founded lineage resolution and not poisoning reuse with incomplete/corrupt outcomes.
