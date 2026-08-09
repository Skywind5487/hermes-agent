# Session-search lineage research

This directory is the durable repo home for session-lineage winner-resolution research.

## Current source of truth

- **#54 — current umbrella/final gate:** measurement-space audit + final `Pure TEMP` vs fixed 3-stage shared decision on the production VM.
- `research/session-lineage/benchmark/README.md` — durable benchmark method, orthogonal measurement basis, coverage, data generation, results map, planner traps, and explicit unmeasured cells.
- `research/session-lineage/benchmark/run.py` — clone-and-run local two-finalist entry point.

No production winner is selected yet.

## Evidence / historical research

- #45 — algorithm/design-space convergence and local graveyard/final-duel discussion; superseded as the current status ticket by #54.
- #46 / PR #49 — code / exact-line / provenance research and production semantic discrepancy.
- #47 / PR #48 — upstream and analogous prior art.
- #50 / PR #53 — TEMP connection lifecycle, WAL read connection, explicit snapshot transaction, and non-WAL fallback mechanics.
- #51 / PR #52 — depth-64 provenance and bounded-work safety intent.
- #29 — historical ADR / exploratory benchmark log. Historical evidence only.
- #14 — production candidate-search dependency / ordering context.

## Research model

Keep three algorithm axes separate:

1. **Scheduling / 走法** — ranked sequential, shared batch, staged, or hybrid.
2. **Reuse / 記憶** — none, completed immutable coverage, or mutable keyed `node -> root` memo.
3. **Representation / 容器** — recursive SQL set/queue, completed CTE results, TEMP table, Python reference, or persistent-schema lower bound.

Cycle protection, missing-parent behavior, compression-edge semantics, and runaway-work bounds are cross-cutting safety requirements rather than separate algorithm families.

## Benchmark model

The benchmark starts after ranked **distinct candidate sessions** already exist. It measures lineage resolution/winner selection, not FTS/title candidate generation or final hydration.

The measurable-space audit is more important than any one scenario name. Its workload/environment/resource bases include:

- candidate count and result K;
- lineage diversity/concentration and Kth-root rank;
- compression-depth distribution and candidate density within lineages;
- blocked/interleaved/random ordering and modern-vs-legacy mix;
- reachability and malformed/cycle/missing/boundary semantics;
- algorithm, cache/connection state, DB background size, journal route, TEMP policy, runtime/hardware;
- work budget, statements, memory, and disk/temp footprint.

Current local work fully crossed `23 algorithms × 16 performance scenarios × 2 cache modes`, then narrowed to the two finalists. The workload bases are a designed fractional-factorial sample, not a full Cartesian product. See `benchmark/README.md` for exact cells and missing dimensions.

## Current finalists

1. **Pure TEMP keyed memo** — ranked early stop with mutable query-local `node -> root`; multi-statement logical search requires an explicit read transaction for snapshot consistency.
2. **Fixed 3-stage shared CTE** — one SQLite statement, no new table, completed-stage reuse, and global bounded work.

Local synthetic/WAL evidence leans TEMP but is **not** the production decision. #54 must add the e2-micro/runtime gate, memory/disk accounting, true/closer VM-cold behavior, and a bounded non-WAL fallback measurement.

## Benchmark artifacts

- `benchmark/README.md` — methods + measurable-space coverage + findings + limitations.
- `benchmark/measurement_space.csv` — scenario coordinates in the orthogonalized workload basis.
- `benchmark/scenarios.py` — deterministic synthetic data/scenario generator.
- `benchmark/temp_memo.py` — TEMP finalist implementation used by the clone-runnable harness.
- `benchmark/fixed3_optimized.py` — planner-corrected fixed-shared finalist.
- `benchmark/final_duel.py` — two-finalist measurement runner.
- `benchmark/run.py` — easy local entry point.
- `benchmark/results/graveyard_summary.csv` — aggregate results for 23 historical/current implemented resolver shapes.
- `benchmark/results/correctness_summary.csv` — graveyard semantic/adversarial summary.
- `benchmark/results/final_duel_combined.csv` — per-scenario local finalist evidence.

Do not paste another large benchmark source into an issue now that the repo benchmark package exists.
