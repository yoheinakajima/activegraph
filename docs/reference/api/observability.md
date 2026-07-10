# Observability

Accepted-event sinks, the metrics protocol, and shipped backends. For
the conceptual model see the
[Operating in production](../../guides/operating-in-production.md)
guide.

## Accepted-event sinks

::: activegraph.EventSink

::: activegraph.DeliveryContext

::: activegraph.SinkConfig

::: activegraph.OverflowPolicy

::: activegraph.SinkHandle

::: activegraph.SinkStatus

::: activegraph.SinkState

::: activegraph.JSONLEventSink

::: activegraph.RecordingSink

::: activegraph.RecordedDelivery

## Adapter conformance

Future sink adapters subclass this suite, implement its two read/factory
hooks, and set `__test__ = True` for pytest collection.

::: activegraph.sinks.conformance.EventSinkConformance

## Metrics protocol

::: activegraph.Metrics

## Backends

::: activegraph.NoOpMetrics

::: activegraph.PrometheusMetrics

::: activegraph.OpenTelemetryMetrics
