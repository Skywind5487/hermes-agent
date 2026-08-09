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

Gap semantics match the index: the supplement folds both sides with
`_fts_unicode61_fold` (Unicode case-fold + diacritic removal, mirroring
unicode61) instead of SQLite's ASCII-only `LOWER()`, so `MATCH 'ecole'` finds
`École` in the gap exactly as it does in the index. The merged FTS+gap result
is sorted globally by `started_at DESC` (never lane-then-gap), which is what
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
  `_session_fts_rebuild_gap`, `_fts_unicode61_fold`, `_fts_metadata_candidates`
  (returns `(fts_ok, candidates)` sorted globally), updated
  `_fts_numbered_variants`.
- `hermes_state_schema.py` — `_init_schema` wiring (the `fts_storage_version`
  stamp stays message-scoped; unified storage-version settlement is #27).
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
  regions, crash/restart, bounded-gap search (incl. Unicode-fold parity and
  cross-lane ordering), finish, delete probes that read the index directly and
  a `rank=1` consistency check on completed indexes, two real concurrent
  runners (thread + barrier), shared throttle.
