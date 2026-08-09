# #30 implementation — normalized external-content session trigram FTS

Status: **implemented (2026-08-09)**  
Code base: **`e94f2630a50d7585f78cfc06365753c033113cb9`** (#25 / PR #59 merge, per the #34 handoff)  
Branch: `fts/session-trigram-external-content`

## What was built

A first-class modern `sessions_fts_trigram` (FTS5 `tokenize='trigram'`
external-content) for session-metadata search, keyed by stable
`sessions.row_id`, reading through a derived VIEW that projects compact
title, RAW id, and compact display_name — with its own resumable H/P rebuild
lane and tokenizer-independent convergence of the legacy same-name
`tokenize='simple'` object.

```text
sessions (canonical)
  row_id / raw title / raw id / raw display_name
            |
            v
sessions_fts_trigram_src VIEW
  compact(title) / raw(id) / compact(display_name)
            |
            v
sessions_fts_trigram FTS5
  tokenize='trigram'  content='sessions_fts_trigram_src'  content_rowid='row_id'
```

No persistent normalized canonical columns were added.

## Canonical compact policy

`SESSION_METADATA_COMPACT_SEPARATORS = ("-", "_", ".", " ")` in
`hermes_state_common.py` is the single source of truth. Both the Python
query compacting (`compact_session_metadata_text`) and the SQL expression
embedded in the VIEW (`_session_metadata_compact_sql`, nested `REPLACE`)
derive from it — separator deletion only, never broadened to the old Python
`re.sub(r"[\W_]+", "", ...)`. Case-insensitivity comes from the trigram
tokenizer's default, not a second Python-vs-SQL lower policy. `id` stays
raw (the #16 contract: punctuation-bearing interior id substrings survive).

## Files / seams

- `hermes_state_common.py` — policy constant, both compact helpers,
  `SESSIONS_FTS_TRIGRAM_SQL` (VIEW + vtable + 4 gated triggers),
  `_SESSIONS_FTS_TRIGRAM_TRIGGERS`.
- `hermes_state.py` — `_classify_sessions_fts_trigram` (schema identity:
  absent / legacy_simple / modern_trigram / unknown_same_name),
  `_ensure_sessions_trigram_fts_schema`, `_fts_session_trigram_schema_transition`
  (crash-atomic VIEW/table/trigger install + trigger-owned catch-up),
  `_demote_legacy_sessions_trigram_fts` (tokenizer-independent demotion),
  `_session_trigram_rebuild_gap`, `_fts_session_trigram_candidates` (the
  low-level #14 lane), `_SESSIONS_FTS_TRIGRAM_*` statement constants.
- `hermes_state_search.py` — `_FTS_SESSION_TRIGRAM_SPEC` (its OWN marker
  pair), `fts_session_trigram_rebuild_status/step` wrappers reusing the #25
  shared chunk engine, `_seed_session_trigram_fts_rebuild_markers`,
  `_repair_session_trigram_fts_bookkeeping`, `fts_optimize_available` +
  `optimize_fts_storage` integration.
- `hermes_state_schema.py` — startup `_ensure_sessions_trigram_fts_schema`
  call in `_init_schema`, independent of the Unicode lane.
- `tests/test_session_metadata_trigram_fts.py` — new suite.

## Independent H/P decision (recorded + tested)

Trigram gets its own `fts_session_trigram_rebuild_high_water` /
`fts_session_trigram_rebuild_progress`. `P` means target-specific processed
completeness, and Unicode vs trigram can each be created/demoted/repaired
independently — sharing `P` would let one target's completion falsely assert
the other's. `test_unicode_complete_while_trigram_pending` pins the invariant.

## Live maintenance

The delete halves are BEFORE triggers reading the still-visible old
projected row from the VIEW (after a canonical DELETE/UPDATE the old VIEW
representation is gone). Ownership predicate `row_id <= P OR row_id > H`
keeps the `(P, H]` historical gap worker-owned. UPDATE is narrow
(`OF title, id, display_name` + value-change guard) so heartbeat/accounting
writes never rewrite the index. INSERT/DELETE/update + unrelated-update
no-rewrite + same-value no-rewrite + gap no-double-write are all tested.

## Legacy same-name `simple` convergence

Classified by schema identity (`sqlite_master.sql`), never by name alone:
recognized historical shape = FTS5 + `tokenize='simple'` + title-only
INTERNAL content. The demotion never SELECTs/DROPs the vtable (a runtime
without `simple` rejects even `DROP TABLE`) — it drops the known legacy
triggers, removes only the recognized root vtable declaration via
`writable_schema`, renames the orphaned shadows into the shared
`fts_v22_trash_` namespace, and seeds the durable trigram H/P — one
`BEGIN IMMEDIATE`. Unknown same-name shapes fail closed (never deleted).

Crash matrix (mirrors #34): before demotion commit → legacy reruns; after
demotion/claim before modern create → re-ensure preserves P; during
schema+catch-up → rollback + rerun; during backfill → resume own P; H
without P → `_repair_missing_progress` resets only trigram; empty modern +
populated source + no claim → orphan repair seeds full claim; completed
reopen → no marker recreation.

## Ownership boundaries (kept)

- #30 owns the same-name legacy convergence only; global `simple`
  retirement stays #19 (`load_simple_extension` untouched).
- The unified six-index lifecycle stays #27; no second scheduler/registry
  was built.
- `fts_storage_version = 2` is NOT stamped (that's #31).
- `_fts_session_trigram_candidates` is the low-level lane for #14; the
  picker/listing routing, 0-result LIKE fallback, and lineage projection
  were not moved into #30. Trigram MATCH cannot match <3-Unicode-char
  substrings — #14's bounded-LIKE fallback remains necessary.

## Validation

- `tests/test_session_metadata_trigram_fts.py` — 33 passed (schema identity,
  compact/raw search representation, narrow live maintenance, independent
  H/P + restart/orphan/boundary, legacy convergence, e2e).
- `tests/test_session_metadata_fts.py` — 48 passed (no #25 regression).
- `tests/test_hermes_state.py` — 178 passed; `test_state_db_malformed_repair.py`
  — 9 passed; `tests/hermes_cli/test_session_listing.py` — 6 passed.
- `tests/test_optional_cjk_tokenizer_fallback.py` — 2 failures, both
  pre-existing at the pinned base `e94f2630` (verified in a clean worktree);
  not introduced by #30.
- `ruff check` on the five touched files — clean.

## Review-fix round (2026-08-09)

After the two-axis code review (posted on issue #30), implemented the
relevant findings:

1. **No-trigram host no longer leaves a stuck claim (Spec).** A host whose
   SQLite build lacks the trigram tokenizer previously seeded the durable
   H/P claim and then failed the crash-atomic transition — leaving
   `fts_optimize_available()` permanently True and `optimize_fts_storage()`
   permanently `backfill_incomplete`. `_ensure_sessions_trigram_fts_schema`
   now clears the fresh claim when the fresh-create transition fails
   (`_clear_session_trigram_rebuild_claim`); a later capable reopen re-seeds
   and heals. Pinned by `test_trigram_tokenizer_missing_clears_fresh_claim`
   (criterion 10's reverse invariant + criterion 13 tokenizer-absence
   coverage).
2. **De-duplicated the shared seams (Standards).** The crash-atomic schema
   transition is now one spec-parameterized `_session_fts_schema_transition`
   shared by the Unicode (#25) and trigram (#30) lanes (the per-lane
   `_fts_session_schema_transition` / `_fts_session_trigram_schema_transition`
   are thin wrappers). `_repair_session_fts_bookkeeping(spec)` and
   `_seed_session_metadata_fts_rebuild_markers(conn, spec)` are likewise
   parameterized, with the trigram lane delegating to them.
3. **Removed the unused `_SESSIONS_FTS_TRIGRAM_TRIGGERS` constant (Standards
   dead code).**

Deferred as not-related (per the reviewer, #14 territory): the <3-char
needle gap-supplement vs indexed-lane divergence — trigram MATCH cannot match
substrings under 3 Unicode characters, and #14's bounded-LIKE fallback is the
owner.
