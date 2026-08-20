# #109 final upstream prior-art and conflict gate

Date: 2026-08-20
Requesting ticket: #109
Feature branch at research start: `fork/session-search@2a2690844389c67dac979b9e328763945b61e4b3`
Composition base: `NousResearch/hermes-agent@243352e7b8bddc9f33eba1b6506810f8dd88beaa`
Current upstream checked: `NousResearch/hermes-agent main@f43eabee5f36e11448086ee8ee17c499958e81bf`

Backlinks: #109 · #128 · #129 · #130 · topology #134 · `docs/design/session-search.md`

This note is the final upstream gate for the composed #109 Session Search feature. It does not replace the child research notes; it refreshes their moving upstream evidence and asks one narrower question: **since the fixed composition base, has upstream absorbed the remaining contracts or introduced a conflict that changes the #109 landing decision?**

## Sources read

Primary sources only:

- #109 issue body and comments, including the composition/code-review accounting: https://github.com/Skywind5487/hermes-agent/issues/109
- composed feature contract: `docs/design/session-search.md`
- #128 research PR / note: https://github.com/Skywind5487/hermes-agent/pull/135
- #129 research PR / note: https://github.com/Skywind5487/hermes-agent/pull/136
- #130 research note: `docs/research/issue-130-current-upstream-session-title-resolution.md` at its research commit
- current upstream `main`: https://github.com/NousResearch/hermes-agent/commit/f43eabee5f36e11448086ee8ee17c499958e81bf
- current upstream PRs / commits cited below.

The user also pointed to walkthrough commit comment `197007752` on the composed branch. The commit is verified to have one commit comment, but the available GitHub connector does not expose a commit-comment read action, so the comment body is **reported but unverified** here and is not used as evidence for any conclusion.

## 查到什麼

### 1. Current upstream moved a lot, but the #109 search core did not

**Verified.** The composition base is the merge-base of current upstream, and current upstream is 921 commits ahead of it:

- base: `243352e7b8bddc9f33eba1b6506810f8dd88beaa`
- current upstream: `f43eabee5f36e11448086ee8ee17c499958e81bf`
- compare: https://github.com/NousResearch/hermes-agent/compare/243352e7b8bddc9f33eba1b6506810f8dd88beaa...f43eabee5f36e11448086ee8ee17c499958e81bf

A blob-level audit of the production files touched by #109 shows that the three most important search/lineage implementation files are **byte-identical** between the fixed base and current upstream:

| file | base blob | current-main blob | drift? |
|---|---|---|---|
| `hermes_state_common.py` | `58b3744859d152fa7bb3f6af59b739c051221a47` | same | no |
| `hermes_state_search.py` | `e8d29f413ee85ee64e0139d4d0b570097d1b7cec` | same | no |
| `tools/session_search_tool.py` | `c5752f5ca4adfbdd5d5a69b15783ddaed7875b6f` | same | no |
| `hermes_cli/web_routers/sessions.py` | `8b37f9fa9085c922e492c94d4f6ae3b95017de52` | same | no |

Files with verified upstream drift are `hermes_state.py`, `hermes_state_schema.py`, `apps/desktop/src/app/chat/sidebar/index.tsx`, and `apps/desktop/src/types/hermes.ts`. Drift alone is not treated as a conflict; the relevant seams were checked separately below.

### 2. #128 metadata-search residual is still not upstream-owned

**Verified, high confidence.** Current upstream still does not contain the #128 metadata-index implementation (`_FTS_SESSION_LANES`, `compact_session_metadata_text`, and the composed metadata-search substrate are absent from current main). The direct moving prior art remains unmerged:

- #89553 — **open, unmerged**: Desktop stored-title search/title surfacing. https://github.com/NousResearch/hermes-agent/pull/89553
- #71912 — **open, unmerged**: `display_name` in `list_sessions_rich`. https://github.com/NousResearch/hermes-agent/pull/71912
- #87636 — **open, unmerged**: Desktop/web fuzzy session search. It explicitly supersedes stale/dirty #71225. https://github.com/NousResearch/hermes-agent/pull/87636
- #67381 — **open, unmerged**: LIKE title substring matching inside `search_messages()`. Its ownership shape still mixes metadata discovery with the message-content lane and does not replace the #128/#130 split. https://github.com/NousResearch/hermes-agent/pull/67381
- #71225 — **closed, unmerged; superseded by #87636**, not accepted as upstream authority.

Previously established upstream authority remains authority rather than fork residual:

- #57685 — **already in main**: CLI/Gateway `list_sessions_rich(search_query=...)` seam and current listing behavior.

A new merged upstream change after the #109 base is #90357 / commit `20059cbc6993570ca52db4df7eb46286d6e1134c`: Desktop strips raw `>>>term<<<` FTS markers from search-result previews. It changes `searchResultToSession()` in the same sidebar file/function that #128 changes, but its behavior is **complementary**: upstream changes preview cleanup; #128 preserves/surfaces the stored result title. It does not absorb #128.

Source: https://github.com/NousResearch/hermes-agent/commit/20059cbc6993570ca52db4df7eb46286d6e1134c

### 3. #129 compression-lineage residual is still not upstream-owned

**Verified, high confidence.** Current upstream still has the generic parent-root discovery behavior in `tools/session_search_tool.py`; `resolve_lineage_winners` is absent. The file itself is byte-identical to the #109 base, so the #129 target seam has not drifted since composition.

The established merged authority remains in main:

- #12960 — compression-tip list/resume authority
- #38393 — Desktop compression-lineage search dedupe
- #39062 — ID/content lane composition through the compression keyspace
- #69544 — compacted/compression-history recall; salvages the useful part of #63144
- #86652 — reset recall; supersedes #85764

Closed/split provenance remains correctly classified:

- #63144 — **closed/unmerged; partially salvaged by #69544**
- #85764 — **closed/unmerged; superseded by #86652**
- #24111 / #66728 — **closed/unmerged provenance**, not current authority

Moving evidence refreshed on 2026-08-20:

- #81561 — **open, unmerged**. Adds strict compression-continuation traversal for a Desktop lineage navigator, but its API/UX contract and bounds are not the #129 query-global `B=2000` resolver contract. https://github.com/NousResearch/hermes-agent/pull/81561
- #53992 — **open, unmerged**. Compression-aware export stitching. https://github.com/NousResearch/hermes-agent/pull/53992
- #83829 — **open, unmerged**. Telegram session-search scoping; preserves compression-lineage recall. https://github.com/NousResearch/hermes-agent/pull/83829

#### New direct prior art: #75496 / #90619

This is the most important new upstream item since the child research snapshots.

- #75496 — **open, unmerged**. Adds unified CLI/Gateway session list/search, chain-aware dedupe, global ranking/pagination, lane-scoped content/title candidate pools, and touches `hermes_state.py`, `hermes_state_common.py`, `hermes_state_search.py` plus CLI/Gateway surfaces. https://github.com/NousResearch/hermes-agent/pull/75496
- #90619 — **closed, unmerged, duplicate**. Its branch contained the same cleanup/fixes; #75496 explicitly consolidates/supersedes it. https://github.com/NousResearch/hermes-agent/pull/90619

#75496 is therefore **high-overlap future prior art**, not present authority. If it merges, #129/#128 must be re-researched before rebasing because it occupies several of the same state/search seams. Its current PR contract does not prove the #129 positive-only continuation equivalence plus one query-local memo, fail-closed missing-parent/cycle behavior, early-K, and query-global `B=2000` work bound; it must not be treated as an already-equivalent replacement.

### 4. #130 literal-safe title-resolution residual is still present on current main

**Verified directly against current `main@f43eabee...`, high confidence.** `get_next_title_in_lineage()` still contains the greedy extraction:

`re.match(r'^.* #(\\d+)$', t)`

The same target code was already present at the fixed #109 base. Therefore the file-level drift in `hermes_state.py` did **not** repair this seam.

The direct upstream fix candidate is still:

- #41223 — **open, unmerged**: anchors lineage numbering to the resolved base and demonstrates the deeper-suffix bug. https://github.com/NousResearch/hermes-agent/pull/41223

The child research's partial-upstream-absorption result remains valid: upstream owns the literal LIKE escape helper / direct title candidate substrate, but it still does not own the strict shared `base + " #" + ASCII[0-9]+` predicate used consistently by both resolution and numbering.

### 5. Conflict classification

#### A. Internal #109 composition conflict

**Verified and already resolved.** #109's code-review comment records one composition conflict: #128 + #129 both touched the import seam in `hermes_state_search.py`. The resolution retained #128's metadata-search imports and #129's `_RESET_END_REASONS`; the imported symbols are used, no duplicate definitions/conflict markers remain, and bundle review accepted the resolution.

This is historical composition evidence, not an outstanding blocker.

#### B. Semantic conflict with current upstream main

**No semantic replacement/conflict found. High confidence.** The current upstream changes checked here do not invalidate the three residual contracts:

- #128 metadata search remains residual; post-base Desktop marker stripping is complementary.
- #129's core tool/search files have not changed since the fixed base.
- #130's target regex/grammar bug is still present on current main.
- post-base `hermes_state_schema.py` drift includes the v25 prompt-dedupe contention fix (`e99743500420e4e8fdee8de9a16ff04858afb701`), which is orthogonal to session-metadata indexing semantics.

#### C. Git/textual rebase conflict with current upstream main

**Unknown as a whole-tree binary answer; do not claim either “conflict” or “clean merge” without a merge-tree/PR mergeability check.**

What is verified:

- `hermes_state_search.py`, `hermes_state_common.py`, `tools/session_search_tool.py`, and the REST sessions router are unchanged upstream since the base; those #109 production seams have no base→current textual drift.
- Desktop `sidebar/index.tsx` changed upstream and in #109. Upstream #90357 modifies `searchResultToSession()` preview normalization while #128 modifies title propagation in that same helper. The semantics compose, but the edits are close enough that a future rebase deserves explicit manual attention.
- `apps/desktop/src/types/hermes.ts`, `hermes_state.py`, and `hermes_state_schema.py` also have upstream blob drift. Targeted inspection found no #109 semantic replacement in them, but a whole-tree Git merge was not executed.

The available GitHub connector exposes compare and merge-a-PR operations but no non-mutating `merge-tree` / mergeability simulation for an arbitrary branch pair, and there is no reason to create or merge a PR merely to manufacture that signal. The smallest verification before actually rebasing is therefore a local `git merge-tree` / trial rebase against the then-current upstream main.

#### D. Future conflict risk from open upstream PRs

**Verified risk, not a current conflict.** #75496 is the largest one: it changes the root state/search/listing substrate used by #128/#129. Because it is still open/unmerged, it does not change today's #109 ownership decision. If it merges before Phase 3 landing, re-run the upstream gate rather than mechanically resolving conflicts.

## 查不到什麼

1. The body of commit walkthrough comment `197007752`.
2. A definitive whole-tree Git answer for `fork/session-search` rebased/merged onto `upstream/main@f43eabee...`.

## 為什麼查不到

1. The connected GitHub tool can fetch commits, issues, PR comments, reviews, files, and compares, but exposes no read operation for commit comments. Fetching the commit proves one commit comment exists but does not expose its body. The user-supplied comment ID is therefore recorded but not promoted to evidence.
2. The connected GitHub tool has no non-mutating arbitrary `merge-tree` operation. Creating a PR or invoking a merge would mutate repository state and is unnecessary for this research gate. A local checkout/trial rebase is the concrete missing evidence.

## 研究者自我檢驗

Checks performed instead of inferring from branch names or stale child notes:

- Read #109 issue body/comments first and used its actual umbrella/child contract as scope.
- Read the actual #128/#129/#130 research contents, not just their issue titles.
- Resolved current upstream main directly (`f43eabee...`) and compared it to the fixed base; did not assume the child research pins were still current.
- Refreshed direct moving PR states instead of calling old “open” evidence current by memory.
- Classified #90619 as closed/unmerged **with reason** (duplicate/consolidated into #75496), rather than equating “closed” with rejected.
- Checked current source for #130's exact regex instead of assuming its prior finding remained true.
- Compared production-file blob SHAs to separate 921-commit repository churn from actual #109 seam drift.
- Did **not** equate “same file changed” with a Git conflict and did **not** equate “open PR overlaps” with upstream ownership.

Mistake corrected during this research: the first pass began by interpreting the branch name before reading #109. After the user's correction, the issue became the authority and all later searches were scoped to its documented #128/#129/#130 contracts.

No feasible evidence check identified during this pass remains skipped except the two concrete access/tool boundaries listed above.

## 結論與下一步

### Conclusion — high confidence

**Keep the composed #109 feature. Current upstream main has not absorbed its remaining #128/#129/#130 contracts, and no semantic conflict/blocker was found.** The existing fixed-base bundle acceptance therefore remains meaningful.

### Rebase status — medium confidence / deliberately narrower claim

**Do not label the branch “rebase-clean against current upstream” yet.** There is verified upstream drift in a small set of files, especially the Desktop sidebar helper, but no non-mutating whole-tree merge simulation was available in this research run. That is a landing/integration check, not evidence that the feature contract is obsolete.

### Smallest next verification

Immediately before Phase 3 integration/landing:

1. fetch the then-current upstream `main`;
2. run `git merge-tree` or a disposable trial rebase of `fork/session-search`;
3. if #75496 has merged by then, stop and re-run #128/#129 prior-art ownership before resolving any state/search conflicts;
4. carry upstream #90357's preview marker stripping together with #128's stored-title propagation if the sidebar hunk needs manual resolution;
5. re-run `tests/test_session_search_bundle.py` plus the child targeted suites after the rebase.
