"""Prometheus implementation of the Metrics protocol. CONTRACT v0.8 #10.

Lazy import of ``prometheus_client`` so the dep stays optional. Install
with ``pip install 'activegraph[prometheus]'``.

The implementation creates Counter / Histogram / Gauge instruments
on-demand, keyed by ``(name, sorted-tag-keys)``. Tag values become
label values. We use the documented metric names as-is — they already
follow Prometheus conventions (``_total`` for counters, ``_seconds`` /
``_usd`` for histogram units, snake_case throughout).
"""

from __future__ import annotations

from typing import Any


class PrometheusMetrics:
    """Drop-in Metrics implementation backed by prometheus_client.

    Instruments are lazy. Tag keys for an instrument are fixed by the
    first observation; subsequent observations with a different key set
    raise (prometheus_client behavior). This matches the standard metric
    list's fixed tag schemas.
    """

    def __init__(self, registry: Any | None = None) -> None:
        client = _require_client()
        self._client = client
        self._registry = registry if registry is not None else client.REGISTRY
        self._counters: dict[tuple[Any, ...], Any] = {}
        self._histograms: dict[tuple[Any, ...], Any] = {}
        self._gauges: dict[tuple[Any, ...], Any] = {}

    @staticmethod
    def available() -> bool:
        try:
            import prometheus_client  # noqa: F401
        except ImportError:
            return False
        return True

    # ---- protocol ----

    def counter(self, name: str, tags: dict[str, str], value: float = 1.0) -> None:
        keys = tuple(sorted(tags.keys()))
        key = (name, keys)
        c = self._counters.get(key)
        if c is None:
            c = self._client.Counter(
                name, name.replace("_", " "), labelnames=keys,
                registry=self._registry,
            )
            self._counters[key] = c
        if keys:
            c.labels(**tags).inc(value)
        else:
            c.inc(value)

    def histogram(self, name: str, tags: dict[str, str], value: float) -> None:
        keys = tuple(sorted(tags.keys()))
        key = (name, keys)
        h = self._histograms.get(key)
        if h is None:
            h = self._client.Histogram(
                name, name.replace("_", " "), labelnames=keys,
                registry=self._registry,
            )
            self._histograms[key] = h
        if keys:
            h.labels(**tags).observe(value)
        else:
            h.observe(value)

    def gauge(self, name: str, tags: dict[str, str], value: float) -> None:
        keys = tuple(sorted(tags.keys()))
        key = (name, keys)
        g = self._gauges.get(key)
        if g is None:
            g = self._client.Gauge(
                name, name.replace("_", " "), labelnames=keys,
                registry=self._registry,
            )
            self._gauges[key] = g
        if keys:
            g.labels(**tags).set(value)
        else:
            g.set(value)


def _require_client() -> Any:
    try:
        import prometheus_client
    except ImportError as e:
        from activegraph.errors import MissingOptionalDependency
        raise MissingOptionalDependency(
            package="prometheus_client",
            feature="PrometheusMetrics",
            extras="prometheus",
        ) from e
    return prometheus_client
