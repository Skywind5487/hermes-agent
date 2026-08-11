# Research: #33 — CJK session-metadata FTS lifecycle for #26

**Status:** implementation-ready research handoff; no #26 source implementation in this commit.  
**Pinned base:** `e94f2630a50d7585f78cfc06365753c033113cb9` (`dev`, immediately after accepted #25 / PR #59).  
**Target:** #26 — make `sessions_fts_cjk` an optional CJK specialization of #25's session-metadata FTS lifecycle.

## 1. Executive conclusion

#26 should **not** invent a CJK scheduler or clone the existing message-CJK rebuild functions. The accepted #25 implementation already supplies the reusable session rebuild state machine: stable `sessions.row_id`, external-content metadata documents, generic H/P rebuild specs, chunk/finish/boundary logic, crash repair, and shared pacing.

The current CJK session surface is the pre-#25 shape: **internal-content, title-only, one-shot backfill, one serving boolean**. #26 should replace only that variant-specific surface with:

- external-content raw `(title, id, display_name)` keyed by the same `sessions.row_id`;
- independent durable CJK session markers;
- independent stale/quarantine state;
- a **worker-operable** capability distinct from **search-serving** availability;
- the same `(P,H]` ownership invariant and generic chunk/finish/pacing engine as Unicode;
- safe tokenizer-unavailable degradation that never clears pending work merely because the current host cannot operate it.

No ticket split is needed. This is narrow enough for four commit-sized steps below.

## 2. Authority / prior-art classification

| Item | Classification at pinned base | Decision |
|---|---|---|
| Fork #25 / PR #59 | accepted and **the pinned base itself** | Reuse its stable `row_id`, generic session rebuild spec/worker, repair and pacing. No reimplementation. |
| NousResearch/hermes-agent#77629 (`2f32092b…`) | merged upstream and **in current fork ancestry** | Reuse the accepted invariant: optional work in **finish** must be guarded by the same operability capability as optional work in **step**. No cherry-pick. |
| Upstream read split commits `6623ee9b…` + `f228e145…` | accepted behavior and **in current fork ancestry** | Reuse `_read_ctx()` / per-thread `mode=ro` connection seam; do not put CJK MATCH back under the writer lock. No cherry-pick. |
| NousResearch/hermes-agent#65541 | closed/unmerged PR, but its accepted read-path behavior subsequently landed as the commits above | Historical provenance/evidence only; current code is authoritative. |
| NousResearch/hermes-agent#65544 | closed/unmerged CJK-bigram proposal | Structural evidence only: optional tokenizer, 2-char CJK, self-heal by disabling unsafe triggers, and “partial index must not serve” are useful precedent. Its v2 table/scripts/config shape is **not** a transplant target. |
| Existing `messages_fts_cjk` in pinned base | merged/current code | Structural precedent for split table/trigger DDL and stale quarantine. Do **not** reuse its message marker names for session metadata. |
| Existing `sessions_fts_cjk` in pinned base | current fork code but pre-#26 legacy shape | Migration input / test fixture only; do not preserve its title-only internal-content architecture. |

There is no merged-but-absent exact implementation of #26 to cherry-pick. The useful upstream behavior is already in ancestry or is only structural evidence.

Primary upstream references:

- #77629: https://github.com/NousResearch/hermes-agent/pull/77629
- #65541: https://github.com/NousResearch/hermes-agent/pull/65541
- #65544: https://github.com/NousResearch/hermes-agent/pull/65544

## 3. Pinned source map

All line references below are against `e94f2630a50d7585f78cfc06365753c033113cb9`, never moving `dev`.

### 3.1 Canonical Unicode session metadata architecture — reuse, do not fork

**`hermes_state_common.py:L620-L790`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_common.py#L620-L790

- `SESSIONS_FTS_SQL`: raw `(title,id,display_name)`, `content='sessions'`, `content_rowid='row_id'`, `unicode61`.
- live INSERT/DELETE and narrow `AFTER UPDATE OF title,id,display_name` triggers.
- trigger ownership is `row_id > H OR row_id <= P` using `fts_session_rebuild_high_water` / `fts_session_rebuild_progress`.
- immediately afterward, `SESSIONS_FTS_CJK_SQL` shows the problem #26 owns: title-only, internal-content, hidden-rowid-oriented, broad update, no dedicated H/P lifecycle.

**Implementation consequence:** create the CJK document with the same three canonical columns and same named row identity. Prefer the message-CJK split-DDL discipline (`TABLE_SQL` + `TRIGGER_SQL`) so a stale optional index can exist while unsafe triggers remain absent.

### 3.2 Stable session document identity

**`hermes_state_schema.py:L300-L520`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_schema.py#L300-L520

- `_migrate_sessions_row_id()` is #25's authoritative migration.
- legacy hidden `rowid` is copied exactly to named `row_id INTEGER PRIMARY KEY AUTOINCREMENT`; deleted-row holes are preserved.
- logical `sessions.id` remains the public text identity.

**Implementation consequence:** #26 must not introduce another identity column or remap document IDs. `sessions_fts_cjk.rowid == sessions.row_id`.

### 3.3 Current startup seam for session Unicode vs CJK

**`hermes_state_schema.py:L1135-L1285`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_schema.py#L1135-L1285

- `_ensure_sessions_fts_schema(cursor)` establishes the #25 Unicode surface and `self._sessions_fts_available`.
- the adjacent CJK block explicitly says its lifecycle is owned by #26.
- today it gates CJK creation on `self._fts_cjk_loaded`, calls generic `_ensure_fts_schema(...SESSIONS_FTS_CJK_SQL)`, stores only `self._sessions_cjk_available`, then runs `_backfill_sessions_fts_cjk(cursor)` synchronously/one-shot.

**Implementation consequence:** this is the seam to replace. Remove the one-shot backfill path. Do not use search-serving availability as the worker gate.

### 3.4 Optional CJK tokenizer + message-CJK quarantine precedent

**`hermes_state.py:L1510-L1685`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state.py#L1510-L1685

- `FTS_CJK_TABLE_SQL` is external-content.
- `FTS_CJK_TRIGGER_SQL` has a dedicated message-CJK H/P pair and narrow update trigger.
- `load_fts5_cjk_extension(conn)` is best-effort and returns false for missing/disabled/unloadable tokenizer rather than making the whole store unavailable.
- the bigram tokenizer's useful indexed lower bound is two CJK characters; a lone CJK character still needs fallback.

**`hermes_state.py:L2700-L2820`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state.py#L2700-L2820

- `_ensure_fts_cjk_schema()` demonstrates the safe degraded-host rule for an optional tokenizer.
- tokenizer missing + live optional triggers: persist stale breadcrumb **before** dropping triggers, so canonical writes stay usable.
- stale optional index is not served and its triggers are not blindly reinstalled, because external-content delete against never-indexed rowids is unsafe.
- capable host can later reset/rebuild from canonical rows.

**Implementation consequence:** give session-CJK its own trigger tuple and stale key, e.g. `fts_session_cjk_stale`. Do not reuse message `FTS_CJK_STALE_KEY`.

### 3.5 Writer and read-connection capability seams

**`hermes_state.py:L1840-L2350`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state.py#L1840-L2350

Two different read situations exist and must not be conflated:

1. Writable `SessionDB` startup loads `cjk_unicode61` on the writer before `_init_schema()` and records `_fts_cjk_loaded`.
2. `_get_read_conn()` opens per-thread `mode=ro` WAL connections and, when the writer loaded CJK, also loads the CJK extension on that read connection before MATCH.
3. The dedicated `SessionDB(read_only=True)` attach branch probes base/trigram FTS but currently returns without loading/probing session CJK capability.

**Implementation consequence:** preserve `_read_ctx()` as the read execution seam. #26 must explicitly cover the dedicated read-only attach branch so a read-only consumer neither falsely claims CJK service nor unnecessarily loses it when the tokenizer is available. A writer-level `_fts_cjk_loaded=True` is not by itself proof that every individual read connection can MATCH; a failed connection-local tokenizer load/probe must degrade to fallback, not crash.

### 3.6 Generic #25 rebuild machinery — the scheduler #26 should reuse

**`hermes_state_search.py:L20-L620`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_search.py#L20-L620

- `_FTS_SESSION_SPEC` describes the Unicode session index with its H/P keys, canonical source, `row_id`, columns, reset tables, and capability callback.
- `_fts_rebuild_pause()` is the shared pacing primitive; policy comes from the shared chunk/duty/min-pause constants.
- `fts_rebuild_status(spec)`, `fts_rebuild_step(spec)`, `_fts_rebuild_finish(spec)` are already generic.
- `_fts_rebuild_finish()` performs the boundary sweep before marker clear.
- `fts_session_rebuild_status/step()` are thin wrappers around the generic spec.
- the later `fts_cjk_rebuild_*` methods are the older **message-CJK-specific** state machine. They are precedent, not the shape to copy for session CJK.

**Implementation consequence:** add a session-CJK spec/wrappers to the generic engine. Do not add a second copy of chunking, duty factor, pause, boundary sweep, or crash-repair policy.

The key capability rule inherited from #77629 is:

> If optional capability gates the chunk step, the same operability capability must gate optional work in finish. Missing capability is never evidence of completion.

For session CJK, the callback used by the worker must represent **worker operability**, not `_sessions_cjk_available` (which is search-serving state).

### 3.7 Crash bookkeeping / optimize driver / shared pacing

**`hermes_state_search.py:L620-L980`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_search.py#L620-L980

- `_repair_missing_progress()` is the shared crash rule: H without P resets a non-empty target before replay, then restores `P=0`.
- `_repair_session_fts_bookkeeping()` is the #25 session Unicode specialization and should be generalized/reused for CJK rather than cloned semantically.
- `fts_optimize_available()` already treats message-CJK pending/stale as offerable only on a tokenizer-capable host; session-CJK should gain the equivalent **independent** durable check.
- `optimize_fts_storage()` repairs bookkeeping before running work and calls the shared pause function between chunks.

**`hermes_state_search.py:L980-L1140`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_search.py#L980-L1140

- message CJK and session Unicode both run as foreground phases using the same `_fts_rebuild_pause()`.
- settlement refuses to stamp while known required work remains.

**Implementation consequence:** insert the session-CJK phase beside session Unicode and message CJK. Do not create CJK-specific pacing numbers. #26 should expose its durable pending/stale state clearly for #27; #26 itself should not redesign/storage-v2 settlement.

### 3.8 Search/fallback interface that #14 will consume later

**`hermes_state.py:L5850-L6210`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state.py#L5850-L6210

- `_fts_metadata_candidates()` is the #25 raw-Unicode metadata candidate helper over all three fields and distinguishes “FTS lane failed” from “valid zero matches”.
- `_fts_numbered_variants()` currently dispatches CJK to `sessions_fts_cjk` but reads the legacy title-only table under `self._lock` and contains a CJK-local supplement tied to the **Unicode** rebuild gap.
- when `_sessions_cjk_available` is false it returns `None`, which means callers use canonical LIKE fallback.

**`hermes_state.py:L6200-L7600`**  
https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state.py#L6200-L7600

- `list_sessions_rich(search_query=...)` is still the broad metadata `%LIKE%` path. Replacing that picker candidate path belongs to #14, not #26.

**Implementation consequence for #26:** establish a narrow CJK metadata-candidate seam with the same result/failure distinction as Unicode and execute it through `_read_ctx()`. It must cover `title`, `id`, `display_name` after completion. Pending/stale/unavailable or one-character CJK should signal fallback rather than advertise a partial index as searchable. #14 can later route ordinary CJK picker queries to that seam without owning lifecycle state.

Because #26 explicitly requires **pending CJK not to serve search**, do not copy #25's Unicode “serve indexed lane + supplement `(P,H]`” policy into CJK. During CJK pending, `(P,H]` is a **worker ownership region**; search stays on a complete fallback lane until CJK finish makes the optional index servable.

### 3.9 Test seams

- `tests/test_session_metadata_fts.py` — authoritative #25 fixtures and lifecycle tests; its module header explicitly scopes CJK to #26. Reuse its legacy-row-id and H/P patterns.
- `tests/test_optional_cjk_tokenizer_fallback.py` — optional-tokenizer degradation seam.
- `tests/test_session_db_read_path_split.py` — WAL/read-only connection behavior; add CJK read-connection capability regression here or in a focused CJK test module.

Pinned URLs:

- https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/tests/test_session_metadata_fts.py
- https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/tests/test_optional_cjk_tokenizer_fallback.py
- https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/tests/test_session_db_read_path_split.py

## 4. Required durable and in-process state

### Durable CJK-session keys

Recommended names, independent from both Unicode-session and message-CJK state:

- `fts_session_cjk_rebuild_high_water`
- `fts_session_cjk_rebuild_progress`
- `fts_session_cjk_stale`

Do not infer CJK completion from `fts_session_rebuild_*`, and do not share `fts_cjk_rebuild_*` (those belong to the message index).

### In-process capability split

Keep the existing writer tokenizer fact:

- `_fts_cjk_loaded`: tokenizer registered/usable on the writer connection.

Add/derive a **session-CJK worker-operable** fact separately from serving state, for example:

- `_sessions_cjk_worker_operable`: this writable process can create/maintain/rebuild the session CJK index now.
- `_sessions_cjk_available`: preserve this name as **search-serving** availability for compatibility with current call sites.

Exact spelling is an implementation choice; the semantic split is not.

A valid pending state is:

```text
worker_operable = true
search_serving  = false
H/P present
```

That state must still allow the worker to advance.

## 5. State-transition model

```text
                         tokenizer unavailable
                  ┌────────────────────────────────┐
                  │                                ▼
ABSENT/NO CAP ────┘                         DEGRADED / STALE
 W=0, S=0                                  W=0, S=0
 Unicode unaffected                        optional triggers absent
 no false completion                       durable work not cleared
                                                  │
                                                  │ capable host
                                                  ▼
                                           RESET + RECLAIM H/P
                                                  │
                                                  ▼
CAPABLE + populated sessions ──seed H=max(row_id), P=0──> PENDING
 W=1                                                W=1, S=0
                                                     │
                         chunk: index (P,next] + P advance atomically
                                                     │
                                                     ▼
                                               PENDING (P grows)
                                                     │
                       restart on capable host ──────┘ (resume same H/P)
                                                     │
                                                     │ P >= H
                                                     ▼
                                         FINISH / boundary sweep
                                         (same W capability gate)
                                                     │
                                                     ▼
                                                 COMPLETE
                                                 W=1, S=1

Fresh empty DB + capable tokenizer:
  create external CJK table + safe live triggers -> COMPLETE immediately.
```

### Trigger ownership while `H/P` exists

```text
row_id <= P     worker already indexed -> live trigger may maintain it
P < row_id <= H historical worker-owned gap -> live trigger must leave it alone
row_id > H      post-capture live row -> live trigger indexes it when operable
```

The CJK trigger predicates should therefore mirror #25 with the **CJK-session** marker names.

### Degraded host while a rebuild is already pending

This is the subtle case to pin in tests:

1. Persist `fts_session_cjk_stale=1` before removing unsafe session-CJK triggers.
2. Do **not** clear H/P and do not call finish.
3. Normal `sessions` writes and Unicode metadata FTS continue.
4. Because trigger removal creates an unknown post-H gap, a later capable host should treat `stale` as stronger than the old partial claim: reset the CJK index to a known-empty surface and **reseed a fresh CJK H/P claim from current `MAX(row_id)`**, then rebuild.
5. Only successful boundary finish clears pending/stale state and makes `S=1`.

This avoids both false completion and external-content delete against a row the optional index never held.

## 6. Ordered implementation plan for #26

### Commit 1 — RED lifecycle/capability contract

Add failing tests before production changes. Prefer a focused `tests/test_session_metadata_cjk_fts.py` if keeping `test_session_metadata_fts.py` readable becomes difficult; shared fixture helpers may remain in the existing module.

RED cases:

1. legacy/current title-only CJK shape is rejected as the final shape: target must expose raw `(title,id,display_name)` external content keyed by named `row_id`;
2. populated DB creates **CJK-session-owned** H/P state;
3. `H/P present + tokenizer operable + search unavailable` still allows a rebuild step (deadlock regression);
4. tokenizer unavailable leaves Unicode/session writes healthy and does not clear CJK pending state;
5. one-character CJK is classified as fallback-only.

Validation:

```bash
uv run pytest tests/test_session_metadata_cjk_fts.py -q
uv run pytest tests/test_session_metadata_fts.py tests/test_optional_cjk_tokenizer_fallback.py -q
```

(If tests are kept in the existing file, substitute that path.)

### Commit 2 — external-content DDL + independent capability/quarantine

Production changes:

- replace `SESSIONS_FTS_CJK_SQL` with split table/trigger DDL over `title,id,display_name`, `content='sessions'`, `content_rowid='row_id'`;
- add CJK-session trigger tuple and stale key;
- detect/replace the pre-#26 internal title-only table on a capable host;
- seed independent CJK-session H/P for populated canonical rows;
- split worker operability from search-serving availability;
- narrow UPDATE to `UPDATE OF title,id,display_name` plus value-change guard;
- extend writer/read-only connection probing; all CJK MATCH goes through `_read_ctx()`.

RED→GREEN additions:

- existing internal CJK table upgrades safely;
- unrelated session metadata UPDATE does not rewrite CJK FTS;
- `>H` inserts are live-indexed;
- tokenizer-less open drops only unsafe session-CJK triggers after durable stale breadcrumb and leaves Unicode operational;
- `SessionDB(read_only=True)` correctly probes/loads optional CJK capability and otherwise falls back.

Validation:

```bash
uv run pytest tests/test_session_metadata_cjk_fts.py tests/test_optional_cjk_tokenizer_fallback.py tests/test_session_db_read_path_split.py -q
uv run python -m compileall hermes_state.py hermes_state_common.py hermes_state_schema.py hermes_state_search.py
```

### Commit 3 — plug CJK session spec into #25 generic rebuild engine

Production changes:

- add `_FTS_SESSION_CJK_SPEC` using the independent CJK-session H/P keys and raw three-column document;
- make its worker gate use worker operability, never `_sessions_cjk_available`;
- add thin `fts_session_cjk_rebuild_status/step` wrappers;
- reuse `_repair_missing_progress()` and the session bookkeeping pattern;
- add CJK-session phase to `optimize_fts_storage()` with the same `_fts_rebuild_pause()`;
- apply #77629 invariant: finish/boundary work uses the same operability gate as step;
- on successful finish: boundary sweep, clear CJK-session H/P/stale, then set search-serving availability true;
- stale capable restart resets to known-empty + reseeds from current high-water before replay.

RED→GREEN cases:

```text
pending + W=1 + S=0 -> step advances P -> finish -> markers clear -> S=1
restart after partial P -> same H/P resumes, no P reset
H without P -> shared crash repair resets safely then P=0
stale after incapable writer -> capable restart rebuilds from fresh high-water
```

Validation:

```bash
uv run pytest tests/test_session_metadata_cjk_fts.py tests/test_session_metadata_fts.py -q
uv run pytest tests/test_optional_cjk_tokenizer_fallback.py tests/test_session_db_read_path_split.py -q
```

### Commit 4 — CJK metadata candidate seam + all-three-field acceptance

Production changes:

- add/finish a CJK metadata candidate helper analogous to `_fts_metadata_candidates()` over `title,id,display_name`;
- execute via `_read_ctx()`, not `self._lock`;
- distinguish “CJK lane unavailable/unsupported” from “valid zero matches” so #14 can route correctly later;
- pending/stale/unavailable/1-char CJK -> signal bounded canonical fallback; do not serve a partial CJK index;
- update `_fts_numbered_variants()` to use this seam rather than direct legacy title-only MATCH / Unicode-gap coupling.

Acceptance tests:

- completed CJK index finds representative 2+ char CJK in **title**;
- same through logical **session id**;
- same through **display_name**;
- pending CJK returns correct results through fallback but is not counted as CJK-serving;
- unavailable tokenizer does the same;
- a successful CJK MATCH returns a valid zero result distinctly from capability failure.

Do **not** replace `list_sessions_rich(search_query=...)` broad discovery here; #14 owns picker candidate routing and benchmarking.

Validation:

```bash
uv run pytest tests/test_session_metadata_cjk_fts.py tests/test_session_metadata_fts.py tests/test_optional_cjk_tokenizer_fallback.py tests/test_session_db_read_path_split.py -q
```

Then run the repository's normal targeted/full test gate used for state changes before PR creation.

## 7. Pitfalls / non-goals

### Must avoid

1. **One boolean for worker + serving.** This recreates the known deadlock: pending makes serving false, the same false blocks the worker, finish never happens.
2. **Using Unicode H/P for CJK.** The indexes can be at different completion points and CJK capability is optional.
3. **Copying message-CJK's standalone scheduler.** The generic #25 session rebuild engine now exists specifically to avoid this duplication.
4. **Finish without capability.** #77629 pins the opposite contract.
5. **Reinstalling triggers over stale unknown gaps.** Reset/reseed first on a capable host.
6. **Plain DELETE semantics on external-content FTS.** Use the same external-content delete-command discipline as #25/message CJK.
7. **Broad `AFTER UPDATE ON sessions`.** Only title/id/display_name changes belong to the metadata index.
8. **Writer-only tokenizer assumption.** MATCH happens on read connections; capability is connection-local enough that failures must degrade safely.
9. **Serving pending CJK with an ad-hoc `(P,H]` supplement.** #26 explicitly says pending is not search-serving. Canonical fallback is complete and simpler.
10. **Settling storage-v2 or picker routing.** #27 owns unified settlement; #14 owns candidate-first picker routing.

### Explicit non-goals

- normalized/trigram session metadata index (#30 / sibling lifecycle work);
- full picker/listing search routing and performance benchmark (#14);
- ranking changes;
- lineage/projection algorithm changes;
- changing canonical session identity or the #25 `row_id` migration;
- changing the native CJK tokenizer algorithm.

## 8. Split decision

**Do not split #26.**

The pinned code audit reduced the task to one specialization across four tightly coupled seams: optional DDL/capability, generic rebuild spec, degraded-host recovery, and the small search interface. Splitting these into separate tickets would create intermediate states where the optional table exists without a safe lifecycle or lifecycle exists without a truthful serving flag. Four commits inside one PR give reviewable boundaries without fragmenting the correctness invariant.

## 9. `/implement #26` entry checklist

The implementation agent should:

1. verify `dev` still contains pinned base `e94f2630a50d7585f78cfc06365753c033113cb9` (if HEAD moved, inspect only overlapping state files; do not redo this research unless the relevant seams changed);
2. read #26 + this note + #26's existing #77629 / R2 pitfall comments;
3. start with Commit 1 RED tests;
4. reuse #25 generic rebuild machinery; no second scheduler;
5. preserve the state transition `pending: W=1,S=0 -> finish -> S=1`;
6. keep CJK unavailable as degradation, never completion.

Research complete for #33.