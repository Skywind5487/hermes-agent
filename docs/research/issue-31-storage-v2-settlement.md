# #31 implementation — storage-v2 settlement state machine

Status: **implemented (2026-08-12)**  
Code base: **`9d140c8594c67ed37729b190fd3733508d0d770c`** (post-#27 `dev` merge, per the #36 research handoff)  
Branch: `feat/31-storage-v2-settlement`  
Research artifact: `docs/research/issue-36-storage-v2-settlement.md` (PR #77)  
Target: **#31 — Session metadata FTS: settle storage v2 after all required indexes complete**

> This is the implementation record for #36's handoff. #31 owns **only the
> storage-layout settlement / completion state machine**: when the DB is
> allowed to claim `fts_storage_version = 2`, and how that claim recovers
> across interruption/reopen. It is NOT a rebuild engine, scheduler,
> registry, or migration framework; #25/#26/#27/#30 keep owning every
> per-lane worker/repair/stale-transition path, and search routing/ranking
> stays with #14/#28.

## What changed

`FTS_STORAGE_VERSION` moved `1 -> 2` in the same stack as one **shared
SELECT-only, durable/schema-aware completion evaluator**,
`SessionDB._fts_storage_v2_blocker(conn)`:

- returns `None` when the DB is acceptance-complete for v2, or
- returns `(reason, actionable_here)` for the first blocker found.

The SAME evaluator now drives every place that can claim or advertise
completion, so the four pre-#31 completion decisions can never diverge again:

| Consumer | Before #31 | After #31 |
|---|---|---|
| writable startup | message-only subset, **before** session ensure | runs **after** every message/session ensure path, same evaluator |
| `fts_optimize_available()` | independent inventory + operability-gated `_fts_lane_pending()` | evaluator result, `actionable_here` only |
| foreground pre-VACUUM refusal | hard-coded partial session list | same evaluator |
| final transactional `_settle()` | message high-water/trash/message-empty only | same evaluator, re-checked inside the write transaction |

### Refusal semantics (durable, not serving-boolean)

- Any required message/session **H, P, or stale breadcrumb** blocks v2 — even
  on a host that cannot operate the lane. Missing capability is never
  evidence of completion.
- `unknown_same_name` trigram (incl. the historical fork `tokenize='simple'`
  root), foreign trigger occupants, source collisions, and an incomplete
  exact modern trigger set fail closed — v2 is refused and the object is
  left byte-identical (no destructive legacy-simple convergence resurrected).
- Optional capability rule preserved: *optional + never established + no
  durable work* = valid degraded completion; *optional + H/P/stale/quarantine
  or incomplete existing target* = unfinished.

### Claim withdrawal (crash-safety)

A stale v2 claim is withdrawn wherever the evaluator reports a blocker —
startup reopen, the pre-VACUUM refusal, and the final transactional re-check
— so no completion is advertised while work remains (e.g. a DB settled at v2
that later stages a new optional CJK/trigram index).

### Reuse, not reimplementation

- `_FTS_REBUILD_LANES` is the marker/stale membership source for the
  per-lane H/P/stale loop (one tiny reason-label map added).
- `_FTS_INDEXES` / #30 ownership classifiers / #76832 claim-before-empty +
  transactional re-check seams are all consumed unchanged.
- `_fts_lane_pending()` was removed — its only consumer was
  `fts_optimize_available()`, and its combined operability gate is wrong for
  settlement (the evaluator splits "blocked" from "actionable here").

## Files

- `hermes_state_common.py` — `FTS_STORAGE_VERSION = 2`.
- `hermes_state_search.py` — `_fts_storage_v2_blocker`, lane helpers
  (`_fts_lane_durable_keys` / `_fts_meta_has_any` / `_fts_lane_actionable`),
  `_fts_session_trigram_settlement_blocker`, and the three consumers
  (`fts_optimize_available`, pre-VACUUM refusal, `_settle`).
- `hermes_state_schema.py` — startup stamp moved after all FTS/session
  ensure paths; withdraws a stale claim when blocked.
- `tests/test_fts_storage_v2_settlement.py` — refusal matrix, startup +
  interruption/reopen, six-index acceptance matrix (24 tests).

## Validation

```bash
uv run pytest -q tests/test_fts_storage_v2_settlement.py \
  tests/test_hermes_state.py tests/test_session_metadata_fts.py \
  tests/test_session_metadata_cjk_fts.py tests/test_session_metadata_trigram_fts.py \
  tests/test_fts_cjk_bigram.py tests/test_fts_lifecycle_registry.py \
  tests/test_session_db_read_path_split.py tests/state/test_fts_runtime_rebuild.py \
  tests/test_state_db_malformed_repair.py tests/test_fts_update_of_narrowing.py

uvx ruff check hermes_state_common.py hermes_state_schema.py \
  hermes_state_search.py tests/test_fts_storage_v2_settlement.py
```

Focused suite: 375 passed / 43 skipped (CJK-tokenizer capability skips) on
this host. ruff clean.

## Boundary / non-goals

- No per-index search-routing changes (#14/#28).
- No ordinary optimize/merge/health/repair membership changes (#27).
- No message FTS storage-semantics changes (v23 path).
- `schema_version` stays independent of storage-v2 settlement (decoupled).
- `unknown_same_name` trigram objects are never deleted or demoted here.
