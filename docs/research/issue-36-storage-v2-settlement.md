# #36 research — storage-v2 settlement state machine for #31

Status: **READY FOR IMPLEMENTATION — no blocker / no further split required**  
Pinned post-#27 base: **`9d140c8594c67ed37729b190fd3733508d0d770c`** (`dev`, PR #76 / #27 merge)  
Target: **#31 — Session metadata FTS: settle storage v2 after all required indexes complete**  
Scope: research / executable handoff only. This note does **not** implement #31.

## 0. Executive result

The pinned code already has the crash-safe per-index machinery #31 needs. The missing piece is much smaller: **one durable/schema-aware storage-settlement evaluator consumed everywhere that can claim or advertise completion**.

Today four decisions have drifted apart:

1. writable startup auto-stamps `fts_storage_version` from a **message-only** subset of state, and does so **before** the three session-metadata ensure paths run;
2. `fts_optimize_available()` uses #27's `_fts_lane_pending()`, whose documented meaning is *pending work this process can operate*, not *DB acceptance completeness*;
3. the foreground pre-VACUUM refusal checks message + session Unicode + locally-available session trigram, but omits CJK markers/stale state and structural fail-closed states;
4. the final transactional `_settle()` regresses to only message high-water + trash + message-empty checks before stamping.

That is exactly the failure class #31 should remove. **Do not add a second rebuild engine, scheduler, registry, or migration framework.** Reuse #25/#26/#30/#27 and the merged upstream #76832/#77629 seams; centralize only the decision “may this database claim storage v2?”

Recommended implementation shape:

- bump `FTS_STORAGE_VERSION` from `1` to `2`;
- add one **SELECT-only** settlement evaluator, conceptually `_fts_storage_v2_blocker(conn)`, where `None` means acceptance-complete and a returned blocker means **do not stamp**;
- make the evaluator durable/schema based — it must not hide state merely because this process lacks an optional tokenizer;
- move startup settlement **after** session Unicode/CJK/trigram schema ensure/classification;
- use the same evaluator for startup, `fts_optimize_available()`, the foreground pre-VACUUM refusal, and the final `_execute_write()` settlement re-check;
- preserve current per-lane repair/rebuild code unchanged.

Optional-capability rule: **absence with no durable claim/stale state can be a valid degraded terminal state when that lane's contract allows it; an existing H/P claim, stale/quarantine breadcrumb, legacy/unknown same-name object, or incomplete existing target is never “complete” merely because the current host cannot operate it.**

---

## 1. Authority and pin

### 1.1 BASE_SHA

`dev` after #27 is:

```text
9d140c8594c67ed37729b190fd3733508d0d770c
```

PR #76 is the accepted #27 implementation and explicitly leaves storage settlement / `FTS_STORAGE_VERSION` to #31.

Pinned code links in this note use that SHA, never moving `dev`.

### 1.2 Primary-source authority order

1. pinned fork code at `9d140c8594...`;
2. #31 acceptance contract;
3. merged upstream crash-safety seams already present in fork ancestry:
   - NousResearch/hermes-agent#76832 — claim-before-empty-schema, orphan/known-empty recovery, refuse final stamp while incomplete;
   - NousResearch/hermes-agent#77629 — optional capability must gate finish exactly as it gates step;
4. accepted #25/#26/#30/#27 implementation records only to explain why the current code has the state it has.

Fork-only historical donor shapes are evidence, not an implementation template.

---

## 2. `fts_storage_version`: exact current readers/writers

### 2.1 Version constant

`hermes_state_common.py:L156-L170`  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_common.py#L156-L170

```python
FTS_STORAGE_VERSION = 1
```

The marker is deliberately independent from `SCHEMA_VERSION`. #31 is the owner of the next storage-layout claim, so implementation should change this to `2` only when the shared v2 predicate is introduced in the same stack.

### 2.2 Writable-startup writer — currently too early and too narrow

`hermes_state_schema.py:L1104-L1128` (`_init_schema`)  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_schema.py#L1104-L1128

Current startup stamps when all of these are true:

- FTS5 available;
- no legacy inline **message** FTS;
- no `fts_rebuild_high_water`;
- no message FTS trash;
- message base external index is not empty against non-empty `messages`.

It does **not** inspect session Unicode/CJK/trigram state.

Worse, the session ensure paths occur later at `hermes_state_schema.py:L1252-L1274`:  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_schema.py#L1252-L1274

- `_ensure_sessions_fts_schema(cursor)`;
- `_ensure_sessions_fts_cjk_schema(cursor)`;
- `_ensure_sessions_trigram_fts_schema(cursor)`.

Therefore a writable open can currently write the storage marker **before** those paths have had the chance to stage H/P, classify a same-name trigram object, or quarantine stale optional state.

### 2.3 Repair writer — clears a premature marker for the historical message-empty crash shape

`hermes_state_search.py:L1114-L1174` (`_repair_optimize_bookkeeping`)  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_search.py#L1114-L1174

If the message external index is empty over non-empty messages and no message claim exists, repair deletes `fts_storage_version` and re-seeds the message rebuild claim. This is the merged #76832 recovery seam and should remain the model for **known-empty/orphan recovery**, not be reimplemented per session lane.

### 2.4 Foreground final writer — currently message-only

`hermes_state_search.py:L1555-L1604` (`optimize_fts_storage._settle`)  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_search.py#L1555-L1604

The final transactional re-check currently refuses only for:

- `fts_rebuild_high_water`;
- message FTS trash;
- message external index empty against non-empty `messages`.

It then writes `fts_storage_version` and clears `fts_optimize_available`.

**This is the highest-risk drift point:** even the foreground pre-check knows more session state than the final transaction that actually writes the claim.

### 2.5 No independent marker-as-proof reader in the settlement path

Current readiness is re-derived from physical/meta state by `fts_optimize_available()` and the settlement guards; the marker itself is primarily written/cleared as the resulting layout claim. #31 should preserve that direction: **state proves the version claim; the version claim must not be used to prove the state.**

---

## 3. The authoritative per-index state already exists — reuse it

### 3.1 Five deferred rebuild lanes

`hermes_state_search.py:L180-L329`  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_search.py#L180-L329

The post-#27 specs already expose all durable H/P pairs:

| Lane | H key | P key | stale key |
|---|---|---|---|
| message Unicode (+ message trigram sidecar) | `fts_rebuild_high_water` | `fts_rebuild_progress` | — |
| message CJK | `fts_cjk_rebuild_high_water` | `fts_cjk_rebuild_progress` | `fts_cjk_stale` |
| session Unicode | `fts_session_rebuild_high_water` | `fts_session_rebuild_progress` | — |
| session trigram | `fts_session_trigram_rebuild_high_water` | `fts_session_trigram_rebuild_progress` | `fts_session_trigram_stale` |
| session CJK | `fts_session_cjk_rebuild_high_water` | `fts_session_cjk_rebuild_progress` | `fts_session_cjk_stale` |

`_FTS_REBUILD_LANES` is the correct membership source for these lane-specific durable keys. #31 should reuse the list to inspect durable state rather than hard-code another marker ladder.

### 3.2 `_fts_lane_pending()` is **not** the storage-v2 completion predicate

`hermes_state_search.py:L431-L463`  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_search.py#L431-L463

The helper explicitly returns pending only when `spec["available"](self)` is true. Its docstring says “durable pending work ... that **THIS process can operate**”.

That is correct for the worker/status surface, but wrong for v2 completeness. Examples:

- a tokenizer-less host can carry `fts_session_cjk_rebuild_high_water` + `fts_session_cjk_stale`; `_fts_lane_pending()` returns false because the worker is inoperable, but the database is not acceptance-complete;
- a quarantined session trigram target has `_sessions_trigram_available=False`, so the lane helper hides its stale state even though that stale breadcrumb must forbid v2.

**Implementation rule:** use `_FTS_REBUILD_LANES` for names, but inspect H/P/stale durably without the lane operability gate when deciding whether v2 may settle.

### 3.3 Shared crash bookkeeping is already correct

`hermes_state_search.py:L1082-L1212`  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_search.py#L1082-L1212

- `_repair_missing_progress(conn, spec)` is the shared H-without-P recovery rule;
- it resets a partially populated target to known-empty before publishing `P=0`;
- `_repair_session_spec_bookkeeping(spec)` reuses that rule and seeds an orphan empty external session index;
- Unicode/trigram/CJK wrappers delegate to the shared primitive.

#31 should **not** clone any of this into settlement. Settlement only refuses while those states exist; existing repair paths own making them progressable.

---

## 4. Session index state that settlement must understand

### 4.1 Session Unicode (#25) — required

`hermes_state.py:L3069-L3207` (`_ensure_sessions_fts_schema`, `_fts_session_schema_transition`)  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state.py#L3069-L3207

Accepted invariants already present:

- pre-#25 internal-content shape is converted;
- H/P is staged before a populated external index can exist empty;
- crash between claim and schema install reopens with the claim intact;
- fresh schema + trigger-owned catch-up is one `BEGIN IMMEDIATE` transition;
- zero canonical sessions are complete by construction and do not retain H=0/P=0.

Settlement must refuse while the Unicode session target is legacy/internal, has either marker, or is externally empty over non-empty `sessions`.

### 4.2 Session CJK (#26) — optional capability, durable incompleteness

`hermes_state.py:L2804-L3067` (`_ensure_sessions_fts_cjk_schema`, `_fts_session_cjk_schema_transition`)  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state.py#L2804-L3067

The final #26 code explicitly distinguishes:

- `_sessions_cjk_worker_operable` — this process can build/maintain;
- `_sessions_cjk_available` — completed index can serve search.

On a tokenizer-less host with a present live CJK surface it writes the stale breadcrumb **before** dropping triggers and **never clears pending H/P merely because capability is missing**. A capable host converts legacy internal shape, seeds H/P before empty external creation, and later resets stale state from canonical rows.

Therefore:

- **absent CJK target + no CJK H/P/stale + unavailable tokenizer** may be a valid optional degraded terminal state;
- **any CJK H/P or stale breadcrumb** blocks v2 even on an incapable host;
- legacy internal CJK shape blocks v2;
- an existing external CJK target empty over populated `sessions` blocks v2 until existing orphan repair/rebuild settles it.

### 4.3 Session normalized trigram (#30) — exact same-name identity matters

`hermes_state.py:L3370-L3775` (`_classify_sessions_fts_trigram`, `_ensure_sessions_trigram_fts_schema`, quarantine/stale recovery)  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state.py#L3370-L3775

The final classifier intentionally recognizes only:

- `absent`;
- `modern_trigram`;
- `unknown_same_name`.

The earlier research design's fork-only `legacy_simple` convergence was deliberately removed from #30. The accepted implementation record documents that scope correction in `docs/research/issue-30-normalized-session-metadata-trigram-fts.md`.

**Consequence for #31:** do not resurrect legacy-simple migration. A historical fork-only `tokenize='simple'` same-name root is now an `unknown_same_name` shape and must simply **refuse v2, fail closed, and remain untouched**. #31 owns settlement, not destructive convergence.

Additional trigram fail-closed states that must refuse v2:

- unknown root/source identity;
- foreign same-name trigger occupants;
- modern root with an incomplete exact trigger set and no stale breadcrumb;
- durable stale/quarantine;
- H/P pending;
- modern target empty over populated derived source with no claim.

Optional terminal case:

- no modern root, no H/P/stale/collision, and this runtime genuinely lacks trigram capability may be accepted as degraded optional absence;
- if the runtime can provide trigram, startup's ensure path should establish the modern target before the settlement evaluator runs.

A healthy modern target reopened on a no-trigram runtime is **not** the same as optional absence: #30 quarantines it by writing stale and dropping owned triggers, so v2 must refuse until a capable recovery re-establishes authority.

---

## 5. Current control-flow drift

### 5.1 Startup

Current order:

```text
main migrations
  -> message-only storage stamp decision        [too early]
  -> schema_version advance
  -> message FTS ensure
  -> message CJK ensure
  -> session Unicode ensure / H-P staging
  -> session CJK ensure / stale classification
  -> session trigram exact identity / quarantine / H-P staging
  -> commit
```

Required #31 order:

```text
main migrations
  -> schema_version advance (still independent)
  -> all existing message/session ensure paths
  -> shared storage-v2 evaluator
       -> blocker: do not stamp
       -> complete: upsert fts_storage_version=2
  -> commit
```

Do not hold `schema_version` behind storage v2; that independence is an accepted v23 contract.

### 5.2 Foreground optimize

Current phase driver already repairs and runs the right engines:

`hermes_state_search.py:L1340-L1480`  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_search.py#L1340-L1480

- message orphan/H-without-P repair;
- session Unicode repair;
- session trigram repair;
- session CJK repair;
- stale reset/recovery hooks;
- `_fts_run_pending_lane_steps()` over all five deferred lanes;
- trash teardown.

The problem begins only at settlement.

Pre-VACUUM refusal at `hermes_state_search.py:L1481-L1533`:  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_search.py#L1481-L1533

- checks message H + session Unicode H;
- checks session trigram H/empty only if `_sessions_trigram_available`;
- does not check message-CJK/session-CJK H/P/stale;
- does not check session-trigram stale/quarantine or unknown same-name structure.

Final `_settle()` at `L1555-L1604` is even narrower and only rechecks the message base.

Required #31 flow:

```text
repair / ensure / stale recovery
  -> shared lane worker loop
  -> trash teardown
  -> shared evaluator (same as startup)
       -> blocker => return refusal BEFORE VACUUM
  -> optional VACUUM
  -> BEGIN IMMEDIATE
       -> shared evaluator AGAIN on this transaction's connection
       -> blocker => no stamp
       -> complete => write fts_storage_version=2
```

Calling the same evaluator twice is intentional: the first avoids an expensive VACUUM when completion is already impossible; the second preserves #76832's race-safe “re-check in the write transaction that performs the claim” rule.

### 5.3 `fts_optimize_available()`

Current code: `hermes_state_search.py:L1214-L1283`  
https://github.com/Skywind5487/hermes-agent/blob/9d140c8594c67ed37729b190fd3733508d0d770c/hermes_state_search.py#L1214-L1283

It should stop maintaining its own incomplete-state list. Derive it from the same v2 evaluator.

Recommended semantic split **inside the one evaluator result**, not as a second inventory:

- `complete` — stamp allowed, optimize not advertised;
- `blocked + actionable here` — optimize advertised;
- `blocked + requires optional capability / external resolution` — stamp still refused; CLI/status may report why it cannot finish on this host;
- valid optional absence — not blocked.

If implementation prefers a tiny return type, one blocker record such as `(reason, actionable_here)` is enough; avoid a new framework. `None` remains the single proof of settlement completeness.

This lets `fts_optimize_available()` and the final stamp share the **same state truth** while preserving the distinction between “work exists” and “this process can perform every step now”.

---

## 6. Complete storage-v2 refusal-state table

The table below is the executable contract for #31. “Block v2” is based on durable/schema state, not local serving booleans.

| State | Block v2? | Actionable here? | Existing owner / action |
|---|---:|---:|---|
| FTS5 unavailable | yes | no | preserve no-stamp behavior |
| legacy inline message FTS | yes | yes | existing `_demote_legacy_fts_to_trash` |
| any message Unicode H **or P** marker | yes | yes | shared message rebuild |
| message FTS trash remains | yes | yes | existing chunked teardown |
| message base external index empty while `messages` non-empty | yes | yes | `_repair_optimize_bookkeeping` seeds claim |
| message-CJK H/P or `fts_cjk_stale` exists | yes | only if CJK capable | existing CJK reset/rebuild |
| message-CJK absent with no durable CJK state on incapable host | no | n/a | valid optional absence; preserve fallback |
| session Unicode legacy/internal shape | yes | yes | `_ensure_sessions_fts_schema` conversion |
| session Unicode H **or P** marker | yes | yes | shared session rebuild |
| session Unicode external index empty while `sessions` non-empty | yes | yes | `_repair_session_spec_bookkeeping` |
| session CJK legacy/internal shape | yes | only if CJK capable | existing #26 conversion/recovery |
| session CJK H/P or stale breadcrumb | yes | only if CJK capable | existing #26 reset/rebuild; incapability is not completion |
| session CJK external target exists but is empty over populated `sessions` | yes | only if CJK capable | existing orphan/claim path |
| session CJK absent + no H/P/stale on CJK-incapable host | **no** | n/a | valid optional degradation |
| session trigram `unknown_same_name` (includes fork-only historical simple root) | yes | no inside #31 | fail closed; #31 must not delete/demote it |
| session trigram source/root/trigger namespace collision or noncanonical modern trigger set | yes | no automatic #31 mutation | existing #30 fail-closed ownership boundary |
| session trigram H/P or stale/quarantine breadcrumb | yes | only if trigram capable | existing #30 recovery/rebuild |
| modern session trigram target empty over populated derived source | yes | if owned + capable | existing open/orphan repair |
| session trigram absent + no H/P/stale/collision on trigram-incapable host | **no** | n/a | valid optional degradation |
| healthy modern session trigram reopened on incapable host | yes after #30 quarantine writes stale | no | later capable reopen/recovery |
| canonical `sessions` empty and optional target absent with no durable state | no | n/a | complete by construction; existing seed helpers already clear H=0/P=0 |
| P-without-H or other impossible leftover marker shape | yes | no blind inference | fail closed; never treat orphan meta as completion |
| all applicable targets current, no H/P/stale/trash/orphan/unknown state | **no** | n/a | storage v2 may settle |

### Why check both H and P

The existing workers normally clear H/P together and the known crash repair focuses on H-without-P. Settlement should still fail closed if **either** marker survives. A stray P-only state is not evidence of completeness and should never be enough to write v2.

### Optional means “absence may be acceptable”, not “stale may be ignored”

This distinction is the important #31 capability rule:

```text
optional + never established + no durable work = valid degraded completion
optional + claim/stale/quarantine/incomplete existing target = unfinished
```

It preserves #26/#30's “missing capability is never evidence of completion” while avoiding a requirement that every installation possess every optional tokenizer.

---

## 7. Reuse audit — what #31 must NOT rebuild

### 7.1 Merged upstream #76832: use unchanged

Primary source: https://github.com/NousResearch/hermes-agent/pull/76832

Already present in the fork:

- H/P claim committed before an empty external message schema can exist;
- final settlement re-check inside the write transaction;
- unmarked-empty-index repair;
- H-without-P repair with known-empty reset before replay;
- O(1) `EXISTS` empty probes;
- FTS5 `delete-all` reset.

Session Unicode/CJK/trigram implementations already copied/adapted the same ordering. #31 only aggregates their state into one settlement proof.

### 7.2 Merged upstream #77629: use unchanged

Primary source: https://github.com/NousResearch/hermes-agent/pull/77629

Step/finish optional capability symmetry is already embedded in the generic rebuild specs/engine. Do not change worker capability semantics to make settlement easier. Settlement must observe durable state independently.

### 7.3 #27 registry/lane work: use, do not extend into a second framework

`FTS_INDEXES` owns six static index identities. `_FTS_REBUILD_LANES` owns the five deferred rebuild lanes and their marker/stale names. #31 may consume these sources; it must not add another six-index registry.

---

## 8. RED-first test plan

Existing suites already prove each lane's own crash/rebuild machinery. #31 tests should target **settlement composition**, not duplicate those engines.

### Commit 1 — shared v2 blocker + RED matrix

Suggested commit:

```text
test(fts): pin storage-v2 refusal states
```

Add a focused `tests/test_fts_storage_v2_settlement.py` (preferred over further growing `test_hermes_state.py`) with table-driven cases that construct the minimal physical/meta state and assert the shared evaluator blocks/allows correctly.

Required RED cases:

1. session Unicode H/P blocks v2 while message base is fully settled;
2. session CJK H/P blocks v2 even when current host is incapable;
3. session CJK stale-only blocks v2;
4. healthy optional CJK absence on incapable host does **not** block;
5. session trigram H/P blocks v2 even when local serving flag is false;
6. session trigram stale/quarantine blocks v2;
7. `unknown_same_name` trigram blocks v2 and survives byte/schema-identical (no destructive “repair”);
8. optional trigram absent/no-state on no-trigram host does not block;
9. session Unicode orphan-empty target blocks;
10. applicable optional existing target orphan-empty blocks;
11. any P-only leftover blocks fail-closed;
12. fully settled matrix returns no blocker.

### Commit 2 — startup and foreground consume one evaluator

Suggested commit:

```text
fix(fts): unify storage-v2 settlement predicate
```

Tests:

- populated DB first open: startup may not stamp before session ensure stages H/P;
- complete fresh/empty DB may stamp v2;
- startup stale/quarantined/unknown trigram cannot stamp;
- `fts_optimize_available()` derives from the same evaluator result;
- pre-VACUUM and final transactional settle return the same blocker reason for a manufactured state.

### Commit 3 — interruption / reopen / final-stamp race

Suggested commit:

```text
test(fts): cover storage-v2 interruption and reopen
```

Reuse existing fixture techniques rather than monkeypatching internals that have no production seam.

Mandatory interruption cases:

1. **claim before schema** — after durable session H/P is committed but before the external table/schema transition, reopen preserves claim and v2 absent;
2. **schema before backfill finish** — empty/partial external session target + H/P, reopen remains incomplete and does not stamp;
3. **H without P** — existing repair restores P only after known-empty reset; v2 remains absent until rebuild completes;
4. **stale written after a formerly healthy optional target** — reopen refuses v2 and does not serve stale as complete;
5. **race before final stamp** — inject/reseed a blocker between pre-VACUUM check and final `_execute_write`; transactional re-check refuses the stamp;
6. **completed reopen** — v2 remains current, no new H/P/stale created, no rebuild steps invoked.

Existing primary test seams to reuse:

- `tests/test_hermes_state.py::test_demote_writes_markers_before_empty_schema`;
- `tests/test_hermes_state.py::test_optimize_heals_premature_stamp_with_empty_index`;
- `tests/test_hermes_state.py::test_optimize_settle_refuses_pending_backfill`;
- `tests/test_session_metadata_fts.py::test_partial_index_orphan_hp_resets_and_replays`;
- `tests/test_session_metadata_cjk_fts.py` stale-capable-restart / incapable-pending tests;
- `tests/test_session_metadata_trigram_fts.py::test_orphan_hp_resets_only_trigram`;
- `tests/test_session_metadata_trigram_fts.py::TestOpenTimeOrphanRepair`;
- `tests/test_session_metadata_trigram_fts.py::test_classifier_unknown_same_name`;
- `tests/test_session_metadata_trigram_fts.py::test_trigram_tokenizer_missing_preserves_fresh_claim`.

### Commit 4 — acceptance matrix / cleanup

Suggested commit:

```text
test(fts): prove complete six-index storage-v2 settlement
```

One final matrix should prove:

- message base + all applicable session lanes complete => v2 stamped;
- any one required/applicable blocker reintroduced => marker cleared/refused on repair/re-evaluation;
- re-completing that one lane => v2 can be re-earned;
- no search routing/ranking behavior changes.

If the implementation stays small enough, commits 3 and 4 may be combined; do not split production code merely to satisfy a nominal commit count.

---

## 9. Validation commands

Focused settlement/lifecycle sweep:

```bash
uv run pytest -q \
  tests/test_fts_storage_v2_settlement.py \
  tests/test_hermes_state.py \
  tests/test_session_metadata_fts.py \
  tests/test_session_metadata_cjk_fts.py \
  tests/test_session_metadata_trigram_fts.py \
  tests/test_fts_cjk_bigram.py \
  tests/test_fts_lifecycle_registry.py \
  tests/test_session_db_read_path_split.py
```

Then the state/repair regressions that exercise the shared storage substrate:

```bash
uv run pytest -q \
  tests/state/test_fts_runtime_rebuild.py \
  tests/test_state_db_malformed_repair.py \
  tests/test_fts_update_of_narrowing.py
```

Static check on touched files:

```bash
uv run ruff check \
  hermes_state_common.py \
  hermes_state_schema.py \
  hermes_state_search.py \
  tests/test_fts_storage_v2_settlement.py
```

CJK-capable tests may skip unless `native/fts5_cjk/fts5_cjk.c` can be built/loaded or `HERMES_FTS5_CJK_SO` points at a loadable artifact. **A skip is not capability evidence.** The incapable-host matrix must still run.

---

## 10. Pitfalls / non-goals for `/implement #31`

1. **Do not use `_fts_lane_pending()` as the v2 predicate.** It is intentionally operability-gated.
2. **Do not put the shared predicate before session ensure in `_init_schema`.** That preserves the current premature-stamp bug.
3. **Do not check only H.** Any leftover H or P is non-settled durable state.
4. **Do not use process-local `_sessions_*_available` as proof of DB completeness.** It is serving/operability state, not durable acceptance state.
5. **Do not resurrect #30's removed legacy-simple migration.** `unknown_same_name` must fail closed and refuse v2.
6. **Do not clear stale/H/P because a tokenizer is unavailable.** Missing capability is not evidence of completion.
7. **Do not require optional indexes that have never existed and have no durable work on an incapable host.** That would deadlock valid degradation.
8. **Do not redesign search routing, ranking, candidate fallbacks, lifecycle registry, ordinary optimize/merge/health, or repair membership.** Those belong to #14/#27/etc.
9. **Do not use the storage-version marker to prove the underlying state.** Re-evaluate state, then write the marker as the claim.
10. **Keep the final re-check inside the same write transaction that writes v2.** A preflight-only predicate reintroduces the #76832 race class.
11. **Do not add a second state framework.** A tiny blocker result (`None` vs reason/actionability) is sufficient.
12. **Do not hold `SCHEMA_VERSION` behind v2.** Storage-layout versioning remains independently opt-in/recoverable.

---

## 11. Implementation handoff

#31 is **ready for `/implement`** on exactly:

```text
BASE_SHA = 9d140c8594c67ed37729b190fd3733508d0d770c
```

The implementation agent should verify that `dev` still contains this SHA (or rebase/re-audit only if settlement-relevant code changed after it), then:

1. add the shared SELECT-only storage-v2 blocker/evaluator using current durable/schema sources;
2. bump `FTS_STORAGE_VERSION = 2` in the same implementation stack;
3. move startup stamp after all FTS/session ensure paths and consume the evaluator;
4. replace `fts_optimize_available()`'s independent incomplete-state inventory with the evaluator result;
5. replace both foreground pre-VACUUM and final transactional hard-coded lists with the evaluator;
6. leave all per-lane worker/repair/stale-transition logic in its current owner;
7. add the RED/reopen matrix above.

**No blocker remains. No new explorer ticket is required.**
