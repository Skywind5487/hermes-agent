# #54 production-shaped gate addendum

This addendum extends the existing two-finalist benchmark without changing the current production shortlist. `Pure TEMP` and `Fixed 3-stage shared CTE` remain the finalists; `per_seed_point` is only a sequential/no-memo crossover ablation to answer whether very small post-search work is cheaper than constructing reusable state.

## Scripts

### Synthetic VM / WSL gate

Run the same synthetic gate on e2-micro and WSL:

```bash
python research/session-lineage/benchmark/run.py --gate \
  --output-dir /tmp/hermes-lineage-gate
```

Fast smoke:

```bash
python research/session-lineage/benchmark/run.py --quick-gate \
  --output-dir /tmp/hermes-lineage-gate-smoke
```

`vm_gate.py` never opens a production `state.db`. It records a machine/runtime receipt and produces:

- `small_c.csv` — C=3,5,10,20,30,50,100,300 sequential-vs-TEMP-vs-Fixed crossover;
- `full_consume.csv` — K unreachable, so all candidates must be consumed;
- `budget_pathological.csv` — 10k-hop and long+concentrated stress across B;
- `db_size.csv` — 0/20k/250k unrelated filler scaling;
- `lifecycle.csv` — first/second/warm reuse on the same connection;
- `nonwal_delete.csv` — `journal_mode=DELETE` locked-writer fallback, Python lock wait and competing SQLite writer latency;
- `temp_store.csv` — DEFAULT/FILE/MEMORY TEMP policy and logical TEMP pages;
- `eqp.json` — Fixed query plan; gate fails on a full `sessions`/`child` scan;
- `receipt.json` — Python/SQLite source ID and compile options, CPU/affinity/cgroup/memory/disk/mount, git identity, and best-effort `hermes-gateway.service` identity.

This gate is still synthetic and begins after ranked candidates exist.

### Frozen recovered production topology (WSL only)

The recovery source of truth is #20/#22. By default `production_profile.py` accepts only:

```text
/home/skywind/hermes-recovery/runs/20260807-081043/state.recovered.patched.db
SHA-256 23cfa3c8adb94ed403058329ae7e252e1d4c4bc01ead76e22ac7d0ff99948104
```

Run:

```bash
python research/session-lineage/benchmark/production_profile.py \
  --out /tmp/hermes-production-lineage-profile
```

Safety is fail-closed: wrong SHA, symlink, or non-empty `-wal`/`-shm`/`-journal` sidecar aborts. The database is opened `mode=ro&immutable=1` with `query_only=ON`; no TEMP table, journal change, migration, VACUUM, FTS build, or finalist replay is performed. SHA/stat are checked again after profiling.

The profile records canonical row counts, quick/foreign-key health, schema/FTS presence, generic parent edges, positive compression-continuation edges, missing parents, branch/delegate/tool markers, lineage depth and lineage-size distributions, and pathological static topology. It deliberately does **not** invent post-search candidate distributions from arbitrary query strings.

If #20 later names a replacement authoritative master, update the recorded path/hash from that source of truth before profiling; do not bypass the hash guard against an unverified file.

## What the new gate can decide

- whether Fixed's first-prepare penalty exists on e2-micro and how it changes on the second call of the same reader;
- whether the old full-table planner regression returns on the deployment SQLite;
- whether small work favors a no-memo sequential lower bound;
- where all-consume work favors a one-statement shared resolver;
- how pathological latency grows with the global work fuse B;
- how expensive non-WAL fallback is for both the Hermes Python lock and a competing rollback-journal writer;
- whether TEMP policy materially changes latency/footprint;
- whether 20k→250k unrelated DB growth changes lookup-shaped resolver cost;
- whether the recovered production corpus actually has deep/dense compression topology.

It cannot, by itself, select the final B or reconstruct real `C/K/Kth-root-rank` search distributions. Final B should be bracketed by (a) production-derived normal work/depth distribution and (b) the e2-micro pathological B→latency/resource curve. Real ranked-candidate distributions require bounded telemetry at the post-search candidate seam or a safe recorded-candidate replay.
