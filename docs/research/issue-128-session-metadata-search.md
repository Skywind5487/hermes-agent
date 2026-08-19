# Issue #128 research — reconstruct Session Metadata Search on current upstream

Status: **READY FOR IMPLEMENTATION after a fresh upstream-base refresh**  
Research date: **2026-08-19**  
Control plane: fork issue [#134](https://github.com/Skywind5487/hermes-agent/issues/134)  
Intent: fork issue [#128](https://github.com/Skywind5487/hermes-agent/issues/128), under [#109](https://github.com/Skywind5487/hermes-agent/issues/109)

## Executive handoff

The next agent should **not** reconstruct #128 by replaying the old `dev` diff. The durable contract is still valid, but the implementation must be rebuilt on a freshly refreshed upstream generation.

Current pins at research time:

- upstream `NousResearch/hermes-agent:main`: [`63565fa26b00a2096247064785c4380aafab2303`](https://github.com/NousResearch/hermes-agent/commit/63565fa26b00a2096247064785c4380aafab2303)
- fork clean tracking `main`: `243352e7b8bddc9f33eba1b6506810f8dd88beaa`
- fork historical composition `dev`: `fa5ed679cc6559c619038f327e6276f4b7e8d735`
- Phase-1.5 archaeology artifact: PR [#108](https://github.com/Skywind5487/hermes-agent/pull/108), head `5aa4f4e27ccf2169beb4fc1f1d1eeb655d13b548`

The issue's earlier upstream pins (`395c70d…`, later `a6bada…`) are stale. Refresh again immediately before coding because the relevant upstream PRs are still moving.

**First RED test:** add a behavior-level regression in `tests/hermes_cli/test_web_server_session_search.py` proving that a session whose **stored title matches the query but whose message body does not** is returned by `GET /api/sessions/search`, and that the stored title survives in the result. This is the narrowest vertical proof of the missing shared metadata-discovery lane. Do **not** green it with an endpoint-local `LIKE`; the first production change should route the endpoint through the shared metadata-discovery abstraction that will also serve `list_sessions_rich(search_query=...)`.

Then add the second RED directly at the state/listing seam: `list_sessions_rich(search_query="finance")` must find a gateway session whose `display_name` is `Acme Guild / #finance` even when title/message content do not contain `finance`.

## 1. What upstream already owns

### 1.1 Merged authority — reuse, do not rebuild

Upstream PR [#57685](https://github.com/NousResearch/hermes-agent/pull/57685) is **merged** (`19d4174454624a1ca91bc47b8f2a7ae8c3b4b5d3`). It is the authoritative CLI/Gateway session-search seam:

- `/sessions search <query>` / `find`
- `hermes_cli/session_listing.py::query_session_listing()`
- `SessionDB.list_sessions_rich(search_query=...)`
- compression-chain-aware title/id matching
- punctuation-compacted title matching
- literal-safe `%` / `_` handling
- existing resume authorization remains untouched

It explicitly salvaged and superseded the much larger old PR [#57595](https://github.com/NousResearch/hermes-agent/pull/57595). Therefore #128 extends #57685; it does not resurrect #57595.

Message-content discovery is also already upstream-owned through `search_messages()`. #128 must leave that substrate as the content channel and add metadata discovery beside it.

### 1.2 Still missing on current upstream — open PR evidence only

These PRs are **not merged** as of the pin above, so their code is evidence/prior art only:

| PR | Current status | What it proves | #128 treatment |
|---|---|---|---|
| upstream [#89553](https://github.com/NousResearch/hermes-agent/pull/89553) | OPEN, unmerged | Desktop `/api/sessions/search` still misses stored-title-only sessions; result mapping discards stored title | absorb behavior, not branch |
| upstream [#71912](https://github.com/NousResearch/hermes-agent/pull/71912) | OPEN, unmerged | `list_sessions_rich(search_query=...)` still searches title/id but not `display_name`; maintainer sweeper marked premise present / salvageability high | absorb one-column behavior into shared metadata lane |
| upstream [#87636](https://github.com/NousResearch/hermes-agent/pull/87636) | OPEN, unmerged, currently `mergeable=false` | current replay of Desktop/web metadata search; propagates `title`/`matched_on`; forwards `source` / `sources` / `exclude_sources` through the metadata pass | newest Desktop prior art; recheck before coding |
| upstream [#67381](https://github.com/NousResearch/hermes-agent/pull/67381) | OPEN, unmerged, currently `mergeable=false` | title substring demand is real, but proposes folding it into `search_messages()` and removing the exact-title tool path | **do not copy ownership shape**; #128 keeps metadata and message channels separate |

Two overlapping Desktop title proposals are open at once (#89553 and #87636). Do not stack them mechanically. At implementation start, classify any newly merged result using #134's rule: merged+equivalent → drop overlap; merged+partial → shrink #128 to the residual.

### 1.3 Historical / superseded upstream prior art

- [#71225](https://github.com/NousResearch/hermes-agent/pull/71225): **closed-unmerged** and explicitly superseded by #87636 because its branch was ~5k commits stale/dirty. Use only for provenance.
- [#62399](https://github.com/NousResearch/hermes-agent/pull/62399): **closed-unmerged** historical title/display-name Desktop search implementation. Provenance only.
- [#57595](https://github.com/NousResearch/hermes-agent/pull/57595): old broad Gateway search proposal, salvaged/superseded by merged #57685. Provenance only.

## 2. Accepted fork behavior to preserve

The accepted contract is the behavior from fork issues [#12](https://github.com/Skywind5487/hermes-agent/issues/12), [#14](https://github.com/Skywind5487/hermes-agent/issues/14), [#16](https://github.com/Skywind5487/hermes-agent/issues/16), and the routing research [#37](https://github.com/Skywind5487/hermes-agent/issues/37), not the historical file layout.

The old implementation donors are useful because they contain reviewed tests and invariants:

| Accepted donor | Merged SHA | Durable behavior to salvage |
|---|---|---|
| fork PR [#59](https://github.com/Skywind5487/hermes-agent/pull/59) | `e94f2630a50d7585f78cfc06365753c033113cb9` | stable `sessions.row_id`; Unicode `sessions_fts` external-content over `(title,id,display_name)`; resumable H/P lifecycle; bounded migration gap supplement |
| fork PR [#65](https://github.com/Skywind5487/hermes-agent/pull/65) | `bdf2fc218264538c4f3238b58532488fe665ff9e` | optional CJK metadata index; separate worker-operability vs search-serving state; independent H/P/stale; safe fallback when tokenizer unavailable/pending/stale or lone-CJK-char query |
| fork PR [#73](https://github.com/Skywind5487/hermes-agent/pull/73) | `919f4469e832bc2b38bba0ea5af26b842bf91acd` | optional normalized trigram external-content index; compact title/display_name + raw id; independent H/P; capability-loss quarantine/recovery |
| fork PR [#89](https://github.com/Skywind5487/hermes-agent/pull/89) | `35c8564c9c0af3d75bcbdf1d793e7207e5528f06` | candidate-first Unicode/CJK/trigram routing; one bounded canonical LIKE fallback; candidate-seeded expensive projection; Desktop whole-store search behavior |

### Frozen behavioral contract

Searchable metadata dimensions are exactly:

1. `sessions.title`
2. public/logical session `id`
3. gateway `display_name`

Routing behavior:

- ordinary Unicode/tokenizable query → Unicode metadata FTS first
- CJK query that the optional CJK target can safely serve → CJK lane (with the accepted Unicode relationship from the donor contract)
- normalized/arbitrary infix query → trigram metadata lane when available
- known unsupported/unindexable query → bounded direct fallback
- for ordinary indexable queries, **FTS candidates first; LIKE only when the candidate route yields zero or is unavailable**, never “FTS + unbounded LIKE every time”
- one-character CJK remains a deliberate fallback case
- metadata candidate narrowing happens **before** expensive compression-lineage/projection work
- wildcard input remains literal-safe
- source/profile/visibility constraints must be applied consistently across every candidate channel

The accepted storage shape is derived search state, not a new canonical source of truth:

- canonical data remains in `sessions`
- `row_id` is stable FTS row identity; public `id` remains logical identity
- Unicode/CJK metadata documents use raw `(title,id,display_name)`
- trigram uses compact title + raw id + compact display_name through a derived source/view; do not add persistent normalized canonical columns
- each optional target has independent durable completeness/capability state; one index must never falsely certify another
- migration/rebuild gaps must fail toward **no false negatives**, not toward serving a known-partial index

## 3. Ownership boundaries — what #128 must not absorb

Per #128 + #134, this child is an independently meaningful **Session Search metadata-discovery line**, not the whole Session Search feature.

Do not absorb:

- #129 compression-aware lineage semantics beyond what is required to preserve the existing caller contract
- #130 literal-safe exact title binding / exact-title resolution behavior
- `tools/session_search_tool.py`'s exact-title numbered resolution contract; fuzzy metadata discovery is a separate channel
- message-content FTS behavior owned by upstream `search_messages()`
- resume authorization/security widening
- a new ranking product beyond the minimum deterministic metadata priority required by the caller
- old fork-only migration states that cannot exist on the refreshed upstream base

This is also why upstream #67381 is not the implementation template: it intentionally mixes partial title discovery into the message-content function and removes the exact-title tool-layer path, which crosses #128/#130's ownership wall.

## 4. Current implementation map

Start code archaeology at these **symbols**, not by replaying historical hunks:

### State/search layer

- `hermes_state.py`
  - `SessionDB.list_sessions_rich(...)`
  - existing compression-chain/search-query SQL
  - session canonical schema callers and any current metadata helper seam
- `hermes_state_search.py`
  - message FTS/search/rebuild primitives that can be generalized/reused without coupling metadata to message content
  - current optional CJK/trigram capability patterns
- `hermes_state_common.py`
  - canonical DDL/constants/normalization policy
- `hermes_state_schema.py`
  - startup schema ensure/migration wiring

Historical donor tests show these are the likely stable responsibility boundaries, but **verify current symbols after refreshing upstream**; do not assume old line numbers.

### CLI / Gateway

- `hermes_cli/session_listing.py::query_session_listing()`
- `SessionDB.list_sessions_rich(search_query=...)`
- Gateway `/sessions search` caller introduced by merged #57685

Desired change: swap the current title/id-only candidate source for the shared metadata-discovery abstraction while preserving source scope, visibility, ordering, limit semantics, and resume authorization.

### Desktop / REST

- `hermes_cli/web_routers/sessions.py` — `GET /api/sessions/search`
- `tests/hermes_cli/test_web_server_session_search.py`
- Desktop sidebar/search result mapping under `apps/desktop/src/app/chat/sidebar/`
- Desktop Hermes API/types under `apps/desktop/src/hermes.ts` / `src/types/hermes.ts` (verify current split)

Desired change: merge metadata candidates with the existing ID + message-content lanes, dedupe by the current lineage/root contract, and preserve stored `title` plus safe origin metadata instead of synthesizing a `title: null` row.

### Historical donor test files worth porting selectively

- `tests/test_session_metadata_fts.py` — Unicode/lifecycle invariants (#59)
- CJK coverage from PR #65
- `tests/test_session_metadata_trigram_fts.py` — trigram/lifecycle invariants (#73)
- `tests/test_session_metadata_picker_routing.py` — routing/fallback/candidate-first contract (#89)
- `tests/hermes_cli/test_web_server_session_search.py` — REST composition
- `tests/hermes_cli/test_session_listing.py` — CLI/Gateway search-query behavior
- Desktop picker/sidebar search tests from #89 / current upstream prior art

Port only tests whose behavior remains part of #128. Do not bring forward tests for deleted historical topology.

## 5. First RED and implementation order

### RED 1 — public Desktop/API metadata-only title discovery

In `tests/hermes_cli/test_web_server_session_search.py`:

1. create a session with a distinctive stored title, e.g. `Arby's Faribault, MN`
2. append messages that do **not** contain `Arby's`, `Faribault`, or the title text
3. query `GET /api/sessions/search?q=Faribault`
4. assert the session is present
5. assert the returned result carries the stored title

Expected on current upstream: RED, matching the live reproduction recorded by #89553.

**Green constraint:** no endpoint-local full-table LIKE scan. Wire a shared metadata-discovery state abstraction and let the endpoint consume it beside ID/content results.

### RED 2 — display-name through the existing listing seam

Create a real SessionDB session:

- title: `Quarterly Budget Review`
- message: unrelated text
- gateway `display_name`: `Acme Guild / #finance`

Assert `list_sessions_rich(search_query="finance", order_by_last_active=True)` returns the session. Also pin:

- compact query, e.g. `an94` vs `#an-94-ops`
- literal `%` / `_`
- compression-chain behavior already owned by the seam

This is the focused prior art from #71912.

### Slice 3 — shared metadata substrate

Port/reconstruct the minimum accepted Unicode external-content substrate on current schema architecture:

- stable row identity
- raw three-field document
- resumable H/P lifecycle
- live narrow maintenance
- migration-gap no-hide behavior

Do not restore obsolete fork-only migration states.

### Slice 4 — optional CJK + trigram capabilities

Add the accepted optional targets using the current generalized rebuild/schema machinery:

- independent target state
- pending/stale/unavailable never served as complete
- capability loss degrades canonical writes/search safely
- normalized trigram source is derived, raw id preserved

### Slice 5 — candidate router + bounded fallback

Port the #14/#37/#89 decision policy, not its old function graph:

- classify query once
- choose candidate lanes explicitly
- candidate-first
- canonical bounded fallback only when required
- apply candidate set before lineage/projection
- one source/profile/visibility contract across routes

### Slice 6 — caller integration + result propagation

- CLI/Gateway: preserve #57685 behavior and authorization
- Desktop REST: metadata + ID + message composition; dedupe consistently
- result: carry stored title and safe origin metadata
- Desktop UI: render those fields; keep message snippets for body matches
- tool: preserve exact-title resolution as a separate path

## 6. Verification matrix

Run focused tests first, then the adjacent suites:

```text
pytest tests/hermes_cli/test_web_server_session_search.py -q
pytest tests/hermes_cli/test_session_listing.py -q
pytest tests/test_session_metadata_fts.py -q
pytest tests/test_session_metadata_trigram_fts.py -q
pytest tests/test_session_metadata_picker_routing.py -q
pytest tests/test_hermes_state.py -q
```

Add current CJK session-metadata tests under whatever filename survives the reconstruction.

For Desktop, run the focused sidebar/search tests, then the Desktop typecheck/lint/test command current `apps/desktop/AGENTS.md` requires.

Required sabotage checks before declaring the line done:

- disable each candidate lane and prove its owned query class turns RED or safely falls back as specified
- force CJK/trigram capability unavailable and prove canonical session writes still succeed
- leave a rebuild pending and prove the incomplete index is not treated as authoritative
- reintroduce unconditional LIKE alongside an indexable successful FTS candidate and prove a routing test catches it
- drop `display_name` from metadata candidates and prove the gateway-name regression catches it
- drop title propagation from REST result mapping and prove the Desktop/API regression catches it

## 7. Merge / upstream drift risks

### Highest risk: moving open upstream search PRs

Recheck **#89553, #71912, and #87636 immediately before first code change**. They overlap exactly the caller surfaces #128 intends to modify. Use #134's classification, not chronology:

- merged + equivalent/present → delete that fork slice
- merged + partial → implement only residual
- open/unmerged → evidence only
- closed/unmerged/superseded → provenance only

### Medium risk: broad current-state refactors

The historical donors were built around an older `hermes_state.py` topology. Prefer current shared schema/rebuild helpers and keep #128 tiny. A current helper that already expresses an invariant outranks donor structure.

### Medium risk: duplicate semantics between metadata and content search

Do not solve title discovery by contaminating `search_messages()`. The two channels have different data, matching rules, result provenance, optional tokenizers, and caller semantics. Merge **results**, not ownership.

### Low/controlled risk: optional tokenizer absence

This is an accepted degradation state, not an exceptional install failure. Canonical writes and non-specialized search must keep working.

## 8. Prior-art classification snapshot

| Source | Classification at 2026-08-19 | Action |
|---|---|---|
| upstream #57685 | MERGED / current authority | extend |
| upstream #89553 | OPEN / unmerged | evidence; recheck |
| upstream #71912 | OPEN / unmerged | evidence; recheck |
| upstream #87636 | OPEN / unmerged, supersedes #71225 | newest Desktop evidence; recheck |
| upstream #67381 | OPEN / unmerged; ownership conflicts with #128 boundary | demand evidence only; do not copy shape |
| upstream #71225 | CLOSED / unmerged / superseded by #87636 | provenance only |
| upstream #62399 | CLOSED / unmerged | provenance only |
| upstream #57595 | superseded/salvaged by merged #57685 | provenance only |
| fork #59 | MERGED accepted donor | salvage Unicode contract/tests |
| fork #65 | MERGED accepted donor | salvage CJK contract/tests |
| fork #73 | MERGED accepted donor | salvage trigram contract/tests |
| fork #89 | MERGED accepted donor | salvage routing/caller contract/tests |
| fork #108 | OPEN draft archaeology artifact | semantic map only; #134 owns topology |

## Done condition

The research handoff is complete when the implementation agent can start RED 1 without rediscovering:

- what #128 owns vs #129/#130/content search
- current upstream/fork pins
- which upstream work is actually merged vs merely open prior art
- which historical work was superseded
- which accepted fork behaviors/tests are authoritative donors
- which files/symbols are the current starting seams
- the first two failing behaviors to pin
- the ordered implementation slices
- the merge/drift risks and exact PRs that must be refreshed before coding

That condition is satisfied by this document. The only mandatory pre-code discovery left is a **fresh status/SHA refresh of moving upstream `main` plus #89553/#71912/#87636**, followed by reading the current checked-out blobs at the named symbols.