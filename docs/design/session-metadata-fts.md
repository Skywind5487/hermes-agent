# Session metadata FTS (#25) — stable `row_id` + resumable Unicode external-content index

Status: **implemented on `fts/session-row-id-unicode-migration`** (fork #25).

This documents the architecture shipped by #25 and the invariants later tickets
(#26 CJK, #27 unified lifecycle/storage settlement, #30 normalized trigram) build on.
Research context: [`docs/research/issue-32-stable-row-id-unicode-session-fts.md`](../research/issue-32-stable-row-id-unicode-session-fts.md).

## What changed

1. `sessions` gained a named `row_id INTEGER PRIMARY KEY AUTOINCREMENT` while
   `id TEXT NOT NULL UNIQUE` stays the logical/public identity. Legacy hidden
   `rowid` values are copied verbatim into `row_id` (deleted-row holes
   preserved), in one `BEGIN IMMEDIATE` transaction.
2. `sessions_fts` is now **external-content** over the raw canonical
   `(title, id, display_name)` tuple, keyed by `content_rowid='row_id'`,
   `tokenize='unicode61'`. No normalization or synthetic concatenation
   (that is #30's scope).
3. Startup no longer runs a blocking one-shot Unicode backfill. It stages a
   durable H/P claim (`fts_session_rebuild_high_water` / `..._progress`) and
   the historical rows are backfilled by the resumable chunk engine, sharing
   the message rebuild's crash-safe claim/repair/finish rules and its single
   monkeypatchable pause helper.
4. The sessions_fts upgrade is **decoupled from the message-FTS layout**: it
   runs on BOTH the legacy v22-inline-messages path and the v23 path.
   `_migrate_sessions_row_id()` rebuilds `sessions` via `DROP TABLE`, which
   carries away any pre-#25 `sessions_fts_*` triggers with it — so the
   sessions ensure must not live inside the message `else` branch, or a
   legacy-message DB would strand the old internal title-only `sessions_fts`
   with no triggers and no H/P claim.

## Ownership model

A session's `row_id` determines who may touch its FTS document while a rebuild
is pending:

```mermaid
flowchart LR
    subgraph regions["row_id regions during rebuild (H = high water, P = progress)"]
        L["row_id <= P<br/>already backfilled<br/>triggers maintain FTS"]
        G["(P, H]<br/>historical worker owns it<br/>triggers leave it alone<br/>search supplements it"]
        R["row_id > H<br/>live row<br/>inserted after capture<br/>trigger indexes immediately"]
    end
```

The live triggers gate on `row_id > H OR row_id <= P`; when no rebuild is
pending both markers are absent and `COALESCE(..., -1)` makes the gate a
tautology (normal operation). This makes every existing canonical
session-delete path correct without a second manual FTS delete path:

- deleting `<= P` removes the already-indexed document;
- deleting `(P, H]` issues no external-content delete (the document was never
  indexed) and the later range backfill simply finds no canonical row;
- deleting `> H` removes the live-indexed document.

## Crash-safe H/P rebuild

Reuses the accepted message-FTS seam (upstream #76832):

- durable claim is committed **before** an empty external index can look
  complete;
- an internal→external conversion over a DB with **zero sessions** stages no
  claim at all (and clears any stale one): the index is complete by
  construction, and an `H=0/P=0` pair would never enter the rebuild (status
  total ≤ 0) yet would leave `optimize_fts_storage` permanently pending as
  `backfill_incomplete`;
- after the new external table + gated triggers are ensured, a **transition
  catch-up** runs in its own `BEGIN IMMEDIATE` (a `>H OR <=P` insert with a
  docsize anti-join): a concurrent writer can commit a `>H` row in the window
  between the stage transaction's COMMIT and the trigger install, and that
  row is neither trigger-indexed nor in the `(P,H]` gap supplement — the
  catch-up closes the window (idempotent; rows committing after it are
  trigger-indexed). Applies to both the internal→external conversion and the
  fresh-create-over-populated path;
- `H` present / `P` missing never replays from zero over a maybe-partial index:
  either a proven boundary is recovered or the index is reset known-empty
  (`'delete-all'`) first;
- each chunk claims `(P, min(P + _FTS_REBUILD_CHUNK_ROWS, H)]` and publishes
  progress in the same `BEGIN IMMEDIATE` transaction (crash-atomic, and two
  concurrent runners interleave disjoint chunks);
- finish runs a narrow docsize anti-join boundary sweep before clearing the
  markers.

## Search during migration

`_fts_metadata_candidates(raw_query)` is the raw Unicode lane over
`(title, id, display_name)`. While the backfill is pending it supplements only
the bounded gap `(P, H]` from canonical rows and deduplicates by `row_id`, so
migration never silently hides a matching session. It is wired into the
production title-lineage resolution path (`_fts_numbered_variants` delegates to
it for non-CJK titles), and `list_sessions_rich(search_query=...)` now also
matches the raw `display_name` dimension. The existing normalized / infix
`%LIKE%` fallback is preserved until #30 deliberately replaces it.

Gap semantics approximate the index conservatively as a **superset of the FTS
predicate**: the supplement folds both sides with `_fts_unicode61_fold` (NFKD
decompose + drop combining marks + casefold) instead of SQLite's ASCII-only
`LOWER()`, and matches on ANY positive term extracted from the query
(`_fts_query_positive_terms` — multi-token implicit AND, `OR`, quoted phrases,
prefix `*`), so `MATCH 'ecole'` finds `École` in the gap and `MATCH 'Alpha
Project'` finds a gap row titled `Alpha middle Project` exactly as the indexed
lane would. Terms split on an ASCII-only separator boundary (unicode61 always
treats ASCII non-alphanumerics — whitespace, punctuation, notably `_` — as
separators); every non-ASCII character is kept inside terms, because Python's
`unicodedata` cannot mirror SQLite's unicode61 (Unicode 6.1) — e.g. U+1018C
is a token char in unicode61 but category `So` in Python, so excluding by
category would risk a miss. Non-ASCII runs also emit each ASCII sub-run and
each non-ASCII codepoint, so a query unicode61 tokenizes more finely cannot
miss either. The sanitizer-quoted `[._-]` punctuation is a separator
too: `foo_bar` / `foo-bar` / `foo.bar` all yield the terms (`foo`, `bar`)
exactly as the index tokenizes a `foo bar` document — a term that kept `_` as
a literal would MISS a session the FTS lane finds. Boolean operator words
(`and`/`or`/`not`/`near`) are deliberately kept as terms too: a quoted
`"AND"` is a literal FTS phrase (the sanitizer protects balanced quotes), so
stripping them would empty the terms for that query and hide a matched row —
keeping them can only over-match, never miss. Over-matching is accepted
(the backfilled index restores exact semantics); a MISS is not. The fold is deliberately a conservative Unicode
approximation, NOT exact unicode61 parity (the real tokenizer folds per
Unicode 6.1, strips Latin-script diacritics, and preserves single-codepoint
multi-diacritic characters such as `ộ`), so the gap lane can produce a
temporary false positive that disappears after backfill — accepted, because
#25's core risk is migration MISSING a result. The merged FTS+gap result is
sorted globally by `started_at DESC` (never lane-then-gap), which is what
`resolve_session_by_title` relies on to resume the latest continuation. The
helper returns `(fts_ok, candidates)`: when the `sessions_fts` MATCH lane
itself fails, `fts_ok` is False so title resolution falls back to the LIKE
lane instead of trusting a partial result.

## Files

- `hermes_state_common.py` — `SESSIONS_FTS_SQL` (external DDL + gated narrow
  triggers), `SESSION_TABLE_REBUILD_SQL` / `SESSION_INDEX_SQL_STATEMENTS`
  (the unique title index is deliberately excluded: the existing post-migration
  duplicate-title repair owns it).
- `hermes_state.py` — `_migrate_sessions_row_id`, `_ensure_sessions_fts_schema`,
  `_db_has_internal_content_sessions_fts`, `_backfill_sessions_fts_cjk`,
  `_session_fts_rebuild_gap`, `_fts_unicode61_fold`, `_fts_query_positive_terms`,
  `_fts_metadata_candidates` (returns `(fts_ok, candidates)` sorted globally;
  the gap supplement is a term-superset of the FTS predicate), updated
  `_fts_numbered_variants`.
- `hermes_state_schema.py` — `_init_schema` wiring: the sessions-FTS block is
  placed OUTSIDE the message legacy/`else` branch so it runs for every message
  layout (the `fts_storage_version` stamp stays message-scoped; unified
  storage-version settlement is #27).
- `hermes_state_search.py` — shared `_FTS_MESSAGE_SPEC` / `_FTS_SESSION_SPEC`,
  parameterized `fts_rebuild_status/step`, `_fts_rebuild_finish`,
  `_seed_fts_rebuild_markers`, `_repair_missing_progress` (the shared crash-safe
  repair), `_repair_optimize_bookkeeping` / `_repair_session_fts_bookkeeping`,
  `_fts_rebuild_pause`, `fts_optimize_available` / `optimize_fts_storage`
  session phase.
- `hermes_cli/session_recovery.py` — session markers treated as generated /
  pending in offline recovery.
- `tests/test_session_metadata_fts.py` — rowid-hole migration (incl. legacy
  duplicate-title upgrade), raw Unicode external-content, H/P ownership
  regions, crash/restart (incl. the partial-index H-without-P orphan
  reset/replay regression), the trigger-install window race (two-connection
  catch-up regressions for both the internal→external and fresh-create paths),
  bounded-gap search (conservative Unicode-fold supplement + its explicit
  non-parity edge, multi-token implicit-AND and OR no-hide regressions,
  sanitizer-quoted `[._-]` punctuation + quoted-boolean + PUA/U+1018C
  no-hide regressions, cross-lane ordering), finish, delete probes that read
  the index directly plus an ordinary internal integrity-check mid-migration
  and a `rank=1` consistency check on completed indexes, two real concurrent
  runners (thread + barrier), shared throttle, and a legacy-message ×
  old-session-FTS cross-layout upgrade path (one optimize settles both) plus
  the empty-legacy-DB no-zombie-marker path.
