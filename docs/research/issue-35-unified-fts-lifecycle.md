# #35 — six-index FTS lifecycle implementation map for #27

> Research-only handoff. This document maps the accepted six-index session/message FTS architecture onto the actual post-#30 `dev` implementation. It does **not** implement #27 and does **not** own storage-v2 settlement.
>
> Authority order: #12 + completed #16 projection + accepted #25/#26/#30 + #27 contract → merged upstream accepted seams → pinned fork `dev` → donor/local history as evidence only → open/unmerged upstream as evidence only.

## 0. Executive result

**READY FOR IMPLEMENTATION. No additional split or blocker is required.**

The smallest coherent #27 change is a **data-only, module-level lifecycle index registry** in `hermes_state_common.py` that describes the six modern FTS indexes without becoming a search-routing or migration-state framework.

The registry should own only static index identity needed across module-level/offline code and SessionDB mixins:

- FTS table name;
- canonical/derived content source;
- row key;
- indexed columns;
- owned modern trigger names;
- required capability class (`fts5`, `trigram`, `cjk`);
- owned derived objects needed by destructive derived-index repair (notably source VIEWs).

Dynamic facts remain outside the registry:

- H/P rebuild claims and stale breadcrumbs;
- worker-operable vs search-serving state;
- exact same-name ownership classification;
- search routing/ranking/fallback;
- final storage-layout settlement.

This keeps #25/#26/#30 state machines intact while removing the distributed hard-coded table/trigger membership that currently excludes session indexes from normal maintenance, health, repair, and read-only discovery.

The six authoritative modern members are:

1. `messages_fts`
2. `messages_fts_trigram`
3. `messages_fts_cjk`
4. `sessions_fts`
5. `sessions_fts_cjk`
6. `sessions_fts_trigram`

The older five-index list in #12 predates #30 and is historical only; #27 itself explicitly freezes the six-member target.

---

## 1. Pinned receipt

Research date: 2026-08-11.

| Item | Immutable pin / state |
|---|---|
| fork integration branch | `dev` |
| **BASE_SHA** | **`919f4469e832bc2b38bba0ea5af26b842bf91acd`** |
| #25 stable row_id + Unicode | accepted / landed |
| #26 session CJK lifecycle | accepted / landed |
| #30 modern session trigram | accepted / landed through PR #73 |
| `hermes_state.py` blob | `223645ab6ee9061f6825c4d5f7b1be845a61120a` |
| `hermes_state_common.py` blob | `24d96b4cfa77b97ab28f75acb38d882b5f8bf766` |
| `hermes_state_schema.py` blob | `5636541c1c25119afc74294d1518fb80114d0f6f` |
| `hermes_state_search.py` blob | `9eeacaab5cea0930fa3d601ea6f3db5ca77d1562` |

Primary ticket sources:

- #35: <https://github.com/Skywind5487/hermes-agent/issues/35>
- #27: <https://github.com/Skywind5487/hermes-agent/issues/27>
- #12 architecture: <https://github.com/Skywind5487/hermes-agent/issues/12>
- #25: <https://github.com/Skywind5487/hermes-agent/issues/25>
- #26: <https://github.com/Skywind5487/hermes-agent/issues/26>
- #30 corrected modern-only scope: <https://github.com/Skywind5487/hermes-agent/issues/30>
- PR #73: <https://github.com/Skywind5487/hermes-agent/pull/73>

All source links below are pinned to `919f4469e832bc2b38bba0ea5af26b842bf91acd`.

---

## 2. Authoritative static descriptor proposal

### 2.1 Minimum shape

Put a frozen/data-only descriptor in `hermes_state_common.py`, importable from:

- `hermes_state.py` module-level health/offline repair functions;
- `SessionSchemaMixin`;
- `SessionSearchMixin`;
- the `SessionDB` host;

without importing `hermes_state.py` back into a mixin and creating a cycle.

Recommended minimum shape, names illustrative:

```python
@dataclass(frozen=True)
class FtsIndexDescriptor:
    table: str
    source: str
    row_key: str
    columns: tuple[str, ...]
    trigger_names: tuple[str, ...]
    capability: Literal["fts5", "trigram", "cjk"]
    derived_objects: tuple[tuple[str, str], ...] = ()

FTS_INDEXES: tuple[FtsIndexDescriptor, ...] = (...six members...)
```

`derived_objects` is for owned derived schema needed by destructive repair (`("view", "messages_fts_trigram_src")`, `("view", "messages_fts_cjk_src")`, `("view", "sessions_fts_trigram_src")`, etc.), not a generic schema graph.

Do **not** add dynamic callbacks such as `available=lambda self: ...` to the common registry. The current rebuild specs need `self`; module-level offline repair does not have one. A data-only registry is the smallest common denominator.

Do **not** add H/P/stale keys to the per-index identity descriptor merely to make it “complete”. Rebuild **lanes** and indexes are not 1:1: message Unicode + message trigram currently share one H/P lane, while message CJK and each session lane have independent state. Mixing those concerns recreates complexity under a different name.

### 2.2 Six entries

| index | source | row key | columns | capability | notes |
|---|---|---|---|---|---|
| `messages_fts` | `messages` | `id` | `content, tool_name, tool_calls` | `fts5` | required base lane |
| `messages_fts_trigram` | `messages_fts_trigram_src` | `id` | same | `trigram` | derived source excludes tool rows |
| `messages_fts_cjk` | `messages_fts_cjk_src` | `id` | same | `cjk` | optional/loadable tokenizer |
| `sessions_fts` | `sessions` | `row_id` | `title, id, display_name` | `fts5` | raw Unicode metadata |
| `sessions_fts_cjk` | `sessions` | `row_id` | same | `cjk` | optional; worker != serving |
| `sessions_fts_trigram` | `sessions_fts_trigram_src` | `row_id` | same | `trigram` | compact title/display, raw id |

Primary DDL:

- message Unicode/trigram: `hermes_state_common.py:L491-L620` — <https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_common.py#L491-L620>
- session Unicode + compact policy + modern trigram: `hermes_state_common.py:L650-L862` — <https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_common.py#L650-L862>
- session CJK: `hermes_state_common.py:L864-L952` — <https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_common.py#L864-L952>

### 2.3 Dynamic applicability helper belongs on SessionDB

A small SessionDB-side helper may translate `descriptor.capability` + ownership into operation-specific applicability:

- **maintainable/operable**: this process can execute the index's FTS special commands safely;
- **serving**: this index is complete, non-stale, owned, and queryable.

They are deliberately different. Search uses serving. Maintenance/rebuild uses operability/ownership. Read-only discovery computes capability/serving but does not mutate schema.

For modern session trigram, *table name in the registry is not authorization to mutate it*. #30's exact root/source/trigger identity classifier remains the ownership gate; `unknown_same_name` stays fail-closed.

---

## 3. Existing rebuild specs: retain the state-machine concern, remove duplicated identity

Current `hermes_state_search.py:L135-L239` defines:

- `_FTS_MESSAGE_SPEC`
- `_FTS_SESSION_SPEC`
- `_FTS_SESSION_TRIGRAM_SPEC`
- `_FTS_SESSION_CJK_SPEC`

Source: <https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_search.py#L135-L239>

These are already a useful shared **deferred rebuild lane** abstraction. `fts_rebuild_status`, `_fts_rebuild_finish`, and `fts_rebuild_step` consume the spec at `hermes_state_search.py:L293-L583`:
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_search.py#L293-L583>

Recommendation:

- keep lane-specific H/P keys, availability/worker callback, reset targets, and finish hook in rebuild specs;
- make each spec reference an authoritative index descriptor instead of repeating table/source/row-key/columns;
- add a generic message-CJK rebuild spec and move its currently bespoke status/step/finish implementation onto the same shared engine **without changing its independent H/P/stale semantics**.

The remaining bespoke message-CJK rebuild is at `hermes_state_search.py:L620-L715`, followed by shared CJK stale-reset plumbing at `L717-L818`:
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_search.py#L620-L818>

This is the clearest current duplication after #25/#26/#30 landed.

---

## 4. Lifecycle inventory: current → #27 target

### 4.1 Authoritative membership / duplicate tuples

**Current**

- `SessionDB._FTS_TABLES = ("messages_fts", "messages_fts_trigram", "messages_fts_cjk")` at `hermes_state.py:L10790-L10796`.
  <https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L10790-L10796>
- `_FTS_TRIGGERS` in `hermes_state_common.py` is message Unicode + message trigram only.
- `_FTS_CJK_TRIGGERS` and `_FTS_SESSION_CJK_TRIGGERS` are separate tuples.
- `_SESSIONS_TRIGRAM_MODERN_TRIGGER_NAMES` in `hermes_state.py:L278-L284` is another membership tuple used by the #30 ownership classifier.

**Target**

- remove `_FTS_TABLES` and generic modern trigger-membership tuples in favor of `FTS_INDEXES`;
- keep exact canonical DDL maps where they serve a genuinely different concern, especially `_SESSIONS_TRIGRAM_MODERN_TRIGGER_DDL` for #30 ownership identity;
- legacy migration-only lists may remain scoped to legacy code if they are not modern lifecycle membership.

### 4.2 Ordinary optimize

**Current**: `SessionSearchMixin.optimize_fts`, `hermes_state_search.py:L3452-L3492`, iterates `self._FTS_TABLES`, therefore only the three message indexes.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_search.py#L3452-L3492>

**Target**: iterate applicable owned descriptors. Required Unicode indexes participate whenever present; tokenizer-gated indexes participate only when operable/owned. Unknown #30 same-name trigram is never touched.

### 4.3 Bounded incremental merge

**Current**: `SessionSearchMixin._merge_fts_incrementally`, `hermes_state_search.py:L3527-L3619`, also iterates `self._FTS_TABLES`.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_search.py#L3527-L3619>

It is called from `_try_incremental_merge_fts` and the 1000-write cadence in `_execute_write`; adding the session members through the shared applicability path is sufficient—no session-specific cadence.

### 4.4 Explicit rebuild

**Current**: `SessionSearchMixin.rebuild_fts`, `hermes_state_search.py:L3494-L3525`, iterates the same message-only tuple and its docstring incorrectly says rebuild from canonical `messages`.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_search.py#L3494-L3525>

**Target**: iterate applicable canonical owned external-content indexes. FTS5's `rebuild` command follows each table's declared `content=` source, so `sessions_fts_trigram` naturally rebuilds through `sessions_fts_trigram_src`; do not duplicate compact SQL in the rebuild function.

### 4.5 VACUUM pre-maintenance

**Current**: `SessionDB.vacuum`, `hermes_state.py:L10825-L10861`, calls `self.optimize_fts()` before `VACUUM`.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L10825-L10861>

**Target**: no new session hook. Once `optimize_fts()` is registry-driven, VACUUM reaches all applicable six members through the existing normal path.

### 4.6 Health read probe

**Current**: `_db_opens_cleanly`, `hermes_state.py:L1410-L1465`, probes only `messages_fts`, `messages_fts_trigram`, `messages_fts_cjk` with a representative `MATCH`.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L1410-L1465>

**Target**: use static registry inventory plus connection-local capability/identity checks. Probe all applicable owned session indexes too. Missing optional tokenizer remains capability degradation, not corruption. Unknown #30 same-name object remains outside owned repair.

### 4.7 Rollback-only health write probe

**Current**: `_db_opens_cleanly`, `hermes_state.py:L1467-L1508`, inserts a session with only `(id, source, started_at)` then a message and always rolls back.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L1467-L1508>

Because all indexed session metadata fields except `id` are null and the probe was designed around message triggers, it is not a strong intentional session-metadata trigger probe.

**Target**: insert non-null `title` and `display_name` (with the unique probe id) so Unicode/CJK/trigram session INSERT triggers execute their real projection. Keep `BEGIN IMMEDIATE` + unconditional rollback and assert no probe rows persist.

### 4.8 Trigger inventory + broad→narrow convergence

**Current**: `SessionSchemaMixin._fts_trigger_count` and `_migrate_broad_fts_update_triggers`, `hermes_state_schema.py:L69-L171`, derive from `_FTS_TRIGGERS` and hard-code message update names (+ message CJK when applicable).
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_schema.py#L69-L171>

The already-landed canonical session DDL is narrow:

- `sessions_fts_update`: `AFTER UPDATE OF title, id, display_name`;
- `sessions_fts_cjk_update`: same;
- trigram `update_before` + `update_after`: same.

Source: `hermes_state_common.py:L650-L952`.

Legacy #25 test fixtures still demonstrate a broad `sessions_fts_update AFTER UPDATE ON sessions` predecessor, so convergence is required for recognized/owned session objects.

**Target**:

- generalize the existing `AFTER UPDATE OF` inspector over recognized owned update triggers;
- safely converge `sessions_fts_update` and `sessions_fts_cjk_update` from broad to canonical narrow DDL;
- do **not** blindly rewrite arbitrary `sessions_fts_trigram*` occupants. #30 modern trigram already has exact stored-DDL root/source/trigger identity. A foreign/unknown same-name namespace must remain fail-closed; missing/foreign modern trigger handling stays inside #30's ownership model.

### 4.9 Degraded-runtime trigger teardown / quarantine

**Current global FTS5 teardown**: `_drop_fts_triggers`, `hermes_state.py:L3755-L3762`, drops only `_FTS_TRIGGERS` (message Unicode/trigram).
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L3755-L3762>

**Current optional CJK/session-trigram behavior is intentionally richer**:

- session CJK capability loss persists stale first, then drops owned CJK triggers while preserving H/P; `hermes_state.py:L2700-L2815`.
- modern session trigram exact identity/ownership classifier and quarantine/recovery live at `hermes_state.py:L3150-L3560`.

Sources:
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L2700-L2815>
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L3150-L3560>

**Target**:

- global whole-FTS5-unavailable owned-trigger teardown derives trigger names from the registry;
- CJK/trigram tokenizer-loss quarantine remains dedicated because it must preserve stale ordering, worker/serving distinctions, and #30 exact ownership. Registry membership is not permission to drop a foreign trigger.

### 4.10 Runtime corruption recovery

**Current**: `_execute_write` delegates recognized FTS corruption to `_try_runtime_fts_rebuild`, which one-shots through `self.rebuild_fts()`, `hermes_state.py:L3890-L4008`.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L3890-L4008>

**Target**: no second recovery engine. Making `rebuild_fts()` six-index/capability/ownership aware makes the existing runtime seam cover session indexes as well. Preserve the once-per-instance retry guard.

### 4.11 Offline least-destructive repair

**Current**: `repair_state_db_schema` strategy 0, `hermes_state.py:L1511-L1600`, hard-codes only the three message FTS tables for in-place `rebuild`.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L1511-L1600>

**Target**: module-level repair iterates the data-only registry and loads capabilities before touching optional indexes. Modern trigram's derived representation is already declared by its FTS `content=` source; do not invent a second compact projection.

### 4.12 Destructive derived-index repair

**Current**: strategy 2 deletes only `sqlite_master` objects matching `messages_fts%`, then VACUUMs, `hermes_state.py:L1653-L1678`.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L1653-L1678>

Therefore broken `sessions_fts*` objects can survive a repair that reports success.

**Target**: destructive derived-index repair removes **owned derived FTS objects for all six members**, including owned source VIEWs and triggers, while preserving canonical `sessions` and `messages`. Reopen recreates supported derived schema. Tests must snapshot canonical rows before corruption and prove byte/value-equivalent canonical rows afterward.

Normal startup/quarantine remains stricter than the explicit destructive repair command: #30 unknown same-name objects still fail closed unless the repair path has positively classified them as Hermes-owned derived schema.

### 4.13 Read-only capability probing

**Current**: `SessionDB.__init__(read_only=True)`, `hermes_state.py:L2255-L2333`:

- probes `messages_fts`;
- probes `messages_fts_trigram`;
- loads CJK and only discovers session CJK table/pending/stale;
- does not discover `sessions_fts` or modern `sessions_fts_trigram` as first-class read-only capabilities.

Source: <https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state.py#L2255-L2333>

**Target**: one SELECT-only discovery pass over registry descriptors, with per-capability connection-local tokenizer loading and #30 ownership classification where needed. No DDL, stale mutation, trigger repair, H/P seeding, or delete-all from a read-only open.

Preserve #26 semantics: tokenizer capability is not search-serving availability. In particular, a pending/stale session CJK index remains non-serving even if the tokenizer loads. Avoid treating a read-only connection as a mutating worker merely because `_sessions_cjk_worker_operable` historically reused that concept.

### 4.14 Progress/status exposure

**Current**: generic `fts_rebuild_status(spec)` exists, but `optimize_fts_storage()`'s progress emitter and phase driver probe/execute message, message-CJK, session Unicode, session trigram, session CJK one-by-one. `fts_optimize_available()` similarly hard-codes each marker/stale lane.

Main ranges: `hermes_state_search.py:L293-L583` and `L1080-L1480`.
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_search.py#L293-L583>
<https://github.com/Skywind5487/hermes-agent/blob/919f4469e832bc2b38bba0ea5af26b842bf91acd/hermes_state_search.py#L1080-L1480>

**Target**: define a small shared rebuild-lane iteration surface for status/worker loops. This is separate from the index registry because H/P state is lane-specific. Do not alter final storage-layout stamp/refusal semantics here.

---

## 5. Mandatory questions — answers

### Q1. Minimum descriptor usable by module-level/offline + SessionDB without cycles?

A **data-only module-level descriptor in `hermes_state_common.py`**, no `self` callbacks. Static fields: table, source, row key, columns, owned trigger names, capability class, owned derived objects. SessionDB supplies dynamic applicability/ownership. Existing rebuild specs reference these descriptors.

### Q2. Which tuples become redundant vs remain different concerns?

Remove/derive as modern membership:

- `SessionDB._FTS_TABLES`;
- `_FTS_TRIGGERS`;
- `_FTS_CJK_TRIGGERS`;
- `_FTS_SESSION_CJK_TRIGGERS`;
- `_SESSIONS_TRIGRAM_MODERN_TRIGGER_NAMES` as an independent membership source.

Retain as different concerns:

- rebuild lane specs and independent marker/stale keys;
- exact canonical trigger DDL map for #30 ownership identity (derive its key set from the descriptor if convenient);
- legacy-layout-specific tuples/constants only in legacy migration code;
- worker/serving flags and stale recovery policy.

### Q3. Modern trigram derived-source repair/rebuild?

The descriptor names `sessions_fts_trigram_src` as the content source. Explicit FTS5 `rebuild` acts through the table's declared external-content source. Generic H/P rebuild continues to select from `_FTS_SESSION_TRIGRAM_SPEC`'s referenced descriptor source. Destructive repair knows the source VIEW is an owned derived object. **No compact SQL copy appears in maintenance/repair.** The canonical compact representation remains the #30 VIEW/policy.

### Q4. CJK optionality/quarantine without conflating worker and serving?

Capability is static (`cjk`), state is dynamic:

- connection can load tokenizer / operate owned target;
- durable index may be pending/stale;
- serving is true only when complete + non-stale + queryable.

Keep #26's `_sessions_cjk_worker_operable` versus `_sessions_cjk_available` semantics. Registry applicability helpers may ask separately for `operable` or `serving`; never replace both with `available`. Read-only probing derives serving without mutation.

Message CJK should gain the same generic rebuild-spec machinery, but its stale breadcrumb and tokenizer-loss behavior remain independent.

### Q5. Which broad session UPDATE triggers converge, and can machinery generalize?

For recognized Hermes-owned session schemas:

- `sessions_fts_update` → canonical `AFTER UPDATE OF title, id, display_name`;
- `sessions_fts_cjk_update` → same;
- modern trigram's `sessions_fts_trigram_update_before` and `_after` are already canonical narrow triggers and should be **validated by #30 exact identity**, not blindly rewritten.

The existing `_fts_update_trigger_needs_narrowing()` predicate is reusable. Generalize the candidate list/DDL selection through registry/owned-schema metadata, but preserve #30's foreign/unknown fail-closed rule.

---

## 6. Upstream ancestry / reuse decisions

### Merged + already in BASE: reuse, do not rewrite

1. **NousResearch/hermes-agent#76832** — crash-safe external-content optimize bookkeeping/reset/refusal seam; merge commit `1e2e69db989066047e5fce2cc0a0c24b24633c9f`. Fork comparison shows BASE is ahead with this ancestor. Reuse its reset-before-replay invariant and least-destructive repair shape.
2. **#76895** — read-only SessionDB capability-probe seam; merge commit `e38055a85e242dd999809155bf4f7d472508102d`. Fork BASE descends from it. Extend the existing read-only shape; do not create another read-only connection model.
3. **#77629** — optional capability must gate rebuild finish exactly as step; merge commit `2f32092b38dce4c3ade0c48897c6fab0edecb893`. Fork BASE descends from it. Preserve this invariant when message CJK moves to generic spec.

### Merged upstream but absent behavior: do not absorb into #27

**#81043** adds resumable high-water teardown for demoted legacy v22 trash. Current BASE still has the older chunk-by-`LIMIT` `_fts_teardown_trash_step` in `hermes_state_search.py:L413-L465`. That is real merged-upstream drift, but it is a legacy trash-teardown state machine, not six-index lifecycle membership. Do not cherry-pick it as part of #27 unless implementation unexpectedly edits that exact seam; track/sync separately.

### Open/unmerged: evidence only

- #71933 missing-table health classification — useful health/repair regression evidence, not a cherry-pick baseline.
- #73431 message source-exclusion changes triggers/rebuild projection — unrelated feature evidence only.
- #69798 broader multimodal/storage work — do not absorb; storage-v2 settlement is explicitly #31.

No whole-PR cherry-pick is required for #27.

---

## 7. Ordered commit-sized `/implement #27` plan

### Commit 1 — authoritative static membership + ordinary maintenance

- add data-only six-index `FtsIndexDescriptor`/`FTS_INDEXES` in `hermes_state_common.py`;
- make rebuild specs reference descriptors rather than repeat table/source/column identity;
- add generic message-CJK rebuild spec using existing shared chunk/status/finish engine;
- remove `SessionDB._FTS_TABLES` and move `optimize_fts`, bounded merge, explicit rebuild onto registry applicability;
- leave `vacuum()` unchanged except tests: its existing call to `optimize_fts()` should now reach all applicable members.

### Commit 2 — health + trigger inventory/convergence

- registry-driven health read probes;
- rollback write probe inserts non-null title/display_name;
- derive modern owned trigger inventory from registry;
- generalize broad→narrow migration for owned session Unicode/CJK updates;
- retain exact #30 modern trigram trigger identity/fail-closed behavior.

### Commit 3 — runtime/offline/destructive repair + degraded runtime

- runtime recovery continues through registry-driven `rebuild_fts()`;
- module-level least-destructive repair iterates static descriptors and respects optional capabilities;
- destructive derived-index repair removes owned six-index tables/shadows/views/triggers, never canonical data;
- global FTS5-unavailable trigger teardown derives owned triggers from registry;
- preserve dedicated CJK/trigram stale/quarantine state machines.

### Commit 4 — read-only capability + operational status iteration

- read-only init discovers existing session Unicode/CJK/trigram capabilities without DDL;
- enforce ownership for modern trigram before serving/maintenance;
- preserve worker-operable vs serving distinction;
- add shared rebuild-lane iteration for progress/status/foreground worker sequencing where it removes hard-coded ladders;
- **do not change `fts_storage_version`, final settle/refusal, or “fully optimized” rules**.

### Commit 5 — six-index regression matrix

- high-level disposable-DB tests across maintenance, corruption repair, trigger degradation, read-only discovery, and canonical-data preservation;
- delete obsolete assertions that encode message-only lifecycle membership.

---

## 8. RED-first test map

Prefer observable SessionDB/module-level behavior over SQL-string snapshots. Exact DDL comparison remains appropriate only where #30 ownership itself is the contract.

1. **ordinary optimize / bounded merge** — create all supported modern indexes; assert each applicable member receives its special command; tokenizer-unavailable optional members skip safely; unknown same-name session trigram is untouched.
2. **explicit rebuild** — corrupt each disposable index in turn; `rebuild_fts()` restores search for all applicable members. For session trigram, use data whose compact representation differs from raw text so a duplicated/raw projection would fail.
3. **VACUUM** — spy/monkeypatch shared optimize path and prove session indexes participate without a separate session VACUUM hook; preserve `sessions.row_id` across VACUUM.
4. **health read probe** — corrupt `sessions_fts`, `sessions_fts_cjk`, `sessions_fts_trigram` shadow/index data and require `_db_opens_cleanly()` to report the affected owned index.
5. **health rollback write probe** — break an owned session FTS insert/update path; non-null probe metadata must expose the failure; afterward assert the probe session/message do not exist.
6. **trigger narrowing** — legacy broad `sessions_fts_update` / `sessions_fts_cjk_update` converge to `UPDATE OF title,id,display_name`; heartbeat/token/accounting updates do not invoke FTS rewrites. Foreign trigram trigger namespace remains untouched/fail-closed.
7. **degraded runtime** — whole FTS5 loss drops all owned live triggers needed to keep canonical writes safe; CJK/trigram tokenizer-only loss persists stale before dropping only owned optional triggers and never invents completion.
8. **runtime corruption recovery** — a session-metadata write that hits corrupt session FTS invokes the existing one-shot rebuild/retry and succeeds; canonical session row content is preserved.
9. **offline strategy 0** — repairs all applicable owned six-index targets; unavailable optional tokenizer does not make Unicode repair fail.
10. **offline destructive fallback** — after intentionally broken derived schema, repair leaves no broken `sessions_fts*` objects behind, reopen recreates supported derived schema, and canonical `sessions`/`messages` snapshots are unchanged.
11. **read-only** — existing Unicode/CJK/trigram session capabilities are discovered on `mode=ro`; no DDL/meta writes occur; pending/stale lanes do not serve; unknown trigram fails closed.
12. **progress/status** — pending Unicode/trigram/CJK session work is surfaced through the shared operational iteration, while independent marker pairs remain independent.

Likely owning test files:

- `tests/test_hermes_state.py`
- `tests/test_fts_update_of_narrowing.py`
- `tests/test_fts_cjk_bigram.py`
- `tests/test_session_metadata_fts.py`
- `tests/test_session_metadata_cjk_fts.py`
- `tests/test_session_metadata_trigram_fts.py`
- `tests/test_state_db_malformed_repair.py`
- `tests/test_session_db_read_path_split.py`

---

## 9. Validation commands

Run the focused lifecycle matrix first:

```bash
pytest -q \
  tests/test_hermes_state.py \
  tests/test_fts_update_of_narrowing.py \
  tests/test_fts_cjk_bigram.py \
  tests/test_session_metadata_fts.py \
  tests/test_session_metadata_cjk_fts.py \
  tests/test_session_metadata_trigram_fts.py \
  tests/test_state_db_malformed_repair.py \
  tests/test_session_db_read_path_split.py
```

Then lint the touched production/tests:

```bash
ruff check \
  hermes_state.py hermes_state_common.py hermes_state_schema.py hermes_state_search.py \
  tests/test_hermes_state.py tests/test_fts_update_of_narrowing.py \
  tests/test_session_metadata_fts.py tests/test_session_metadata_cjk_fts.py \
  tests/test_session_metadata_trigram_fts.py tests/test_state_db_malformed_repair.py \
  tests/test_session_db_read_path_split.py
```

On a CJK-capable host, run the CJK-specific suites with the loadable tokenizer present. Capability-skipped tests are not evidence that the CJK lifecycle works.

---

## 10. Pitfalls / non-goals

1. **Do not create a universal FTS framework.** Static membership is shared; search routing, H/P ownership, stale recovery, and serving state are different concerns.
2. **Do not conflate CJK worker capability and serving availability.** Pending CJK is intentionally W=1/S=0.
3. **Do not let registry membership authorize mutation of `sessions_fts_trigram`.** #30 exact schema/source/trigger ownership remains the gate; unknown same-name objects stay untouched.
4. **Do not duplicate #30 compact SQL** in rebuild/repair. The derived VIEW is the representation.
5. **Do not flatten independent rebuild marker pairs.** Processed completeness is target-specific.
6. **Do not add a session-specific VACUUM hook.** Fix ordinary maintenance membership once.
7. **Do not redesign search routing/ranking/fallback.** #14/#28 own that.
8. **Do not revive historical fork-only `sessions_fts_trigram(tokenize='simple')` compatibility.** #30 explicitly removed it; remaining global simple EOL is #19.
9. **Do not absorb upstream #81043 legacy trash-teardown work** unless an exact implementation overlap forces it.
10. **Do not alter final storage settlement here.** #31 owns the next point.

---

## 11. Explicit #31 boundary

#27 may make maintenance/status code aware that work exists. It must **not** decide when the database is finally stamped `fts_storage_version = 2`, what final required/optional completion means for that stamp, or how startup/foreground settlement refusal is persisted/retried.

Those are a separate state machine owned only by **#31**.

Concretely, while implementing #27:

- do not bump `FTS_STORAGE_VERSION`;
- do not change the final `optimize_fts_storage()` settlement stamp to v2;
- do not add v2 startup/refusal meta;
- do not make CJK optionality into a storage-version policy decision.

The implementation should leave #31 a cleaner registry/status substrate, not pre-solve #31.

---

## 12. Final handoff state

**No blocker. No split. #27 can be marked `ready-for-agent` once this research handoff is posted.**

`/implement #27` should verify that `dev` still descends from pinned BASE_SHA `919f4469e832bc2b38bba0ea5af26b842bf91acd`; if so, it should execute the commit plan above rather than repeating the lifecycle audit.