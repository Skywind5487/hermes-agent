# Issue #144 — Agent Session Metadata Discovery research receipt

Date: 2026-08-21

Status: **AUDITED / ACTIVE RESIDUAL / implementation seam provisional**

## Backlinks

- `/to-spec`: [#144](https://github.com/Skywind5487/hermes-agent/issues/144) and parent composition spec [#106](https://github.com/Skywind5487/hermes-agent/issues/106)
- `/to-tickets`: parent [#109](https://github.com/Skywind5487/hermes-agent/issues/109), metadata [#128](https://github.com/Skywind5487/hermes-agent/issues/128), lineage [#129](https://github.com/Skywind5487/hermes-agent/issues/129), exact title [#130](https://github.com/Skywind5487/hermes-agent/issues/130), integration [#144](https://github.com/Skywind5487/hermes-agent/issues/144)
- `/wayfinder`: [#134](https://github.com/Skywind5487/hermes-agent/issues/134)
- `/recon`: `RECON FINAL @ 5b82fdfa0d1bd3b0701b61cbcf438db8c1a9444e` on #144 (direct link added after the issue comment is posted)

## Source receipt

This audit intentionally pins every moving component separately rather than pretending one branch already contains the composed feature.

| Component | Primary source pin | State at audit |
| --- | --- | --- |
| upstream | `NousResearch/hermes-agent:main@fc9cbc872d8050c22f1192b16bc5ff4aed471e10` | current `main`; does not contain #91341/#67381 |
| metadata parent | `NousResearch/hermes-agent#91341@5b82fdfa0d1bd3b0701b61cbcf438db8c1a9444e` | OPEN / unmerged |
| direct title prior art | `NousResearch/hermes-agent#67381@c0ff110c238fccc489aca69ebc0f49d339d4f2fe` | OPEN / unmerged |
| fork metadata line | `Skywind5487/hermes-agent#138@6da18e9f3bb9e13eebd0371d38f3e38d4fd3574a` | CLOSED / unmerged; historical fork donor |
| fork lineage line | `fork/session-search-lineage@256401ca6a75a24fc82e3b566cdf5bf1f397b73c` | completed reviewed line, including two post-review cleanups |
| fork exact-title line | `fork/session-title-resolution@8b857a2aa5a88133ba4825cac51e887da9324803` | completed reviewed line |
| fork current main | `Skywind5487/hermes-agent:main@de5ad8f20cea6de37bce7bdb1131cd143087ab33` | documentation branch base |
| historical donor | `Skywind5487/hermes-agent:dev@fa5ed679cc6559c619038f327e6276f4b7e8d735` | provenance only, not current architecture authority |

Claim labels below use **VERIFIED** for primary-source observations, **INFERRED** for conclusions assembled from verified facts, **REPORTED** for statements present in another artifact but not independently reproduced here, and **UNKNOWN** when the primary record does not establish a fact.

## 查到什麼

### 1. Gate result: #144 is still live, and the residual is composition rather than another metadata index

**VERIFIED.** Current upstream `main@fc9cbc872...` has not merged #91341, #67381, #89553, #71912, #87636, or #75496. #91341 and #67381 remain open and unmerged. Therefore no moving upstream PR currently eliminates #144's Agent-side residual.

**VERIFIED.** #91341 already owns the shared metadata-search substrate. Its state layer exposes a routed metadata candidate engine over exactly stored `title`, logical session `id`, and gateway `display_name`; ordinary successful indexed routes do not redundantly execute the canonical LIKE fallback. `SessionDB.list_sessions_rich(search_query=...)` consumes that engine and applies the existing listability/compression-chain projection rules.

**VERIFIED.** #91341 does **not** modify `tools/session_search_tool.py`. At its current head, Agent discovery still has only:

1. deterministic title binding through `_title_match_result()` / `resolve_session_by_title()`; and
2. message-content discovery through `db.search_messages()`.

There is no fuzzy metadata candidate lane in Agent discovery.

**INFERRED.** This makes the smallest remaining #144 problem a wiring/composition change: consume the already-owned metadata seam in Agent discovery, then pass metadata and content candidates through the already-owned #129 compression-root winner boundary. Rebuilding FTS/schema/routing inside the tool would duplicate #128/#91341 ownership and violate #144's sequencing guard.

### 2. The current metadata parent already contains a direct composition precedent

**VERIFIED.** #91341's Desktop REST `GET /api/sessions/search` already composes three discovery lanes in this order: direct session-id candidates, shared metadata candidates via `list_sessions_rich(search_query=q)`, then message-content candidates. The metadata lane:

- forwards the same source filters;
- sets `include_archived=True`;
- orders by last activity;
- over-fetches a small bounded multiple (`safe_limit * 4`);
- reuses the endpoint's lineage hydration/dedup path rather than adding endpoint-local metadata SQL.

**INFERRED.** This is the strongest current architecture precedent for #144's candidate generation. The Agent should reuse the same shared listing seam, while retaining #129—not the Desktop endpoint's older local lineage helper—as the winner/dedup authority.

### 3. #129 is a usable winner boundary, but metadata candidates need an explicit no-message-anchor shape

**VERIFIED.** The current fork lineage line is `fork/session-search-lineage@256401ca6a75a24fc82e3b566cdf5bf1f397b73c`. It is two cleanup commits ahead of the earlier reviewed `cbaecd2ed...` fixed point, so `cbaecd2ed...` is stale as a handoff pin.

**VERIFIED.** `SessionDB.resolve_lineage_winners(...)` consumes a **pre-ranked bounded candidate list**, resolves only positive compression continuations as identity, preserves generic branch/delegation ancestry as distinct roots, applies current-context exclusion, uses one query-local memo/read snapshot/work budget, stops early at K, and returns winner rows without transcript hydration.

**VERIFIED.** Its present docstring describes candidates as message hits carrying both `session_id` and message `id`. Mechanically, the implementation already ignores missing `id` values in the batched compacted-message lookup; the remaining incompatibility is mainly the caller's hydration path, which currently assumes every winner has a message anchor and calls `get_anchored_view(hit_sid, msg_id, ...)` directly.

**VERIFIED.** A bounded existing primitive already avoids adding another state helper: `SessionSearchMixin.list_recent_user_messages(session_id, limit=1, include_inactive=True)` returns at most one real user-turn anchor. `get_anchored_view()` then supplies the bounded window/bookends.

**INFERRED.** Metadata candidates can therefore enter winner selection with `id=None` plus explicit provenance, and only **winning** metadata candidates need the bounded one-user-message anchor lookup before `get_anchored_view()`. This preserves #129's "dedup before hydration" invariant and avoids N transcript reads for N metadata candidates. A metadata-only session with no user turn can still return a metadata result with empty message slices rather than disappearing.

### 4. #130 remains a separate deterministic priority lane

**VERIFIED.** `fork/session-title-resolution@8b857a2aa5a88133ba4825cac51e887da9324803` implements the accepted literal-safe exact/numbered-title contract in the storage resolver. It deliberately does not implement fuzzy discovery or modify `session_search` callers.

**INFERRED.** #144 should keep exact-title matching as its existing separate first slot. Fuzzy metadata candidates must not be folded into `resolve_session_by_title()` and must not be allowed to outrank or duplicate the exact-title lineage. The current #129 tool already reserves one result slot for `title_result` and passes the title lineage as an exclusion to winner selection; that is the correct composition skeleton to preserve.

### 5. Prior-art / upstream classification

| Artifact | Classification | What it establishes for #144 |
| --- | --- | --- |
| upstream #57685 | **MERGED / present authority** | `/sessions search` list/title/id baseline landed as a smaller safe implementation; reuse current listing seams rather than reconstructing broad gateway search. |
| upstream #91341 | **OPEN / unmerged / current parent seam** | Shared indexed metadata search over title/id/display_name plus Desktop REST composition precedent. This is the provisional parent #144 must refresh immediately before implementation. |
| upstream #67381 | **OPEN / unmerged / direct competing prior art** | Proves the real user need for partial title matching and the insufficiency of exact-only title discovery, but its architecture is title-only LIKE supplementation inside message search and removes deterministic exact-title handling. Evidence/credit, not the target composition shape. |
| upstream #89553, #71912, #87636, #75496 | **OPEN / unmerged / adjacent evidence** | Search/UI/title work remains useful evidence; none is in current main and none presently absorbs #144. |
| upstream #66247 | **CLOSED / unmerged / superseded-resubmitted** | Automated sweeper closed it as "implemented" by exact-title support; author explicitly recorded that this was incorrect and resubmitted the missing partial-title behavior as #67381. Do not cite the closure as proof fuzzy search landed. |
| upstream #71225 | **CLOSED / unmerged / superseded** | Author explicitly replaced the stale/dirty branch with clean replay #87636; use #87636 for current evidence. |
| upstream #62399 | **CLOSED / unmerged / independently absorbed** | Author closed it after the session-list search stack landed independently on upstream; its review still provides the ranking-after-LIMIT lesson, but the PR itself is not an unlanded residual. |
| upstream #57595 | **CLOSED / unmerged / partially salvaged** | Broad original gateway direction was superseded by merged #57685's narrower safe implementation; use #57685 as authority. |
| fork PR #138 | **CLOSED / unmerged / internal donor** | Completed #128 implementation/review evidence. The PR conversation does not state a definitive closure reason; #144 independently instructs us to follow #91341/replacement as the moving metadata parent, so #138 must not be treated as current upstream authority. |
| fork PR #135 | **CLOSED / unmerged / docs-only historical evidence** | Old #128 research note did not land on current fork main; it is not a canonical durable note for this audit. |

### 6. Visibility and ranking boundaries that must survive composition

**VERIFIED.** #129's winner logic is the authority for positive compression-root dedup, current-live exclusion, safe-prefix truncation on work-budget exhaustion, and preserving generic branch/delegation parentage as distinct identities.

**VERIFIED.** #91341's shared listing seam defaults to listable/non-hidden sessions and accepts `exclude_sources`; its metadata engine itself is not a replacement for #129's Agent-context visibility rules.

**INFERRED.** Candidate order can remain intentionally simple and deterministic at this stage:

1. exact-title result (separate priority slot);
2. bounded metadata candidates in the order returned by `list_sessions_rich(... order_by_last_active=True)`;
3. message-content candidates after the existing `_order_for_recall()` pass;
4. one call to `resolve_lineage_winners()` over the combined non-exact candidates.

This matches #91341's current metadata-before-content REST precedent and preserves each lane's existing internal order. It does **not** invent a new cross-lane relevance scorer.

### 7. Durable-research catalog status

**VERIFIED.** The current fork branch did not contain a `docs/research` catalog/index. Historical notes are on unmerged research branches/PRs.

This audit creates `docs/research/README.md` and makes this file the canonical home for #144 research facts. Historical #128/#130 research artifacts remain cited evidence rather than silently becoming duplicate canonical copies.

## 查不到什麼

1. **UNKNOWN (moving external fact):** the final accepted API/helper shape of #91341. It is open and may be amended, split, replaced, or merged before #144 implementation begins.
2. **UNKNOWN (moving external fact):** whether #67381 or another competing title-search PR lands first, and which pieces upstream maintainers may salvage into a different PR.
3. **UNKNOWN (not recorded):** the definitive reason fork PR #138 was closed unmerged. Its conversation records implementation/review/CI evidence but no authoritative closure explanation. The safe fact is only that it is closed-unmerged and #144 separately points at #91341 as current parent.
4. **UNKNOWN until composition exists:** exact line numbers after rebasing/cherry-picking #91341 + #129 + #130 into one implementation branch. Recon therefore pins line ranges to the individual source receipts and symbols, not invented future line numbers.

None of these unknowns blocks a current-source recon. The first two are explicit invalidation triggers requiring a tiny pre-implementation refresh.

## 為什麼查不到

The unresolved facts are not hidden local facts that more repository grep can recover. They depend on future maintainer decisions or an unstated historical decision:

- open PR heads and merge/split decisions are inherently moving external state;
- a closed PR with no recorded closure rationale does not justify guessing a motivation;
- composed-branch line numbers do not exist before the composition branch exists.

For everything currently observable, the audit checked primary GitHub state, relevant code at exact refs, issue/PR discussion for closure/supersession reasons, and the completed fork line branches.

## 研究者自我檢驗

- **Primary-source first:** current upstream branch/PR state, current fork branches, concrete source files, patches, and issue/PR comments were used before historical summaries.
- **Moving-target check performed:** upstream `main` and all directly relevant open PRs were refreshed during this audit rather than copied from #144's earlier comment.
- **Closed-unmerged reasons checked:** #66247, #71225, #62399, and #57595 were classified from their discussion/successor records. #138 is explicitly left UNKNOWN rather than receiving an invented reason.
- **No stale branch pin:** #129 was rechecked and found to be two commits beyond the earlier `cbaecd2ed...` review point; the receipt is updated to `256401ca...`.
- **No donor replay:** `dev@fa5ed679...` and fork #138 are provenance. Current architecture authority for metadata is #91341 plus its shared `list_sessions_rich(search_query=...)` seam.
- **No false "already merged" conclusion:** the historical #66247 sweeper closure was contradicted by the author and superseded by #67381; current main was checked directly.
- **No runtime reproduction claimed:** this task is research/recon only. The behavior conclusions about current code are static primary-source archaeology; the RED matrix belongs to the recon brief and implementation phase.
- **Scope challenge:** I considered wiring metadata by duplicating `_metadata_candidate_row_ids()` into the tool, merging fuzzy semantics into the exact-title resolver, and pre-hydrating metadata candidates. All three are worse: they respectively duplicate #128 ownership, violate #130 separation, or violate #129's dedup-before-hydration constraint.

## 結論與下一步

**Conclusion: #144 is ACTIVE and research-complete at this receipt.** It is not another Session Metadata Search implementation. The current minimal residual is to compose #91341's shared metadata listing seam into the #129 Agent winner pipeline while preserving #130's exact-title priority and performing transcript hydration only after a metadata candidate wins.

Implementation can start from the paired `/recon` brief without another repo-wide investigation. Immediately before RED, repeat only this moving-target gate:

1. refresh upstream `main` SHA;
2. refresh #91341 status/head and inspect whether `list_sessions_rich(search_query=...)` / metadata router ownership moved;
3. refresh #67381 status (and any successor named in its discussion);
4. confirm `fork/session-search-lineage` and `fork/session-title-resolution` heads have not advanced.

If #91341 lands unchanged, replace the provisional parent pin with the merge/main SHA and proceed. If it is split/replaced, rebind #144 to the replacement shared metadata seam; do **not** preserve the old helper name merely because this note pinned it.
