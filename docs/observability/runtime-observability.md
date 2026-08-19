# Runtime observability

Runtime observability is an **optional residual diagnostic extension** rebuilt
from the historical fork capability after refreshing current upstream ownership.
It does not restore the old telemetry stack wholesale.

The important reconstruction result is that the five historical domains no
longer all belong to one fork implementation:

| Domain | Current authority | #114 action |
| --- | --- | --- |
| lifecycle | merged gateway monitoring + merged Relay shared metrics | reuse; do not duplicate |
| tool | merged Relay shared metrics | reuse; do not duplicate |
| stream | no equivalent bounded operator event | add residual diagnostic |
| SQLite/session persistence | current runtime policy/logging, no equivalent bounded operator event | add residual diagnostic only; do not change retry policy |
| delivery | current retry/fallback runtime, no equivalent bounded operator event | add residual diagnostic only |

This split matters because `agent.monitoring` explicitly excludes run/model/tool
trajectory capture; that plane belongs to Relay. Re-emitting session and tool
trajectories through gateway monitoring would create a second owner rather than
preserve the current architecture.

## Enablement, lifetime, and sink

The residual producer is off by default:

```yaml
monitoring:
  runtime_observability:
    enabled: true
```

**Lifetime contract:** once the Hermes process imports `hermes_cli`, the residual
observation boundary is installed for the rest of that process lifetime. It is
installed as a dormant logging handler: installation performs no configuration
I/O, network I/O, disk I/O, or event emission. Exact matched runtime facts check
`monitoring.runtime_observability.enabled` at emission time.

This deliberately does **not** wait for `on_session_start`. Both the gateway
(`hermes_cli.config`) and SessionDB (`hermes_cli.sqlite_runtime`) enter through
the `hermes_cli` package before the residual runtime boundaries are constructed,
so startup SQLite/reconnect, stream, and delivery facts are observable before
the first session exists.

That switch only enables the residual producer. Export remains controlled by
the existing gateway monitoring configuration. For OTLP export, operators use
the existing sink, for example:

```yaml
monitoring:
  runtime_observability:
    enabled: true
  gateway_health_export:
    enabled: true
  export:
    otlp:
      enabled: true
      endpoint: http://collector-host:4318/v1/traces
      headers_env: {}
```

The producer can become active before the OTLP subscriber is attached during
gateway startup. In that interval an already-opted-in residual fact uses the
existing monitoring emitter's explicit `emit_buffered()` path. That one event is
retained in the same bounded queue without turning on ordinary `emit()` calls
from unrelated monitoring producers. The dispatcher does not start while the subscriber set is empty, and
`subscribe()` never starts it either: a pre-buffered residual must survive the
gateway's two-sink assembly (span streamer attaches first, diagnostic streamer
second) rather than being dequeued into a partial fan-out and lost. The
ordinary initial snapshot emitted after the full fan-out attaches starts
dispatch, which drains the buffered residual through every subscriber. The
queue keeps its existing 10,000-event bound and oldest-drop policy. There is no
second queue, local telemetry database, or persistence layer. A flush is a
no-op whenever nothing can drain the queue — no subscriber, or a subscriber
attached before the dispatcher started — so it never adds an artificial
shutdown delay.

There is no built-in destination and no new local telemetry store. If runtime
observability is disabled, its installed handler is dormant. If the queue is
full or an exporter/collector fails, the observed Hermes operation keeps its
normal result. Diagnostics are never a runtime dependency.

## Stable event subset

Only the **residual** domains add event names here. Events use the existing
`GatewayDiagnosticEvent` envelope.

| Domain | Stable residual facts |
| --- | --- |
| stream | `runtime.stream.failed` |
| SQLite/session persistence | `runtime.sqlite.persistence_failed`, `runtime.sqlite.connection_recovered`, `runtime.sqlite.connection_recovery_failed` |
| delivery | `runtime.delivery.retry_succeeded`, `runtime.delivery.retries_exhausted`, `runtime.delivery.fallback_failed` |

For `runtime.sqlite.persistence_failed`, the optional `error_code` is restricted
to the current coarse persistence vocabulary:

`locked`, `compression`, `compression_closed`, `turn_lease`, `corrupt`, `disk`,
or `unknown`.

Those values reuse the runtime's existing `classify_persistence_error()`
semantics. The set is frozen in this feature: a future upstream classifier
bucket maps to `unknown` until the telemetry contract is deliberately reviewed.

The omission of high-volume success events is intentional. This extension is
not an execution trace. Stream success, every SQLite write, each retry attempt,
and every ordinary delivery are implementation noise for this contract.

Lifecycle and tool remain deliberately absent from this residual event list:
current upstream already owns those signals. Residual diagnostics no longer
consume any lifecycle hook merely to bootstrap themselves.

## Why a static-log adapter is acceptable here

Stream, persistence, and delivery do not currently expose a stable lifecycle
hook at the exact residual failure/terminal boundaries. Adding new hooks to
three large hot paths solely for #114 would create a wider behavioral surface.

Instead, the residual adapter recognizes only exact, code-owned **static log
templates** that already represent those domain facts, then projects the fixed
fact name. It matches `LogRecord.msg`, never fuzzy-matches rendered text, and
never calls `LogRecord.getMessage()`.

For stream and delivery it does not inspect interpolation arguments at all. For
the one SQLite persistence-failure template, it passes only the exception
object to the existing bounded persistence classifier and exports only the
closed cause bucket above; the stage, exception text, and other arguments are
never exported.

This is deliberately fail-closed but carries a maintenance cost: an upstream
wording-only change to one mapped log template will silently remove that
structured fact until the explicit table is refreshed. The table is therefore
a reviewed compatibility surface, not a fuzzy parser. This trade-off is kept
narrow here rather than pretending the current runtime already exposes stable
structured hooks at those three boundaries.

## Redaction / privacy contract

Residual runtime events may contain only the existing diagnostic envelope plus
the closed event name, subsystem, fixed error class, optional bounded SQLite
error code, and fixed severity. They must not contain:

- prompts, responses, streamed text, message content, or previews;
- tool names, tool arguments, tool results, or raw tool/provider errors;
- session, chat, message, turn, task, request, span, or tool-call identifiers;
- filesystem paths;
- provider raw responses;
- arbitrary exception class names or exception strings.

Values substituted into stream/delivery log `%s` / `%d` placeholders remain on
the local runtime log path and never enter the structured event. SQLite failure
text is used only inside the already-existing coarse classifier; only its
allowlisted bucket can cross the event boundary. Tests include sensitive paths,
provider text, platform identifiers, and objects that raise from `__str__` on
stream/delivery paths to pin this boundary.

The existing OTLP monitoring exporter applies its own fixed allowlist/redaction
policy again at the sink boundary. Producer-side tests remain mandatory so
privacy does not depend on one downstream filter.

## Failure isolation

There are four fail-open boundaries:

1. `hermes_cli` process bootstrap catches installation/import failures;
2. the dormant log handler catches normalization/producer failures;
3. the monitoring emitter remains non-blocking and bounded, including the explicit pre-subscriber residual path;
4. monitoring subscribers/exporters remain fail-isolated from the runtime.

Telemetry must never change a tool outcome, session result, stream/delivery
result, SQLite retry policy, startup result, or exception propagation that the
runtime already owns.

## Explicit non-goals

This feature does **not** restore the historical fork telemetry implementation
wholesale. In particular it does not add:

- `/proc`, cgroup, disk, kernel, RSS/PSS, or resource-probe telemetry;
- SQLite progress-handler/native-owner instrumentation or per-write counters;
- per-token/per-delta stream telemetry;
- raw lifecycle correlation IDs;
- a local telemetry SQLite store;
- another OpenTelemetry shim/exporter;
- duplicate lifecycle/tool trajectories already owned upstream;
- compression no-op/session-boundary behavior;
- SQLite write-contention retry/backoff behavior.

Compression boundary semantics and SQLite contention policy remain owned by
their separate feature lines. Runtime observability may report residual facts
from persistence but cannot gate or alter those policies.

## Prior-art disposition used for this reconstruction

The implementation is reconstructed against current upstream behavior rather
than replaying historical code:

- **merged/current sink authority:** gateway monitoring / OTLP diagnostics;
- **merged/current lifecycle and tool authority:** Relay shared metrics,
  including bounded tool aggregation;
- **merged after revert/rework:** the current Relay landing is the restored
  implementation, not its reverted first landing;
- **closed/unmerged evidence only:** monolithic OpenTelemetry and local-SQLite
  telemetry proposals;
- **open/unmerged evidence only:** local timeline/delivery proposals;
- **historical fork evidence only:** the old telemetry commit identifies useful
  diagnostic domains, but its resource probes, raw identifiers, SQLite native
  instrumentation, and unrelated compression behavior are not restored.
