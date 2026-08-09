# Session-lineage prior art for #45

Research deliverable for [fork issue #47](https://github.com/Skywind5487/hermes-agent/issues/47), intended to constrain the decision in [#45](https://github.com/Skywind5487/hermes-agent/issues/45).

## Research pin and method

- Fork source pin: [`Skywind5487/hermes-agent@d72f99e`](https://github.com/Skywind5487/hermes-agent/tree/d72f99eb1b897dd29a46692a310aa15b1bfd77e8) (`dev` at research start).
- Upstream source pin used for current-code comparison: [`NousResearch/hermes-agent@1792e75`](https://github.com/NousResearch/hermes-agent/tree/1792e756e426fa8d84af7083dab67527da5db1c9).
- PR/issue prose was treated as a claim, not as source of truth. Merge state, current PR patch, commit ancestry, and pinned source were checked independently where they affect the conclusion.
- `in dev ancestry = yes` below means the upstream merge commit was explicitly checked as an ancestor of the pinned fork `dev`, not merely that the PR was merged upstream.

## Bottom line for #45

1. **The correctness contract is already settled more strongly than the optimization shape.** Merged [#69544](https://github.com/NousResearch/hermes-agent/pull/69544) and [#72279](https://github.com/NousResearch/hermes-agent/pull/72279) require compaction history to remain discoverable/scrollable while delegation and rewind remain excluded.
2. **Deep compression-parent chains are compatibility/fallback workload, not the default modern workload.** Merged [#49739](https://github.com/NousResearch/hermes-agent/pull/49739) introduced in-place soft archive, and merged [#52658](https://github.com/NousResearch/hermes-agent/pull/52658) made it the default. Legacy rotation still has to be correct, but benchmark weighting should not pretend every new compaction grows a new session chain.
3. **The current hot winner-selection architecture is fork-local.** At the fork pin, [`hermes_state_search.py::search_session_winners`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py) resolves each distinct candidate session upward in one recursive SQL pipeline. The same seam was not present in the pinned upstream file. #45 is therefore optimizing the fork's current pipeline, not merely choosing among old upstream Python patches.
4. **Do not use #55640 as a live lineage contender.** Its current PR body still describes depth/lookup budgets and path compression, but its current head patch (`d0e5a364…`) contains no lineage traversal change. Historical discussion may still be informative; the current open PR is not implementation authority for #45.
5. **Candidate over-fetch before lineage dedupe is deliberate accepted behavior.** Merged [#53597](https://github.com/NousResearch/hermes-agent/pull/53597) raised the discover scan from 50 to 300 and source-prioritized interactive sessions before dedupe to avoid cron starvation. The fork's current SQL preserves that shape with a separate `candidate_limit`, `result_limit`, and `source_priority`. A lineage optimization must not collapse those into `K`.

## 1. MERGED / ACCEPTED

### #12960 — compression tip projection in list/resume

- PR: [#12960](https://github.com/NousResearch/hermes-agent/pull/12960)
- Head: `684fc0ddc62cdd0aa2831c428b34e5c00e5f68df`; merge: `22efc81cd7f660bb3192ccb91aef91dfb22ca38d`.
- In pinned fork `dev` ancestry: **yes**.
- Failure addressed: a logical compression lineage could be represented by an old/root session in listing/resume even though a later compression continuation was the live/content-bearing tip.
- Implementation shape: project a compression tip while retaining a stable lineage/root identity; distinguish compression continuation from other parent/child relationships.
- Reusable invariant: **dedupe identity, display/live tip, and content owner are separate concepts**.
- Obsolete assumption to reject: `parent_session_id` alone means "same compression conversation".
- Surviving anchor: current fork winner rows explicitly carry both `session_id` (owning/content session) and `lineage_root_id` in [`search_session_winners`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py).

### #38393 — lineage dedupe while returning a usable live session

- PR: [#38393](https://github.com/NousResearch/hermes-agent/pull/38393)
- Head: `1b89715e153f3daac8f697c95f386a1a4bf0947d`; merge: `b91c382035631a07ac12606b8e19cff908a3131d`.
- In pinned fork `dev` ancestry: **yes**.
- Relevant failure: session-search results could duplicate a compression lineage or surface an identity that was not the useful live session.
- Implementation shape: dedupe by lineage root while returning the live compression tip/session plus lineage-root metadata.
- Reusable invariant/test: multiple physical sessions in one compression lineage count as one logical result, but the result still needs a separately usable owning/live identity.
- Surviving anchor: fork `search_session_winners` partitions by `lineage_root_id` but returns `owning_session_id` as `session_id`.

### #49739 — in-place soft archive

- PR: [#49739](https://github.com/NousResearch/hermes-agent/pull/49739)
- Head: `cd0b3c69c94357b717d842bcfe2ad67d8c31c888`; merge: `69716a2e6f7cb101ea52a350df6f9dce92cb89a5`.
- In pinned fork `dev` ancestry: **yes**.
- Failure/design pressure: rotating to a fresh session on every compaction creates lineage/session-id churn and makes human history harder to reason about.
- Implementation shape: archive prior active rows in place as `active=0, compacted=1`, then store the compacted active set under the same session id.
- Reusable invariant/test: the persisted state has three materially different classes: live (`active=1`), compaction archive (`active=0, compacted=1`), and rewind/hidden (`active=0, compacted=0`).
- Rejected assumption: "historical" or `active=0` rows are all equivalent and can all be hidden.
- Surviving anchors: [`hermes_state.py`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state.py) archive/compaction storage plus search predicates that include compacted rows.

### #52658 — in-place compaction becomes the default

- PR: [#52658](https://github.com/NousResearch/hermes-agent/pull/52658) (salvage/superseder of unmerged #51959)
- Head: `8a4757b5053c629a5c62d606034d280587bb98b5`; merge: `0654319644bd76e848c85cdb8822d551ca9b764d`.
- In pinned fork `dev` ancestry: **yes**.
- Implementation shape: flips the default to in-place compaction and fixes the guard to key off the actual compaction result.
- Reusable benchmark implication: modern/default sessions commonly have **zero parent hop added by compaction**; legacy rotation/degraded fallback can still produce deep chains and remains a correctness fixture.
- Rejected assumption: benchmark corpus dominated by repeated rotating compactions represents the present default workload.

### #53597 — over-fetch and source-prioritize before lineage dedupe

- PR: [#53597](https://github.com/NousResearch/hermes-agent/pull/53597)
- Head: `97538f4836c396d33b26cca7d0522140d3dfba25`; merge: `fe1c1c1121002166da38fc254a4fe977aa4da071`.
- In pinned fork `dev` ancestry: **yes**.
- Failure addressed: repetitive cron vocabulary dominated top FTS rows; early lineage dedupe could consume the small scan budget before an interactive session was ever inspected.
- Implementation shape: stable source-class rerank before lineage dedupe and scan limit `50 -> 300`; cron is demoted, not excluded.
- Reusable invariant/test: **candidate budget is intentionally wider than result budget** and ranking/source policy happens before lineage collapse. Keep cron-only recall too.
- Surviving anchor: current fork SQL derives `source_priority`, limits a wider `candidate_hits` set, then partitions/dedupes by lineage before the final `LIMIT` in [`hermes_state_search.py`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1430-L1660).

### #69544 — compacted history is searchable without opening delegation/rewind

- PR: [#69544](https://github.com/NousResearch/hermes-agent/pull/69544)
- Head: `7916e4e95e4488217f661847ec19cf043058578c`; merge: `e907ecccefaee9c38e4e6e968cd3822eb16fb146`.
- In pinned fork `dev` ancestry: **yes**.
- Failure addressed: child/archived compression history could match FTS but disappear during session-level resolution/filtering.
- Implementation shape: `_resolve_to_parent` carries whether a compression hop occurred; in-place compacted history and legacy compression history are allowed, delegation and rewind are not.
- Reusable invariant/test: resolving a root is not enough; traversal needs enough semantic information to decide whether the hit is legitimate history.
- Rejected assumption: all descendants of a root, or all inactive rows, are equally recallable.
- Surviving anchors: [`tools/session_search_tool.py::_resolve_to_parent`, `_discover`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py) and compacted-row predicates in the DB search path.

### #72279 — scrolling obeys the same history boundary

- PR: [#72279](https://github.com/NousResearch/hermes-agent/pull/72279)
- Head: `a19ef23d00e95c8c5d40c14d150f52db3840a297`; merge: `b93fd077c0652a53d66231f5aeaa701e242692dc`.
- In pinned fork `dev` ancestry: **yes**.
- Failure addressed: discovery could surface compaction history but scroll still rejected it.
- Implementation shape: allow scroll for compaction-archived rows or legacy compression-ended owners, while active current/delegation and rewind remain rejected.
- Reusable invariant/test: winner grouping and hydration/scroll semantics must agree; an optimization that finds a "correct" root but makes the winning hit unscrollable is not equivalent.
- Surviving anchor: [`tools/session_search_tool.py::_scroll`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py).

## 2. OPEN / UNMERGED

### #55640 — current PR body is stale relative to current head

- PR: [#55640](https://github.com/NousResearch/hermes-agent/pull/55640)
- Current head: `d0e5a364584a820154c8c72f9bdd4b8d08ff6136`; state: **open, unmerged**.
- In pinned fork `dev` ancestry: **no**.
- The current PR body still advertises lineage depth/lookup budgets, path compression, and early stop.
- Independent current-head patch inspection found only compaction-summary recognition/content shaping/title-recall payload bounding; **no lineage traversal change is present in the current 3-file patch**.
- Reusable lesson: old review/commit history may be useful evidence, but #45 must not cite the present #55640 head as an implementation of bounded traversal or path compression.
- Rejected assumption: PR body == current patch.

A separate open [#70903](https://github.com/NousResearch/hermes-agent/pull/70903) mentions a bounded ancestor walk for human transcript recovery. It is not retained as authority here: it is unmerged and solves transcript reconstruction, not ranked top-K session-search grouping.

## 3. CLOSED / SUPERSEDED / UNMERGED

These are useful mostly as failure-history and test/invariant sources, not as current architecture authority.

### #5447 — root identity accidentally used as hydration identity

- PR: [#5447](https://github.com/NousResearch/hermes-agent/pull/5447); head `dac7edeecf43fe910ed34d798cd2a479e9b06949`; closed unmerged; ancestry **no**.
- Failure/shape: a child hit was resolved to its root for both dedupe and hydration; if the root had no useful matching rows the logical result vanished.
- Keep: **root/dedupe key != content owner/hydration key**.
- Reject: parent relationship alone proves compression continuation.
- Survives through the separate `lineage_root_id` and `session_id` fields in current fork winners.

### #6256 — exact-current exclusion is too weak as a full semantic rule

- PR: [#6256](https://github.com/NousResearch/hermes-agent/pull/6256); head `8c3fbf7c0bf1d66830bad60094a87a89d58b98444`; closed unmerged; ancestry **no**.
- Shape: stop excluding the whole lineage and exclude only the exact current session to recover compression parents.
- Keep: compression-parent recall fixture.
- Reject: exact-current exclusion alone is sufficient; it can expose delegation/live descendants and lacks the later compacted-vs-rewind distinction.

### #13501 -> #13841 — derive compression-hop semantics during the traversal already being paid for

- [#13501](https://github.com/NousResearch/hermes-agent/pull/13501), head `28a517bf48742dd31831ebd71404e524994e7706`, closed unmerged, ancestry **no**: added a separate `_is_compression_session` lookup.
- [#13841](https://github.com/NousResearch/hermes-agent/pull/13841), head `7d54a8fc6392e8db04f3eae531a568d3aecfe221`, closed unmerged, ancestry **no**: folded `has_compression_hop` into the parent walk and added multi-level coverage.
- Keep: if traversal can derive required semantic metadata at negligible marginal cost, do not add another per-candidate lookup.
- Later accepted form: #63144 -> #69544.

### #19438 -> accepted #53597 — candidate starvation before dedupe

- PR: [#19438](https://github.com/NousResearch/hermes-agent/pull/19438); head `ea7dba10d828715788c60e20924ae67784f3f237`; closed unmerged; ancestry **no**.
- Diagnosis included cron dominance and too-small pre-dedupe scan budget.
- Keep: starvation fixture and the principle that lineage dedupe needs a sufficiently broad, correctly ordered candidate set.
- Accepted descendant for this sub-problem: #53597.

### #2201 / #3531 — structural parent walk, but old whole-lineage exclusion semantics

- [#2201](https://github.com/NousResearch/hermes-agent/pull/2201), head `237371b9052a9477785d3756f0f282f9a0f4d334`, closed unmerged, ancestry **no**: exclude the full current lineage.
- [#3531](https://github.com/NousResearch/hermes-agent/pull/3531), head `7cdcee204454dfd6de23ab5fbf10c684163b22ee`, closed unmerged, ancestry **no**: replace a non-structural root heuristic with an actual parent walk plus cycle protection.
- Keep from #3531: structural parent traversal and cycle safety.
- Reject from both as current semantics: blanket whole-lineage exclusion, because later accepted compaction history inside that lineage is intentionally searchable.

### #24111 — positive compression-edge detection via lifecycle evidence

- PR: [#24111](https://github.com/NousResearch/hermes-agent/pull/24111); head `c9a3e393d6528cbe135b3d1a53774320435e11c2`; closed unmerged; ancestry **no**.
- Shape: identify a compression continuation with parent lifecycle/end reason plus timing rather than treating every parent edge alike.
- Keep: explicit positive evidence for "compression continuation" is safer than generic parent linkage.
- Reject: timing heuristic as a durable primary invariant; modern lifecycle/storage behavior has since changed substantially.

### #27593 — explicit current-lineage recall mode

- PR: [#27593](https://github.com/NousResearch/hermes-agent/pull/27593); head `8478b774709d65f3bfcb316e2c64fda82cf8fdab`; closed unmerged; ancestry **no**.
- Shape: explicit `in_session` behavior for current-lineage recall.
- Keep only as API/UX history; it does not choose the traversal algorithm for #45.

### #51959 -> accepted #52658 — do not mistake the seed PR for the merged default flip

- PR: [#51959](https://github.com/NousResearch/hermes-agent/pull/51959); head `62cf58ee522700e990378391378383e019b1430c`; **closed unmerged**; ancestry **no**.
- The intended default flip was salvaged and actually merged as #52658.
- Rejected assumption from #47's seed list: presence in the historical chain means the exact PR was accepted.

### #63144 -> accepted #69544

- PR: [#63144](https://github.com/NousResearch/hermes-agent/pull/63144); head `dbb0bb7c05a5a60f85dcd2f3eced27efe448aba2`; closed unmerged; ancestry **no**.
- Direct precursor to the compacted-history recall fix, later salvaged into #69544.
- Keep: regression/test history around archived compression hits and delegation/rewind separation.
- Architecture authority: #69544, not this closed head.

## 4. FORK-LOCAL / DEV

### `hermes_state_search.py::search_session_winners`

- Source snapshot: [`d72f99e/hermes_state_search.py`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py).
- Status: **present in pinned fork `dev`; absent from the corresponding pinned upstream source inspected during this research**.
- Exact introduction commit was not recovered from the available primary-history query, so this document intentionally uses the verified source snapshot SHA rather than inventing provenance.
- Current shape:
  - candidate scan is wider than result `K`;
  - candidate rows are ranked/source-prioritized before lineage collapse;
  - `lineage_seeds` dedupes candidate session ids;
  - recursive `lineage_walk(seed_session_id, ...)` then walks upward **independently per seed** with `UNION ALL`;
  - safety fuse is `lineage_depth_cap` (currently 64 in the caller/default path) plus an explicit path-string cycle guard;
  - `lineage_resolution` picks a safe root/fallback per seed;
  - window partitioning picks one winner per resolved root;
  - snippets/hydration are deferred until final winners where possible.
- The repeated-work target for #45 is therefore precise: overlapping seed paths do not currently share parent-resolution work inside `lineage_walk`.
- Important non-target: do not erase the existing candidate-order/source-priority/owner-vs-root behavior while changing traversal scheduling/reuse.

### #29 algorithm research is evidence, not accepted prior art

[#29](https://github.com/Skywind5487/hermes-agent/issues/29) has useful synthetic comparisons among per-seed traversal, shared recursive `UNION`, Python memoization, stateful/lazy SQL, priority-queue SQL, staged path collision, and hybrids. Its current notes also flag correctness-oracle gaps around cap/cycle/missing-parent behavior.

For #45, use it to construct contenders and measurements, **not** to infer upstream acceptance. Production-shaped measurement should remain downstream of the final candidate path (#14), because candidate narrowing changes how much lineage work exists to optimize.

## Seed audit

Every seed named by #47 was classified; independently discovered accepted work is listed separately.

| Seed | Classification | Disposition for #45 |
|---|---|---|
| #5447 | closed/unmerged | keep owner-vs-root invariant |
| #6256 | closed/unmerged | keep compression-parent recall fixture; reject exact-current-only rule |
| #13501 | closed/unmerged | separate semantic lookup is superseded |
| #13841 | closed/unmerged | keep "derive metadata during traversal" idea |
| #19438 | closed/unmerged | keep starvation diagnosis; accepted descendant #53597 |
| #2201 | closed/unmerged | old whole-lineage exclusion now too coarse |
| #3531 | closed/unmerged | keep structural walk + cycle protection |
| #12960 | merged/accepted | root/tip/owner identity separation |
| #24111 | closed/unmerged | positive compression-edge evidence; timing heuristic stale |
| #27593 | closed/unmerged | API/history evidence only |
| #38393 | merged/accepted | lineage dedupe while returning usable live/owner identity |
| #51959 | closed/unmerged | superseded by merged #52658 |
| #55640 | open/unmerged | current head is **not** a lineage implementation despite stale body |
| #63144 | closed/unmerged | salvaged by merged #69544 |
| #69544 | merged/accepted | authoritative search-history boundary |
| #72279 | merged/accepted | authoritative scroll/hydration boundary |

Independently found because behavior search was not limited to the seed list:

- [#49739](https://github.com/NousResearch/hermes-agent/pull/49739): merged in-place soft archive foundation.
- [#52658](https://github.com/NousResearch/hermes-agent/pull/52658): merged default flip / salvage of #51959.
- [#53597](https://github.com/NousResearch/hermes-agent/pull/53597): merged over-fetch/source-priority fix / accepted descendant of #19438's starvation diagnosis.

## Concrete constraints this adds to #45

### Correctness oracle

- Treat `lineage_root_id` as a grouping key, not automatically as the row/session to hydrate.
- Preserve separate owning/content session identity.
- Compaction archive and legacy compression history are recallable; delegation and rewind are not.
- Winner discovery and scroll/hydration must agree on the same semantic boundary.
- Preserve cycle and missing-parent fail-safe behavior; malformed lineage must terminate deterministically.
- Do not assume a unique downward compression child. Upward parent traversal is the safer semantic basis.

### Contender interpretation

- Current **per-seed recursive SQL** is the fork baseline, not an upstream-accepted optimum.
- A **shared recursive traversal** or **Python query-local memo** should be judged as fork-local optimization contenders; there is no evidence here that upstream accepted or rejected either as the final winner-grouping design.
- #55640 should be removed as evidence that "path compression is already being proposed upstream" unless a specific historical commit/review is cited separately.
- Keep `K` dynamic. Historical fixed-three result behavior does not justify a hardcoded three-stage traversal.
- Keep candidate scheduling/order separate from parent-work reuse; accepted #53597 shows ordering before dedupe changes correctness/recall.

### Benchmark shape

Measure at least two lifecycle regimes instead of one synthetic chain shape:

1. **Modern/default in-place:** many candidate sessions with no compaction-created parent hop, plus archived rows within the same session id.
2. **Legacy/fallback rotation:** shallow and deep compression-parent chains, including clustered candidates sharing ancestry.

Also retain:

- cron-heavy/starvation fixtures with `candidate_limit > K`;
- current-lineage exclusion and archived-history fixtures;
- delegation and rewind negatives;
- cycle and missing-parent fixtures;
- dynamic `K` (not only `K=3`);
- parent-expansion / parent-lookup counts and unique nodes visited, not wall-clock latency alone;
- production-shaped candidate distributions after #14, because reducing candidate count can reduce or eliminate the payoff of a more complex memo/traversal scheme.

The depth cap should remain a **safety fuse** in tests until compatibility evidence establishes it as intended user-visible semantics; an algorithm should not win merely by doing less work after silently cutting off a valid legacy lineage.
