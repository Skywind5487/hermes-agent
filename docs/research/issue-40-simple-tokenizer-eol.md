# #40 research: retire remaining `simple` tokenizer compatibility

Status: **research complete**  
Target implementation: **#19**  
Parent: **#13**  
Code-audit base: **`4e5ad5c2230300d1ffae84b089ffc70e368c8a23`** (`dev@4e5ad5c22`, final #12 acceptance)  
Research-only branch: `research/40-simple-tokenizer-eol`

## Executive conclusion

Final #12 has **no supported database state that requires the legacy loadable `simple` tokenizer at open or write time**.

At the pinned base, the remaining `simple` dependency is a global compatibility shim in `hermes_state.py`: it loads `libsimple.so` on the health probe, writer connection, and inherited read connections because old fork development builds may still carry `messages_fts_trigram` / `sessions_fts_trigram` declarations using `tokenize='simple'`.

That compatibility contract is no longer supported:

- final #30 deliberately removed the historical fork-only `sessions_fts_trigram(simple)` migration/classifier path and requires that state to be treated as unsupported history, not as a modern compatibility obligation;
- the supported legacy **message** migration is shape-driven and tokenizer-independent;
- current message trigram and session trigram use SQLite `tokenize='trigram'`, not `simple`;
- offline session recovery copies canonical rows into a fresh current-schema destination and never copies derived FTS schema.

Therefore #19 should be a **subtractive EOL cleanup**, not another legacy migration subsystem:

1. structurally sanitize only **exact, Hermes-owned historical `simple` residue** before ordinary writable schema initialization can touch it;
2. preserve canonical `sessions` / `messages` rows and let the existing modern FTS lifecycle rebuild derived indexes;
3. keep unknown same-name objects fail-closed and untouched;
4. remove `libsimple` path/loading/capability propagation completely.

Do **not** restore #30's deleted `legacy_simple` classifier, legacy-session migration state machine, or permanent simple-tokenizer capability.

---

## Authority and fixed point

#40 was previously blocked because #12/#30 had not converged. That blocker is gone. Final #12 acceptance records `dev@4e5ad5c22` and closes the six-index lifecycle/storage work, including #30.

All source line numbers below are against:

```text
BASE_SHA=4e5ad5c2230300d1ffae84b089ffc70e368c8a23
```

The repository's later `dev` commits are not used to silently rewrite this audit. `/implement #19` should first compare the listed seams against current `dev`; if they drift materially, update only the affected mapping rather than repeating the whole research pass.

---

## Exhaustive remaining `simple` dependency inventory

### A. Runtime compatibility shim — REMOVE

`hermes_state.py:L1914-L1952`

- `simple_tokenizer_so_path()`
  - `HERMES_LIBSIMPLE_PATH` override;
  - fallback `~/.hermes/libsimple/libsimple.so`.
- `load_simple_extension(conn)`
  - best-effort loadable-extension registration;
  - comment explicitly names pre-v23 development DBs carrying `messages_fts_trigram` / `sessions_fts_trigram` with `tokenize='simple'`.

Classification: **REMOVE** after the pre-init EOL sanitation path exists.

### B. Health / corruption probe — REMOVE simple load, RETAIN probe

`hermes_state.py:L1389-L1418`

`_db_opens_cleanly()` currently calls `load_simple_extension(conn)` before CJK loading and FTS health checks.

Classification:

- `load_simple_extension(conn)`: **REMOVE**;
- `_db_opens_cleanly()` and its canonical/FTS health probes: **RETAIN**;
- exact historical-simple residue must be classified as EOL-repairable (or explicitly unsupported on a read-only probe), not made healthy by reinstalling `libsimple`.

### C. Writer connection — REMOVE

`hermes_state.py:L2318-L2323`

- `_simple_loaded = False` process-local capability flag.

`hermes_state.py:L2518-L2526`

- writer open calls `self._simple_loaded = load_simple_extension(self._conn)` immediately before `_init_schema()`.

Classification: **REMOVE** flag and loader call.

Important ordering consequence: if historical simple triggers/vtables can make `_init_schema()` touch a missing tokenizer, #19's narrow sanitation must run **before** ordinary schema initialization on writable open. Otherwise deleting the loader merely changes the failure from hidden compatibility to `no such tokenizer: simple` before the user can reach repair/optimize.

### D. Read connection propagation — REMOVE

`hermes_state.py:L2645-L2654`

Per-thread read connections reload `simple` only when `_simple_loaded` was true on the writer.

Classification: **REMOVE**. A normal read connection created by a successfully initialized writable `SessionDB` should never inherit a retired tokenizer requirement.

For any explicit standalone read-only open of an unsanitized historical-simple DB, policy should be explicit: fail/degrade with an actionable writable-repair instruction; do not resurrect `libsimple` solely to make unsupported residue queryable.

### E. Supported legacy message detection — RETAIN

`hermes_state.py:L2706-L2732`

`_db_has_legacy_inline_fts(cursor)` detects every supported pre-v23 `messages_fts` shape by stored DDL: v23 declares `tool_name` / `tool_calls`; their absence means legacy layout. This is a pure `sqlite_master.sql` shape test and does not require any tokenizer.

Classification: **RETAIN**. It is exactly the kind of tokenizer-independent compatibility seam #19 should preserve.

### F. Supported message demotion / optimize — RETAIN

`hermes_state_search.py` message-storage demotion/optimize path (`_demote_legacy_fts_to_trash`, rebuild/status/finish, trash teardown; pinned base around the storage-optimize section).

The accepted v22→v23 path structurally removes legacy FTS roots via `writable_schema`, renames FTS shadows into ordinary `fts_v22_trash_*` tables, seeds durable rebuild H/P state, and creates/rebuilds the modern indexes. It does not need to instantiate or `DROP TABLE` an old tokenizer-backed virtual table.

Classification: **RETAIN / REUSE**. #19 should reuse the structural-detach pattern instead of loading an obsolete tokenizer to execute DDL against it.

### G. Modern message trigram — RETAIN

`hermes_state_common.py:L548+` (`FTS_TRIGRAM_SQL`)

Modern `messages_fts_trigram` is external-content over `messages_fts_trigram_src` and declares:

```sql
tokenize='trigram'
```

Classification: **RETAIN**. This is unrelated to `simple` despite the historical reuse of the `messages_fts_trigram` name.

### H. Modern session trigram classifier — RETAIN, never broaden

`hermes_state.py:L3225-L3485`

Final #30 determines same-name session-trigram identity from normalized stored DDL, not `PRAGMA table_info`, so schema identity is decidable without connecting the vtable/tokenizer.

Current classifier states are only:

- `absent`;
- `modern_trigram`;
- `unknown_same_name`.

There is intentionally **no `legacy_simple` state**. A modern root requires exact FTS5 `(title,id,display_name)`, `content='sessions_fts_trigram_src'`, `content_rowid='row_id'`, `tokenize='trigram'`, and a compatible source VIEW. Anything else fails closed.

Classification: **RETAIN** exactly this modern/unknown boundary. #19 must not re-add a supported `legacy_simple` branch merely to delete historical residue.

### I. Generic destructive FTS repair — RETAIN / REUSE

`hermes_state.py` destructive FTS repair / owned-derived-schema teardown section at the pinned base.

The repair model already treats FTS as derived data: detach/drop Hermes-owned derived schema while preserving canonical rows, then rebuild current indexes from `sessions` / `messages`. The session-trigram path deliberately refuses unknown same-name ownership.

Classification: **RETAIN / REUSE**. #19 may add a narrower historical-simple signature recognizer for the EOL sanitation entry point, but must not weaken generic unknown-object ownership rules.

### J. Offline session recovery — RETAIN; no simple source dependency

`hermes_cli/session_recovery.py:L1-L65`, plus `_copy_state_meta()` around `L620+`.

Recovery guarantees:

- source database is never opened by SQLite directly;
- source bundle is copied to disposable storage;
- canonical rows are copied into a fresh current-schema destination;
- derived FTS schema and FTS transition metadata are rebuilt, not copied;
- `_GENERATED_META_KEYS` excludes all FTS lifecycle markers from canonical metadata transfer.

Classification: **RETAIN**. A source carrying simple residue does not justify loading simple. Add a #19 regression proving the residue does not cross the canonical-copy boundary.

### K. Backup / import — RETAIN; sanitation belongs to first writable open

`hermes_cli/backup.py` snapshots SQLite databases using SQLite backup semantics and excludes live journal sidecars. It does not load `simple` directly. Import restores files; the next `SessionDB` open owns schema convergence.

Classification: **RETAIN** backup/import behavior. Add a regression for backup/import of a disposable historical-simple fixture followed by a writable EOL-clean open; do not teach archive code tokenizer semantics.

### L. Tests / fixtures — RETAIN tombstones, REMOVE compatibility expectations

`tests/test_session_metadata_trigram_fts.py:L1+` now specifies modern `tokenize='trigram'`, normalized external content, and unknown-same-name fail-closed behavior. Final #30 scope correction explicitly deleted the historical `legacy_simple` classifier/migration tests.

Classification:

- modern trigram / unknown-shape fixtures: **RETAIN**;
- new exact historical-simple fixtures: **ADD as EOL tombstones only** (prove safe removal without `libsimple`);
- tests asserting legacy-simple → modern as a supported compatibility migration: **DO NOT REINTRODUCE**.

`tests/state/test_fts_runtime_rebuild.py` covers modern derived-index corruption self-heal; retain it as evidence that canonical data can rebuild derived FTS without legacy tokenizer support.

### M. Docs / config compatibility surface — REMOVE

Runtime `HERMES_LIBSIMPLE_PATH`, `~/.hermes/libsimple/libsimple.so`, and comments stating that normal open/read must support simple-backed vtables are part of the retired shim.

Classification: **REMOVE** these compatibility promises. Historical research/issue records may retain the term as archaeology; current operator documentation must not imply `libsimple` is required.

---

## Supported legacy-state matrix

| State at final #12 boundary | Supported? | Needs `simple`? | #19 action |
|---|---:|---:|---|
| Fresh/current schema; storage-v2 complete | Yes | No | Remove shim; no migration |
| Supported pre-v23 message `messages_fts` layout | Yes | No | Existing shape-driven demote/rebuild |
| Supported pre-#25 session Unicode internal-content layout | Yes | No | Existing session Unicode migration |
| Supported pre-#26 session CJK internal-content layout | Yes | No | Existing optional-CJK migration/degradation |
| Exact modern #30 `sessions_fts_trigram(tokenize='trigram')` | Yes | No | Keep modern capability/quarantine lifecycle |
| Historical fork `sessions_fts_trigram(tokenize='simple')` | **No** | No permanent support | Exact-signature EOL sanitation only; preserve canonical rows |
| Historical fork message simple-backed residue | **No** | No permanent support | Exact-signature EOL sanitation only; preserve canonical rows |
| Unknown / foreign same-name FTS object | No ownership claim | No | Fail closed, untouched, actionable diagnostic |
| Offline recovery source carrying simple residue | Recovery input may contain it | No | Copy canonical rows only; rebuild fresh derived state |
| Backup/import containing raw simple residue | Archive may contain it | No | Preserve archive semantics; sanitize on first writable open |

**Answer to question 1:** zero supported post-#12 states need `simple` at open or write time.

---

## What proves the shim is safe to remove?

No single `SCHEMA_VERSION` value is sufficient.

At the pinned base:

```text
SCHEMA_VERSION = 25
FTS_STORAGE_VERSION = 2
```

`fts_storage_version=2` is a strong positive proof that the owned six-index modern layout has settled, and therefore no supported owned index uses `simple`.

But marker absence / storage-v1 is still a supported legacy condition for some message/session migrations. Therefore #19 must use **version + stored schema shape**, not `schema_version` alone:

1. storage-v2 + exact modern owned DDL → definitely no simple dependency;
2. recognized supported legacy message/session shapes → existing tokenizer-independent migration path;
3. exact historical Hermes `tokenize='simple'` signature → unsupported derived residue, safe only for narrowly owned EOL sanitation;
4. anything else / near-match → unknown, fail closed, never delete by name.

This gives a removal proof without keeping `libsimple` installed forever.

---

## Proposed EOL sanitation seam

The smallest safe shape is a pre-init writable sanitation pass that reads only `sqlite_master.sql` and never connects an obsolete vtable.

For an **exact historical Hermes simple signature only**:

1. acquire the same write/connection lifecycle protections used by current schema maintenance;
2. remove/drop only known Hermes sync triggers that can poison canonical writes;
3. structurally detach the recognized root vtable declaration using the already-proven `writable_schema` demotion technique;
4. remove or rename only its known derived shadow objects as ordinary derived trash;
5. leave canonical `sessions`, `messages`, IDs, relationships, and non-FTS metadata untouched;
6. continue into the existing current-schema ensure / H-P rebuild lifecycle.

No new permanent marker or state machine should be needed: the canonical tables are the source of truth and current ensure/rebuild is already restartable. If implementation discovers that a crash boundary cannot be made idempotent with the existing structural demotion/rebuild primitives, **stop and escalate that concrete invariant** instead of inventing a second legacy lifecycle.

Unknown same-name objects remain outside this sanitizer.

---

## RED test matrix for #19

Create a focused file such as `tests/test_simple_tokenizer_eol.py`.

Before production code, pin at least:

1. **message simple residue, tokenizer absent**
   - disposable historical Hermes-owned simple root/shadows/triggers;
   - no `libsimple.so` / invalid `HERMES_LIBSIMPLE_PATH`;
   - writable open does not raise `no such tokenizer: simple`;
   - canonical message rows/ids/content unchanged;
   - current message FTS is rebuilt or left in an explicit resumable modern claim.
2. **session simple residue, tokenizer absent**
   - exact historical Hermes-owned session simple residue;
   - canonical session row_id/text id/title/display_name unchanged;
   - residue removed as EOL debt, not classified as a supported #30 legacy migration;
   - modern trigram creates/serves when runtime capable, otherwise follows current modern fallback/quarantine policy.
3. **unknown same-name negative control**
   - unicode61/foreign VIEW/INDEX/near-match shapes survive byte-for-schema unchanged;
   - no broad name-based deletion.
4. **loader is truly gone**
   - missing `HERMES_LIBSIMPLE_PATH` cannot affect fresh/current writable or read connections;
   - no `_simple_loaded` propagation.
5. **health/repair path**
   - `_db_opens_cleanly` / destructive repair do not require simple;
   - exact EOL residue gets the explicit repairable/unsupported outcome, not a hidden successful probe because a developer happens to have `libsimple.so` installed.
6. **interruption/reopen**
   - interrupt after structural detach but before current-schema ensure;
   - reopen converges from canonical rows without restoring simple or corrupting row identities.
7. **offline recovery**
   - source contains historical-simple derived schema/markers;
   - destination copies canonical rows only and contains no simple residue.
8. **backup/import**
   - raw backup/import may preserve the historical DB file;
   - first writable open EOL-cleans it without canonical loss.
9. **read-only unsanitized policy**
   - explicit read-only open does not load simple;
   - fail/degrade result is actionable and deterministic.

Keep the historical simple fixture after EOL lands: it is a tombstone proving the retired schema can never silently become a runtime dependency again.

---

## Ordered implementation commits

1. `test(fts): pin simple-tokenizer EOL policy`
   - RED exact message/session simple tombstones, unknown negative controls, tokenizer-missing open/health behavior.
2. `fix(fts): sanitize retired simple schema before init`
   - exact stored-DDL signature recognizer;
   - structural derived-schema detach using existing demotion primitives;
   - canonical preservation and restartability;
   - no new supported legacy classifier/state machine.
3. `refactor(fts): remove global simple extension shim`
   - delete `simple_tokenizer_so_path`, `load_simple_extension`, `_simple_loaded`, writer/read/health callers, `HERMES_LIBSIMPLE_PATH` compatibility comments/config surface.
4. `test(fts): cover simple EOL maintenance and portability`
   - optimize/destructive repair/recovery/backup-import/interruption coverage.
5. `docs(fts): close simple compatibility contract`
   - current docs/comments say unsupported residue is sanitized/fails closed; historical research remains historical.

---

## Validation

Start narrow:

```bash
uv run pytest tests/test_simple_tokenizer_eol.py -q
uv run pytest tests/test_session_metadata_trigram_fts.py -q
uv run pytest tests/state/test_fts_runtime_rebuild.py -q
```

Then lifecycle/storage/recovery:

```bash
uv run pytest tests/test_fts_lifecycle_registry.py tests/test_fts_storage_v2_settlement.py -q
uv run pytest tests/hermes_cli/test_session_recovery.py -q
```

Finally run the repository's broader SessionDB / CLI FTS suite and Ruff over every touched Python/test file.

Acceptance probes on the disposable tombstone DB should additionally record before/after canonical counts and identities (`sessions.row_id/id`, `messages.id/session_id/content`) and verify no stored `sqlite_master.sql` declaration contains the retired Hermes `tokenize='simple'` signature afterward.

---

## Rollback / safety

- Never test destructive EOL behavior against the production DB; use disposable copies/fixtures.
- The sanitation target is **derived FTS only**. Canonical rows are not rewritten as part of #19.
- A code rollback must not require reinstalling `libsimple`: after sanitation, current indexes rebuild from canonical data.
- Unknown same-name objects are never touched, so rollback cannot depend on reconstructing foreign schema.
- Do not bump `SCHEMA_VERSION` or `FTS_STORAGE_VERSION` merely to record that unsupported residue was deleted. If implementation proves a durable new state is actually required for crash safety, pause and raise that as a policy/design change.

---

## Upstream / ancestry audit

A current upstream search found no `load_simple_extension`, `HERMES_LIBSIMPLE_PATH`, or equivalent `simple` compatibility surface in `NousResearch/hermes-agent`. The global loader is therefore fork-local debt, not a merged-upstream contract to preserve.

Reuse accepted upstream-derived lifecycle ideas already present in the fork (#76832-style crash-safe H/P demotion/rebuild, #71933 health probing, #73431 lifecycle consistency, #77629 optional-tokenizer degradation). There is no clean upstream cherry-pick that implements #19's fork-specific simple EOL.

---

## `/implement #19` handoff

#19 is implementation-ready on this research result.

The key constraint is intentionally narrow:

> Remove the global simple-tokenizer compatibility shim and structurally sanitize only exact Hermes-owned historical simple residue. Do not turn unsupported fork history back into a supported migration state, and do not weaken unknown-schema fail-closed ownership.

Implementation should verify the pinned seams against current `dev`, create RED tombstone fixtures first, and then execute the five commit-sized steps above.