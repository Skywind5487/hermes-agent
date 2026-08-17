# Fork archaeology inventory

- Fork ref: `35c8564c9c0af3d75bcbdf1d793e7207e5528f06`
- Upstream ref: `460d345642ee3d143a3e461abe39fd42b86a7e54`
- Merge base: `91937a6dc3ffbbe2f3be91a500f0ecf962c4cf53`

| Provenance bucket | Change records | Merge events | Status | Disposition | Confidence |
| --- | ---: | ---: | --- | --- | --- |
| `capability:session-fts-storage-v2` | 9 | 1 | `FORK_ONLY` | `PORT` | `high` |
| `capability:session-fts-lifecycle` | 15 | 1 | `FORK_ONLY` | `PORT` | `high` |
| `capability:memory-trim-diagnostics` | 1 | 0 | `PARTIAL_UPSTREAM` | `KEEP` | `medium` |
| `capability:headroom-retrieval` | 5 | 1 | `FORK_ONLY` | `PORT` | `high` |
| `capability:session-fts-cjk` | 12 | 1 | `FORK_ONLY` | `PORT` | `high` |
| `capability:lifecycle-sqlite-telemetry` | 1 | 0 | `NEEDS_REVIEW` | `NEEDS_REVIEW` | `medium` |
| `capability:request-transform-hook` | 1 | 0 | `FORK_ONLY` | `PORT` | `high` |
| `capability:session-fts-trigram` | 30 | 3 | `FORK_ONLY` | `PORT` | `high` |
| `capability:session-search-lineage` | 20 | 1 | `PARTIAL_UPSTREAM` | `SPLIT` | `high` |
| `capability:outbound-code-fence-safety` | 5 | 2 | `SEMANTIC_UPSTREAM` | `DROP` | `high` |
| `capability:session-fts-simple-eol` | 10 | 3 | `SEMANTIC_UPSTREAM` | `DROP` | `high` |
| `capability:configurable-reasoning-display` | 1 | 1 | `FORK_ONLY` | `PORT` | `high` |
| `capability:headroom-compression` | 4 | 1 | `FORK_ONLY` | `PORT` | `high` |
| `non-capability:integration-merge` | 0 | 4 | `NEEDS_REVIEW` | `NEEDS_REVIEW` | `high` |
| `capability:browser-timeout-cleanup` | 3 | 2 | `FORK_ONLY` | `PORT` | `high` |
| `capability:memory-trim-policy` | 1 | 0 | `PARTIAL_UPSTREAM` | `PORT` | `high` |
| `non-capability:performance-validation` | 2 | 1 | `NEEDS_REVIEW` | `NEEDS_REVIEW` | `high` |
| `capability:session-fts-unicode` | 5 | 0 | `FORK_ONLY` | `PORT` | `high` |
| `capability:session-search-routing` | 7 | 1 | `FORK_ONLY` | `PORT` | `high` |
| `non-capability:incidental-hardening` | 3 | 0 | `NEEDS_REVIEW` | `NEEDS_REVIEW` | `medium` |
| `non-capability:production-evidence` | 5 | 1 | `NEEDS_REVIEW` | `NEEDS_REVIEW` | `high` |
| `capability:session-title-safety` | 4 | 2 | `PARTIAL_UPSTREAM` | `SPLIT` | `high` |
| `capability:cron-nul-safety` | 1 | 0 | `SEMANTIC_UPSTREAM` | `DROP` | `high` |

## Evidence

### `capability:session-fts-storage-v2` - test(fts): pin storage-v2 refusal states (#31)

- Changes: `00a2675a9270`, `b86e4f526ec3`, `2c907113fd2c`, `f887f44fa2d2`, `180ae82a2d7e`, `1d6d73f3bb8f`, `0fafc1dd7b58`, `67d4f719d6b0`, `0ff8583e91c8`
- Merge events: `276d497764fe`
- Files: `docs/research/issue-31-storage-v2-settlement.md`, `hermes_state_common.py`, `hermes_state_schema.py`, `hermes_state_search.py`, `tests/test_fts_storage_v2_settlement.py`
- Upstream matches: none
- Evidence: Survives in hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py and tests/test_fts_storage_v2_settlement.py.; Frozen upstream has no session six-index storage-v2 settlement state machine.
- Behavioral contracts: One predicate defines required-index settlement; startup and foreground paths use it.; Incomplete or interrupted required settlement refuses serving; reopen/resume reaches an explicit terminal state.; Optional indexes do not silently become required blockers.

### `capability:session-fts-lifecycle` - feat(fts): authoritative six-index registry + registry-driven ordinary maintenance (#27)

- Changes: `00e680a8cb22`, `e64af5e0f8a3`, `474b88fe76e1`, `f9ade6baff7f`, `e64ad007b459`, `bdd9d699f6dc`, `0400c66f072d`, `75223d19544c`, `933a2ce86c8a`, `dc1f153a71d0`, `ed5ab2e8ecd0`, `a822d37d1806`, `3bc7d3f58f80`, `d6a4d7048362`, `0cf4cd0baffb`
- Merge events: `9d140c8594c6`
- Files: `docs/research/issue-27-unified-fts-lifecycle.md`, `hermes_state.py`, `hermes_state_common.py`, `hermes_state_schema.py`, `hermes_state_search.py`, `tests/test_fts_lifecycle_registry.py`, `tests/test_hermes_state.py`
- Upstream matches: none
- Evidence: Survives in hermes_state.py, hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py and tests/test_fts_lifecycle_registry.py.; Frozen upstream has message FTS primitives but no equivalent session six-index lifecycle boundary.
- Behavioral contracts: The registry covers messages_fts, messages_fts_trigram, messages_fts_cjk, sessions_fts, sessions_fts_cjk, and sessions_fts_trigram.; Maintenance, health, read-only discovery, repair, and degraded runtime iterate the same descriptors.; Unknown or incomplete trigger state fails closed for serving.

### `capability:memory-trim-diagnostics` - feat(mem-trim): instrument gc/trim split + malloc_info frag + VmSwap, add RSS low-water threshold

- Changes: `04f1af72be07`
- Merge events: (none)
- Files: `hermes_cli/mem_trim.py`
- Upstream matches: none
- Evidence: Frozen fork splits gc_ms and trim_ms, records VmSwap, and measures malloc_info fragmentation.; Frozen upstream has basic RSS/RssAnon snapshots and trim logging but not the fork's attribution diagnostics.
- Behavioral contracts: gc_ms versus trim_ms identifies the freeze mechanism without changing the policy decision.; Fragmentation and VmSwap are best-effort diagnostics and never block memory recovery.

### `capability:headroom-retrieval` - feat(headroom): add retrieval (CCR) support to Phase 1 compression plugin

- Changes: `0a4b567ff087`, `d3f44c9d63b7`, `ff4df5b71e15`, `b527e4df996b`, `0b4e7601227a`
- Merge events: `516d739f64a9`
- Files: `hermes_cli/tools_config.py`, `plugins/headroom/__init__.py`, `plugins/headroom/plugin.yaml`, `pyproject.toml`, `tests/plugins/test_headroom_plugin.py`, `uv.lock`
- Upstream matches: none
- Evidence: Survives in plugins/headroom, tools_config.py, plugin.yaml and plugin tests.; No equivalent retrieval tool is present in frozen upstream.
- Behavioral contracts: The retrieval tool has a stable schema.; It is auto-enabled only when compression is active and returns bounded context.; Registration failures are explicit and warning-visible.

### `capability:session-fts-cjk` - fix: migrate FTS5 trigram tokenizer from trigram to simple for CJK search

- Changes: `15f5aa07a0a1`, `3053abf3b039`, `6311e77e27f4`, `3e23375f7934`, `7b4d37c2727c`, `8ec0ec3bf85a`, `47c537c766bb`, `83431f775a9e`, `8efead2600a3`, `6b3402ea0556`, `0eee69ef43f1`, `6447a52cd994`
- Merge events: `bdf2fc218264`
- Files: `docs/design/session-metadata-fts.md`, `docs/research/issue-33-cjk-session-metadata-fts-lifecycle.md`, `hermes_cli/session_recovery.py`, `hermes_state.py`, `hermes_state_common.py`, `hermes_state_schema.py`, `hermes_state_search.py`, `tests/hermes_cli/test_session_recovery.py`, `tests/test_session_metadata_cjk_fts.py`, `tests/test_session_metadata_picker_routing.py`, `tools/session_search_tool.py`
- Upstream matches: none
- Evidence: Survives in hermes_state.py, hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py and tests/test_session_metadata_cjk_fts.py.; Frozen upstream has no session CJK metadata index or equivalent optional lifecycle.
- Behavioral contracts: CJK search covers title, id, and display_name through external-content FTS.; Unavailable tokenizer/index state degrades safely without hiding the base path.; Guard and MATCH run against one SQLite snapshot; rebuild is independent and resumable.

### `capability:lifecycle-sqlite-telemetry` - feat: add lifecycle and SQLite observability telemetry

- Changes: `176646d2cd6c`
- Merge events: (none)
- Files: `agent/conversation_compression.py`, `agent/conversation_loop.py`, `agent/diagnostic_config.py`, `agent/kernel_telemetry.py`, `agent/lifecycle_telemetry.py`, `agent/sqlite_native_telemetry.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `docs/observability/session-db-api-handoff.md`, `gateway/run.py`, `gateway/stream_consumer.py`, `hermes_cli/config.py`, `hermes_state.py`, `plugins/memory/holographic/__init__.py`, `plugins/platforms/discord/adapter.py`, `scripts/hermes_kernel_trace.py`, `tests/agent/test_lifecycle_telemetry.py`, `tests/agent/test_tool_lifecycle_telemetry.py`, `tests/gateway/test_discord_delivery_lifecycle_telemetry.py`, `tests/gateway/test_stream_lifecycle_telemetry.py`, `tests/plugins/memory/test_holographic_prefetch_gate.py`, `tests/test_machine_health_collector.py`, `tests/test_optional_cjk_tokenizer_fallback.py`, `tests/test_session_db_lock_telemetry.py`, `tests/test_sqlite_kernel_telemetry.py`, `tests/test_sqlite_owner_telemetry.py`, `tools/session_search_tool.py`
- Upstream matches: none
- Evidence: Survives across lifecycle_telemetry.py, sqlite_native_telemetry.py, turn_context.py, gateway code and tests.; Frozen upstream comparison was not completed per telemetry surface; absence is not treated as proof.
- Behavioral contracts: Important boundaries emit structured telemetry rather than only ad-hoc logs.; SQLite lock/owner and stream/tool failures remain diagnosable.

### `capability:request-transform-hook` - feat: add transform_api_request plugin hook for full-payload request transformation

- Changes: `296d9c6e2e5a`
- Merge events: (none)
- Files: `agent/conversation_loop.py`, `hermes_cli/hooks.py`, `hermes_cli/plugins.py`, `tests/run_agent/test_run_agent.py`
- Upstream matches: none
- Evidence: Survives in agent/conversation_loop.py, hermes_cli/hooks.py, hermes_cli/plugins.py and run-agent tests.; Frozen upstream has pre_llm_call but not a full-payload transform hook at dispatch.
- Behavioral contracts: transform_api_request receives and returns the complete payload at a documented boundary.; The hook is optional and preserves the request when absent.; Hook errors are explicit and do not dispatch partial data.

### `capability:session-fts-trigram` - feat(title-fts5): add sessions_fts + sessions_fts_trigram FTS5 tables with CJK dispatch

- Changes: `37811327cdc6`, `19e6e6223bb5`, `ad0c45f09243`, `48b04dae36d6`, `6f82bcbb3c5f`, `0f29f1454ea2`, `dc2cd8cfaf94`, `252920f02442`, `4483194b8149`, `bfa928d53da7`, `cc5261ce775c`, `e33b533e1d24`, `b5155410abbf`, `9aad13bcad68`, `3172c9f4611c`, `6467a30563fb`, `e1ef573d3feb`, `72388ff87f0b`, `0c02b83d4d87`, `a9c1b2d11bab`, `2142e2fa1b49`, `c7163001f23f`, `6f534c8760a3`, `10e3419d10e9`, `69591fe65977`, `7b513c6bf2f6`, `783d3fa59ae2`, `4aefa3abbbe4`, `d4c9a165c5ab`, `f17ab5897975`
- Merge events: `f18b3c90c230`, `919f4469e832`, `4e5ad5c22303`
- Files: `docs/research/issue-30-normalized-session-metadata-trigram-fts.md`, `docs/research/issue-34-normalized-session-metadata-trigram-fts.md`, `docs/research/issue-79-session-recovery-trigram-markers.md`, `hermes_cli/session_recovery.py`, `hermes_state.py`, `hermes_state_common.py`, `hermes_state_schema.py`, `hermes_state_search.py`, `tests/hermes_cli/test_session_recovery.py`, `tests/test_session_metadata_trigram_fts.py`
- Upstream matches: none
- Evidence: Survives in hermes_state.py, hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py and tests/test_session_metadata_trigram_fts.py.; Frozen upstream has no sessions_fts_trigram external-content index; generic message trigram is not equivalent.
- Behavioral contracts: The external-content source normalizes title/display_name and keeps raw id searchable.; Serving requires complete trigger/namespace ownership; unknown same-name objects fail closed.; Recovery markers support resumable rebuild without treating a partial index as healthy.

### `capability:session-search-lineage` - perf: reduce SELECT * in get_compression_lineage to 4 columns

- Changes: `3de538d32a3c`, `6fa6c5572571`, `2732c47e28fb`, `2d2bad204ec6`, `9a1f477df5c8`, `d72f99eb1b89`, `8838d16060bb`, `46907bc8b55b`, `2f12b03079cb`, `2e1b2e64d7a4`, `a13d012e5b87`, `66eb2fad76ab`, `f32b51764e43`, `42c421f68b65`, `c81ab321ec4b`, `dcd10233d32e`, `55dc24391f92`, `b2710666a29e`, `1f5edc591353`, `a615feaea695`
- Merge events: `2f90c254d524`
- Files: `hermes_state.py`, `hermes_state_search.py`, `research/session-lineage/README.md`, `tests/hermes_state/test_get_messages_around.py`, `tests/test_session_metadata_picker_routing.py`, `tests/test_session_search_sql_winners.py`, `tests/tools/test_session_search.py`, `tools/session_search_tool.py`, `website/docs/developer-guide/session-storage.md`
- Upstream matches: none
- Evidence: Survives in hermes_state_search.py and session-search tools/tests, including bounded winner hydration.; Frozen upstream has _lineage_root_id/basic lookup but not the stronger resolver, memo, B-boundary, or explicit truncation contract.
- Behavioral contracts: Only positive compression edges are followed; branch/delegate markers and tool children do not become roots.; Missing/cyclic lineage fails closed; query-local memo/path compression is bounded by B=2000 successful uncached row fetches.; B exhaustion returns truncated/warning without poisoned memo; one snapshot, early-K and deferred hydration are preserved.

### `capability:outbound-code-fence-safety` - test: code fence tracking coverage for all send/split paths

- Changes: `44afcc8d3f86`, `47cc1764dc2c`, `347442ddc80a`, `efd68fe04400`, `c1a3e16c3289`
- Merge events: `535b33c95f94`, `eed6ba2734bc`
- Files: `gateway/platforms/base.py`, `gateway/run.py`, `gateway/stream_consumer.py`, `plugins/platforms/discord/adapter.py`, `tests/gateway/test_code_fence_tracking.py`, `tests/gateway/test_escape_reasoning_fences.py`, `tests/gateway/test_truncation_fence_edge_case.py`, `tests/plugins/platforms/discord/test_edit_message_fence.py`
- Upstream matches: none
- Evidence: Survives in gateway/stream_consumer.py and Discord adapter/tests.; Frozen upstream already has escape_code_fences_for_display, ensure_closed_code_fences, and chunk balancing with equivalent behavior.
- Behavioral contracts: Untrusted backticks are escaped before outer wrapping.; Chunk balancing and truncation close open triple-backtick and inline-code spans.; Discord edit/truncation preserves the same user-visible balance guarantee.

### `capability:session-fts-simple-eol` - fix: load simple FTS5 tokenizer and apply read-performance PRAGMAs

- Changes: `44dd4c5e586e`, `a4e2c52f1582`, `a1136e548ea1`, `c20cb64b4b79`, `f779b5320c60`, `196b90441737`, `85fd91fd7c7b`, `186986a44708`, `deb10243cceb`, `1d5b80cab802`
- Merge events: `dc2270aad842`, `433a49f66b44`, `53da9e89f8f3`
- Files: `docs/research/issue-19-simple-tokenizer-eol-implementation.md`, `docs/research/issue-40-simple-tokenizer-eol.md`, `hermes_state.py`, `tests/test_optional_cjk_tokenizer_fallback.py`, `tests/test_simple_tokenizer_eol.py`
- Upstream matches: none
- Evidence: Survives in hermes_state.py sanitizer/repair logic and the #19/#87 research and tests.; Frozen upstream has no load_simple_extension/simple-tokenizer compatibility shim; the legacy debt is already absent there.
- Behavioral contracts: Supported post-#12 databases do not require simple-tokenizer loading or repair branches.; Unsupported legacy residue is sanitized or rejected explicitly rather than silently kept writable.; Modern trigram/CJK lifecycle remains the canonical path.

### `capability:configurable-reasoning-display` - feat: configurable reasoning max lines via display.reasoning_max_lines

- Changes: `895ba96014ec`
- Merge events: `9fe97e2aa1b0`
- Files: `cli.py`, `gateway/run.py`, `tests/gateway/test_reasoning_max_lines.py`
- Upstream matches: none
- Evidence: Survives in cli.py/config parsing and gateway display code.; Frozen upstream hardcodes the first 15 lines and has no reasoning_max_lines seam.
- Behavioral contracts: reasoning_max_lines controls display truncation.; Invalid or absent configuration falls back safely.; Fence escaping remains applied before wrapping.

### `capability:headroom-compression` - Add opt-in headroom tool-output compression plugin

- Changes: `915913db360d`, `5eb552458de9`, `c16ebe6b8649`, `237176ed1d97`
- Merge events: `b7e62647bd65`
- Files: `hermes_cli/config.py`, `plugins/headroom/__init__.py`, `plugins/headroom/plugin.yaml`, `tests/plugins/test_headroom_plugin.py`
- Upstream matches: none
- Evidence: Survives in plugins/headroom, tools_config.py, plugin.yaml, pyproject.toml/uv.lock and plugin tests.; Frozen upstream has no headroom ContentRouter/SmartCrusher implementation.
- Behavioral contracts: Compression activates only at configured thresholds and does not expand tool-result metadata.; Failures degrade with warning/logging and preserve usable content.; Plugin registration and schema remain valid when active.

### `non-capability:integration-merge` - Merge remote-tracking branch 'origin/main' into dev

- Changes: (none)
- Merge events: `9d6accc0f3de`, `1602d06f5045`, `b66c149de0c8`, `637ef2e193b6`
- Files: (none)
- Upstream matches: none
- Evidence: Integrated behavior is represented by capability records on either side.; Merge topology is provenance, not semantic upstream evidence.
- Behavioral contracts: A merge event is not silently omitted merely because it has no patch-id or changed-file payload.

### `capability:browser-timeout-cleanup` - fix(browser): config-gated daemon kill on timeout

- Changes: `a2450695db0e`, `b42bc25e622c`, `cfb2636a6a3e`
- Merge events: `9e3439835e9d`, `8a0a1a145e22`
- Files: `.github/actions/hermes-smoke-test/action.yml`, `.github/workflows/build-windows-installer.yml`, `.github/workflows/docker-publish.yml`, `.github/workflows/nix-lockfile-fix.yml`, `.github/workflows/nix.yml`, `agent/gemini_cloudcode_adapter.py`, `agent/google_code_assist.py`, `agent/google_oauth.py`, `apps/desktop/src/app/chat/composer/skin-slash-popover.tsx`, `apps/desktop/src/app/cron/cron-job-actions-menu.tsx`, `apps/desktop/src/app/overlays/overlay-search-input.tsx`, `apps/desktop/src/app/right-sidebar/terminal/index.tsx`, `apps/desktop/src/app/session/hooks/use-message-stream.ts`, `apps/desktop/src/app/session/hooks/use-prompt-actions.test.tsx`, `apps/desktop/src/app/session/hooks/use-prompt-actions.ts`, `apps/desktop/src/app/session/hooks/use-session-actions.ts`, `apps/desktop/src/components/assistant-ui/streaming.test.tsx`, `apps/desktop/src/components/assistant-ui/thread-virtualizer.tsx`, `apps/desktop/src/components/assistant-ui/thread.tsx`, `apps/desktop/src/components/assistant-ui/todo-tool.tsx`, `apps/desktop/src/components/assistant-ui/tool-approval-group.test.tsx`, `apps/desktop/src/components/assistant-ui/tool-approval.test.tsx`, `apps/desktop/src/components/assistant-ui/tool-approval.tsx`, `apps/desktop/src/components/assistant-ui/tool-fallback-model.test.ts`, `apps/desktop/src/components/assistant-ui/tool-fallback-model.ts`, `apps/desktop/src/components/assistant-ui/tool-fallback.tsx`, `apps/desktop/src/components/assistant-ui/user-message-text.tsx`, `apps/desktop/src/components/chat/generated-image-context.tsx`, `apps/desktop/src/components/desktop-onboarding-overlay.test.tsx`, `apps/desktop/src/components/desktop-onboarding-overlay.tsx`, `apps/desktop/src/components/ui/braille-spinner.tsx`, `apps/desktop/src/lib/gateway-ws-url.ts`, `gateway/platforms/dingtalk.py`, `gateway/platforms/email.py`, `gateway/platforms/feishu.py`, `gateway/platforms/feishu_comment.py`, `gateway/platforms/feishu_comment_rules.py`, `gateway/platforms/feishu_meeting_invite.py`, `gateway/platforms/homeassistant.py`, `gateway/platforms/matrix.py`, `gateway/platforms/slack.py`, `gateway/platforms/sms.py`, `gateway/platforms/telegram.py`, `gateway/platforms/telegram_network.py`, `gateway/platforms/wecom.py`, `gateway/platforms/wecom_callback.py`, `gateway/platforms/wecom_crypto.py`, `gateway/platforms/whatsapp.py`, `optional-skills/productivity/shop-app/SKILL.md`, `plans/gemini-oauth-provider.md`, `tests/tools/test_browser_terminate_daemon_timeout.py`, `tests/tools/test_browser_timeout_cleanup.py`, `tools/browser_tool.py`
- Upstream matches: none
- Evidence: Survives in tools/browser_tool.py and timeout cleanup/daemon tests.; Frozen upstream has timeout cleanup/orphan reaping but not terminate_daemon_on_timeout configuration behavior.
- Behavioral contracts: Timeout cleanup clears in-memory browser state in a finally-equivalent path.; Daemon termination is opt-in/config-gated.; Cleanup failures are contained and test-covered.

### `capability:memory-trim-policy` - fix(mem-trim): gate gc.collect() behind gc_cooldown_seconds, measure frag before trim

- Changes: `a9d2b9af4f80`
- Merge events: (none)
- Files: `hermes_cli/mem_trim.py`
- Upstream matches: none
- Evidence: Frozen fork hermes_cli/mem_trim.py adds threshold_mb and gc_cooldown_seconds policy gates.; Frozen upstream hermes_cli/mem_trim.py has a general trim cooldown but no RSS low-water gate or independent GC cooldown.
- Behavioral contracts: RSS below threshold_mb skips the expensive trim work on housekeeping ticks.; gc.collect() runs at most once per gc_cooldown_seconds while malloc_trim remains eligible on the normal trim cadence.; Force behavior and invalid configuration retain explicit safe fallbacks.

### `non-capability:performance-validation` - perf(search): batch context queries — chunked session_id batches (20/sql)

- Changes: `b879cbd332b8`, `7b66bbf8e2af`
- Merge events: `a34da2c31ca6`
- Files: `hermes_state.py`, `scripts/benchmarks/session_metadata_picker.py`, `tools/session_search_tool.py`
- Upstream matches: none
- Evidence: Benchmark and query-shape changes remain visible in history.; No upstream equivalence is inferred from local measurements.
- Behavioral contracts: A benchmark supports a decision but does not prove equivalence or impose an unreviewed target.

### `capability:session-fts-unicode` - fix: use _rowid_ (INT) instead of id (TEXT) for sessions_fts triggers and queries

- Changes: `c3f1abc5cc34`, `2ac803bd949f`, `9b25b0c8bd74`, `e94f2630a50d`, `a4ceb2521b8b`
- Merge events: (none)
- Files: `docs/design/session-metadata-fts.md`, `docs/research/issue-32-stable-row-id-unicode-session-fts.md`, `hermes_cli/session_recovery.py`, `hermes_state.py`, `hermes_state_common.py`, `hermes_state_schema.py`, `hermes_state_search.py`, `tests/test_session_metadata_fts.py`
- Upstream matches: none
- Evidence: Survives in hermes_state.py, hermes_state_common.py, hermes_state_schema.py, hermes_state_search.py, hermes_cli/session_recovery.py and tests/test_session_metadata_fts.py.; Frozen upstream has messages_fts but no equivalent sessions_fts metadata index.
- Behavioral contracts: Stable sessions.row_id is retained across hidden rows and gaps.; External-content FTS indexes title, id, and display_name with resumable high-water backfill.; Backfill and triggers preserve delete correctness and never expose hidden sessions.

### `capability:session-search-routing` - fix(session-search): define ranked_candidates for LIKE route

- Changes: `ca4d3041a5e0`, `099080332349`, `5882274ff6c8`, `3f4795a9fbc7`, `b4efc640fab0`, `b97d00e7319e`, `3ebb2592c141`
- Merge events: `35c8564c9c0a`
- Files: `apps/desktop/src/components/session-picker.test.tsx`, `apps/desktop/src/components/session-picker.tsx`, `apps/desktop/src/hermes.test.ts`, `apps/desktop/src/hermes.ts`, `apps/desktop/src/i18n/en.ts`, `apps/desktop/src/i18n/types.ts`, `apps/desktop/src/i18n/zh.ts`, `hermes_cli/web_routers/profiles.py`, `hermes_cli/web_routers/sessions.py`, `hermes_state.py`, `scripts/benchmarks/session_metadata_picker.py`, `tests/hermes_cli/test_web_server.py`, `tests/hermes_cli/test_web_server_session_search.py`, `tests/test_session_metadata_picker_routing.py`
- Upstream matches: none
- Evidence: Survives in hermes_state.py, hermes_state_search.py, web session/profile routers, desktop picker code and routing tests.; Frozen upstream has session ID/title primitives but no equivalent metadata-index router.
- Behavioral contracts: Unicode, CJK, and infix queries select their intended index; LIKE is bounded fallback.; FTS narrows IDs before lineage hydration; only empty successful FTS triggers fallback.; Visibility, archive/pin/source filters, lineage, branch markers, whole-store scope, and limits remain correct.

### `non-capability:incidental-hardening` - fix: exponential backoff, slow-write log, and Discord API delivery log

- Changes: `cc2531fbc6df`, `9f3056cb3a96`, `4270ea5b9a90`
- Merge events: (none)
- Files: `.gitignore`, `hermes_state.py`, `plugins/platforms/discord/adapter.py`, `tests/tools/test_web_searxng_e2e.py`
- Upstream matches: none
- Evidence: Record-specific survival is visible in the frozen tree.; No equivalence claim is made without focused comparison.
- Behavioral contracts: The safety invariant remains visible in record subject/files while capability ownership is unresolved.

### `non-capability:production-evidence` - docs(recovery): attest production gateway runtime surface

- Changes: `cea5282a181d`, `311bf7d6d28b`, `0460c1944c8c`, `8b085795df03`, `4292711d0a7c`
- Merge events: `bee13fc08507`
- Files: `docs/research/issue-22-production-ready-state-db-execution.md`, `research/recovery/issue-21-production-runtime-runbook.md`, `research/recovery/issue-41-production-gateway-runtime-attestation.md`
- Upstream matches: none
- Evidence: Survives as docs/research and research/recovery artifacts.; No code-level equivalence is claimed from operational documents.
- Behavioral contracts: Operational records remain durable evidence and are not mistaken for code-level equivalence.

### `capability:session-title-safety` - test(session): pin literal-safe numbered-title resolution (#15)

- Changes: `d18dadb5f3b5`, `faf96ef221dd`, `69bee0838ae1`, `17b0d2c9135d`
- Merge events: `984eff15df1f`, `dcf8096413c7`
- Files: `docs/design/session-metadata-fts.md`, `docs/research/issue-38-numbered-title-literal-safety.md`, `hermes_state.py`, `hermes_state_common.py`, `tests/test_hermes_state.py`, `tests/test_session_metadata_cjk_fts.py`, `tests/test_session_metadata_fts.py`
- Upstream matches: none
- Evidence: Survives in hermes_state.py, hermes_state_common.py and title/metadata tests.; Frozen upstream escapes LIKE but does not enforce the fork's strict numeric suffix filter.
- Behavioral contracts: Escape SQL LIKE literals independently from Python literals.; Only strict ASCII #N suffixes are variants; %, _, \, #, CJK, and literal titles stay exact and safe.; The public resolution API and root/current exclusion semantics remain unchanged.

### `capability:cron-nul-safety` - fix(cron): harden lifecycle guard + terminal fallback against NUL-bearing paths (#76762)

- Changes: `d53e209b3b62`
- Merge events: (none)
- Files: `cron/lifecycle_guard.py`, `tools/terminal_tool.py`
- Upstream matches: none
- Evidence: Survives in cron/lifecycle_guard.py and tools/terminal_tool.py.; Frozen upstream contains the corresponding NUL/binary-safe guard and terminal fallback.
- Behavioral contracts: NUL-bearing input does not reach unsafe process/lifecycle calls.; Terminal fallback returns a clear failure rather than truncating at NUL.

Discovery completeness: **PASS** — 171 of 171 records emitted.
Capability accounting: **PASS** - explicit disposition mapping is required before this gate can pass.
