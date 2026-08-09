# Session-search lineage execution map (#46)

> Research-only artifact for #46 / #45. This document maps current behavior and provenance; it does **not** select or implement a #45 lineage algorithm.
>
> Method: current source + immutable git objects + merged/open upstream PRs + tests + official SQLite documentation. Issue text and prior handoffs were treated as search pointers, not as evidence.

## 0. Pinned research receipt

Research date: 2026-08-09.

| Item | Immutable pin |
|---|---|
| fork `dev` | [`d72f99eb1b897dd29a46692a310aa15b1bfd77e8`](https://github.com/Skywind5487/hermes-agent/commit/d72f99eb1b897dd29a46692a310aa15b1bfd77e8) |
| current upstream `main` observed at research time | [`1792e756e426fa8d84af7083dab67527da5db1c9`](https://github.com/NousResearch/hermes-agent/commit/1792e756e426fa8d84af7083dab67527da5db1c9) |
| fork/upstream merge-base | [`91937a6dc3ffbbe2f3be91a500f0ecf962c4cf53`](https://github.com/Skywind5487/hermes-agent/commit/91937a6dc3ffbbe2f3be91a500f0ecf962c4cf53) |
| active decision ticket | #45, open at research time |
| code/provenance ticket | #46, open at research start |
| historical ADR | #29, closed; historical evidence only |

Do not conflate the upstream SHA observed on 2026-08-09 with the fork merge-base. The fork has its own post-base history, and upstream has advanced independently.

Pinned blobs inspected:

| path | blob |
|---|---|
| `tools/session_search_tool.py` | `24f4d077c3bda862ba6ca74d1f14000527f8f866` |
| `hermes_state_search.py` | `15daf505aad40017b0cc7c85c94ec928e8af6684` |
| `hermes_state_common.py` | `c0c0ff8908e99c7a7bef182398db062f0c9128b6` |
| `hermes_state.py` | `2710a54b139a75ec304051900c8e0820d18d1bb0` |
| `hermes_state_portability.py` | `decf8d3d8ad7312d1507a0ac5474d1ac6bdb9dff` |
| `agent/conversation_compression.py` | `b2d052d7889ef612d8ae4c4f286e772ab26b7178` |
| `tests/tools/test_session_search.py` | `c5c64635de37702dddb892f2e1e23cafd16953f3` |
| `tests/test_session_search_sql_winners.py` | `9548737ae64501de33c0acb4a138a8806ae96fb6` |

### Provenance labels used below

- **UPSTREAM-MERGED-IN-BASE** — upstream-accepted behavior whose merge commit is an ancestor of pinned fork `dev`.
- **FORK-DEV** — fork-only/current fork behavior after the relevant upstream base.
- **FORK-HISTORICAL** — old fork prototype/donor behavior that is not the current implementation.
- **UPSTREAM-OPEN** — unmerged upstream proposal; evidence only.

## 1. Findings that change the #45 problem statement

These are the high-leverage findings; the detailed evidence follows.

1. **Public K is dynamic but bounded to 1..10.** `session_search()` clamps the caller-visible `limit` to `[1, 10]`. `_discover()` then gives the DB `K` of `limit - 1` when an exact-title result occupies a slot. The DB helper's `0..100` clamp is an internal defensive range, not the public tool contract.
2. **Current SQL does not actually traverse a compression-only relation.** `lineage_walk` follows every `parent_session_id`. It does not require `end_reason='compression'` and does not reject branch/delegation/tool child edges. Calling its output a “compression lineage” therefore overstates what current code proves.
3. **The generic-parent assumption exists twice.** Tool-layer `_resolve_lineage()` also walks generic parents and is used for the current-session root and exact-title lane. Fixing only the DB CTE would leave title/current exclusion semantics on a different root function.
4. **The repeated-work source is exact and local.** `candidate_hits` becomes `DISTINCT owning_session_id` seeds, then `lineage_walk` carries `seed_session_id` in every recursive row and uses `UNION ALL`. Shared ancestors reached from two seeds are therefore separate rows; merely swapping `UNION ALL` for `UNION` would still not merge rows whose seed differs.
5. **Current compaction topology is mostly flat.** In-place compaction keeps one durable session ID and creates no parent edge. Parent-chain traversal is needed primarily for legacy rotation/history and for other generic parent relationships.
6. **Cycle protection and the depth fuse are separate mechanisms.** Search has an explicit visited-path cycle guard; import also rejects/detaches cycle-forming parent edges. The exact historical rationale for the numeric default `64` was not recovered from a primary source. Preserve bounded work as a safety requirement; do not promote “64 hops” into lineage identity without new evidence.
7. **The active test surface contains static drift and also encodes blind-parent grouping.** Some tests no longer observe the active code path, and the SQL-winner lineage fixture collapses a plain parent edge whose parent was never compression-ended. Benchmark-v2 needs an intent-level oracle before comparing algorithms.

## 2. One-screen macro flow

```text
session_search(query, limit, sort, current_session_id, ...)
  |
  |-- session_id + around_message_id --> SCROLL
  |-- session_id only ----------------> READ
  |-- no query -----------------------> BROWSE
  `-- query --------------------------> DISCOVERY
                                         |
                                         v
                              clamp public limit 1..10
                                         |
                                         v
                                  _discover()
                                         |
                     +-------------------+------------------+
                     |                                      |
                     v                                      v
          current-session generic root           exact/title lookup
             (_resolve_lineage)                 (_title_match_result)
                                                        |
                                                        v
                                              title generic root
                                              reserves 0/1 result slot
                     +-------------------+------------------+
                                         |
                                         v
                               search_session_winners()
                                         |
                      FTS unicode61 / trigram / LIKE
                                         |
                                         v
                         ranked message candidates
                           candidate_hits (message rows)
                                         |
                                         v
                   DISTINCT owning_session_id = lineage_seeds
                                         |
                                         v
                        recursive generic parent walk
                                         |
                                         v
                        seed -> resolved root-like key
                                         |
                                         v
                 one best candidate MESSAGE per resolved key
                       + current/title exclusions
                                         |
                                         v
                                result LIMIT DB-K
                                         |
                                         v
                    winner still carries owning session_id
                         + original winning message_id
                                         |
                                         v
                      get_anchored_view(hit_sid, msg_id)
                       + bookends + root/session metadata
                                         |
                                         v
                                 tool JSON result
```

### Data shape transitions

| boundary | input | output / invariant |
|---|---|---|
| search route | query text | message-level candidates with message id, owning session id, rank/timestamp/source metadata |
| `candidate_hits` | candidate message rows | at most `candidate_limit` ranked message rows |
| `lineage_seeds` | candidate message rows | distinct **owning session IDs**, not message IDs |
| `lineage_resolution` | session seed | root-like dedupe key currently derived by generic parent walk |
| `lineage_ranked` | message rows + root-like key | one best **message** per key; message anchor is not moved to the root |
| hydration | winning `(session_id, message_id)` | anchored window/bookends for the actual matching message |

The “winner message stays the anchor” invariant is important for every #45 contender: lineage resolution is a dedupe identity operation, not a request to rewrite the hit's owning session/message pair.

## 3. Public/tool layer inventory

All links in this section are pinned to fork `dev` `d72f99e...`.

| source | current behavior | consumers / notes | provenance |
|---|---|---|---|
| [`tools/session_search_tool.py:L863-L985`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py#L863-L985) `session_search()` | Dispatches scroll/read/browse/discovery; clamps `limit` to `[1,10]`; normalizes sort | public model-tool entry point | mixed; public session-search behavior is upstream-derived, current discovery bridge has fork changes |
| [`L116-L149`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py#L116-L149) `_resolve_to_parent()` | Walks **all** `parent_session_id` edges to a root; separately records whether any visited session ended by compression | visibility/history helper; generic ancestry is not itself a positive compression-edge predicate | **UPSTREAM-MERGED-IN-BASE** contract from #69544, with later surviving edits |
| [`L151-L170`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py#L151-L170) `_resolve_lineage()` / `_is_compression_ended()` | `_resolve_lineage` discards the compression flag; `_is_compression_ended` is a separate storage/visibility test | current/title/scroll guards distinguish “generic root” from “is this row compression-ended?” | **UPSTREAM-MERGED-IN-BASE** visibility semantics |
| [`L625-L689`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py#L625-L689) `_title_match_result()` | resolves title → session; computes generic `_resolve_lineage`; rejects current root/hidden source; hydrates title result | exact-title lane can reserve one result slot | current shape mixed; generic resolver semantics inherited from merged upstream path |
| [`L692-L861`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py#L692-L861) `_discover()` | computes current generic root; title lane; calls `search_session_winners`; hydrates winner anchors | current DB winner bridge is fork-specific | **FORK-DEV** bridge over upstream tool behavior |
| [`L58-L72`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py#L58-L72) `_DISCOVER_SCAN_LIMIT=300` | candidate scan budget is message rows, before distinct session seeds | not the final result K | fork/current performance policy |
| [`L953-L960`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py#L953-L960) public clamp | public K is 1..10 | disproves any assumption that DB's 100 is public API | current contract |
| [`L706-L740`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py#L706-L740) title/DB slot handoff | if title result and `limit<=1`, skip DB winners; otherwise DB `result_limit=max(0, limit-title_slot)` | effective DB K can be 0..10 | **FORK-DEV** current bridge |
| [`L773-L835`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tools/session_search_tool.py#L773-L835) winner hydration | `get_anchored_view(hit_sid,msg_id,window=5,bookend=3)`; output links remain tied to hit session/message | lineage key does not replace anchor ownership | current contract |

### Current-session and title exclusion are part of the semantic seam

`_discover()` computes `current_lineage_root = _resolve_lineage(...)`, and `_title_match_result()` computes another generic root before SQL winners run. Consequently, a future compression-only DB resolver cannot be considered semantically complete if these tool-side roots remain generic while DB roots become compression-only. The simplest future design should either:

- share one definition of “dedupe lineage key” across title/current/DB paths; or
- explicitly prove why title/current visibility uses a different relation.

This is a seam map, not an implementation recommendation.

## 4. Database winner-selection inventory

Primary source: [`hermes_state_search.py:L1307-L1720`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1307-L1720).

### 4.1 Parameters and routing

- `candidate_limit` defaults to 300 and is clamped `1..1000`.
- `result_limit` defaults to 3 and is clamped `0..100`.
- `lineage_depth_cap` defaults to 64 and is clamped `1..256`.
- CJK routing chooses trigram FTS when available/eligible and otherwise LIKE; ordinary tokenized queries use the main FTS path.
- Source priority demotes cron before final winner limit.
- Relevance FTS can use an inner ranked pre-limit; temporal/LIKE shapes retain their own ordering path.

Relevant ranges:

- [`L1307-L1345`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1307-L1345): signature + clamps.
- [`L1347-L1555`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1347-L1555): route/filter/ranking construction.

### 4.2 Exact message → session → root-like-key transition

The decisive ranges are:

- [`L1562-L1575`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1562-L1575): `candidate_hits` + candidate stats.
- [`L1576-L1579`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1576-L1579): `lineage_seeds` = `SELECT DISTINCT owning_session_id`.
- [`L1580-L1600`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1580-L1600): recursive `lineage_walk`.
- [`L1601-L1627`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1601-L1627): cycle/deepest fallback resolution.
- [`L1628-L1660`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1628-L1660): per-key winner ranking, exclusions, final LIMIT.

Current recursive row:

```text
(seed_session_id, session_id, parent_session_id, depth, path)
```

Current recursive transition:

```text
walk.parent_session_id
  -> sessions parent
  -> keep walk.seed_session_id
```

There is no compression-edge predicate in that transition.

### 4.3 Why shared ancestors repeat today

Suppose two seed sessions `A` and `B` both reach ancestor `X`. Current rows are conceptually:

```text
(A, X, ...)
(B, X, ...)
```

Those are distinct rows because the seed is part of row identity. Current SQL additionally uses `UNION ALL`, so there is no exact-row duplicate suppression at all. Changing only `UNION ALL` → `UNION` would still leave `(A,X)` and `(B,X)` distinct.

This is the precise repeated-work seam #45 is trying to change.

### 4.4 Root/fallback behavior

`lineage_resolution` first detects a parent already present in the path (cycle), otherwise picks the deepest row reached, otherwise the seed. Therefore:

- missing parent: recursive JOIN stops; deepest existing session becomes key;
- cycle: explicit path membership prevents infinite recursion and resolution chooses a stable member encountered by the walk;
- acyclic over-depth chain: walk stops at the depth fuse and the deepest reached session becomes key.

That last behavior is why preserving an accidental numeric depth as identity would be dangerous: the current safety fuse can change the dedupe key for sufficiently long acyclic chains.

### 4.5 Read/lock behavior

[`L1673-L1679`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_search.py#L1673-L1679) executes the whole winner SQL under `self._lock` on `self._conn`.

That differs from many search helpers that use `_read_ctx()`. A future Python N+1/memo contender must therefore make transaction/lock consistency explicit rather than assuming point lookups automatically inherit the current statement's single-connection behavior.

## 5. Lineage creation semantics: what `parent_session_id` actually means

The key correction is that `parent_session_id` is a **generic relationship field**, not a synonym for “compression continuation”. Current code has explicit mechanisms to distinguish child types.

### 5.1 Existing classification helpers

[`hermes_state_common.py:L72-L105`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_common.py#L72-L105):

- `_BRANCH_CHILD_SQL` recognizes the stable `_branched_from` marker and a legacy branch heuristic.
- `_COMPRESSION_CHILD_SQL` requires the referenced parent to have `end_reason='compression'`.
- `_ephemeral_child_sql` excludes branches and compression children from generic ephemeral-child classification.

`_COMPRESSION_CHILD_SQL` alone is not a universal “legal edge” oracle; lifecycle callers add stronger conditions when necessary.

[`hermes_state.py:L3439-L3479`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state.py#L3439-L3479) `find_live_compression_child()` is the strongest current forward-continuation gate found in source:

- parent must be ended with `end_reason='compression'`;
- child must be live;
- `_branched_from` absent;
- `_delegate_from` absent;
- `source != 'tool'`;
- exactly one matching child; multiple children fail closed.

[`hermes_state.py:L3481-L3570`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state.py#L3481-L3570) `publish_compression_child()` publishes the legacy rotation boundary transactionally.

### 5.2 In-place compaction versus legacy rotation

Current compressor path: [`agent/conversation_compression.py:L3200-L3305`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/agent/conversation_compression.py#L3200-L3305).

| shape | durable graph effect | search implication |
|---|---|---|
| in-place compaction | same session id; **no** `end_session`, new session row, or `parent_session_id` edge | pre-compaction rows remain discoverable inside one session; lineage parent traversal contributes nothing for this event |
| legacy rotation | old session is closed and a new continuation child is published with `parent_session_id=old` | historical compression chain exists and needs continuation-aware grouping |

In-place storage itself is [`hermes_state.py:L6833-L6885`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state.py#L6833-L6885): active rows are soft-archived as `active=0, compacted=1`, then compacted live rows are inserted under the same session id atomically.

### 5.3 Semantic truth table for #45 correctness fixtures

This table records the behavior implied by current lifecycle/visibility code; it deliberately does **not** bless the current SQL blind-parent walk.

| relationship | identifying evidence | collapse for compression-history dedupe? | why |
|---|---|---:|---|
| in-place compaction | same session id; archived rows `active=0, compacted=1` | already same id | no graph edge exists |
| legacy compression continuation | parent ended `compression`; child is continuation, not branch/delegate/tool | yes | continuation of one logical compacted conversation |
| `/branch` child | `_branched_from` or legacy branch heuristic | **no** | branch is a distinct conversation path |
| delegation child | `_delegate_from` / delegation relationship | **no** | delegated work is not compression continuation |
| tool child | `source='tool'` relationship | **no** | tool run is not compression continuation |
| plain/generic parent edge with no compression evidence | only `parent_session_id` | **not proven** | parent pointer alone does not establish compression semantics |
| missing parent | parent id references no row | stop safely | malformed/legacy data must not fail search |
| cycle | repeated node | stop safely | corruption/imported history must not loop forever |
| multiple possible live compression children | forward continuation lookup returns >1 | fail closed | current lifecycle refuses to guess canonical continuation |

A positive compression-continuation predicate is therefore more faithful than “follow every parent unless something looks fork-like”.

## 6. Upstream-accepted behavior versus fork behavior

### 6.1 UPSTREAM-MERGED-IN-BASE

#### NousResearch/hermes-agent#69544

Merged 2026-07-22, merge SHA [`e907ecccefaee9c38e4e6e968cd3822eb16fb146`](https://github.com/NousResearch/hermes-agent/commit/e907ecccefaee9c38e4e6e968cd3822eb16fb146). The merge is an ancestor of pinned fork `dev`.

Accepted behavior:

- in-place compaction archives on the current session remain discoverable;
- legacy compression-rotated history remains discoverable;
- rewind rows remain hidden;
- delegation children remain excluded;
- `_resolve_to_parent` tracks generic root plus whether a compression hop exists.

This PR is authority for **visibility semantics**, not proof that every generic parent edge should be a session-search dedupe edge.

#### NousResearch/hermes-agent#72279

Merged 2026-07-26, merge SHA [`b93fd077c0652a53d66231f5aeaa701e242692dc`](https://github.com/NousResearch/hermes-agent/commit/b93fd077c0652a53d66231f5aeaa701e242692dc). The merge is an ancestor of pinned fork `dev`.

Accepted behavior: discovery→scroll is allowed for compaction-archived / legacy compression history while active current/delegation and rewound rows stay guarded.

Again, this is a storage-state/visibility contract, not acceptance of the fork's later SQL winner implementation.

### 6.2 FORK-DEV

The current SQL winner path is fork work layered onto the upstream tool semantics.

Verified modifying commits include:

- [`9a1f477df5c8f25fd7ba4f57318e9f5ffcb2fc32`](https://github.com/Skywind5487/hermes-agent/commit/9a1f477df5c8f25fd7ba4f57318e9f5ffcb2fc32) — `fix(session-search): ranked Top-N pre-limit for relevance sort`.
- [`2d2bad204ec644455ad1273f2934f388eb4111dd`](https://github.com/Skywind5487/hermes-agent/commit/2d2bad204ec644455ad1273f2934f388eb4111dd) — `fix(session-search): defer winner snippets`; moves FTS snippet generation after lineage winner selection.

The pinned upstream merge-base `hermes_state_search.py` still has the older `search_messages()`-centric architecture in the inspected region, whereas pinned fork `dev` contains `search_session_winners()` and the recursive winner CTE. The exact *first* introducing commit for the current lineage CTE was **not recovered from a primary commit search in this pass**; do not substitute issue #11's historical summary as proof. The surviving code and verified fork commits are enough to classify the current SQL winner architecture as fork-specific, but the introduction SHA remains an archaeology gap.

### 6.3 FORK-HISTORICAL

[`a17959c7e919bd6247398cc5eaed0b6f0fd2b85b`](https://github.com/Skywind5487/hermes-agent/commit/a17959c7e919bd6247398cc5eaed0b6f0fd2b85b) (`fix/session-search-perf`) belongs to the older Python/per-hit search architecture. It remains useful as performance-history evidence, not as current code semantics.

### 6.4 UPSTREAM-OPEN

NousResearch/hermes-agent#55640 was still open/unmerged at research time. Its PR description includes bounded lineage traversal, lookup budgets, path compression, and stopping once requested result count is satisfied. Treat it as prior-art evidence only; #47 owns its detailed comparison.

## 7. Safety and failure-intent map

| risk | current protection | source-level conclusion |
|---|---|---|
| cycle during search | `path` string + membership test before recursive step | explicit cycle detector independent of depth fuse |
| long acyclic/pathological ancestry | `lineage_depth_cap` default 64, clamp 1..256 | bounded-work fuse; exact `64` rationale not recovered |
| missing parent | inner JOIN stops recursive expansion; deepest reached row/seed fallback | deterministic safe degradation |
| malformed imported cycle | import first creates sessions detached, then rejects/detaches cycle-forming parent update | normal import does not intentionally create new cycles |
| ambiguous forward compression continuation | `find_live_compression_child()` requires exactly one child | fail closed, do not guess |
| query read consistency | winner SQL runs as one statement under `self._lock` / `self._conn` | Python point-lookup alternatives must choose consistency/lock strategy deliberately |

Import evidence: [`hermes_state_portability.py:L657-L715`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/hermes_state_portability.py#L657-L715) builds a pending parent map, detects cycles, verifies parents exist, and detaches the closing edge when invalid.

### What is known and unknown about `lineage_depth_cap=64`

Known:

- it is not the cycle detector; path membership already does that;
- it changes resolution for an acyclic chain longer than the allowed expansion;
- the DB helper clamps caller input to `1..256`;
- a bounded-work safeguard remains valuable for corrupt/pathological data.

Unknown:

- no primary introducing commit/review was recovered that explains why the default is exactly `64`;
- no public tool contract found promises “lineage identity is defined by at most 64 hops”.

Therefore benchmark-v2 should test **runaway-work behavior separately from lineage identity**.

## 8. Test inventory and drift

### 8.1 Current SQL-winner tests

Pinned file: [`tests/test_session_search_sql_winners.py`](https://github.com/Skywind5487/hermes-agent/blob/d72f99eb1b897dd29a46692a310aa15b1bfd77e8/tests/test_session_search_sql_winners.py).

It currently covers:

- one best hit per current root-like key;
- temporal ordering comparison to an older Python oracle;
- cron/source priority before final limit;
- current/explicit root exclusions;
- missing parent, cycle, and depth-cap fallback;
- no candidate-context hydration;
- title lookup behavior;
- CJK LIKE/trigram winner shape.

### 8.2 Important test-semantic problem

`test_sql_winners_keep_best_hit_per_lineage_and_preserve_candidate_scan` creates `root` and `child`, writes only `child.parent_session_id = root`, **never marks `root` as compression-ended**, and expects the two sessions to collapse into one lineage.

That test therefore codifies the current **generic parent** implementation, not the compression-continuation intent evidenced by lifecycle/visibility code. It cannot serve unchanged as benchmark-v2's correctness oracle.

### 8.3 Static source drift found (not runtime-tested here)

Two contradictions are visible directly in pinned source:

1. `test_sql_winners_match_existing_python_oracle_for_all_temporal_orders` assigns `_resolve_to_parent(...)` to `root` as though it were a scalar, while current `_resolve_to_parent()` returns `(root_id, has_compression_hop)`.
2. `test_title_discovery_does_not_call_get_messages` expects title discovery not to call `db.get_messages`, while current `_title_match_result()` directly calls `db.get_messages(session_id)`.
3. `tests/tools/test_session_search.py::test_discovery_field_plan_preserves_full_default_result` spies on `db.search_messages`, but active `_discover()` now calls `db.search_session_winners()` directly; the spy no longer observes the active message discovery path.

These are **static audit findings only**. This research branch did not execute the test suite, so they are not reported as observed runtime failures.

### 8.4 Upstream-derived semantic tests worth retaining

`tests/tools/test_session_search.py` contains the accepted compaction/visibility regression surface from #69544/#72279, including:

- legacy compression rotation;
- in-place compaction archives;
- rewind exclusion;
- delegation exclusion;
- delegation under a compression ancestor;
- current-lineage scroll guards.

Those tests are more authoritative for *visibility intent* than a synthetic generic-parent lineage fixture.

### 8.5 Missing benchmark-v2 correctness fixtures

Before #45 compares algorithms, add/repair intent-level fixtures for:

- positive legacy compression continuation;
- `/branch` child with a parent edge must remain distinct;
- `_branched_from` marker path;
- `_delegate_from` / delegation child must remain distinct;
- `source='tool'` child must remain distinct;
- plain parent edge with no compression evidence;
- in-place compaction (same session ID) separately from legacy rotation;
- missing parent;
- cycle;
- long acyclic chain / work-budget behavior independently of identity;
- ambiguous/multiple compression-child behavior where forward traversal is used;
- public result limits `1`, `3`, `10`;
- exact-title result consuming one result slot;
- current/title exclusion using the same intended lineage key as DB winner selection;
- winner message/session anchor preserved after dedupe.

No query-count/parent-expansion work-bound regression test was found for the current SQL winner lineage stage.

## 9. SQLite capabilities that materially constrain #45

Primary references:

- SQLite WITH / recursive CTE documentation: <https://www.sqlite.org/lang_with.html>
- SQLite expression/bind-parameter documentation: <https://www.sqlite.org/lang_expr.html#parameters>

Only the constraints relevant to this code are recorded here.

### Recursive queue

SQLite's recursive CTE algorithm conceptually places seed rows in a queue, extracts a row, and evaluates the recursive term using that row. Recursive-term `ORDER BY` controls queue extraction order. That makes ranked/BFS/DFS scheduling expressible without claiming that SQL text order alone is execution order.

### `UNION` duplicate suppression is full-row equality

With recursive `UNION`, a row is added only if an identical row has not already been added. Identity is the full recursive row. Consequently, current `(seed_session_id, ancestor, ...)` rows from different seeds do not merge simply because the ancestor ID matches.

### Recursive self-reference is deliberately restricted

The recursive table must appear exactly once in the top-level `FROM` of each recursive SELECT and may not also be queried from a nested subquery. Aggregate/window functions are also prohibited in the recursive SELECT. This is why a recursive relation is not an arbitrary mutable `node -> value` memo table.

### Completed earlier CTEs are ordinary composition

A later CTE/subquery can consume the result of an earlier completed CTE. That makes staged prototypes (`path1` then `path2` reads `path1`) legal. It does **not** make a runtime-variable number of named stages native SQL syntax.

### Runtime K versus fixed SQL structure

Bind parameters substitute values in expressions (for example a `LIMIT ?`). They do not dynamically create identifiers or a variable number of CTE definitions. Public K is runtime-variable 1..10, so literal `path1 ... pathK` SQL requires hard unrolling to a fixed maximum, generated SQL, or another state representation.

### Materialization / co-routine caveat

`MATERIALIZED` / `NOT MATERIALIZED` are planner controls/hints, not replacements for semantic proof. Likewise, a specific `EXPLAIN QUERY PLAN` showing a co-routine and useful outer-LIMIT early stop is evidence for that exact contender/query plan, not a universal SQLite guarantee. #45's rank-priority/streaming contenders must measure their exact plan and parent expansions.

## 10. Where a #45 implementation would plug in

### SQL shared traversal

The narrow DB seam is:

```text
candidate_hits
  -> lineage_seeds
  -> [replace lineage_walk / lineage_resolution]
  -> lineage_ranked
```

Keep candidate generation/ranking, source priority, final winning message ID, exclusions, and hydration behavior stable unless correctness research says otherwise.

### Sequential / Python memo

A Python resolver would need:

1. the same ranked candidate message/session information after candidate narrowing;
2. a query-local `seed_session_id -> intended lineage key` resolver with bounded work;
3. the same winner ordering and original message anchor;
4. an explicit transaction/lock/read-context strategy.

Do not load the whole session graph merely to imitate the current single SQL statement.

### Cross-layer semantic seam

Because tool-layer current/title exclusion still uses `_resolve_lineage()`, any correction from generic ancestry to positive compression-continuation semantics must account for **both**:

- DB winner dedupe; and
- tool-side current/title lineage identity.

Otherwise the same session can be classified under two different root definitions inside one discovery call.

## 11. Answers to #46 exit questions

- **Where does K come from?** Public `session_search.limit`, clamped 1..10; title match can consume one slot; DB's 0..100 clamp is internal.
- **Where do message rows become session seeds?** `candidate_hits` → `lineage_seeds SELECT DISTINCT owning_session_id`, `hermes_state_search.py:L1576-L1579`.
- **Why do shared ancestors repeat?** Recursive row retains `seed_session_id`; different seeds produce different rows, and current walk uses `UNION ALL`.
- **Which parent edges are legal compression edges?** Generic `parent_session_id` alone is insufficient. Positive evidence requires compression-ended parent plus continuation semantics; branch/delegation/tool relationships are distinct. `find_live_compression_child()` shows the strongest current forward gate.
- **In-place vs legacy graph shape?** In-place adds no parent edge; legacy rotation publishes a continuation child.
- **What do cycle/depth/missing-parent guards protect?** Cycle guard prevents loops; missing parent stops safely; depth is a separate bounded-work fuse. Exact reason for numeric 64 remains unrecovered.
- **Upstream vs fork?** Compacted-history visibility/scroll semantics are upstream-merged (#69544/#72279); current SQL winner/recursive CTE architecture and ranked/deferred-snippet optimizations are fork work. #55640 remains open evidence only.
- **Where would a new resolver plug in?** Primarily between `lineage_seeds` and `lineage_ranked`, but a semantic correction also needs tool-side current/title roots aligned.
- **Which tests prove equivalence today?** None alone. Upstream compaction/visibility tests preserve important intent, while current SQL-winner tests contain generic-parent assumptions/static drift. Benchmark-v2 needs the intent fixtures listed above.

## 12. Remaining archaeology gaps

This research deliberately leaves two facts unresolved rather than guessing:

1. the exact first fork commit that introduced the surviving `search_session_winners()` lineage CTE;
2. the primary historical rationale for choosing the depth default `64` specifically.

Neither gap blocks #45's algorithm research. They do block claims that the current blind-parent SQL or exactly-64-hop behavior is an upstream/product semantic contract.
