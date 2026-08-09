# Issue #34 research: normalized session-metadata trigram FTS

> Research-only implementation map for #30. Do **not** treat this note as an implementation of #30.
>
> Pinned code base: `e94f2630a50d7585f78cfc06365753c033113cb9` (`dev` immediately after #25 / PR #59 merged).

## 0. Executive decision

#30 is ready to implement on top of #25 without another exploration split.

The lowest-risk shape is:

1. keep `sessions` canonical and keep `sessions.row_id` as the stable FTS identity;
2. add a non-persistent `sessions_fts_trigram_src` VIEW exposing:
   - compact `title`;
   - raw `id`;
   - compact `display_name`;
3. add modern external-content `sessions_fts_trigram` with built-in `tokenize='trigram'`, `content='sessions_fts_trigram_src'`, `content_rowid='row_id'`;
4. give this target its **own** resumable H/P marker pair and a separate rebuild spec, while reusing #25's generic claim/chunk/finish/repair engine;
5. make one compact policy authoritative: preserve the merged-upstream compact behavior by removing exactly `-`, `_`, `.`, and ASCII space from title/display-name search representations; do **not** promote the current Python `re.sub(r"[\W_]+", "", ...)` into the canonical rule because it is broader than the merged upstream stored-side SQL;
6. converge a same-name historical `sessions_fts_trigram(tokenize='simple')` by schema identity, not name: demote its virtual-table declaration without requiring `simple`, move its shadows to the existing FTS trash namespace, durably claim the modern trigram backfill, then create/resume the new schema;
7. leave global `simple` runtime EOL to #19; #30 owns only the same-name session object because it blocks creation of the modern object;
8. expose a low-level modern trigram candidate lane for #14, but do not move the common picker/listing routing policy into #30.

No persistent `*_search_norm` columns are needed.

---

## 1. Authority and pin

### 1.1 CODE_BASE_SHA

`dev` was verified identical to:

```text
e94f2630a50d7585f78cfc06365753c033113cb9
```

This is PR #59 / issue #25's merge commit. Research-note commits after this pin are documentation-only and do not change the implementation baseline.

### 1.2 Authority order used

1. #12 + final #16 contract + accepted #25 + #30.
2. Merged upstream behavior/seams already in ancestry.
3. Pinned fork source at `e94f2630...`.
4. Fork/local history only for legacy-shape evidence and failure cases.
5. Open upstream only as evidence.

---

## 2. Exact pinned source map

All links below are pinned to `e94f2630a50d7585f78cfc06365753c033113cb9` unless explicitly historical.

### 2.1 Canonical session row and stable identity

- `hermes_state_common.py:L193-L260`
  - `sessions.row_id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `sessions.id TEXT NOT NULL UNIQUE`
  - canonical `title`, `display_name`, and the rest of session state remain on `sessions`.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_common.py#L193-L260
- `hermes_state_schema.py:L380-L520` (`_migrate_sessions_row_id`)
  - preserves exact legacy hidden rowids while rebuilding to named `row_id`;
  - preserves text-ID relationships;
  - transactional swap and FK verification.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_schema.py#L380-L520

#30 must not reopen this identity decision.

### 2.2 Accepted Unicode session FTS substrate

- `hermes_state_common.py:L620-L760` (`SESSIONS_FTS_SQL`)
  - external-content `sessions_fts`;
  - raw `(title, id, display_name)`;
  - `content='sessions'`, `content_rowid='row_id'`;
  - H/P-gated INSERT/DELETE/narrow UPDATE triggers.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_common.py#L620-L760
- `hermes_state.py:L2450-L2710`
  - `_db_has_internal_content_sessions_fts`;
  - `_ensure_sessions_fts_schema`;
  - `_fts_session_schema_transition`;
  - durable claim before empty external schema;
  - trigger-free-window catch-up in one `BEGIN IMMEDIATE` transaction.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state.py#L2450-L2710
- `hermes_state_schema.py:L1110-L1215` (`_init_schema` session FTS setup)
  - runs independently of message v22/v23 state;
  - ensures Unicode session FTS, then optional CJK.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_schema.py#L1110-L1215

### 2.3 Shared rebuild state machine from #25

- `hermes_state_search.py:L31-L82`
  - `_FTS_MESSAGE_SPEC` and `_FTS_SESSION_SPEC` are descriptors for the shared rebuild engine;
  - session spec deliberately has no trigram sidecar today.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_search.py#L31-L82
- `hermes_state_search.py:L250-L650`
  - generic `fts_rebuild_status` / `fts_rebuild_step` / `_fts_rebuild_finish`;
  - `fts_session_rebuild_status` / `fts_session_rebuild_step` wrappers;
  - target/source/row-key driven chunking.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_search.py#L250-L650
- `hermes_state_search.py:L650-L850`
  - `_repair_missing_progress`;
  - known-empty reset before replay;
  - `_repair_session_fts_bookkeeping` orphan claim recovery;
  - `fts_optimize_available` marker/orphan detection.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_search.py#L650-L850
- `hermes_state_search.py:L850-L1030`
  - existing message legacy demotion via `writable_schema`;
  - marker-before-new-schema ordering;
  - foreground optimize resume and phased backfill.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state_search.py#L850-L1030

### 2.4 Current query/search seam

- `hermes_state.py:L5820-L5985`
  - `_session_fts_rebuild_gap`;
  - `_fts_metadata_candidates(raw_query)` for raw Unicode metadata;
  - FTS + bounded `(P,H]` supplement; returns `(fts_ok, candidates)`.
  - This is the closest low-level shape for a trigram candidate lane.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state.py#L5820-L5985
- `hermes_state.py:L6315-L6385` (`list_sessions_rich` search filter)
  - current broad upstream-derived discovery lane;
  - raw title / id / display-name LIKE;
  - compact query uses Python `re.sub(r"[\W_]+", "", ...)`;
  - compact stored-title SQL removes only `-`, `_`, `.`, and ASCII space;
  - compact `display_name` is **not** currently applied in this SQL lane.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state.py#L6315-L6385

This is observable normalization drift, not a helper worth reusing verbatim.

### 2.5 Legacy `simple` compatibility still present

- `hermes_state.py:L1655-L1735`
  - `simple_tokenizer_so_path()`;
  - `load_simple_extension()`;
  - comment explicitly names legacy `messages_fts_trigram` and `sessions_fts_trigram` with `tokenize='simple'`.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/hermes_state.py#L1655-L1735
- SessionDB writer/read connection initialization conditionally loads `simple` so historical schema can currently be touched.

#30 must not delete this global shim; #19 owns global retirement.

### 2.6 Baseline tests to extend

- `tests/test_session_metadata_fts.py:L1-L420`
  - real pre-#25 session table fixture;
  - old internal `sessions_fts` fixture;
  - named-row-id invariants;
  - cross-layout upgrade fixtures.
  - https://github.com/Skywind5487/hermes-agent/blob/e94f2630a50d7585f78cfc06365753c033113cb9/tests/test_session_metadata_fts.py#L1-L420
- The rest of `tests/test_session_metadata_fts.py` already exercises #25 H/P, restart, orphan, gap-supplement, and concurrency rules. Add #30 tests adjacent to this file unless the fixture matrix becomes unwieldy; if it does, split only the trigram/legacy-simple matrix into `tests/test_session_metadata_trigram_fts.py`.

---

## 3. Normalization decision

### 3.1 What merged upstream actually does

Merged upstream PR #57685 / commit `19d4174454624a1ca91bc47b8f2a7ae8c3b4b5d3` is in the pinned base ancestry.

Its `list_sessions_rich(search_query=...)` implementation uses:

```python
compact_needle = re.sub(r"[\W_]+", "", search_needle)
```

but the SQL-side stored title projection is only:

```sql
REPLACE(REPLACE(REPLACE(REPLACE(
  LOWER(COALESCE(title, '')),
  '-', ''), '_', ''), '.', ''), ' ', '')
```

Therefore the pre-#30 fork already contains a mismatch: the query removes more Unicode punctuation than the stored-side SQL. Preserving that mismatch as the new persistent index contract would make representation drift permanent.

Primary upstream behavior source:
- https://github.com/NousResearch/hermes-agent/pull/57685
- merge commit: `19d4174454624a1ca91bc47b8f2a7ae8c3b4b5d3`

### 3.2 Canonical compact policy for #30

Do **not** invent a broader normalization policy in this storage ticket.

Define one explicit policy constant in `hermes_state_common.py`, conceptually:

```text
SESSION_METADATA_COMPACT_SEPARATORS = ("-", "_", ".", " ")
```

Then derive both:

- Python query compacting (`compact_session_metadata_text()`), and
- the SQL expression used to build the derived VIEW,

from that same separator list.

The transform is separator deletion only. It does not persist normalized values.

Case-insensitivity should come from the modern trigram tokenizer's default case-insensitive behavior, not from a second Python-vs-SQL Unicode lowercasing policy. SQLite's built-in trigram tokenizer is case-insensitive by default unless `case_sensitive 1` is explicitly requested.

Why this choice:

- preserves the exact useful merged behavior required by #16 (`AN-94` ↔ `an94`);
- keeps `id` raw, per #16;
- avoids silently expanding the contract to every Unicode punctuation character;
- prevents the current Python/SQL compact mismatch from being copied into the index;
- makes the normalization policy reviewable as data, not scattered regex/REPLACE snippets.

### 3.3 Why not an application-defined SQLite function in the VIEW

A custom `hermes_compact()` SQLite function would look tidy, but SQLite application-defined functions are registered per database connection. Hermes has writer, read-pool, repair/offline, test, and migration connections. A VIEW depending on a custom function would therefore create another capability that every connection must remember to register.

That is unnecessary for four fixed separators. Prefer a pure-SQL VIEW expression generated from the single separator policy constant.

SQLite primary sources:

- external-content FTS may name a table, virtual table, **or view**: https://sqlite.org/fts5.html#external_content_tables
- application-defined functions are registered per connection: https://sqlite.org/appfunc.html
- trigram tokenizer / case-sensitivity behavior: https://sqlite.org/fts5.html#the_trigram_tokenizer

---

## 4. Derived representation and modern DDL

### 4.1 Authoritative DB-side projection

Recommended object:

```sql
CREATE VIEW sessions_fts_trigram_src AS
SELECT
    row_id,
    compact_sql(title)        AS title,
    id                        AS id,
    compact_sql(display_name) AS display_name
FROM sessions;
```

`compact_sql(...)` above is notation for the generated nested `REPLACE()` expression from `SESSION_METADATA_COMPACT_SEPARATORS`; it is not a runtime SQL function.

Then:

```sql
CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5(
    title,
    id,
    display_name,
    content='sessions_fts_trigram_src',
    content_rowid='row_id',
    tokenize='trigram'
);
```

SQLite FTS5 explicitly supports a VIEW as an external-content source. The VIEW keeps canonical state in `sessions` and makes special-command rebuilds/readback use the same derived representation.

### 4.2 Live trigger projection

Avoid retyping normalization literals inside trigger bodies.

Preferred trigger shape uses the VIEW as the DB-side projection:

- AFTER INSERT: insert the new projected row by `row_id` from `sessions_fts_trigram_src`;
- BEFORE DELETE: issue the FTS `'delete'` command from the still-visible old projected row in the VIEW;
- BEFORE UPDATE OF `title,id,display_name`: if values actually changed and row is trigger-owned, delete the old projected row through the VIEW;
- AFTER UPDATE OF `title,id,display_name`: under the same ownership/value-change predicate, insert the new projected row through the VIEW.

Why BEFORE for the delete half: after a canonical DELETE/UPDATE, the old VIEW representation no longer exists. Reading it before mutation avoids duplicating compact SQL in FTS delete payloads.

All four triggers use the same H/P ownership predicate:

```text
row_id <= P  OR  row_id > H
```

Rows in `(P,H]` remain worker-owned. An update/delete in that gap is left to canonical `sessions`; the worker later sees the current surviving row or no row.

If implementation review rejects split BEFORE/AFTER triggers, the fallback is to generate every old-value compact SQL expression from the same `SESSION_METADATA_COMPACT_SEPARATORS` constant. Do not hand-copy separator lists.

---

## 5. Mandatory H/P decision: independent marker pair

### Decision

Modern session trigram needs an **independent** rebuild claim.

Recommended keys:

```text
fts_session_trigram_rebuild_high_water
fts_session_trigram_rebuild_progress
```

and a separate `_FTS_SESSION_TRIGRAM_SPEC` reusing the generic #25 engine.

Do **not** attach `sessions_fts_trigram` as `trigram_fts` inside `_FTS_SESSION_SPEC`.

### Crash-safety invariant

For one target index, `P` means:

> Every canonical row with `row_id <= P` that should exist in this target has been processed into this target's index, and trigger ownership may safely include that region.

`H` separates the historical worker-owned range from post-claim live inserts:

- `row_id <= P`: target index is worker-complete;
- `P < row_id <= H`: historical worker owns it; live triggers skip it;
- `row_id > H`: created after the claim; live triggers own it.

A Unicode target and trigram target cannot safely share `P` because they can be created, demoted, repaired, reset, or resumed independently. If Unicode advances/clears a shared `P` while trigram is missing those rows, the marker would falsely assert trigram completeness and live delete/update triggers could operate on documents that never existed.

The scheduler/chunk algorithm is shared; the **claim state is target-specific**.

### Reuse shape

Add `_FTS_SESSION_TRIGRAM_SPEC` with roughly:

```text
fts_table      = sessions_fts_trigram
source_table   = sessions_fts_trigram_src
source_columns = title,id,display_name
row_key        = row_id
reset_tables   = (sessions_fts_trigram,)
available      = _sessions_trigram_worker/search capability as appropriate
```

Then thin wrappers analogous to #25:

```text
fts_session_trigram_rebuild_status()
fts_session_trigram_rebuild_step()
```

No second scheduler, pacing loop, missing-progress algorithm, or finish algorithm.

---

## 6. Legacy same-name `simple` convergence

### 6.1 Historical exact object

Fork history commit `37811327cd` created:

```sql
CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5(
    title,
    tokenize='simple'
);
```

with three broad session triggers keyed by the then-hidden session rowid. Later CJK work superseded its live search role, but supported DB files can still carry the object. Current `load_simple_extension()` exists specifically so such databases remain openable/touchable.

Historical source:
- https://github.com/Skywind5487/hermes-agent/blob/37811327cd/hermes_state.py

Issue #6 records the local history as:

```text
37811327cd  original sessions simple/trigram
2ac803bd94  CJK path replaces it for live CJK search
f779b5320c  simple loader kept for compatibility
```

### 6.2 Why normal DROP is not a tokenizer-absence migration

Reproduction on Python's SQLite 3.46.1:

1. create an FTS5 table;
2. rewrite its stored tokenizer declaration to an unavailable `simple` tokenizer using `writable_schema` (disposable DB);
3. reopen without `simple` registered.

Observed:

```text
SELECT count(*) FROM sessions_fts_trigram
=> OperationalError: no such tokenizer: simple

DROP TABLE sessions_fts_trigram
=> OperationalError: no such tokenizer: simple
```

So a supported migration cannot be `DROP TABLE IF EXISTS sessions_fts_trigram; CREATE ...` and cannot require `simple` merely to retire `simple`.

### 6.3 Schema classifier

Introduce one exact classifier, conceptually:

```text
absent
legacy_simple
modern_trigram
unknown_same_name
```

Classification must use the stored `sqlite_master.sql`, not table name alone.

`legacy_simple` should require the known Hermes-owned historical shape (FTS5 + `tokenize='simple'`, historical title-only/internal content signature).

`modern_trigram` should require the #30 identity:

- FTS5;
- `tokenize='trigram'`;
- `content='sessions_fts_trigram_src'`;
- `content_rowid='row_id'`;
- logical columns `title`, `id`, `display_name`;
- compatible derived VIEW exists.

Runtime tokenizer capability is separate from schema identity. A host missing built-in trigram may still carry a **modern** schema; it is unavailable on that host, not legacy.

`unknown_same_name` must fail closed / remain unavailable with an actionable diagnostic. Do not delete an arbitrary same-name table that does not match a recognized Hermes-derived shape.

### 6.4 Transition state machine

For `legacy_simple`:

```text
legacy simple vtable
        |
        | BEGIN IMMEDIATE
        |  - drop known legacy sessions_fts_trigram triggers
        |  - PRAGMA writable_schema=ON
        |  - remove only the recognized root vtable declaration
        |  - PRAGMA writable_schema=RESET
        |  - rename remaining sessions_fts_trigram_* shadows
        |    -> fts_v22_trash_sessions_fts_trigram_*
        |  - seed trigram H=max(sessions.row_id), P=0
        | COMMIT
        v
root absent + durable trigram H/P + ordinary trash tables
        |
        | outside that transaction / crash-safe schema transition
        |  - ensure derived VIEW
        |  - create modern external-content trigram vtable
        |  - install gated live triggers
        |  - catch up trigger-owned region
        v
modern trigram, partial but claimed
        |
        | generic #25 chunk worker
        v
finish sweep -> clear trigram H/P -> modern complete
```

This is the same ordering principle as the pinned message-v22 demotion seam in `hermes_state_search.py`: claim before an empty new index can masquerade as complete.

### 6.5 Crash matrix

| Interruption point | Reopen rule |
|---|---|
| before demotion commit | legacy classifier still sees `simple`; rerun stage |
| after demotion + H/P commit, before modern create | root absent + trigram H/P present; re-ensure modern schema, preserve P |
| during modern schema/catch-up transaction | transaction rolls back; reopen re-runs transition |
| after modern schema, during backfill | modern classifier + trigram H/P; resume generic worker |
| H exists but P lost | `_repair_missing_progress` rule: reset this target to known-empty, then P=0 |
| modern empty + populated source + no markers | target-specific orphan repair seeds a full trigram claim |
| completed modern | no markers re-created merely because rows exist |

### 6.6 Trash handling

Reuse the existing `fts_v22_trash_` namespace and teardown worker rather than teaching #30 a second deletion engine. The old session trigram shadows become ordinary tables once the root declaration is demoted and therefore no longer require the `simple` tokenizer.

Upstream PR #81043 (`23dce021...`) improves trash teardown from O(n²) to high-water O(n), but ancestry verification against this pinned fork base shows it is **not** in-base (`diverged`). This is a useful follow-up/cherry-pick for the existing optimize-storage maintenance issue, not a correctness prerequisite for #30. Do not fold that unrelated performance import into #30 unless the implementation run intentionally does the already-tracked cherry-pick first.

---

## 7. What belongs to #30 vs #19/#27/#31/#14

### #30 owns

- modern `sessions_fts_trigram` schema and derived VIEW;
- exact compact policy used by that representation;
- target-specific H/P spec + low-level backfill/restart/orphan safety;
- exact legacy **same-name** `sessions_fts_trigram(simple)` classifier and demotion because it blocks creation of the modern same-name object;
- low-level trigram candidate helper/query preprocessing needed to prove the index's semantics;
- minimal optimize/status hooks needed so a pending trigram claim can actually finish.

### #19 owns after #30

- removing `load_simple_extension()` globally;
- removing `HERMES_LIBSIMPLE_PATH` / bundled `libsimple` compatibility;
- obsolete `messages_fts_trigram(simple)` and any other simple residue;
- global init/read/repair cleanup after no supported state needs `simple`.

This boundary is already explicit in #19.

### #27 owns later

- one six-index lifecycle registry/descriptor;
- maintenance membership, health, repair, read-only discovery, trigger inventory;
- replacing #30's necessarily local/minimal membership knowledge with the shared final model.

Do not pre-build #27 inside #30.

### #31 owns later

- final `fts_storage_version = 2` settlement/refusal predicate;
- startup vs foreground completion agreement.

#30 must **not** stamp storage v2.

### #14 owns later

- common picker/listing routing decision;
- FTS-first vs direct LIKE vs 0-result LIKE fallback;
- candidate narrowing before lineage/projection;
- visibility/scoping/over-fetch result policy.

#30 should expose a low-level trigram lane analogous to `_fts_metadata_candidates`; #14 consumes it.

---

## 8. Query/candidate seam for #14

Add a target-local helper with a shape analogous to #25, for example:

```text
_fts_session_trigram_candidates(raw_query)
    -> (fts_ok, candidates)
```

Responsibilities inside #30:

- compact the title/display-name needle with the authoritative helper;
- keep the ID needle raw;
- issue a field-aware modern trigram MATCH query;
- join hits back to canonical `sessions` by `row_id`;
- supplement this target's own `(P,H]` gap from canonical rows using the same compact/raw field policy;
- return globally deduped/sorted candidate records and signal FTS failure separately from zero hits.

Responsibilities **not** inside #30:

- deciding whether this query shape should have gone Unicode/CJK/trigram;
- deciding when a zero-result route triggers bounded LIKE;
- doing the full listing lineage/scoping projection.

Those belong to #14.

SQLite's trigram MATCH path cannot match query substrings shorter than 3 Unicode characters. #14 therefore still needs its explicit short/unindexable fallback table; #30 must not pretend the index removes that requirement.

Primary SQLite source: https://sqlite.org/fts5.html#the_trigram_tokenizer

---

## 9. Prior-art ancestry audit

| Source | Classification at `e94f2630...` | Decision |
|---|---|---|
| fork PR #59 / #25 | accepted, is the pinned base | reuse row-id + generic rebuild machinery directly |
| upstream PR #57685, merge `19d417...` | merged **and ancestor of pinned base** | behavior already in-base; no cherry-pick; preserve compact-title/raw-ID discovery semantics |
| upstream #76832 crash-safe FTS rebuild work | structural seam already reflected by pinned generic rebuild code/comments | reuse; no reimplementation |
| historical fork `37811327cd` simple session trigram | local history / exact legacy-schema evidence | fixture archaeology only; do not restore implementation shape |
| upstream PR #81043, merge `23dce021...` | merged upstream but **not in pinned fork ancestry** | useful trash-teardown perf import, separately tracked; not required for #30 correctness |
| upstream display-name search work such as #71912 | open/unmerged evidence; final #16 already made fork decision authoritative | no cherry-pick; #16 contract controls |

No direct upstream commit provides the whole #30 combination (derived compact VIEW + stable row_id + independent resumable session trigram + same-name simple convergence), so there is no clean implementation cherry-pick. Reuse current accepted seams instead.

---

## 10. RED tests to write before implementation

Prefer a new `tests/test_session_metadata_trigram_fts.py` if adding this matrix would make the already-large #25 file harder to navigate. Reuse #25 fixture helpers where practical rather than copying the whole session schema.

### A. Representation / schema identity

1. fresh DB modern table is external-content with `tokenize='trigram'`, `content='sessions_fts_trigram_src'`, `content_rowid='row_id'`;
2. VIEW exposes compact title/display-name and raw ID for a row such as:
   - title `AN-94 Prestige.Barrel`;
   - id `discord:thread-123`;
   - display_name `Acme / #an-94-ops`;
3. classifier distinguishes absent / legacy-simple / modern-trigram / unknown same-name;
4. a modern schema on a runtime without trigram is classified modern-but-unavailable, not legacy.

### B. Search representation

5. `AN-94` matches trigram query `an94`;
6. `Acme / #an-94-ops` matches `an94` through display_name;
7. true interior title fragment matches through trigram;
8. raw punctuation-bearing ID interior fragment matches without compacting ID;
9. a punctuation character outside the explicit `- _ . space` policy is not silently normalized away merely because Python `\W` would remove it;
10. pending-gap supplement uses the same compact-title/compact-display/raw-ID semantics as the indexed lane.

### C. Narrow live maintenance

11. INSERT produces one trigram document;
12. DELETE removes it;
13. title/id/display_name update rewrites it;
14. unrelated session counter/activity update does not rewrite the trigram document;
15. UPDATE assigning the same metadata values does not rewrite it;
16. rows in `(P,H]` are not double-written by triggers while worker-owned.

### D. Independent H/P / crash safety

17. Unicode P can be complete/cleared while trigram P remains pending; trigram remains incomplete and correct;
18. trigram worker resumes after restart from its own P;
19. orphan H-without-P resets only trigram to known-empty before replay;
20. empty modern trigram + sessions rows + no trigram markers seeds a trigram claim;
21. boundary-finish catches rows around H and clears only trigram markers when complete;
22. completed trigram reopen does not re-seed markers.

### E. Legacy same-name `simple`

23. historical exact `sessions_fts_trigram(tokenize='simple')` is recognized;
24. migration succeeds when `simple` extension **is not loadable**;
25. the historical triggers are removed and cannot poison normal session writes;
26. old shadows are demoted/renamed before modern same-name creation;
27. canonical `sessions` rows and `row_id` values are unchanged;
28. crash after demotion+claim but before modern schema create resumes safely;
29. crash after modern schema create during backfill resumes from P;
30. unknown same-name schema is not blindly deleted.

### F. Capability / ownership boundaries

31. missing built-in trigram degrades target availability without disabling Unicode session FTS;
32. #30 does not remove the global simple loader or message-simple compatibility;
33. no test expects #30 to stamp `fts_storage_version=2`;
34. low-level trigram helper returns zero/failure distinctly so #14 can own fallback routing.

---

## 11. Ordered implementation commits

### Commit 1 — RED fixtures + representation contract

Suggested message:

```text
test(fts): pin modern session trigram and legacy-simple states
```

Add failing fixtures/tests for:

- compact/raw representation;
- modern schema identity;
- historical same-name simple schema;
- tokenizer-missing same-name legacy state;
- independent trigram marker names.

Validation:

```bash
uv run pytest tests/test_session_metadata_trigram_fts.py -q
uv run pytest tests/test_session_metadata_fts.py -q
```

Expected: new tests RED, existing #25 suite green.

### Commit 2 — compact policy + derived VIEW + modern schema/live triggers

Suggested message:

```text
feat(fts): add normalized external-content session trigram schema
```

Implement:

- authoritative separator policy + Python helper + SQL-expression generator;
- `sessions_fts_trigram_src` VIEW;
- modern external-content FTS DDL;
- narrow H/P-gated live trigger projection;
- schema identity classifier.

Validation:

```bash
uv run pytest tests/test_session_metadata_trigram_fts.py -q
uv run pytest tests/test_session_metadata_fts.py -q
```

### Commit 3 — independent resumable rebuild + candidate lane

Suggested message:

```text
feat(fts): add resumable session trigram rebuild lane
```

Implement:

- `_FTS_SESSION_TRIGRAM_SPEC`;
- status/step wrappers;
- orphan/missing-P/boundary finish reuse;
- schema-transition catch-up;
- low-level `_fts_session_trigram_candidates` + target gap supplement;
- minimal foreground optimize/status integration so pending work can finish.

Validation:

```bash
uv run pytest tests/test_session_metadata_trigram_fts.py -q
uv run pytest tests/test_session_metadata_fts.py -q
uv run pytest tests/test_hermes_state.py -q
```

### Commit 4 — legacy-simple same-name convergence

Suggested message:

```text
fix(fts): converge legacy simple session trigram schema
```

Implement:

- exact legacy-simple recognition;
- tokenizer-independent root demotion;
- shadow-trash rename;
- claim-before-modern-create state machine;
- interruption/reopen tests;
- unknown-shape fail-closed behavior.

Do not remove the global simple loader.

Validation:

```bash
uv run pytest tests/test_session_metadata_trigram_fts.py -q
uv run pytest tests/test_session_metadata_fts.py -q
uv run pytest tests/test_optional_cjk_tokenizer_fallback.py -q
```

### Commit 5 — integration/regression proof

Suggested message:

```text
test(fts): cover normalized session trigram end to end
```

Finish:

- AN-94;
- display-name;
- raw ID;
- independent Unicode/trigram progress;
- unrelated UPDATE non-rewrite;
- capability degradation;
- docs/comments for #14/#19/#27/#31 ownership seams.

Validation:

```bash
uv run pytest tests/test_session_metadata_trigram_fts.py tests/test_session_metadata_fts.py -q
uv run pytest tests/test_hermes_state.py tests/hermes_cli/test_session_listing.py -q
uv run ruff check hermes_state.py hermes_state_common.py hermes_state_schema.py hermes_state_search.py tests/test_session_metadata_trigram_fts.py
```

Then run the repository's normal targeted CI/smoke subset used by the implementation agent.

---

## 12. Non-goals / pitfalls

Do **not**:

- add persistent `title_search_norm` / `display_name_search_norm` columns;
- compact session IDs;
- share Unicode's H/P markers with trigram;
- encode modern trigram as a sidecar inside `_FTS_SESSION_SPEC`;
- use table name alone to call a same-name object modern;
- require `simple` to be loaded in order to retire the old session-simple vtable;
- remove global simple compatibility (#19);
- redesign CJK lifecycle (#26);
- build the six-index lifecycle registry (#27);
- stamp storage v2 (#31);
- move picker routing/ranking/lineage policy into #30 (#14/#28/#29);
- copy the current `re.sub(r"[\W_]+", ...)` behavior as the persistent compact contract without also changing the accepted spec;
- let an empty/partial trigram index appear complete after a crash.

### Specific trap: external-content FTS reads

An external-content FTS table reads its displayed column values from the named content source. Therefore `content='sessions_fts_trigram_src'` is not decorative metadata: the VIEW must remain queryable and field-compatible for external-content operations/rebuilds. Schema identity should verify both the vtable declaration and source VIEW shape.

### Specific trap: short trigram queries

FTS5 trigram MATCH has no useful hit for substrings shorter than three Unicode characters. Do not make #30's low-level helper imply otherwise. #14's routing/fallback contract remains required.

---

## 13. Representation diagram

```text
                           canonical state
                 +-----------------------------+
                 | sessions                    |
                 | row_id  INTEGER PK AUTOINC  |
                 | id      TEXT UNIQUE          |
                 | title   raw                  |
                 | display_name raw             |
                 +--------------+--------------+
                                |
                 non-persistent projection
                                v
             +-------------------------------------+
             | sessions_fts_trigram_src VIEW       |
             | row_id                              |
             | title        = compact(title)       |
             | id           = raw id               |
             | display_name = compact(display_name)|
             +------------------+------------------+
                                |
                                | content=VIEW
                                | content_rowid=row_id
                                v
             +-------------------------------------+
             | sessions_fts_trigram FTS5           |
             | tokenize='trigram'                  |
             +-------------------------------------+

Live ownership for this target:

  <= P        (P,H]             > H
  worker done historical gap    post-claim/live
  triggers    worker owns       triggers

Independent markers:

  fts_session_trigram_rebuild_high_water = H
  fts_session_trigram_rebuild_progress   = P
```

---

## 14. `/implement #30` start checklist

The implementation agent should begin by verifying only:

1. `dev` still contains code base `e94f2630...` plus documentation-only research commits, or re-pin if real code moved;
2. #30 contract has not been superseded;
3. #26 has not landed overlapping code that changes the exact current session FTS symbols;
4. no other accepted PR has already created modern `sessions_fts_trigram`.

Then implement from the ordered plan above. Do **not** repeat the #16 semantic grilling or the legacy-history audit unless the code base materially changed.
