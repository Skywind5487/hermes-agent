# #30 implementation — normalized external-content session trigram FTS

Status: **implemented (2026-08-09); scope-corrected (2026-08-11); re-reviewed (2026-08-10→12)**  
Code base: **`e94f2630a50d7585f78cfc06365753c033113cb9`** (#25 / PR #59 merge, per the #34 handoff)  
Branch: `fts/session-trigram-external-content`

> **Scope correction (2026-08-11).** The fork's legacy `tokenize='simple'`
> session trigram table never existed upstream (#25 base); it was a fork-only
> artifact. #30's job is the modern external-content trigram lane, and the
> upstreamable surface must not carry a fork-specific migration for a table
> that cannot exist there. The entire legacy convergence surface
> (`_demote_legacy_sessions_trigram_fts`, `LEGACY_SESSIONS_TRIGRAM_*`,
> `_SESSIONS_TRIGRAM_LEGACY_*`, the classifier's `legacy_simple` branch,
> `allow_legacy_shadows`, `drop_legacy_orphan_triggers`) was therefore
> removed in `c7163001f` (tests in `2142e2fa1`). Only the modern trigram lane
> remains.

> **Round-12 re-review (2026-08-10, comment `5240949473` supersedes
> `5240412376`; implemented in `10e3419d1` + `69591fe65`).** The reviewer re-applied
> the corrected #30 source of truth with the rule "only defend states the
> final modern lane can actually create" and asked for **one real capability
> fix plus a large subtraction of review-driven hardening** — not more
> hardening. Net base→HEAD is now strongly subtractive (+134 / -502).
>
> - **P1 (fixed): healthy exact-modern target on a no-trigram runtime was
>   left with live triggers.** A healthy modern reopen (no stale breadcrumb,
>   exact triggers) probed via the generic `_ensure_fts_schema` which returns
>   unavailable without quarantining — a later canonical `sessions` write
>   could fail inside the live triggers with `no such tokenizer: trigram`.
>   The `modern_trigram` branch now probes the exact modern root directly
>   (`_fts_table_probe`); `None` → reuse `_sessions_trigram_quarantine()`
>   (stale set + owned triggers dropped, canonical writes survive); `True` →
>   orphan-empty check → serve. No new marker / state. The previous
>   fresh-create foreign-DDL TOCTOU P1 was **withdrawn** — no CAS added for a
>   hypothetical concurrent manual schema rewrite.
> - **P2 (merge-blocking, deleted): Round-10/11 defended unsupported post-open
>   schema damage.** Deleted: `SessionTrigramOwnershipLost`, the
>   `write_guard` spec key + branches in the shared `_fts_rebuild_finish` /
>   `fts_rebuild_step`, `_sessions_trigram_owned_servable()`,
>   `_sessions_trigram_require_owned_modern_for_write()`,
>   `SESSIONS_TRIGRAM_MODERN_SHADOW_TABLES` + `_sessions_trigram_shadow_collision()`
>   (a transaction rollback cannot leave the fresh FTS shadows), and the
>   "modern trigger missing **without** stale → full stale rebuild" path
>   (final #30 never creates it — fail closed instead).
>   `_ensure_sessions_trigram_fts_schema_depth(cursor, depth)` collapsed back
>   into `_ensure_sessions_trigram_fts_schema(cursor)`. Corresponding tests
>   (`TestWriteAuthorizationCAS`, `TestShadowNamespacePreflight`,
>   `test_modern_missing_trigger_stale_rebuild`) and their fixtures were
>   removed; one new P1 regression
>   (`test_healthy_modern_quarantined_on_no_trigram_host`) was added. The
>   open state machine is now: unknown root/source/foreign trigger →
>   unavailable; root absent → ensure VIEW → seed/reuse H/P → atomic modern
>   create; root modern → stale ? capable-recover/incapable-quarantine :
>   (trigger set not exact → unavailable) : (tokenizer missing → quarantine) :
>   (orphan-empty → available).
> - Kept untouched (direct acceptance-criterion owners): exact stored
>   DDL/source/trigger identity, literal-safe identity comparison,
>   independent H/P, shared #25 chunk/finish primitives, missing-P +
>   orphan-empty repair, stale breadcrumb, capability recovery, one-snapshot
>   candidate/gap lane.

## What was built

A first-class modern `sessions_fts_trigram` (FTS5 `tokenize='trigram'`
external-content) for session-metadata search, keyed by stable
`sessions.row_id`, reading through a derived VIEW that projects compact
title, RAW id, and compact display_name — with its own resumable H/P rebuild
lane.

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
  absent / modern_trigram / unknown_same_name),
  `_ensure_sessions_trigram_fts_schema`, `_fts_session_trigram_schema_transition`
  (crash-atomic VIEW/table/trigger install + trigger-owned catch-up),
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

## Legacy `simple` convergence — removed (2026-08-11 scope correction)

Early rounds built a tokenizer-independent demotion for a recognized legacy
same-name `tokenize='simple'` `sessions_fts_trigram` (drop exact-historical
triggers, `writable_schema` root removal, exact-shadow rename to
`fts_v22_trash_`, H/P seed in one `BEGIN IMMEDIATE`, CAS-guarded). The
classifier had a `legacy_simple` branch verified by normalized DDL against
`LEGACY_SESSIONS_TRIGRAM_FTS5_DECLARATION` (never PRAGMA — connecting the
vtable raises `no such tokenizer: simple`), and the fresh schema transition
dropped exact legacy orphan triggers under the lock
(`drop_legacy_orphan_triggers`).

Review on the #25 base showed this whole surface was fork-only: upstream's
`SESSIONS_FTS_TRIGRAM_SQL` (modern trigram) was the only sessions trigram
table that ever shipped, so a migration for a `simple` table that cannot
exist upstream is dead weight and an upstreamability liability. Per the
acceptance gate ("if `simple` never existed, it is not needed → delete"),
all of it was removed:

- `_demote_legacy_sessions_trigram_fts` and its CAS/trash/writable_schema
  body;
- `LEGACY_SESSIONS_TRIGRAM_FTS5_DECLARATION` / `SESSIONS_TRIGRAM_LEGACY_SHADOW_TABLES` /
  `LEGACY_SESSIONS_TRIGRAM_TRIGGER_SQL` and the `_SESSIONS_TRIGRAM_LEGACY_*`
  derived constants;
- the classifier's `legacy_simple` branch, trigger_status `exact_legacy`,
  namespace_owned / shadow_collision legacy branches,
  `allow_legacy_shadows`, and the spec's `drop_legacy_orphan_triggers` key.

The classifier now distinguishes only absent / modern_trigram /
unknown_same_name; unknown same-name shapes still fail closed (never
deleted). The historical round-by-round entries below (Round-3 … Round-11)
record the work as it happened and are kept for provenance, but the legacy
code they describe is gone from the branch.

## Ownership boundaries (kept)

- #30 owns the modern external-content trigram lane only; the global
  `simple` retirement stays #19 (`load_simple_extension` untouched) and
  legacy `simple` session-trigram convergence was removed outright (see
  above).
- The unified six-index lifecycle stays #27; no second scheduler/registry
  was built.
- `fts_storage_version = 2` is NOT stamped (that's #31).
- `_fts_session_trigram_candidates` is the low-level lane for #14; the
  picker/listing routing, 0-result LIKE fallback, and lineage projection
  were not moved into #30. Trigram MATCH cannot match <3-Unicode-char
  substrings — #14's bounded-LIKE fallback remains necessary.

## Validation

- `tests/test_session_metadata_trigram_fts.py` — 58 passed after the legacy
  removal (schema identity, compact/raw search representation, narrow live
  maintenance, independent H/P + restart/orphan/boundary, e2e).
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

### Round-2 polish (2026-08-09)

Round-2 review found no hard violations. Applied the judgement-call polish:
consolidated the duplicated `if not ok:` epilogue in
`_ensure_sessions_trigram_fts_schema` (one `available=False` / `return False`
path; the fresh-claim clear is now gated inside it); the shared
`_session_fts_schema_transition` now builds its catch-up column list from
`spec["fts_columns"]` / `spec["source_columns"]` (no hardcoded column names)
and carries type hints. The seed's empty-DB guard deliberately still counts
`FROM sessions` (a canonical-table question, not a lane projection);
`COUNT(*)` from the trigram VIEW would add a VIEW-missing risk for no benefit.

### Round-3 fix — exact schema classifier (2026-08-09)

Round-3 review (P2): the classifier's column checks were substring-based and
too loose — `"id" in sql` is fooled by `content_rowid='row_id'`, and the
legacy check never verified the historical title-only shape. A near-match
same-name object could be misclassified modern/legacy and then either be
demoted (writable_schema root removal) or operated on with a missing column,
blowing up at session write/rebuild instead of failing closed at the
migration boundary.

`_classify_sessions_fts_trigram` now verifies the EXACT logical column set
via `PRAGMA table_info` (`_fts_declared_columns` — reads the DDL column list
without instantiating the vtable, so it works on a host without `simple`)
and requires a compatible derived source VIEW (`_sessions_trigram_src_compatible`):
- `legacy_simple` = `tokenize='simple'` + internal content + exactly `{title}`;
- `modern_trigram` = `tokenize='trigram'` + the exact content/content_rowid +
  exactly `{title, id, display_name}` + a compatible VIEW when one is present
  (a missing VIEW is healable, a mismatched one fails closed);
- everything else — including simple-with-other-columns, trigram-without-`id`,
  and modern-root-with-incompatible-VIEW — is `unknown_same_name` (fail
  closed, never demoted/deleted).

Pinned by three new regression tests
(`test_classifier_simple_wrong_column_shape_unknown`,
`test_classifier_trigram_missing_id_column_unknown`,
`test_classifier_modern_root_incompatible_view_unknown`).

### Round-4 hardening — exact source-VIEW identity (2026-08-09)

Round-4 review (P2): `_sessions_trigram_src_compatible` checked only the
output column NAMES via PRAGMA table_info — it could not see a VIEW with the
right four names but rewired expressions (e.g. `display_name AS id`, raw
title), and a same-name TABLE shadow was treated as "VIEW missing, healable"
(`CREATE VIEW IF NOT EXISTS` silently no-ops over a table, so it is never
healed). Also, FTS5 itself was not verified as part of the root identity.

`_sessions_trigram_src_compatible` now confirms the source object is an
**actual VIEW** (`sqlite_master.type == 'view'`; a same-name table returns
incompatible → fail closed) and that its stored definition is the **canonical
#30 projection** (`_sessions_trigram_src_definition_matches` — whitespace- and
`IF NOT EXISTS`/trailing-`;`-normalized comparison against the statement the
code itself creates), so only the exact `compact(title)` / raw `id` /
`compact(display_name)` VIEW is accepted. The classifier additionally requires
the root to be a `CREATE VIRTUAL TABLE ... USING fts5` declaration.

Pinned by two new regression tests
(`test_classifier_modern_root_miswired_view_unknown`,
`test_classifier_modern_root_same_name_table_src_unknown`).

### Round-5 fix — legacy identity without connecting the vtable (P1, 2026-08-10)

Round-5 review (P1): `PRAGMA table_info()` must CONNECT an FTS5 virtual table,
so on a host without the legacy `simple` tokenizer it raises
`no such tokenizer: simple`. The round-3 classifier used `_fts_declared_columns`
(PRAGMA) to verify the legacy shape — on such a host that returned None →
`unknown_same_name` → the #30 demotion never ran, silently re-introducing the
exact #34 contract violation ("legacy-simple → modern must not require
`simple`").

`_sessions_trigram_legacy_definition_matches` now verifies the legacy identity
by a normalized DDL comparison of the stored root declaration against the
canonical historical `LEGACY_SESSIONS_TRIGRAM_FTS5_DECLARATION` (FTS5,
title-only, INTERNAL content, `tokenize='simple'`) — it reads the declared
columns directly from the stored SQL and never connects the vtable, so it
works on a host without `simple`. The MODERN branch keeps PRAGMA table_info
(built-in trigram; a no-trigram host failing closed to unknown is safe — the
table is never deleted). Pinned by
`test_classifier_legacy_simple_without_simple_tokenizer` (raw connection, no
`simple` → classifies legacy → open path demotes to modern) and
`test_classifier_legacy_does_not_probe_vtable` (monkeypatched
`_fts_declared_columns` → None; legacy still classifies, proving no PRAGMA
dependency).

### Round-6 fix — remove ALL PRAGMA from the classifier (P2, 2026-08-10)

Round-6 review (P2): the MODERN branch still used `_fts_declared_columns`
(`PRAGMA table_info`), which must CONNECT the FTS5 vtable and resolves the
declared tokenizer — on a host without built-in trigram it raises
`no such tokenizer: trigram` → columns None → `unknown_same_name`, even for a
correct modern table. That re-coupled schema identity to runtime tokenizer
capability, exactly the round-5 coupling #34 forbids (a correct modern schema
on a no-trigram host must classify **modern-but-unavailable**, not unknown).

The classifier now contains **zero PRAGMA**. All three identity checks are
canonical stored-DDL comparisons against the exact statements the code itself
creates:

- `legacy_simple` → `_sessions_trigram_legacy_definition_matches` (canonical
  `LEGACY_SESSIONS_TRIGRAM_FTS5_DECLARATION`);
- `modern_trigram` → `_sessions_trigram_modern_definition_matches` (canonical
  `_SESSIONS_FTS_TRIGRAM_STATEMENTS[1]` — the exact modern vtable DDL, which
  also pins the `(title, id, display_name)` column set and the
  content/content_rowid/tokenize attributes) AND
  `_sessions_trigram_src_compatible` (canonical VIEW DDL);
- everything else → `unknown_same_name` (fail closed).

`_fts_declared_columns` is deleted (dead). Runtime tokenizer capability is
decided solely by the availability probe
(`_ensure_sessions_trigram_fts_schema` → `_sessions_trigram_available`), never
by the classifier. Tests: `test_classifier_legacy_never_connects_vtable` and
`test_classifier_modern_never_connects_vtable` run the classifier through a
`_FtsProbeBlockingCursor` proxy that raises on any `PRAGMA table_info`
(simulating a host without the tokenizer) and assert classification still
succeeds — RED before this fix, GREEN after.

### Round-7 fix — source-collision guard in the ensure path (P2, 2026-08-10)

Round-7 review (P2): `_ensure_sessions_trigram_fts_schema` created the
derived VIEW (`CREATE VIEW IF NOT EXISTS sessions_fts_trigram_src`) BEFORE
seeding H/P / demoting legacy — but `CREATE VIEW IF NOT EXISTS` silently
no-ops when the source NAME is occupied by a same-name TABLE or a
non-canonical VIEW. The rebuild H (`SELECT COALESCE(MAX(row_id), 0) FROM
sessions_fts_trigram_src`) would then be computed from the WRONG source and a
modern index would silently index nothing; the legacy path was worse — the
demotion's shadow rename (`name LIKE 'sessions_fts_trigram\_%'`, type=table)
would even drag the same-name source TABLE into the `fts_v22_trash_`
namespace before the collision surfaced.

`_ensure_sessions_trigram_fts_schema` now runs `_sessions_trigram_src_compatible`
immediately after classification (before any `CREATE VIEW IF NOT EXISTS` /
H/P seed / legacy demotion) and fails closed on a collision (absent → OK, the
VIEW gets created; canonical VIEW → OK; TABLE / bad VIEW → fail closed, no
modern build, no claim, legacy untouched). This also makes the legacy
demotion's shadow-rename safe: it is only reachable with the source absent or
the canonical VIEW (type='view', excluded by the `type='table'` filter).

Pinned by `TestSourceCollisionGuard`:
`test_root_absent_source_table_fail_closed` (root absent + source TABLE → no
modern index, no H/P seed, table survives) and
`test_legacy_simple_bad_source_does_not_demote` (exact legacy-simple + source
TABLE → not demoted, no H/P, table survives) — both RED before this fix (the
legacy one failed with `no such table: sessions_fts_trigram_src` as the
demotion dragged the source into trash), GREEN after.

### Round-8 fix — root classifier sees the table/view namespace (P2, 2026-08-10)

Round-8 review (P2): the root classifier's lookup only matched
`type = 'table'`, so a same-name VIEW (`CREATE VIEW sessions_fts_trigram AS
SELECT 1 AS x`) was misclassified `absent` — violating #34's "unknown
same-name object → untouched, capability off". Worse, on the open path that
made `CREATE VIRTUAL TABLE IF NOT EXISTS` silently no-op over the VIEW, seed
H/P, enter the schema transition, and run a catch-up INSERT against a
non-updatable VIEW → `cannot modify sessions_fts_trigram because it is a
view` (open error). The regression reproduced exactly that on the buggy code.

`_classify_sessions_fts_trigram` now looks up the root in the shared
table/view namespace first (`type IN ('table', 'view')`):
- no table or view → `absent`;
- VIEW → `unknown_same_name` (fail closed — the ensure path never seeds H/P
  or runs a transition against it);
- TABLE → the existing exact FTS5 DDL identity checks.

Pinned by `test_classifier_root_same_name_view_unknown` (same-name root VIEW
→ `unknown_same_name`, no H/P seed, the VIEW preserved, and `SessionDB`
open must not raise — RED with the exact `cannot modify ... because it is a
view` error before the fix, GREEN after).

### Round-9 fix — exact shadow allowlist + root classifier sees index (P1+P2, 2026-08-10)

Round-9 review found two migration-safety findings:

**P1 — legacy demotion shadow discovery too broad.** The demotion renamed
shadow tables by prefix sweep (`name LIKE 'sessions_fts_trigram\_%'`), so any
unrelated table that merely shares the prefix (e.g.
`sessions_fts_trigram_unrelated`) was renamed into `fts_v22_trash_*` — where
teardown deletes it (data loss). Now only the EXACT five legacy FTS5 shadow
tables move, via the new `SESSIONS_TRIGRAM_LEGACY_SHADOW_TABLES` allowlist
(`_data/_idx/_content/_docsize/_config`), never a prefix sweep. Pinned by
`test_legacy_demotion_leaves_unrelated_prefix_table` (unrelated prefix table
+ sentinel row survives migration AND teardown — RED before, the table was
swept into trash, GREEN after).

**P2 — same-name INDEX classified `absent`.** The root classifier looked up
only the table/view namespace, so a same-name index was `absent` — and
`CREATE VIRTUAL TABLE IF NOT EXISTS` then raised `there is already an index
named sessions_fts_trigram` instead of failing closed. The root lookup now
covers `type IN ('table', 'view', 'index')`; only a table proceeds to the
exact FTS5 DDL identity, anything else is `unknown_same_name`. Pinned by
`test_classifier_root_same_name_index_unknown` (same-name harmless index →
`unknown_same_name`, no H/P seed, index preserved, capability false, open not
raise — RED with the exact `there is already an index named ...` error before
the fix, GREEN after).

### Round-10 — full from-scratch audit hardening (6 P1 + 3 P2 + 1 P3, 2026-08-10)

A full-state audit (base `e94f2630` → HEAD `3172c9f46`) found lifecycle /
ownership gaps beyond the round-1..9 classifier fixes. Implemented as one
coherent state-machine hardening pass in the review's prescribed order:

1. **Literal-safe DDL normalizer (F6/P2).** New shared
   `_normalize_ddl_for_identity()` — whitespace between SQL tokens is
   formatting and dropped, whitespace INSIDE single-/double-quoted literals is
   preserved byte-for-byte (incl. doubled-quote escapes), `IF NOT EXISTS` is
   stripped only as a DDL token outside literals. Replaces the three drifting
   `"".join(sql.split())` closures so `REPLACE(title,' ','')` can never be
   confused with `REPLACE(title,'  ','')` or `content='...src '` with
   `content='...src'`. (Pinned: `test_classifier_source_view_double_space_separator_unknown`,
   `test_classifier_modern_root_literal_whitespace_unknown`.)

2. **Trigger + namespace ownership (F2/P1, F8/P2).** New
   `_sessions_trigram_trigger_status()` classifies each target trigger name
   (`absent | exact_modern | exact_legacy | foreign`) by stored-DDL comparison
   AND `tbl_name='sessions'`; `_sessions_trigram_namespace_owned()` gates
   ownership per root state; `_sessions_trigram_shadow_collision()` preflights
   the modern reserved shadows (`_data/_idx/_docsize/_config`; `_content` not
   reserved; legacy shadows allowed only on the demotion path). Foreign
   same-name triggers fail closed and are untouched; missing modern triggers
   mean stale (never blind `IF NOT EXISTS`). `_sessions_trigram_src_compatible`
   now queries the table/view/index namespace (a same-name trigger no longer
   affects row order). (Pinned: `TestTriggerNamespaceOwnership`,
   `TestShadowNamespacePreflight`.)

3. **Legacy demotion compare-and-swap (F3/P1).** `_demote_legacy_sessions_trigram_fts`
   now returns `demoted | superseded | refused` and REVALIDATES the exact
   legacy identity, source/trigger ownership, and trash destinations INSIDE
   its own `BEGIN IMMEDIATE`; drops only triggers proven exact-historical; a
   stale second opener converges on the winner's modern state instead of
   destructively re-running. (Pinned: `TestDemotionCAS`.)

4. **Stale / quarantine lifecycle (F1/P1).** New `fts_session_trigram_stale`
   breadcrumb (CJK precedent). A modern target with a missing owned trigger or
   durable stale is STALE: a capable host runs `_fts_session_trigram_recover_stale`
   (atomic: revalidate ownership, `delete-all`, verify docsize empty, re-seed
   H/P, reinstall owned triggers, catch-up, clear stale LAST — idempotent under
   two capable processes); an incapable host quarantines (persist stale before
   dropping only exact owned triggers so canonical `sessions` writes survive)
   and never serves. (Pinned: `TestStaleLifecycle`.)

5. **Serving / repair gates + reset postcondition + open-time orphan repair
   (F4/F5/F6, all P1).** `_sessions_trigram_owned_servable()` gates EVERY
   mutating/serving surface: candidates return `(False, [])` for unowned /
   stale lanes, `_repair_session_trigram_fts_bookkeeping()` is ownership-gated,
   `fts_optimize_available` / optimize's settle only advertise trigram work a
   capable owned runtime can complete. `_repair_missing_progress` verifies the
   required primary target is proven empty after `delete-all` before
   publishing `P=0`. Writable open runs the hardened empty-orphan repair
   (modern empty + populated source + no claim → seed full claim atomically)
   so reopen never serves a silent false-negative window. (Pinned:
   `TestServingRepairGates`, `TestResetPostcondition`, `TestOpenTimeOrphanRepair`.)

6. **Empty-session claim rule (F7/P2).** `_seed_session_metadata_fts_rebuild_markers`
   clears this spec's H/P when `COUNT(sessions)==0` regardless of force — no
   `H=0/P=0` zombie claim that `fts_optimize_available` would advertise
   forever. (Pinned: `TestEmptySessionClaim`.)

7. **Query cap (F9/P3).** `_fts_session_trigram_candidates` bounds the needle
   to `MAX_FTS5_QUERY_CHARS` before compacting / building the MATCH
   expression. (Pinned: `TestQueryCap`.)

Also: the crash-atomic transition and stale recovery now share one
`_fts_session_trigram_catchup_sql()` source of truth; the process-local
`_sessions_trigram_available` flag is explicitly initialized in the
constructor.

Verification: `test_session_metadata_trigram_fts.py` 66 passed (17 new);
related FTS/session suites 251 passed, 9 skipped. The two
`test_session_search_sql_winners.py` failures are PRE-EXISTING on the pinned
base (an unrelated `search_session_winners` projection shape) and are not
introduced by this pass. ruff clean.

