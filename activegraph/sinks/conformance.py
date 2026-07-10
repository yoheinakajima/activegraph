"""Reusable EventSink adapter conformance suite. CONTRACT v1.8 #5.

Like ``EventStoreConformance``, concrete pytest classes subclass this ABC.
Because EventSink is write-only, adapters additionally provide a test-only
``read_deliveries`` hook; that hook is not part of the production protocol.
"""

from __future__ import annotations

import os
import tempfile
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

import pytest

from activegraph.core.event import Event
from activegraph.core.graph import Graph
from activegraph.runtime.runtime import Runtime
from activegraph.sinks.base import DeliveryContext, EventSink, SinkConfig
from activegraph.sinks.testing import RecordedDelivery
from activegraph.store.memory import InMemoryEventStore


class _RaisingSink:
    def open(self) -> None:
        return

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        raise RuntimeError(f"broken on {event.id}")

    def flush(self) -> None:
        return

    def close(self) -> None:
        return


class _BlockingSink:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def open(self) -> None:
        return

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        self.entered.set()
        self.release.wait()

    def flush(self) -> None:
        return

    def close(self) -> None:
        return


class EventSinkConformance(ABC):
    """Mix into a pytest class to validate an EventSink adapter.

    Subclasses implement ``make_sink`` and ``read_deliveries`` and set
    ``__test__ = True`` so pytest collects the inherited cases.  A fresh sink
    is requested per test; ``cleanup`` may remove files or release
    adapter-specific resources.  The inherited tests drive adapters only
    through the public Runtime/Graph attachment boundary.
    """

    __test__ = False

    @abstractmethod
    def make_sink(self) -> EventSink:
        """Return a fresh candidate adapter for one conformance test."""

    @abstractmethod
    def read_deliveries(
        self, sink: EventSink
    ) -> Sequence[RecordedDelivery]:
        """Read adapter output as event/context pairs after a flush."""

    def cleanup(self) -> None:
        """Release adapter-specific test resources. Default: no-op."""

    @staticmethod
    def _event(graph: Graph, n: int, payload: dict[str, Any] | None = None) -> Event:
        return Event(
            id=graph.ids.event(),
            type="test.accepted",
            payload=payload if payload is not None else {"n": n},
            actor="conformance",
            caused_by=None,
            frame_id=None,
            timestamp=f"2026-07-09T00:00:{n:02d}Z",
        )

    def test_live_delivery_preserves_event_and_context_order(self) -> None:
        sink = self.make_sink()
        graph = Graph(run_id="run_sink_order")
        runtime = Runtime(graph, behaviors=[], sinks=[sink])
        try:
            for n in range(1, 6):
                graph.emit(self._event(graph, n))
            assert runtime.flush_sinks(timeout=2.0)
            deliveries = list(self.read_deliveries(sink))
            assert [d.event.id for d in deliveries] == [
                event.id for event in graph.events
            ]
            assert [d.context.sequence for d in deliveries] == [1, 2, 3, 4, 5]
            assert {d.context.run_id for d in deliveries} == {"run_sink_order"}
            assert {d.context.mode for d in deliveries} == {"live"}
        finally:
            runtime.close_sinks(timeout=2.0)
            self.cleanup()

    def test_nested_unicode_payload_round_trips(self) -> None:
        sink = self.make_sink()
        graph = Graph(run_id="run_sink_payload")
        runtime = Runtime(graph, behaviors=[], sinks=[sink])
        payload: dict[str, Any] = {
            "nested": {"items": [1, {"text": "café — 🚀"}]},
            "empty": [],
            "none": None,
        }
        try:
            graph.emit(self._event(graph, 1, payload))
            assert runtime.flush_sinks(timeout=2.0)
            deliveries = list(self.read_deliveries(sink))
            assert len(deliveries) == 1
            assert deliveries[0].event.payload == payload
        finally:
            runtime.close_sinks(timeout=2.0)
            self.cleanup()

    def test_shared_sink_preserves_each_concurrent_run_order(self) -> None:
        sink = self.make_sink()
        graph_a = Graph(run_id="run_sink_concurrent_a")
        graph_b = Graph(run_id="run_sink_concurrent_b")
        runtime_a = Runtime(graph_a, behaviors=[], sinks=[sink])
        runtime_b = Runtime(graph_b, behaviors=[], sinks=[sink])
        barrier = threading.Barrier(2)

        def emit_ten(graph: Graph) -> None:
            barrier.wait()
            for n in range(1, 11):
                graph.emit(self._event(graph, n))

        thread_a = threading.Thread(target=emit_ten, args=(graph_a,))
        thread_b = threading.Thread(target=emit_ten, args=(graph_b,))
        try:
            thread_a.start()
            thread_b.start()
            thread_a.join(timeout=2.0)
            thread_b.join(timeout=2.0)
            assert not thread_a.is_alive()
            assert not thread_b.is_alive()
            assert runtime_a.flush_sinks(timeout=2.0)
            assert runtime_b.flush_sinks(timeout=2.0)
            deliveries = list(self.read_deliveries(sink))
            for run_id in ("run_sink_concurrent_a", "run_sink_concurrent_b"):
                run_deliveries = [
                    delivery
                    for delivery in deliveries
                    if delivery.context.run_id == run_id
                ]
                assert [delivery.context.sequence for delivery in run_deliveries] == list(
                    range(1, 11)
                )
                assert [delivery.event.payload["n"] for delivery in run_deliveries] == list(
                    range(1, 11)
                )
        finally:
            runtime_a.close_sinks(timeout=2.0)
            runtime_b.close_sinks(timeout=2.0)
            self.cleanup()

    def test_event_rejected_before_acceptance_is_not_delivered(self) -> None:
        sink = self.make_sink()
        store = InMemoryEventStore(run_id="run_sink_rejected")
        graph = Graph(run_id="run_sink_rejected")
        runtime = Runtime(graph, behaviors=[], store=store, sinks=[sink])
        try:
            with pytest.raises(TypeError):
                graph.emit(self._event(graph, 1, {"bad": object()}))
            assert runtime.flush_sinks(timeout=2.0)
            assert list(self.read_deliveries(sink)) == []
            assert graph.events == []
            assert store.count() == 0
        finally:
            runtime.close_sinks(timeout=2.0)
            self.cleanup()

    def test_raising_sibling_sink_is_isolated(self) -> None:
        sink = self.make_sink()
        graph = Graph(run_id="run_sink_failure")
        runtime = Runtime(
            graph,
            behaviors=[],
            sinks=[
                SinkConfig(sink=sink, name="candidate"),
                SinkConfig(sink=_RaisingSink(), name="broken"),
            ],
        )
        try:
            for n in range(1, 4):
                graph.emit(self._event(graph, n))
            assert runtime.flush_sinks(timeout=2.0)
            assert [d.event.id for d in self.read_deliveries(sink)] == [
                event.id for event in graph.events
            ]
            statuses = {status.name: status for status in runtime.sink_statuses()}
            assert statuses["candidate"].delivered == 3
            assert statuses["candidate"].errors == 0
            assert statuses["broken"].errors == 3
        finally:
            runtime.close_sinks(timeout=2.0)
            self.cleanup()

    def test_bounded_overflow_is_counted(self) -> None:
        blocker = _BlockingSink()
        graph = Graph(run_id="run_sink_overflow")
        runtime = Runtime(
            graph,
            behaviors=[],
            sinks=[
                SinkConfig(
                    sink=blocker,
                    name="blocked",
                    queue_capacity=1,
                )
            ],
        )
        try:
            graph.emit(self._event(graph, 1))
            assert blocker.entered.wait(timeout=2.0)
            graph.emit(self._event(graph, 2))
            graph.emit(self._event(graph, 3))
            status = runtime.sink_statuses()[0]
            assert status.queue_capacity == 1
            assert status.dropped == 1
            assert status.dropped_by_reason == (("overflow.drop_newest", 1),)
        finally:
            blocker.release.set()
            runtime.close_sinks(timeout=2.0)
            self.cleanup()

    def test_load_with_sink_does_not_redeliver_history(self) -> None:
        sink = self.make_sink()
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        original = Runtime(
            Graph(run_id="run_sink_replay"),
            behaviors=[],
            persist_to=path,
        )
        original.graph.emit(self._event(original.graph, 1))
        original.graph.emit(self._event(original.graph, 2))
        store = original.graph.store
        if store is not None:
            store.close()

        loaded: Runtime | None = None
        try:
            loaded = Runtime.load(
                path,
                run_id="run_sink_replay",
                behaviors=[],
                replay_strict=True,
                sinks=[sink],
            )
            assert loaded.flush_sinks(timeout=2.0)
            assert list(self.read_deliveries(sink)) == []

            loaded.graph.emit(self._event(loaded.graph, 3))
            assert loaded.flush_sinks(timeout=2.0)
            deliveries = list(self.read_deliveries(sink))
            assert len(deliveries) == 1
            assert deliveries[0].context.sequence == 3
        finally:
            if loaded is not None:
                loaded.close_sinks(timeout=2.0)
                loaded_store = loaded.graph.store
                if loaded_store is not None:
                    loaded_store.close()
            self.cleanup()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass


__all__ = ["EventSinkConformance"]
