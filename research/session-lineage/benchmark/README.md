# Session-lineage benchmark: measurable space + current gate

> **Current umbrella/source of truth: #54.**
>
> Status: research only. **No production winner selected.**

This directory records the benchmark as an **experimental measurement space**, not just a list of algorithms or scenario names. The durable question is: which orthogonal workload/environment/resource axes exist, which Cartesian cells have actually been measured, and which dimensions are still empty before the production decision.

Current finalists:

1. **Pure TEMP keyed memo** — mutable query-local `node -> root`, ranked early stop, explicit transaction for a multi-statement logical snapshot.
2. **Fixed 3-stage shared CTE** — one SQLite statement, no new table, completed-stage reuse, global bounded work.

Historical evidence remains in #29, #45, #46/#47, #50/#51 and PRs #48/#49/#52/#53. #54 is the current status/gate ticket.

## What this benchmark measures

The benchmark starts **after ranked distinct candidate sessions already exist**:

```text
ranked distinct candidate sessions
    -> lineage resolution / dedupe
    -> first K distinct lineage winners
```

It does not yet include FTS/title/LIKE candidate generation, snippet construction, hydration, gateway latency, or end-to-end `session_search()` latency.

The reference oracle follows positive compression-continuation edges; branch/delegation/tool are boundaries; cycle/missing parent are unresolved rather than fake roots. This is the research oracle derived from #46/#47/#51, not a claim that current fork SQL already implements identical semantics everywhere.

Candidate IDs are unique, matching the production `DISTINCT owning_session_id` seam. Earlier synthetic work that repeated the same physical session ID is not used for the current performance ranking.

---

# 1. Orthogonal measurement basis

A scenario is a point in this basis.

## Workload / semantic axes

| axis | meaning | values represented locally | status |
|---|---|---|---|
| candidate count `C` | ranked distinct session seeds | 30, 300 | sampled |
| result `K` | distinct winners requested | perf: 3,10; correctness: 1 | public 1..10 not fully crossed |
| lineage count `L` | distinct resolvable lineages in candidates | 1,5,~50,~78-90,~279,300 | sampled |
| Kth-root rank | first rank where K roots become available | 3,7,10,13,~55,unreachable,randomized | sampled |
| compression depth | candidate-to-root hops | exact 0,1,3,5; mixed 0..5 | realistic range sampled |
| candidate density | physical sessions per lineage represented | 1, sparse <=2, dense <=6, malformed 300:1 | sampled |
| rank layout | order of same-lineage candidates | blocked, interleaved, random/Zipf, sparse mixed | sampled |
| modern/legacy mix | how much parent traversal exists | 0%,100%,~25%,~70-84% legacy candidates | sampled |
| reachability | whether K roots exist | reachable, unreachable | represented |
| shared-ancestor shape | overlap topology | ordinary chains, clustered overlap, malformed fanout | sampled |
| boundary semantics | lineage stop condition | root, compression, fork, delegation, tool, missing, cycle | correctness matrix |
| runaway ancestry | pathological acyclic work | 1000-hop chain | safety-only, excluded from perf ranking |

`measurement_space.csv` stores numeric scenario coordinates: `C`, `K`, candidate-lineage count, Kth-root rank, density, depth min/median/max, and legacy fraction.

## Execution / environment axes

| axis | measured | empty/unmeasured |
|---|---|---|
| algorithm | 23 graveyard implementations; 2 finalists | no new contender search planned |
| cache/connection | warm persistent connection; `cold_fadvise_reopen` | reboot/whole-VM true cold |
| journal/connection route | normal WAL-created DB, reopened `mode=ro` | **non-WAL / locked-writer fallback** |
| unrelated DB size | 20k filler full matrix; 250k finalist probe | real production `state.db` distribution |
| TEMP policy | `temp_store=MEMORY` | default, FILE, spill |
| page cache | `cache_size=-32768` | deployment variants |
| runtime | local Python-linked SQLite | actual Hermes/e2-micro runtime |
| hardware | local research machine | actual e2-micro |
| concurrency | single client | concurrent gateway readers/writers |
| WAL/checkpoint pressure | not dedicated | reader/checkpoint/WAL-growth gate |

## Resource / safety axes

| axis | measured | missing |
|---|---|---|
| global work budget `B` | finalist default 10000; safety 5/10/64/256/2000 | production gate/value undecided |
| statements | recorded | production trace integration |
| new lineage work | recorded for finalists | production histogram |
| memory | **0 coverage** | RSS/heap, page-cache, TEMP peak rows/bytes |
| disk/temp | **0 coverage** | temp-file bytes, spill, write amplification |
| lock duration | mechanics researched in #50 | non-WAL fallback timing not measured |

---

# 2. Which Cartesian cells were actually measured

The workload axes use a **designed fractional-factorial sample**. We did not blindly multiply every possible `C × K × L × depth × density × rank-layout × ...` combination; many cells are redundant or nonsensical. The actual sampled workload coordinates are explicit in `measurement_space.csv`.

The outer experimental layers **were** fully crossed.

## Graveyard performance matrix

```text
23 implemented algorithms
× 16 performance/stress scenarios
× 2 cache modes
= 736 aggregate result cells
```

For each performance cell:

```text
warm: one excluded warm-up + 5 measured repeats
cold-ish: 3 measured repeats, each on a reopened connection
```

That is **2,944 measured timing executions**, plus excluded warm-ups.

Aggregate graveyard results: `results/graveyard_summary.csv`.

## Correctness/adversarial matrix

```text
23 algorithms
× 7 semantic/adversarial fixtures
× 2 modes
= 322 cells
```

Fixtures: root, compression chain, fork, delegation, tool, missing parent, cycle. This was a smoke matrix, not distribution-quality latency measurement.

Summary: `results/correctness_summary.csv`.

## Final duel

```text
2 finalists
× 16 performance/stress scenarios
× 2 cache modes
= 64 aggregate cells
```

Per cell:

```text
warm: 9 measured repeats after one excluded warm-up
cold-ish: 7 measured repeats
```

That is **512 measured timing executions**, plus excluded warm-ups.

The headline aggregate excludes malformed fanout, leaving 15 normal/realistic workload points.

Raw finalist cells: `results/final_duel_combined.csv`.

## Additional partial crosses

- Planner/materialization sweep: `4 scenarios × 5 variants × 2 cache modes = 40 cells`.
- 250k-filler scale probe: `4 scenarios × 2 finalists × 2 modes = 16 cells`.
- Bound smoke: `2 finalists × B={5,10,64,256,2000} = 10 cells` on the 1000-hop safety chain.

## Entire dimensions with zero coverage

```text
non-WAL / locked-writer fallback             0
actual e2-micro/Hermes runtime               0
rebooted / true whole-VM cold                0
TEMP default/FILE/spill                      0
memory footprint                             0
disk/TEMP bytes                              0
concurrent gateway pressure                  0
production state.db candidate distribution   0
end-to-end session_search latency            0
```

These cannot be inferred from the normal-WAL local runs.

---

# 3. Data generation

The clone-runnable generator is `scenarios.py`.

Synthetic rows are:

```text
(session_id, parent_id, edge_kind)
```

with `edge_kind` including `root`, `compression`, `fork`, `delegation`, and `tool` where relevant.

`Builder.chain(depth)` defines depth as **compression hops**; depth 5 therefore means 6 physical session rows.

Main performance families:

- modern/in-place roots (depth 0);
- independent legacy chains depth 1/3/5;
- 50-lineage dense blocked ranking;
- same 50-lineage topology interleaved;
- K=10 reachable and unreachable;
- Kth root exactly at rank 13 followed by realistic depth-5 tail;
- shared-middle crossover fixture;
- mostly-modern sparse legacy mix;
- deterministic Zipf/random mixtures with seeds 11/22/33;
- malformed fanout stress, excluded from normal aggregate;
- 1000-hop acyclic chain for bound safety only.

Every candidate list is asserted unique before SQLite insertion.

Each scenario DB also receives unrelated root filler rows. The full local matrix used 20k; the scale probe used 250k.

Random families are deterministic (`SEED=5487` plus explicit scenario seeds).

---

# 4. Measurement method

## Warm

One connection stays open. Each algorithm receives one excluded warm-up invocation, then measured repeats. This includes statement-cache/prepare warming for repeated SQL text.

## Cold-ish

Before every measured repeat:

1. `posix_fadvise(..., POSIX_FADV_DONTNEED)` on that DB file when available;
2. close/reopen the SQLite connection as `file:...?mode=ro`;
3. run one measured invocation.

This is stronger than connection-cold but **not equivalent to reboot/global OS page-cache cold on the VM**.

## Statistics

Stored summaries use:

- arithmetic mean;
- median;
- p95;
- population stdev;
- min/max;
- exactness vs reference;
- work/statement counts where available.

The cross-scenario headline uses geometric mean of per-scenario medians; per-scenario rows remain authoritative for workload-specific trade-offs.

---

# 5. Historical/graveyard result space

The graveyard pass timed 23 implemented resolver shapes:

1. current optimized per-seed SQL;
2. shared `UNION(node)`;
3. contraction -> shared;
4. contraction -> per-head;
5. single-state early stop;
6. lazy-covered state;
7. priority queue stream;
8. staged path collision K<=3;
9. component flood;
10. hard-unroll depth 5;
11. completed shared 2-stage;
12. completed shared 3-stage;
13. completed shared 4-stage;
14. restart widening;
15. host-carried map;
16. TEMP memo;
17. shared -> shared -> TEMP;
18. TEMP -> shared -> TEMP;
19. Python dict memo;
20. Python chain-query memo;
21. Python early stop without memo;
22. persistent-root lower bound;
23. bulk all-sessions-to-Python reference/anti-pattern.

Ideas that never became a complete core-SQLite Hermes resolver were **not assigned fake end-to-end timings**: RTRIM collation trick, DuckDB `USING KEY`, JSONB recurring state, closure virtual-table extension.

Raw speed alone did not revive discarded designs. Examples: hard-unroll5 is extremely fast but bakes depth into query shape; historical encoded-state/priority/staged forms have safety/maintainability gaps; persistent root changes schema/write invariants; Python graph loops remain outside the SQLite-first production direction.

---

# 6. Planner traps found during the benchmark

## Accidental full `sessions` scan

An early fixed-3 shared query let SQLite reorder Stage 2/3 label-anchor joins into:

```text
SCAN child
```

At 250k unrelated rows this inflated fixed-shared numbers into roughly 20/40 ms warm/cold.

Using `CROSS JOIN` to force the intended small-stage-set -> `sessions` PK lookup removed the DB-size cliff. **Those pre-fix 20/40 ms values are not intrinsic shared-CTE scaling.**

The production gate should capture `EXPLAIN QUERY PLAN` and reject a regression to a full `sessions` scan.

## First execution of the giant fixed-shared statement

After the scan fix, the large fixed-shared statement still costs roughly **13-15 ms on its first invocation on a newly opened local connection**, then falls to sub-ms/low-ms on the same connection.

Current evidence points mainly to first prepare/planner/statement-cache cost rather than full-table I/O. This matters for restart/new-reader/VM-cold behavior.

Do not generalize the magnitude to production yet. #50 shows normal WAL read connections are thread-local/persistent, so this is not necessarily paid on every search.

---

# 7. Current local finalist result — not a production decision

Across 15 normal/realistic scenarios:

| mode | Pure TEMP geom-mean median | fixed 3-stage shared | shared/TEMP |
|---|---:|---:|---:|
| warm | ~0.177 ms | ~0.291 ms | ~1.64x |
| cold-ish reopen/fadvise | ~0.222 ms | ~13.459 ms | ~60.7x |

Interpretation:

- with similar work on a warm connection, fixed shared can be close and can win when all candidates must be consumed;
- TEMP mainly wins clustered/early-stop workloads by avoiding batch over-work;
- the large local cold-ish shared gap is dominated by first execution of a very large SQL statement after the full-scan trap was removed;
- local evidence leans TEMP, but engineering costs remain different: TEMP needs a query-local table + explicit transaction; fixed shared needs neither.

No winner until #54's VM gate.

---

# 8. Explicitly not measured yet

1. Actual e2-micro / production VM.
2. Non-WAL `_read_ctx()` locked-writer fallback performance/lock duration.
3. Production acceptance thresholds/gate.
4. Memory: RSS, Python heap, SQLite cache, TEMP rows/bytes, peak allocations.
5. Disk/TEMP: default/FILE/temp spill and temporary bytes.
6. True reboot/whole-VM cold.
7. Production `state.db` candidate/depth/clustering distribution.
8. Concurrent readers/writers/gateway pressure.
9. WAL checkpoint pressure during the logical search.
10. End-to-end FTS/title/source-priority/snippet/hydration/gateway latency.

---

# 9. Repo artifacts

- `scenarios.py` — deterministic data/scenario generator.
- `measurement_space.csv` — orthogonalized workload coordinates for every scenario.
- `temp_memo.py` — TEMP finalist used by the clone runner.
- `fixed3_optimized.py` — planner-corrected fixed-shared finalist.
- `final_duel.py` — repeated warm/cold-ish two-finalist runner.
- `run.py` — clone entry point.
- `results/graveyard_summary.csv` — aggregate 23-algorithm performance evidence.
- `results/correctness_summary.csv` — graveyard correctness/adversarial summary.
- `results/final_duel_combined.csv` — per-scenario finalist local evidence.
- `results/bound_safety_smoke.csv` — global-bound behavior.

The full graveyard raw-run artifact was generated in the research session; the durable repo summary records all 23 implemented algorithms and their exact-performance/error coverage. The clone runner is intentionally narrowed to the two current finalists instead of reopening historical contender search.

---

# 10. Clone-and-run

Standard library only.

Small smoke from repository root:

```bash
python research/session-lineage/benchmark/run.py
```

Full current synthetic finalist matrix:

```bash
python research/session-lineage/benchmark/run.py --full
```

Direct control:

```bash
python research/session-lineage/benchmark/final_duel.py \
  --out /tmp/hermes-lineage-final-duel \
  --warm 9 \
  --cold 7 \
  --filler 20000 \
  --budget 10000
```

A successful local run is **not** the production gate. A VM run must record commit SHA, Python/SQLite versions, journal route, PRAGMAs, CPU/memory environment, and whether it used the normal WAL reader or a fallback route.

---

# 11. Next gate (#54)

Only the two finalists need the production VM matrix. Add the currently empty cells:

- actual e2-micro/Hermes runtime;
- warm + fresh-reader, and true VM-cold if practical;
- memory/TEMP footprint;
- disk/temp spill;
- normal WAL plus bounded non-WAL/locked-writer fallback;
- EQP assertion against full `sessions` scans;
- production-derived candidate/depth/clustering distribution or safe shadow replay;
- enough end-to-end/lock-duration evidence to choose a winner.

Until then: **Pure TEMP vs fixed 3-stage shared remains an open gate.**
