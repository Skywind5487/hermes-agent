# Issue #92 research — exact session-metadata priority before LIMIT

Status: **research complete; product intent stable, implementation attachment provisional**  
Research source pin: `Skywind5487/hermes-agent dev@fa5ed679cc6559c619038f327e6276f4b7e8d735`  
Upstream observation: `NousResearch/hermes-agent main@fc9cbc872d8050c22f1192b16bc5ff4aed471e10` and open PR `#91341@5b82fdfa0d1bd3b0701b61cbcf438db8c1a9444e`  
Issue: #92  
Predecessor research: #28 / research PR #93

This note answers one bounded question:

> After whole-store session-metadata discovery exists, what is the smallest ranking rule that prevents an old exact metadata identity match from being removed by the final result `LIMIT`, and where must that rule live so compression-lineage dedupe and cross-profile merging remain correct?

It deliberately does **not** redesign the metadata search engine, introduce BM25/fuzzy relevance, or rank message-content search.

## 查到什麼

### 1. Verified — #92 is still a real residual behavior, not an obsolete duplicate

The fork issue already has the right product invariant:

1. exact raw logical session ID first;
2. exact title / `display_name` under the current canonical metadata normalization second;
3. every other valid metadata candidate third;
4. within a tier, preserve the existing recency tie-break.

The important placement invariant is also still correct:

```text
whole-store metadata candidates
  -> classify the matching physical segment
  -> apply existing eligibility/filtering
  -> reverse compression closure
  -> visible logical root + MIN(segment tier)
  -> effective last-active
  -> ORDER BY root tier, recency
  -> LIMIT / OFFSET
  -> normal projection / hydration
```

A raw candidate must **not** be globally capped before compression-lineage closure. One logical conversation can own many matching physical compression segments; capping those rows first lets one conversation consume the candidate budget and starve another logical conversation.

Primary source: #92 and its implementation clarification comments.

### 2. Verified — the measured predecessor research supports a deterministic tier, not BM25

The predecessor research in #28 / PR #93 is not merged to `dev`, but the research artifact is intact at:

`docs/research/issue-28-session-metadata-ranking-before-limit.md` on `research/28-session-metadata-ranking`.

Its decisive observations are:

- FTS5/BM25 is not a common score across the Unicode, CJK, trigram, LIKE-fallback, and rebuild-gap candidate sources.
- An exact identity-like match is not guaranteed to have the best BM25 score.
- Ranking/capping raw physical rows before lineage dedupe can starve distinct logical conversations.
- In its 100k-row isolated benchmark, broad-query exact-tier ordering cost roughly 1.2–1.3x the recency-only reference, while raw FTS ranking cost roughly 2.5–3x in the medium/broad cells.
- In the adversarial 20,000-match fixture, one deliberately old exact-title row was absent from the recency top page and sat around rank 4,000 by BM25, while deterministic exact-tier ranking put it first.

These are **verified as recorded results in the repository research artifact**, not independently rerun in this audit. The benchmark remains disposable and reproducible rather than a production latency promise.

Primary source: PR #93 research artifact. Upstream SQLite FTS5 BM25/rank documentation cited by that artifact remains consistent with the conclusion that BM25 optimizes a different objective from exact-identity protection.

### 3. Verified — current fork `dev` already has the right candidate/lineage seam, but no match tier

Current fork `dev@fa5ed679...` contains merged PR #89 / issue #14 metadata routing.

The relevant current architecture is:

- `hermes_state.py :: _metadata_candidate_row_ids*` routes the bounded query to Unicode / CJK+Unicode / trigram / canonical LIKE fallback and returns stable `sessions.row_id` values.
- `hermes_state.py :: _metadata_candidate_roots_cte` maps whole-store candidate row IDs back to canonical `sessions` rows, reverse-closes compression-continuation edges, applies existing list eligibility, and emits visible eligible root IDs.
- `hermes_state.py :: list_sessions_rich` computes `_effective_last_active`, then currently orders only by recency before `LIMIT`.
- There is no `match_tier`/equivalent field in this path.

That means the fork does **not** need a second metadata scan and does **not** need to change the low-level Unicode/CJK/trigram lane APIs merely to implement #92. The canonical `sessions` join that already exists at candidate→root mapping is the natural place to classify exactness.

This is an implementation inference from verified current source, and is finalized separately by the recon audit.

### 4. Verified — compression semantics require tier to originate on the matching segment and aggregate with `MIN`

A visible logical conversation can be represented by a root plus one or more compression continuations. The metadata match may live on an old segment even when the projected live tip has a different title or `display_name`.

Therefore:

- classify the **candidate physical segment**;
- propagate the tier while walking backwards across the same canonical compression edges already used by listing;
- for each eligible visible root, retain `MIN(match_tier)`;
- only then compute forward-chain activity and page the roots.

Re-evaluating exactness only on the final projected tip is incorrect because it can erase the fact that a historical segment was an exact match.

### 5. Verified — exact ID and exact title/display-name are intentionally different field contracts

Current metadata-search tests establish that the logical session ID stays a **raw** field, while title and `display_name` participate in the canonical compact/normalization policy.

For #92 this implies:

- tier 0: case-insensitive exact equality on the raw logical session ID;
- tier 1: exact equality for title / `display_name` using the same current canonical metadata normalization contract already shared by the routed search (`compact_session_metadata_text` / `_session_metadata_compact_sql`), not a new hand-written punctuation rule;
- tier 2: every remaining valid candidate.

Do not compact logical IDs just to make ranking convenient; that would silently change the field semantics accepted by #14/#16.

### 6. Verified — cross-profile aggregation is a second ranking boundary

`hermes_cli/web_routers/profiles.py :: get_profiles_sessions` fans a query out to every profile DB, asks each DB for a bounded result set, appends those rows into `merged`, then sorts the combined rows by recency and slices the global page.

Consequently, a DB-local exact tier alone is insufficient: the cross-profile merge can erase the ordering by re-sorting exact and partial matches together on recency.

When `q` is active, a private match-tier value must survive at least until the aggregate global sort/slice. The public response must not gain a new ranking field merely as an implementation leak.

The global search order should be the same product rule as the single-DB order:

```text
match_tier ASC,
last_active DESC,
started_at DESC,
id DESC
```

and only then global `offset/limit`.

### 7. Verified — current tests already own the right RED seams

No new testing framework is required.

Single-DB whole-store / lineage / filters / paging behavior is already concentrated in:

`tests/test_session_metadata_picker_routing.py :: TestWholeStoreListingSearch`

Existing tests cover old matches outside the recent page, compression projection, multiple matching segments, branch/delegate/tool visibility, filters before `LIMIT`, >999 candidates, pins, and `include_children`.

Cross-profile whole-store search is already exercised in:

`tests/hermes_cli/test_web_server.py`

including an old match outside the recent page and a match in a second profile. What is missing is the exact-vs-newer-partial global ordering contract.

### 8. Verified — current benchmark can be extended rather than replaced

`scripts/benchmarks/session_metadata_picker.py` already builds disposable corpora and reports routed warm p50/p95 by query class. #92 should extend it with an adversarial exact-priority/high-cardinality cell rather than introduce a separate benchmark harness.

The existing #92 acceptance gate of roughly `<= 30%` warm p95 regression on the high-cardinality search path is consistent with the predecessor research result. Treat it as a regression guard, not an SLA.

## Prior art / upstream disposition

The audit used upstream PR/issue state as of 2026-08-21. Status matters because #92's product invariant is stable while its parent metadata implementation is moving.

| Upstream / fork work | Current disposition | Relationship to #92 |
|---|---|---|
| Fork #89 (`route session-metadata picker candidates through FTS`) | **MERGED into fork `dev`** | Current fork substrate. Provides the whole-store candidate→root seam that #92 can extend today. |
| Upstream #91341 (`indexed session metadata search`) | **OPEN, unmerged**; head `5b82fdfa...`; current main `fc9cbc8...`; live compare is diverged, 13 ahead / 21 behind | Provisional upstream parent/replacement for the metadata-search substrate. It does not implement exact-before-LIMIT priority. Must be refreshed before implementation. |
| Upstream #71912 (`display_name` in session search) | **OPEN, unmerged** | Narrow field-coverage fix. #91341 says FOLLOW; not exact-priority ranking. |
| Upstream #89553 (`Desktop title search`) | **OPEN, unmerged** | Different Desktop search endpoint; already uses exact→prefix→substring title ordering. Useful product prior art for deterministic tiers, but not the common metadata listing seam. #91341 says ABSORB/FOLLOW. |
| Upstream #67381 (`session_search` title substring) | **OPEN, unmerged / currently non-mergeable** | Agent message-search/title supplement path, not this metadata picker/listing path. Notes an older exact-title tool-layer fix already in main, but that is a different consumer and does not close #92. |
| Upstream #75496 (`sessions list/search options, pagination`) | **OPEN, unmerged** | Shares compression-chain/listing concerns and is a watch item; does not implement #92's exact metadata tier before global result limit. |
| Upstream #71225 (fuzzy session+skill search) | **CLOSED, unmerged** | Superseded by #87636 after review/correctness problems. Do not copy as parent architecture. |
| Upstream #87636 (clean replay of fuzzy search) | **OPEN, unmerged / currently non-mergeable** | Richer fuzzy/scored UX, deliberately outside #92's minimal deterministic exact-priority scope. Coexist rather than absorb. |
| Fork #28 / PR #93 research | #28 **CLOSED complete**; PR #93 **OPEN, research-only and unmerged** | Measurement/proof source for #92. The research conclusion remains useful even though the artifact has not landed on `dev`. |

I also searched current upstream source/PRs for an existing `match_tier` / equivalent session-metadata exact-priority implementation and found no equivalent implementation that would absorb #92.

### What is already in `main` vs merely proposed

The upstream repository has pieces of session-title identity search already landed in other paths (for example the exact-title tool-layer behavior referenced by #67381), but **the common indexed metadata candidate/listing path represented by #91341 is itself still unmerged**, and no current-main exact-before-LIMIT metadata tier was found.

So the correct disposition is:

- do not mark #92 absorbed by current upstream;
- do not bind #92 to the exact file shape of #91341 yet;
- carry the behavioral residual forward if #91341 lands;
- drop/absorb #92 only if an upstream replacement demonstrably implements the same exact-tier-before-logical-root-LIMIT contract, including multi-profile global merge.

## 查不到什麼

### 1. Unknown — the final upstream attachment seam

#91341 is still open and has no review discussion at the time of this audit. Its head is behind current upstream main and its architecture differs from current fork #89: it moves the metadata router into `hermes_state_search.py` and integrates row-ID candidates into the existing chain/listing filter differently.

Therefore the **exact future upstream symbol/SQL shape** where #92 should attach is not stable yet.

This is not a blocker for the product decision or current-source recon; it is an implementation preflight requirement.

### 2. Unknown — whether #91341 lands intact, is split, or is replaced

No maintainer review comment currently provides a landing decision. It may merge after rebase, be rewritten, or have parts absorbed elsewhere.

The only safe implementation rule is: refresh current upstream main and #91341 (or its accepted replacement) immediately before writing #92 code, then reconstruct only the surviving behavioral delta.

### 3. No canonical research index found on fork `dev`

`docs/research/` on current `dev` contains topic notes but no README/index/catalog file was present in the directory listing. I therefore did not invent a parallel catalog solely for this note. The canonical backlinks are this file, #92, predecessor #28/#93, and the `RECON FINAL @ <SHA>` issue comment.

## 為什麼查不到

The two material unknowns are process/topology unknowns, not missing technical research:

- upstream #91341 has not reached a stable reviewed/merged state;
- upstream main is moving while the PR remains open;
- no primary-source maintainer decision exists yet that can tell us which exact attachment seam will survive.

Guessing the final upstream file shape would violate the point of #92's own sequencing guard.

## 研究者自我檢驗

### Could this be solved by simply sorting raw FTS candidates?

No. That reintroduces the exact starvation class through compression segment multiplicity and makes LIKE/gap candidates incomparable with FTS scores.

### Could we run a second exact lookup and prepend those rows?

Technically yes, but it is the wrong architecture. It duplicates normalization/visibility/lineage logic and creates two discovery paths that can drift. Current fork source already joins candidate row IDs back to canonical session rows before root closure; exact classification belongs there.

### Could exactness be evaluated only after projecting to the live compression tip?

No. A historical segment may be the exact metadata match while the live tip has changed metadata. The tier must follow the matching segment to the root and aggregate with `MIN`.

### Could DB-local ranking be enough?

No. `/api/profiles/sessions` performs another global sort and `LIMIT`. Without carrying a private tier through that boundary, a newer partial match from another profile can outrank an older exact match.

### Am I overfitting to current fork #89 internals?

The implementation recommendation for current `dev` is intentionally source-specific, but the product invariant is not. The note explicitly marks the upstream attachment as provisional and requires a pre-implementation refresh. This avoids turning today's convenient CTE into a permanent architectural requirement.

### Is BM25/fuzzy scoring secretly needed for acceptable UX?

No evidence currently justifies it for this issue. The measured failure is an identity-protection problem, and a deterministic 0/1/2 tier fixes that problem with less policy and lower measured cost. Richer relevance remains separable follow-up work.

## 結論與下一步

### Research conclusion

**Proceed with #92 as a narrow residual feature.** The smallest defensible behavior is deterministic exact-priority on the current common metadata candidate path:

```text
0 = exact raw logical session ID
1 = exact normalized title or display_name
2 = all other valid metadata matches
```

Compute the tier on the matching physical segment, carry it through the existing compression reverse closure, aggregate `MIN` per visible logical root, order roots by tier then existing recency, and only then apply `LIMIT/OFFSET`. Carry the private tier through cross-profile global merge/slice and strip it before public response serialization.

Do not add BM25, fuzzy weights, a second lookup, persistent ranking columns, or raw-candidate pre-LIMIT.

### Mandatory implementation preflight

Before `/implement 92`:

1. fetch current `NousResearch/hermes-agent:main`;
2. refresh #91341 and determine whether it is still open, merged, split, or superseded;
3. search current main for an equivalent exact-priority contract;
4. if no equivalent exists, port only the behavioral residual onto the surviving metadata-search architecture;
5. pin the implementation/recon source SHA again if the attachment seam changed.

### RED → GREEN targets

Add RED cases to the existing suites for:

- old exact raw ID outranking newer partial matches;
- old exact title outranking newer partial title matches;
- old exact `display_name` outranking newer partial matches;
- normalized title/display exact semantics without compacting logical IDs;
- best tier from a historical compression segment surviving root projection;
- several matching segments in one lineage not starving another exact logical root;
- filters/archive/source/include-children behavior unchanged;
- matching pins obeying the same tier semantics without non-matching pin leakage;
- `offset/limit` applied after tier+root ordering;
- cross-profile old exact match outranking a newer partial match from another profile;
- private tier absent from public API response;
- high-cardinality benchmark remaining within the issue's approximate warm-p95 regression guard.

### Backlinks

- Issue: #92
- Predecessor measurement issue: #28
- Predecessor research PR: #93
- Current fork substrate: #89
- Provisional upstream parent: NousResearch/hermes-agent#91341
- Recon: issue #92 comment labelled `RECON FINAL @ fa5ed679cc6559c619038f327e6276f4b7e8d735`
