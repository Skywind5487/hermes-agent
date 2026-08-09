# Session-search lineage research

This directory is the durable repo home for session-lineage winner-resolution research.

## Current source of truth

- **#54 — current umbrella/final gate.**
- **`benchmark/FOCUSED_GATE.md` — current resolver decision surface and VM handoff.**
- `benchmark/run.py --focused-gate` — current production-VM measurement entry point.
- #60 — separate ChatGPT historical import/merge provenance archaeology; it does not block the focused resolver VM run unless it produces evidence that changes the historical workload interpretation.

> Older `benchmark/README.md`, `benchmark/GATE.md`, TEMP-vs-Fixed duel material, and early #54 comments remain historical measurement evidence. Their "Pure TEMP vs Fixed3 only" finalist framing is superseded by `FOCUSED_GATE.md`.

No production winner or final global work budget `B` is selected yet.

## Current decision surface

The focused gate compares:

1. **simple ranked sequential point traversal, no memo**;
2. **the same scheduler with a tiny query-local Python `node -> root` memo/path compression**;
3. **Pure TEMP** as the established memo/overlap reference;
4. **Fixed3 shared CTE** as the established one-statement SQL reference.

The first two are the current KISS production candidates. TEMP and Fixed3 are references, not privileged finalists.

A lazy-candidate SQL state machine remains a separate SQL exploration direction and is intentionally not implemented in this gate.

## Workload classes

Keep these separate rather than blending them into one average:

### Hermes-normal

Observed current positive compression ancestry is shallow. The focused fixtures use depth 0/1 with `K=3` reached immediately, so candidate-level early stop is visible.

### Historical/import compatibility

The robust frozen-corpus extreme is depth 14 / lineage size 15. The focused fixture ranks all 15 members deepest-to-root before two independent roots, maximizing repeated ancestry before `K=3`.

### Safety only

Synthetic 5k/10k chains exist only for cycle/missing/runaway-work and global-`B` safety. They are not normal performance weighting.

## Focused benchmark artifacts

- `benchmark/FOCUSED_GATE.md` — current gate rationale and handoff.
- `benchmark/python_memo.py` — hardened Python dict/path-compression candidate.
- `benchmark/per_seed.py` — simple no-memo sequential candidate.
- `benchmark/focused_scenarios.py` — shallow normal + depth14/size15 historical fixtures.
- `benchmark/focused_vm_gate.py` — production-VM focused runner.
- `benchmark/tests/test_focused_gate.py` — correctness/reuse/bound checks.
- `benchmark/temp_memo.py` — TEMP reference.
- `benchmark/fixed3_optimized.py` — Fixed3 SQL reference.

## Evidence / historical research

- #45 — algorithm/design-space convergence and local graveyard/final-duel discussion; superseded as current status by #54.
- #46 / PR #49 — code / exact-line / production semantic discrepancy.
- #47 / PR #48 — upstream and analogous prior art.
- #50 / PR #53 — TEMP connection lifecycle, WAL read connection, snapshot transaction, and non-WAL fallback mechanics.
- #51 / PR #52 — depth-fuse provenance and bounded-work safety intent.
- #58 — Hermes-native compaction lifecycle vs imported ChatGPT-history provenance boundary.
- #60 — ChatGPT DB/chat archaeology.
- #29 — historical ADR / exploratory benchmark log.

## Important invariants

The resolver operates after ranked **distinct owning-session candidates** already exist. Duplicate FTS message rows are not lineage work.

Cycle protection, missing-parent fail-closed behavior, positive compression-edge semantics, one logical read snapshot, and a global work budget are requirements independent of the chosen scheduler.

Do not paste another large benchmark implementation into issues now that the repo package exists.
