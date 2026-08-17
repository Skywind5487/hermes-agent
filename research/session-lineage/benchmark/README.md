# Session-lineage benchmark: measurable space + current gate

> **Current umbrella/source of truth: #54.**
>
> **Current resolver decision/handoff: `FOCUSED_GATE.md`.**
>
> This file preserves the broader measurement-space audit and historical TEMP-vs-Fixed work. Its older "Pure TEMP vs Fixed3 only" finalist framing is superseded by `FOCUSED_GATE.md`.

## Current decision surface

The narrowed production gate compares:

1. simple ranked sequential point traversal, no memo;
2. the same scheduler with query-local Python `node -> root` path compression;
3. Pure TEMP as an established reference;
4. Fixed3 shared CTE as an established SQL reference.

The first two are the current KISS production candidates. TEMP and Fixed3 remain useful reference points, not privileged finalists.

A lazy-candidate SQL state machine is a separate SQL exploration direction and is intentionally outside the focused gate.

## Why this supersedes the old finalist framing

The resolver receives ranked **distinct owning-session candidates**. Duplicate FTS/message hits have already been collapsed.

Current evidence separates three workload classes:

- **Hermes-normal:** observed positive compression ancestry is shallow; focused fixtures use depth 0/1 and early `K=3`.
- **Historical/import compatibility:** robust frozen-corpus extreme is depth 14 / lineage size 15; focused fixtures deliberately rank all 15 members deepest-to-root.
- **Safety only:** synthetic 5k/10k chains exist only for global-work-budget/cycle/malformed protection and must not be normal performance weighting.

The focused runner and exact decision rules live in `FOCUSED_GATE.md`.

## Broader measurement-space archive

This directory still preserves the earlier broader experiment because it contains useful evidence that should not be lost:

- 23 implemented graveyard resolver shapes;
- correctness fixtures for root/compression/fork/delegation/tool/missing/cycle;
- TEMP vs Fixed3 warm/cold-ish duel results;
- planner/materialization and DB-size probes;
- global-bound smoke;
- non-WAL, TEMP-store, lifecycle, resource, and production-topology scripts;
- deterministic synthetic scenario generation and historical results under `results/`.

The benchmark seam remains:

```text
ranked distinct candidate sessions
    -> lineage resolution / dedupe
    -> first K distinct lineage winners
```

It does not measure FTS/title/LIKE candidate generation, snippets, hydration, gateway latency, or the full end-to-end `session_search()` path.

## Important historical findings retained

### Planner trap

An early Fixed3 query allowed SQLite to reorder a stage join into a full child/session scan. The corrected implementation forces lookup-shaped join order. Keep the EQP assertion; do not reuse pre-fix DB-size cliff numbers as intrinsic Fixed3 scaling.

### Fixed3 first-prepare cost

The giant statement has a notable first invocation/planner cost on a fresh local reader, then drops sharply on reuse. The exact magnitude must be measured on deployment SQLite rather than generalized from local runs.

### Python memo provenance

Graveyard #19 already implemented ranked sequential candidate traversal with a query-local Python dict/path compression. It was not eliminated by a correctness/performance loss; it was excluded later by an SQLite-first scope decision. `python_memo.py` is the hardened focused-gate form of that known design.

### Very deep synthetic chains

5k/10k chains are safety/B probes only. They are not workload evidence and must not be averaged into the normal architecture decision.

## Current focused artifacts

- `FOCUSED_GATE.md` — current decision source of truth.
- `python_memo.py` — hardened Python memo/path-compression candidate.
- `per_seed.py` — no-memo sequential candidate.
- `focused_scenarios.py` — shallow normal + real depth14/size15 compatibility fixtures.
- `focused_vm_gate.py` — focused VM runner.
- `tests/test_focused_gate.py` — correctness/reuse/bound tests.
- `temp_memo.py` — TEMP reference.
- `fixed3_optimized.py` — Fixed3 reference.

## Historical / broad gate artifacts

- `GATE.md` — broader VM/WSL gate addendum.
- `vm_gate.py` / `gate_sweeps.py` — broader environment/resource sweeps.
- `production_profile.py`, `production_tail.py`, `distribution_profile.py`, `edge_semantics_audit.py` — frozen-corpus topology/provenance evidence.
- `final_duel.py` — historical TEMP-vs-Fixed duel.
- `measurement_space.csv` — historical synthetic measurement coordinates.
- `results/` — historical aggregate evidence.

## Current clone-and-run

Focused smoke:

```bash
python research/session-lineage/benchmark/run.py \
  --quick-focused-gate \
  --output-dir /tmp/hermes-lineage-smoke
```

Focused production-VM run:

```bash
python research/session-lineage/benchmark/run.py \
  --focused-gate \
  --output-dir /tmp/hermes-lineage-20260809
```

Broader historical gates remain available through `--gate`, `--quick-gate`, and `--full`, but they are not the next decision step.

## `B` remains separate

Do not derive the global work budget from max depth alone. Existing frozen-corpus envelopes established in #54 are:

```text
C <= 300   -> <= 554 successful node visits
C <= 1000  -> <= 1254 successful node visits
one real 15-node lineage, worst no-memo ranking -> <= 120 visits
```

The final malformed/future-DB safety fuse must be chosen separately from architecture winner selection using the VM safety curve.

## Provenance boundary

#60 independently reconstructs ChatGPT historical import/merge behavior from archived chats + DB fields. That work may refine interpretation of pre-Hermes historical topology, but it does not block the focused VM gate unless it produces evidence that materially changes the compatibility envelope.

No production winner and no final global `B` are selected yet.
