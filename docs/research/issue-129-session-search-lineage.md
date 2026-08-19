# Issue #129 research — compression-aware Session Search lineage on current upstream

Status: **READY FOR IMPLEMENTATION after a fresh upstream refresh**  
Research date: **2026-08-19**  
Intent: fork issue [#129](https://github.com/Skywind5487/hermes-agent/issues/129), under [#109](https://github.com/Skywind5487/hermes-agent/issues/109)

## Evidence policy for this research

This is an **external-only** preflight.

- Fork issue #129 is used only as the frozen acceptance/behavior contract.
- Historical fork donors named by the ticket (#68/#66/#54/#46/#58/#62/PR #63) were **not** inspected and are not evidence for the findings below.
- Findings are based on current upstream source plus upstream PR/issue history, with merged/current upstream behavior taking precedence over open or closed-unmerged prior art.

Research-time pins:

- upstream `NousResearch/hermes-agent:main`: [`63565fa26b00a2096247064785c4380aafab2303`](https://github.com/NousResearch/hermes-agent/commit/63565fa26b00a2096247064785c4380aafab2303)
- fork clean `main` (branch base only, not behavioral evidence): `243352e7b8bddc9f33eba1b6506810f8dd88beaa`

The ticket's projection-time upstream pin (`395c70d...`) is stale. Refresh upstream again immediately before coding.

## Executive handoff

**#129 is still a real residual. It is not an already-upstream feature and it is not a cherry-pick task.**

Current upstream already owns substantial pieces of the problem:

1. list/resume projection has a positive compression-continuation concept and projects a logical conversation to its live compression tip (#12960, merged);
2. Desktop `/api/sessions/search` learned compression-lineage dedupe while keeping branch hits distinct (#38393, merged), and later ID/content lanes were composed through one dedupe keyspace (#39062, merged);
3. `session_search` can recall compacted/legacy-compressed content that left live context (#69544, merged, salvaged from #63144);
4. reset sessions are now recalled even though they have `parent_session_id` (#86652, merged, superseding #85764), explicitly proving that generic parent ancestry is not equivalent to compression continuity.

But the current **agent `session_search` discovery resolver still canonicalizes by generic `parent_session_id` ancestry**. At upstream `63565fa...`, `tools/session_search_tool.py::_discover()` computes `resolved_sid, _ = _resolve_to_parent(db, raw_sid)` and dedupes on that value. `_resolve_to_parent()` walks the parent chain to a root and separately records whether any hop was a compression hop; it does not make compression evidence the condition for root equivalence. The later current-session/reset/delegation checks are visibility exceptions layered on top of that generic-root model.

That conflicts with #129's frozen invariant:

> Only a **proven positive compression-continuation edge** may make two physical session rows the same logical conversation. Generic parent ancestry alone must not.

There is also no upstream implementation found for #129's exact query-global safety contract: **one query-local memo/path-compression map, `B=2000` successful uncached lineage-node row fetches, memo-only completion after B, safe-prefix truncation on the first required 2001st lookup, and mandatory early-K stop**.

Therefore the implementation should reconstruct only the residual lineage-equivalence/bounded-resolution seam on current upstream. Do not replay the old fork search pipeline and do not replace upstream-owned content-discovery, compaction visibility, Desktop composition, or list/resume projection.

## 1. Current upstream source: what is actually live

### 1.1 Agent `session_search`: dedupe exists, but its equivalence key is too broad

Current source: [`tools/session_search_tool.py` at `63565fa...`](https://github.com/NousResearch/hermes-agent/blob/63565fa26b00a2096247064785c4380aafab2303/tools/session_search_tool.py).

The current discovery pipeline is already candidate-first and hydration-late:

1. compute current lineage root;
2. run message FTS and preserve upstream ranking/source ordering;
3. dedupe raw hits into `seen_sessions`;
4. only after dedupe, hydrate the surviving raw hit with `get_anchored_view(hit_sid, msg_id, ...)`;
5. use root metadata for title/source/model while preserving the raw owning hit id for the anchored message window.

The important current code shape is:

```text
raw_sid = r["session_id"]
resolved_sid, _ = _resolve_to_parent(db, raw_sid)
...
if resolved_sid not in seen_sessions:
    row = dict(r)
    row["_lineage_root"] = resolved_sid
    seen_sessions[resolved_sid] = row
...
hit_sid = match_info.get("session_id") or lineage_root
view = db.get_anchored_view(hit_sid, msg_id, ...)
```

This means **"no dedupe exists" is no longer true**. The remaining defect is the meaning of `resolved_sid`.

`_resolve_to_parent()` currently walks the generic parent chain and returns `(root_id, has_compression_hop)`. The compression flag is used by visibility logic, but generic parent ancestry is still the dedupe/current-lineage identity. That is narrower than the old bug, but still wider than #129's accepted semantics.

Useful upstream behavior to preserve exactly while changing equivalence:

- original best-ranked raw owner + message anchor survive winner selection;
- expensive anchored-window hydration happens only after dedupe;
- cron/source ordering stays upstream-owned;
- in-place compacted rows and ended historical fragments can remain searchable when they have left live context;
- current live session remains excluded where appropriate;
- explicit read/scroll shapes remain separate from discovery orchestration.

### 1.2 Desktop search: already much closer to compression-only semantics

Merged upstream PR [#38393](https://github.com/NousResearch/hermes-agent/pull/38393) did **not** merely walk every parent to a root. Its implementation introduced a memoized `compression_root()` that follows a parent edge only when the parent ended with `end_reason='compression'` and the child's `started_at` is at/after the parent's `ended_at`; a non-compression branch stops at itself. Its regression explicitly keeps branch-specific hits on the branch.

That PR also returns the live compression tip as `session_id` and a durable root as `lineage_root`.

Merged PR [#39062](https://github.com/NousResearch/hermes-agent/pull/39062) then added SQL-bounded ID search and composed direct-ID + content matches through one compression-lineage dedupe keyspace.

The endpoint has since moved/split into `hermes_cli/web_routers/sessions.py`. Current open metadata PR #89553 shows the live composition helper `add_lineage_result(...)` still exists, but #89553 is #128 metadata-search territory and does not change #129 lineage semantics.

**Implication:** do not replace Desktop search wholesale. Reuse its existing composition/ranking/result projection and replace only the lineage-equivalence seam if current-main inspection shows a residual against #129's stricter marker/cycle/budget contract.

### 1.3 List/resume projection already owns live compression-tip behavior

Merged upstream PR [#12960](https://github.com/NousResearch/hermes-agent/pull/12960) added `SessionDB.get_compression_tip(session_id)` and `list_sessions_rich(project_compression_tips=True)`.

Its continuation rule is positive and directional: parent ended with `compression`, and the candidate child starts at/after the parent ended. It keeps delegate subagents and branches distinct, and surfaces one logical conversation at the live tip with `_lineage_root_id` metadata.

This is current authority for **listing/resume projection**, not a ready-made #129 query resolver. It has different directionality and no #129 query-global B/memo/truncation contract.

Do not force CLI/Gateway listing onto the `session_search` orchestration just to share code. Share semantics only where a narrow helper is demonstrably equivalent.

## 2. Merged upstream authority / partial overlap

| Upstream PR | Status at research time | What landed | #129 treatment |
|---|---|---|---|
| [#12960](https://github.com/NousResearch/hermes-agent/pull/12960) | **MERGED** `22efc81c...` | compression-tip projection for lists/resume; positive compression edge via end reason + timing | **REUSE substrate/semantics where equivalent**; do not replace list/resume pipeline |
| [#38393](https://github.com/NousResearch/hermes-agent/pull/38393) | **MERGED** `b91c3820...` | Desktop search dedupe; compression-only backward walk with branch stop; tip + root identity | **PRESENT/PARTIAL**; keep composition and branch behavior; inspect residual marker/cycle/B semantics only |
| [#39062](https://github.com/NousResearch/hermes-agent/pull/39062) | **MERGED** `580d9240...` | SQL-bounded ID search; ID/content share one compression-lineage keyspace | **PRESENT** composition behavior; do not rebuild |
| [#69544](https://github.com/NousResearch/hermes-agent/pull/69544) | **MERGED** `e907eccc...` | compacted and legacy compression history becomes discoverable; delegation remains hidden; salvaged #63144 | **PRESENT** recall/visibility behavior; preserve while fixing equivalence |
| [#86652](https://github.com/NousResearch/hermes-agent/pull/86652) | **MERGED** `1a06e70e...` | `/new`/reset parents are recalled although they share `parent_session_id`; supersedes #85764 | **PRESENT** and strong evidence generic parent != compression identity |

### Why #69544 and #86652 do not complete #129

These two merged fixes make the current generic-root model less harmful by adding visibility exceptions:

- #69544 asks whether content left live context due to compaction/compression;
- #86652 admits reset/ended parents that are no longer in the active transcript.

Neither changes the underlying agent-tool winner key from generic `_resolve_to_parent()` ancestry to a strictly proven compression-continuation equivalence relation.

#129 should preserve those visibility fixes but remove the need for generic-parent identity to stand in for conversation identity.

## 3. Superseded / closed-unmerged history

| Upstream PR | Status | Classification |
|---|---|---|
| [#63144](https://github.com/NousResearch/hermes-agent/pull/63144) | **CLOSED, unmerged** | explicitly salvaged by merged #69544; provenance only |
| [#85764](https://github.com/NousResearch/hermes-agent/pull/85764) | superseded | merged #86652 is authority |
| [#24111](https://github.com/NousResearch/hermes-agent/pull/24111) | **CLOSED, unmerged** | earlier tool-side compression-recall proposal; later merged recall work owns current behavior |
| [#66728](https://github.com/NousResearch/hermes-agent/pull/66728) | **CLOSED, unmerged** | unusually relevant strict backward-root design, but **not authority**; no matching helper is present on current main |

### #66728 is useful prior art, but must not be mistaken for landed code

#66728 proposed `_compression_lineage_root(session_id)` that walks upward only while:

- the parent ended with compression;
- the child is not a marked branch/delegate;
- the child is not a tool-source child.

Its rationale is directly relevant: a branch created after a compression-ended parent can satisfy a timing-only guard, so reverse traversal needs stronger stop evidence than generic timing.

It also used cycle protection, but with a 100-hop bound. That is **not** #129's accepted semantics: #129 requires traversal-local cycle detection with no semantic depth cap, plus a query-global `B=2000` DB-work bound.

Salvage ideas/tests only. Do not cherry-pick the closed PR.

## 4. Open upstream prior art — evidence only

| Upstream PR | Status | Relevant evidence | Do not confuse with |
|---|---|---|---|
| [#81561](https://github.com/NousResearch/hermes-agent/pull/81561) | **OPEN, unmerged** | strict compression-continuation traversal excluding ordinary branches, delegates/subagents, tool sessions and stale orphan-reaped children; fail-closed cycle/ambiguity handling | Desktop lineage navigator; 100-segment bound is not #129's B semantics |
| [#53992](https://github.com/NousResearch/hermes-agent/pull/53992) | **OPEN, unmerged** | export-side compression-only chain root/IDs; branch/delegate/tool not folded | export stitching, not Session Search ranking/winner resolution |
| [#83829](https://github.com/NousResearch/hermes-agent/pull/83829) | **OPEN, unmerged** | Telegram conversation scoping across browse/read/scroll/discover; patch context confirms lineage code already existed | not a #129 dedupe implementation |
| [#89553](https://github.com/NousResearch/hermes-agent/pull/89553) | **OPEN, unmerged** | current Desktop metadata/title pass composes through existing `add_lineage_result` seam | #128 metadata search, not lineage semantics |

Open PRs can move or merge. Refresh them before implementation. If a newly merged change is semantically equivalent to a #129 clause, delete that fork slice instead of duplicating it.

## 5. Real residual against #129

### Residual A — agent discovery uses generic ancestry as winner identity

Current `_resolve_to_parent()` is the clearest current-main residual.

Required change in behavior:

- a positive compression continuation may collapse into one logical root;
- a generic parent link without positive compression evidence must stop and remain distinct;
- branch/delegate/tool/reset boundaries must not collapse simply because they carry `parent_session_id`;
- stale/foreign markers must be interpreted according to the positive-edge rule, not as broad global vetoes.

The smallest likely seam is **candidate-lineage resolution in `tools/session_search_tool.py`**, not message FTS and not transcript hydration.

### Residual B — one query-local resolver state, exact B semantics

No upstream PR/source implementation matching the exact #129 contract was found for:

- one memo/path-compression map shared by candidate 1..N;
- caching both proven roots and proven semantic-unresolved paths;
- global `B=2000` successful **uncached** lineage-node row fetches;
- root proven on work unit 2000 succeeds;
- a required 2001st fetch is refused;
- memo-only completion after B remains allowed;
- B-exhausted partial path is not memoized as semantic unresolved;
- first bound-hit stops lower-ranked scanning;
- already-proven winners form a safe prefix and an explicit truncation signal is returned;
- early-K stops resolution once enough safe winners exist.

This is the largest true fork-specific residual and should be reconstructed, not inferred from open upstream traversal limits.

### Residual C — shared equivalence across dedupe and exclusions

Within a public search call, the same strict compression-root meaning must drive:

- winner dedupe;
- current-session/current-lineage exclusion;
- exact-title exclusion where the caller performs it.

Do not let one path use generic `_resolve_to_parent`, another use `get_compression_tip`, and a third use an endpoint-local root walker if those predicates disagree on branch/delegate/tool/reset cases.

Sharing the **equivalence predicate/resolver semantics** is required. Sharing every caller orchestration function is not.

### Residual D — Desktop only if current-main inspection proves a stricter gap

Desktop already has merged compression-aware branch-stop semantics. Before touching it, refresh the current `hermes_cli/web_routers/sessions.py` implementation and test its root helper against #129's full truth table.

Likely residuals, if still present:

- branch/delegate/tool marker completeness beyond the old end-reason+timing rule;
- missing-parent/cycle fail-closed behavior;
- query-global B/truncation semantics when composing ID/metadata/content passes;
- equivalence parity with the agent tool.

Do **not** replace the current multi-lane search pipeline merely to make the code shape match the agent tool.

## 6. Recommended implementation shape

### 6.1 Resolver contract

Implement/reuse one narrow query-scoped resolver abstraction whose observable contract is:

```text
resolve(raw_session_id) -> one of
  PROVEN_ROOT(root_id)
  PROVEN_UNRESOLVED(reason)
  BOUND_EXHAUSTED
```

The exact type/name is implementation detail. Required semantics:

1. fetch a node only when the memo cannot answer;
2. count only successful uncached lineage-node row fetches toward B;
3. detect cycles from the active traversal path;
4. follow only a positively proven compression continuation edge;
5. path-compress/cache only outcomes that are semantically proven;
6. never convert budget exhaustion into `UNRESOLVED`;
7. expose work/bound state to the caller so safe-prefix truncation is observable.

Do not introduce a persistent root column or whole-graph preload; both are ticket non-goals.

### 6.2 Agent discovery integration

In `tools/session_search_tool.py::_discover()`:

- preserve current `search_messages()` generation/ranking/source ordering;
- preserve the first raw owner/message anchor for each winning logical root;
- resolve distinct raw owning session IDs sequentially through the shared query-local memo;
- stop at K proven winners;
- on B hit, stop scanning and return the already-proven winner prefix plus truncation metadata;
- only then call `get_anchored_view()` for surviving winners;
- preserve #69544/#86652 visibility behavior, but express current/title exclusions through the same strict root semantics.

Avoid loading full transcripts merely to decide lineage.

### 6.3 Desktop integration

If current-main tests show a residual:

- keep current ID/metadata/content source priority;
- keep live-tip projection and existing result shape unless #129 contract requires an additive truncation signal;
- run candidates through the same compression-equivalence semantics before expensive result hydration;
- preserve best-ranked/source-priority anchor;
- stop once K safe unique winners exist;
- do not import open #89553/#81561 wholesale.

### 6.4 Listing/browse/resume

Keep `list_sessions_rich` / `get_compression_tip` ownership intact unless a narrowly shared strict edge predicate can replace duplicated logic without changing its public listability/projection contract.

#129 explicitly does not require one identical orchestration function across every caller.

## 7. First RED tests

### RED 1 — agent discovery must not collapse a generic branch

In `tests/tools/test_session_search.py`:

1. create parent P;
2. create child B with `parent_session_id=P` but no positive compression-continuation evidence (a real branch marker/end reason if current fixture helpers expose it);
3. put distinct ranked query hits in P and B;
4. call discovery with `limit >= 2`;
5. assert P and B remain two logical winners.

Expected on current upstream agent discovery: **RED** if `_resolve_to_parent()` collapses them to one generic root.

Sabotage: replacing the strict resolver with generic parent walking must make this test fail.

### RED 2 — positive compression duplicates collapse while anchor survives

Build `root -> compression continuation -> tip`, put several FTS hits across segments, and assert:

- exactly one logical winner;
- highest-ranked raw owning hit/message anchor is preserved;
- hydration uses that anchor after dedupe;
- no full transcript is loaded during lineage classification.

### RED 3 — current-session exclusion parity

A branch/delegate/reset related only by generic ancestry must not be excluded merely because the current session shares a generic root. A true compression continuation uses the same strict root semantics as winner dedupe.

### RED 4 — missing parent and cycles fail closed

Cover:

- missing parent row;
- direct cycle;
- long positive cycle;
- a non-compression parent link terminating traversal before unrelated ancestry.

A cycle must be classified from traversal-local evidence, not by hitting a semantic depth cap.

### RED 5 — exact B boundary

Construct controlled chains/candidate order for:

- `B=1999`: room remains;
- `B=2000`: a root whose final required fetch is work unit 2000 succeeds;
- `B=2001`: the first required 2001st uncached fetch yields bound exhaustion;
- memo hit after B: still completes;
- locally provable cycle after B with no new DB fetch: still classifiable;
- partial path stopped by B: not memoized as semantic unresolved.

### RED 6 — safe-prefix truncation and early K

For K=1/3/10 and a defensive larger internal limit:

- enough proven winners => stop without resolving lower-ranked candidates;
- B hit before K => return only proven prefix + explicit truncation;
- lower-ranked unproven rows never leak into the response.

### RED 7 — Desktop parity fixture, only if residual remains

Compose a fixture containing:

- two physical compression segments of conversation A;
- a branch B from A;
- a delegate/tool child;
- ID/content (and metadata if #128 has landed) hits across lanes.

Assert compression duplicates collapse while branch/delegate/tool semantics match the agent resolver and existing lane priority is unchanged.

## 8. Verification / sabotage matrix

Focused first:

```text
pytest tests/tools/test_session_search.py -q
pytest tests/hermes_cli/test_web_server_session_search.py -q
pytest tests/test_hermes_state.py -q
```

Then run the current adjacent Gateway/Desktop suites required by their present AGENTS.md instructions.

Required sabotage checks:

- replace strict positive-edge check with generic parent walk -> branch/delegate/reset regression turns RED;
- remove memo reuse -> fetch-count/B tests turn RED;
- count cache hits toward B -> boundary test turns RED;
- memoize B-exhausted partial path as unresolved -> later-candidate/memo test turns RED;
- continue scanning after bound hit -> safe-prefix test turns RED;
- hydrate before dedupe -> bounded-hydration/spy test turns RED;
- overwrite winning raw anchor with a later duplicate -> anchor regression turns RED;
- give Desktop and agent different edge predicates -> parity fixture turns RED.

## 9. Concurrency / read-snapshot note

#129 requires candidate selection plus lineage lookups to observe a coherent logical read where the current connection model requires it.

This external preflight did **not** find a merged upstream Session Search change that supplies #129's query-wide resolver snapshot/budget contract. Do not guess a transaction wrapper from historical fork code. During implementation, inspect the freshly refreshed `SessionDB` read-connection model and choose the narrowest supported read-snapshot mechanism that covers candidate fetch + lineage-node lookups without moving expensive hydration under a long writer lock.

This is an implementation verification item, not a reason to widen #129 into read-pool architecture work.

## 10. Scope walls

Do not absorb:

- #128 session metadata FTS/routing;
- message FTS redesign;
- title `#N` literal/exact-title safety repair;
- broad full-transcript lineage read/merge UX;
- Desktop lineage navigator UX from #81561;
- export stitching from #53992;
- persistent lineage-root columns;
- whole-graph preload;
- resolver strategy/B reselection;
- general branch/delegation provenance redesign.

## 11. Implementation verdict

**Disposition: IMPLEMENT RESIDUAL, reusing current upstream.**

What is already upstream and should stay owned there:

- positive compression-tip projection for list/resume (#12960);
- Desktop search composition + compression-aware dedupe foundation (#38393/#39062);
- compacted/compression-history visibility (#69544);
- reset recall exception (#86652);
- current candidate ordering and late anchored hydration in `session_search`.

What still needs #129 work:

1. replace agent discovery's generic-parent equivalence with the frozen positive compression-continuation semantics;
2. make dedupe/current/exact-title exclusion use that same meaning;
3. add the query-local memo/path-compression resolver with exact `B=2000`, early-K and safe-prefix truncation semantics;
4. tighten Desktop only for residual truth-table/B/parity gaps proven on refreshed current main;
5. add focused regression/sabotage coverage without replaying the old fork search architecture.

**First implementation move:** refresh upstream main, re-open current `tools/session_search_tool.py` plus `hermes_cli/web_routers/sessions.py`, then write RED 1 (generic branch must remain distinct) before changing production code.

## 12. Prior-art refresh list before coding

Recheck these immediately before the first implementation commit:

- upstream main SHA;
- [#81561](https://github.com/NousResearch/hermes-agent/pull/81561) — strict Desktop lineage traversal;
- [#83829](https://github.com/NousResearch/hermes-agent/pull/83829) — session_search Telegram scope overlap;
- [#89553](https://github.com/NousResearch/hermes-agent/pull/89553) — Desktop metadata composition overlap from #128 territory;
- [#53992](https://github.com/NousResearch/hermes-agent/pull/53992) — strict export chain helper prior art.

Classification rule:

- merged + semantically equivalent -> drop that fork slice;
- merged + partial -> shrink #129 to the residual;
- open/unmerged -> evidence only;
- closed-unmerged/superseded -> provenance only.
