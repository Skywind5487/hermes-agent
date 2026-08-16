# Fork archaeology inventory

`fork_archaeology.py` is the first, evidence-preserving slice for issue #96.
It accepts three immutable commit SHAs and refuses to run when the supplied
merge base is not the actual merge base. It inventories every fork commit in
the range, including merge commits, and compares stable patch IDs with the
upstream range.

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

The optional intent map makes capability grouping explicit when commit subjects
do not carry a usable PR marker:

```json
{"commits": {"<fork-commit-sha>": "capability:session-search"}}
```

The optional evidence file is the human/behavioral boundary. It can record
semantic equivalence, partial absorption, lost-in-fork findings, contracts,
confidence, and recommended disposition without pretending Git similarity
proved them:

```json
{
  "intents": {
    "capability:session-search": {
      "upstream_status": "SEMANTIC_UPSTREAM",
      "disposition": "DROP",
      "confidence": "medium",
      "evidence": ["current upstream passes the recovered search contract"],
      "behavioral_contracts": ["search reaches sessions outside the recent page"]
    }
  }
}
```

The JSON and Markdown files are rendered from the same inventory. Exact
patch-id matches are classified `EXACT_UPSTREAM` / `DROP`; mixed exact and
non-exact commits in one capability become `PARTIAL_UPSTREAM` / `SPLIT`.
Everything else is left as `NEEDS_REVIEW` until behavioral/provenance evidence
is supplied. Merge commits remain visible with their first-parent changed
files. The completeness gate fails if an explicit map references a commit
outside the frozen fork range or leaves a discovered commit unmapped.

The issue's reference SHAs must be fetched into the local object database
before running the command. A missing object is an input failure, not evidence
that a feature is absent. The frozen #96 run is checked in under
[`artifacts/fork-archaeology-issue-96/`](../../artifacts/fork-archaeology-issue-96/):
171 historical commits map to 116 capabilities, with zero unaccounted commits.
All 116 remain `NEEDS_REVIEW`; no exact patch-id match was found, and no
semantic classification was invented without behavioral evidence.
