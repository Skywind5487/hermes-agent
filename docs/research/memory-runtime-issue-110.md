# Memory Runtime reconstruction preflight — issue #110

Date: 2026-08-17

## Authority / pinned refs

- Phase-2 ticket: #110 (`repair:memory-runtime`).
- Composition authority: PR #108 at `5aa4f4e27ccf2169beb4fc1f1d1eeb655d13b548` — `line:memory-trim-diagnostics EXTENDS line:memory-trim-policy`; diagnostics must fail open.
- Phase-1 accounting: PR #104 at `f81cd921a89516d855b5b69906ce99e6351bc741`.
- Frozen fork evidence: current `dev` at preflight time `fa5ed679cc6559c619038f327e6276f4b7e8d735`.
- Reconstruction substrate: upstream `NousResearch/hermes-agent` `main` at `3b9a963b8e5cdb804a422755bed9a60fcd778273`.

## Upstream prior art / current authority

| Upstream work | State at preflight | Use here |
|---|---|---|
| #76905 — config-driven allocator trim with telemetry | **merged** | Current authority. Reuse `hermes_cli.mem_trim.trim_memory`, config loading, glibc probe, lifecycle wiring, cooldown and force-floor seams. |
| #77356 — post-compression trim | **merged** | Confirms compression should call the same `trim_memory()` seam rather than introduce a second GC path. |
| #81127 — gateway agent-cache memory-pressure bound | **merged** | Adjacent memory-pressure policy; it calls the current trim seam after releasing cache references. It does not implement #110's low-water/GC-cooldown contract. |
| #66355 — earlier allocator trim consolidation | closed, unmerged | Superseded by #76905; provenance/design evidence only. |
| #64591 — periodic idle-reaper trim | closed, unmerged | Absorbed by the later consolidated trim implementation; do not replay its branch shape. |
| #63708 — earlier config-driven trim | currently open, unmerged | Superseded semantically by merged #76905 even though the old PR is currently open; not current authority. |
| #46022 — proactive GC above 400 MB | closed, unmerged | Rejected shape for this reconstruction. Review identified its RSS source as a high-water mark, which could retrigger full GC forever after one crossing. |
| #80974 — GC after large tool results | currently open, unmerged | Design evidence only. It is not current-main authority and is outside the `trim_memory()` housekeeping contract reconstructed here. |

## Fork residual contract

Phase-1 provenance identifies two residual capabilities:

1. `capability:memory-trim-policy`
   - `threshold_mb` is an RSS low-water gate for non-forced housekeeping work.
   - `gc.collect()` has an independent `gc_cooldown_seconds` (historical default 300 s).
   - `malloc_trim(0)` remains eligible on the normal trim cadence even when GC is cooling down.
   - force and invalid configuration paths remain safe.
2. `capability:memory-trim-diagnostics`
   - log GC and allocator-trim cost separately (`gc_ms`, `trim_ms`).
   - expose `VmSwap` and pre-trim glibc fragmentation evidence where supported.
   - diagnostics are best-effort and must never be required for recovery.

Historical evidence: fork commits `04f1af72be078cef69de538f1519f93a73088b0d` and `a9d2b9af4f800fef23fa7ecaf2ea270b43e326eb`.

## Reconstruction decisions

This is a conscious port onto current upstream, not a cherry-pick of the old fork implementation.

- Keep the merged #76905 `trim_memory()` seam and all current lifecycle callers unchanged.
- Use current `/proc/self/status` `VmRSS` as the low-water signal. If current RSS is unavailable, fail open and keep recovery eligible; do **not** treat missing RSS as zero.
- A low-water skip is not a trim attempt and therefore must not consume the normal cooldown or the 5-second forced-close floor.
- Keep GC state separate from allocator-trim state. A cooling-down GC does not suppress `malloc_trim(0)`.
- Coerce `gc_cooldown_seconds` with its own 300-second fallback rather than accidentally falling back to the allocator trim's 60-second default.
- Preserve `force=True` as a bypass of the RSS gate and GC cooldown while retaining upstream's burst-close force floor.
- Rebuild fragmentation collection as best-effort glibc instrumentation without the historical predictable `/tmp/hermes_malloc_info_<pid>.xml` pathname. Diagnostic failure returns no diagnostic data and cannot veto trim policy.
- Read `malloc_info` pre-trim so fragmentation describes the state that motivated recovery.

## Acceptance mapping

Tests for this feature line must cover:

- below-threshold skip and unavailable-RSS fail-open behavior;
- low-water skips not suppressing a following forced trim;
- independent GC cooldown with allocator trim still running;
- force bypass of the low-water and GC-cooldown gates;
- invalid policy values falling back safely;
- `VmSwap` parsing;
- GC-vs-trim timing in the operator-visible log;
- fragmentation parsing/collection and diagnostic failure not blocking trim.
