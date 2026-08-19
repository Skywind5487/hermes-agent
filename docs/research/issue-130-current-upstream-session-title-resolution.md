# Research: current-upstream literal-safe Session Title resolution for #130

**Date:** 2026-08-19  
**Ticket:** [#130](https://github.com/Skywind5487/hermes-agent/issues/130)  
**Parent:** [#109 Session Search](https://github.com/Skywind5487/hermes-agent/issues/109)  
**Historical accepted behavior:** [#15](https://github.com/Skywind5487/hermes-agent/issues/15) / [PR #88](https://github.com/Skywind5487/hermes-agent/pull/88)  
**Current Wayfinder:** [#134](https://github.com/Skywind5487/hermes-agent/issues/134)

## Pins and authority

- Fork `dev` research-source pin: `fa5ed679cc6559c619038f327e6276f4b7e8d735`.
- Fork clean tracking `main`: `243352e7b8bddc9f33eba1b6506810f8dd88beaa`.
- Current upstream `main` at final refresh: `dc77f2c87f11c4929e151e65396b8f321a1d6a14`.
- Relevant current-upstream code was inspected at `ab173e26d2aa0300f22f5a5944c0284d732cfa8f`; upstream moved during research by two commits to `dc77f2c...`. A GitHub compare from `ab173e26...` to `dc77f2c...` changes only `.github/workflows/ci.*` and `.github/workflows/label-rerun.yml`, so the audited SessionDB/title code is unchanged at the final pin.

Primary sources used: current upstream source/tests, fork #15/#88/#38 evidence, current issue/Wayfinder bodies, and upstream PR metadata. Open/unmerged PRs are design/provenance evidence only.

## 查到什麼

### 1. Verdict: #130 is still a live residual, but much smaller than PR #88

**VERIFIED.** Current upstream has absorbed the SQL-LIKE literal escaping substrate, but it has **not** absorbed the strict numbered-title grammar accepted by fork #15.

The correct reconstruction is therefore **not** a replay of PR #88. Preserve the behavior contract, but implement it on current upstream's simpler title-binding seam.

Current ownership split:

| Contract / implementation concern | Current status | #130 action |
|---|---|---|
| exact title lookup (`get_session_by_title`) | upstream-owned | preserve unchanged |
| literal SQL LIKE escaping for `%`, `_`, `\\` | upstream-owned via `hermes_state_common.escape_like` | reuse, do not port helper |
| old fork `_fts_numbered_variants` / session-metadata-FTS title-binding lane | obsolete implementation shape on current upstream | do not reconstruct |
| numbered resolver must accept only literal `base + " #" + ASCII digits` | **missing upstream** | reconstruct |
| `get_next_title_in_lineage` must count only direct strict `#N` variants | **missing upstream** | reconstruct |
| suffix stripping must use ASCII digits, not Python Unicode `\d` | **missing upstream** | reconstruct |
| fuzzy / substring metadata discovery | separate #128 concern | keep out of #130 |

### 2. LIKE metacharacter escaping is already upstream-owned

**VERIFIED.** Current upstream `hermes_state_common.escape_like()` escapes backslash first, then `%` and `_`. Both `resolve_session_by_title()` and `get_next_title_in_lineage()` already use the shared helper.

Upstream provenance is merged commit [`52a5fc004`](https://github.com/NousResearch/hermes-agent/commit/52a5fc0048ac434bb9674c36e621463d6f1dfc5b), `refactor(state): consolidate SQL LIKE escaping onto one shared helper`. Its commit message explicitly lists title resolution and lineage numbering among the migrated call sites.

Therefore the old #15 work that introduced/ported `escape_like` is now **UPSTREAM_OWNED**. #130 should not add another escaping helper or inline replacement chain.

### 3. Current `resolve_session_by_title()` still over-accepts numbered variants

**VERIFIED.** Current upstream does:

1. exact lookup;
2. `LIKE f"{escape_like(title)} #%" ESCAPE '\\'`, ordered by `started_at DESC`;
3. if any row exists, return the first row;
4. otherwise return the exact match if present.

There is no Python/literal post-filter validating the suffix.

Consequences for resolving base `foo`:

- `foo #bar` is incorrectly eligible;
- `foo #` is incorrectly eligible;
- `foo # 2` is incorrectly eligible;
- `foo #2x` is incorrectly eligible;
- `foo #2.0` is incorrectly eligible;
- `foo #2 ` is incorrectly eligible;
- `foo #２` is incorrectly eligible at the resolver candidate layer because SQL `#%` accepts any suffix;
- a legitimate literal base containing `%`, `_`, or `\\` does **not** widen the SQL candidate set anymore because upstream escaping already fixes that part.

Exact-vs-numbered precedence should stay unchanged: if at least one **valid** numbered continuation exists, the latest valid numbered row by `started_at DESC` wins; otherwise the exact row wins.

### 4. Current `get_next_title_in_lineage()` violates the accepted grammar in three independent ways

**VERIFIED.** Current upstream still strips a suffix with:

```python
re.match(r'^(.*?) #(\d+)$', base_title)
```

and scans candidate titles with:

```python
re.match(r'^.* #(\d+)$', t)
```

The SQL candidate set is `title = base OR title LIKE escaped_base + " #%"`.

This leaves three residual bugs:

1. **Unicode-digit bug.** Python `\d` accepts non-ASCII decimal digits, so a title such as `foo #２` can be interpreted as a numbered suffix even though #15's accepted grammar is ASCII `[0-9]+`.
2. **Non-numeric occupancy bug.** If `foo` does not exist but `foo #bar` does, the SQL query returns a nonempty `existing` list; `max_num` stays 1, so the helper returns `foo #2` instead of the free base `foo`.
3. **Greedy deeper-suffix bug.** `^.* #(\d+)$` can read the final number from `foo #2 #5` as if it were a direct child of `foo`, inflating the next title to `foo #6`. Upstream PR #41223 independently reports exactly this bug.

These three should be solved by one shared literal grammar predicate rather than separate regex patches.

### 5. Smallest current implementation seam

**VERIFIED design recommendation, inferred implementation shape from current code + accepted fork contract.** Add one small helper local to the SessionDB/title module, equivalent in behavior to PR #88's `_numbered_variant_value(title, base)`:

- title must start with literal `base + " #"`;
- remaining suffix must be non-empty;
- suffix must be ASCII;
- suffix must consist only of decimal digits;
- return/parse the integer N when valid, otherwise no match.

Then reuse the same predicate in exactly two storage methods:

1. **`resolve_session_by_title()`** — keep the current escaped `LIKE "base #%"` as cheap candidate selection, filter candidates through the strict predicate, then preserve current `started_at DESC` selection and exact fallback.
2. **`get_next_title_in_lineage()`** — strip only an ASCII `#N` suffix; after SQL candidate selection, count only `title == base` or strict direct variants; compute max N through the same predicate.

Do **not** reintroduce `_fts_numbered_variants`, CJK FTS routing, session metadata FTS lifecycle, or a second escape helper. Literal Python string comparison naturally handles `#`, CJK, punctuation, `%`, `_`, and `\\` in the base once SQL candidate selection is escaped.

### 6. Required regression matrix on current upstream

**VERIFIED against #130/#15 acceptance; current upstream test file does not contain the strict near-miss/fullwidth/deeper-suffix cases searched below.** Minimum tests should cover both resolution and next-title generation where relevant:

| Case | Expected |
|---|---|
| `my project` + `my project #2` | valid continuation |
| `100% done` + `100% done #2` | valid; `%` literal |
| `my_notes` + `my_notes #2` | valid; `_` literal |
| `a\\b` + `a\\b #2` | valid; `\\` literal |
| `topic # hash` + `topic # hash #2` | valid; embedded `#` literal |
| CJK base + ` #2` | valid |
| `foo #bar` | reject |
| `foo #` | reject |
| `foo # 2` | reject |
| `foo #2x` | reject |
| `foo #2.0` | reject |
| `foo #2 ` | reject |
| `foo #２` | reject (fullwidth digit) |
| `foo #2 #5` while numbering base `foo` | do not count `#5` as direct child |
| `foo #01` | accept (ASCII digits; existing #15 contract) |
| `foo #1` | accept as syntactically valid numbered variant; unnumbered root still counts as 1 for generation |
| exact `foo` + invalid `foo #bar` | exact `foo` must win |

Current upstream `tests/test_hermes_state.py` does contain title-lineage and SQL-wildcard coverage, including underscore literal-safety, but targeted searches found no `foo #bar`, `#2x`, `fullwidth`, or `#2 #5` regression. #130 should extend the current `TestTitleLineage` / title-wildcard neighborhood instead of recreating the old fork's FTS-specific test suites.

### 7. Current caller map

**VERIFIED from current-upstream code search; production files only.** `resolve_session_by_title` currently has two production consumers:

- `tools/session_search_tool.py` — title-first exact-binding discovery via `_title_match_result()`;
- `gateway/slash_commands.py` — gateway `/resume <name>`, then compression-tip projection.

`get_next_title_in_lineage` currently has seven production consumers:

- `agent/title_generator.py` — dedupe title collisions;
- `cron/scheduler.py` — cron title dedupe;
- `hermes_cli/sessions_cmd.py` — session retitle/dedupe flow;
- `gateway/slash_commands.py` — gateway title/branch flow;
- `tui_gateway/methods_session.py` — TUI session title flow;
- `gateway/platforms/api_server.py` — API fork/title flow;
- `hermes_cli/cli_commands_mixin.py` — CLI branch/title flow.

The grammar helper is therefore shared storage semantics: fixing it once prevents inconsistent continuation numbering across these callers.

Pending caller prior art: upstream PR [#26631](https://github.com/NousResearch/hermes-agent/pull/26631) is still OPEN/unmerged and would route TUI `session.resume` title binding through `resolve_session_by_title()`. Treat it as future-caller evidence, not current authority.

### 8. Prior art classification

| Upstream work | Status verified 2026-08-19 | Relevance / disposition |
|---|---|---|
| commit `52a5fc004` shared `escape_like` | **merged and present** | upstream-owned exact overlap; reuse |
| PR [#41223](https://github.com/NousResearch/hermes-agent/pull/41223) anchor numbering to resolved base | **OPEN / unmerged** | directly confirms deeper-suffix bug; design evidence only; its `\d` shape alone would not satisfy ASCII-only #130 |
| PR [#14411](https://github.com/NousResearch/hermes-agent/pull/14411) truncated-title resume prefix | **CLOSED / unmerged** | adjacent resume UX + wildcard regressions; not current authority |
| PR [#61075](https://github.com/NousResearch/hermes-agent/pull/61075) source-scoped title uniqueness/resolution | **CLOSED / unmerged** | adjacent namespace semantics; not #130 and not landed |
| PR [#47442](https://github.com/NousResearch/hermes-agent/pull/47442) child title inheritance | **OPEN / unmerged** | consumer of `get_next_title_in_lineage`; not a grammar fix |
| PR [#26389](https://github.com/NousResearch/hermes-agent/pull/26389) auto-title dedup | **CLOSED / unmerged** | historical consumer evidence; current title generator independently uses the helper |
| PR [#26631](https://github.com/NousResearch/hermes-agent/pull/26631) TUI resume→title resolver | **OPEN / unmerged** | potential future exact-binding caller |
| PR [#67381](https://github.com/NousResearch/hermes-agent/pull/67381) session-search substring title matching | **OPEN / unmerged** | fuzzy discovery, belongs with #128; explicitly proposes removing the exact title-search tool lane |
| PR [#89553](https://github.com/NousResearch/hermes-agent/pull/89553) Desktop title search/surfacing | **OPEN / unmerged** | fuzzy/Desktop metadata discovery, #128 overlap; not replacement for #130 |

No reviewed upstream work is both merged **and** equivalent to the strict ASCII/direct-child grammar residual. Current source still demonstrates the residual, so #130 is not `UPSTREAM_OWNED / DROP`.

### 9. Relationship to #128 / #129

**VERIFIED from #109/#130/#134.** #130 is exact binding correctness, not recall/search-ranking policy.

- #128 owns fuzzy metadata discovery (`title` / logical id / `display_name`, substring/normalization/FTS/trigram/routing).
- #129 owns compression-aware search lineage/dedupe/hydration composition.
- #130 owns exact/numbered title family grammar and literal safety.

Shared callers/files are `SHARES_SUBSTRATE`, not a runtime `REQUIRES` edge. No hard dependency on #128 or #129 was found.

## 查不到什麼

No blocking unknown remains for implementation planning.

One source-system limitation occurred: GitHub contents fetches intermittently returned HTTP 429 while current upstream was moving. This did not leave a material gap because:

- the relevant `hermes_state.py`, `hermes_state_common.py`, tests, and key caller code had already been fetched from immutable SHA `ab173e26...`;
- the final upstream head `dc77f2c...` was refreshed;
- a commit comparison from `ab173e26...` to `dc77f2c...` showed only GitHub workflow files changed;
- current code search independently inventories the title helper callers.

There is no dedicated `docs/research/README` / catalog in the fork `dev` research directory; research notes are stored as a flat `docs/research/issue-*.md` set. This note follows that existing convention.

## 為什麼查不到

The only unavailable reads were transient GitHub anti-scraping/rate-limit responses (`429`), not missing repository evidence. They did not affect the conclusion for the reason above.

No runtime/live-DB reproduction was performed: this research ticket is an upstream-source reconstruction audit, and the defects are deterministically visible in the current SQL/regex logic. The implementation line should still run the regression matrix against an isolated test DB before acceptance.

## 研究者自我檢驗

### Competing explanation: "upstream shared escaping means #15 is fully absorbed"

Rejected. Shared escaping solves SQL wildcard widening, but current `resolve_session_by_title()` still trusts every `base + " #..."` row and `get_next_title_in_lineage()` still uses Unicode `\d` plus greedy final-number extraction. These are separate correctness properties.

### Competing explanation: "PR #41223 already fixes this"

Rejected as authority. #41223 is OPEN/unmerged, and current `main` still contains the greedy regex. It is useful independent confirmation of one bug only. Its proposed `\d` regex also does not by itself establish #130's ASCII-only grammar.

### Competing explanation: "#89553/#67381 should replace this with fuzzy title search"

Rejected. Those PRs target discovery/substring UX. Exact title→continuation binding has a different contract: a candidate that merely looks similar must never hijack `/resume` or exact title matching. #130/#109 explicitly preserve this boundary.

### Competing explanation: "replay PR #88 because it is already accepted"

Rejected. PR #88's behavior is authoritative historical evidence, but its `_fts_numbered_variants` architecture belonged to the old fork Session Metadata FTS stack. Current upstream has a simpler direct LIKE lane and already owns the escape helper. Replaying implementation shape would add obsolete coupling.

### Scope audit

In scope and answered:

- current upstream ownership vs fork residual;
- exact current implementation seam;
- strict suffix grammar and edge cases;
- current production callers;
- upstream prior art and merge status;
- separation from #128/#129;
- implementation/test shape.

Out of scope intentionally:

- fuzzy title/display-name ranking or indexes (#128);
- compression-lineage search composition (#129);
- changing title uniqueness/source namespace policy;
- implementing the fix in this research branch.

## 結論與下一步

**Decision:** keep #130 alive as a small internal Session Search line. It is **PARTIAL UPSTREAM ABSORPTION**, not a no-op.

Recommended implementation intent:

> Reconstruct one strict literal numbered-title predicate on current upstream, reuse upstream `escape_like`, post-filter resolver candidates, and make next-title generation use the same ASCII/direct-child grammar.

Expected production delta should be small: primarily `hermes_state.py` plus focused current-upstream tests. No FTS schema/lifecycle change, no new index, no fuzzy search, no new escape helper.

Before coding, refresh upstream `main` once more (it moved during this research), then implement against the new immutable base. If #41223 merges before implementation, re-audit the residual: its direct-child fix may shrink one clause, but ASCII-only suffix validation and resolver post-filtering still need explicit verification before classifying #130 as absorbed.
