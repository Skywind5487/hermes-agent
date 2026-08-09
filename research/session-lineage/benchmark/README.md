# Session-lineage benchmark: measurement space, reproducibility, and current gate

> Current umbrella/source-of-truth issue: **#54**. Historical design discussion and evidence remain in #29, #45, #46/#47, #50/#51 and their linked PRs.
>
> Status: **research only; no production winner selected**.

This directory is the durable benchmark package for Hermes session-lineage winner resolution. Its job is not merely to list algorithms. It records the **measurable space**: the orthogonal axes that can change resolver cost/correctness, which Cartesian cells we actually measured, which cells were only sampled, and which dimensions are still completely unmeasured.

The current local shortlist is deliberately only:

1. **Pure TEMP keyed memo**; and
2. **Fixed 3-stage shared CTE**.

The local numbers are evidence for the VM gate, not the gate itself.

---

## 1. Research lineage / provenance

Use these as evidence, not competing current sources of truth:

- **#29** — historical ADR / reconstructed exploratory benchmark and algorithm archaeology.
- **#45** — design-space convergence, SQLite syntax audit, staged/TEMP discussion, and the graveyard/final-duel progression.
- **#46 / PR #49** — production code/execution/provenance map; in particular the generic-parent vs positive compression-continuation discrepancy.
- **#47 / PR #48** — prior art; modern in-place compaction, candidate over-fetch/source-priority constraints, and accepted upstream semantics.
- **#50 / PR #53** — TEMP lifecycle; WAL `_read_ctx()`, connection locality, explicit snapshot transaction, and non-WAL locked-writer fallback mechanics.
- **#51 / PR #52** — depth-64 provenance; preserve bounded-work/cycle/malformed safety intent, not the literal number 64 as lineage semantics.
- **#54** — current umbrella gate and latest decision/status record.

Repository index: `research/session-lineage/README.md`.

---

## 2. What is being measured

The benchmark starts **after ranked candidate sessions already exist**. It measures only the lineage resolver/winner-selection problem:

```text
ranked distinct candidate sessions
    -> lineage resolution / dedupe
    -> first K distinct lineage winners
```

It intentionally does **not** include FTS/title/LIKE candidate generation, snippet construction, hydration, Discord/gateway latency, or end-to-end `session_search()` latency yet.

Candidate session IDs are unique in the current generator, matching the production `DISTINCT owning_session_id` seam. This corrected an earlier synthetic mistake where duplicate physical session IDs could make memo approaches unrealistically cheap.

The semantic reference model used by the current benchmark follows a **positive compression-continuation edge** and treats branch/delegation/tool as lineage boundaries. Cycle and missing-parent cases are unresolved rather than assigned a fake root. This is the research oracle implied by #46/#47/#51, not a claim that current fork SQL already has identical semantics.

---

## 3. Orthogonal measurement basis

The important object is not a bag of scenario names. A scenario is one point in the following basis.

### 3.1 Workload / semantic basis

| basis axis | meaning | values represented locally | coverage status |
|---|---|---|---|
| `C` candidate count | ranked distinct session seeds offered to lineage resolution | 30, 300 | sampled, not full |
| `K` result limit | distinct lineage winners requested | performance: 3, 10; correctness: 1 | public 1..10 **not fully crossed** |
| lineage count `L` | number of distinct resolvable lineages represented by candidates | 1, 5, ~50, ~78-90, ~279, 300 | sampled |
| Kth-root rank | first candidate rank at which K distinct roots become available | 3, 7, 10, 13, ~55, unreachable, randomized | sampled |
| compression depth distribution | candidate-to-root compression hops | exact 0, 1, 3, 5; mixed 0..5 | realistic range sampled; 2/4 not isolated |
| candidates-per-lineage density | physical history sessions from one lineage appearing in candidate list | 1, sparse <=2, dense up to all 6 nodes, malformed 300:1 | sampled |
| rank layout | how same-lineage candidates are ordered | blocked, interleaved, random/Zipf, sparse mixed | sampled |
| modern/legacy mix | fraction with parent-chain work | 0%, 100%, ~25%, ~70-84% | sampled |
| reachability | whether K roots actually exist in the candidate set | reachable, unreachable | represented |
| shared-ancestor shape | overlap between candidate ancestry | ordinary chains, clustered same-lineage overlap, malformed fanout | sampled |
| semantic boundary | what stops lineage | root, compression, fork, delegation, tool, missing parent, cycle | correctness matrix |
| pathological acyclic work | runaway ancestry | 1000-hop safety fixture only | safety-only; excluded from perf ranking |

The numeric coordinate table generated from the scenarios is versioned as `measurement_space.csv`. It records `C`, `K`, candidate-lineage count, Kth-root position, candidates-per-lineage density, depth min/median/max, and legacy fraction for every performance/stress scenario.

### 3.2 Execution / environment basis

| basis axis | values actually measured | missing cells |
|---|---|---|
| algorithm | 23 graveyard implementations; 2 finalists | no new contender search planned |
| cache/connection state | warm persistent connection; `cold_fadvise_reopen` | true reboot/whole-VM cold |
| main DB journal route | WAL-created DB, reopened `mode=ro` | **non-WAL / locked-writer fallback not benchmarked** |
| unrelated DB background size | 20k filler across full matrix; 250k on 4 finalist probes | production `state.db` size/distribution |
| SQLite TEMP policy | `PRAGMA temp_store=MEMORY` | default, FILE, spill thresholds |
| SQLite page cache | `cache_size=-32768` in harness | deployment-specific alternatives |
| SQLite runtime | local Python-linked SQLite used by run | actual e2-micro/Hermes runtime not yet measured |
| hardware | local research environment | **e2-micro production VM not yet measured** |
| concurrency | single benchmark client | concurrent gateway/read/write traffic |
| WAL/checkpoint interaction | no dedicated pressure test | long-reader/checkpoint/WAL-growth gate |

### 3.3 Resource / safety basis

| basis axis | current coverage | missing |
|---|---|---|
| global work budget `B` | finalist default 10,000; safety sweep 5/10/64/256/2000 | production value/gate undecided |
| statements | recorded for finalists and graveyard summaries | production trace integration |
| unique/new lineage work | recorded for finalists | production histogram |
| memory | **not measured** | RSS/heap, SQLite cache, TEMP peak rows/bytes |
| disk/temp footprint | **not measured** | temp file bytes, spill, write amplification |
| lock duration | mechanics known from #50 | **not measured on non-WAL fallback** |

---

## 4. Which Cartesian cells were actually crossed

The workload basis above is a **fractional-factorial design**. We did not blindly multiply every possible `C × K × L × depth × density × rank-layout × ...` combination; many such combinations are redundant or nonsensical. Instead we selected workload points that isolate meaningful best/worst/crossover behavior.

What **was** fully crossed is explicit.

### 4.1 Graveyard matrix

```text
23 implemented algorithms
× 16 performance/stress scenarios
× 2 cache modes
= 736 aggregate result cells
```

For each performance cell in the graveyard pass:

```text
warm: 1 excluded warm-up + 5 measured repeats
cold-ish: 3 measured repeats, each on a reopened connection
```

That is **2,944 measured timing executions** for the 736 performance cells, plus excluded warm-ups.

### 4.2 Correctness/adversarial matrix

```text
23 algorithms
× 7 semantic/adversarial fixtures
× 2 modes
= 322 result cells
```

Fixtures: root, compression chain, fork boundary, delegation boundary, tool boundary, missing parent, cycle.

This was a smoke pass rather than a distribution-quality latency measurement; the stored rows are single-repeat correctness observations. Do not compare their timing as performance data.

### 4.3 Final duel

```text
2 finalists
× 16 performance/stress scenarios
× 2 cache modes
= 64 aggregate cells
```

Each cell:

```text
warm: 9 measured repeats after one excluded warm-up
cold-ish: 7 measured repeats
```

That is **512 measured final-duel timing executions**, plus excluded warm-ups.

The main reported aggregate excludes malformed-fanout stress, leaving 15 normal/realistic workload points.

### 4.4 Additional partial crosses

- **Planner/materialization sweep:** 4 scenarios × 5 shared/TEMP variants × 2 cache modes = 40 aggregate cells.
- **250k filler scale probe:** 4 representative scenarios × 2 finalists × 2 cache modes = 16 cells.
- **Global-bound smoke:** 2 finalists × B={5,10,64,256,2000} on the 1000-hop safety chain = 10 cells.
- Historical-algorithm safety run on the 1000-hop fixture is preserved separately.

### 4.5 Entire dimensions with zero coverage

These are the most important empty regions of the measurement product:

```text
journal route = non-WAL / locked-writer fallback        0
hardware/runtime = actual e2-micro Hermes deployment    0
cache = rebooted / true whole-VM cold                   0
TEMP policy = default / FILE / spill                    0
memory footprint                                        0
disk / temp bytes                                       0
concurrent gateway writer/read pressure                 0
production state.db candidate distribution              0
end-to-end session_search latency                       0
```

Do not infer these from the local WAL synthetic runs.

---

## 5. Synthetic data generation

The generator lives in `benchmark_v3_graveyard.py`, primarily `Builder`, `make_perf_scenarios()`, and `make_correctness_scenarios()`.

### 5.1 Row model

Each synthetic session is represented as:

```text
(session_id, parent_id, edge_kind)
```

with `edge_kind` including `root`, `compression`, `fork`, `delegation`, and `tool` in the relevant fixtures.

`Builder.chain(depth)` defines `depth` as **compression hops**, so a depth-5 lineage has 6 physical session rows.

### 5.2 Main scenario families

The current performance generator includes:

- modern/in-place roots, depth 0;
- independent legacy chains at depth 1/3/5;
- dense 50-lineage blocked and interleaved rankings;
- K=10 reachable and unreachable cases;
- Kth-root-at-rank-13 tail killer;
- a shared-middle crossover case;
- mostly-modern sparse legacy mix;
- three deterministic Zipf/random seeds;
- malformed fanout stress (excluded from normal aggregate);
- a 1000-hop acyclic chain as **safety only**.

Every candidate list is asserted unique before insertion into the SQLite `candidates` table.

### 5.3 Background rows

Each scenario DB gets unrelated root rows (`filler`) to prevent all queries from living in a tiny toy B-tree. The full matrix used 20,000 filler rows; a separate final-duel probe used 250,000.

### 5.4 Determinism

Randomized scenario families use fixed seeds (`SEED=5487` and explicit per-scenario seeds), so a fresh clone produces the same logical workload topology.

---

## 6. Measurement method

### Warm

One connection is kept open. Each algorithm receives one excluded warm-up invocation, then repeated measured invocations. This includes SQLite/Python statement-cache warming for repeated SQL text.

### Cold-ish

Before every measured repeat:

1. call `posix_fadvise(..., POSIX_FADV_DONTNEED)` on the scenario DB file when supported;
2. close/reopen the SQLite connection as `file:...?mode=ro`;
3. run one measured invocation.

This deliberately re-exposes connection/page-cache/statement-prepare behavior. It is **not** equivalent to a rebooted VM or guaranteed global OS page-cache eviction.

### Statistics

Stored summaries contain:

- arithmetic mean;
- median;
- p95;
- population standard deviation;
- min/max;
- statement-count median where available;
- exactness against the reference oracle.

The cross-scenario headline uses the **geometric mean of per-scenario medians**. This avoids a single slow workload dominating the relative summary while still preserving per-scenario tables for interpretation.

---

## 7. Graveyard coverage: what was actually implemented and timed

The graveyard pass contains 23 complete resolver shapes:

1. current optimized per-seed SQL baseline;
2. seedless shared `UNION(node)`;
3. contraction -> shared;
4. contraction -> per-head;
5. single-state early-stop SQL;
6. lazy-covered state machine;
7. priority-queue streaming SQL;
8. staged path collision (K<=3 prototype);
9. component flood;
10. hard-unroll depth 5;
11. completed shared 2-stage;
12. completed shared 3-stage;
13. completed shared 4-stage;
14. restart widening;
15. host-carried known map;
16. TEMP memo;
17. shared -> shared -> TEMP hybrid;
18. TEMP -> shared -> TEMP hybrid;
19. Python dict memo;
20. Python recursive-chain-query memo;
21. Python early-stop without memo;
22. persistent-root schema lower bound;
23. bulk all-sessions-to-Python anti-pattern/reference.

The following ideas were **not assigned fake end-to-end timings** because they never became a complete Hermes resolver in core SQLite:

- `RTRIM` collation partial-key `UNION` trick;
- DuckDB `USING KEY` (external-engine prior art);
- JSONB recurring-state experiments;
- SQLite closure virtual-table extension.

Raw-latency winners do not automatically return to the production shortlist. For example hard-unroll5 is very fast in a realistic 0..5 model but bakes a depth assumption into query shape; historical state-machine/priority/staged prototypes expose cycle-safety/maintainability problems; persistent-root changes schema/write invariants; Python graph loops are outside the SQLite-first production direction.

---

## 8. Planner traps and corrected interpretation

### 8.1 Accidental full `sessions` scan

An early bound-aware fixed-3 shared SQL allowed SQLite to reorder Stage 2/3 label-anchor joins into:

```text
SCAN child
```

That scanned the full `sessions` table before joining the small stage relation. At 250k unrelated filler rows it inflated fixed-shared measurements into the ~20/40 ms warm/cold range.

Rewriting the relevant branches with `CROSS JOIN` forced the intended small-side-first shape:

```text
small stage node set
    -> sessions primary-key point lookup
```

After that, the DB-size cliff disappeared. **Pre-fix large-DB numbers must not be interpreted as intrinsic shared-CTE scaling.**

The production gate must capture `EXPLAIN QUERY PLAN` and fail closed if this full-scan shape returns.

### 8.2 Large single-statement first prepare/planner cost

Even after eliminating the accidental scan, the fixed-3 shared statement shows a large first invocation on a newly opened local connection: roughly **13-15 ms** in the current cold-ish runs, while a second invocation on the same connection falls to sub-ms/low-ms.

Current evidence therefore points to first prepare/planner/statement-cache cost as a major cold fixed cost, not full-table I/O. This is especially relevant to process restart, reader recreation, and VM-cold behavior.

Do **not** generalize the magnitude to Hermes production until the actual e2-micro Python/SQLite runtime is measured. #50 shows normal WAL readers are thread-local and persistent, so this is not necessarily a per-search cost in steady state.

---

## 9. Current local result, deliberately not a production decision

Across 15 normal/realistic final-duel scenarios (malformed fanout excluded):

| mode | Pure TEMP geometric-mean median | fixed 3-stage shared | shared/TEMP |
|---|---:|---:|---:|
| warm | ~0.177 ms | ~0.291 ms | ~1.64x |
| cold-ish reopen/fadvise | ~0.222 ms | ~13.459 ms | ~60.7x |

Interpretation:

- when both do approximately the same work on an already-warm connection, fixed shared can be close to TEMP and can win when all candidates must be consumed;
- TEMP wins clustered/early-stop cases mainly because it avoids batch over-work;
- the local cold-ish fixed-shared gap is dominated by the first execution of a very large SQL statement after the full-scan planner trap was removed;
- local synthetic evidence therefore leans TEMP, but the engineering trade remains real: TEMP needs a query-local table + explicit logical-search transaction; fixed shared needs neither.

No winner is selected until #54's VM gate is complete.

---

## 10. What has **not** been measured

The following omissions are explicit and decision-blocking where noted:

1. **Actual e2-micro / production VM:** not run.
2. **Non-WAL fallback:** not benchmarked. #50 proved correctness/lifecycle mechanics only; fallback would use the shared writer connection under `self._lock` and needs bounded lock-duration measurement.
3. **Production gate thresholds:** not decided.
4. **Memory:** no RSS, Python heap, SQLite page-cache, TEMP rows/bytes, or peak-memory accounting.
5. **Disk/temp spill:** local harness pins `temp_store=MEMORY`; no default/FILE/spill measurement.
6. **True VM cold:** `cold_fadvise_reopen` is not reboot/global-cache cold.
7. **Production `state.db` distribution:** synthetic data begins after candidate ranking; no real candidate-depth/clustering histogram yet.
8. **Concurrency:** no simultaneous writer/read/gateway pressure.
9. **WAL checkpoint pressure:** no dedicated long-reader/checkpoint/WAL-growth benchmark.
10. **End-to-end search:** no FTS/title/source-priority/snippet/hydration/gateway timing in these resolver numbers.

---

## 11. Clone-and-run

The package is standard-library-only.

From the repository root:

```bash
python research/session-lineage/benchmark/run.py
```

This runs a small smoke matrix and writes outputs under `research/session-lineage/benchmark/out/`.

Full local synthetic matrix:

```bash
python research/session-lineage/benchmark/run.py --full
```

Direct graveyard controls:

```bash
python research/session-lineage/benchmark/benchmark_v3_graveyard.py \
  --output-dir /tmp/hermes-lineage-graveyard \
  --warm-repeats 5 \
  --cold-repeats 3 \
  --filler 20000 \
  --budget 10000
```

Direct final duel:

```bash
python research/session-lineage/benchmark/final_duel.py \
  --out /tmp/hermes-lineage-final-duel \
  --warm 9 \
  --cold 7 \
  --filler 20000 \
  --budget 10000
```

`posix_fadvise` is used when available; lack of it weakens the cold-ish mode rather than changing correctness.

A successful local clone-and-run is **not** the production gate. The VM run should record exact commit SHA, Python version, `sqlite3.sqlite_version`, journal route, relevant PRAGMAs, CPU/memory environment, and whether the read connection is the normal WAL `_read_ctx()` path or a fallback path.

---

## 12. Next decision gate (#54)

Only the two finalists need the production VM matrix. Do not reopen the algorithm search unless the VM data falsifies both.

The gate should add the currently empty basis cells:

- actual e2-micro/Hermes runtime;
- warm and fresh-reader behavior, plus true VM-cold if practical;
- memory/TEMP footprint;
- disk/temp spill under deployment policy;
- normal WAL route **and** a bounded non-WAL/locked-writer fallback probe;
- EQP assertion against full `sessions` scans;
- production-derived candidate/depth/clustering distributions or a safe shadow/snapshot replay;
- end-to-end/lock-duration observations sufficient to choose a production winner.

Until then: **Pure TEMP vs fixed 3-stage shared remains an open gate, not an implementation decision.**
