# #32 research — stable `sessions.row_id` + resumable Unicode session metadata FTS

Status: **complete research handoff for #25**  
Research code base: **`CODE_BASE_SHA=9f3056cb3a9642c39056af8409cc3198007e68a8`** (`dev` at exploration start)  
Scope: research only; this note does **not** implement #25.

## Decision

**Proceed with #25 as one implementation ticket. No new child split/blocker was found.**

The storage-identity migration and Unicode external-content rebuild are coupled by the same `row_id`: the named integer key is the FTS `content_rowid`, the high-water boundary is measured in that key, and live-trigger ownership depends on newly allocated keys being greater than the captured high-water. Splitting those into separate production tickets would create an intermediate schema that has no independently useful or safe contract.

Existing downstream tickets remain the intended split boundaries:

- #25: named `row_id` + **raw Unicode** `sessions_fts(title,id,display_name)` + crash-safe H/P migration.
- #26: CJK capability/index policy.
- #30: modern trigram / normalized arbitrary infix.
- #27: unified FTS lifecycle / storage-version settlement.

Do **not** absorb those later concerns into #25 merely because the donor branch bundled them.

---

## 1. Primary-source facts that constrain the design

### 1.1 A named `INTEGER PRIMARY KEY` is the stable row identity

SQLite's rowid documentation says an `INTEGER PRIMARY KEY` aliases the underlying rowid, while an unnamed rowid may change (notably under `VACUUM`). SQLite's `VACUUM` documentation repeats that rowids may change for tables without an explicit `INTEGER PRIMARY KEY`.

Primary docs:

- https://www.sqlite.org/rowidtable.html
- https://www.sqlite.org/lang_vacuum.html

Therefore #25's desired shape is structurally correct:

```sql
row_id INTEGER PRIMARY KEY AUTOINCREMENT,
id     TEXT NOT NULL UNIQUE
```

`row_id` becomes the storage/document identity. `id` remains the logical/public session identity used by APIs and text foreign keys.

### 1.2 `AUTOINCREMENT` is useful here for the high-water ownership invariant

SQLite documents that `AUTOINCREMENT` prevents reuse of previously committed rowids and makes automatically chosen rowids monotonically increasing (not necessarily gap-free):

- https://www.sqlite.org/autoinc.html

That is exactly the property the rebuild needs after capture of `H = MAX(row_id)`: a newly-created session can be treated as live-owned because its automatically allocated `row_id` will be `> H`.

The migration must still explicitly preserve *legacy* hidden rowids. `AUTOINCREMENT` does not do that for us.

### 1.3 External-content FTS delete operations are index maintenance, not ordinary table DELETEs

SQLite FTS5 documents the special `'delete'` command for external-content/contentless tables and `'delete-all'` for resetting their index contents:

- https://www.sqlite.org/fts5.html

This matters during a partial rebuild. A canonical session in `(P,H]` has not yet been indexed, so its DELETE/UPDATE trigger must **not** send a special external-content delete for a document that the index never owned. The current accepted message-FTS gate already encodes the correct region model.

---

## 2. Pinned current-code map (`9f3056c`)

### 2.1 Canonical `sessions` table still uses a hidden rowid

`hermes_state_common.py` around lines 194-247:

https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state_common.py#L194-L247

Current shape starts with:

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    ...
)
```

There is no named integer key. Relationships continue to use text IDs, e.g. `messages.session_id TEXT NOT NULL REFERENCES sessions(id)` in the same schema. That relationship model should remain unchanged.

**Required #25 change:** fresh schema becomes `row_id INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, ...`; child/text relationships continue referencing `sessions(id)`.

### 2.2 Current Unicode session FTS is internal-content, title-only, hidden-rowid based, and broadly updated

`hermes_state_common.py` lines 551-581:

https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state_common.py#L551-L581

Current `SESSIONS_FTS_SQL`:

- stores only `title`;
- has no `content='sessions'` / `content_rowid`;
- writes `new.rowid` / `old.rowid`;
- runs `AFTER UPDATE ON sessions` for every session update;
- uses ordinary `DELETE FROM sessions_fts` because the table is currently internal-content.

**Required #25 replacement (Unicode only):**

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    title,
    id,
    display_name,
    content='sessions',
    content_rowid='row_id',
    tokenize='unicode61'
);
```

The three indexed columns are the **raw canonical values** from `sessions`; do not introduce title normalization, compacted IDs, concatenated documents, or another shadow copy here.

### 2.3 The accepted message FTS trigger gate is the structural authority

`hermes_state_common.py` lines 402-450:

https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state_common.py#L402-L450

Current message external-content FTS already implements:

```text
indexed by live trigger iff row_id/id > H OR <= P
```

and a narrow `AFTER UPDATE OF ...` plus value-change guard.

#25 should adapt this exact invariant to `sessions.row_id` and Unicode columns:

```text
<= P      indexed historical prefix; ordinary external-content maintenance is valid
(P,H]     historical worker owns it; triggers must leave FTS alone
> H       post-capture live row; triggers own it immediately
```

Session trigger shape should be:

- INSERT gate on `NEW.row_id > H OR NEW.row_id <= P`;
- DELETE gate on `OLD.row_id > H OR OLD.row_id <= P`;
- `AFTER UPDATE OF title, id, display_name` with an `IS NOT` value-change guard and the same indexed-region gate;
- external-content special `'delete'` carrying the old raw `(title,id,display_name)` followed by insert of the new raw values.

Do **not** add manual FTS deletes in `delete_session*`, prune, delegate cleanup, etc. All canonical `DELETE FROM sessions ...` paths should flow through one correct trigger contract.

### 2.4 Startup currently performs a blocking one-shot Unicode backfill

`hermes_state_schema.py` lines 1027-1051:

https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state_schema.py#L1027-L1051

Startup ensures `sessions_fts` and then calls `_backfill_sessions_fts()`.

The current helper is in `hermes_state.py` lines 2356-2395:

https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state.py#L2356-L2395

Its Unicode half is one statement plus immediate commit:

```sql
INSERT OR IGNORE INTO sessions_fts(rowid, title)
SELECT _rowid_, COALESCE(title, '')
FROM sessions
WHERE title IS NOT NULL;
```

That has no durable H/P ownership, no resume point, and no bounded-gap search. #25 should retire this **Unicode one-shot** path. Do not use the ticket as an excuse to redesign the CJK half; #26 owns that.

### 2.5 `_init_schema()` has the right ordering seam for a table-shape heal

`hermes_state_schema.py` around lines 568-620:

https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state_schema.py#L568-L620

The current init flow is roughly:

```text
SCHEMA_SQL
→ _reconcile_columns()
→ dedicated PK/table-shape heals
→ indexes / migrations / FTS ensure
```

`_reconcile_columns()` cannot ALTER an existing primary key. Existing `gateway_routing` and `session_model_usage` helpers already establish the repo pattern: PK shape changes need a dedicated rebuild/heal.

For #25, insert a dedicated `sessions` row-id migration **before `_reconcile_columns()`** (same intended seam as the donor), but use the safer migration described in section 4 below.

### 2.6 Accepted crash-safe message rebuild machinery already exists; generalize/reuse it

`hermes_state_search.py`:

- `fts_rebuild_status()` / `_fts_rebuild_finish()` / `fts_rebuild_step()` around lines 76-267:
  https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state_search.py#L76-L267
- #76832 repair/seed seam (`_fts_external_index_empty_with_messages`, `_fts_index_known_empty`, `_reset_fts_index_to_empty`, `_seed_fts_rebuild_markers`, `_repair_optimize_bookkeeping`) around lines 360-550.
- optimize driver and its currently-nested throttle around lines 665-770:
  https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state_search.py#L665-L770

The current chunk contract is already the one #25 needs:

```text
BEGIN IMMEDIATE
re-read P
upper = min(P + chunk_rows, H)
insert canonical rows where P < id <= upper
advance P in the same transaction
COMMIT
```

The existing finish also performs a narrow anti-join boundary sweep before deleting markers.

**Do not fork this into an independent session-only recovery model.** Refactor the accepted machinery just enough that the same crash/recovery rule can operate with:

- source table / row key (`messages.id` vs `sessions.row_id`),
- target FTS table and canonical columns,
- marker names,
- index-empty/reset probes,
- finish sweep.

Thin session wrappers/specification are fine; duplicated recovery semantics are not.

### 2.7 Shared pacing constants exist, but the pause implementation is not yet shareable

`hermes_state.py` around lines 2877-2879 defines:

```python
_FTS_REBUILD_CHUNK_ROWS = 500
_FTS_REBUILD_DUTY_FACTOR = 4.0
_FTS_REBUILD_MIN_PAUSE = 0.2
```

https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state.py#L2854-L2891

`optimize_fts_storage()` currently has a **nested** `_pause(chunk_seconds)` implementing:

```python
time.sleep(max(
    self._FTS_REBUILD_MIN_PAUSE,
    chunk_seconds * self._FTS_REBUILD_DUTY_FACTOR,
))
```

For #25's “shared pause implementation” acceptance criterion, extract this into one monkeypatchable method/helper (for example `_fts_rebuild_pause(chunk_seconds)`) and route both existing message phases and the Unicode session phase through it. Do not create session copies of `500`, `4.0`, `0.2`, or the formula.

### 2.8 User-visible session search currently has multiple lanes that must not be conflated

`hermes_state.py` around `list_sessions_rich()` (roughly lines 5680+ in the pinned file) already has a `search_query` path that performs case-insensitive `%LIKE%` matching against title/id plus a punctuation-stripped variant. It currently does **not** cover `display_name`.

`resolve_session_by_title()` / `_fts_numbered_variants()` are a separate title-lineage surface around lines 5491-5575:

https://github.com/Skywind5487/hermes-agent/blob/9f3056cb3a9642c39056af8409cc3198007e68a8/hermes_state.py#L5491-L5575

#25 should establish a reusable raw-Unicode metadata-candidate helper over `(title,id,display_name)` plus bounded `(P,H]` supplementation. It should also update any Unicode session-FTS joins to use `sessions.row_id` instead of hidden `_rowid_`.

**Scope guard:** do not delete/regress the existing normalized/infix fallback behavior just to claim the FTS migration is “used”. #30 owns the final normalized arbitrary-infix architecture. #25's new raw Unicode lane can coexist with the legacy fallback until #30 replaces that fallback deliberately.

---

## 3. Donor branch audit (`fts/session-title-external-content`)

Donor head examined: `697210b6fa2dc566bdde744dad1b1b528ca6bec4` (two commits ahead of the research base).

Treat this branch as a parts bin only.

### Useful donor material

- Desired fresh-table shape already uses `row_id INTEGER PRIMARY KEY AUTOINCREMENT` and `id TEXT NOT NULL UNIQUE`.
- It identified the correct init seam: table rebuild before `_reconcile_columns()`.
- It introduced focused `tests/test_session_title_fts.py` fixtures that can be rewritten for #25.
- It contains a bounded-gap search prototype and session rebuild plumbing that are useful for failure-case discovery.

### Do **not** copy its row-id migration

Donor `_maybe_rebuild_sessions_row_id()` says it preserves rowids by copying rows `ORDER BY rowid`, but its actual insert omits the destination `row_id`:

https://github.com/Skywind5487/hermes-agent/blob/697210b6fa2dc566bdde744dad1b1b528ca6bec4/hermes_state_schema.py#L420-L485

Conceptually it does:

```sql
INSERT INTO sessions_new (<shared columns except row_id>)
SELECT <same columns>
FROM sessions
ORDER BY rowid;
```

That only preserves order. It **densifies holes** because the new AUTOINCREMENT key is allocated afresh.

Counterexample:

```text
legacy: rowid 1=A, 2=deleted, 3=B
required: row_id 1=A, 3=B
donor:    row_id 1=A, 2=B   ← wrong identity
```

Correct copy must explicitly write the old hidden rowid:

```sql
INSERT INTO sessions_new (row_id, ...)
SELECT rowid, ...
FROM sessions;
```

### Donor table swap is also too crash-fragile for the #25 contract

The donor performs create/copy/drop/rename as separate autocommit statements. A process death after dropping `sessions` but before renaming the populated replacement can leave an unsafe intermediate persistent schema.

#25 should perform the replacement in one explicit transaction with foreign-key enforcement toggled **outside** that transaction:

```text
PRAGMA foreign_keys=OFF
BEGIN IMMEDIATE
  DROP stale sessions_new if any
  CREATE sessions_new (... desired schema ...)
  INSERT sessions_new(row_id, ...) SELECT rowid, ... FROM sessions
  verify counts / identities
  DROP old sessions
  ALTER TABLE sessions_new RENAME TO sessions
  recreate required indexes
COMMIT
PRAGMA foreign_keys=ON
PRAGMA foreign_key_check
```

SQLite DDL participates in explicit transactions; avoid `executescript()` inside this critical swap because this codebase's comments correctly note that `executescript()` can introduce transaction boundaries that defeat the intended `BEGIN IMMEDIATE` ownership.

Preflight defensively for pathological legacy `id IS NULL` rows before tightening `id` to `NOT NULL`; fail without mutating rather than silently inventing an ID.

### Do **not** copy donor FTS scope

Donor `SESSIONS_FTS_SQL` is still **title-only**:

https://github.com/Skywind5487/hermes-agent/blob/697210b6fa2dc566bdde744dad1b1b528ca6bec4/hermes_state_common.py#L620-L665

It also mixes CJK and unified lifecycle/storage-v2 work into the same branch. #16/#25 supersede that shape:

```text
Unicode document = raw (title, id, display_name)
CJK = #26
normalized/trigram = #30
unified lifecycle/storage settlement = #27
```

### Donor test gaps that #25 must add/fix

Donor `tests/test_session_title_fts.py` is a useful starting fixture, but it does **not** prove the final acceptance contract. In particular:

1. legacy migration fixture uses dense rows; it does not pin a deleted-row hole;
2. Unicode FTS tests are title-only, not `(title,id,display_name)`;
3. no three-region DELETE test (`<=P`, `(P,H]`, `>H`) with FTS5 `integrity-check`;
4. no partial-index `H present / P missing` orphan recovery test;
5. no crash injection around durable claim → empty external schema → first chunk;
6. no concurrent two-runner chunk-claim test;
7. no shared-throttle monkeypatch test;
8. unified CJK/lifecycle tests are explicitly out of #25 scope.

Donor test file:

https://github.com/Skywind5487/hermes-agent/blob/697210b6fa2dc566bdde744dad1b1b528ca6bec4/tests/test_session_title_fts.py

---

## 4. Upstream ancestry / reuse decision

### NousResearch/hermes-agent#76832 — **behavioral authority; already in ancestry; do not cherry-pick**

Merged PR:

https://github.com/NousResearch/hermes-agent/pull/76832

Merge commit: `1e2e69db989066047e5fce2cc0a0c24b24633c9f`.

Repository compare proved that merge commit is an ancestor of `CODE_BASE_SHA`; the fork is hundreds of commits ahead of it.

Reuse its accepted invariants:

- durable backfill claim before an empty external index can look complete;
- detect empty-external-index/no-marker orphan state;
- if `H` exists but `P` is missing, do **not** blindly replay from zero onto a maybe-partial index;
- either recover a proven boundary or reset/prove the derived index known-empty first;
- FTS5 `'delete-all'` is the reset seam for an external-content index;
- schema ensure after a staged claim must be resumable if the process dies between those boundaries.

#25 should **generalize/reuse** these rules for session Unicode metadata; it should not invent parallel semantics.

### NousResearch/hermes-agent#81043 — **not in ancestry; exact scope is trash teardown; no cherry-pick for #25**

Merged PR:

https://github.com/NousResearch/hermes-agent/pull/81043

Merge commit: `23dce021a5fd5540f88e6845014c44a05866d1a5`.

Compare against the research base is diverged; this merge is not currently in fork `dev`. Its concrete change is high-water optimization for **demoted FTS trash teardown**.

#25 does not need to touch the message v22 trash teardown path. Therefore **do not cherry-pick #81043 as part of #25**. If implementation unexpectedly edits `_fts_teardown_trash_step()` / trash-table accounting, stop and re-evaluate; at that point #81043 becomes exact-overlap prior art and should be cherry-picked/reused first.

### Fork donor — **no cherry-pick**

Donor is unmerged local prior art and conflicts with current authority on rowid preservation, raw metadata fields, crash/recovery reuse, and ticket scope. Port only individually justified tests/helpers after rewriting them to #25's contract.

---

## 5. Implementation design for #25

### 5.1 Migration phase A — durable named session key

Desired fresh schema:

```sql
CREATE TABLE sessions (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    ...,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id),
    ...
);
```

Legacy conversion rules:

1. detect absence of named `row_id` by `PRAGMA table_info('sessions')`;
2. before mutation, capture `{id -> hidden rowid}` (or verify with SQL after copy);
3. FK-off outside transaction;
4. one `BEGIN IMMEDIATE` transaction for create/copy/drop/rename/index recreation;
5. **explicitly copy `rowid -> row_id`**;
6. verify row count and exact `(id,row_id)` identity before dropping/committing;
7. re-enable FK and run `PRAGMA foreign_key_check`;
8. after migration, `VACUUM` must preserve all named `row_id` values;
9. fresh inserts omit `row_id` and use AUTOINCREMENT normally.

Do not rewrite any text relationship to use `row_id`. `messages.session_id`, `parent_session_id`, routing/accounting tables, and public APIs remain keyed by text `id`.

### 5.2 Migration phase B — claim Unicode rebuild before empty external index can exist

Use dedicated marker names, shared semantics:

```text
fts_session_rebuild_high_water = H
fts_session_rebuild_progress   = P
```

When converting an existing internal-content `sessions_fts`:

1. in a write transaction, capture `H = COALESCE(MAX(sessions.row_id), 0)`;
2. persist **both H and P=0** as the durable rebuild claim;
3. disable/drop old Unicode session FTS triggers and remove the old internal-content table as part of a recoverable staged transition;
4. commit the claim/stage;
5. only then ensure the new empty external-content `sessions_fts` schema and gated triggers.

If the process dies after step 4 but before step 5, reopen sees durable markers and recreates the external table before trying a chunk. It must not stamp the migration as complete.

If old buggy state is observed with empty external `sessions_fts`, canonical session rows, and no markers, the shared #76832 repair seam should establish a full rebuild claim.

If `H` exists and `P` is missing:

```text
if a proven durable boundary can be recovered: use it
else: prove/reset sessions_fts to known-empty, then set P=0
```

Never set P=0 over a maybe-partial index.

### 5.3 Chunk engine — one shared crash/recovery rule

For each Unicode session chunk:

```text
BEGIN IMMEDIATE
  re-read durable P
  if P >= H: nothing to claim
  upper = min(P + _FTS_REBUILD_CHUNK_ROWS, H)
  INSERT sessions_fts(rowid,title,id,display_name)
    SELECT row_id,title,id,display_name
    FROM sessions
    WHERE row_id > P AND row_id <= upper
  UPDATE progress = upper
COMMIT
```

Important details:

- row-id *range* is the claim unit; deleted gaps are normal;
- raw NULL `title`/`display_name` values are allowed; `id` is non-null so every surviving session has a searchable Unicode document;
- do not `COALESCE` canonical columns into a synthetic FTS document;
- progress and FTS inserts are one transaction;
- concurrent callers serialize on `BEGIN IMMEDIATE`, then re-read P inside that transaction;
- on retry, a committed P is never silently reset.

### 5.4 Live triggers — enforce the ownership regions

Conceptual gate:

```sql
WHEN NEW/OLD.row_id > COALESCE(H, -1)
  OR NEW/OLD.row_id <= COALESCE(P, -1)
```

On a fresh fully-settled DB the markers are absent; `H=-1`, so every positive `row_id` is trigger-owned.

On a partial rebuild:

- `<=P`: already indexed, trigger INSERT/DELETE/UPDATE normally;
- `(P,H]`: historical worker owns it; trigger does nothing;
- `>H`: live-created after capture; trigger maintains it immediately.

This automatically makes all existing canonical delete paths correct without modifying each caller:

- deleting `<=P` emits special FTS delete;
- deleting `(P,H]` emits no FTS delete and the later range SELECT simply does not see the row;
- deleting `>H` emits special FTS delete.

### 5.5 Bounded historical-gap search

Add/reuse one helper that reads H/P and returns a gap only when migration is pending:

```text
(P, H]
```

Raw Unicode metadata candidate retrieval should:

1. query `sessions_fts MATCH ?` for indexed candidates;
2. query **only** canonical `sessions` rows whose `row_id > P AND row_id <= H` for the temporary historical gap;
3. match the same raw metadata dimensions (`title`, `id`, `display_name`) without introducing #30 normalization policy;
4. merge/deduplicate by logical session `id` (or stable `row_id` internally, then map to id);
5. keep all existing visibility/source/lineage filtering downstream rather than widening it in the migration helper.

Do not fall back to an unbounded all-sessions migration scan. The whole purpose of H/P is to make incompleteness explicit and bounded.

Existing normalized `%LIKE%`/punctuation fallback in `list_sessions_rich(search_query=...)` is a separate pre-#30 behavior. Preserve it until #30 deliberately replaces it; do not make #25 a hidden behavior regression.

### 5.6 Finish — boundary sweep, then clear markers atomically

Adapt the accepted message `_fts_rebuild_finish()` pattern:

1. read H inside the finishing write transaction;
2. inspect a narrow window around H;
3. insert canonical session documents missing from `sessions_fts_docsize`;
4. only after the sweep, delete the session H/P markers in the same transaction.

The anti-join avoids duplicate documents and catches a write that slipped at the claim/trigger boundary. Completed migration must reopen with no pending session markers and must **not** re-seed itself.

### 5.7 Shared throttle

Extract the current nested throttle into a single testable helper and use it for all chunk loops:

```python
pause = max(
    self._FTS_REBUILD_MIN_PAUSE,
    chunk_seconds * self._FTS_REBUILD_DUTY_FACTOR,
)
```

Tests should monkeypatch `time.sleep` / timing surface and assert the requested duration. No real sleeps.

---

## 6. Exact test plan / gaps

Recommended focused file: `tests/test_session_metadata_fts.py` (or rewrite donor `tests/test_session_title_fts.py` under the new metadata name).

Keep existing title/lineage regressions in `tests/test_hermes_state.py` intact; current title behavior around `TestSessionTitle`, title uniqueness, lineage transfer, and `resolve_session_by_title()` is existing compatibility coverage.

### Group A — row identity migration

1. **RED: deleted-row hole is preserved exactly**
   - hand-build legacy `sessions` with explicit hidden rowids `1=A`, `3=B`, optionally `7=C`;
   - open with new code;
   - assert `{A:1, B:3, C:7}` exactly, not `[1,2,3]`.
2. assert `PRAGMA table_info` reports `row_id` as integer PK and `id` is non-null+unique.
3. parent/child `parent_session_id`, `messages.session_id`, model-usage/routing references still resolve by text id.
4. capture all `(id,row_id)`, run `VACUUM`, assert byte-for-value equality of the mapping.
5. insert a fresh session after migration and assert its `row_id > MAX(legacy row_id)`.
6. inject failure during the table-swap transaction and reopen; assert either old layout+all rows or new layout+all rows, never an empty replacement/orphaned data table.

### Group B — raw Unicode external content

1. fresh `sessions_fts` SQL contains `content='sessions'` and `content_rowid='row_id'`;
2. search each dimension independently: title, logical `id`, `display_name`;
3. raw Unicode token behavior only; do not encode #30 normalized arbitrary-infix expectations here;
4. update title/id/display_name and prove old term disappears/new term appears under same `row_id`;
5. unrelated updates (`message_count`, heartbeat/activity, cost/token fields, archived/pinned, etc.) do not rewrite the Unicode FTS document.

For the last case, donor's docsize-count test is too weak because a rewrite can keep document count constant. Prefer tracing/auditing the FTS write surface or comparing a stable shadow/index change signal that proves no FTS DML occurred; if a targeted SQL-shape assertion is necessary, keep it narrowly scoped to `AFTER UPDATE OF title, id, display_name` rather than snapshotting the whole trigger string.

### Group C — crash/restart bookkeeping

1. valid `H,P` survive close/reopen unchanged;
2. `H present / P missing + known-empty index` repairs to P=0 and backfills;
3. `H present / P missing + partially populated index` resets/proves empty before P=0 replay; `integrity-check` healthy afterward;
4. empty external index + canonical sessions + no markers is recognized as orphaned incomplete state, not complete;
5. crash after durable claim but before external schema ensure resumes by re-ensuring schema;
6. crash after one committed chunk resumes from its durable P, not zero;
7. completed migration reopens with no pending claim and no automatic re-seed.

### Group D — trigger ownership and deletes

Create H/P with all three regions represented, then test separately:

1. delete indexed prefix row `<=P`: existing document disappears;
2. delete historical-gap row `(P,H]`: no unsafe FTS delete is issued; later backfill skips the absent canonical row;
3. delete live row `>H`: live document disappears;
4. run FTS5 `integrity-check` after each representative case;
5. repeat mutation coverage for metadata UPDATE where useful;
6. create `>H` session while backfill remains pending and assert it is searchable immediately.

### Group E — bounded-gap search + finish

1. put a matching session only in `(P,H]`; raw Unicode search still returns it through bounded supplementation;
2. put same logical candidate in FTS + supplemental route where boundary overlap is simulated; dedupe to one result;
3. prove supplement SQL is bounded by `row_id > P AND row_id <= H`;
4. finish with a deliberately missing boundary doc; sweep repairs it before markers clear;
5. deleted canonical boundary row is not resurrected.

### Group F — concurrency + throttle

1. open two `SessionDB` instances on the same temp DB and race one Unicode rebuild step;
2. assert progress advances through non-overlapping claimed ranges and all expected documents appear exactly once;
3. `integrity-check` remains healthy;
4. monkeypatch shared timing helper and assert pause is `max(min_pause, build_time * duty_factor)`;
5. prove the session loop calls that **same helper** used by the message rebuild, not a copied policy.

Run focused tests first, then the broader state suite:

```bash
uv run pytest tests/test_session_metadata_fts.py -q
uv run pytest tests/test_hermes_state.py -q
```

Also rerun the existing message optimize/rebuild bookkeeping regressions touched by any generalized helper; #76832 behavior must not regress while extracting shared machinery.

---

## 7. Commit-level implementation plan

The issue's six suggested boundaries are sound after audit; refine them as follows.

### Commit 1 — RED: pin exact legacy storage identity

Tests only:

- hidden-rowid gap fixture (`1,3,7`);
- relationship mapping snapshot;
- VACUUM stability expectation;
- transactional table-swap interruption expectation if practical in the same fixture.

Expected pre-change failure: no named `row_id` / donor-style dense migration would fail exact mapping.

### Commit 2 — migrate `sessions` to named row_id safely

Production:

- `hermes_state_common.py`: fresh schema shape + replacement-table DDL if needed;
- `hermes_state_schema.py`: dedicated pre-reconcile transactional table-shape migration;
- preserve hidden `rowid` explicitly;
- FK pre/post verification;
- leave all text relationships/public API identities unchanged.

No FTS architecture change yet beyond whatever is necessary to keep legacy session-title FTS coherent because the numeric values are unchanged.

### Commit 3 — RED: pin partial-rebuild ownership/recovery/delete behavior

Tests only / failing against current one-shot model:

- H/P restart;
- orphan H-without-P known-empty + partial-index cases;
- claim/schema interruption;
- `<=P`, `(P,H]`, `>H` INSERT/UPDATE/DELETE;
- live `>H` searchability;
- FTS integrity checks.

### Commit 4 — raw Unicode external-content H/P rebuild

Production:

- `hermes_state_common.py`: raw `(title,id,display_name)` external-content Unicode DDL + gated narrow triggers;
- `hermes_state_search.py`: generalize/reuse #76832 claim/repair/step semantics for session markers;
- `hermes_state_schema.py`: replace startup one-shot Unicode backfill with safe staged/resumable migration/ensure;
- `hermes_state.py` / search mixin: extract shared throttle helper and wire session chunks through it;
- **do not** add #26 CJK or #27 storage-v2 lifecycle work.

### Commit 5 — bounded-gap raw search + boundary finish

Production:

- reusable raw Unicode metadata candidate helper over `(title,id,display_name)`;
- bounded `(P,H]` canonical supplement + dedupe;
- update existing Unicode session-FTS joins to `s.row_id = f.rowid`;
- session boundary sweep before H/P marker deletion;
- preserve existing legacy normalized/infix fallback until #30.

### Commit 6 — concurrency/shared-throttle acceptance hardening

Tests + only minimal fixes revealed by them:

- two-runner chunk race;
- same shared pause helper for message and session chunks;
- monkeypatched duty-factor/minimum-pause assertions;
- full reopen/completion check;
- rerun message rebuild tests to prove the generalization did not fork or regress #76832 behavior.

---

## 8. Review checklist / stop conditions

Reject the #25 PR if any of these are true:

- legacy hidden-rowid holes become dense after migration;
- table swap can persist a state where the old canonical `sessions` is gone but replacement is not atomically installed;
- `messages.session_id` or any other existing relationship is rewritten to integer `row_id`;
- Unicode FTS stores only title or stores a synthesized/normalized document instead of raw `(title,id,display_name)`;
- a session-only clone of #76832 recovery logic is introduced instead of a shared/generalized seam;
- `H present / P missing` blindly becomes P=0 on a maybe-partial index;
- empty external session FTS can exist durably with canonical rows and no repair claim while being considered complete;
- `(P,H]` DELETE/UPDATE issues an external-content delete for an unindexed historical document;
- bounded-gap search scans outside `(P,H]` merely to hide an incomplete migration;
- session rebuild copies pacing constants/formula instead of using the shared helper;
- #25 bumps unified storage lifecycle/version or adds CJK/trigram policy owned by #26/#27/#30;
- #81043 is cherry-picked without actually crossing its trash-teardown surface.

---

## 9. Final handoff

**No new blocker found. #25 is implementable from current `dev` after this docs-only research commit.**

Implementation agent should:

1. re-read #25 plus its two pitfall/preflight comments;
2. use this note's `CODE_BASE_SHA` for audited line references;
3. start from latest `dev` (which may now be a docs-only descendant of the pinned SHA) and verify there were no intervening production changes to the mapped seams;
4. create the implementation worktree/branch only then;
5. follow the six commit boundaries above;
6. treat #76832 as accepted behavior already present, donor as evidence only, and #81043 as no-op unless exact trash teardown overlap emerges.
