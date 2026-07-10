"""Metrics protocol + NoOp default. CONTRACT v0.8 #8–#10.

Three methods. No timers (use a histogram with a latency value), no
summaries (Prometheus-specific), no custom types. Adding a metric is
a public API change — the standard metric list below is the operator
contract.

Cardinality rule (locked, CONTRACT v0.8 #C4):

  run_id MAY appear as a tag on gauges of active state (cardinality
  is bounded by the number of concurrently active runs).
  run_id MUST NOT appear as a tag on counters or histograms.

The ``METRIC_NAMES`` table enforces this at import time — any standard
metric whose tag set violates the rule fails the test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


# ---- the protocol --------------------------------------------------------


@runtime_checkable
class Metrics(Protocol):
    """Three methods, all best-effort, all non-throwing.

    Implementations MUST tolerate unknown metric names. Unknown tag keys
    are also accepted; cardinality discipline is the caller's job. Methods
    may be called concurrently by independent runtime and sink workers.
    """

    def counter(self, name: str, tags: dict[str, str], value: float = 1.0) -> None: ...
    def histogram(self, name: str, tags: dict[str, str], value: float) -> None: ...
    def gauge(self, name: str, tags: dict[str, str], value: float) -> None: ...


# ---- the no-op default ---------------------------------------------------


class NoOpMetrics:
    """Default Metrics implementation. Does nothing.

    Three method bodies, each a single ``return``. The runtime is fully
    functional with NoOpMetrics. Profile-checked for zero allocation
    pressure under steady load.
    """

    __slots__ = ()

    def counter(self, name: str, tags: dict[str, str], value: float = 1.0) -> None:
        return

    def histogram(self, name: str, tags: dict[str, str], value: float) -> None:
        return

    def gauge(self, name: str, tags: dict[str, str], value: float) -> None:
        return


# ---- the documented metric table ----------------------------------------

# Type tags: c=counter, h=histogram, g=gauge.
# Source of truth for the standard metric list. Conformance tests pin
# this table; adding/removing a row is a public API change.


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: str  # "counter" | "histogram" | "gauge"
    tags: tuple[str, ...]
    description: str


METRIC_NAMES: tuple[MetricSpec, ...] = (
    MetricSpec(
        "activegraph_events_emitted_total",
        "counter",
        ("event_type",),
        "Every event that lands in the graph's event log.",
    ),
    MetricSpec(
        "activegraph_behaviors_invoked_total",
        "counter",
        ("behavior",),
        "Each behavior invocation. Increments before the handler runs.",
    ),
    MetricSpec(
        "activegraph_behaviors_failed_total",
        "counter",
        ("behavior", "reason"),
        "Behavior invocations that produced a behavior.failed event.",
    ),
    MetricSpec(
        "activegraph_behaviors_duration_seconds",
        "histogram",
        ("behavior",),
        "Wall-clock duration of a behavior invocation (handler only).",
    ),
    MetricSpec(
        "activegraph_llm_calls_total",
        "counter",
        ("model",),
        "Every llm.requested event (cached and non-cached).",
    ),
    MetricSpec(
        "activegraph_llm_cache_hits_total",
        "counter",
        ("model",),
        "LLM calls served from the recorded-response cache.",
    ),
    MetricSpec(
        "activegraph_llm_failed_total",
        "counter",
        ("model", "reason"),
        "LLM calls that failed before producing a usable response.",
    ),
    MetricSpec(
        "activegraph_llm_tokens_in",
        "histogram",
        ("model",),
        "Input tokens reported by the provider per llm.responded.",
    ),
    MetricSpec(
        "activegraph_llm_tokens_out",
        "histogram",
        ("model",),
        "Output tokens reported by the provider per llm.responded.",
    ),
    MetricSpec(
        "activegraph_llm_cost_usd",
        "histogram",
        ("model",),
        "Per-call cost in USD as reported by the provider.",
    ),
    MetricSpec(
        "activegraph_tools_calls_total",
        "counter",
        ("tool",),
        "Every tool.requested event (cached and non-cached).",
    ),
    MetricSpec(
        "activegraph_tools_cache_hits_total",
        "counter",
        ("tool",),
        "Tool calls served from the recorded-response cache.",
    ),
    MetricSpec(
        "activegraph_tools_failed_total",
        "counter",
        ("tool", "reason"),
        "Tool calls that produced a tool.failed event.",
    ),
    MetricSpec(
        "activegraph_tools_duration_seconds",
        "histogram",
        ("tool",),
        "Wall-clock duration of a tool invocation.",
    ),
    MetricSpec(
        "activegraph_queue_depth",
        "gauge",
        (),
        "Current depth of the runtime's event queue.",
    ),
    MetricSpec(
        "activegraph_sink_queue_depth",
        "gauge",
        ("sink", "run_id"),
        "Waiting deliveries in one attached sink's bounded queue.",
    ),
    MetricSpec(
        "activegraph_sink_events_delivered_total",
        "counter",
        ("sink",),
        "Events successfully handled by an attached sink.",
    ),
    MetricSpec(
        "activegraph_sink_events_dropped_total",
        "counter",
        ("sink", "reason"),
        "Sink deliveries rejected or evicted under declared policy.",
    ),
    MetricSpec(
        "activegraph_sink_errors_total",
        "counter",
        ("sink", "operation"),
        "Adapter lifecycle or on_event failures isolated by sink workers.",
    ),
    MetricSpec(
        "activegraph_budget_cost_remaining_usd",
        "gauge",
        ("run_id",),
        "Remaining cost budget for an active run, USD.",
    ),
    MetricSpec(
        "activegraph_budget_events_remaining",
        "gauge",
        ("run_id",),
        "Remaining event budget for an active run.",
    ),
    MetricSpec(
        "activegraph_patterns_evaluated_total",
        "counter",
        (),
        "Pattern evaluations across all behaviors.",
    ),
    MetricSpec(
        "activegraph_patterns_evaluation_duration_seconds",
        "histogram",
        (),
        "Per-evaluation duration of a pattern subscription.",
    ),
    MetricSpec(
        "activegraph_replay_divergence_detected_total",
        "counter",
        ("reason",),
        "Replay-strict re-runs that diverged from the recorded log.",
    ),
)

METRIC_BY_NAME: dict[str, MetricSpec] = {m.name: m for m in METRIC_NAMES}


def validate_cardinality_rule(metrics: tuple[MetricSpec, ...] = METRIC_NAMES) -> None:
    """Enforce CONTRACT v0.8 #C4: run_id only appears on gauges.

    Called from the conformance test. Raises if any counter or histogram
    declares run_id as a tag.
    """
    for spec in metrics:
        if "run_id" in spec.tags and spec.kind != "gauge":
            raise AssertionError(
                f"metric {spec.name!r} ({spec.kind}) lists run_id as a tag — "
                f"forbidden by the cardinality rule (run_id is gauge-only)."
            )


# Validate at import time so any in-tree edit fails loud.
validate_cardinality_rule()
