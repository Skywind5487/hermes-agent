# #54 production gate addendum — historical broad gate

> **Superseded for the current resolver architecture decision by `FOCUSED_GATE.md`.**
>
> Keep this file as the broader environment/resource gate archive. Its earlier
> "Pure TEMP vs Fixed3 remain the finalists" framing is no longer current.

The scripts described here remain useful for broader environment questions:

- VM/runtime receipt;
- DB-size scaling;
- Fixed3 EQP regression checks;
- first/second/warm reader lifecycle;
- non-WAL rollback-journal fallback;
- TEMP-store policy;
- pathological `B` curves;
- frozen production-topology profiling.

For the next production decision, run:

```bash
python research/session-lineage/benchmark/run.py \
  --focused-gate \
  --output-dir /tmp/hermes-lineage-20260809
```

See `FOCUSED_GATE.md` for the current no-memo vs Python-memo decision surface and the role of TEMP/Fixed3 as references.

## Broad synthetic gate

The original broad gate is still available:

```bash
python research/session-lineage/benchmark/run.py --gate \
  --output-dir /tmp/hermes-lineage-broad
```

Fast smoke:

```bash
python research/session-lineage/benchmark/run.py --quick-gate \
  --output-dir /tmp/hermes-lineage-broad-smoke
```

`vm_gate.py` never opens a production `state.db`. It records a machine/runtime receipt and emits small-C, full-consume, pathological-budget, DB-size, lifecycle, non-WAL, TEMP-store, and EQP evidence.

## Frozen recovered production topology

The recovery source of truth remains the read-only canonical DB named by #20/#22 and the production profiler. Do not bypass its SHA/sidecar safety checks.

Static topology evidence is separate from post-search candidate telemetry. The focused architecture gate no longer requires new query-distribution telemetry before the VM duel.

## What this broad gate can still answer

- whether Fixed3 first-prepare cost exists on the deployment runtime;
- whether a full-session planner regression returns;
- how broad all-consume/shared work behaves;
- how pathological latency grows with `B`;
- how rollback-journal fallback affects resolver/writer latency;
- whether TEMP policy changes latency/footprint;
- whether unrelated DB growth changes lookup-shaped resolver cost.

These are useful secondary/diagnostic dimensions, but they are not a reason to reopen the old TEMP-vs-Fixed-only finalist framing.
