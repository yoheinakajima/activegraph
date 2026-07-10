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

import threading
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
        self._instrument_locks: dict[tuple[Any, ...], threading.Lock] = {}
        self._instrument_locks_lock = threading.Lock()

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
        with self._creation_lock(("counter", *key)):
            c = self._counters.get(key)
            if c is None:
                c = self._client.Counter(
                    name,
                    name.replace("_", " "),
                    labelnames=keys,
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
        with self._creation_lock(("histogram", *key)):
            h = self._histograms.get(key)
            if h is None:
                h = self._client.Histogram(
                    name,
                    name.replace("_", " "),
                    labelnames=keys,
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
        with self._creation_lock(("gauge", *key)):
            g = self._gauges.get(key)
            if g is None:
                g = self._client.Gauge(
                    name,
                    name.replace("_", " "),
                    labelnames=keys,
                    registry=self._registry,
                )
                self._gauges[key] = g
        if keys:
            g.labels(**tags).set(value)
        else:
            g.set(value)

    def _creation_lock(self, key: tuple[Any, ...]) -> threading.Lock:
        """Return a per-instrument initialization lock.

        The map lock is never held while prometheus_client code runs, so a
        slow instrument creation cannot stall observations of another name.
        """

        with self._instrument_locks_lock:
            lock = self._instrument_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._instrument_locks[key] = lock
            return lock


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
