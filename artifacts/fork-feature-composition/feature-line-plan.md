# Phase-1.5 Feature-Line Composition Plan

This report composes Phase-1 capability contracts into reconstruction units. Capability remains the preservation/accounting unit; feature line is the reconstruction/maintenance unit.

- Phase-1 manifest head: `f81cd921a89516d855b5b69906ce99e6351bc741`
- Current fork reconstruction base: `fa5ed679cc6559c619038f327e6276f4b7e8d735`
- Current upstream main: `56526bc0d36522ab7a87ee0056f70e3847d2f0e6`
- Capabilities covered: **22**
- Non-capability groups accounted: **4**
- Final hard-dependency blockers: **0**

## Composition decisions

- FTS lifecycle + storage-v2 + Unicode form the smallest valid metadata-search substrate.
- CJK and trigram are optional extensions; routing requires the core but tolerates unavailable variants.
- Title safety shares session substrate only; it is not an FTS dependency.
- Headroom retrieval requires compression; compression remains independently useful.
- Memory diagnostics extends policy and must fail open.
- Compression session-boundary and compression-lineage search share substrate only.
- Lifecycle telemetry is an optional independent observability line, not a hidden dependency of every feature.

## Feature-line summary

| Line | Family | Role | Owned capabilities |
|---|---|---|---|
| `line:session-metadata-search-core` | `session-metadata-search` | `shared_substrate` | `capability:session-fts-storage-v2`, `capability:session-fts-lifecycle`, `capability:session-fts-unicode` |
| `line:session-metadata-search-cjk` | `session-metadata-search` | `optional_extension` | `capability:session-fts-cjk` |
| `line:session-metadata-search-trigram` | `session-metadata-search` | `optional_extension` | `capability:session-fts-trigram` |
| `line:session-metadata-search-routing` | `session-metadata-search` | `vertical_feature` | `capability:session-search-routing` |
| `line:session-title-safety` | `session-safety` | `vertical_feature` | `capability:session-title-safety` |
| `line:session-search-lineage` | `session-content-search` | `vertical_feature` | `capability:session-search-lineage` |
| `line:session-search-context-hydration` | `session-content-search` | `vertical_feature` | `capability:session-search-context-hydration` |
| `line:headroom-compression` | `headroom` | `vertical_feature` | `capability:headroom-compression` |
| `line:headroom-retrieval` | `headroom` | `optional_extension` | `capability:headroom-retrieval` |
| `line:memory-trim-policy` | `memory-runtime` | `vertical_feature` | `capability:memory-trim-policy` |
| `line:memory-trim-diagnostics` | `memory-runtime` | `optional_extension` | `capability:memory-trim-diagnostics` |
| `line:compression-session-boundary` | `compression-lifecycle` | `vertical_feature` | `capability:compression-session-boundary` |
| `line:runtime-lifecycle-observability` | `runtime-observability` | `optional_extension` | `capability:lifecycle-sqlite-telemetry` |
| `line:sqlite-write-contention-policy` | `runtime-reliability` | `shared_substrate` | `capability:sqlite-write-contention-policy` |
| `line:browser-timeout-cleanup` | `browser-runtime` | `vertical_feature` | `capability:browser-timeout-cleanup` |
| `line:configurable-reasoning-display` | `display-and-request` | `vertical_feature` | `capability:configurable-reasoning-display` |
| `line:request-transform-hook` | `display-and-request` | `vertical_feature` | `capability:request-transform-hook` |

## Relationship graph

| From | Type | To | Meaning |
|---|---|---|---|
| `line:session-metadata-search-cjk` | `EXTENDS` | `line:session-metadata-search-core` | CJK is optional and degrades to the core route. |
| `line:session-metadata-search-trigram` | `EXTENDS` | `line:session-metadata-search-core` | Trigram is optional and degrades to bounded fallback/core search. |
| `line:session-metadata-search-routing` | `REQUIRES` | `line:session-metadata-search-core` | Routing needs a valid core candidate/index contract. |
| `line:session-metadata-search-routing` | `REQUIRES` | `line:session-search-lineage` | The routing contract explicitly promises lineage/branch-marker correctness during result hydration. |
| `line:session-metadata-search-routing` | `SHARES_SUBSTRATE` | `line:session-metadata-search-cjk` | Routing knows the optional route but remains valid when it is unavailable. |
| `line:session-metadata-search-routing` | `SHARES_SUBSTRATE` | `line:session-metadata-search-trigram` | Routing knows the optional route but remains valid when it is unavailable. |
| `line:session-title-safety` | `SHARES_SUBSTRATE` | `line:session-metadata-search-core` | Title safety shares session storage/callers only. |
| `line:headroom-retrieval` | `REQUIRES` | `line:headroom-compression` | Retrieval has no meaningful compressed context without compression. |
| `line:memory-trim-diagnostics` | `EXTENDS` | `line:memory-trim-policy` | Policy remains correct when best-effort diagnostics are absent. |
| `line:session-search-context-hydration` | `SHARES_SUBSTRATE` | `line:session-search-lineage` | Both read session messages/lineage but neither requires the other's implementation. |
| `line:session-search-context-hydration` | `SHARES_SUBSTRATE` | `line:session-metadata-search-core` | Both use session search storage; context can be accepted with another result producer. |
| `line:compression-session-boundary` | `SHARES_SUBSTRATE` | `line:session-search-lineage` | Compression creates lineage records, but local no-op boundary semantics are independently testable. |
| `line:runtime-lifecycle-observability` | `SHARES_SUBSTRATE` | `line:compression-session-boundary` | Telemetry observes compression lifecycle but does not gate it. |
| `line:runtime-lifecycle-observability` | `SHARES_SUBSTRATE` | `line:sqlite-write-contention-policy` | Telemetry observes SQLite writes but does not own retry policy. |

## Coverage

| Capability | Phase-1 disposition | Outcome | Owning lines |
|---|---|---|---|
| `capability:browser-timeout-cleanup` | `PORT` | `FEATURE_LINE` | `line:browser-timeout-cleanup` |
| `capability:compression-session-boundary` | `SPLIT` | `FEATURE_LINE` | `line:compression-session-boundary` |
| `capability:configurable-reasoning-display` | `PORT` | `FEATURE_LINE` | `line:configurable-reasoning-display` |
| `capability:cron-nul-safety` | `DROP` | `UPSTREAM_OWNED` | upstream-owned |
| `capability:headroom-compression` | `PORT` | `FEATURE_LINE` | `line:headroom-compression` |
| `capability:headroom-retrieval` | `PORT` | `FEATURE_LINE` | `line:headroom-retrieval` |
| `capability:lifecycle-sqlite-telemetry` | `NEEDS_REVIEW` | `FEATURE_LINE` | `line:runtime-lifecycle-observability` |
| `capability:memory-trim-diagnostics` | `KEEP` | `FEATURE_LINE` | `line:memory-trim-diagnostics` |
| `capability:memory-trim-policy` | `PORT` | `FEATURE_LINE` | `line:memory-trim-policy` |
| `capability:outbound-code-fence-safety` | `DROP` | `UPSTREAM_OWNED` | upstream-owned |
| `capability:request-transform-hook` | `PORT` | `FEATURE_LINE` | `line:request-transform-hook` |
| `capability:session-fts-cjk` | `PORT` | `FEATURE_LINE` | `line:session-metadata-search-cjk` |
| `capability:session-fts-lifecycle` | `PORT` | `FEATURE_LINE` | `line:session-metadata-search-core` |
| `capability:session-fts-simple-eol` | `DROP` | `UPSTREAM_OWNED` | upstream-owned |
| `capability:session-fts-storage-v2` | `PORT` | `FEATURE_LINE` | `line:session-metadata-search-core` |
| `capability:session-fts-trigram` | `PORT` | `FEATURE_LINE` | `line:session-metadata-search-trigram` |
| `capability:session-fts-unicode` | `PORT` | `FEATURE_LINE` | `line:session-metadata-search-core` |
| `capability:session-search-context-hydration` | `PORT` | `FEATURE_LINE` | `line:session-search-context-hydration` |
| `capability:session-search-lineage` | `SPLIT` | `FEATURE_LINE` | `line:session-search-lineage` |
| `capability:session-search-routing` | `PORT` | `FEATURE_LINE` | `line:session-metadata-search-routing` |
| `capability:session-title-safety` | `SPLIT` | `FEATURE_LINE` | `line:session-title-safety` |
| `capability:sqlite-write-contention-policy` | `PORT` | `FEATURE_LINE` | `line:sqlite-write-contention-policy` |

## Reconstruction waves

### Wave 1 — parallelizable

Independent acceptance seams; shared files are handled as integration risk, not runtime dependency.

- `line:session-metadata-search-core`
- `line:session-search-lineage`
- `line:headroom-compression`
- `line:memory-trim-policy`
- `line:compression-session-boundary`
- `line:runtime-lifecycle-observability`
- `line:sqlite-write-contention-policy`
- `line:browser-timeout-cleanup`
- `line:configurable-reasoning-display`
- `line:request-transform-hook`

### Wave 2 — ordered/extensions

Extensions and coordinators follow the substrate or optional base lines they consume.

- `line:session-metadata-search-cjk`
- `line:session-metadata-search-trigram`
- `line:session-metadata-search-routing`
- `line:session-title-safety`
- `line:session-search-context-hydration`
- `line:headroom-retrieval`
- `line:memory-trim-diagnostics`

## Non-capability evidence

| Group | Attachments | Decision |
|---|---|---|
| `non-capability:incidental-hardening` | `line:sqlite-write-contention-policy`, `line:runtime-lifecycle-observability` | Retry policy is promoted as a capability overlay; remaining slow-write/delivery logs attach as evidence and do not create a feature line. |
| `non-capability:integration-merge` | none | Accounting/provenance evidence only; no feature line owns this group. |
| `non-capability:performance-validation` | `line:session-search-context-hydration`, `line:session-metadata-search-routing` | Benchmark evidence informs the two affected acceptance seams; the benchmark record itself is not a feature line. |
| `non-capability:production-evidence` | none | Accounting/provenance evidence only; no feature line owns this group. |

## /to-tickets projection

No implementation tickets or Phase-2 branches are created by this issue. The following is the later projection:

- `line:session-metadata-search-core` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.
- `line:session-metadata-search-cjk` — proposed implementation ticket; requires none; wave 2; targeted research preflight: yes.
- `line:session-metadata-search-trigram` — proposed implementation ticket; requires none; wave 2; targeted research preflight: yes.
- `line:session-metadata-search-routing` — proposed implementation ticket; requires `line:session-metadata-search-core`, `line:session-search-lineage`; wave 2; targeted research preflight: yes.
- `line:session-title-safety` — proposed implementation ticket; requires none; wave 2; targeted research preflight: yes.
- `line:session-search-lineage` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.
- `line:session-search-context-hydration` — proposed implementation ticket; requires none; wave 2; targeted research preflight: yes.
- `line:headroom-compression` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.
- `line:headroom-retrieval` — proposed implementation ticket; requires `line:headroom-compression`; wave 2; targeted research preflight: yes.
- `line:memory-trim-policy` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.
- `line:memory-trim-diagnostics` — proposed implementation ticket; requires none; wave 2; targeted research preflight: yes.
- `line:compression-session-boundary` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.
- `line:runtime-lifecycle-observability` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.
- `line:sqlite-write-contention-policy` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.
- `line:browser-timeout-cleanup` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.
- `line:configurable-reasoning-display` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.
- `line:request-transform-hook` — proposed implementation ticket; requires none; wave 1; targeted research preflight: yes.

Upstream-owned capabilities receive no reconstruction ticket: `capability:cron-nul-safety`, `capability:outbound-code-fence-safety`, `capability:session-fts-simple-eol`.
