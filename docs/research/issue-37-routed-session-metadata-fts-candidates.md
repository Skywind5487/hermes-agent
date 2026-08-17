# #37 research: routed session-metadata FTS candidates

Status: **research complete; #14 is implementation-ready**

Target implementation: **#14**

Parent map: **#13**

Accepted storage/search substrate: **#12**

Code-audit base: **`196b904417371cf233f3eb9b560c80a079ab8d72`** (`dev@196b90441`)

Research-only branch: `research/37-routed-session-metadata-fts`

## Executive conclusion

#14 should extend the existing authoritative
`SessionDB.list_sessions_rich(search_query=...)` seam. No sibling metadata
search subsystem is justified.

The accepted #12 substrate already exposes all three low-level candidate lanes
that #14 needs:

- `_fts_metadata_candidates()` for raw Unicode/token candidates;
- `_fts_cjk_metadata_candidates()` for servable CJK-bigram candidates;
- `_fts_session_trigram_candidates()` for compact title/display-name and raw-ID
  arbitrary infix candidates.

The missing work is routing and integration. At the pinned base,
`list_sessions_rich` still builds a recursive compression chain from the whole
eligible root set and then applies leading-wildcard `LOWER(... ) LIKE '%...%'`
predicates to title, ID, and display name. It therefore pays broad metadata
scan and lineage work before candidate narrowing. It also has two observable
drifts from the final #16 contract:

1. its compact query uses broad Python `re.sub(r"[\W_]+", "", ...)` while the
   accepted canonical policy removes exactly `-`, `_`, `.`, and ASCII space;
2. the LIKE predicate compacts `title` but not `display_name`.

There is also a search-specific pin leak: the pinned back-fill reuses only the
base eligibility predicates, not the active search predicate, so
`include_pinned=True` can append a non-matching pinned conversation.

The implementation shape is:

```text
bounded raw query
  -> deterministic route plan
  -> whole-store metadata row_id candidates (FTS, or LIKE only on direct/zero fallback)
  -> reverse compression closure from matching historical rows
  -> existing root/branch/delegate/source/user/archive eligibility
  -> DISTINCT visible logical roots
  -> forward chain/activity only for survivors
  -> existing order + LIMIT/OFFSET + preview hydration
  -> existing tip projection/result shape
```

Candidate IDs must not receive an unsafe global pre-eligibility `LIMIT`.
Visibility, source/session-key filters, and multiple matching segments in one
compression lineage can otherwise consume the candidate window and starve a
valid top-N result. The safe bound remains after root mapping, eligibility, and
dedupe. The rare LIKE fallback is bounded to the three metadata fields, capped
input, escaped literal patterns, and the same final page bound; it must not
hydrate messages/previews while scanning candidates.

No blocker remains. Ranking/match provenance remains #28; additional
query-local lineage optimization remains outside #14; the post-#14 read-path
sweep remains #39/#18.

---

## Authority and fixed point

All code links and line numbers below are pinned to:

```text
BASE_SHA=196b904417371cf233f3eb9b560c80a079ab8d72
```

This is the latest `dev` at research completion. Its only delta from the
previous audit point `bee13fc085079d78d0334722ad4c37a1144e3102` is the
research document for #40; application code is unchanged. Final #12 acceptance
commit `4e5ad5c2230300d1ffae84b089ffc70e368c8a23` is an ancestor.

Authority order used here:

1. [#14's implementation contract](https://github.com/Skywind5487/hermes-agent/issues/14),
   [final #16 contract](https://github.com/Skywind5487/hermes-agent/issues/16#issuecomment-5225691010),
   and accepted #12 substrate;
2. merged upstream seams proven present in the pinned base;
3. pinned fork code;
4. open/unmerged upstream work as design evidence only.

If `/implement #14` starts after `dev` moves, compare the listed seams against
the new head. A docs-only or unrelated change needs only a base refresh; a
material change to `list_sessions_rich`, the three candidate helpers, or the
REST/profile consumers requires updating the affected part of this map.

---

## Direct answers to #37's mandatory questions

### 1. Can #14 extend `list_sessions_rich(search_query=...)`?

**Yes.** It is already the shared database seam for CLI/gateway discovery and
the ID-search helper, and it already owns root visibility, compression-chain
matching, last-active ordering, pagination, preview shaping, and tip
projection. Moving those semantics to another API would create divergence.

REST and profile endpoints should delegate metadata discovery to this same
seam. Adding an optional `q` parameter to an existing list endpoint is an
adapter change, not a second search engine.

### 2. What exact classifier chooses Unicode, CJK, trigram, or direct fallback?

Classification operates on the raw query after truncating to
`MAX_FTS5_QUERY_CHARS` and trimming outer whitespace. CJK classification runs
before token/literal classification.

| Query shape | Required route | On unavailable/failure | On valid zero | Why |
|---|---|---|---|---|
| Empty after bounding/trim | no search / empty result as caller requires | n/a | n/a | Never manufacture a full-store query |
| Any maximal CJK run is exactly 1 character | direct canonical LIKE fallback | n/a | return fallback result | Bigram cannot preserve arbitrary single-character substring recall |
| Contains CJK and every CJK run is 2+ | union CJK (`sessions_fts_cjk`) + Unicode (`sessions_fts`), dedupe by `row_id` | direct LIKE if the required CJK lane or the route group cannot serve coherently | run LIKE exactly once | Final #16 requires the CJK-bigram + Unicode path; mixed-script/token recall must not depend on one partial lane |
| Non-CJK with explicit token syntax: balanced quoted phrase, standalone `AND`/`OR`/`NOT`, or a legal trailing prefix `*` | Unicode (`sessions_fts`) | direct LIKE | run LIKE exactly once | Preserves the existing FTS5 token-query affordance |
| Plain non-CJK literal where both raw-ID and compact-title/display needles are at least 3 characters | normalized trigram (`sessions_fts_trigram`) | direct LIKE | run LIKE exactly once | A bare picker string does not reveal whether it is an interior fragment; trigram is the only routed lane that preserves #16 arbitrary infix recall without a parallel scan |
| Plain non-CJK literal where either required field representation is shorter than 3 | direct canonical LIKE fallback | n/a | return fallback result | A successful partial trigram lane could otherwise hide matches in the unindexable field representation |
| Sanitizer produces no usable routed expression | direct canonical LIKE fallback, unless raw input is empty | n/a | return fallback result | Unsupported syntax must degrade, not become a false negative |

The plain-literal decision is deliberate. Routing every bare Latin word through
Unicode first is not contract-safe: a token hit could suppress fallback while
other rows match the same input only as an interior title/display-name/ID
fragment. Explicit FTS syntax supplies token intent; ordinary picker text is a
literal substring query.

For CJK, the router normalizes the existing helper outcomes into three states:

- **unavailable/unservable**;
- **servable zero**;
- **servable hits**.

The CJK and Unicode result sets form one route group. Only a successful,
non-empty union suppresses LIKE. A route exception/failure never trusts a
partial union.

### 3. Where does zero-result fallback happen?

Exactly once, inside the internal metadata route orchestrator invoked by
`list_sessions_rich`, before lineage/projection hydration.

The orchestrator should return a structured outcome such as
`(path, status, row_ids)` rather than making callers infer failure from an empty
list. Existing helper results already carry the needed distinctions:

- Unicode/trigram: `(fts_ok, candidates)`;
- CJK: `(servable, candidates_or_none)`.

Rules:

1. known unsupported/unindexable -> LIKE directly;
2. routed failure/unavailable -> LIKE directly;
3. routed success with zero deduped row IDs -> LIKE once;
4. routed success with one or more IDs -> never run LIKE.

REST, CLI, gateway, Desktop, and web adapters must not add their own second
fallback after the common seam returns.

### 4. How are candidates over-fetched/deduped without starvation?

- Candidate identity is `sessions.row_id`, never the public text ID.
- Unioned lanes dedupe by `row_id` before lineage work.
- Candidate-to-lineage mapping uses a reverse recursive closure across only
  valid compression edges. `DISTINCT` visible root IDs dedupe multiple
  historical segment hits.
- Existing source/session-key/archive/child/delegate/tool filters apply to the
  surfaced root before final `LIMIT/OFFSET`.
- The forward activity chain, preview subquery, and tip-row hydration run only
  for surviving roots.
- Do not cap the global FTS/LIKE candidate set before those operations. A hard
  cap is only safe if implemented as iterative paging until the requested
  number of eligible distinct roots is satisfied or the lane is exhausted.
- Gateway's additional asynchronous `_resume_row_visible` policy remains after
  SQL. Preserve its current deliberate over-fetch (search asks for 50, then
  displays 10); do not reduce it in #14.

Use one JSON array parameter and `json_each(?)` to expose row IDs to the SQL CTE
rather than one bind per candidate. The repository already depends on JSON1,
and this avoids SQLite's bind-variable ceiling for broad matches. A temp table
would be a write and is inappropriate on `_read_ctx()`'s read-only connection.

### 5. What stays deferred to #28?

#14 preserves current deterministic recency ordering and existing exact-ID
fast-path priority in adapters. It does **not** add:

- BM25/relevance ordering;
- title-vs-ID-vs-display-name field weights;
- fuzzy edit distance;
- `matched_on` / match provenance in the public result shape;
- rank-before-limit policy.

Those are [#28](https://github.com/Skywind5487/hermes-agent/issues/28).
Candidate completeness and lineage dedupe are correctness, not ranking.

---

## Pinned source map

### Read seam and index substrate

| File / symbol | Pinned lines | What it owns for #14 |
|---|---:|---|
| `hermes_state.py::_read_ctx` | [L2673-L2687](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L2673-L2687) | WAL-safe independent reader with locked-writer fallback. Every new #14 read must use this seam. |
| `hermes_state_common.py::SESSIONS_FTS_SQL` | [L672-L717](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state_common.py#L672-L717) | Raw `unicode61` external-content `(title,id,display_name)` keyed by `sessions.row_id`. |
| `SESSION_METADATA_COMPACT_SEPARATORS` / `compact_session_metadata_text` | [L719-L778](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state_common.py#L719-L778) | Canonical compact policy: exactly `- _ .` and ASCII space. |
| `SESSIONS_FTS_TRIGRAM_SQL` | [L781-L850](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state_common.py#L781-L850) | Derived VIEW: compact title/display, raw ID; modern `trigram`. |
| `SESSIONS_FTS_CJK_*` | [L853-L927](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state_common.py#L853-L927) | Raw three-field `cjk_unicode61` table, triggers, stale guard. |
| `FTS_INDEXES` | [L930-L1050](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state_common.py#L930-L1050) | Authoritative six-index lifecycle registry; explicitly not the routing owner. |
| sanitizer / CJK / trigram eligibility helpers | [hermes_state_search.py L1870-L2011](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state_search.py#L1870-L2011) | Bounded FTS syntax sanitizer and current Unicode/CJK shape primitives. Reuse; add only the metadata-specific literal/token plan. |

### Candidate APIs already delivered by #12 children

| Symbol | Pinned lines | Contract to preserve |
|---|---:|---|
| `_session_fts_rebuild_gap` | [L6956-L6981](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L6956-L6981) | Unicode H/P gap discovery. |
| `_fts_metadata_candidates` | [L6983-L7082](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L6983-L7082) | Raw Unicode three-field candidates, bounded gap supplement, `row_id` dedupe, explicit success/failure. |
| `_session_trigram_rebuild_gap` / `_trigram_match_needle` | [L7084-L7132](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7084-L7132) | Trigram H/P snapshot and safe quoted field needles. |
| `_fts_session_trigram_candidates` | [L7134-L7280](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7134-L7280) | Compact title/display + raw ID, one snapshot, stale/capability gate, gap supplement, failure vs zero. |
| `_fts_cjk_metadata_candidates` | [L7282-L7365](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7282-L7365) | Raw CJK three-field candidates; unservable vs valid zero; one guard+MATCH snapshot. |
| `_cjk_lane_durable_guarded` | [L7367-L7385](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7367-L7385) | Pending/stale serving veto on the same connection snapshot. |

The three helpers currently open their own `_read_ctx()` scopes (Unicode's gap
probe also opens separately). #14 should add an optional caller-supplied read
connection or split out `..._on_conn` internals so route selection, lifecycle
guards/gaps, MATCH, candidate-to-root mapping, and the bounded result query can
share one explicit read transaction. This is new-path correctness, not the
whole-system #18 sweep.

### Current authoritative list/search seam

| Section | Pinned lines | Finding |
|---|---:|---|
| `list_sessions_rich` signature/docs | [L7544-L7615](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7544-L7615) | Already owns `search_query`, source filters, children, archives, projection, paging, compact rows, pins, and `session_key`. |
| Base root eligibility | [L7617-L7667](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7617-L7667) | Root/branch policy, delegate exclusion, source/session-key/cwd/message/archive filters. Preserve verbatim as the eligibility authority. |
| Current ID/search LIKE construction | [L7677-L7753](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7677-L7753) | Leading-wildcard raw predicates; broad Python compact drift; compact title only. Replace only `search_query` broad acquisition. |
| Recursive chain, preview, order, LIMIT | [L7755-L7816](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7755-L7816) | Currently seeds the whole eligible store before search. Refactor to seed from eligible candidate roots; keep edge predicate/order/result columns. |
| Pinned back-fill | [L7825-L7863](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7825-L7863) | Reuses base filters but loses search predicate. Constrain to matching candidate roots when search is active. |
| Compression tip projection | [L7865-L7914](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7865-L7914) | Per-root tip walk, batched tip-row hydration, visible result shape. Preserve; feed fewer roots. |
| `get_compression_tip` | [L7473-L7525](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L7473-L7525) | Existing forward continuation edge rules and bounded walk. Do not redesign/memoize here. |

### Exact binding paths that remain separate

| Symbol / consumer | Pinned lines | #14 treatment |
|---|---:|---|
| `resolve_session_id` | [hermes_state.py L6599-L6625](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L6599-L6625) | Keep exact lookup plus unique escaped prefix semantics. |
| `get_session_by_title` / `resolve_session_by_title` | [hermes_state.py L6903-L6954](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state.py#L6903-L6954) | Keep equality/numbered continuation ambiguity rules; discovery does not redefine `/resume`. |
| Gateway `/resume` | [gateway/slash_commands.py L4338-L4456](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/gateway/slash_commands.py#L4338-L4456) | Direct ID, exact/title resolution, continuation resolution, and authorization remain separate. |
| `search_sessions_by_id` | [hermes_state_search.py L3482-L3533](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_state_search.py#L3482-L3533) | Current REST helper is broad ID LIKE plus Python tiering. Do not use it as a parallel full metadata lane after routed search; retain only genuinely cheaper exact behavior where needed. |

---

## Consumer call graph and required adapter work

### CLI and gateway

`hermes_cli/session_listing.py` is the shared adapter:

- parser: [L8-L42](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_cli/session_listing.py#L8-L42);
- `query_session_listing`: [L45-L88](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_cli/session_listing.py#L45-L88).

It already over-fetches 4x, passes `search_query`, requests last-active order,
then excludes the current/unnamed session before final truncation. It needs no
second search implementation.

Gateway `/sessions search` calls that helper at
[gateway/slash_commands.py L4506-L4575](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/gateway/slash_commands.py#L4506-L4575).
It pushes `session_key`/source/tool filtering into the database, asks for 50
rows during search, applies `_resume_row_visible`, then displays 10. Preserve
that post-SQL authorization defense and over-fetch.

### REST, web, and Desktop global search

`GET /api/sessions/search` at
[hermes_cli/web_routers/sessions.py L166-L389](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_cli/web_routers/sessions.py#L166-L389)
currently runs broad ID discovery first, message FTS second, then performs a
second Python compression-root/tip walk and row hydration.

#14 should make the existing endpoint consume the common metadata seam:

1. retain a truly exact ID B-tree hit first (existing first-hit priority, not a
   new ranking system);
2. add `list_sessions_rich(search_query=q, ...)` metadata rows;
3. append existing message-content FTS hits;
4. reuse the endpoint's first-win lineage dedupe/result shaping;
5. remove/avoid the parallel broad `search_sessions_by_id` scan for the same
   arbitrary-infix ID contract.

The web Sessions page already debounces and calls this endpoint at
[web/src/pages/SessionsPage.tsx L1249-L1274](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/web/src/pages/SessionsPage.tsx#L1249-L1274),
through [web/src/lib/api.ts L820-L832](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/web/src/lib/api.ts#L820-L832).
Desktop's global/sidebar search calls the same API through
[apps/desktop/src/hermes.ts L611-L615](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/apps/desktop/src/hermes.ts#L611-L615).
These clients need no independent metadata matcher.

Do not port upstream `matched_on` or fuzzy ranking in #14.

### Desktop resume picker and cross-profile listing

The Desktop resume picker is the remaining whole-store product gap:

- [session-picker.tsx L27-L96](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/apps/desktop/src/components/session-picker.tsx#L27-L96)
  loads only the latest 200 sessions and lets `cmdk` filter that loaded page;
- [listAllProfileSessions L404-L430](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/apps/desktop/src/hermes.ts#L404-L430)
  has no query parameter;
- [the profile endpoint L73-L198](https://github.com/Skywind5487/hermes-agent/blob/196b904417371cf233f3eb9b560c80a079ab8d72/hermes_cli/web_routers/profiles.py#L73-L198)
  pages each profile before merging and exposes no search query.

Add optional `q` to the existing `/api/profiles/sessions` adapter, pass it to
each profile DB's `list_sessions_rich(search_query=q,
order_by_last_active=True)`, and merge already-filtered results. On non-empty
input, the picker debounces server queries and displays server results; the
loaded recent 200 remain only the empty-query view. Keep profile routing and
error isolation intact. A non-matching pin must not be back-filled into a
search response.

Search totals should not trigger a second full `COUNT(*)` per profile. Keep
`total` / `profile_totals` as the endpoint's existing unfiltered corpus counts
for response compatibility; the picker ignores those fields while `q` is
active. Do not add an expensive exact match count just for the dialog.

### TUI

`tui_gateway/methods_session.py::session.list` currently exposes a recent list,
not a server-search parameter, and the active session switcher loads a bounded
list. No #14 change is required unless that surface gains type-to-search in the
same PR; if it does, it must call the same database seam rather than filter only
the loaded page.

---

## Proposed database/CTE shape

### Internal route API

Keep routing internal to `SessionDB`, for example:

```python
MetadataCandidateResult(
    path: Literal["unicode", "cjk+unicode", "trigram", "like"],
    status: Literal["hits", "zero", "unavailable"],
    row_ids: list[int],
)
```

Names are illustrative; the semantic states are required. The router accepts a
caller-supplied read connection and does all route-group work in one read
snapshot.

### Candidate-to-visible-root mapping

The current forward chain edge is authoritative:

- parent `end_reason = 'compression'`;
- child has no `_branched_from` marker;
- child has no `_delegate_from` marker;
- child source is not `tool`;
- no timestamp predicate (the code documents real insert/end races).

Use the same edge in reverse from every candidate row. The reverse CTE should
emit the candidate itself plus every valid compression ancestor, not only the
terminal root. That preserves both modes:

- default `include_children=False`: existing outer eligibility admits the
  logical root/branch and hides continuation/delegate rows;
- `include_children=True`: candidate segments and valid ancestors remain
  individually eligible exactly as the current forward-seeded semantics allow.

Conceptual SQL:

```sql
WITH RECURSIVE
candidate_row_ids(row_id) AS (
  SELECT CAST(value AS INTEGER) FROM json_each(?)
),
candidate_sessions(id) AS (
  SELECT DISTINCT s.id
  FROM sessions s JOIN candidate_row_ids c ON c.row_id = s.row_id
),
reverse_chain(surface_id, cur_id) AS (
  SELECT id, id FROM candidate_sessions
  UNION
  SELECT p.id, rc.cur_id
  FROM reverse_chain rc
  JOIN sessions child ON child.id = rc.surface_id
  JOIN sessions p ON p.id = child.parent_session_id
  WHERE p.end_reason = 'compression'
    AND json_extract(COALESCE(child.model_config, '{}'), '$._branched_from') IS NULL
    AND json_extract(COALESCE(child.model_config, '{}'), '$._delegate_from') IS NULL
    AND COALESCE(child.source, '') != 'tool'
),
eligible_roots(id) AS (
  SELECT DISTINCT s.id
  FROM sessions s
  JOIN reverse_chain rc ON rc.surface_id = s.id
  WHERE <existing list eligibility predicates>
),
chain(root_id, cur_id) AS (
  SELECT id, id FROM eligible_roots
  UNION ALL
  SELECT c.root_id, child.id
  FROM chain c
  JOIN sessions parent ON parent.id = c.cur_id
  JOIN sessions child ON child.parent_session_id = c.cur_id
  WHERE <same existing forward compression edge>
),
chain_max AS (...)
SELECT ...
FROM sessions s
JOIN eligible_roots er ON er.id = s.id
LEFT JOIN chain_max cm ON cm.root_id = s.id
ORDER BY <existing effective-last-active order>
LIMIT ? OFFSET ?;
```

The exact column alias in `reverse_chain` may change during implementation;
tests must prove the semantics, especially branch/delegate boundaries and
historical-row matches.

### Snapshot rule

For non-empty `search_query`, use one explicit read transaction for:

1. route capability/stale/H-P reads;
2. MATCH and bounded H-P supplements;
3. optional zero/direct LIKE fallback;
4. candidate/root/forward-chain SQL and page hydration.

Commit/rollback before the existing per-result tip projection if refactoring
that projection into the same read transaction would broaden #14. New reads
must use `_read_ctx()` immediately. Existing writer-bound reads in
`get_compression_tip` and `_get_session_rich_rows_batch` are left for the
post-#14 [#39](https://github.com/Skywind5487/hermes-agent/issues/39) /
[#18](https://github.com/Skywind5487/hermes-agent/issues/18) audit.

---

## Canonical LIKE fallback

The fallback must be one helper/predicate shared by direct, route-failure, and
route-zero paths. It operates on the capped raw needle and the canonical
compact needle.

Literal escaping order:

```python
escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
pattern = f"%{escaped}%"
```

Every predicate uses `ESCAPE '\\'`. Search exactly:

- raw case-insensitive substring of `title`;
- raw case-insensitive substring of `id` (ID punctuation remains intact);
- raw case-insensitive substring of `display_name`;
- canonical compact substring of `title`;
- canonical compact substring of `display_name`.

Build both compact expressions from the same
`SESSION_METADATA_COMPACT_SEPARATORS` policy; do not repeat the broad
`re.sub(r"[\W_]+", ...)` behavior and do not compact IDs. Include compact
`display_name`, which the current fallback lacks.

The fallback is candidate-only: select stable `row_id`s, not message previews,
prompts, or tip rows. Push filters that are semantically local without
duplicating lineage policy; perform remaining root eligibility in the common
CTE before final pagination. Do not add a global pre-root candidate `LIMIT`.

Wildcard regressions must prove `%`, `_`, and `\` are literal. A query
containing only `%` or `_` must not become a match-all scan.

---

## Pinned current behavior and known pitfalls

1. **Search only runs in last-active mode today.** `search_query` is ignored
   outside `order_by_last_active=True`. Preserve that compatibility or make the
   invariant explicit with a test; all search adapters should request recent
   order.
2. **Flush happens before reads.** `list_sessions_rich` calls
   `flush_token_counts()` before building the read query. Do not hold a read
   snapshot while flushing the writer.
3. **Historical metadata can match.** A root, middle segment, or tip may supply
   the candidate; normal listing projection still returns one visible tip.
4. **Branch/delegate are not generic parent edges.** Never climb every
   `parent_session_id`; reuse the compression predicates exactly.
5. **Root eligibility is authoritative.** Do not make a hidden sibling visible
   because its `display_name` matched. Gateway post-filtering remains defense in
   depth.
6. **Pins must remain search-constrained.** `include_pinned` means matching pins
   survive pagination, not that unrelated pins appear in search results.
7. **Do not bind one SQL placeholder per hit.** Broad results can exceed 999;
   use `json_each(?)` or an equivalent one-parameter read-only candidate source.
8. **Do not trust partial route unions.** If required CJK+Unicode routing fails
   partway, discard the partial set and use fallback.
9. **Do not catch semantic/programming errors as “zero.”** Preserve explicit
   failure telemetry; zero and unavailable are different states.
10. **Do not duplicate compact policy.** Import the canonical helper/constants
    or generate both Python and SQL forms from one definition.
11. **No FTS candidate ranking in #14.** Existing helpers' `started_at DESC`
    ordering is not a relevance contract; candidate membership is what #14
    consumes.
12. **No production DB benchmarks.** Use disposable copies/generated corpora.

---

## RED test plan

Create a focused behavioral suite such as
`tests/test_session_metadata_picker_routing.py`. Tests must call public/session
behavior, not read production source text.

### Classifier and fallback

1. explicit quoted/boolean/prefix token query -> Unicode only;
2. plain eligible literal/infix -> trigram only;
3. CJK 2+ -> CJK + Unicode union;
4. any lone CJK run -> direct LIKE, no FTS call;
5. short non-CJK representation -> direct LIKE;
6. unavailable/stale/corrupt required lane -> direct LIKE;
7. successful non-empty FTS route -> zero LIKE calls;
8. successful empty route -> exactly one LIKE call;
9. route failure after a partial CJK union -> discard partial results and call
   LIKE exactly once;
10. `%`, `_`, and `\` remain literal in fallback, including match-all negative
    controls.

### #16 field semantics

11. all three fields match through Unicode;
12. all three fields match through CJK;
13. compact `AN-94` title and `#an-94-ops` display name match `an94` through
    trigram;
14. true interior fragment (`stige Bar` -> `Prestige Barrel`) matches;
15. raw punctuated ID infix matches without compacting ID;
16. fallback uses the same exact compact separator set for title and display
    name; punctuation outside `- _ . space` is not silently deleted;
17. mixed CJK/Latin route union dedupes the same `row_id`.

### Whole-store, lineage, eligibility, pagination

18. a matching session older than the loaded/default page is found;
19. historical compression-root/middle metadata matches and returns the one
    normal visible tip;
20. multiple matching segments in one lineage return one row and do not starve
    the next distinct lineage;
21. branch remains an independent visible result; delegate/tool children stay
    hidden;
22. `source`, `sources`, `exclude_sources`, `session_key`, cwd,
    `min_message_count`, archive modes, and child policy apply before final
    `LIMIT/OFFSET`;
23. enough globally matching but ineligible rows cannot starve eligible top-N;
24. more than SQLite's traditional bind-variable count of candidate row IDs
    succeeds;
25. non-matching pins do not leak; a matching pin retains the existing
    back-fill behavior;
26. current-session and gateway post-visibility cuts retain sufficient
    over-fetch;
27. candidate/MATCH and root page see one coherent read snapshot under a
    concurrent metadata update/quarantine fixture.

### Consumers

Extend:

- `tests/hermes_cli/test_session_listing.py` for CLI/gateway search delegation,
  current-session removal, source/session-key behavior, and no second fallback;
- `tests/test_hermes_state.py` for public list lineage/pagination behavior;
- `tests/hermes_cli/test_web_server_session_search.py` for exact ID first,
  metadata discovery, message fallback/append, and one lineage result;
- profile router tests for `/api/profiles/sessions?q=...` finding an old session
  outside the recent 200 without crossing profile/source/archive scope;
- Desktop Vitest coverage for debounced server search, empty-query recent list,
  stale response ordering, error recovery, and resume selection.

Retain and run the substrate suites:

- `tests/test_session_metadata_fts.py`;
- `tests/test_session_metadata_cjk_fts.py`;
- `tests/test_session_metadata_trigram_fts.py`.

---

## Benchmark and equivalence plan

Add a disposable benchmark driver such as
`scripts/benchmarks/session_metadata_picker.py`. It must never mutate the
operator's live state DB.

Corpus matrix:

- 1k, 10k, and 100k session rows;
- realistic compression-chain lengths and multiple matches per lineage;
- source/session-key/archive/pin/branch/delegate/tool mixes;
- ASCII token, ASCII interior infix, punctuation-compacted title/display,
  punctuated ID, CJK 2+, CJK 1-char, mixed CJK/Latin, wildcard literals,
  zero-hit, and high-cardinality queries.

Compare:

1. pinned legacy broad-LIKE/list path;
2. new routed FTS hit path;
3. routed valid-zero -> LIKE once;
4. direct unsupported -> LIKE;
5. end-to-end result shaping through lineage/filters/page hydration.

For each query/filter fixture record warm p50/p95 (at least 30 measured runs),
candidate count, distinct visible-root count, fallback call count, and final
row count. Record cold-open samples separately instead of mixing them into warm
percentiles.

Correctness gate:

- final visible IDs, ordering, and duplicate behavior equal the canonical
  legacy reference fixtures for the #16 contract;
- known current bugs (compact display-name omission and nonmatching pinned
  leakage) use the #16/#14 expected result, not accidental legacy output;
- FTS-hit fixtures report zero LIKE executions;
- zero/direct fallback fixtures report exactly one LIKE execution.

Performance gate:

- routed indexable p50/p95 must materially beat the legacy broad-LIKE path at
  10k/100k scale;
- preview/tip hydration count scales with surviving page rows, not global
  candidate count;
- no writer-lock convoy is introduced by the new read path;
- any hard candidate cap proposal fails review unless the starvation fixtures
  prove iterative exhaustion semantics.

Example command after the driver exists:

```bash
python scripts/benchmarks/session_metadata_picker.py \
  --sizes 1000 10000 100000 --iterations 30 --output /tmp/session-metadata-picker.json
```

---

## Ordered implementation commits

1. `test(state): pin session metadata route and fallback contract`
   - RED classifier, three-field semantics, no-LIKE-on-hit, zero/direct
     fallback, wildcard escaping, and capability failure tests.
2. `refactor(state): add snapshot-aware metadata candidate router`
   - internal route result states;
   - optional shared read connection for the three candidate helpers;
   - canonical compact/fallback helper;
   - CJK+Unicode union and `row_id` dedupe.
3. `perf(state): narrow session listing before lineage hydration`
   - JSON candidate source;
   - reverse compression closure + existing eligibility;
   - forward chain only for surviving roots;
   - final pagination;
   - candidate-constrained pin back-fill.
4. `test(state): cover whole-store lineage visibility and pagination`
   - historical matches, duplicate lineages, branches/delegates, filters,
     >999 candidates, pins, coherent snapshot.
5. `feat(api): route session metadata discovery through the common seam`
   - REST search exact-ID fast path + common metadata lane + message FTS;
   - remove parallel broad ID scan from this consumer;
   - optional `q` on existing profile list endpoint.
6. `feat(desktop): search the whole session store in the resume picker`
   - debounced profile query, stale-response guard, empty-query recent state,
     error/loading behavior, Vitest coverage.
7. `perf(search): benchmark routed session metadata candidates`
   - disposable corpus driver, result-equivalence fixtures, p50/p95 report.

Keep commits independently testable. Do not mix #28 ranking, #39 read sweeps,
or unrelated list materialization into them.

---

## Validation commands

Use the repository's canonical Python runner; never call `pytest` directly.

Narrow state/search tests:

```bash
scripts/run_tests.sh \
  tests/test_session_metadata_picker_routing.py \
  tests/test_session_metadata_fts.py \
  tests/test_session_metadata_cjk_fts.py \
  tests/test_session_metadata_trigram_fts.py -q
```

Listing/API regression tests:

```bash
scripts/run_tests.sh \
  tests/hermes_cli/test_session_listing.py \
  tests/hermes_cli/test_web_server_session_search.py \
  tests/test_hermes_state.py -q
```

Desktop/web targeted checks (use the actual new test paths):

```bash
npm --prefix apps/desktop run test -- src/components/session-picker.test.tsx
npm --prefix apps/desktop run typecheck
npm --prefix web run test -- src/pages/SessionsPage.test.tsx
npm --prefix web run typecheck
```

Lint every touched file, run the benchmark matrix, then run the broader
repository test slices selected by the final diff. A full Python suite remains
the final CI-parity check:

```bash
scripts/run_tests.sh
```

---

## Upstream reuse / ancestry audit

| Upstream evidence | Status at this audit | Decision |
|---|---|---|
| [NousResearch #57685](https://github.com/NousResearch/hermes-agent/pull/57685), merge `19d4174454624a1ca91bc47b8f2a7ae8c3b4b5d3` | Merged; commit is an ancestor of the pinned fork base | Reuse the merged `/sessions search` + `list_sessions_rich(search_query)` seam. This is the primary architecture. |
| [#71225](https://github.com/NousResearch/hermes-agent/pull/71225) | Open/unmerged | Evidence for candidate-first metadata and provenance/ranking only. Do not port fuzzy scoring or `matched_on`; #28 owns it. |
| [#71597](https://github.com/NousResearch/hermes-agent/pull/71597), [issue #81490](https://github.com/NousResearch/hermes-agent/issues/81490) | Unmerged/open evidence | Confirms search must cover the whole store, not the loaded page. |
| [#71912](https://github.com/NousResearch/hermes-agent/pull/71912) | Open/unmerged; not an ancestor | Evidence for `display_name` in the shared list seam. The fork's accepted #12/#16 code is authoritative. |
| [#62399](https://github.com/NousResearch/hermes-agent/pull/62399) | Closed/unmerged | Separate Desktop title/display candidate path is negative architecture evidence; do not recreate it. |
| [#63389](https://github.com/NousResearch/hermes-agent/pull/63389), [#77214](https://github.com/NousResearch/hermes-agent/pull/77214) | Evidence only | Supports cheap candidate projection before expensive hydration; no clean cherry-pick is required. |
| [#57595](https://github.com/NousResearch/hermes-agent/pull/57595), [#36082](https://github.com/NousResearch/hermes-agent/pull/36082), [#61075](https://github.com/NousResearch/hermes-agent/pull/61075) | Closed/unmerged evidence | Preserve source/user/origin eligibility before final candidate LIMIT. Current fork `session_key` + `_resume_row_visible` remains authoritative. |
| [#73344](https://github.com/NousResearch/hermes-agent/pull/73344) and follow-ups listed on #18 | Upstream read-split prior art; abstraction already present in fork | Use current `_read_ctx()` contract for new reads. Do not import the old per-thread-lifetime implementation or broaden into #18. |

There is no clean upstream PR to cherry-pick for the fork's final three-index
router. #14 is a local integration over accepted substrate, using merged
upstream list semantics and open upstream only as review evidence.

---

## Explicit ticket boundaries

### #28 — ranking/provenance

Out: relevance-before-limit, field weights, exact/prefix/word/fuzzy scoring,
BM25, `matched_on`, and public provenance. #14 preserves existing recency and
first-win adapter behavior only.

### #29 / current lineage work

Out: a new persistent lineage column or general query-local path-compression
subsystem. The candidate CTE must dedupe correctly; optimizing unrelated or
remaining lineage walks is separate. #29's historical findings and later
lineage tickets do not authorize semantic changes to branch/delegate edges.

### #18 / #39 — read-path sweep

In: every **new** #14 query uses `_read_ctx()` and route/list reads share a
coherent snapshot where practical.

Out: moving every existing `get_compression_tip`, rich-row hydration, resolver,
or non-metadata read off the writer lock. #18's latest execution order requires
#39 after #14 specifically so the sweep audits the final path.

### Exact `/resume` binding

Out: title ambiguity, unique prefix binding, authorization, and resume target
resolution. Discovery can match many rows; binding remains exact/guarded.

---

## `/implement #14` handoff

#14 is ready on this research result.

The central invariant is:

> FTS (or one direct/zero-result LIKE fallback) supplies stable metadata
> `row_id` candidates across the whole store; existing lineage and eligibility
> determine visible logical sessions; only then may ordering, pagination,
> preview hydration, and tip projection run.

Implementation should verify the pinned seams against current `dev`, create the
RED classifier/fallback tests first, then execute the seven commit-sized steps
above. It should not repeat the architecture audit or wait on #28/#39.
