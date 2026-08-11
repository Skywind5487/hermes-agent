# #27 implementation — unified six-index FTS lifecycle membership

Status: **implemented (2026-08-11)**  
Code base: **`919f4469e832bc2b38bba0ea5af26b842bf91acd`** (post-#30 `dev` snapshot, per the #35 research handoff)  
Branch: `feat/27-unified-fts-lifecycle`  
Research artifact: `docs/research/issue-35-unified-fts-lifecycle.md` (PR #75)

> This is the implementation record for #35's handoff. #27 owns **unified
> lifecycle membership only** for the six modern FTS indexes — it is NOT a
> search-routing or migration-state framework, and storage-v2 settlement
> stays with #31 (no `FTS_STORAGE_VERSION` change here).

> **Review round 1 (2026-08-11, ponytail + code-review, issue comment
> `5253715082`, fixed in `0400c66f0`).** All over-engineering/standards
> findings were within #27 scope and implemented: removed the never-read
> `"name"` key from `_FTS_REBUILD_LANES`; merged `_drop_fts_triggers` /
> `_drop_message_fts_triggers` into `_drop_fts_triggers(cursor, names=None)`
> (demote passes the message subset); parameterized the message/session CJK
> UPDATE-narrowing + quarantine helpers by `(trigger_name, stale_key)`
> (caller clears its own availability flag); deleted the now-dead
> `_FTS_TRIGGERS` membership tuple (+ its back-compat re-export) per research
> §4.1 Q2. Net −35 lines. Kept (spec-required, not a finding to fix): the
> spec `"descriptor"` / `"trigram_descriptor"` keys — they are the explicit
> "specs reference authoritative descriptors" seam from the research and the
> drift-prevention test anchor. Validation after the round: 149 + 187 focused
> tests green, ruff clean.

## The six authoritative members

1. `messages_fts`
2. `messages_fts_trigram`
3. `messages_fts_cjk`
4. `sessions_fts`
5. `sessions_fts_cjk`
6. `sessions_fts_trigram`

## Descriptor / registry

A single **data-only module-level descriptor registry** lives in
`hermes_state_common.py`:

```python
@dataclass(frozen=True)
class FtsIndexDescriptor:
    table: str
    source: str
    row_key: str
    columns: tuple[str, ...]
    trigger_names: tuple[str, ...]
    capability: Literal["fts5", "trigram", "cjk"]
    derived_objects: tuple[tuple[str, str], ...] = ()   # owned source VIEWs

FTS_INDEXES: tuple[FtsIndexDescriptor, ...] = (... six members ...)
```

- Module-level so offline repair and the SessionDB mixins consume the same
  membership source without importing `hermes_state` (cycle-free).
- `derived_objects` names owned source VIEWs (`messages_fts_trigram_src`,
  `messages_fts_cjk_src`, `sessions_fts_trigram_src`) needed by destructive
  repair.
- Dynamic concerns stay in their existing owners: H/P markers + stale
  breadcrumbs (rebuild lanes), worker-vs-serving state, #30 exact same-name
  ownership, search routing, storage settlement.
- **Registry membership is NOT authorization to mutate `sessions_fts_trigram`**
  — #30's ownership classifier remains the gate; `unknown_same_name` stays
  fail-closed everywhere (ordinary maintenance, repair, read-only discovery).

## Rebuild specs reference the registry

`_FTS_MESSAGE_SPEC` / `_FTS_SESSION_SPEC` / `_FTS_SESSION_TRIGRAM_SPEC` /
`_FTS_SESSION_CJK_SPEC` derive their static identity (`descriptor`,
`fts_table`, `source_table`, `row_key`, `fts_columns`, `source_columns`,
`trigram_descriptor`) from `FTS_INDEXES` instead of repeating it, so
membership can never drift from the lifecycle consumers. Lane-specific H/P
keys, availability callbacks, reset targets, and finish hooks stay in the
specs. A new generic `_FTS_MESSAGE_CJK_SPEC` folds the bespoke message-CJK
rebuild onto the shared status/step/finish engine — its own H/P pair and
stale breadcrumb (`FTS_CJK_STALE_KEY`) are preserved, and the backfill now
reads through the `messages_fts_cjk_src` VIEW (equivalent to the old
`messages` + `role <> 'tool'` filter).

## Commit plan → what landed

1. **`00e680a8c` — registry + ordinary maintenance.** `FtsIndexDescriptor` /
   `FTS_INDEXES` / `_fts_descriptor()`; specs reference descriptors; generic
   message-CJK spec + delegating `fts_cjk_rebuild_*` wrappers; removed
   `SessionDB._FTS_TABLES`; new `_fts_maintenance_tables()` applicability
   helper drives `optimize_fts` / `rebuild_fts` / `_merge_fts_incrementally`
   across all six members (required Unicode indexes always, tokenizer-gated
   members only when operable/owned). VACUUM reaches session indexes through
   the existing `optimize_fts()` call — no session-specific VACUUM hook.
2. **`e64af5e0f` — health + trigger inventory/convergence.** `_db_opens_cleanly`
   read probe iterates `FTS_INDEXES` (detects corrupt session Unicode/CJK/
   trigram indexes); the rollback-only write probe now inserts non-null
   `title`/`display_name` so session INSERT triggers execute their real
   projection; `_drop_fts_triggers` derives all owned modern triggers from
   the registry (message-scoped `_drop_message_fts_triggers` added for the
   v22→v23 demote path); `_fts_trigger_count` derives from message
   descriptors; `_migrate_broad_fts_update_triggers` converges owned
   `sessions_fts_update` / `sessions_fts_cjk_update` from broad to canonical
   narrow `AFTER UPDATE OF title, id, display_name` (trigram update triggers
   remain under #30 exact identity).
3. **`474b88fe7` — repair + degraded runtime.** Module-level
   `_owned_fts_object_names` (six-index tables + shadows + triggers + source
   VIEWs, with #30 trigram fail-closed) and `_drop_owned_fts_derived_schema`;
   `repair_state_db_schema` strategy 0 (in-place `rebuild`) iterates
   `FTS_INDEXES` (session trigram rebuilds through its derived compact VIEW —
   no compact SQL duplicated); strategy 2 (destructive) removes owned derived
   schema for all six members while preserving canonical `sessions`/`messages`
   and never touching an unknown same-name trigram object. Runtime recovery
   continues through the now-registry-driven `rebuild_fts()` (covers session
   indexes via the existing one-shot seam).
4. **`f9ade6baf` — read-only + operational status.** Read-only init
   SELECT-only discovers `sessions_fts` and `sessions_fts_trigram`
   (ownership-classified, tokenizer-probed, stale-checked) in addition to the
   existing message/session-CJK probes — no DDL, no mutation; worker-vs-serving
   preserved. A shared rebuild-lane surface `_FTS_REBUILD_LANES` +
   `_fts_lane_pending` / `_fts_first_pending_lane_status` /
   `_fts_run_pending_lane_steps` drives `optimize_fts_storage`'s progress
   emitter + foreground phase driver and `fts_optimize_available`'s
   marker/stale checks (removes the hard-coded per-lane ladders).
5. **`e64ad007b` — six-index regression matrix.** `tests/test_fts_lifecycle_registry.py`
   pins: registry shape, spec-descriptor derivation, maintenance coverage,
   explicit-rebuild through the compact VIEW, VACUUM row-id preservation,
   health read/write probes, trigger inventory/convergence, offline +
   destructive repair with canonical-row preservation, read-only discovery,
   shared-lane sequencing, and runtime recovery. Message-only lifecycle
   assertions in `test_hermes_state.py::TestOptimizeFts` were updated to the
   six-index expectation.

## Contracts preserved (not redesigned)

- #25 raw-`(title, id, display_name)` Unicode document, named `row_id`, H/P
  three-region ownership; #26 session-CJK worker-vs-serving distinction +
  stale semantics; #30 exact same-name root/source/trigger identity,
  quarantine/recovery, derived compact VIEW representation.
- The shared chunk/finish engine, #76832 crash-safe claim ordering, #77629
  "optional capability gates finish exactly as step".
- No new core-tool / framework abstraction: the registry is data-only;
  search routing/ranking/fallback stays in #14/#28.

## Known limitation (documented, not hidden)

A session-metadata **UPDATE** that fires `sessions_fts_update`'s `'delete'`
half against a corrupt `sessions_fts` index hits an FTS5 in-transaction
connection-state quirk: after the failed delete + rollback, an in-place
`rebuild` fails on the SAME connection (verified in isolation; the message
UPDATE path and the session INSERT path both recover fine). That path
degrades to the registry-driven offline repair / startup auto-heal (fresh
connection), which now covers session indexes — a strict improvement over
base, where session FTS had no runtime recovery at all.

## Validation

Focused lifecycle matrix (research §9) + the new registry suite:

```bash
pytest -q \
  tests/test_hermes_state.py \
  tests/test_fts_update_of_narrowing.py \
  tests/test_fts_cjk_bigram.py \
  tests/test_session_metadata_fts.py \
  tests/test_session_metadata_cjk_fts.py \
  tests/test_session_metadata_trigram_fts.py \
  tests/test_state_db_malformed_repair.py \
  tests/test_session_db_read_path_split.py \
  tests/test_fts_lifecycle_registry.py
```

Result: **339 passed, 43 skipped** (CJK capability-skipped on this host; not
capability evidence per the research). Plus broader regression sweep
(`tests/state/test_fts_runtime_rebuild.py`, `test_session_search_sql_winners.py`,
`test_session_listing.py`, `tools/test_session_search.py`,
`tools/test_tool_search*.py`, `gateway/test_session.py`,
`gateway/test_api_server_toolset.py`, `state/test_compression_lineage_guard.py`,
`state/test_no_more_rows_retry.py`) — all green. `ruff check` clean on every
touched production/test file.
