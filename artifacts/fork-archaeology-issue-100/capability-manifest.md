# Phase-1 capability manifest

Frozen fork `35c8564c9c0af3d75bcbdf1d793e7207e5528f06`; upstream `460d345642ee3d143a3e461abe39fd42b86a7e54`; merge base `91937a6dc3ffbbe2f3be91a500f0ecf962c4cf53`.

## Accounting

- Historical change records: **145**
- Merge events: **26**
- Total mapped records: **171**
- Explicit accounting: **PASS**

The 116 inventory provenance buckets are evidence containers, not capability boundaries.

## Group summary

| Group | Class | Changes | Merges | Status | Disposition | Confidence |
|---|---:|---:|---:|---|---|---|
| `capability:browser-timeout-cleanup` | capability | 3 | 2 | FORK_ONLY | PORT | high |
| `capability:configurable-reasoning-display` | capability | 1 | 1 | FORK_ONLY | PORT | high |
| `capability:cron-nul-safety` | capability | 1 | 0 | SEMANTIC_UPSTREAM | DROP | high |
| `capability:headroom-compression` | capability | 4 | 1 | FORK_ONLY | PORT | high |
| `capability:headroom-retrieval` | capability | 5 | 1 | FORK_ONLY | PORT | high |
| `capability:lifecycle-sqlite-telemetry` | capability | 1 | 0 | NEEDS_REVIEW | NEEDS_REVIEW | medium |
| `capability:memory-trim-observability` | capability | 2 | 0 | NEEDS_REVIEW | NEEDS_REVIEW | low |
| `capability:outbound-code-fence-safety` | capability | 5 | 2 | SEMANTIC_UPSTREAM | DROP | high |
| `capability:request-transform-hook` | capability | 1 | 0 | FORK_ONLY | PORT | high |
| `capability:session-fts-cjk` | capability | 12 | 1 | FORK_ONLY | PORT | high |
| `capability:session-fts-lifecycle` | capability | 15 | 1 | FORK_ONLY | PORT | high |
| `capability:session-fts-simple-eol` | capability | 10 | 3 | SEMANTIC_UPSTREAM | DROP | high |
| `capability:session-fts-storage-v2` | capability | 9 | 1 | FORK_ONLY | PORT | high |
| `capability:session-fts-trigram` | capability | 30 | 3 | FORK_ONLY | PORT | high |
| `capability:session-fts-unicode` | capability | 5 | 0 | FORK_ONLY | PORT | high |
| `capability:session-search-lineage` | capability | 20 | 1 | PARTIAL_UPSTREAM | SPLIT | high |
| `capability:session-search-routing` | capability | 7 | 1 | FORK_ONLY | PORT | high |
| `capability:session-title-safety` | capability | 4 | 2 | PARTIAL_UPSTREAM | SPLIT | high |
| `non-capability:incidental-hardening` | non_capability | 3 | 0 | NEEDS_REVIEW | NEEDS_REVIEW | medium |
| `non-capability:integration-merge` | non_capability | 0 | 4 | NEEDS_REVIEW | NEEDS_REVIEW | high |
| `non-capability:performance-validation` | non_capability | 2 | 1 | NEEDS_REVIEW | NEEDS_REVIEW | high |
| `non-capability:production-evidence` | non_capability | 5 | 1 | NEEDS_REVIEW | NEEDS_REVIEW | high |

## `capability:browser-timeout-cleanup` — Browser timeout cleanup policy

**Intent:** Make browser daemon termination on timeout configuration-gated and crash-resilient while retaining finally-path cleanup.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** none recorded
**Historical change records:** 3; **merge events:** 2

**Behavioral contract:**
- Timeout cleanup clears in-memory browser state in a finally-equivalent path.
- Daemon termination is opt-in/config-gated.
- Cleanup failures are contained and test-covered.

**Current-dev survival:** Survives in tools/browser_tool.py and timeout cleanup/daemon tests.

**Upstream prior art/current implementation:** Frozen upstream has timeout cleanup/orphan reaping but not terminate_daemon_on_timeout configuration behavior.

**Unresolved questions:**
- Check whether upstream orphan reaping supersedes this toggle.

**Accounting source records:** commit:8a0a1a145e2290682e8d4e6f76ed6ec90b0b933f, commit:9e3439835e9dbbad0d0076d539d769db1f84de0a, commit:a2450695db0e99f3dd8362f129024cb74c700bb8, commit:b42bc25e622c11230998cc57f7050ec2a2cdd090, commit:cfb2636a6a3ed02ff773f106beb71c88576f1284

## `capability:configurable-reasoning-display` — Configurable reasoning display limit

**Intent:** Replace a hardcoded reasoning display limit with explicit configuration while retaining safe truncation.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** PR #3
**Historical change records:** 1; **merge events:** 1

**Behavioral contract:**
- reasoning_max_lines controls display truncation.
- Invalid or absent configuration falls back safely.
- Fence escaping remains applied before wrapping.

**Current-dev survival:** Survives in cli.py/config parsing and gateway display code.

**Upstream prior art/current implementation:** Frozen upstream hardcodes the first 15 lines and has no reasoning_max_lines seam.

**Unresolved questions:**
- Confirm configuration source and documentation expected upstream.

**Accounting source records:** commit:895ba96014ecf934dbc140306ffe578a0a533ea3, pr:3

## `capability:cron-nul-safety` — NUL-safe cron lifecycle and terminal fallback

**Intent:** Safely reject NUL-bearing paths before lifecycle/terminal process APIs can misinterpret them.

**Status / disposition / confidence:** `SEMANTIC_UPSTREAM` / `DROP` / `high`

**Historical issues/PRs:** PR #76762, PR #76762
**Historical change records:** 1; **merge events:** 0

**Behavioral contract:**
- NUL-bearing input does not reach unsafe process/lifecycle calls.
- Terminal fallback returns a clear failure rather than truncating at NUL.

**Current-dev survival:** Survives in cron/lifecycle_guard.py and tools/terminal_tool.py.

**Upstream prior art/current implementation:** Frozen upstream contains the corresponding NUL/binary-safe guard and terminal fallback.

**Unresolved questions:**
- Confirm upstream release and canonical tests.

**Accounting source records:** pr:76762

## `capability:headroom-compression` — Headroom output compression

**Intent:** Compress oversized tool/context payloads with bounded output, metadata-safe behavior, and explicit warning semantics.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** none recorded
**Historical change records:** 4; **merge events:** 1

**Behavioral contract:**
- Compression activates only at configured thresholds and does not expand tool-result metadata.
- Failures degrade with warning/logging and preserve usable content.
- Plugin registration and schema remain valid when active.

**Current-dev survival:** Survives in plugins/headroom, tools_config.py, plugin.yaml, pyproject.toml/uv.lock and plugin tests.

**Upstream prior art/current implementation:** Frozen upstream has no headroom ContentRouter/SmartCrusher implementation.

**Unresolved questions:**
- Identify upstream context-management owner before porting.
- Keep output compression separate from retrieval.

**Accounting source records:** commit:237176ed1d973910972984ba83d876d01c00f3db, commit:5eb552458de9828f4496880fd878f58015a63389, commit:915913db360de5fe5b147d4895276ff39a6e8686, commit:b7e62647bd657a429d967cfa77034632ee490f9a, commit:c16ebe6b8649c570b688fd82d6b66f9a8416d59c

## `capability:headroom-retrieval` — Headroom retrieval (CCR)

**Intent:** Expose bounded retrieval of compressed context through a registered tool when compression is active.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** none recorded
**Historical change records:** 5; **merge events:** 1

**Behavioral contract:**
- The retrieval tool has a stable schema.
- It is auto-enabled only when compression is active and returns bounded context.
- Registration failures are explicit and warning-visible.

**Current-dev survival:** Survives in plugins/headroom, tools_config.py, plugin.yaml and plugin tests.

**Upstream prior art/current implementation:** No equivalent retrieval tool is present in frozen upstream.

**Unresolved questions:**
- Decide whether retrieval is a generic upstream context API or plugin-owned.

**Accounting source records:** commit:0a4b567ff08776a3a10ee7a32d37332d873f20b2, commit:0b4e7601227a23bc653d0d3018a3a9591d02f2a1, commit:516d739f64a95a4f380b899fbb9a349d750fc2aa, commit:b527e4df996b859356c0c48c2b9c9eae4c74c4dc, commit:d3f44c9d63b7b3444e2cf4c71fcbde86020e6ea6, commit:ff4df5b71e1557660dbe994eb5569983f57c9901

## `capability:lifecycle-sqlite-telemetry` — Lifecycle and SQLite observability telemetry

**Intent:** Expose structured lifecycle, tool, stream, SQLite, and delivery telemetry at important execution boundaries.

**Status / disposition / confidence:** `NEEDS_REVIEW` / `NEEDS_REVIEW` / `medium`

**Historical issues/PRs:** none recorded
**Historical change records:** 1; **merge events:** 0

**Behavioral contract:**
- Important boundaries emit structured telemetry rather than only ad-hoc logs.
- SQLite lock/owner and stream/tool failures remain diagnosable.

**Current-dev survival:** Survives across lifecycle_telemetry.py, sqlite_native_telemetry.py, turn_context.py, gateway code and tests.

**Upstream prior art/current implementation:** Frozen upstream comparison was not completed per telemetry surface; absence is not treated as proof.

**Unresolved questions:**
- Separate domain facts from diagnostics.
- Which event names/sinks are stable enough to port?

**Accounting source records:** commit:176646d2cd6c95fa49b9414f21ed9e781b0aaa84

## `capability:memory-trim-observability` — Memory trim observability and cooldown

**Intent:** Instrument memory trim decisions with GC/trim split, fragmentation, VmSwap, low-water and cooldown signals.

**Status / disposition / confidence:** `NEEDS_REVIEW` / `NEEDS_REVIEW` / `low`

**Historical issues/PRs:** none recorded
**Historical change records:** 2; **merge events:** 0

**Behavioral contract:**
- Telemetry distinguishes measurement from the trim action and cooldown prevents repeated gc.collect calls.

**Current-dev survival:** Survives in hermes_cli/mem_trim.py and related diagnostics.

**Upstream prior art/current implementation:** Not yet compared at behavioral-contract level against frozen upstream.

**Unresolved questions:**
- Is this a user-facing capability, internal diagnostic, or non-capability?
- What evidence establishes the trim policy?

**Accounting source records:** commit:04f1af72be078cef69de538f1519f93a73088b0d, commit:a9d2b9af4f800fef23fa7ecaf2ea270b43e326eb

## `capability:outbound-code-fence-safety` — Outbound code-fence and truncation safety

**Intent:** Preserve balanced code fences and inline-code spans when reasoning, streaming chunks, or Discord edits are escaped or truncated.

**Status / disposition / confidence:** `SEMANTIC_UPSTREAM` / `DROP` / `high`

**Historical issues/PRs:** PR #2
**Historical change records:** 5; **merge events:** 2

**Behavioral contract:**
- Untrusted backticks are escaped before outer wrapping.
- Chunk balancing and truncation close open triple-backtick and inline-code spans.
- Discord edit/truncation preserves the same user-visible balance guarantee.

**Current-dev survival:** Survives in gateway/stream_consumer.py and Discord adapter/tests.

**Upstream prior art/current implementation:** Frozen upstream already has escape_code_fences_for_display, ensure_closed_code_fences, and chunk balancing with equivalent behavior.

**Unresolved questions:**
- Run upstream Discord edit-truncation tests before deleting fork provenance.

**Accounting source records:** commit:347442ddc80a2658582cb62dd268a0be024b1bcf, commit:44afcc8d3f869c7780beb1bc0bece86df1e45cb2, commit:47cc1764dc2c2c65cc83fe20500da939698d72d3, pr:2, commit:c1a3e16c32896ea2d469536c657814049c29bec6, commit:eed6ba2734bc7aaabbd1e8cf08cd794badc72d71, commit:efd68fe044001c57c5d148a66469209589c344d9

## `capability:request-transform-hook` — Full-payload outbound request transform hook

**Intent:** Allow plugins to transform the complete outbound API request before dispatch.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** none recorded
**Historical change records:** 1; **merge events:** 0

**Behavioral contract:**
- transform_api_request receives and returns the complete payload at a documented boundary.
- The hook is optional and preserves the request when absent.
- Hook errors are explicit and do not dispatch partial data.

**Current-dev survival:** Survives in agent/conversation_loop.py, hermes_cli/hooks.py, hermes_cli/plugins.py and run-agent tests.

**Upstream prior art/current implementation:** Frozen upstream has pre_llm_call but not a full-payload transform hook at dispatch.

**Unresolved questions:**
- Define ordering relative to pre_llm_call and provider adapters.

**Accounting source records:** commit:296d9c6e2e5a6f852344ad8fa9b90f5687912be1

## `capability:session-fts-cjk` — CJK session metadata search

**Intent:** Add an optional, independently maintained CJK session metadata index with safe degradation and snapshot-consistent serving.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** PR #65, issue #26, PR #26, PR #65, issue #26
**Historical change records:** 12; **merge events:** 1

**Behavioral contract:**
- CJK search covers title, id, and display_name through external-content FTS.
- Unavailable tokenizer/index state degrades safely without hiding the base path.
- Guard and MATCH run against one SQLite snapshot; rebuild is independent and resumable.

**Current-dev survival:** Survives in hermes_state.py, hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py and tests/test_session_metadata_cjk_fts.py.

**Upstream prior art/current implementation:** Frozen upstream has no session CJK metadata index or equivalent optional lifecycle.

**Unresolved questions:**
- What tokenizer availability contract is acceptable upstream?
- Should CJK remain a separate optional port?

**Accounting source records:** pr:65, commit:15f5aa07a0a1e4ffcf12b5b90aeea6d187e37114, commit:3053abf3b039a5b4c23637597cb076f2339250fb, pr:26, commit:6311e77e27f4fd4009c73d0df16abebf56121f4a, commit:6447a52cd994bf30c2811814f21095d12aba83ae, commit:6b3402ea05566fa1553a93aceb285eb517d7e67d, commit:83431f775a9e3798611e03b16a2f7d9a7ac74a57, commit:8efead2600a3f932f9881b53cd0c7eabc755f82f

## `capability:session-fts-lifecycle` — Unified six-index FTS lifecycle

**Intent:** Give all session and message FTS indexes one descriptor-driven maintenance, health, repair, discovery, and degraded-runtime boundary.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** PR #76, issue #27, PR #27, PR #76, issue #27
**Historical change records:** 15; **merge events:** 1

**Behavioral contract:**
- The registry covers messages_fts, messages_fts_trigram, messages_fts_cjk, sessions_fts, sessions_fts_cjk, and sessions_fts_trigram.
- Maintenance, health, read-only discovery, repair, and degraded runtime iterate the same descriptors.
- Unknown or incomplete trigger state fails closed for serving.

**Current-dev survival:** Survives in hermes_state.py, hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py and tests/test_fts_lifecycle_registry.py.

**Upstream prior art/current implementation:** Frozen upstream has message FTS primitives but no equivalent session six-index lifecycle boundary.

**Unresolved questions:**
- Which lifecycle pieces are required by upstream?
- How should #31 remain an explicit dependency?

**Accounting source records:** pr:27, commit:0cf4cd0baffb68b571a44f2a39d3bf817cd38348, commit:75223d19544ceb73cf24fb859fc0780ed9a099a5, pr:76, commit:a822d37d18064d1442eb20b6a2cb4e9f8b17d48d, commit:bdd9d699f6dc13b32852a078c1ecf5664bcb837d, commit:dc1f153a71d025d903f255861da57f94d649ca37

## `capability:session-fts-simple-eol` — Legacy simple-tokenizer retirement

**Intent:** Remove the unsupported legacy simple-tokenizer compatibility debt and make unsupported database states fail explicitly.

**Status / disposition / confidence:** `SEMANTIC_UPSTREAM` / `DROP` / `high`

**Historical issues/PRs:** PR #87, issue #19, PR #19, PR #87, issue #19
**Historical change records:** 10; **merge events:** 3

**Behavioral contract:**
- Supported post-#12 databases do not require simple-tokenizer loading or repair branches.
- Unsupported legacy residue is sanitized or rejected explicitly rather than silently kept writable.
- Modern trigram/CJK lifecycle remains the canonical path.

**Current-dev survival:** Survives in hermes_state.py sanitizer/repair logic and the #19/#87 research and tests.

**Upstream prior art/current implementation:** Frozen upstream has no load_simple_extension/simple-tokenizer compatibility shim; the legacy debt is already absent there.

**Unresolved questions:**
- Prove the release ancestry of upstream database states before closing the port question.

**Accounting source records:** commit:196b904417371cf233f3eb9b560c80a079ab8d72, commit:433a49f66b447f41fdd488dc4858a07c7cd7154d, commit:44dd4c5e586e60c22a322a9eb89d1aed63892e36, pr:87, pr:19, commit:a1136e548ea1f2200429bf44f8f1a7297cc84246, commit:a4e2c52f1582952686ee56aaedd7285874326102, commit:c20cb64b4b79468c641626e9b9d3957790f7a671, commit:dc2270aad84242da42f4342ac137e243ca181aa5, commit:f779b5320c6008e1c8b49bf7386fdfe76dea7a8a

## `capability:session-fts-storage-v2` — Session FTS storage-v2 settlement

**Intent:** Make required session FTS storage state explicit and refuse unsafe serving until settlement is complete.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** PR #78, issue #31, PR #31, PR #78, issue #31
**Historical change records:** 9; **merge events:** 1

**Behavioral contract:**
- One predicate defines required-index settlement; startup and foreground paths use it.
- Incomplete or interrupted required settlement refuses serving; reopen/resume reaches an explicit terminal state.
- Optional indexes do not silently become required blockers.

**Current-dev survival:** Survives in hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py and tests/test_fts_storage_v2_settlement.py.

**Upstream prior art/current implementation:** Frozen upstream has no session six-index storage-v2 settlement state machine.

**Unresolved questions:**
- Which upstream storage contract is the port target?
- Can refusal remain separate from six-index lifecycle?

**Accounting source records:** pr:31, pr:78

## `capability:session-fts-trigram` — Normalized session metadata trigram FTS

**Intent:** Provide normalized external-content trigram search for arbitrary infix session metadata with fail-closed namespace ownership.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** PR #79, issue #30, PR #30, PR #79, issue #30
**Historical change records:** 30; **merge events:** 3

**Behavioral contract:**
- The external-content source normalizes title/display_name and keeps raw id searchable.
- Serving requires complete trigger/namespace ownership; unknown same-name objects fail closed.
- Recovery markers support resumable rebuild without treating a partial index as healthy.

**Current-dev survival:** Survives in hermes_state.py, hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py and tests/test_session_metadata_trigram_fts.py.

**Upstream prior art/current implementation:** Frozen upstream has no sessions_fts_trigram external-content index; generic message trigram is not equivalent.

**Unresolved questions:**
- Validate SQLite/tokenizer deployment constraints.
- Keep #30 ownership separate from #27 orchestration.

**Accounting source records:** commit:19e6e6223bb58a4a53c8c02c86a0127d34afaf5a, commit:37811327cdc65c85c4b0bd565b1cdb3a4590dd91, pr:30, pr:79, commit:ad0c45f0924349f6f0453d4941c3d76ad5258cdc, commit:f18b3c90c2309ee7616010f99c1c927c9f05013f

## `capability:session-fts-unicode` — Unicode session metadata FTS

**Intent:** Provide stable-row-id, resumable external-content Unicode search over session title, id, and display_name.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** PR #59, issue #25, PR #59, issue #25
**Historical change records:** 5; **merge events:** 0

**Behavioral contract:**
- Stable sessions.row_id is retained across hidden rows and gaps.
- External-content FTS indexes title, id, and display_name with resumable high-water backfill.
- Backfill and triggers preserve delete correctness and never expose hidden sessions.

**Current-dev survival:** Survives in hermes_state.py, hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py, hermes_cli/session_recovery.py and tests/test_session_metadata_fts.py.

**Upstream prior art/current implementation:** Frozen upstream has messages_fts but no equivalent sessions_fts metadata index.

**Unresolved questions:**
- Confirm upstream migration and database-version policy.

**Accounting source records:** commit:2ac803bd949f0aba96c0d4d500edd2ad65aa3036, commit:9b25b0c8bd7408eaf2398529b6a04ed1a068b892, commit:a4ceb2521b8bb14e753e6f02242d0a29f6d57fb2, commit:c3f1abc5cc34f1a356740529c9f17dfc0f4157b0, pr:59

## `capability:session-search-lineage` — Compression-lineage-aware session search

**Intent:** Resolve positive compression edges to a stable root with bounded, snapshot-aware search hydration and explicit truncation.

**Status / disposition / confidence:** `PARTIAL_UPSTREAM` / `SPLIT` / `high`

**Historical issues/PRs:** PR #72, issue #68, PR #72, issue #68
**Historical change records:** 20; **merge events:** 1

**Behavioral contract:**
- Only positive compression edges are followed; branch/delegate markers and tool children do not become roots.
- Missing/cyclic lineage fails closed; query-local memo/path compression is bounded by B=2000 successful uncached row fetches.
- B exhaustion returns truncated/warning without poisoned memo; one snapshot, early-K and deferred hydration are preserved.

**Current-dev survival:** Survives in hermes_state_search.py and session-search tools/tests, including bounded winner hydration.

**Upstream prior art/current implementation:** Frozen upstream has _lineage_root_id/basic lookup but not the stronger resolver, memo, B-boundary, or explicit truncation contract.

**Unresolved questions:**
- Can the upstream model host the stronger resolver without storage changes?
- Keep benchmarks non-normative.

**Accounting source records:** commit:1f5edc591353a0a65ec7aa2f02c6916fd67fd505, commit:2732c47e28fbf7aaea97bd8c5cf82045a4c34159, commit:2d2bad204ec644455ad1273f2934f388eb4111dd, commit:2e1b2e64d7a452f524f703a99d68b1b110b93d3e, commit:2f12b03079cb7ae28acf666d16cfd748e5eaac74, pr:72, commit:3de538d32a3c076929c1d7c173dd77e4055100f9, commit:42c421f68b6556757c17b4dc78756cf31babd239, commit:46907bc8b55ba36ef73e0d96c10eccb9b28b0310, commit:55dc24391f92d71d9e293c7990089075f2ff49c9, commit:66eb2fad76abf150043ffd899fbdfbd328aba2d0, commit:6fa6c557257175f26c1689799d352fdf948cb787, commit:8838d16060bb7610c6d7ad3697f384df3edb70c1, commit:9a1f477df5c8f25fd7ba4f57318e9f5ffcb2fc32, commit:a13d012e5b8736928662f27269d9f2a7cf84f0af, commit:a615feaea695123d1b3a832417a9a76554301af8, commit:b2710666a29eed6fc46f3b6ce23c72f4dc766181, commit:c81ab321ec4bb3d5a33429b28907cd5836e41789, commit:d72f99eb1b897dd29a46692a310aa15b1bfd77e8, commit:dcd10233d32eee72df93ec7ff5f8f9efcec4eef6, commit:f32b51764e43e48195d10e62d64b195a052d4968

## `capability:session-search-routing` — Session metadata search routing

**Intent:** Route picker and resume candidates across Unicode, CJK, trigram, and bounded LIKE fallback while preserving visibility and lineage semantics.

**Status / disposition / confidence:** `FORK_ONLY` / `PORT` / `high`

**Historical issues/PRs:** issue #14, PR #14, issue #14
**Historical change records:** 7; **merge events:** 1

**Behavioral contract:**
- Unicode, CJK, and infix queries select their intended index; LIKE is bounded fallback.
- FTS narrows IDs before lineage hydration; only empty successful FTS triggers fallback.
- Visibility, archive/pin/source filters, lineage, branch markers, whole-store scope, and limits remain correct.

**Current-dev survival:** Survives in hermes_state.py, hermes_state_search.py, web session/profile routers, desktop picker code and routing tests.

**Upstream prior art/current implementation:** Frozen upstream has session ID/title primitives but no equivalent metadata-index router.

**Unresolved questions:**
- Separate routing from index contracts and desktop/API seams.
- Revalidate ranking against the post-fork upstream model.

**Accounting source records:** commit:0990803323497a36761c9627a20ceb57be56d5a3, pr:14, commit:3ebb2592c141b232ecd0717adf32279afa6bee3f, commit:3f4795a9fbc70c39cbfbe11ed93e66d36b91c01d, commit:5882274ff6c868c24f9d36f3796de08e5de8a245, commit:b4efc640fab0517571bd05447cf2be2ace9cf3e1, commit:b97d00e7319e5147d569519b6a34ef63c6635551, commit:ca4d3041a5e0b6d2b792cdf3c9f2689c9a18683e

## `capability:session-title-safety` — Literal-safe numbered-title resolution

**Intent:** Resolve numbered session-title variants without SQL LIKE metacharacters or non-numeric # suffixes becoming variants.

**Status / disposition / confidence:** `PARTIAL_UPSTREAM` / `SPLIT` / `high`

**Historical issues/PRs:** issue #15, PR #15, issue #15
**Historical change records:** 4; **merge events:** 2

**Behavioral contract:**
- Escape SQL LIKE literals independently from Python literals.
- Only strict ASCII #N suffixes are variants; %, _, \, #, CJK, and literal titles stay exact and safe.
- The public resolution API and root/current exclusion semantics remain unchanged.

**Current-dev survival:** Survives in hermes_state.py, hermes_state_common.py and title/metadata tests.

**Upstream prior art/current implementation:** Frozen upstream escapes LIKE but does not enforce the fork's strict numeric suffix filter.

**Unresolved questions:**
- Check callers that may depend on broader historical #% behavior.

**Accounting source records:** commit:984eff15df1fb749f00aece7fc24c43e9afcb7e8, pr:15

## `non-capability:incidental-hardening` — Incidental hardening and maintenance

**Intent:** Account for isolated reliability/logging/cleanup changes without manufacturing a broader ontology.

**Status / disposition / confidence:** `NEEDS_REVIEW` / `NEEDS_REVIEW` / `medium`

**Historical issues/PRs:** none recorded
**Historical change records:** 3; **merge events:** 0

**Behavioral contract:**
- The safety invariant remains visible in record subject/files while capability ownership is unresolved.

**Current-dev survival:** Record-specific survival is visible in the frozen tree.

**Upstream prior art/current implementation:** No equivalence claim is made without focused comparison.

**Unresolved questions:**
- Promote only if a user-visible invariant and test boundary is identified.

**Accounting source records:** commit:4270ea5b9a903d0dcf692db2365875ac5b3fe319, commit:9f3056cb3a9642c39056af8409cc3198007e68a8, commit:cc2531fbc6df8fdb34fca0b096b798eb53f970dd

## `non-capability:integration-merge` — Branch integration and merge bookkeeping

**Intent:** Account for merge events that combine classified work without claiming a new capability.

**Status / disposition / confidence:** `NEEDS_REVIEW` / `NEEDS_REVIEW` / `high`

**Historical issues/PRs:** none recorded
**Historical change records:** 0; **merge events:** 4

**Behavioral contract:**
- A merge event is not silently omitted merely because it has no patch-id or changed-file payload.

**Current-dev survival:** Integrated behavior is represented by capability records on either side.

**Upstream prior art/current implementation:** Merge topology is provenance, not semantic upstream evidence.

**Unresolved questions:**
- Split conflict resolution into a capability if it changes behavior.

**Accounting source records:** commit:1602d06f5045d173ee31161b92e387de69ab821c, commit:637ef2e193b6b2dada5d0b79173461fca03fbf20, commit:9d6accc0f3de1e99c8c4fde24a8f5e3fcf5ef140, commit:b66c149de0c8240e84ded1a2f983b8c42f7b984b

## `non-capability:performance-validation` — Performance validation record

**Intent:** Account for benchmarks and query-shape optimizations without upgrading measurements into semantic boundaries.

**Status / disposition / confidence:** `NEEDS_REVIEW` / `NEEDS_REVIEW` / `high`

**Historical issues/PRs:** none recorded
**Historical change records:** 2; **merge events:** 1

**Behavioral contract:**
- A benchmark supports a decision but does not prove equivalence or impose an unreviewed target.

**Current-dev survival:** Benchmark and query-shape changes remain visible in history.

**Upstream prior art/current implementation:** No upstream equivalence is inferred from local measurements.

**Unresolved questions:**
- Re-run only benchmarks needed for a concrete port candidate.

**Accounting source records:** commit:7b66bbf8e2af4fc27959f58b5ca70cfaba0346d0, commit:a34da2c31ca60f9b5280558c514a3a1880035036, commit:b879cbd332b8ac66ada6aaebfc5a9e61ae6cbe70

## `non-capability:production-evidence` — Production recovery/build evidence

**Intent:** Account for recovery runbooks, production DB build/freeze, and runtime attestation without treating them as runtime capabilities.

**Status / disposition / confidence:** `NEEDS_REVIEW` / `NEEDS_REVIEW` / `high`

**Historical issues/PRs:** PR #82, issue #21, issue #22, issue #41, PR #82, issue #21, issue #22, issue #41
**Historical change records:** 5; **merge events:** 1

**Behavioral contract:**
- Operational records remain durable evidence and are not mistaken for code-level equivalence.

**Current-dev survival:** Survives as docs/research and research/recovery artifacts.

**Upstream prior art/current implementation:** No code-level equivalence is claimed from operational documents.

**Unresolved questions:**
- Which recovery procedures remain required after a future port?

**Accounting source records:** commit:0460c1944c8c7c40c6d9d7b7ebdb9e036aa49d57, commit:311bf7d6d28b204f0aa977ddcd05d44141d2d4ba, commit:4292711d0a7cdcef9ef20399b1da6d4b1c87d9ba, commit:8b085795df0359adbf3c81315f888bc7041fb044, pr:82, commit:cea5282a181d23af471f4467f449fd9bfc022ca5

## Phase 2 boundaries

- Port session metadata FTS base, CJK, trigram, lifecycle, and storage-v2 as separate slices with explicit dependency ordering.
- Port session search routing only after index contracts and the lineage resolver are independently validated.
- Evaluate headroom output compression and CCR retrieval as separate optional plugin slices.
- Port configurable reasoning display and request transformation after upstream hook/config ordering is specified.
- Keep lifecycle telemetry, memory-trim observability, and incidental hardening in NEEDS_REVIEW until contracts and upstream comparisons are complete.
