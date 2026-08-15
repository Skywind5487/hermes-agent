# Research: numbered-title literal safety for #15 (issue #38)

**Date:** 2026-08-15
**Pinned BASE_SHA:** `196b90441` — `origin/dev` HEAD (post final-#12 acceptance / #79 close, #82 merge, #40 research map). Verified 2026-08-15; implement #15 against this SHA.
**Ticket:** Skywind5487/hermes-agent issue #38 → #15
**Status:** Research complete — plan posted on #15.

All line numbers below are against the pinned BASE (`196b90441`, clean worktree `.worktrees/issue-38-research`).

---

## 1. Residual-scope audit — #15 is still a real, live bug

#15's premise is verified against current `dev`:

- `resolve_session_by_title()` (hermes_state.py:6917) still runs two numbered-variant lanes:
  - `_like_numbered_variants()` (hermes_state.py:6946) — SQL `LIKE '{escaped} #%' ESCAPE '\'`, no numeric-suffix validation.
  - `_fts_numbered_variants()` (hermes_state.py:7387) — FTS5 + CJK dispatch, post-filter `title.startswith(f"{escaped} #")`.
- The FTS lane is the **production** path: `_fts_enabled` is set by `_ensure_fts_schema("messages_fts", ...)` (hermes_state_schema.py:1173/1189) and is True on any FTS5-capable SQLite — i.e. effectively always. So both bugs below are reachable in production, not just in the LIKE fallback.

Two distinct defects (both in #15 scope):

1. **SQL wildcard escaping leaks into a Python literal comparison** (Q1) — the FTS post-filter reuses the SQL-escaped string as a Python `startswith()` prefix. False **negatives** for titles containing `%`, `_`, `\`.
2. **The `#N` suffix is not validated as an integer** (Q2) — both lanes accept `foo #bar` / `foo #` / `foo # 2` / `foo #2x` as continuations. False **positives**.

`get_next_title_in_lineage()` (hermes_state.py:7438) already encodes the canonical `#N` grammar for *generation* (`re.match(r'^(.*?) #(\d+)$')`, line 7450); the *resolution* path does not reuse it.

---

## 2. File / symbol / line map (pinned `196b90441`)

### hermes_state.py
| Line | Symbol | Role |
|---|---|---|
| 6903 | `get_session_by_title(title)` | exact `WHERE s.title = ?` (B-tree equality on unique title) |
| 6917 | `resolve_session_by_title(title)` | exact first → numbered lanes → latest by `started_at DESC` |
| 6946 | `_like_numbered_variants(title)` | LIKE `f"{escaped} #%"` ESCAPE `'\\'`, raw rows, **no numeric check** |
| 7387 | `_fts_numbered_variants(title)` | CJK dispatch → `_fts_cjk_metadata_candidates` / `_fts_metadata_candidates`; post-filter `startswith(f"{escaped} #")` at **7420** (CJK) and **7435** (non-CJK); `escaped` computed at **7407** |
| 7438 | `get_next_title_in_lineage(base_title)` | strip `re.match(r'^(.*?) #(\d+)$')` (7450); `LIKE '{escaped} #%'` (7457); max via `re.match(r'^.* #(\d+)$')` (7466) |
| 7473 | `get_compression_tip(session_id)` | resume projection — walks compression chain (not title search) |
| 8907 | `resolve_resume_session_id(session_id)` | wraps `get_compression_tip`; gateway/WebUI resume projection |
| 6610 | `resolve_session_id(prefix)` | inline LIKE-escape copy (ID prefix) |
| 7707 | `_like_pattern` (in `list_sessions_rich`) | inline LIKE-escape copy (broad `%LIKE%` recall) |
| 10458 | cwd `LIKE` clause | inline LIKE-escape copy |

### hermes_state_search.py
| Line | Symbol | Role |
|---|---|---|
| 1870 | `_sanitize_fts5_query(query)` | FTS5 MATCH sanitizer (used by `_fts_numbered_variants`) |
| 1959 | `_contains_cjk(text)` | CJK classifier |
| 2402 / 3190 / 3446 | inline LIKE-escape copies | message-content `LIKE` clauses |

### hermes_state_common.py
| Line | Symbol | Role |
|---|---|---|
| 753 | `SESSION_METADATA_COMPACT_SEPARATORS` | trigram compact separators `- _ . space` |
| 756 | `compact_session_metadata_text` | trigram compact policy |

### hermes_state_schema.py
| Line | Symbol | Role |
|---|---|---|
| 1173 / 1189 | `self._fts_enabled = ...` | enables the production FTS lane |

---

## 3. Caller inventory

### Callers of `resolve_session_by_title` (exact title→latest-continuation binding)
- `tools/session_search_tool.py:664` — `_title_match_result()` (session-search tool "session title matched" lane; title lineage excluded via `_resolve_lineage`).
- `hermes_cli/main.py:1505` — `_resolve_session_by_name_or_id()` (CLI `--resume` / `--session`); then projects through `get_compression_tip` (~1513).
- `gateway/slash_commands.py:4429` — gateway `/resume`; then `resolve_resume_session_id` (hermes_state.py:8907).
- Tests (mocked): `tests/gateway/test_matrix_project_context_isolation.py:314/321/332`, `tests/gateway/test_session_boundary_security_state.py:91`.

### Callers of `get_session_by_title` (exact equality)
- `cli.py:10011` — `/title` uniqueness pre-check.
- `tui_gateway/methods_session.py:337`.

### Callers of `get_next_title_in_lineage` (title generation / dedup)
- `agent/conversation_compression.py:3335` — compression continuation auto-title (`old_title` → next).
- `agent/title_generator.py:227` — `_persist_session_title()` ValueError dedup.
- `hermes_cli/cli_commands_mixin.py:1132` — `/branch` auto-title.
- `hermes_cli/sessions_cmd.py:970` — `sessions retitle-skills` dedup.
- `cron/scheduler.py:85, 3830` — cron session title dedup.
- `gateway/platforms/api_server.py:3456` — API fork title.
- `gateway/slash_commands.py:4614` — gateway `/branch`.
- `tui_gateway/methods_session.py:2627-2628`.

---

## 4. Answers to the four questions

### Q1. Where is SQL wildcard escaping confused with literal Python comparison?

**`_fts_numbered_variants`, hermes_state.py:7407 → 7420 & 7435.**

```python
escaped = title.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")   # 7407 (SQL escape)
...
if c["title"] and c["title"].startswith(f"{escaped} #")                          # 7420 / 7435 (Python literal!)
```

`escaped` is built for **SQL** `LIKE ... ESCAPE '\'`. Reusing it as a Python literal prefix is wrong: the FTS lane then misses real continuations whose title contains `%`, `_`, or `\`.

Concrete false negative (FTS enabled, i.e. production):
- base `my_notes`, continuation title `my_notes #2` (indexed).
- FTS MATCH finds the row; post-filter tests `"my_notes #2".startswith("my\_notes #")` → **False** → row dropped.
- `_fts_numbered_variants` returns `[]` (not `None`), so `resolve_session_by_title` does **not** fall back to LIKE → returns `None`. The continuation is silently unresolvable. (Same for `100% done #2`, `a\b #2`, CJK titles containing `_`/`%`.)

The LIKE lane (`_like_numbered_variants`, 6948) escapes **correctly** for SQL — but only that lane; and it is only reached when FTS is off or returns `None`.

### Q2. What exact grammar counts as a numbered continuation suffix?

The canonical grammar is already defined by `get_next_title_in_lineage`'s strip regex (hermes_state.py:7450):

```
numbered_suffix  := " #" [0-9]+   anchored at end of title
numbered_variant := <base> <numbered_suffix>
```

i.e. a session is a numbered continuation of `base` iff `title == base + " #" + digits`. The unnumbered original is the family root; numbered members are `#2`, `#3`, ... (the strip regex accepts `#1` too — an explicitly-titled root; see truth table).

Neither lane enforces this today:
- `_like_numbered_variants`: `LIKE '{escaped} #%'` — `%` matches anything after `#`.
- `_fts_numbered_variants`: `startswith('{escaped} #')` — accepts anything after `#`.

**Lookalikes that MUST fail** (resolving base `foo`):
`foo #bar` · `foo #` · `foo # 2` · `foo #2x` · `foo ##2` · `foo #2.0` · `foo2` (no ` #`) · `foo #2 ` (trailing space) · `foo #２` (fullwidth digits, if ASCII-only grammar).

**Ambiguities to pin in the implementation:**
- `foo #1` — N=1 equals the unnumbered root. Recommendation: accept (it satisfies the `#N` integer form and `get_next_title_in_lineage` treats the unnumbered as #1), and document.
- `foo #01` / `foo #002` — leading zeros parse as integer; accept for consistency with the strip regex, document.
- Fullwidth digits — Python `re` `\d` matches Unicode digits, SQLite `CAST(... AS INTEGER)` does not. Recommendation: use ASCII `[0-9]` in **both** the resolve predicate and (optionally) tighten `get_next_title_in_lineage`'s strip regex so generation and resolution share one grammar.

**One shared predicate** should drive both lanes (and be unit-testable):

```python
# hermes_state.py (module level or static)
_NUMBERED_SUFFIX_RE = re.compile(r"^(.*?) #([0-9]+)$")

def _numbered_variant(title, base) -> Optional[int]:
    m = _NUMBERED_SUFFIX_RE.fullmatch(title or "")
    if not m or m.group(1) != base:
        return None
    return int(m.group(2))
```

FTS lane post-filter uses the **raw** `base` (never the escaped string); LIKE lane filters its raw rows through the same predicate instead of trusting `#%`.

### Q3. Can the fix reuse a shared LIKE-escape helper?

**Yes — and upstream already merged one.** `hermes_state_common.escape_like` exists on upstream main:

- Commit `52a5fc004` "refactor(state): consolidate SQL LIKE escaping onto one shared helper" (kshitij, 2026-08-06) moved the 3-replace chain to `hermes_state_common.escape_like` and routed every copy through it; follow-ups `4bab91944` (`_cwd_prefix_clause`) and `1d2dabce5` (prune/archive substring filters).
- Upstream wiring: `hermes_state.py:64` `escape_like as _escape_like`, `hermes_state_search.py:29` — the comment there states search "must not import hermes_state (cycle)", which is exactly why the fork's `hermes_state_common` exists.
- **The fork does not have it**: 8 inline copies remain (hermes_state.py:6610/6948/7407/7453/7707/10458; hermes_state_search.py:2402/3190/3446).

Upstream definition (port verbatim so future sync is clean):

```python
def escape_like(text: str) -> str:
    """Escape SQL LIKE wildcards so operator/session-derived text matches
    literally.  Pair with ``ESCAPE '\\'`` in the clause."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

`#15` should add it to `hermes_state_common.py` and route the numbered-title SQL clauses through it. The Python literal post-filter in `_fts_numbered_variants` must use the **raw** base (this is precisely the point of Q1). Consolidating the other five copies is a mechanical upstream-sync item — flag it, but keep it out of #15 scope.

### Q4. Which callers require exact binding semantics (separate from #14 broad discovery)?

Exact-binding surfaces that **must not** be folded into #14's routed-FTS candidate path:

| Surface | Why exact |
|---|---|
| `resolve_session_by_title` + `_like_numbered_variants` + `_fts_numbered_variants` | title→latest-continuation resume binding; predicate is a **prefix** `base #N`, not `%LIKE%` recall |
| `get_session_by_title` | B-tree equality on unique title — cheaper than any FTS route |
| `get_next_title_in_lineage` | title-generation dedup, not search at all |
| `get_compression_tip` / `resolve_resume_session_id` | resume projection (lineage walk), no title predicate |

Consumers of these: CLI `_resolve_session_by_name_or_id` (hermes_cli/main.py:1505), gateway `/resume` (slash_commands.py:4429), session-search tool title lane (session_search_tool.py:664), `/branch` + title dedup paths.

#14's broad discovery is `list_sessions_rich(search_query=...)` (hermes_state.py:7685+), `%LIKE%` over title/id/display_name (+ compact variant) for the picker. Different shape (`%...%` vs `base #N` prefix), different purpose (recall vs exact binding). #14's own acceptance criteria already say "Exact title/session-ID binding paths that have cheaper B-tree/exact semantics remain separate from broad discovery where appropriate." **#15 must not move `resolve_session_by_title` onto the #14 candidate lanes** — the fix is local to the numbered-title helpers.

---

## 5. Edge-case truth table

`resolve_session_by_title(base)` with FTS enabled (production). Current → Required:

| # | base | existing titles | current | required |
|---|---|---|---|---|
| 1 | `my project` | `my project`, `my project #2` | `my project #2` | `my project #2` |
| 2 | `my project` | `my project` | exact | exact |
| 3 | `foo` | `foo #bar` | **`foo #bar` (BUG)** | `None` (no exact) |
| 4 | `foo` | `foo #` | **`foo #` (BUG)** | `None` |
| 5 | `foo` | `foo # 2` | **`foo # 2` (BUG)** | `None` |
| 6 | `foo` | `foo #2x` | **`foo #2x` (BUG)** | `None` |
| 7 | `foo` | `foo ##2` | **`foo ##2` (BUG)** | `None` |
| 8 | `my_notes` | `my_notes #2` | **`None` (BUG, FTS literal)** | `my_notes #2` |
| 9 | `100% done` | `100% done #2` | **`None` (BUG, FTS literal)** | `100% done #2` |
| 10 | `a\b` | `a\b #2` | **`None` (BUG, FTS literal)** | `a\b #2` |
| 11 | `專案` | `專案 #2` | `專案 #2` (CJK lane) | `專案 #2` |
| 12 | `專案_甲` | `專案_甲 #2` | **`None` (BUG, CJK literal)** | `專案_甲 #2` |
| 13 | `Project` | `Project #3` in `(P,H]` gap | `C` (gap-merged) — covered test_session_metadata_fts.py:1314 | `C` (unchanged) |
| 14 | `Base Title` | `Base Title #2`, FTS lane dropped | `base2` via LIKE fallback — covered test_session_metadata_fts.py:1341 | `base2` |
| 15 | `foo` | `foo #1` (explicit) | `foo #1` | `foo #1` (document N=1) |
| 16 | `foo` | `foo #01` | `foo #01` | `foo #01` (document leading zeros) |

`get_next_title_in_lineage` behavior (already correct; only escape-helper reuse + optional `\d`→`[0-9]`):
- `get_next_title_in_lineage("foo")` with `foo`, `foo #2` → `foo #3` (max from `#(\d+)$` only).
- `get_next_title_in_lineage("foo #2")` → strip → `foo` → `foo #3`.
- `foo #bar` never inflates max (anchor `$`), only the `existing` gate — benign.

---

## 6. Upstream ancestry audit

1. **Merged + in base (reuse, do not re-derive):** `get_session_by_title`, `resolve_session_by_title`, `get_next_title_in_lineage` introduced by commit `60b6abefd` ("feat: session naming with unique titles, auto-lineage, rich listing, resume by name") — present in **both** upstream `main` and fork `dev` (verified `merge-base --is-ancestor 60b6abefd origin/dev` = 0).
2. **Fork-local (evidence only / not upstreamable as-is):** `_like_numbered_variants` + `_fts_numbered_variants` added by fork commit `2ac803bd9` ("feat(session-search): port sessions FTS5 title index (unicode61 + cjk_unicode61) from dev"); not present in upstream main. The FTS/CJK lane is a fork enhancement layered on the upstream function.
3. **Merged upstream accepted helper (cherry-pick candidate):** `hermes_state_common.escape_like` — commits `52a5fc004`, `4bab91944`, `1d2dabce5` on upstream main. The fork's `hermes_state_common` module (with the same "no hermes_state import / cycle" contract) is the drop-in home.
4. **Upstream has NOT fixed the #N validation:** upstream `main`'s `resolve_session_by_title` (hermes_state.py:8222) is still pure-LIKE with `" #%"` (over-match, no integer check). The #15 literal-safety fix is fork-local work that upstream hasn't done.

---

## 7. Implementation plan (commit-sized, for #15)

Branch from pinned `196b90441`. Suggested commits:

1. **Commit A — test (RED).** Extend `tests/test_hermes_state.py` (`TestTitleSqlWildcards` + a new `TestNumberedTitleLiteralSafety`) and `tests/test_session_metadata_fts.py`:
   - `%` in title: `100% done #2` resolvable (LIKE + FTS lanes).
   - `\` in title: `a\b #2` resolvable.
   - `_` continuation via **FTS lane**: `my_notes #2` resolvable (the current false-negative; needs the FTS-enabled fixture from test_session_metadata_fts.py).
   - non-numeric lookalikes rejected: `foo #bar`, `foo #`, `foo # 2`, `foo #2x`, `foo ##2` → `None` (or exact).
   - CJK numbered: `專案 #2` and `專案_甲 #2` resolvable (test_session_metadata_cjk_fts.py).
   - whitespace / exact-title behavior preserved.
   - gap continuation test (existing 1314) kept green.
2. **Commit B — feat: shared escape helper.** Add `escape_like` to `hermes_state_common.py` (port verbatim from upstream `52a5fc004`); import as `_escape_like` in `hermes_state.py` and `hermes_state_search.py`. Route `_like_numbered_variants` (6948) and `get_next_title_in_lineage` (7453) SQL clauses through it. (Optionally also `resolve_session_id` 6610 / `_like_pattern` 7707 / cwd 10458 / search 2402·3190·3446 — flag as follow-up upstream-sync, keep #15 narrow.)
3. **Commit C — feat: strict `#N` validation on raw base.** Add `_numbered_variant(title, base)` predicate (ASCII `[0-9]`). In `_fts_numbered_variants`, replace `startswith(f"{escaped} #")` (7420/7435) with the raw-base predicate. In `_like_numbered_variants`, post-filter raw rows with the same predicate (stop trusting `#%`). `resolve_session_by_title` semantics unchanged: exact → numbered (latest by `started_at DESC`) → exact-or-None.
4. **Commit D — docs.** This note + docstring updates on the three functions.

**Validation commands (from the worktree, main-repo `.venv`):**
```bash
.venv\Scripts\python.exe -m pytest tests/test_hermes_state.py -k "Title or Numbered or Wildcard" -q
.venv\Scripts\python.exe -m pytest tests/test_session_metadata_fts.py -k "title or numbered" -q
.venv\Scripts\python.exe -m pytest tests/test_session_metadata_cjk_fts.py -q
.venv\Scripts\python.exe -m pytest tests/test_session_metadata_trigram_fts.py tests/test_fts_lifecycle_registry.py -q
uvx ruff check hermes_state.py hermes_state_common.py hermes_state_search.py tests/test_hermes_state.py tests/test_session_metadata_fts.py
```
Env note (fork): tests run with the main repo `.venv` python from the worktree dir; ruff via `uvx ruff`. `tests/hermes_cli/test_session_recovery.py::test_cli_allow_partial_salvages...` cp950 failure is a pre-existing env artifact on base — ignore.

---

## 8. Non-goals (separating exact binding from #14)

- Do **not** fold `resolve_session_by_title` / numbered-title resolution into #14's routed-FTS candidate path (Q4 — exact binding stays local; #14's own acceptance mandates the split).
- Do **not** change the "latest by `started_at DESC`" resume-ordering semantics (number-based ordering is a separate concern, not requested by #15).
- Do **not** do the full repo-wide LIKE-escape consolidation (5 non-#15 sites) in this ticket — mechanical upstream-sync follow-up.
- Do **not** touch `get_compression_tip` / `resolve_resume_session_id` / lineage-walk semantics.
- No schema / FTS-storage changes (#12/#31 territory, already settled).
- No change to the public API (`resolve_session_by_title` / `get_session_by_title` / `get_next_title_in_lineage` signatures).
