# Issue #100: frozen fork capability accounting

This report is the Phase-1 semantic accounting for the frozen fork history requested by [issue #100](https://github.com/Skywind5487/hermes-agent/issues/100). It consumes the inventory produced by [PR #99](https://github.com/Skywind5487/hermes-agent/pull/99) and does not change the excavation tooling.

## Frozen boundary

- Fork: `35c8564c9c0af3d75bcbdf1d793e7207e5528f06`
- Upstream: `460d345642ee3d143a3e461abe39fd42b86a7e54`
- Merge base: `91937a6dc3ffbbe2f3be91a500f0ecf962c4cf53`
- Input: 145 historical change records and 26 merge events.
- Result: all 171 records are explicitly mapped; the archaeology runner reports discovery and capability accounting PASS.

The original 116 provisional provenance buckets are not used as capability boundaries. They remain available in the input inventory and are listed as `source_inventory_records` in the manifest.

## Grouping method

Each record was triaged on four axes:

1. historical intent and behavioral contract;
2. merge provenance and work-item identity;
3. survival in the frozen fork dev tree and tests;
4. semantic prior art/current implementation at the frozen upstream ref.

A capability is only a behavior boundary that can state an observable contract and a verification surface. Research notes, production evidence, benchmarks, integration topology, and isolated maintenance are explicit non-capability categories rather than silent omissions. Merge events with their own feature provenance stay attached to that capability; integration-only merges are accounted as bookkeeping.

## Phase-1 disposition

The main port candidates are the session FTS family (Unicode base, CJK, normalized trigram, unified lifecycle, and storage-v2), session search routing, compression-lineage search, literal-safe numbered-title resolution, headroom compression/CCR retrieval, browser timeout cleanup, configurable reasoning display, and the full-payload request transform hook.

Two boundaries are already semantically upstream at the frozen ref and are marked `DROP`: outbound code-fence/truncation safety and legacy simple-tokenizer retirement. Numbered-title safety and compression-lineage search are `PARTIAL_UPSTREAM` and therefore `SPLIT`, not exact replacements. Lifecycle/SQLite telemetry and memory-trim observability remain `NEEDS_REVIEW` because the current comparison does not establish stable cross-project contracts.

The machine-readable manifest contains the per-group historical commits, issues/PRs, merge events, contracts/tests, current-dev survival, upstream evidence, status, disposition, confidence, and unresolved questions. The generated inventory is the auditable 171-record accounting layer.

## Phase-2 boundaries

- Port session metadata FTS base, CJK, trigram, lifecycle, and storage-v2 as separate slices with explicit dependency ordering.
- Port session search routing only after index contracts and the lineage resolver are independently validated.
- Evaluate headroom output compression and CCR retrieval as separate optional plugin slices.
- Port configurable reasoning display and request transformation after upstream hook/config ordering is specified.
- Keep telemetry, memory-trim observability, and incidental hardening in `NEEDS_REVIEW` until their contracts and upstream comparisons are complete.

## Evidence gaps

- Frozen upstream has partial lineage/title prior art, but not the stronger fork contracts; replacement requires focused behavior tests.
- The session FTS family is fork-only at the frozen upstream ref, but deployment/tokenizer and migration policies still need upstream-target decisions.
- Some broad observability and maintenance records survive in dev without a stable issue-level contract; they are intentionally not promoted.
- Static archaeology does not prove runtime equivalence. A Phase-2 port needs fresh isolated tests and, where applicable, live database/browser/Discord validation.
