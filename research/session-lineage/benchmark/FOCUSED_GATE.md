# #54 focused resolver gate — decision record

> Status: **gate complete**.
>
> Selected production resolver shape: **`python_dict_memo`**.
>
> Selected global successful-row-lookup budget: **`B = 1500`**.
>
> Raw e2-micro evidence: `results/focused_vm_gate_20260809.md`.

This file supersedes the older "Pure TEMP vs Fixed3 only" finalist framing in
`README.md`, `GATE.md`, and early #54 comments. Those remain historical evidence.

## Final architecture decision

Use ranked sequential indexed point traversal with a tiny **query-local**
`node -> resolved_root` memo/path compression map.

Keep:

- one deferred read transaction for the whole logical search;
- ranked candidate-level early stop;
- query-local path compression only;
- local cycle detection;
- missing-parent fail closed;
- global successful-row-lookup budget `B=1500`;
- no persistent schema and no cross-query cache.

TEMP and Fixed3 remain reference evidence only. A lazy-candidate SQL state machine
remains a separate SQL exploration direction and is not required for this
production implementation.

## Why memo wins

The focused e2-micro run used PR #55 head
`444c161218b00166bda73f2ec5a21f250e2049bc`, a clean worktree, a 35-second CPU
precondition, and disposable synthetic DBs only. Eleven focused/contract tests
passed.

### Hermes-normal shallow cases

| case | no memo median | memo median |
|---|---:|---:|
| depth0, C=300, K=3 at rank 3 | 0.037460 ms | 0.038469 ms |
| depth1, C=300, K=3 at rank 3 | 0.051469 ms | 0.053704 ms |

Memo's shallow overhead is only about 3–4%, or roughly 0.001–0.002 ms here.

### Historical/import compatibility extreme

| case | no memo median | memo median | memo speedup |
|---|---:|---:|---:|
| depth14/size15, deepest-to-root then two roots | 0.592984 ms | 0.124840 ms | ~4.75x |
| depth14/size15 full-consume | 0.619249 ms | 0.114357 ms | ~5.42x |

The full-consume fixture deterministically confirms the work reduction:

```text
no memo = 120 successful row lookups
memo    = 15 successful row lookups
```

TEMP and Fixed3 are also slower than the best KISS candidate across these focused
cells, by roughly 3.4x–7.9x on median.

That is enough to pay for the tiny dict: essentially no normal-path penalty, but a
large compatibility/repeated-ancestry reduction.

## Final `B = 1500`

Do not derive `B` from max depth alone.

Frozen-corpus work envelopes already established in #54 are:

```text
C <= 300   -> <= 554 successful node visits
C <= 1000  -> <= 1254 successful node visits
historical one-lineage adversary -> <= 120 visits
```

`B=1500` covers the hard `C<=1000` legitimate frozen-corpus envelope with about
20% headroom while still bounding malformed/future paths.

The e2-micro safety sweep contains non-monotonic 100–200ms stalls caused by the
already-attributed shared-core quota regime, so there is no honest single
wall-time "cliff" to fit. The budget is therefore selected primarily from the
logical legitimate-work envelope plus a modest margin, with the VM curve used as
a sanity check rather than a false precision threshold.

## Runtime caveat

The saved VM receipt shows the focused run used `/usr/bin/python3` linked against
SQLite 3.40.1, not a Hermes-managed embedded/runtime interpreter.

This does **not** reopen the architecture decision: no-memo and memo use the same
indexed point-lookup shape, and the decisive difference is query-local reuse.
The caveat matters more to planner-heavy TEMP/Fixed references.

Production integration must still run the normal Hermes test/runtime path before
merge. Do not add another broad algorithm benchmark solely because of this
receipt difference.

## Workload interpretation

### Hermes-normal observed topology

Observed post-adoption positive compression ancestry is shallow (depth 0/1 in the
recovered corpus). Candidate-level early stop therefore dominates normal work.

### Historical/import compatibility

The robust frozen-corpus extreme remains:

```text
max positive compression depth = 14
max lineage size               = 15
```

This is compatibility evidence, not normal Hermes weighting. #60 independently
owns the exact ChatGPT import/merge provenance of pre-Hermes historical rows.
Its result may refine provenance wording, but should not reopen the resolver
algorithm space unless it materially changes the compatibility requirement.

### Safety only

Synthetic 5k/10k chains are only for malformed/pathological protection and budget
cost. Do not average them into normal performance ranking.

## Production integration contract

The benchmark's synthetic schema uses `edge_kind='compression'`. Production code
must substitute current Hermes positive compression-continuation semantics while
preserving:

1. ranked distinct owning-session candidates in;
2. candidate-level early stop at K roots;
3. one logical read snapshot;
4. query-local `node -> root` memo/path compression;
5. cycle and missing-parent fail-closed behavior;
6. global successful-row-lookup budget `B=1500`;
7. deterministic diagnostics/work counters suitable for tests.

## Durable evidence

- `results/focused_vm_gate_20260809.md` — exact VM receipt, test output, focused
  performance CSV, budget CSV, and source package SHA-256.
- `python_memo.py` — hardened research candidate.
- `focused_scenarios.py` — shallow normal + depth14/size15 fixtures.
- `focused_vm_gate.py` — production-VM focused runner.
- `tests/test_focused_gate.py` — exactness/reuse/budget assertions.
- `per_seed.py` — no-memo reference candidate.
- `temp_memo.py` / `fixed3_optimized.py` — historical references.

## Closed / do not reopen by default

- TEMP vs Fixed3 as the only finalist set;
- broad algorithm-zoo expansion;
- e2-micro 8x burst/sustained cliff attribution;
- whether memo reuse is worth its normal-path overhead;
- production resolver mechanism;
- global budget value (`B=1500`).

Next work is **production integration**, not more resolver research.
