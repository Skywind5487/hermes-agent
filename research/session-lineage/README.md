# Session-search lineage research

This directory is the durable repo home for the research that supersedes the long-form discussion in issue #29.

## Source of truth

- Active algorithm / benchmark decision ticket: #45
- Code / exact-line / provenance research: #46
- Upstream and analogous prior-art research: #47
- Historical ADR / exploratory benchmark log: #29
- Production candidate-search dependency: #14

Issue #29 is historical evidence, not the active decision record.

## Current research model

Every algorithm is described on three independent axes:

1. **Scheduling / 走法** — sequential, shared, rank-priority shared, or memory-preserving scheduler switch.
2. **Reuse / 記憶** — none, result-only memory, or accumulated known coverage.
3. **Representation / 容器** — recursive SQL set/queue, completed CTE results, Python dict, or another query-local representation.

Cycle protection, missing-parent behavior, compression-edge semantics, and runaway-work bounds are cross-cutting safety requirements rather than separate algorithm families.

## Benchmark provenance

The current `hermes_lineage_benchmark.py` is a reconstructed harness created on 2026-08-09 after the original exploratory source was not persisted. Its exact reconstructed source is preserved in four comments on #29 and in the saved benchmark bundle. It is useful for algorithm-shape evidence, but its existing correctness oracle and scenario matrix are not authoritative enough for the next decision.

The next benchmark revision should live in this directory as normal versioned repo code, e.g.:

- `benchmark.py` — algorithm runners + measurements;
- `scenarios.md` or a data module — workload definitions and rationale;
- correctness fixtures separated from performance workloads.

Do not paste another large benchmark source into an issue once the repo version exists.

## Benchmark-v2 workload model

Model the ranked **candidate-to-lineage distribution after search**. Topic similarity is not lineage identity.

Canonical axes:

- lineage diversity: one / few / many / dominant + long tail;
- rank placement: winners early / duplicate prefix / interleaved / clustered;
- ancestry depth: boundary / shallow / deep;
- candidate density within lineage: dense / sparse-gapped / one-per-lineage;
- candidate count: small / medium / upper scan regime;
- result K: at least 1 / 3 / 10, without assuming 3 is fixed.

Representative user-facing shapes:

- project-focused query: concentrated in one/few project lineages;
- handoff/fresh-session continuation: same semantic project across independent roots;
- common/daily lookup: many unrelated roots;
- mixed/Zipf: one dominant lineage plus incidental roots.

Correctness/adversarial fixtures must separately cover compression vs branch/delegation/tool boundaries, cycle, missing parent, current-lineage exclusions, legacy rotated histories vs in-place compaction, and work-budget safety.

## Explicit discussion-only directions

Do not promote these without new evidence:

- TEMP-table memo;
- single-row recursive state machine / encoded `covered` blob;
- persistent root column except as a theoretical read-path lower bound;
- full sessions-graph load into Python;
- downward component flood relying on unique compression child;
- candidate contraction followed by independent per-head traversal.

## Immediate research questions

- What minimum knowledge representation can be consumed by both sequential and shared schedulers?
- Can a scheduler switch preserve already-discovered coverage instead of restarting?
- Can candidate-local shared contraction add anything beyond shared traversal itself?
- How far can SQLite queue priority + streaming limits go before seed-specific state prevents merging?
- Is Python dict memo the simpler solution once SQL requires mutable coverage semantics?
- What failure intent does the current depth fuse protect, and can a clearer work guard replace it?
