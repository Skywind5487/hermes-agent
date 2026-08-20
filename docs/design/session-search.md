# Session Search — composed #109 feature contract

Status: implemented (composition on `fork/session-search`, base `main@243352e7b`)
Author: Skywind5487
Tickets: umbrella #109 · children #128 / #129 / #130 · topology #134
Recon: #109 `RECON FINAL` comment `5351011142`; maintainer composition decision
comment `5351051799`

## What this is

The #109 Session Search feature composes three independently-owned child line
contracts plus the current-upstream message-content substrate into one
end-to-end capability. It is **not** a fourth Session Search implementation:
after composition the production delta is zero — the shared state/search
seams own all behavior, and #109 owns only the cross-line bundle acceptance
evidence (`tests/test_session_search_bundle.py`).

```text
#128 metadata discovery ─┐
#129 lineage identity ───┼─ SHARES_SUBSTRATE ─> composed Session Search
#130 exact title binding ┘
current upstream search_messages() = reused content substrate
```

## Owned behavior per line

- **#128 metadata discovery** — `SessionDB.list_sessions_rich(search_query=…)`
  routes Unicode / optional CJK / trigram session-metadata FTS with bounded
  literal-safe LIKE fallback over `title`, logical `id`, and gateway
  `display_name`; integrated into Desktop `GET /api/sessions/search` and the
  CLI/Gateway listing seam.
- **#129 compression-aware lineage** — `SessionDB.resolve_lineage_winners`
  (plus `tools/session_search_tool.py` `_resolve_lineage`) composes positive
  compression-continuation roots with one query-local memo, `B=2000` work
  bound, fail-closed missing-parent/cycle, early-K, and deferred/bounded
  hydration. Generic branch/delegation/tool ancestry stays distinct.
- **#130 literal-safe exact/numbered title binding** —
  `SessionDB.resolve_session_by_title` / `get_next_title_in_lineage` admit only
  strict literal `base + " #" + ASCII[0-9]+` continuations; LIKE metacharacters
  (`%` `_` `\`), embedded `#`, CJK, and Unicode-digit lookalikes stay literal.

## Composition seams (shared files are substrate, not dependencies)

- `hermes_state.py` — shared by #128 + #130 (`SHARES_SUBSTRATE`).
- `hermes_state_search.py` — shared by #128 + #129 (`SHARES_SUBSTRATE`).
- No hard `REQUIRES` edge exists among the children.

## Bundle acceptance contract (RED 1–3)

`tests/test_session_search_bundle.py` pins the cross-line composition:

- **RED 1 (#128 × #130)** — fuzzy metadata discovery may surface a literal
  `foo #bar` title, while exact binding for `foo` must never treat it as a
  numbered continuation. Literal `%`/`_`/`\`/embedded-`#` bases do not widen
  either lane.
- **RED 2 (#129 × #130)** — through the shared `session_search` caller,
  title-first binding picks the strict `foo #N` family; the title's
  compression root excludes its own segments from winner selection; positive
  compression segments dedupe to one logical result; generic branch/delegation
  ancestry remains a distinct winner; hydration stays deferred/bounded.
- **RED 3 (#128 × upstream lineage substrate)** — Desktop
  `GET /api/sessions/search` discovers a rotated/compressed conversation by a
  stored chain-member title, collapses the chain to one surfaced conversation
  (projected to the live tip), preserves the stored title in the row, and
  keeps source filters, `limit`, and the message-content lane intact.

## Acceptance state

All child suites pass on the composed tree (lineage 37, tool 51, metadata FTS
36, title 29, web-server + listing 62) and the 8 bundle regressions pass —
273 tests green, `tests/test_hermes_state.py` 260 passed 2 skipped. No
known unfixed production correctness blocker. See #109 for umbrella
accounting/closure.
