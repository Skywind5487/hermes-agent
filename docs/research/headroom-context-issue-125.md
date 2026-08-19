# Issue #125: Headroom Context re-scope against current plugin architecture

This is the targeted Phase-2 preflight for [issue #125](https://github.com/Skywind5487/hermes-agent/issues/125), with handoff back to parent [issue #111](https://github.com/Skywind5487/hermes-agent/issues/111). It re-checks the frozen Phase-1/1.5 `PORT` decision against current upstream architecture instead of treating the old snapshot as current truth.

## Evidence boundary

Final checked snapshots:

- Fork Phase-1.5 composition head: `5aa4f4e27ccf2169beb4fc1f1d1eeb655d13b548` (`codex/issue-106`, PR #108).
- Fork reconstruction base referenced by that composition: `fa5ed679cc6559c619038f327e6276f4b7e8d735` (`dev`).
- Current upstream `NousResearch/hermes-agent` main: `395c70d616f6426e990632ff8b57cf1e9499702f` (2026-08-19).
- Current Headroom upstream main examined: `b77d61291399976985f12adcd6014aba2f0275cf` (2026-08-18).
- Current fork implementation evidence: `fork/headroom-context` plus the older `dev` Headroom implementation.

The Phase-1 accounting and Phase-1.5 composition remain historical/frozen evidence. This note is the authoritative current-main preflight for Headroom Phase 2.

## Disposition

**`EXTERNALIZE` Headroom-specific production code. `DROP` the in-tree reconstruction from #111. No new Headroom-specific core seam is justified by current main.**

The historical semantic capability still has two independently useful slices — tool-result compression and optional retrieval — but both can now be implemented by a standalone third-party plugin using generic upstream-owned extension points. The split remains useful as an external-plugin design/test boundary, not as two fork production patches.

The smallest remaining fork-owned production behavior for #111 is therefore **zero**. If a future external Headroom adapter proves a missing host capability, that gap should become a separate product-agnostic core issue/PR rather than special-casing Headroom.

## 查到什麼

### 1. Current main already owns the generic host seams

**Verified.** Current upstream has all host extension points needed for the scoped tool-output compression/retrieval integration:

1. `transform_tool_result` is a general plugin hook. It receives the final model-bound tool result after `post_tool_call` and before the result is appended to conversation context; the first returned string replaces the result, and hook failure is fail-open. This is already the correct seam for deterministic tool-result compression.
2. General plugins can register model tools with `ctx.register_tool(...)`, so an external adapter can expose a bounded `headroom_retrieve` tool without core wiring.
3. User/project/pip plugins are discovered by the existing plugin system and third-party general plugins are explicitly opt-in via `plugins.enabled` in `~/.hermes/config.yaml`.
4. Context engines are also pluggable and current main now exposes request-only `select_context()` plus post-turn `on_turn_complete()`. `select_context()` can replace the message list for one provider request without mutating persisted history.

Primary sources:

- [`website/docs/user-guide/features/plugins.md`](https://github.com/NousResearch/hermes-agent/blob/395c70d616f6426e990632ff8b57cf1e9499702f/website/docs/user-guide/features/plugins.md)
- [`website/docs/developer-guide/context-engine-plugin.md`](https://github.com/NousResearch/hermes-agent/blob/395c70d616f6426e990632ff8b57cf1e9499702f/website/docs/developer-guide/context-engine-plugin.md)
- [`model_tools.py`](https://github.com/NousResearch/hermes-agent/blob/395c70d616f6426e990632ff8b57cf1e9499702f/model_tools.py)
- [`agent/conversation_loop.py`](https://github.com/NousResearch/hermes-agent/blob/395c70d616f6426e990632ff8b57cf1e9499702f/agent/conversation_loop.py)

**Inference from verified architecture.** #111 does not need a context-engine takeover merely to preserve the historical Headroom tool-result behavior. `transform_tool_result` is narrower and avoids replacing Hermes's built-in context compressor. `select_context()` should remain a future option only if a separately specified non-tool request-selection contract is proven necessary.

### 2. Current upstream policy now answers the placement question directly

**Verified.** Current [`CONTRIBUTING.md`](https://github.com/NousResearch/hermes-agent/blob/395c70d616f6426e990632ff8b57cf1e9499702f/CONTRIBUTING.md) has an explicit section, **“Third-Party Product Integrations: Ship as a Standalone Plugin.”** It says integrations with someone else's product/project do not land in the core repo; they should use the standalone plugin surface. If a capability is missing, the acceptable core change is a generic plugin-surface widening, never a product-specific core special case.

The same file documents the configuration boundary:

- `~/.hermes/config.yaml` — settings/behavior.
- `~/.hermes/.env` — API keys and secrets.

Therefore the current fork's behavioral `HERMES_HEADROOM_*` switches are not the right durable configuration contract for a reconstructed integration.

### 3. Prior art shows generic seams merged while vendor-specific implementations did not

**Verified status as of the evidence boundary:**

| Work | Status | What it proves for #125 |
|---|---|---|
| upstream [#40322](https://github.com/NousResearch/hermes-agent/pull/40322) — Headroom Phase-1 tool-output plugin | **closed, unmerged** | A nearly direct in-tree Headroom plugin was proposed on `transform_tool_result`; it is not upstream authority. No maintainer comment states a closure reason, so closure must not be over-interpreted. |
| upstream [#64656](https://github.com/NousResearch/hermes-agent/pull/64656) — Compresr context + tool-output compression | **closed, unmerged**, `sweeper:not-planned` | Prior art independently split vendor compression into a context engine and a `transform_tool_result` plugin; upstream did not merge the vendor implementation. |
| upstream [#51226](https://github.com/NousResearch/hermes-agent/pull/51226) | **closed, unmerged** | Proposed generic `select_context()` + `on_turn_complete()` and consolidated earlier competing seams. |
| upstream [#70458](https://github.com/NousResearch/hermes-agent/pull/70458) | **merged 2026-07-24** | Maintainer salvage of #51226; the generic request-selection/turn-observation seam is current authority. |
| upstream [#29454](https://github.com/NousResearch/hermes-agent/pull/29454) — tool-result compaction | **closed, unmerged** | Prior art for raw-result persistence + preview/recovery; not authority. |
| upstream [#89582](https://github.com/NousResearch/hermes-agent/pull/89582) | **open, unmerged** | Current evidence for a possible future generic session-bound opaque spill capability; it cannot be designed against as if merged. |
| upstream [#86168](https://github.com/NousResearch/hermes-agent/pull/86168) | **open, unmerged** | Current security-hardening proposal for `select_context()` system-prefix validation. |
| upstream [#88835](https://github.com/NousResearch/hermes-agent/pull/88835) | **closed, unmerged**, duplicate | Same security area was split/deduplicated rather than independently landed. |

The high-confidence architectural pattern is not “closed vendor PR means bad feature”; it is narrower: **merged generic extension surface is authority, vendor-specific integration remains external unless policy changes.**

### 4. Current host persistence does not make Headroom CCR automatically safe or redundant

**Verified.** Current upstream [`tools/tool_result_storage.py`](https://github.com/NousResearch/hermes-agent/blob/395c70d616f6426e990632ff8b57cf1e9499702f/tools/tool_result_storage.py) already persists oversized tool results to Hermes-managed spillover and replaces them with previews/references. However, the normal execution path applies plugin `transform_tool_result` in `model_tools.py` before `agent/tool_executor.py` runs `maybe_persist_tool_result(...)` on the returned value.

**Inference.** If Headroom first compresses an oversized raw result below the host spill threshold, Hermes's generic spill layer sees only the compressed string. It therefore cannot be treated as a guaranteed recovery store for the raw pre-compression value. A Headroom adapter that promises retrieval still needs an explicit recovery contract, unless a future generic capability such as #89582 lands with suitable ordering/API.

### 5. The fork's current Phase-2 implementation still violates the requested privacy/configuration contract

**Verified.** `fork/headroom-context/plugins/headroom/__init__.py` correctly uses the generic `transform_tool_result` hook and `ctx.register_tool` for retrieval, but two problems remain:

1. Behavior is controlled by `HERMES_HEADROOM_ENABLED`, `HERMES_HEADROOM_DISABLE`, `HERMES_HEADROOM_KILL_SWITCH`, `HERMES_HEADROOM_ALLOWLIST`, `HERMES_HEADROOM_MIN_CONTENT_LENGTH`, and `HERMES_HEADROOM_RETRIEVE_MAX_CHARS` rather than a `config.yaml` settings contract.
2. The redaction helper catches any redaction exception and substitutes the original text. The Phase-2 retrieval path then treats that return as the “redacted original” and stores it. A redaction exception can therefore cause raw input to enter the plugin's recovery store — exactly the fail-open privacy path #125 forbids.

The old implementation on `dev` is even more direct: retrieval storage receives the raw `result` as its `original` field.

Primary fork source: [`fork/headroom-context/plugins/headroom/__init__.py`](https://github.com/Skywind5487/hermes-agent/blob/fork/headroom-context/plugins/headroom/__init__.py).

### 6. Current Headroom itself has an explicit way to separate compression from CCR persistence

**Verified.** Current Headroom describes CCR as “compress → store original → retrieve.” Its current public compression module also exposes `UniversalCompressorConfig.ccr_enabled`, defaulting to `True`, and only calls its CCR store when that flag is enabled. `ContentRouterConfig` likewise has a `ccr_enabled` control for the SmartCrusher CCR path.

Primary sources:

- [`wiki/ARCHITECTURE.md`](https://github.com/headroomlabs-ai/headroom/blob/b77d61291399976985f12adcd6014aba2f0275cf/wiki/ARCHITECTURE.md)
- [`headroom/compression/universal.py`](https://github.com/headroomlabs-ai/headroom/blob/b77d61291399976985f12adcd6014aba2f0275cf/headroom/compression/universal.py)
- [`headroom/transforms/content_router.py`](https://github.com/headroomlabs-ai/headroom/blob/b77d61291399976985f12adcd6014aba2f0275cf/headroom/transforms/content_router.py)

**Design consequence.** A privacy-safe Hermes adapter must not rely on Headroom's default raw-original CCR persistence while also claiming that Hermes redaction gates persistence. The adapter should choose a public Headroom compression path with raw CCR persistence disabled, then separately store only a successfully redacted recovery value under the adapter's own contract. Any richer ContentRouter path must be pinned and regression-tested to prove that no secondary raw store remains active.

## Exact residual external-plugin contract

The following is the smallest independently testable behavior worth preserving. It belongs in a standalone Headroom plugin, not this repo's production tree.

### Compression slice

1. Plugin is explicitly enabled through Hermes plugin configuration.
2. Only explicitly allowlisted, model-bound **string** tool results are candidates.
3. Results below the configured minimum size are byte-identical passthrough.
4. Compression uses a documented/public Headroom API; no mutation of Headroom private attributes such as `_kompress`, `_kompress_max_tokens`, or `_text_crusher_enabled` is part of the contract.
5. A successful compression must return a usable string and report enough bounded metadata/warning text for the model to know content was compressed.
6. Headroom import/dependency/compression failure returns the original usable result and emits an observable warning; it does not manufacture a retrieval handle.
7. Compression remains valid when retrieval is disabled or unavailable.

### Optional retrieval slice

1. Retrieval is enabled only when compression is enabled.
2. A handle exists only after compression succeeds **and** a recovery value was safely persisted.
3. Before any Headroom/plugin-owned recovery persistence, deterministic redaction must complete successfully.
4. If redaction raises, returns an invalid value, or storage fails: **do not persist, do not mint a handle, do not expose retrieval for that result.** This is fail-closed persistence while compression itself may remain usable.
5. The persisted value is the redacted recovery text, never the raw pre-redaction result.
6. Handles are opaque, bounded to the originating session (or equivalently strong capability scope), expire/evict on a bounded policy, and retrieval pages have a hard maximum size.
7. Unknown/expired/cross-session handles fail without leaking whether unrelated data exists.

“Persistence” above means the additional recovery/CCR persistence introduced by the Headroom integration. It does not redefine Hermes's existing transcript-retention policy; the privacy invariant is that a redaction-exception path must never call a Headroom/plugin recovery store with raw data.

### Configuration contract

Behavioral settings belong in `~/.hermes/config.yaml`, for example under a plugin-owned `headroom` block (exact key naming belongs to the standalone plugin):

- enabled/kill-switch semantics (prefer the existing `plugins.enabled` gate rather than duplicating it unless a runtime kill switch is independently justified);
- allowlisted tools;
- minimum content size;
- retrieval enablement;
- retrieval page limit / TTL / capacity;
- compression strategy settings.

`.env` is reserved for secrets/API credentials required by an optional remote Headroom backend. **No `HERMES_HEADROOM_*` environment variable is part of the new behavioral contract.**

## RED tests / validation contract

These are the tests an external implementation should make green. Several are intentionally RED against the current fork implementation.

1. **Redaction exception → zero persistence:** inject a redactor that raises; assert store call count is zero, no handle/marker is emitted, and raw bytes are absent from recovery storage.
2. **Invalid redaction result → zero persistence:** `None`/non-string/otherwise rejected redaction output cannot become a stored original.
3. **Exact stored value:** successful redaction persists exactly the redacted value; a sentinel secret present only in raw input is absent from storage and retrieval.
4. **Headroom CCR disabled under adapter ownership:** the chosen Headroom compression object is configured so Headroom itself does not persist the raw original; prove this with a fake/temporary CCR backend where supported.
5. **Compression without retrieval:** retrieval disabled/store unavailable still yields valid compressed output when compression succeeds.
6. **Compression failure passthrough:** dependency/compressor exception returns the original result unchanged, emits no handle, and records an observable warning.
7. **Threshold/allowlist passthrough:** below-threshold or non-allowlisted tools are byte-identical.
8. **Bounded retrieval:** offset/limit are bounded; configured hard maximum cannot be exceeded.
9. **Scope/expiry:** cross-session and expired handles cannot retrieve data.
10. **Config authority:** changing `config.yaml` changes behavior; setting legacy `HERMES_HEADROOM_*` variables does not.
11. **Public API boundary:** adapter tests/mock points target public Headroom constructors/methods only; no private-attribute mutation is required.
12. **No-core regression:** with the external plugin absent/disabled, current Hermes tool and context paths remain byte-identical/no-op by construction.

## Non-goals

- Do not add `plugins/headroom/` back to the Hermes core repo.
- Do not add a Headroom-specific core hook, tool dispatcher, persistence layer, or configuration parser.
- Do not replace Hermes's built-in context compressor merely to compress tool outputs.
- Do not make open upstream PR #89582 a dependency until it is merged into main.
- Do not reimplement Headroom's compressor algorithms in the fork.
- Do not preserve the current fork's private-attribute integration with ContentRouter.
- Do not claim that closing #40322 proves a maintainer rejection rationale that is not recorded.

## 查不到什麼

1. **Why upstream #40322 was closed.** The PR is closed/unmerged and its issue-comment thread is empty; no primary-source maintainer explanation was found.
2. **Whether #89582 or #86168 will merge, and in what final shape.** Both are open and therefore evidence only.
3. **A proof that every current/future ContentRouter compressor path honors one flag as a complete “never store raw anywhere” switch.** The source exposes CCR controls, but the router has multiple compressor strategies and side effects. A standalone adapter must pin the Headroom version/API it uses and prove the no-raw-store property with an integration test rather than extrapolate from one config field.
4. **The final repository/package name for the standalone Headroom adapter.** Placement is settled; packaging/name is not part of #125.

## 為什麼查不到

- #40322 has no recorded maintainer closure comment, so any motive would be speculation.
- Open PRs have no authoritative merged contract yet; current `main` remains the only source of truth.
- Headroom is a fast-moving external project with multiple compression paths. Static inspection can prove the specific current code paths cited above, not a universal future guarantee.
- #125 is a research/re-scope issue, not the standalone plugin implementation ticket.

## 研究者自我檢驗

- I initially carried the Phase-1 label “`FORK_ONLY / PORT`” only as historical evidence. After checking current upstream, that label is **not** retained as the current disposition.
- I separately checked PR state and `merged_at`; “closed” is never treated as “merged.”
- I treated #70458 as authority because it is merged, while #51226/#89582/#86168 remain provenance/evidence only.
- I did not infer a maintainer rationale for #40322 from its closure.
- I checked the actual upstream code/docs and actual frozen research notes, not just repository navigation/index pages.
- Upstream `main` moved during the research from `0b879298...` to `395c70d6...`; the final evidence boundary was refreshed. The intervening final commit is a JS formatting merge, so the Python/plugin sources used for this conclusion remain on the parent, but the report pins the final observed main SHA.
- The main correction to the earlier plan is architectural, not a claim that the frozen Phase-1 work was wrong: the upstream surface and contribution policy changed after the frozen snapshot.

## 結論與下一步

### Verdict for #125

**`EXTERNALIZE`.** Current upstream already provides the generic plugin seams; current contribution policy explicitly places third-party product integrations outside core. There is no Headroom-specific production patch left to reconstruct in this fork.

### Handoff for #111

1. Re-scope `repair:headroom-context` from an in-tree Phase-2 implementation unit to an **external-integration follow-up**.
2. Do not merge/reconstruct the existing `fork/headroom-context` production code into `dev`.
3. Preserve the two semantic contracts — compression and optional retrieval — as external-plugin acceptance tests.
4. If the external plugin implementation discovers a genuinely missing Hermes host primitive, open a **new generic core issue** with a product-independent RED test before touching core.
5. Track #89582 only as possible future simplification of retrieval storage; do not block the external plugin design on it.

This supersedes only the current Phase-2 Headroom disposition. The frozen Phase-1 archaeology and Phase-1.5 composition remain valid records of what was known at their pinned refs.