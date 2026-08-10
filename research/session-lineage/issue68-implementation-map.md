# #68 production resolver implementation map (#70)

> Research-only handoff for #70. This document maps the frozen #68 production contract onto the actual current `dev` implementation surface. It does **not** implement #68.
>
> Authority order used here: #68 frozen spec → #66/#58/#51 stable semantics and safety intent → pinned current `dev` source/tests → #46/#47 prior maps → PR #63/#62 donor evidence only → open research evidence.

## 0. Executive result

**READY FOR IMPLEMENTATION. No blocker issue or further design split is needed.**

The smallest robust implementation is **not** to cherry-pick PR #63 or add a parallel `hermes_state_lineage.py` search stack. Keep the mature route/ranking/candidate machinery in `hermes_state_search.py::search_session_winners()` and replace only the current generic-parent lineage tail:

```text
current
bounded ranked raw message hits
  -> DISTINCT owning session seeds
  -> recursive generic-parent CTE + depth cap
  -> root partition + winner LIMIT

#68 target
bounded ranked raw message hits
  -> ranked DISTINCT owning-session candidates
     (preserve each owner's first/best message anchor)
  -> query-local Python memo/path-compression resolver
     on the SAME connection + logical read snapshot
  -> positive compression-continuation roots only
  -> early-K accepted roots
  -> deferred FTS snippet + existing tool hydration
```

The current source already provides the two infrastructure pieces the target needs:

1. `search_session_winners()` has the modern FTS/trigram/LIKE candidate and ranking pipeline; its lineage CTE is a local replaceable middle/tail seam.
2. `SessionDB._read_ctx()` already gives a WAL per-thread `mode=ro` connection and a locked-writer non-WAL fallback. One explicit `BEGIN` around the multi-statement candidate/resolver/winner phase preserves the current one-statement snapshot guarantee.

PR #63 contains useful donor fragments — especially the narrow node lookup, parent-bound marker predicate, same-connection resolver shape, and focused fixtures — but its architecture is stale for #68: it duplicates the candidate stack, uses retired `B=1500`, traverses raw message candidates rather than ranked distinct owners, and does not memoize semantic unresolved outcomes.

---

## 1. Pinned receipt

Research date: 2026-08-10.

| Item | Immutable pin / state |
|---|---|
| implementation target | issue #68, frozen production spec |
| research ticket | issue #70 |
| fork integration branch | `dev` |
| **BASE_SHA** | **`bdf2fc218264538c4f3238b58532488fe665ff9e`** |
| `hermes_state_search.py` blob | `bc67bf894df1727f4e68cf589a430868452017f0` |
| `tools/session_search_tool.py` blob | `24f4d077c3bda862ba6ca74d1f14000527f8f866` |
| `hermes_state.py` blob | `3742b487143392c7d7ef674c2e52e213c28f9e46` |
| `hermes_state_common.py` blob | `c21135beeb8a90c27232fa90823021e9562ab0b0` |
| `tests/test_session_search_sql_winners.py` blob | `9548737ae64501de33c0acb4a138a8806ae96fb6` |
| PR #63 donor head | `4cd6a4c7afab507114502a1252845ea3e3ff938c` |
| PR #63 base | `19e6e6223bb58a4a53c8c02c86a0127d34afaf5a` |
| PR #55 research head | `4304e75fe39ea74a7284e5d7c4f1a5f432266e07` |

Primary links:

- #68: <https://github.com/Skywind5487/hermes-agent/issues/68>
- #70: <https://github.com/Skywind5487/hermes-agent/issues/70>
- #66 consolidated facts: <https://github.com/Skywind5487/hermes-agent/issues/66>
- #58 lifecycle semantics: <https://github.com/Skywind5487/hermes-agent/issues/58>
- #51 safety/depth provenance: <https://github.com/Skywind5487/hermes-agent/issues/51>
- #54 validation lane: <https://github.com/Skywind5487/hermes-agent/issues/54>
- #46 source map: <https://github.com/Skywind5487/hermes-agent/issues/46>
- PR #63 donor: <https://github.com/Skywind5487/hermes-agent/pull/63>

### Drift from the old research pins matters

The old #46 map pinned `dev=d72f99e...`; current `dev` is 16 commits ahead. More importantly, PR #63 was based on `19e6e62...`; current `dev` is **11 commits ahead of that base**, with material intervening edits in `hermes_state.py` (+431/-94), `hermes_state_common.py` (+64/-12), and `hermes_state_search.py` (+233/-35). Therefore PR #63 is a donor, not a safe whole-file transplant.

Source: GitHub compare `19e6e622... -> bdf2fc218...` and current pinned blobs above.

---

## 2. Frozen contract translated into code obligations

Issue #68 freezes these implementation semantics; they are inputs, not choices for #70:

- always query-local memo/path compression from candidate 1;
- global `B=2000` **successful uncached lineage-node row fetches** per logical resolver query;
- ranked **distinct owning-session** candidates after the bounded raw candidate set;
- first/best ranked message anchor for each owner must survive owner dedupe;
- positive compression-continuation edges only;
- local seen-set cycle detection; no semantic depth cap;
- missing parent and proven cycle may memoize a semantic `unresolved` outcome;
- B exhaustion must **not** memoize the partial path as unresolved;
- on B exhaustion, stop the entire ranked scan and expose an explicit incomplete/truncated safe prefix;
- mandatory early-K stop after K distinct accepted roots;
- current-session/title exclusion must use the same root meaning as DB winner dedupe;
- candidate selection plus ranking-sensitive lineage resolution must observe one coherent logical read snapshot;
- anchored-view/bookend hydration remains deferred until winners are known.

Source: #68; #54 is now explicitly residual validation and records `B=1500` as retired.

---

## 3. Current candidate + ranking pipeline: preserve this

### 3.1 Public/tool K contract

`tools/session_search_tool.py` remains the same blob mapped by #46. Discovery still clamps public `limit` to `1..10`; an exact-title result consumes one slot and the DB gets the effective remainder.

Relevant current ranges at BASE_SHA:

- `tools/session_search_tool.py:L692-L861` — `_discover()` orchestration;
- `L706-L740` — title slot / DB `result_limit` handoff;
- `L773-L835` — winner anchored-view hydration;
- `L863-L985` — public `session_search()` dispatcher and limit clamp.

Pinned source: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/tools/session_search_tool.py#L692-L985>

Do **not** widen public K as part of #68.

### 3.2 DB candidate pipeline

`hermes_state_search.py::search_session_winners()` begins at current `L1721`.

Pinned source: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/hermes_state_search.py#L1721-L2145>

Keep these behaviors:

- `candidate_limit` clamped `1..1000`;
- `result_limit` clamped `0..100` internally;
- unicode61 / trigram / CJK LIKE routing;
- relevance sort uses ranked Top-N pre-limit where safe;
- `newest` / `oldest` preserve timestamp-primary semantics;
- source priority demotes `cron` before the final K;
- FTS snippets are deferred until after winner selection;
- no full candidate message content/context is hydrated in the winner phase.

The key current SQL seam is in `hermes_state_search.py:L1969-L2089`:

<https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/hermes_state_search.py#L1969-L2089>

It currently builds:

```text
candidate_base
  -> candidate_hits       # bounded ranked raw messages
  -> candidate_stats
  -> lineage_seeds        # DISTINCT owning_session_id
  -> lineage_walk         # recursive GENERIC parent walk
  -> lineage_resolution
  -> lineage_ranked
  -> final LIMIT
```

The replacement boundary should start **after `candidate_hits` is formed**, not before the route/ranking code.

### 3.3 Exact ranked-owner handoff

#68 requires distinct owner candidates before lineage traversal, while preserving the best message anchor per owner.

The smallest SQL-side handoff is an owner-rank projection over the already bounded `candidate_hits`, conceptually:

```sql
owner_candidates AS (
    SELECT *
    FROM (
        SELECT
            candidate_hits.*,
            ROW_NUMBER() OVER (
                PARTITION BY owning_session_id
                ORDER BY candidate_order
            ) AS owner_rank
        FROM candidate_hits
    )
    WHERE owner_rank = 1
)
```

Then return owner candidates in the same effective winner order:

```text
source_priority ASC, candidate_order ASC
```

Why both keys matter: current final winner selection partitions by lineage root using `ORDER BY hits.source_priority, hits.candidate_order`, then orders the final roots by the same pair. `source_priority` therefore remains part of the observable winner contract, not merely telemetry.

The retained anchor is the row's existing `(owning_session_id, message_id)` pair. Lineage resolution supplies only a dedupe/exclusion key; it must not rewrite the actual match anchor to the root session.

---

## 4. Replace the generic-parent CTE, not the search stack

### 4.1 What must go

Current `hermes_state_search.py:L1989-L2054` follows every `parent_session_id`, uses a `depth` column, a path string, and `lineage_depth_cap`.

Pinned source: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/hermes_state_search.py#L1989-L2054>

This conflicts with #68 in two ways:

1. generic parentage is not compression-lineage identity;
2. literal depth is not the safety contract.

#51 recovered that the surviving `64` was introduced by fork commit `2732c47e28fbf7aaea97bd8c5cf82045a4c34159` with the SQL winner path; no primary source recovered an “only 64 ancestors count” product meaning. #51's surviving safety intent is cycle termination + deterministic malformed/missing handling + bounded pathological acyclic work. #68 implements that intent with local cycle detection plus global B.

Source: <https://github.com/Skywind5487/hermes-agent/issues/51#issuecomment-5230034020> and <https://github.com/Skywind5487/hermes-agent/issues/51#issuecomment-5230440102>.

### 4.2 Recommended production location

Keep the resolver kernel in `hermes_state_search.py` beside `SessionSearchMixin`, rather than creating PR #63's parallel `hermes_state_lineage.py` subclass.

Suggested private pieces:

```text
_LINEAGE_WORK_BUDGET = 2000
_UNRESOLVED = sentinel
_BUDGET_EXHAUSTED = sentinel
_LINEAGE_NODE_SQL = narrow point lookup
_LineageResolutionState
SessionSearchMixin._resolve_compression_lineage_on_conn(...)
SessionSearchMixin.resolve_compression_lineage(...)  # shared tool semantic helper
```

Reasons:

- no duplicate FTS/ranking implementation;
- current `search_session_winners()` can call the private on-connection kernel without opening another connection or lock;
- tool current/title paths can use the public wrapper and therefore share the exact edge predicate/outcome semantics;
- future search-route edits still have one canonical candidate implementation.

---

## 5. Narrow indexed point lookup shape

### 5.1 Required columns

One uncached node lookup must be enough to decide whether the current child can move to its parent:

```text
child.id
child.parent_session_id
child.source
child.model_config
parent exists?
parent.end_reason
```

The PR #63 donor query is almost exactly the desired primitive:

<https://github.com/Skywind5487/hermes-agent/blob/4cd6a4c7afab507114502a1252845ea3e3ff938c/hermes_state_lineage.py#L20-L33>

Conceptually:

```sql
SELECT
    child.id,
    child.parent_session_id,
    child.source,
    child.model_config,
    parent.id         AS parent_exists,
    parent.end_reason AS parent_end_reason
FROM sessions child
LEFT JOIN sessions parent ON parent.id = child.parent_session_id
WHERE child.id = ?
```

This is preferable to chaining `get_session(child)` + `get_session(parent)`: one successful indexed point statement gives all edge evidence and makes B accounting exact.

### 5.2 Why it is indexed

Current schema makes `sessions.id` `TEXT NOT NULL UNIQUE`; the child lookup and parent join therefore both use unique-key lookups. `parent_session_id` itself does not need a reverse index for this traversal because the query starts from the child's id and joins to one known parent id.

Pinned schema: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/hermes_state_common.py#L184-L239>

### 5.3 Do not reuse `find_live_compression_child()` as the resolver primitive

`hermes_state.py::find_live_compression_child()` is a **forward recovery helper**, not the backward identity predicate. It additionally requires a live child, rejects marker **presence** rather than only parent-bound markers, rejects ambiguity among multiple live children, and queries children by parent.

Pinned current source: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/hermes_state.py#L3995-L4033>

That lifecycle helper should not be broadened or repurposed for #68.

---

## 6. Positive compression-continuation predicate

For child `c` with `p = c.parent_session_id`, follow `c -> p` iff all are true:

1. `p` is non-null;
2. the parent row exists;
3. `p.end_reason == 'compression'`;
4. `c.source != 'tool'`;
5. `json(c.model_config)._branched_from != p.id`;
6. `json(c.model_config)._delegate_from != p.id`.

A foreign/stale marker pointing somewhere else does **not** disqualify the edge.

Sources:

- #66 stable semantics: <https://github.com/Skywind5487/hermes-agent/issues/66>
- #58 Aug-7 marker correction: <https://github.com/Skywind5487/hermes-agent/issues/58#issuecomment-5231217432>
- PR #63 donor implementation: <https://github.com/Skywind5487/hermes-agent/blob/4cd6a4c7afab507114502a1252845ea3e3ff938c/hermes_state_lineage.py#L36-L117>

### Malformed `model_config`

PR #63 treats malformed/non-object marker metadata as “not proven positive edge”, therefore the current child is its own root rather than traversing an unproven parent. That is the conservative direction and is compatible with the frozen positive-evidence rule. Preserve this behavior unless #68 is explicitly amended.

Do **not** reuse `_COMPRESSION_CHILD_SQL` alone as identity. Current `_COMPRESSION_CHILD_SQL` checks only that the parent ended by compression; `_BRANCH_CHILD_SQL` has different list/delete lifecycle semantics, including legacy heuristics. They remain useful lifecycle helpers but are not the complete #68 edge oracle.

Pinned source: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/hermes_state_common.py#L72-L107>

---

## 7. Resolver state + exact control order

### 7.1 Query-local state

Use one state object per logical winner query:

```text
memo: node -> resolved_root | UNRESOLVED
work: successful uncached lineage-node row fetches
memo_hits
bound_hit
candidates_inspected
accepted_roots
```

`memo` must never contain B-exhausted partial paths.

### 7.2 Exact loop ordering

The order is correctness-significant at the B boundary:

```text
resolve(node):
  1. memo lookup
     - resolved root => memo hit, reuse
     - UNRESOLVED   => memo hit, fail closed

  2. traversal-local seen check
     - repeated node => positive cycle proven without another DB lookup
     - memoize current proven cycle path as UNRESOLVED
     - return UNRESOLVED

  3. budget check ONLY because another uncached row lookup is now required
     - if work >= 2000:
         bound_hit = true
         return BUDGET_EXHAUSTED
         DO NOT mutate memo for the partial path

  4. execute the one-node point lookup
     - missing row: zero successful-fetch work for that absent row;
       semantic missing/unresolved may be memoized for the traversed path
     - successful row: work += 1

  5. classify the fetched row
     - parent NULL => root=current; path-compress resolved root
     - referenced parent missing => memoize semantic UNRESOLVED
     - edge not positively compression-continuation => root=current; path-compress
     - positive edge => node=parent and continue
```

### 7.3 Boundary consequences

This ordering gives #68's exact B semantics:

- if lookup #2000 itself proves a root/non-edge/missing-parent outcome, that outcome is allowed and cacheable;
- at `work==2000`, a memo hit still resolves without work;
- at `work==2000`, a cycle that is already provable from `seen` still classifies as cycle/unresolved without another lookup;
- only a required **new uncached** lookup #2001 becomes B exhaustion;
- B exhaustion is operational uncertainty, **not semantic unresolved**.

### 7.4 Missing-parent/cycle memoization

When a traversal proves semantic unresolved, every node on the current path whose identity depends on that malformed/cyclic suffix can be memoized to `UNRESOLVED`. Repeated ranked owners entering the same known-bad suffix should then cost zero additional successful row lookups.

By contrast, B exhaustion leaves the partial path untouched. A later query gets a fresh memo/B budget and may resolve it normally.

---

## 8. Winner loop + mandatory early-K

The Python winner loop should consume the **ranked distinct owner candidates**, not raw message rows.

Conceptually:

```text
for owner_candidate in ranked_owner_candidates:
    if accepted_roots == K:
        break

    candidates_inspected += 1
    outcome = resolve(owner_session_id)

    if outcome is BUDGET_EXHAUSTED:
        stop the entire scan
        mark truncated/bound_hit
        return the accepted safe prefix

    if outcome is UNRESOLVED:
        continue

    if root is excluded/current/title root:
        continue

    if root already accepted:
        continue

    accept owner_candidate + root
```

Do not resolve all candidate owners and slice later. Early-K is part of the frozen production contract and is the main reason normal searches avoid paying the 1000-candidate safety ceiling.

`source_priority,candidate_order` remains the candidate acceptance order.

---

## 9. One coherent logical read snapshot

### 9.1 Current source seam

Current winner selection executes one SQL statement under `self._lock` / `self._conn` (`hermes_state_search.py:L2092-L2097`), so it naturally gets one SQLite statement snapshot.

Pinned source: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/hermes_state_search.py#L2092-L2097>

A Python memo implementation is multi-statement, so it must make the snapshot explicit.

### 9.2 Current `_read_ctx()` is the correct owner

At current BASE_SHA, `SessionDB._get_read_conn()` / `_read_ctx()` live around `hermes_state.py:L2350-L2424`:

<https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/hermes_state.py#L2350-L2424>

Behavior:

```text
WAL:
  per-thread file:...?mode=ro connection
  isolation_level=None
  no self._lock

non-WAL / reader-open failure / read_only SessionDB:
  yield shared self._conn while holding self._lock
```

This is the seam #50 already validated for multi-statement resolver work.

### 9.3 Required transaction shape

Inside `search_session_winners()`:

```text
with self._read_ctx() as conn:
    if not conn.in_transaction: BEGIN
    try:
        candidate query
        ranked owner projection
        all lineage point lookups + memo decisions
        winner selection / winner snippet reads
    finally:
        close the owned read transaction (ROLLBACK is sufficient for read-only work)

# only after that:
tool-layer anchored-view/bookend hydration
```

The private resolver must accept the already-owned `conn`; do **not** call a helper that opens another `_read_ctx()` from inside the winner phase.

### Critical non-WAL pitfall

`self._lock` is a plain `threading.Lock`, not a re-entrant lock. Under non-WAL fallback, `_read_ctx()` already holds it across the whole context. Calling `get_session()`, the public `resolve_compression_lineage()`, or another helper that tries to reacquire `_read_ctx()`/`self._lock` from inside the resolver risks deadlock or snapshot drift. The on-connection kernel must issue its point SQL directly on the supplied `conn`.

Source/probe record: #50 / PR #53, especially <https://github.com/Skywind5487/hermes-agent/pull/53>.

---

## 10. Tool-layer current/title parity

### 10.1 Current problem

Current `tools/session_search_tool.py:L116-L170` defines `_resolve_to_parent()` / `_resolve_lineage()` by walking **generic** `parent_session_id` ancestry through repeated `db.get_session()` calls.

Pinned source: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/tools/session_search_tool.py#L116-L170>

`_discover()` uses this generic root for:

- current-session exclusion;
- exact-title lineage identity;
- the root values passed into DB winner exclusion.

Pinned source: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/tools/session_search_tool.py#L625-L750>

Changing only the DB resolver would therefore leave two definitions of lineage.

### 10.2 Shared semantic helper

Expose `SessionSearchMixin.resolve_compression_lineage(session_id, ...)` as a thin public wrapper over the same private on-connection kernel. Tool `_resolve_lineage()` should use that method for real `SessionDB` objects.

PR #63 already demonstrates this narrow wiring idea:

<https://github.com/Skywind5487/hermes-agent/pull/63/files>

Do not copy its fallback semantics blindly. Production `None/UNRESOLVED` must never be converted into an invented generic ancestor. A compatibility fallback to old generic ancestry, if retained at all, should be limited to explicit legacy/test doubles that do not expose the production method.

### 10.3 Ranking-sensitive exclusion snapshot

The DB winner phase should not depend only on roots precomputed on some earlier connection. To preserve the frozen “one coherent logical read snapshot” for ranking-sensitive exclusion, pass the **current/title session IDs** (or equivalent raw exclusion identities) into `search_session_winners()` and resolve those identities with the same memo/state/connection used for candidate roots.

The tool may still resolve current/title independently for its own exact-title display decision, but DB winner exclusion should re-resolve the raw IDs inside the winner snapshot rather than trusting a stale root string.

This is a small API seam, not a new architecture. Preserve old root parameters only if needed for compatibility tests; production `_discover()` should use the raw-id path.

### 10.4 Exact-title bounded hydration fix is a separate narrow salvage

Current `_title_match_result()` loads the whole session via `db.get_messages(session_id)` merely to find the first anchor, despite `tests/test_session_search_sql_winners.py::test_title_discovery_does_not_call_get_messages` asserting bounded behavior.

PR #63 replaces this with a lightweight first-message-id query before `get_anchored_view()`. Salvage that behavior, preferably as a DB read helper / `_read_ctx()` read rather than direct tool access to `db._conn`.

This is adjacent cleanup already demanded by an existing test; do not expand it into a broader title-search rewrite.

---

## 11. B-hit response contract and observability

### 11.1 DB stats: smallest useful additive fields

Keep existing stats and add:

```text
lineage_candidates_inspected: int
lineage_work: int                 # exact successful uncached row fetches
lineage_memo_hits: int
lineage_memo_entries: int         # resolved + semantic-unresolved entries
lineage_count: int                # accepted distinct roots
lineage_bound_hit: bool
```

Existing `candidate_count`, `candidate_unique_sessions`, `winner_count`, and `route` remain.

`lineage_work` is the authoritative B counter. Do not count memo hits, cycle checks, absent-row fetches, JSON parsing, or message/snippet hydration as work units.

### 11.2 Tool JSON: explicit incompleteness

Discovery should expose an additive `truncated` boolean. On B hit:

```json
{
  "truncated": true,
  "warning": "Session search stopped at the lineage safety work bound; results are a safe ranked prefix and may be incomplete."
}
```

Normal completed discovery may return `"truncated": false` and omit `warning`.

A B-hit response must **never** claim complete top-K coverage.

### 11.3 Empty-prefix trap

Current `_discover()` returns `"No matching sessions found."` whenever DB winners and title are both empty. That message is false if the scan stopped on B before producing a winner.

Therefore branch on `lineage_bound_hit` before emitting the current no-match message. An empty safe prefix under B exhaustion is “incomplete/truncated”, not “no matches”.

### 11.4 Logs

Extend the existing `SESSION_WINNERS` / `DISCOVER_DONE` logs with the same exact counters. No separate telemetry system is needed.

---

## 12. Tests: RED-first map

### 12.1 Primary production seam — `tests/test_session_search_sql_winners.py`

Current file: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/tests/test_session_search_sql_winners.py>

Reuse the existing high-level DB fixture style. Several current tests intentionally encode behavior #68 replaces:

- generic parent collapse in `test_sql_winners_keep_best_hit_per_lineage_and_preserve_candidate_scan`;
- Python oracle uses `_resolve_to_parent()` generic ancestry;
- current/excluded lineage fixture uses generic parent semantics;
- `test_sql_winners_handle_missing_parent_cycle_and_depth_cap` expects a fabricated fallback root on missing/cycle and a depth-capped endpoint.

Those should become RED tests for the frozen contract, not preserved as compatibility behavior.

Required high-level cases:

1. positive compression continuation collapses;
2. plain generic parent does not collapse;
3. branch marker to this parent remains distinct;
4. delegate marker to this parent remains distinct;
5. `source='tool'` child remains distinct;
6. foreign branch/delegate marker pointing elsewhere still permits a real compression edge;
7. best anchor per owning session survives owner dedupe;
8. source-priority + `candidate_order` preserved;
9. early-K stops before trailing expensive/pathological candidates;
10. missing parent fails closed and memo-reuses unresolved;
11. 2-node cycle, longer cycle, tail→cycle;
12. repeated candidates entering proven cycle cost zero later lookups;
13. B required work 1999 / 2000 succeeds; 2001 truncates;
14. cycle provable exactly at `work==B` is cycle, not B hit;
15. B one lookup before cycle proof reports B hit and does not poison memo;
16. B-exhausted path resolves normally in a fresh query;
17. public route shapes unicode61 / LIKE / trigram remain lightweight;
18. `lineage_depth_cap` no longer affects identity; remove/retire the old injected-depth contract.

### 12.2 Tool/integration seam — `tests/tools/test_session_search.py`

Current file: <https://github.com/Skywind5487/hermes-agent/blob/bdf2fc218264538c4f3238b58532488fe665ff9e/tests/tools/test_session_search.py>

Keep existing discovery/current/hydration tests and add:

- current-session exclusion through a positive compression continuation;
- branch/delegate child under current lineage remains distinct as #68 defines;
- exact-title and DB content lane share compression-root semantics;
- B hit returns safe prefix + `truncated=true` + warning;
- B hit with zero winners does **not** emit “No matching sessions found”;
- normal discovery returns `truncated=false`;
- title lane stays bounded (existing `test_title_discovery_does_not_call_get_messages`).

### 12.3 PR #63 fixtures to salvage

PR #63 added useful intent fixtures in `tests/test_session_search_compression_lineage.py` and updates to `tests/test_session_search_sql_winners.py`:

<https://github.com/Skywind5487/hermes-agent/pull/63/files>

Prefer porting their semantic scenarios into the high-level DB winner seam rather than making a large private-helper test suite. Direct helper tests are justified only for artificial boundaries that are otherwise awkward to force (exact B/cycle ordering or marker parser edge cases).

### 12.4 PR #55 / #54 validation fixtures

PR #55's `research/session-lineage/benchmark/tests/test_focused_gate.py` proves the useful historical depth-14/size-15 memo-reuse shape and the earlier budget smoke. Reuse the scenario concept, **not** the retired B value or research-only algorithm abstraction.

PR #55 source: <https://github.com/Skywind5487/hermes-agent/pull/55>

Current #54 supersedes it with implementation validation requirements including 1999/2000/2001, 10k acyclic safety, K=1/3/10, cycle-at-B, and exact `lineage_work` / `lineage_bound_hit` reporting.

Source: <https://github.com/Skywind5487/hermes-agent/issues/54>.

---

## 13. PR #63 salvage map

PR #63 is prototype evidence only; #62 explicitly says not to merge it as-is.

| PR #63 piece | disposition | why |
|---|---|---|
| `_LINEAGE_NODE_SQL` narrow child+parent lookup | **SALVAGE CODE NARROWLY** | exactly the one-statement indexed edge evidence #68 needs |
| parent-bound `_branched_from` / `_delegate_from` parsing | **SALVAGE CODE NARROWLY** | matches #58 foreign-marker correction; retain conservative malformed-config handling |
| local `seen` before budget check | **SALVAGE CONCEPT / ORDER** | needed for cycle-at-B correctness |
| successful lookup increments work after `fetchone()` | **SALVAGE CONCEPT / ORDER** | aligns B work definition |
| query-local resolved-root path compression | **SALVAGE CODE NARROWLY** | selected mechanism |
| `_read_ctx()` + explicit `BEGIN` around multi-statement winner phase | **SALVAGE CODE NARROWLY** | correct snapshot seam from #50 |
| early break after `result_limit` roots | **SALVAGE CONCEPT** | mandatory early-K |
| FTS winner snippet after roots known | **SALVAGE CONCEPT** | preserves deferred candidate snippet work |
| positive branch/delegate/tool/foreign-marker fixtures | **SALVAGE TEST SCENARIOS** | good semantic fixtures; move toward high-level DB seam |
| historical depth-14 memo-reuse fixture | **SALVAGE TEST SCENARIO** | production-shaped tail reuse receipt |
| whole `hermes_state_lineage.py` subclass | **REIMPLEMENT / DO NOT TRANSPLANT** | duplicates mature candidate/ranking stack and creates drift surface |
| duplicated `search_session_winners()` candidate SQL | **DROP** | current `dev` has advanced; canonical search stack belongs in `hermes_state_search.py` |
| `_LINEAGE_WORK_BUDGET = 1500` | **DROP** | #68/#54 froze 2000; 1500 retired |
| `memo: Dict[str,str]` resolved-only | **REIMPLEMENT** | #68 requires semantic unresolved memo for missing/cycle |
| missing-parent/cycle returns `None` without memo | **REIMPLEMENT** | misses required zero-work reuse |
| raw candidate iteration | **REIMPLEMENT** | #68 requires ranked distinct owner candidates first |
| only `lineage_work` + `lineage_bound_hit` stats | **EXTEND** | #68 asks inspected/memo/accepted observability too |
| no tool truncation warning | **ADD** | safe prefix must be explicitly incomplete |
| tool `_resolve_lineage()` fallback `resolver(...) or session_id` | **REIMPLEMENT CAREFULLY** | must not turn semantic unresolved into a fabricated production root |
| title first-anchor point query | **SALVAGE CODE NARROWLY** | fixes existing bounded-hydration test; wrap as DB/read helper if practical |

### Why whole-PR cherry-pick is especially risky now

Current `dev` is 11 commits ahead of PR #63's base and `hermes_state_search.py` alone changed +233/-35 in that interval. A parallel copied search implementation would immediately carry two versions of FTS/ranking policy. #68 needs a lineage resolver, not a second search engine.

---

## 14. Commit-sized implementation plan

### Commit 1 — RED contracts

**Files**

- `tests/test_session_search_sql_winners.py`
- `tests/tools/test_session_search.py`
- optionally a very small direct resolver test file only for exact artificial B/cycle-order boundaries

**Add/flip tests**

- compression-only edge semantics;
- distinct-owner best-anchor preservation;
- early-K;
- unresolved memo reuse;
- B 1999/2000/2001 + cycle-at-B + no-poisoning;
- truncation safe prefix/tool warning;
- current/title parity;
- remove old depth-cap identity expectations.

Expected state: new tests fail against current generic-parent CTE/tool resolver.

### Commit 2 — DB resolver + winner seam

**File**

- `hermes_state_search.py`

**Changes**

1. add `B=2000` resolver state/sentinels + narrow point SQL;
2. add private on-connection resolver with memo → seen → B → lookup ordering;
3. add public thin `resolve_compression_lineage()` wrapper for tool semantics;
4. preserve current route/candidate construction;
5. project ranked distinct owner candidates from bounded `candidate_hits`;
6. run candidate + resolver + ranking-sensitive winner/snippet work under one `_read_ctx()` explicit read transaction;
7. early-K and stop whole scan on B exhaustion;
8. add exact stats fields;
9. remove `lineage_depth_cap` from identity logic (retain signature temporarily only if a compatibility caller requires it, but ignore/deprecate it rather than silently enforcing depth identity).

Do **not** introduce `hermes_state_lineage.py` unless an actual import-cycle/file-size blocker appears during implementation.

### Commit 3 — tool parity + response contract

**File**

- `tools/session_search_tool.py`
- accompanying tool tests

**Changes**

1. route `_resolve_lineage()` to shared compression resolver semantics;
2. ensure production unresolved never falls back to generic ancestry;
3. make DB winner exclusion consume raw current/title session identities for same-snapshot re-resolution where ranking-sensitive;
4. salvage bounded title first-anchor lookup; no `get_messages()` transcript load;
5. expose discovery `truncated` and B-hit warning;
6. fix empty-prefix B hit so it cannot claim “No matching sessions found”;
7. add lineage counters to existing logs where useful.

### Commit 4 — validation/cleanup only if needed

No design changes. Run focused and broader session-search tests, ruff/type checks used by the repo, and record any production-shaped #54 instrumentation receipt. Keep benchmark data separate from production code.

---

## 15. Validation commands / acceptance matrix

Minimum focused test pass after implementation:

```bash
pytest -q tests/test_session_search_sql_winners.py tests/tools/test_session_search.py
```

If a direct resolver boundary file is added:

```bash
pytest -q tests/test_session_search_compression_lineage.py
```

Then run the neighboring session-search surface rather than only the new tests:

```bash
pytest -q tests/test_session_search*.py tests/tools/test_session_search.py
```

Static check the touched files with the repository's normal lint command (at minimum the equivalent ruff check for the changed Python paths).

Required acceptance cells, independent of wall time:

| cell | expected |
|---|---|
| K=1 / 3 / 10 normal shallow | exact ranking, early-K, no bound hit |
| depth14/size15 shared lineage | memo reuse, same winner identity, bounded work |
| required work 1999 | complete |
| required work 2000 | complete; root/cycle/memo proof allowed at boundary |
| required lookup 2001 | stop scan, `lineage_bound_hit=true`, safe prefix only |
| 2-node / long cycle | semantic unresolved, memo-reused |
| tail→cycle at `work==B` with cycle already visible | cycle classification, not bound hit |
| B reached before next node needed to prove cycle | bound hit, no unresolved memo poisoning |
| missing parent | semantic unresolved, later reuse zero work |
| fresh query after prior B exhaustion | fresh memo/B can resolve normally |
| unicode61 / trigram / LIKE | route/rank/anchor behavior preserved |
| current/title exclusion | same compression-root meaning as DB winners |
| B hit + zero winners | explicit incomplete warning, never “No matching sessions” |
| non-WAL fallback | no nested-lock deadlock; same correctness |

#54's real-ranked-prefix replay remains useful diagnostic evidence, not a blocker absent contradictory results.

---

## 16. Known pitfalls for `/implement`

1. **Do not cherry-pick PR #63 wholesale.** Its copied candidate stack is the biggest long-term regression risk.
2. **Do not call `get_session()` inside the on-connection resolver.** It obscures B accounting, adds statements/helpers, and can nest `_read_ctx()`/locks.
3. **Do not count attempts as B work.** Only successfully fetched lineage-node rows count.
4. **Do not check B before memo/seen.** That misclassifies cycle/memo proof at exactly `work==B`.
5. **Do not memoize B exhaustion as unresolved.** That poisons later candidates and future semantics.
6. **Do not keep depth=64 as hidden identity semantics.** #51 established it as defensive mechanism, not product meaning.
7. **Do not resolve raw message candidates repeatedly.** Owner dedupe must precede lineage traversal.
8. **Do not lose the message anchor while deduping owners/roots.** Result hydration must stay on the winning `(session_id,message_id)`.
9. **Do not reorder away source priority.** Current final semantics use `source_priority,candidate_order`.
10. **Do not return normal “no matches” language on bound exhaustion.** The result is incomplete, not negative evidence.
11. **Do not use current lifecycle helpers as the identity oracle.** `_COMPRESSION_CHILD_SQL` is incomplete; `find_live_compression_child()` has forward-recovery-only constraints.
12. **Do not nest a public resolver call inside the winner's `_read_ctx()`.** Use the private same-connection kernel, especially because non-WAL fallback holds a non-reentrant lock.
13. **Do not let tool current/title use generic parent fallback in production.** A compatibility fallback may exist only for old mocks/test doubles.
14. **Do not expand scope into persistent root caches/materialized schema/TEMP/fixed-depth/new algorithm families.** #68 froze the mechanism.

---

## 17. Final handoff state

**READY FOR IMPLEMENTATION against `BASE_SHA=bdf2fc218264538c4f3238b58532488fe665ff9e`.**

No unresolved research question blocks coding. The implementation seam is local and the donor/test evidence is sufficient. If `dev` moves before `/implement`, re-pin and re-check only these drift-sensitive anchors:

- `hermes_state_search.py::search_session_winners()` candidate CTE + execution block;
- `SessionDB._read_ctx()` behavior;
- `tools/session_search_tool.py::_resolve_lineage`, `_title_match_result`, `_discover`;
- `tests/test_session_search_sql_winners.py` and `tests/tools/test_session_search.py`.

Do not restart the #46/#47 archaeology unless one of those blobs materially changes.
