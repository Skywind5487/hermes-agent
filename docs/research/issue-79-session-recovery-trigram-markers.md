# #79 session-recovery: session-trigram derived markers

Status: **implemented (2026-08-13)**  
Code base: **`276d497764feb7d4a71f1424ed44b8958da63b16`** (post-#31 `dev` merge, PR #78)  
Branch: `feat/79-session-recovery-trigram-markers`  
Target: **#79 ??Final #12 acceptance / closure audit**

> This closes the last known derived-FTS recovery gap before #12 acceptance:
> the session-trigram lane was the only session-metadata lane missing from
> the offline recovery `state_meta` inventory, so a source DB carrying
> trigram transition state could leak that derived bookkeeping into the
> recovered database as ordinary `state_meta`.

## Background

Offline session recovery (`hermes_cli/session_recovery.py`) is non-destructive:
canonical rows are copied into a freshly initialized current-schema database,
and **derived FTS state is rebuilt by the fresh destination, never copied**.
That invariant is enforced by two inventories:

- `_GENERATED_META_KEYS` ??the `state_meta` keys excluded during the copy
  (`NOT IN (...)` filter) and deleted by `_finalize_derived_metadata()` before
  the storage-version stamp;
- the `pending_fts_keys` verifier ??the hard-coded tuple checked against the
  recovered DB's `state_meta` so a leftover derived marker fails verification.

At `dev@276d497` both inventories covered the message lane
(`fts_rebuild_*`, `fts_cjk_*`), the session Unicode lane
(`fts_session_rebuild_*`, #25), and the session CJK lane
(`fts_session_cjk_*`, #26) ??but not the session trigram lane (`#30`):

```
fts_session_trigram_rebuild_high_water
fts_session_trigram_rebuild_progress
fts_session_trigram_stale
```

This was recorded as a **pre-existing #30 recovery gap** in the #31 final
merge note.

## What changed

`hermes_cli/session_recovery.py`:

- `_GENERATED_META_KEYS` gained the three `fts_session_trigram_*` keys. This
  makes the copy filter exclude them (they no longer pass the `NOT IN`
  filter) **and** makes `_finalize_derived_metadata()` delete any of them the
  fresh `SessionDB` open happened to re-seed before the final stamp.
- The `pending_fts_keys` verifier tuple gained the same three keys, so a
  recovered DB that still carries trigram transition state now fails
  verification with "derived FTS transition markers remain in the recovered
  database".

The fresh destination is opened as a real `SessionDB` (which runs the full
message + session FTS ensure on a capable host), so on a trigram-capable host
the destination *will* seed its own `fts_session_trigram_rebuild_high_water` /
`progress` during open; the fix makes `_finalize_derived_metadata()` strip
those seeds along with every other generated key, leaving only the
`fts_storage_version` stamp.

## Verification

New tests in `tests/hermes_cli/test_session_recovery.py` (TDD RED ?? GREEN):

- `test_session_trigram_derived_keys_are_generated_meta` ??the three trigram
  keys are members of `_GENERATED_META_KEYS` (mirrors the existing #26 CJK
  membership test).
- `test_recovery_strips_session_trigram_derived_markers` ??behavioral
  regression: a source DB seeded with all three trigram transition markers
  (H=999, P=500, stale=1) is recovered; the recovered DB's `state_meta` must
  not contain any of them, and verification reports no `pending_fts_keys`.
  **Before the fix this failed** with `fts_session_trigram_rebuild_high_water`
  present in the recovered `state_meta`.

Results:

```
tests/hermes_cli/test_session_recovery.py      6 passed, 1 pre-existing env failure
tests/test_fts_storage_v2_settlement.py        (passes)
tests/test_session_metadata_trigram_fts.py     (passes)
tests/test_fts_lifecycle_registry.py           (passes)
ruff check                                     clean
```

The one `test_session_recovery.py` failure is
`test_cli_allow_partial_salvages_rows_across_a_corrupt_leaf`, which fails on
BASE with a subprocess `UnicodeDecodeError: cp950` on this Windows console
(pre-existing environment artifact, documented in the #31 record), unrelated
to this change.

## Out of scope

- Negative-space audit of the full #12 contract against `dev@276d497` (item 2
  of #79).
- Updating the stale #30-ownership statement in #13 (item 3 of #79).
