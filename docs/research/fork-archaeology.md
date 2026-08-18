# Fork archaeology inventory

`fork_archaeology.py` is the evidence-preserving discovery slice for issue
#96. It accepts three immutable commit SHAs, verifies the supplied merge base,
and emits JSON and Markdown from one model.

```powershell
python scripts/fork_archaeology.py `
  --repo . `
  --fork-ref <40-char-fork-sha> `
  --upstream-ref <40-char-upstream-sha> `
  --merge-base <40-char-merge-base-sha> `
  --intent-map intent-map.json `
  --evidence-file evidence.json `
  --output-dir artifacts/fork-archaeology
```

The output is deliberately an evidence layer, not a capability verdict:

- `commits` are historical change records; `merge_events` are integration
  evidence and never receive a patch-id.
- `provenance_buckets` are provisional grouping candidates, not capabilities.
- A patch-id match is reported as
  `PATCH_EQUIVALENT_UPSTREAM_HISTORY` with the matching upstream SHA. It never
  authorizes `DROP`; current-survival and behavioral evidence are required.
- `discovery_coverage` can pass when every historical record was emitted, but
  `capability_accounting` remains FAIL until an explicit intent map accounts
  for every commit. This prevents heuristic fallback from becoming a safety
  gate.
- Evidence validation rejects unsupported combinations such as `FORK_ONLY` +
  `DROP`, and requires evidence/contracts for semantic ownership claims.

The optional intent map records human-reviewed capability assignments:

```json
{"commits": {"<fork-commit-sha>": "capability:session-search"}}
```

The optional evidence file is the human/behavioral boundary. It may record
semantic equivalence, partial absorption, lost-in-fork findings, contracts,
confidence, and disposition only when the required evidence is present.

The issue's reference SHAs must already exist in the local object database. A
missing object is an input failure, not evidence that a feature is absent. The
frozen #96 run is checked in under
[`artifacts/fork-archaeology-issue-96/`](../../artifacts/fork-archaeology-issue-96/).
It contains the 171 historical records and provisional provenance buckets;
these are intentionally not presented as completed capabilities.
