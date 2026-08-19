# Browser Runtime reconstruction preflight — issue #115

Date: 2026-08-19

## Authority / pinned refs

- Phase-2 ticket: #115 (`repair:browser-runtime`).
- Composition authority: PR #108 at `5aa4f4e27ccf2169beb4fc1f1d1eeb655d13b548` — `line:browser-timeout-cleanup` is an independent vertical feature.
- Phase-1 accounting: PR #104 manifest head `f81cd921a89516d855b5b69906ce99e6351bc741`.
- Fork evidence: historical `dev` capability line, including commits `a2450695`, `b42bc25e`, and `cfb2636a`.
- Final reconstruction substrate: current fork `main` / upstream-aligned base `243352e7b8bddc9f33eba1b6506810f8dd88beaa`.
- PR #108 originally pinned upstream `56526bc0d36522ab7a87ee0056f70e3847d2f0e6`; `tools/browser_tool.py` remains unchanged through the final refreshed base (blob `544a06d51277098472436ce4e049e293078b52f3`).

## Upstream prior art / current authority

| Upstream work | State at reconstruction | Use here |
|---|---|---|
| #86755 — resource-leak salvage, including periodic browser orphan reaping from #82145 | **merged** | Current authority. Preserve the periodic orphan sweep, idle-age escape hatch, and `_verify_reapable_browser_daemon()` identity/session binding checks. |
| #82145 — periodic browser orphan reaper / live-owner idle escape hatch | closed, unmerged | Its relevant browser behavior was salvaged into merged #86755. Use #86755/current main as authority, not this branch shape. |
| #68152 — reap daemon directly on command timeout | closed, unmerged | Superseded explicitly by #68220. Provenance/design evidence only. |
| #68220 — verified daemon tree kill on command timeout | open, unmerged | Strong prior art for the same leak family. Evidence only; it is not current-main policy. |
| #50667 — verify PID identity before normal browser session cleanup kill | open, unmerged | Adjacent hardening only. Do not silently change normal-cleanup policy in #115. |
| #64383 — broader ownership/PID-reuse-safe orphan recovery | open, unmerged | Broader hardening evidence only; do not pull its unmerged architecture into this bounded repair. |

## Fork residual contract

Phase-1 provenance identifies one fork-owned capability, `capability:browser-timeout-cleanup`:

1. Timeout-triggered daemon/session teardown is opt-in through `browser.terminate_daemon_on_timeout`; the default is `false`.
2. When disabled, timeout keeps current-upstream behavior: fail the command but leave the browser session for normal inactivity/orphan lifecycle cleanup.
3. When enabled for a local session, timeout cleanup covers the owning session and any `::local` sidecar, but destructive cleanup requires verified daemon/session ownership.
4. Missing, malformed, unverified, or unsuccessfully terminated PIDs preserve the socket directory and in-memory session tracking as recovery evidence for normal lifecycle cleanup and the merged orphan reaper.
5. Cleanup/termination failures are best-effort and cannot mask the original timeout result.
6. Cloud/CDP timeout behavior and ordinary non-timeout browser lifecycle remain unchanged.

The merged periodic orphan reaper does **not** supersede this contract. It is delayed recovery for orphaned/untracked daemons; the fork option is an explicit immediate-on-timeout policy for deployments that choose it. Current upstream still has no `terminate_daemon_on_timeout` configuration seam.

## Reconstruction decision

Port the accepted behavior onto the unchanged current-upstream `tools/browser_tool.py` seam rather than replaying historical merge topology.

- Keep current upstream orphan-reaper, owner PID, idle timeout, and normal cleanup behavior intact.
- Preserve `browser.terminate_daemon_on_timeout=false` as the default and strict no-immediate-teardown path.
- Preserve local-only scope; do not change cloud/CDP timeout ownership.
- Preserve sidecar ownership handling.
- **Do not** replay historical finally-clean-all behavior when daemon ownership is ambiguous. Merged #86755 makes the socket directory — including entry mtimes — recovery evidence.
- Share PID/socket teardown, session-state removal, and last-active-binding pruning primitives with normal browser cleanup so ownership-sensitive logic has one implementation instead of parallel sources of truth.
- Do not import #68220/#50667/#64383 wholesale: they remain unmerged and would expand this ticket into a different lifecycle/security policy.

## Acceptance mapping

Tests for this merge unit must prove:

- default/config-false timeout returns the timeout failure without daemon/session teardown;
- config-true local timeout terminates and removes runtime state only after ownership is verified;
- `::local` sidecars follow current ownership semantics;
- missing/malformed/unverified PID and termination failures preserve socket + session recovery metadata;
- verified ownership plus successful termination removes runtime state;
- cloud/CDP timeout does not take the local daemon-kill path;
- the original timeout error remains observable even if cleanup fails;
- ordinary successful/non-timeout browser behavior remains unchanged.

## Review correction — 2026-08-19

The first reconstruction incorrectly treated ambiguous ownership as cleanup success: it refused to signal an unverified PID but still removed the socket directory and in-memory session metadata. That erased evidence used by the merged #86755 orphan-recovery path, including socket-entry mtimes.

Corrected invariant:

> verified ownership + successful termination → destructive cleanup; ambiguous ownership or failed termination → preserve recovery metadata.

The correction also factors PID/socket teardown and session-state/binding cleanup into shared helpers used by timeout and normal cleanup. Timeout supplies the stricter `require_verified_ownership=True` policy; normal cleanup retains its established best-effort behavior. This removes the duplicated security-sensitive ownership implementation without importing unmerged upstream policy.

## Wayfinder gate resolution — 2026-08-19 (late)

Issue #115 received a Wayfinder intent-topology gate and an implementation
handoff (both 2026-08-19). This section records how the branch satisfies them.

### Upstream refresh (preflight)

- Current fork `main` / branch base: `243352e7b8bddc9f33eba1b6506810f8dd88beaa`.
- Current upstream `main` refreshed: `f82f2dbabd9e66b714f2b4f8a40447fe0c13e732`
  (Wayfinder observed `a6bada232c4889fec1a2b50664f859d5335bc542`; upstream has since advanced).
- Upstream changes since the base touch `tools/process_registry.py`,
  `hermes_cli/config.py`, and `tests/conftest.py`, but **not**
  `tools/browser_tool.py` (the branch's only production seam). No conflict and no
  rebase requirement for the browser feature.
- No `base/browser-runtime-115` branch exists; PR #121 targets fork `main`
  directly, which matches the ticket's branch boundary.

### Handoff directives → status

| Directive | Status |
|---|---|
| Fix the #121 browser regressions around session / last-active pruning under the cleanup seam | Resolved on `1cab18e4a` — `cleanup_browser` restores the established bare-task (unconditional drop) vs `::local` (owner-only drop) last-active binding contract. |
| Do not weaken the recovery-safe ownership contract | Preserved — timeout cleanup still requires `require_verified_ownership=True`; ambiguity/failure keeps socket dir + session metadata + last-active binding for orphan recovery. |
| Unrelated `relay_shared_metrics` lock flakes are not part of this feature | Not addressed here; no longer present on the final head (all 12 CI slices green). |
| Preserve shared teardown mechanics only where policy stays explicit | Timeout uses the strict policy; normal cleanup keeps best-effort semantics. |
| Do not create a child `line:` PR for the regression | No new child line. |
| Classify commits by behavior intents; tests belong with the behavior | `8a0da17e5` (research/evidence), `97457847a` (recovery-safe ownership + tests), `1cab18e4a` (shared-teardown last-active correctness + tests). |

### Final invariant (unchanged)

> verified ownership + successful termination → destructive cleanup; ambiguity or
> termination failure → preserve recovery evidence.

Verification on `1cab18e4a`: browser acceptance + hybrid-routing + orphan-reaper +
timeout/open regressions pass (58 local tests); CI on PR #121 is fully green.

## Review round 2 — 2026-08-19 (late)

`/code-review` on PR #121 returned 2 spec blockers + 3 standards findings
(addressed on `6f7312464`):

| Finding | Fix |
|---|---|
| Spec: `_terminate_host_pid` treated "no exception" as success (Windows `taskkill` return code unchecked; POSIX `AccessDenied`/`OSError` swallowed); destructive cleanup deleted evidence for a still-alive daemon | `_terminate_host_pid` now returns confirmed-dead evidence (taskkill return code + bounded `_pid_gone_within` liveness poll on Windows; whole-tree confirmation after SIGTERM/SIGKILL on POSIX). The timeout teardown path only removes socket dir + session metadata when termination is confirmed; `False` preserves recovery evidence. |
| Spec: process-global `_cached_terminate_daemon` leaked the opt-in policy across multiplexed profiles | `_should_terminate_daemon_on_timeout` resolves context-local config per call when `get_hermes_home_override()` is set (mirrors `_allow_private_urls`); single-profile path still caches. Dual `true→false→true` regression test added. |
| Standards: new `browser.terminate_daemon_on_timeout` missing from `DEFAULT_CONFIG` | Added to `hermes_cli/config_defaults.py` browser section (deep-merge picks it up; no version bump needed). |
| Standards: broad `except Exception` logs lacked tracebacks | `exc_info=True` added on both timeout-cleanup log sites. |
| Standards: acceptance tests mocked the termination helper, hiding the real Windows/POSIX failure mode | Added contract tests that exercise `_terminate_host_pid`'s real Windows taskkill-failure and POSIX confirmed/survivor paths (stubbed `psutil` module so the hermetic venv needs no psutil), plus unconfirmed-termination-preserves / confirmed-termination-cleans teardown tests. |

Verification on `6f7312464`: 23 acceptance tests (hermetic wrapper) + browser
lifecycle set green; ruff clean on all changed files; the only failing
`test_process_registry.py` cases are pre-existing environment/live-system-guard
failures, identical before and after this change.