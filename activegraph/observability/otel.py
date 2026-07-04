"""OpenTelemetry implementation of the Metrics protocol.

Lazy import of ``opentelemetry-api`` and ``opentelemetry-sdk`` so the
dependency stays optional. Install with
``pip install 'activegraph[opentelemetry]'``.

Counters map to OTel Counter instruments, histograms to Histogram
instruments, and synchronous point-in-time gauges are emulated with
UpDownCounter deltas. Instruments are cached by
``(kind, name, sorted-tag-keys)``; gauge last-values are tracked by
``(name, sorted-tag-keys, sorted-tag-values)``.
"""

from __future__ import annotations

from typing import Any


class OpenTelemetryMetrics:
    """Drop-in Metrics implementation backed by OpenTelemetry metrics.

    Pass a custom ``meter`` in tests or applications that already own a
    MeterProvider. Otherwise the global ``opentelemetry.metrics`` meter
    provider is used.
    """

    def __init__(self, meter: Any | None = None, *, meter_name: str = "activegraph") -> None:
        metrics_api, _metrics_sdk = _require_opentelemetry()
        self._meter = meter if meter is not None else metrics_api.get_meter(meter_name)
        self._counters: dict[tuple[str, str, tuple[str, ...]], Any] = {}
        self._histograms: dict[tuple[str, str, tuple[str, ...]], Any] = {}
        self._gauges: dict[tuple[str, str, tuple[str, ...]], Any] = {}
        self._gauge_values: dict[tuple[str, tuple[str, ...], tuple[Any, ...]], float] = {}

    @staticmethod
    def available() -> bool:
        try:
            import opentelemetry.metrics  # noqa: F401
            import opentelemetry.sdk.metrics  # noqa: F401
        except ImportError:
            return False
        return True

    # ---- protocol ----

    def counter(self, name: str, tags: dict[str, str], value: float = 1.0) -> None:
        keys = tuple(sorted(tags.keys()))
        key = ("counter", name, keys)
        counter = self._counters.get(key)
        if counter is None:
            counter = self._meter.create_counter(
                name,
                description=name.replace("_", " "),
            )
            self._counters[key] = counter
        counter.add(value, attributes=_attributes(tags))

    def histogram(self, name: str, tags: dict[str, str], value: float) -> None:
        keys = tuple(sorted(tags.keys()))
        key = ("histogram", name, keys)
        histogram = self._histograms.get(key)
        if histogram is None:
            histogram = self._meter.create_histogram(
                name,
                description=name.replace("_", " "),
            )
            self._histograms[key] = histogram
        histogram.record(value, attributes=_attributes(tags))

    def gauge(self, name: str, tags: dict[str, str], value: float) -> None:
        keys = tuple(sorted(tags.keys()))
        key = ("gauge", name, keys)
        gauge = self._gauges.get(key)
        if gauge is None:
            gauge = self._meter.create_up_down_counter(
                name,
                description=name.replace("_", " "),
            )
            self._gauges[key] = gauge

        values_key = (name, keys, tuple(tags[k] for k in keys))
        previous = self._gauge_values.get(values_key, 0.0)
        delta = float(value) - previous
        self._gauge_values[values_key] = float(value)
        if delta:
            gauge.add(delta, attributes=_attributes(tags))


def _attributes(tags: dict[str, str]) -> dict[str, Any]:
    return dict(tags)


def _require_opentelemetry() -> tuple[Any, Any]:
    try:
        import opentelemetry.metrics as metrics_api
        import opentelemetry.sdk.metrics as metrics_sdk
    except ImportError as e:
        from activegraph.errors import MissingOptionalDependency

        raise MissingOptionalDependency(
            package="opentelemetry-sdk",
            feature="OpenTelemetryMetrics",
            extras="opentelemetry",
        ) from e
    return metrics_api, metrics_sdk
