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
  --output-dir artifacts/fork-archaeology
```

The JSON and Markdown files are rendered from the same inventory. Exact
patch-id matches are classified `EXACT_UPSTREAM` / `DROP`. Everything else is
left as `NEEDS_REVIEW`; this is deliberate because semantic, partial, and
lost-in-fork classifications need behavioral or provenance evidence that
patch similarity cannot establish. The report's completeness gate currently
means that every discovered historical commit has an explicit disposition;
grouping commits into durable capabilities is the next slice.

The issue's reference SHAs must be fetched into the local object database
before running the command. A missing object is an input failure, not evidence
that a feature is absent.
