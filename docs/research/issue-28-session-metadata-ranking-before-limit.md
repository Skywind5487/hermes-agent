# Research: session metadata ranking before result LIMIT (#28)

**Date:** 2026-08-16  
**Ticket:** #28 — Evaluate session metadata ranking before result LIMIT  
**Pinned BASE_SHA:** `35c8564c9c0af3d75bcbdf1d793e7207e5528f06` — `dev` immediately after #14 / PR #89 acceptance  
**Status:** Research complete. A narrow deterministic exact-match protection is justified; FTS5 BM25/rank is not recommended as the common metadata ranking policy.

## Executive recommendation

Do **not** turn session-metadata lookup into a general relevance-ranking subsystem and do **not** use raw FTS5 `rank`/BM25 as the common ordering key.

The post-#14 implementation has a real top-N starvation defect, but the smallest fix is much narrower:

1. generate the complete routed metadata candidate set exactly as #14 does today;
2. map candidate rows through visibility + compression-lineage semantics before any result cap;
3. carry one deterministic, route-independent **exact-match tier** from each matching segment to its visible logical root;
4. order visible roots by `match_tier ASC`, then preserve the existing `_effective_last_active DESC, started_at DESC, id DESC` order;
5. apply `LIMIT/OFFSET` only after that root-level ordering;
6. propagate the tier through the multi-profile merge while search is active, then strip the internal field before returning the public row shape.

Recommended initial tiers:

- **tier 0:** exact raw logical session ID;
- **tier 1:** exact title or exact `display_name` under the already-accepted metadata representation contract (raw and, where the current query route deliberately uses compact representation, exact compact equality);
- **tier 2:** every other valid metadata match — keep current recency ordering.

Do **not** add prefix/word/fuzzy grades in the first implementation. They are a separate product-ranking policy and are not needed to fix the demonstrated starvation.

## 1. Residual-scope audit after #14

#28 was explicitly blocked on final #14 because #14 changes candidate generation, fallback behavior, lineage mapping, and where expensive projection begins. That dependency is now satisfied by PR #89 at the pinned base.

The important post-#14 shape is:

```text
raw query
  -> classify route
     -> unicode / trigram / (cjk + unicode) / canonical LIKE
  -> complete whole-store candidate row_ids (NO global candidate LIMIT)
  -> reverse compression closure + eligibility / source / archive / session_key filters
  -> visible logical roots
  -> forward compression chain + effective last-active
  -> ORDER BY effective_last_active DESC
  -> LIMIT / OFFSET
  -> tip projection / hydration
```

The candidate-routing work is already done. #28 therefore owns only the question of **ordering the already-valid logical result set before the final page limit**.

Current `list_sessions_rich(search_query=..., order_by_last_active=True)` still ends the search SQL with:

```sql
ORDER BY _effective_last_active DESC, s.started_at DESC, s.id DESC
LIMIT ? OFFSET ?
```

So matching currently answers:

> “Among all matching logical conversations, return the newest ones.”

It does **not** answer:

> “Protect an obviously stronger metadata match before the page cap.”

That distinction is the live #28 residual bug.

## 2. Consumer and LIMIT map

### `hermes_cli/session_listing.py::query_session_listing`

- Public helper default: `limit=10`.
- It asks `list_sessions_rich()` for `max(limit * 4, limit)` rows, then performs small caller-side cuts.
- Search ordering therefore comes from the DB's recency order.

Consequence: an exact but old match can still be absent from the over-fetched window.

### Gateway `/sessions search`

- Gateway asks `query_session_listing(..., limit=50)` while searching.
- The helper therefore fetches up to 200 DB rows.
- Gateway applies caller visibility and then returns only the first 10.

This is useful over-fetch for visibility, but it is **not relevance protection**. A valid exact match ranked below the newest 200 metadata matches still disappears.

### REST `/api/sessions/search`

- `safe_limit` is clamped to 1..100.
- Exact session-ID lookup is already protected by a direct B-tree lookup before broad metadata discovery.
- Metadata discovery then calls `list_sessions_rich(..., limit=max(safe_limit*4, safe_limit))`.
- Message-content FTS is a separate later lane.

Important: exact **ID** already has a special fast path here, but exact title / `display_name` do not receive equivalent top-N protection in the shared metadata seam.

### Multi-profile `/api/profiles/sessions?q=...`

- Each profile runs `list_sessions_rich(search_query=q)` independently.
- Results are merged and then globally re-sorted by recency before slicing.
- `per_profile` is capped at 500.

Therefore a future rank implemented only inside one profile DB would still be lost at the cross-profile merge. Any selected pre-limit relevance signal must survive until the global merge.

### Desktop resume picker

- Search is debounced by 250 ms.
- Active search asks the multi-profile backend for 200 results and disables client-side cmdk filtering.
- The server's ordering is therefore the picker ordering.

This is the clearest human-facing consumer of #28.

### AI `session_search(query=...)`

The AI long-term-recall tool does **not** use this metadata picker path for arbitrary metadata discovery.

Its discovery path is:

1. exact/numbered title binding via `resolve_session_by_title()`;
2. message-content `search_session_winners()` with its own ranked FTS candidate/winner pipeline.

Therefore #28 should **not** be justified as a redesign for AI session recall. The current concrete benefit is human-facing session picker/listing metadata lookup. Message-content ranking remains out of scope.

## 3. Why raw FTS5 `rank` / BM25 is the wrong common abstraction

SQLite FTS5 supports relevance sorting with the hidden `rank` column. By default it maps to `bm25()`, and SQLite documents that `ORDER BY rank` can be faster than calling `ORDER BY bm25(...)` directly, especially when a query is abandoned early or has `LIMIT`.

Primary reference: https://www.sqlite.org/fts5.html#the_bm25_function and https://www.sqlite.org/fts5.html#sorting_by_auxiliary_function_results

That capability is real, but it does not fit the semantics of the common Hermes metadata seam.

### 3.1 Not every candidate has an FTS score

The accepted router can return candidates from:

- Unicode `sessions_fts`;
- CJK `sessions_fts_cjk` **unioned with Unicode**;
- normalized `sessions_fts_trigram`;
- canonical LIKE fallback;
- `(P,H]` rebuild-gap supplementation from canonical `sessions` rows.

LIKE and gap-supplemented rows have no FTS5 `rank` at all.

Making them comparable would require inventing a synthetic score/fallback normalization policy — already a new ranking subsystem.

### 3.2 Scores from separate FTS indexes are not one shared scale

BM25 depends on the matched index's token frequencies, document lengths, and tokenizer-derived corpus statistics. Hermes deliberately has separate Unicode, CJK, and trigram indexes with different tokenizations/representations.

A raw score from `sessions_fts_cjk` is therefore not a principled common number to compare with a score from `sessions_fts`, and neither naturally covers the LIKE/gap lanes.

### 3.3 Column weighting immediately becomes product policy

FTS5 can weight title / ID / `display_name` differently through `bm25(table, w1, w2, ...)`.

That is technically easy but conceptually expensive: choosing those weights is exactly the richer relevance policy #28 wanted to avoid unless measurement proves it necessary.

### 3.4 BM25 does not protect exact matches by itself

An exact title match is not necessarily the highest-BM25 document. Frequency across fields, document length, and corpus statistics may outrank it.

That is not a BM25 bug — it is simply a different objective from “an exact metadata identity match must not disappear behind partial matches.”

## 4. Correct placement: ranking cannot precede lineage dedupe

#14 intentionally refuses to globally cap raw metadata candidates before lineage mapping. #28 must preserve that invariant.

One logical conversation may own multiple compression segments, and many segments may match the same query. If raw FTS rows are ranked and capped first:

```text
FTS rows -> ORDER BY rank LIMIT N -> lineage dedupe
```

then one conversation can consume most or all N raw slots and starve other valid logical conversations.

The safe shape is:

```text
all routed candidates
  -> candidate segment match tier
  -> reverse lineage closure + eligibility
  -> GROUP / dedupe by visible logical root, retaining BEST tier per root
  -> effective-last-active
  -> ORDER BY root_match_tier, recency
  -> LIMIT / OFFSET
```

The tier must be derived from the **matching segment**, not re-evaluated only on the projected tip. A historical compression segment can be the row whose title/display metadata matched even though the visible tip later changed metadata.

## 5. Isolated SQLite microbenchmark

### Method

This is a deliberately isolated scoring/cardinality benchmark, **not** a claim about exact end-to-end `SessionDB` latency.

Environment used for this research:

- Python 3.13.5;
- SQLite 3.46.1 via Python `sqlite3`;
- in-memory/disposable databases only;
- FTS5 trigram table with three metadata fields;
- warm p50/p95 measured after cache warm-up;
- result page `LIMIT 40`;
- corpus sizes up to 100,000 session rows.

Compared:

1. current-style recency order after MATCH;
2. `ORDER BY ft.rank LIMIT 40`;
3. deterministic exact-tier then recency.

### 100k-row cardinality results

| Query class | Matches | Recency p50 / p95 ms | FTS rank p50 / p95 ms | Exact-tier p50 / p95 ms |
|---|---:|---:|---:|---:|
| rare | 1 | 0.175 / 0.210 | 0.536 / 0.566 | 0.172 / 0.190 |
| selective | 99 | 1.265 / 1.303 | 3.769 / 4.136 | 1.305 / 1.450 |
| medium | 10,000 | 14.516 / 15.048 | 35.868 / 41.302 | 17.442 / 17.758 |
| broad | 49,999 | 60.641 / 69.850 | 163.430 / 168.758 | 77.385 / 83.595 |

Directional result:

- FTS rank cost about 2.5–3x the recency-sort path in the medium/broad cells;
- exact-tier remained roughly 1.2–1.3x in the broad cells;
- for rare/selective queries the absolute cost of either choice is tiny.

### 10k-row reference

| Query class | Matches | Recency p50 / p95 ms | FTS rank p50 / p95 ms | Exact-tier p50 / p95 ms |
|---|---:|---:|---:|---:|
| rare | 1 | 0.155 / 0.201 | 0.463 / 0.615 | 0.154 / 0.200 |
| selective | 9 | 0.284 / 0.439 | 0.890 / 1.031 | 0.292 / 0.368 |
| medium | 1,000 | 1.325 / 3.161 | 3.771 / 4.972 | 1.645 / 2.516 |
| broad | 4,999 | 4.760 / 5.060 | 13.995 / 21.017 | 6.383 / 6.677 |

These measurements are strong enough to reject “BM25 is free so use it” but should not be used as absolute production latency promises.

## 6. Quality/adversarial fixtures

### 6.1 Old exact title vs many partial matches

100k-row corpus, query `needle`, 20,000 matching rows. One deliberately old row had exactly:

```text
title = "needle"
```

Other rows contained the term as partial metadata matches; 3,999 of them matched strongly in multiple FTS fields.

Observed:

- current recency top-40: old exact title **missed**;
- raw BM25: old exact title ranked approximately **#4000 / 20000**;
- deterministic exact-tier: old exact title ranked **#1**.

This is the decisive quality result. BM25 is not the primitive needed to protect identity-like exact matches.

### 6.2 Raw rank-before-LIMIT lineage starvation

Adversarial corpus:

- one logical root owns 60 matching compression segments;
- those segments are deliberately strong metadata matches;
- 60 other logical roots also have valid matches;
- raw page cap is 40.

Observed unsafe pipeline:

```text
ORDER BY rank LIMIT 40 -> lineage dedupe
```

returned **40 raw rows from one logical root**, yielding only **1 distinct conversation** after dedupe.

Therefore any implementation that caps FTS rows by relevance before #14's root mapping is correctness-invalid even if its individual row ranking looks good.

## 7. Selected policy: exact protection, not general relevance scoring

### 7.1 Why exact protection is justified

There is a concrete user-visible failure mode:

- the desired conversation is definitely in the whole-store candidate set;
- its metadata exactly matches what the user typed;
- it is old;
- many newer partial matches consume the final page window;
- the exact match disappears.

This can happen on CLI/gateway/Desktop/multi-profile listing surfaces today.

### 7.2 Why stop at exact protection

There is no measured need in this ticket for:

- fuzzy edit distance;
- word-vs-prefix-vs-substring numeric grades;
- field-weight tuning;
- BM25 score normalization across routes;
- a persistent ranking configuration.

Upstream open PR #71225 is useful evidence that a richer exact/prefix/word/substring/fuzzy policy can be built, but it is open/unmerged prior art and is much broader than the demonstrated Hermes defect. It should not be imported as the answer to #28.

### 7.3 Proposed deterministic tiers

The exact comparison must reuse #16/#14's accepted representation rules, not create a second normalization dialect.

Proposed first version:

```text
0  exact raw session id
1  exact title OR exact display_name
2  every other routed metadata match
```

For compact/infix routes, exact title/display equality may additionally recognize equality under the canonical `compact_session_metadata_text()` representation when the query itself is being served under that compact contract. Keep raw session ID raw.

If implementation research finds ambiguity here, choose the **narrower raw-exact interpretation first** rather than expanding ranking semantics.

Within one tier, preserve the current ordering exactly:

```text
_effective_last_active DESC,
s.started_at DESC,
s.id DESC
```

## 8. Proposed implementation seam

The smallest implementation stays inside the #14 common metadata seam.

### Candidate phase

Extend candidate data enough to compute a small integer tier from canonical metadata values. Do **not** add a candidate count cap.

Possible shapes:

- return `(row_id, tier)` rather than row_id only; or
- retain candidate row IDs and compute tier when joining canonical `sessions` in the candidate-root CTE.

The latter is preferable if it avoids widening every low-level FTS lane API.

### Root mapping

In `_metadata_candidate_roots_cte` or an adjacent search-only CTE:

- map every candidate segment to the same eligible roots as today;
- aggregate `MIN(match_tier)` for each visible root;
- preserve all existing source/archive/session_key/branch/delegation/compression semantics.

### Final page query

Search-only ordering becomes conceptually:

```sql
ORDER BY er.match_tier ASC,
         _effective_last_active DESC,
         s.started_at DESC,
         s.id DESC
LIMIT ? OFFSET ?
```

No-search listing behavior is unchanged.

### Multi-profile merge

When `q` is active, `/api/profiles/sessions` must merge by:

```text
(match_tier ASC, last_active DESC, started_at DESC, id DESC)
```

not recency alone.

Carry the tier as a private/internal row field only as long as required for that merge and strip it from the stable public response shape unless the UI has a separately approved need to expose match metadata.

## 9. RED-first tests for the implementation ticket

1. old exact title behind more than one page of newer partial matches surfaces first;
2. exact raw ID surfaces first through the shared metadata seam;
3. exact `display_name` surfaces before newer title/display partial matches;
4. compact exact-equivalent title/display behavior matches the already-accepted #16 policy and does not compact IDs;
5. 60 matching segments in one compression lineage cannot starve other roots;
6. the best tier from any matching compression segment propagates to its logical root even when projected tip metadata differs;
7. source filter, archive filter, `session_key`, branch/delegate/tool boundaries remain unchanged;
8. CJK+Unicode union uses the same deterministic tier and does not compare raw BM25 values across indexes;
9. direct LIKE fallback gets identical exact-tier behavior;
10. Unicode/trigram rebuild-gap supplemented rows get identical exact-tier behavior;
11. search-constrained pinned backfill still cannot leak a non-matching pin;
12. multi-profile global merge keeps an exact match from profile B above newer partial matches from profile A;
13. explicit FTS query syntax that has no exact metadata equality preserves recency among its non-exact results;
14. empty-query recent listing is byte/ordering compatible with current behavior.

## 10. Benchmark / acceptance gate for implementation

Extend the existing disposable `scripts/benchmarks/session_metadata_picker.py` instead of introducing a second benchmark framework.

Required cells:

- 1k / 10k / 100k sessions;
- rare / selective / 10%-match / ~50%-match cardinality;
- old exact title / exact display / exact raw ID fixtures;
- many-segments-one-lineage adversarial fixture;
- Unicode, trigram, CJK+Unicode (where tokenizer is available), direct LIKE, and rebuild-gap supplementation;
- single-profile and multi-profile merge correctness.

Primary acceptance rules:

- **correctness:** exact protected match is never lost to page LIMIT;
- **correctness:** no candidate cap occurs before eligibility + lineage/root dedupe;
- **correctness:** route/fallback choice and #14 recall remain unchanged;
- **performance:** high-cardinality warm p95 should stay within about **30%** of the current routed search on the same benchmark host, with absolute deltas recorded separately;
- **performance:** reject a design showing BM25-like multi-x regression on broad matches unless a later ticket demonstrates a correspondingly material relevance gain;
- **UX context:** Desktop currently debounces whole-store search at 250 ms, so end-to-end measurements should be reported against that interaction envelope, but the ratio gate is the portable primary criterion.

The 30% threshold is intentionally a guardrail, not a promise tied to the isolated benchmark hardware.

## 11. Implementation plan

One implementation ticket is sufficient; no new subsystem split is warranted.

Suggested commit shape:

1. **RED exact-protection fixtures** — exact title/ID/display, old-vs-new starvation, lineage duplicate starvation, multi-profile ordering.
2. **Root-level deterministic tier** — compute/aggregate exact tier after #14 candidate generation, order before final LIMIT, preserve recency ties.
3. **Cross-profile propagation** — carry private tier through merge and strip it at the response boundary.
4. **Benchmark/receipts** — extend `session_metadata_picker.py`, record cardinality + adversarial results, update comments/docs only where needed.

## 12. Explicit non-goals

- No message-content FTS ranking redesign.
- No change to `search_session_winners()` ranking.
- No fuzzy search subsystem.
- No BM25 field-weight tuning.
- No normalized score shared across Unicode/CJK/trigram/LIKE.
- No persistent ranking columns or materialized lineage-root schema.
- No candidate `LIMIT` before visibility/lineage/root dedupe.
- No change to #14's one-snapshot routing/fallback contract.
- No attempt to fold exact title resume binding (`resolve_session_by_title`) into broad metadata discovery.

## 13. Final answers to #28

### Does pre-limit ranking improve useful top-N recall enough to justify cost?

**Yes, narrowly:** protecting exact metadata identity matches before the final logical-root LIMIT fixes a demonstrated starvation case at modest measured cost. A broad relevance scorer is not justified.

### How does latency scale with match cardinality?

The isolated benchmark shows scoring cost becomes material with broad match cardinality. Raw FTS rank was roughly 2.5–3x the recency path in medium/broad cells; exact-tier was roughly 1.2–1.3x in the broad cells.

### Can FTS5 rank/BM25 be used directly?

**Not as the common metadata policy.** It lacks scores for LIKE/gap candidates, is not a shared scale across separate tokenizers/indexes, does not guarantee exact-match protection, and is unsafe to LIMIT before lineage dedupe.

### Is a simpler deterministic tier preferable?

**Yes.** Exact ID > exact title/display > all other matches, with existing recency order inside each tier.

### Where should dedupe/over-fetch happen?

Keep #14's complete candidate generation. Eligibility and compression-lineage/root mapping happen before the page cap. Aggregate the best tier per visible logical root, then order and LIMIT. Caller-side over-fetch remains only for caller-specific visibility/dedupe needs; it is not the relevance mechanism.

### Safe acceptance budget?

Use result correctness first and a same-host relative benchmark gate. Start with <=30% warm-p95 regression on high-cardinality routed-search cells; record absolute latency and Desktop's 250 ms debounce envelope as context.

## Sources / prior art

Primary repository sources at pinned `dev`:

- `hermes_state.py` — routed metadata candidate APIs, candidate-root CTE, `list_sessions_rich` search SQL;
- `hermes_state_common.py` — Unicode and trigram session metadata schemas + canonical compact policy;
- `hermes_cli/session_listing.py` — CLI/gateway shared listing over-fetch;
- `gateway/slash_commands.py` — gateway `/sessions search` visibility + final limit;
- `hermes_cli/web_routers/sessions.py` — REST session search exact-ID / metadata / message-content lanes;
- `hermes_cli/web_routers/profiles.py` — multi-profile merge/slice;
- `apps/desktop/src/components/session-picker.tsx` — server-backed whole-store resume picker;
- `tools/session_search_tool.py` — AI discovery path proving arbitrary metadata picker ranking is not its current search seam;
- `scripts/benchmarks/session_metadata_picker.py` — existing disposable #14 benchmark to extend.

Authoritative external reference:

- SQLite FTS5 documentation: https://www.sqlite.org/fts5.html

Open/unmerged evidence only:

- NousResearch/hermes-agent PR #71225 — richer frontend exact/prefix/word/substring/fuzzy ranking prior art. Useful as evidence, not an implementation baseline.
