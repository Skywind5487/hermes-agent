# #54 focused resolver gate — current source of truth

> Status: research only. No production winner or final global `B` selected yet.
>
> This file supersedes the older "Pure TEMP vs Fixed3 only" finalist framing in
> `README.md`, `GATE.md`, and early #54 comments. Those remain historical evidence.

## Decision now

The architecture question is intentionally small:

1. **`per_seed_no_memo`** — ranked sequential indexed point traversal, no reuse;
2. **`python_dict_memo`** — the same scheduler plus query-local `node -> root`
   path compression;
3. **Pure TEMP** — existing overlap/memo reference;
4. **Fixed3** — existing one-statement SQL reference.

The first two are the current KISS production decision candidates. TEMP and
Fixed3 remain references because their earlier measurements are useful, but they
are no longer privileged as the only finalists.

A lazy-candidate SQL state machine is deliberately not implemented here. It is a
separate SQL exploration direction; do not grow this gate into another algorithm
zoo.

## Why the decision narrowed

The resolver receives **ranked distinct owning-session IDs**. Duplicate FTS rows
have already been collapsed before this seam.

Current evidence separates three workload classes:

### 1. Hermes-normal observed topology

Observed post-adoption positive compression ancestry is shallow (currently depth
0/1 in the recovered corpus). When `K` is reached in the first few ranked
candidates, candidate-level early stop matters more than shared ancestry reuse.

### 2. Historical/import compatibility

The robust frozen-corpus extreme is:

```text
max positive compression depth = 14
max lineage size               = 15
```

The focused fixture ranks all 15 members deepest-to-root before two independent
roots. This deliberately maximizes repeated ancestry before `K=3` can be
satisfied.

For the one-lineage full-consume form:

```text
no memo: 15 + 14 + ... + 1 = 120 successful row lookups
memo:    15 successful row lookups, then query-local hits
```

This is compatibility evidence, not normal Hermes workload weighting. #60 owns
the exact ChatGPT import/merge provenance of pre-Hermes historical rows.

### 3. Safety only

Synthetic 5k/10k chains are retained only for:

- global work-budget behavior;
- malformed/pathological future DB protection;
- latency/resource growth as `B` changes.

They must not be averaged into normal performance evidence.

## Files

- `python_memo.py` — hardened graveyard-#19 style Python dict/path-compression resolver;
- `focused_scenarios.py` — current-normal + real historical extreme fixtures;
- `focused_vm_gate.py` — VM runner producing the focused decision evidence;
- `tests/test_focused_gate.py` — correctness, historical-envelope, reuse, and bound assertions;
- `per_seed.py` — no-memo sequential baseline;
- `temp_memo.py` / `fixed3_optimized.py` — references.

## Hardened Python memo contract

`python_dict_memo()` has:

- one deferred read transaction for the entire multi-statement logical search;
- ranked candidate-level early stop;
- query-local `node -> resolved_root` memo;
- path compression;
- local cycle detection;
- missing-parent fail closed;
- global successful-row-lookup budget `B`;
- no persistent schema or cross-query cache.

The synthetic benchmark uses `edge_kind='compression'`. Production integration
must replace that synthetic predicate with current Hermes positive
compression-continuation semantics without changing the scheduler/safety contract.

## Focused VM run

From the research branch:

```bash
python research/session-lineage/benchmark/run.py \
  --focused-gate \
  --output-dir /tmp/hermes-lineage-20260809
```

Smoke first if desired:

```bash
python research/session-lineage/benchmark/run.py \
  --quick-focused-gate \
  --output-dir /tmp/hermes-lineage-smoke
```

The focused runner never opens production `state.db`.

Outputs:

```text
focused-vm-gate/
├── receipt.json
├── tests.txt
├── focused_gate.csv
├── focused_budget.csv
├── fixed3_eqp.json
└── suite_meta.json
```

`receipt.json` records runtime/machine/git context. `focused_gate.csv` contains
normal + historical compatibility performance. `focused_budget.csv` is safety
only and must be interpreted separately.

## How to decide after the VM run

### Architecture winner

Prefer the simpler no-memo traversal if:

- it wins/essentially ties memo on both shallow normal fixtures; and
- its real historical depth14/size15 cost remains comfortably small.

Keep the tiny Python memo if:

- its normal-path overhead is negligible; and
- it materially reduces the real historical compatibility fixture.

TEMP/Fixed3 should displace the KISS candidates only if they show a clear VM
advantage that justifies their additional integration/runtime complexity.

### `B` is a separate decision

Do not derive `B` from max depth alone.

Frozen-corpus no-memo work envelopes already established in #54:

```text
C <= 300   -> <= 554 successful node visits
C <= 1000  -> <= 1254 successful node visits
historical one-lineage adversary -> <= 120 visits
```

These are compatibility/normal-corpus envelopes, not a malformed-future-DB fuse.
Choose final `B` by placing a safety margin above legitimate work and checking the
VM `focused_budget.csv` latency/resource curve.

## Not blocked by #60

#60 is concurrently reconstructing ChatGPT historical import/merge provenance by
reading archived chats and DB fields. The focused resolver VM run does not need to
wait for it.

Only revise the workload interpretation if #60 proves that the historical
lineage topology has a materially different provenance/meaning. Do not let DB
archaeology reopen the resolver algorithm space by default.
