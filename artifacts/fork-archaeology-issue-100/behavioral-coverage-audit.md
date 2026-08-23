# Adversarial behavioral coverage audit

This audit reviews historical changes at hunk/intent level. A commit title or provisional provenance bucket is not treated as a capability boundary.

- Change records reviewed: **145**
- Merge events accounted: **26**
- Behavior-bearing rows: **141**
- Promoted separate-intent rows: **5**
- Explicit non-semantic/bookkeeping rows: **8**
- Uncovered behavior rows: **0**
- Completeness gate: **PASS**

## False-negative corrections

| Record | Existing bucket | Behavioral intent | Coverage/action | Proposed capability |
|---|---|---|---|---|
| `04f1af72be07` | `capability:memory-trim-diagnostics` | RSS below threshold_mb skips the expensive trim work. | `PROMOTE_TO_SEPARATE_CAPABILITY` | `capability:memory-trim-policy` |
| `04f1af72be07` | `capability:memory-trim-diagnostics` | Trim diagnostics attribute GC cost, allocator fragmentation, and swap pressure. | `COVERED_BY_SPLIT_CAPABILITY` | `` |
| `176646d2cd6c` | `capability:lifecycle-sqlite-telemetry` | A compressor-reported noop does not rotate or rewrite the session, even when it returns a new list object. | `PROMOTE_TO_SEPARATE_CAPABILITY` | `capability:compression-session-boundary` |
| `176646d2cd6c` | `capability:lifecycle-sqlite-telemetry` | Structured lifecycle and SQLite telemetry is emitted at execution boundaries. | `COVERED_BY_EXISTING_CONTRACT` | `` |
| `a9d2b9af4f80` | `capability:memory-trim-policy` | gc.collect() is independently cooled down while malloc_trim remains on the trim cadence. | `PROMOTE_TO_SEPARATE_CAPABILITY` | `capability:memory-trim-policy` |
| `b879cbd332b8` | `non-capability:performance-validation` | The query shape forces ranked LIMIT/OFFSET evaluation inside the FTS subquery. | `COVERED_BY_EXISTING_CONTRACT` | `` |
| `b879cbd332b8` | `non-capability:performance-validation` | Search context is hydrated in bounded session batches under one read lock, then indexed outside the lock before producing the same -1/0/+1 window. | `PROMOTE_TO_SEPARATE_CAPABILITY` | `capability:session-search-context-hydration` |
| `7b66bbf8e2af` | `non-capability:performance-validation` | Benchmark records cold-open latency and checks fallback invariants; production routing is not changed by this commit. | `EXPLICIT_NON_SEMANTIC_VALIDATION` | `` |
| `cc2531fbc6df` | `non-capability:incidental-hardening` | SQLite write contention changes from uniform 20-150ms jitter to exponential backoff with a 20ms base and 2s cap. | `PROMOTE_TO_SEPARATE_CAPABILITY` | `capability:sqlite-write-contention-policy` |
| `cc2531fbc6df` | `non-capability:incidental-hardening` | Slow-write and Discord delivery diagnostics are added without changing the delivery contract. | `RETAIN_AS_EXPLICIT_DIAGNOSTICS` | `` |

The full exhaustive per-record and per-merge-event accounting is in `behavioral-coverage-audit.json`. The existing archaeology runner remains commit-to-bucket accounting; this artifact is the many-to-many semantic overlay.
