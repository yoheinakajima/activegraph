"""EventSink conformance and R1 boundary acceptance tests. CONTRACT v1.8."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from activegraph import (
    DeliveryContext,
    Event,
    FrozenClock,
    Graph,
    IDGen,
    JSONLEventSink,
    OverflowPolicy,
    RecordingSink,
    Runtime,
    SinkConfig,
    behavior,
)
from activegraph.observability.metrics import METRIC_BY_NAME
from activegraph.sinks.base import EventSink
from activegraph.sinks.conformance import EventSinkConformance
from activegraph.sinks.testing import RecordedDelivery
from activegraph.store.memory import InMemoryEventStore
from activegraph.store.retention import compact
from activegraph.store.sqlite import SQLiteEventStore


class TestRecordingSinkConformance(EventSinkConformance):
    __test__ = True

    def make_sink(self) -> EventSink:
        return RecordingSink()

    def read_deliveries(
        self, sink: EventSink
    ) -> tuple[RecordedDelivery, ...]:
        return cast(RecordingSink, sink).deliveries


class TestJSONLEventSinkConformance(EventSinkConformance):
    __test__ = True

    def setup_method(self, method: Any) -> None:
        fd, self._path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(self._path)

    def make_sink(self) -> EventSink:
        return JSONLEventSink(self._path)

    def read_deliveries(
        self, sink: EventSink
    ) -> tuple[RecordedDelivery, ...]:
        path = cast(JSONLEventSink, sink).path
        if not path.exists():
            return ()
        out: list[RecordedDelivery] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            event = record["event"]
            context = record["context"]
            out.append(
                RecordedDelivery(
                    event=Event(
                        id=event["id"],
                        type=event["type"],
                        payload=event["payload"],
                        actor=event["actor"],
                        frame_id=event["frame_id"],
                        caused_by=event["caused_by"],
                        timestamp=event["timestamp"],
                    ),
                    context=DeliveryContext(
                        run_id=context["run_id"],
                        sequence=context["sequence"],
                        mode=context["mode"],
                    ),
                )
            )
        return tuple(out)

    def cleanup(self) -> None:
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass


class RecordingMetrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, str], float]] = []
        self.gauges: list[tuple[str, dict[str, str], float]] = []

    def counter(
        self, name: str, tags: dict[str, str], value: float = 1.0
    ) -> None:
        self.counters.append((name, dict(tags), value))

    def histogram(
        self, name: str, tags: dict[str, str], value: float
    ) -> None:
        return

    def gauge(
        self, name: str, tags: dict[str, str], value: float
    ) -> None:
        self.gauges.append((name, dict(tags), value))


class BlockingMetrics(RecordingMetrics):
    """Block the first observation and record every calling thread."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.thread_ids: list[int] = []
        self._lock = threading.Lock()
        self._blocked_once = False

    def _before_call(self) -> None:
        with self._lock:
            self.thread_ids.append(threading.get_ident())
            should_block = not self._blocked_once
            self._blocked_once = True
        if should_block:
            self.entered.set()
            self.release.wait()

    def counter(
        self, name: str, tags: dict[str, str], value: float = 1.0
    ) -> None:
        self._before_call()
        super().counter(name, tags, value)

    def gauge(
        self, name: str, tags: dict[str, str], value: float
    ) -> None:
        self._before_call()
        super().gauge(name, tags, value)


class OrderedGateSink:
    """Block the first delivery while recording every entered event id."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._condition = threading.Condition()
        self._ids: list[str] = []

    def open(self) -> None:
        return

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        with self._condition:
            self._ids.append(event.id)
            count = len(self._ids)
            self._condition.notify_all()
        if count == 1:
            self.entered.set()
            self.release.wait()

    def flush(self) -> None:
        return

    def close(self) -> None:
        return

    @property
    def ids(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(self._ids)

    def wait_for(self, count: int, timeout: float = 2.0) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: len(self._ids) >= count, timeout)


class RaisingSink:
    def open(self) -> None:
        return

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        raise RuntimeError("sink exploded")

    def flush(self) -> None:
        return

    def close(self) -> None:
        return


class HangingOpenSink:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def open(self) -> None:
        self.entered.set()
        self.release.wait()

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        return

    def flush(self) -> None:
        return

    def close(self) -> None:
        return


class MutatingSink:
    def open(self) -> None:
        return

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        event.payload["nested"]["value"] = "corrupted"

    def flush(self) -> None:
        return

    def close(self) -> None:
        return


class InspectingSink:
    def __init__(self, graph: Graph, store: InMemoryEventStore) -> None:
        self.graph = graph
        self.store = store
        self.checked = threading.Event()
        self.failure: str | None = None

    def open(self) -> None:
        return

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        try:
            assert self.store.get_event(event.id) is not None
            object_id = event.payload["object"]["id"]
            assert self.graph.get_object(object_id) is not None
        except Exception as exc:
            self.failure = f"{type(exc).__name__}: {exc}"
        finally:
            self.checked.set()

    def flush(self) -> None:
        return

    def close(self) -> None:
        return


class RejectingStore:
    run_id = "run_rejecting_store"

    def append(self, event: Event) -> None:
        raise RuntimeError("durable append refused")


class GatedAppendStore:
    run_id = "run_concurrent_emit"

    def __init__(self) -> None:
        self.first_entered = threading.Event()
        self.release_first = threading.Event()
        self.second_entered = threading.Event()

    def append(self, event: Event) -> None:
        if event.id == "evt_001":
            self.first_entered.set()
            self.release_first.wait()
        else:
            self.second_entered.set()


class GatedFlushSink:
    def __init__(self) -> None:
        self.flush_entered = threading.Event()
        self.release_flush = threading.Event()

    def open(self) -> None:
        return

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        return

    def flush(self) -> None:
        self.flush_entered.set()
        self.release_flush.wait()

    def close(self) -> None:
        return


class CountingGateSink(OrderedGateSink):
    def __init__(self) -> None:
        super().__init__()
        self.flush_calls = 0

    def flush(self) -> None:
        self.flush_calls += 1


class CloseFailSink:
    def open(self) -> None:
        return

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        return

    def flush(self) -> None:
        return

    def close(self) -> None:
        raise OSError("close failed")


class OpenFailingSlowCloseSink:
    def __init__(self) -> None:
        self.open_entered = threading.Event()
        self.release_open = threading.Event()
        self.close_entered = threading.Event()
        self.release_close = threading.Event()

    def open(self) -> None:
        self.open_entered.set()
        self.release_open.wait()
        raise OSError("open failed")

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        return

    def flush(self) -> None:
        return

    def close(self) -> None:
        self.close_entered.set()
        self.release_close.wait()
        raise OSError("cleanup close failed")


class FlushFailingFile:
    def __init__(self) -> None:
        self.close_called = False

    def flush(self) -> None:
        raise OSError("flush failed")

    def close(self) -> None:
        self.close_called = True


def _event(graph: Graph, n: int, *, type_: str = "test.event") -> Event:
    return Event(
        id=graph.ids.event(),
        type=type_,
        payload={"n": n},
        actor="test",
        timestamp=f"2026-07-09T01:00:{n:02d}Z",
    )


def _canonical_result(graph: Graph) -> bytes:
    value = {
        "events": [event.to_dict() for event in graph.events],
        "objects": [obj.to_dict() for obj in graph.all_objects()],
        "relations": [relation.to_dict() for relation in graph.all_relations()],
    }
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _remove_sqlite(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


def test_delivery_occurs_after_projection_and_durable_append() -> None:
    store = InMemoryEventStore(run_id="run_post_acceptance")
    graph = Graph(run_id="run_post_acceptance")
    sink = InspectingSink(graph, store)
    runtime = Runtime(graph, behaviors=[], store=store, sinks=[sink])
    try:
        graph.add_object("memo", {"text": "accepted"})
        assert runtime.flush_sinks(timeout=2.0)
        assert sink.checked.is_set()
        assert sink.failure is None
    finally:
        runtime.close_sinks(timeout=2.0)


def test_runtime_sink_receives_every_live_event_including_lifecycle() -> None:
    graph = Graph(run_id="run_all_live_events")
    sink = RecordingSink()
    runtime = Runtime(graph, behaviors=[], sinks=[sink])
    try:
        runtime.run_goal("observe the whole accepted log")
        assert runtime.flush_sinks(timeout=2.0)
        assert [delivery.event.id for delivery in sink.deliveries] == [
            event.id for event in graph.events
        ]
        assert any(event.type == "runtime.idle" for event in sink.events)
    finally:
        runtime.close_sinks(timeout=2.0)


def test_failed_durable_append_is_never_offered() -> None:
    graph = Graph(run_id="run_rejecting_store")
    sink = RecordingSink()
    runtime = Runtime(
        graph,
        behaviors=[],
        store=RejectingStore(),
        sinks=[sink],
    )
    try:
        with pytest.raises(RuntimeError, match="durable append refused"):
            graph.emit(_event(graph, 1))
        assert runtime.flush_sinks(timeout=2.0)
        assert sink.deliveries == ()
    finally:
        runtime.close_sinks(timeout=2.0)


def test_reentrant_and_raising_legacy_listeners_cannot_reorder_or_suppress() -> None:
    graph = Graph(run_id="run_recursive_listener")
    sink = RecordingSink()
    graph.add_sink(sink)

    def recursive(event: Event) -> None:
        if event.payload.get("n") == 1:
            graph.emit(_event(graph, 2))

    graph.add_listener(recursive)
    graph.emit(_event(graph, 1))
    assert graph.flush_sinks(timeout=2.0)
    assert [delivery.event.payload["n"] for delivery in sink.deliveries] == [1, 2]
    assert [delivery.context.sequence for delivery in sink.deliveries] == [1, 2]

    def broken_listener(event: Event) -> None:
        raise RuntimeError("legacy listener failed")

    graph.add_listener(broken_listener)
    with pytest.raises(RuntimeError, match="legacy listener failed"):
        graph.emit(_event(graph, 3))
    assert graph.flush_sinks(timeout=2.0)
    assert [delivery.event.payload["n"] for delivery in sink.deliveries] == [
        1,
        2,
        3,
    ]
    assert graph.close_sinks(timeout=2.0)


def test_legacy_listener_can_wait_for_another_emitting_thread() -> None:
    graph = Graph(run_id="run_listener_thread_join")
    sink = RecordingSink()
    graph.add_sink(sink)
    nested_done = threading.Event()

    def listener(event: Event) -> None:
        if event.payload.get("n") != 1:
            return

        def nested_emit() -> None:
            graph.emit(_event(graph, 2))
            nested_done.set()

        thread = threading.Thread(target=nested_emit, daemon=True)
        thread.start()
        assert nested_done.wait(timeout=1.0)

    graph.add_listener(listener)
    try:
        graph.emit(_event(graph, 1))
        assert graph.flush_sinks(timeout=2.0)
        assert [delivery.context.sequence for delivery in sink.deliveries] == [1, 2]
    finally:
        graph.close_sinks(timeout=2.0)


def test_listener_removal_does_not_skip_the_next_snapshot_listener() -> None:
    graph = Graph(run_id="run_listener_removal")
    calls: list[tuple[str, int]] = []

    def first(event: Event) -> None:
        calls.append(("first", event.payload["n"]))
        assert graph._remove_listener(first)

    def second(event: Event) -> None:
        calls.append(("second", event.payload["n"]))

    graph.add_listener(first)
    graph.add_listener(second)
    graph.emit(_event(graph, 1))
    graph.emit(_event(graph, 2))
    assert calls == [("first", 1), ("second", 1), ("second", 2)]


def test_concurrent_emit_serializes_log_sequence_and_sink_order() -> None:
    graph = Graph(run_id="run_concurrent_emit")
    store = GatedAppendStore()
    graph.attach_store(cast(Any, store))
    sink = RecordingSink()
    graph.add_sink(sink)
    first = _event(graph, 1)
    second = _event(graph, 2)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(graph.emit, first)
            assert store.first_entered.wait(timeout=2.0)
            second_future = pool.submit(graph.emit, second)
            assert not store.second_entered.wait(timeout=0.05)
            store.release_first.set()
            first_future.result(timeout=2.0)
            second_future.result(timeout=2.0)
        assert graph.flush_sinks(timeout=2.0)
        assert [delivery.event.id for delivery in sink.deliveries] == [
            "evt_001",
            "evt_002",
        ]
        assert [delivery.context.sequence for delivery in sink.deliveries] == [1, 2]
    finally:
        store.release_first.set()
        graph.close_sinks(timeout=2.0)


def test_constructor_sink_name_failure_leaves_no_zombie_runtime_listener() -> None:
    graph = Graph(run_id="run_duplicate_sink")
    graph.add_sink(RecordingSink(), name="duplicate")
    try:
        assert len(graph._listeners) == 0
        with pytest.raises(ValueError, match="already attached"):
            Runtime(
                graph,
                behaviors=[],
                sinks=[SinkConfig(RecordingSink(), name="duplicate")],
            )
        assert len(graph._listeners) == 0
    finally:
        graph.close_sinks(timeout=2.0)


def test_invalid_sink_protocol_fails_before_store_or_listener_attachment() -> None:
    graph = Graph(run_id="run_invalid_sink")
    store = InMemoryEventStore(run_id=graph.run_id)
    with pytest.raises(TypeError, match="sink must implement"):
        Runtime(
            graph,
            behaviors=[],
            store=store,
            sinks=[cast(Any, object())],
        )
    assert graph.store is None
    assert len(graph._listeners) == 0


@pytest.mark.parametrize(
    "capacity",
    [True, 0, 1.5, float("inf"), float("nan")],
)
def test_queue_capacity_requires_a_positive_integer_without_starting_worker(
    capacity: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        SinkConfig(RecordingSink(), queue_capacity=cast(Any, capacity))

    graph = Graph(run_id="run_bad_capacity")
    sink = RecordingSink()
    with pytest.raises(ValueError, match="positive integer"):
        graph.add_sink(sink, queue_capacity=cast(Any, capacity))
    assert sink._open_count == 0
    assert graph.sink_statuses() == ()


def test_sink_event_copy_prevents_history_and_sibling_mutation() -> None:
    store = InMemoryEventStore(run_id="run_copy_isolation")
    graph = Graph(run_id="run_copy_isolation")
    healthy = RecordingSink()
    runtime = Runtime(
        graph,
        behaviors=[],
        store=store,
        sinks=[
            SinkConfig(MutatingSink(), name="mutating"),
            SinkConfig(healthy, name="healthy"),
        ],
    )
    event = Event(
        id=graph.ids.event(),
        type="test.mutable",
        payload={"nested": {"value": "original"}},
        timestamp="2026-07-09T01:10:00Z",
    )
    try:
        graph.emit(event)
        assert runtime.flush_sinks(timeout=2.0)
        assert graph.events[0].payload["nested"]["value"] == "original"
        persisted = store.get_event(event.id)
        assert persisted is not None
        assert persisted.payload["nested"]["value"] == "original"
        assert healthy.deliveries[0].event.payload["nested"]["value"] == "original"
    finally:
        runtime.close_sinks(timeout=2.0)


def test_raising_and_hanging_sinks_do_not_change_runtime_or_healthy_sink() -> None:
    @behavior(name="project", on=["goal.created"])
    def project(event: Event, graph: Graph, ctx: Any) -> None:
        graph.add_object("result", {"goal": event.payload["goal"]})

    baseline = Graph(
        ids=IDGen(), clock=FrozenClock(), run_id="run_sink_isolation"
    )
    baseline_runtime = Runtime(baseline, behaviors=[project])
    baseline_runtime.run_goal("project identically")

    graph = Graph(ids=IDGen(), clock=FrozenClock(), run_id="run_sink_isolation")
    hanging = OrderedGateSink()
    healthy = RecordingSink()
    runtime = Runtime(
        graph,
        behaviors=[project],
        sinks=[
            SinkConfig(RaisingSink(), name="raising", queue_capacity=32),
            SinkConfig(hanging, name="hanging", queue_capacity=32),
            SinkConfig(healthy, name="healthy", queue_capacity=32),
        ],
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(runtime.run_goal, "project identically")
            future.result(timeout=1.0)
        assert hanging.entered.wait(timeout=2.0)
        assert healthy.wait_for(len(graph.events), timeout=2.0)
        raising_handle = graph._sinks["raising"]
        assert raising_handle.flush(timeout=2.0)
        assert _canonical_result(graph) == _canonical_result(baseline)
        assert [obj.type for obj in graph.all_objects()] == ["result"]
        assert [delivery.event.to_dict() for delivery in healthy.deliveries] == [
            event.to_dict() for event in graph.events
        ]
        statuses = {status.name: status for status in runtime.sink_statuses()}
        assert statuses["raising"].errors == len(graph.events)
        assert statuses["healthy"].delivered == len(graph.events)
    finally:
        hanging.release.set()
        runtime.close_sinks(timeout=2.0)


def test_hanging_open_is_async_and_does_not_delay_other_sink() -> None:
    graph = Graph(run_id="run_hanging_open")
    hanging = HangingOpenSink()
    healthy = RecordingSink()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            lambda: Runtime(
                graph,
                behaviors=[],
                sinks=[
                    SinkConfig(hanging, name="hanging-open", queue_capacity=1),
                    SinkConfig(healthy, name="healthy"),
                ],
            )
        )
        runtime = future.result(timeout=1.0)
    try:
        assert hanging.entered.wait(timeout=2.0)
        graph.emit(_event(graph, 1))
        assert healthy.wait_for(1, timeout=2.0)
        assert runtime.sink_statuses()[0].state.value == "opening"
    finally:
        hanging.release.set()
        runtime.close_sinks(timeout=2.0)


def test_close_timeout_retains_status_and_can_be_retried() -> None:
    graph = Graph(run_id="run_close_retry")
    sink = OrderedGateSink()
    runtime = Runtime(graph, behaviors=[], sinks=[SinkConfig(sink, name="slow-close")])
    graph.emit(_event(graph, 1))
    assert sink.entered.wait(timeout=2.0)
    try:
        assert runtime.close_sinks(timeout=0.01) is False
        statuses = runtime.sink_statuses()
        assert len(statuses) == 1
        assert statuses[0].name == "slow-close"
        assert statuses[0].state.value == "closing"
        graph.emit(_event(graph, 2))
        detached_status = runtime.sink_statuses()[0]
        assert detached_status.enqueued == 1
        assert detached_status.dropped == 0
        sink.release.set()
        assert runtime.close_sinks(timeout=2.0) is True
        assert runtime.sink_statuses() == ()
    finally:
        sink.release.set()
        runtime.close_sinks(timeout=2.0)


def test_repeated_flush_timeouts_do_not_accumulate_control_commands() -> None:
    graph = Graph(run_id="run_flush_retries")
    sink = CountingGateSink()
    runtime = Runtime(graph, behaviors=[])
    handle = runtime.add_sink(sink, name="flush-retry")
    graph.emit(_event(graph, 1))
    assert sink.entered.wait(timeout=2.0)
    try:
        for _ in range(100):
            assert handle.flush(timeout=0.0) is False
        assert len(handle._commands) == 0
        sink.release.set()
        assert runtime.close_sinks(timeout=2.0)
        assert sink.flush_calls == 1
    finally:
        sink.release.set()
        runtime.close_sinks(timeout=2.0)


def test_drop_oldest_bookkeeping_stays_bounded_behind_hung_delivery() -> None:
    graph = Graph(run_id="run_drop_oldest_bounded")
    sink = OrderedGateSink()
    runtime = Runtime(graph, behaviors=[])
    handle = runtime.add_sink(
        sink,
        name="drop-oldest-bounded",
        queue_capacity=1,
        overflow_policy=OverflowPolicy.DROP_OLDEST,
    )
    graph.emit(_event(graph, 1))
    assert sink.entered.wait(timeout=2.0)
    try:
        for n in range(2, 1002):
            graph.emit(_event(graph, n))
        assert len(handle._unsettled_tickets) <= 2
        assert handle.status().queue_depth == 1
        sink.release.set()
        assert sink.wait_for(2, timeout=2.0)
        assert sink.ids == ("evt_001", "evt_1001")
    finally:
        sink.release.set()
        runtime.close_sinks(timeout=2.0)


def test_terminal_close_failure_is_observable_but_name_can_be_reused() -> None:
    graph = Graph(run_id="run_close_failure")
    runtime = Runtime(graph, behaviors=[])
    runtime.add_sink(CloseFailSink(), name="replaceable")
    assert runtime.remove_sink("replaceable", timeout=2.0) is False
    statuses = runtime.sink_statuses()
    assert len(statuses) == 1
    assert statuses[0].name == "replaceable"
    assert statuses[0].state.value == "failed"
    assert statuses[0].last_error_operation == "close"

    replacement = RecordingSink()
    runtime.add_sink(replacement, name="replaceable")
    graph.emit(_event(graph, 1))
    assert runtime.flush_sinks(timeout=2.0)
    assert len(replacement.deliveries) == 1
    assert runtime.close_sinks(timeout=2.0)


def test_sink_handle_identity_and_queue_configuration_are_read_only() -> None:
    graph = Graph(run_id="run_immutable_handle")
    sink = RecordingSink()
    handle = graph.add_sink(sink, name="immutable", queue_capacity=3)
    try:
        for attribute, value in (
            ("name", "stranded"),
            ("run_id", "wrong-run"),
            ("queue_capacity", 999),
            ("overflow_policy", "fail_sink"),
        ):
            with pytest.raises(AttributeError):
                setattr(handle, attribute, value)
        graph.emit(_event(graph, 1))
        assert handle.flush(timeout=2.0)
        assert len(sink.deliveries) == 1
        assert graph.remove_sink(handle, timeout=2.0)
    finally:
        graph.close_sinks(timeout=2.0)


def test_direct_handle_close_detaches_and_frees_its_graph_name() -> None:
    graph = Graph(run_id="run_direct_handle_close")
    old_sink = RecordingSink()
    handle = graph.add_sink(old_sink, name="replaceable")
    assert handle.close(timeout=2.0)
    assert handle.close(timeout=2.0)
    assert graph.sink_statuses() == ()

    replacement = RecordingSink()
    graph.add_sink(replacement, name="replaceable")
    try:
        graph.emit(_event(graph, 1))
        assert graph.flush_sinks(timeout=2.0)
        assert old_sink.deliveries == ()
        assert len(replacement.deliveries) == 1
    finally:
        graph.close_sinks(timeout=2.0)


def test_direct_handle_close_detaches_an_already_failed_worker() -> None:
    graph = Graph(run_id="run_direct_failed_handle_close")
    sink = OpenFailingSlowCloseSink()
    handle = graph.add_sink(sink, name="failed")
    assert sink.open_entered.wait(timeout=2.0)
    sink.release_open.set()
    sink.release_close.set()
    assert handle._stopped.wait(timeout=2.0)

    assert handle.close(timeout=2.0) is False
    assert graph._sinks == {}
    assert graph.sink_statuses()[0].state.value == "failed"
    replacement = graph.add_sink(RecordingSink(), name="failed")
    assert replacement.name == "failed"
    graph.close_sinks(timeout=2.0)


def test_concurrent_direct_handle_closes_are_idempotent() -> None:
    graph = Graph(run_id="run_concurrent_handle_close")
    sink = GatedFlushSink()
    handle = graph.add_sink(sink, name="concurrent-close")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(handle.close, timeout=2.0)
        assert sink.flush_entered.wait(timeout=2.0)
        second = pool.submit(handle.close, timeout=2.0)
        sink.release_flush.set()
        assert first.result(timeout=2.0) is True
        assert second.result(timeout=2.0) is True
    assert graph.sink_statuses() == ()


def test_constructor_can_reuse_retained_terminal_failure_name() -> None:
    graph = Graph(run_id="run_constructor_replacement")
    graph.add_sink(CloseFailSink(), name="replaceable")
    assert graph.remove_sink("replaceable", timeout=2.0) is False
    assert graph.sink_statuses()[0].state.value == "failed"

    replacement = RecordingSink()
    runtime = Runtime(
        graph,
        behaviors=[],
        sinks=[SinkConfig(replacement, name="replaceable")],
    )
    try:
        graph.emit(_event(graph, 1))
        assert runtime.flush_sinks(timeout=2.0)
        assert len(replacement.deliveries) == 1
        statuses = runtime.sink_statuses()
        assert len(statuses) == 1
        assert statuses[0].name == "replaceable"
        assert statuses[0].state.value in {"opening", "running"}
    finally:
        runtime.close_sinks(timeout=2.0)


def test_open_failure_cleanup_finishes_before_terminal_name_reuse() -> None:
    graph = Graph(run_id="run_open_failure_cleanup")
    sink = OpenFailingSlowCloseSink()
    graph.add_sink(sink, name="opening")
    assert sink.open_entered.wait(timeout=2.0)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            close_future = pool.submit(graph.close_sinks, timeout=2.0)
            sink.release_open.set()
            assert sink.close_entered.wait(timeout=2.0)
            assert close_future.done() is False
            with pytest.raises(ValueError, match="already attached"):
                graph.add_sink(RecordingSink(), name="opening")
            sink.release_close.set()
            assert close_future.result(timeout=2.0) is False

        statuses = graph.sink_statuses()
        assert len(statuses) == 1
        assert statuses[0].last_error_operation == "close"
        assert "cleanup close failed" in (statuses[0].last_error or "")
        replacement = graph.add_sink(RecordingSink(), name="opening")
        assert replacement.name == "opening"
    finally:
        sink.release_open.set()
        sink.release_close.set()
        graph.close_sinks(timeout=2.0)


def test_blocking_metrics_never_run_on_or_delay_the_sink_offer_path() -> None:
    graph = Graph(run_id="run_blocking_sink_metrics")
    metrics = BlockingMetrics()
    handle = graph.add_sink(
        RecordingSink(),
        name="bounded-metrics",
        queue_capacity=2,
        metrics=metrics,
    )
    assert metrics.entered.wait(timeout=2.0)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                lambda: (
                    threading.get_ident(),
                    [graph.emit(_event(graph, n)) for n in range(1, 201)],
                )
            )
            emit_thread_id, _events = future.result(timeout=1.0)

        status = handle.status()
        assert status.queue_depth <= 2
        assert status.enqueued == 2
        assert status.dropped == 198
        assert len(handle._pending_drops) <= 5
        assert len(handle._pending_errors) <= 4
        assert metrics.thread_ids
        assert emit_thread_id not in metrics.thread_ids

        metrics.release.set()
        assert handle.flush(timeout=2.0)
        status = handle.status()
        delivered_metric = sum(
            value
            for name, _tags, value in metrics.counters
            if name == "activegraph_sink_events_delivered_total"
        )
        dropped_metric = sum(
            value
            for name, _tags, value in metrics.counters
            if name == "activegraph_sink_events_dropped_total"
        )
        queue_metrics = [
            value
            for name, _tags, value in metrics.gauges
            if name == "activegraph_sink_queue_depth"
        ]
        assert delivered_metric == status.delivered
        assert dropped_metric == status.dropped
        assert queue_metrics[-1] == status.queue_depth
    finally:
        metrics.release.set()
        graph.close_sinks(timeout=2.0)


def test_sink_instrument_creation_cannot_lock_runtime_metrics_hot_path() -> None:
    from activegraph.observability.otel import OpenTelemetryMetrics

    if not OpenTelemetryMetrics.available():
        pytest.skip("opentelemetry-api/opentelemetry-sdk not installed")

    class Instrument:
        def add(self, value: float, attributes: dict[str, Any]) -> None:
            return

        def record(self, value: float, attributes: dict[str, Any]) -> None:
            return

    class GatedMeter:
        def __init__(self) -> None:
            self.gauge_entered = threading.Event()
            self.release_gauge = threading.Event()

        def create_up_down_counter(self, name: str, *, description: str) -> Instrument:
            if name == "activegraph_sink_queue_depth":
                self.gauge_entered.set()
                self.release_gauge.wait()
            return Instrument()

        def create_counter(self, name: str, *, description: str) -> Instrument:
            return Instrument()

        def create_histogram(self, name: str, *, description: str) -> Instrument:
            return Instrument()

    meter = GatedMeter()
    metrics = OpenTelemetryMetrics(meter=meter)
    graph = Graph(run_id="run_metrics_lock_isolation")
    sink = RecordingSink()
    runtime = Runtime(graph, behaviors=[], metrics=metrics, sinks=[sink])
    assert meter.gauge_entered.wait(timeout=2.0)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(graph.emit, _event(graph, 1))
            try:
                emitted = future.result(timeout=1.0)
            finally:
                meter.release_gauge.set()
            assert emitted.id == "evt_001"
        assert runtime.flush_sinks(timeout=2.0)
        assert len(sink.deliveries) == 1
    finally:
        meter.release_gauge.set()
        runtime.close_sinks(timeout=2.0)


def test_flush_racing_with_close_coalesces_with_close_flush() -> None:
    graph = Graph(run_id="run_flush_close_race")
    sink = GatedFlushSink()
    runtime = Runtime(graph, behaviors=[], sinks=[sink])
    with ThreadPoolExecutor(max_workers=2) as pool:
        close_future = pool.submit(runtime.close_sinks, timeout=2.0)
        assert sink.flush_entered.wait(timeout=2.0)
        flush_future = pool.submit(runtime.flush_sinks, timeout=2.0)
        sink.release_flush.set()
        assert close_future.result(timeout=2.0) is True
        assert flush_future.result(timeout=2.0) is True


def test_jsonl_close_attempts_close_and_clears_handle_after_flush_failure(
    tmp_path: Path,
) -> None:
    sink = JSONLEventSink(tmp_path / "unused.jsonl")
    failing_file = FlushFailingFile()
    sink._file = cast(Any, failing_file)
    sink._open_count = 1
    with pytest.raises(OSError, match="flush failed"):
        sink.close()
    assert failing_file.close_called
    assert sink._file is None
    sink.close()


@pytest.mark.parametrize(
    ("policy", "expected_ids", "expected_reason", "expected_enqueued"),
    [
        (
            OverflowPolicy.DROP_NEWEST,
            ("evt_001", "evt_002"),
            "overflow.drop_newest",
            2,
        ),
        (
            OverflowPolicy.DROP_OLDEST,
            ("evt_001", "evt_003"),
            "overflow.drop_oldest",
            3,
        ),
        (
            OverflowPolicy.FAIL_SINK,
            ("evt_001", "evt_002"),
            "overflow.fail_sink",
            2,
        ),
    ],
)
def test_every_overflow_policy_is_exact_counted_and_observable(
    policy: OverflowPolicy,
    expected_ids: tuple[str, ...],
    expected_reason: str,
    expected_enqueued: int,
) -> None:
    graph = Graph(run_id=f"run_overflow_{policy.value}")
    sink = OrderedGateSink()
    metrics = RecordingMetrics()
    runtime = Runtime(
        graph,
        behaviors=[],
        metrics=metrics,
        sinks=[
            SinkConfig(
                sink,
                name="bounded",
                queue_capacity=1,
                overflow_policy=policy,
            )
        ],
    )
    try:
        graph.emit(_event(graph, 1))
        assert sink.entered.wait(timeout=2.0)
        graph.emit(_event(graph, 2))
        graph.emit(_event(graph, 3))
        status = runtime.sink_statuses()[0]
        assert status.enqueued == expected_enqueued
        assert status.dropped == 1
        assert status.dropped_by_reason == ((expected_reason, 1),)

        sink.release.set()
        assert sink.wait_for(2, timeout=2.0)
        assert sink.ids == expected_ids
        if policy is OverflowPolicy.FAIL_SINK:
            assert graph._sinks["bounded"]._stopped.wait(timeout=2.0)
        else:
            assert runtime.flush_sinks(timeout=2.0)
        assert any(
            name == "activegraph_sink_events_dropped_total"
            and tags == {"sink": "bounded", "reason": expected_reason}
            for name, tags, _value in metrics.counters
        )
        assert any(
            name == "activegraph_sink_queue_depth"
            and tags == {"sink": "bounded", "run_id": graph.run_id}
            for name, tags, _value in metrics.gauges
        )
        queue_gauges = [
            value
            for name, tags, value in metrics.gauges
            if name == "activegraph_sink_queue_depth"
            and tags == {"sink": "bounded", "run_id": graph.run_id}
        ]
        assert queue_gauges[-1] == float(runtime.sink_statuses()[0].queue_depth)
    finally:
        sink.release.set()
        runtime.close_sinks(timeout=2.0)


def test_load_and_fork_attach_only_after_history_replay() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    parent = Runtime(Graph(run_id="run_replay_parent"), behaviors=[], persist_to=path)
    parent.graph.emit(_event(parent.graph, 1))
    parent.graph.emit(_event(parent.graph, 2))
    loaded_sink = RecordingSink()
    fork_sink = RecordingSink()
    loaded: Runtime | None = None
    fork: Runtime | None = None
    try:
        loaded = Runtime.load(
            path,
            run_id=parent.run_id,
            behaviors=[],
            replay_strict=True,
            sinks=[loaded_sink],
        )
        assert loaded.flush_sinks(timeout=2.0)
        assert loaded_sink.deliveries == ()
        loaded.graph.emit(_event(loaded.graph, 3))
        assert loaded.flush_sinks(timeout=2.0)
        assert loaded_sink.deliveries[0].context.sequence == 3

        fork = parent.fork(
            at_event=parent.graph.events[-1].id,
            behaviors=[],
            sinks=[fork_sink],
        )
        assert fork.flush_sinks(timeout=2.0)
        assert fork_sink.deliveries == ()
        fork.graph.emit(_event(fork.graph, 3))
        assert fork.flush_sinks(timeout=2.0)
        assert fork_sink.deliveries[0].context.sequence == 3
    finally:
        if loaded is not None:
            loaded.close_sinks(timeout=2.0)
        if fork is not None:
            fork.close_sinks(timeout=2.0)
        for runtime in (loaded, fork, parent):
            if runtime is not None and runtime.graph.store is not None:
                runtime.graph.store.close()
        _remove_sqlite(path)


def test_replay_result_is_byte_identical_with_and_without_sink() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    original = Runtime(Graph(run_id="run_replay_bytes"), behaviors=[], persist_to=path)
    first = original.graph.add_object("memo", {"text": "one"})
    original.graph.patch_object(first.id, {"text": "two"})
    if original.graph.store is not None:
        original.graph.store.close()

    plain: Runtime | None = None
    observed: Runtime | None = None
    sink = RecordingSink()
    try:
        plain = Runtime.load(path, run_id="run_replay_bytes", behaviors=[])
        observed = Runtime.load(
            path,
            run_id="run_replay_bytes",
            behaviors=[],
            replay_strict=True,
            sinks=[sink],
        )
        assert observed.flush_sinks(timeout=2.0)
        assert sink.deliveries == ()
        assert _canonical_result(observed.graph) == _canonical_result(plain.graph)
    finally:
        if observed is not None:
            observed.close_sinks(timeout=2.0)
        for runtime in (plain, observed):
            if runtime is not None and runtime.graph.store is not None:
                runtime.graph.store.close()
        _remove_sqlite(path)


def test_compacted_strict_load_keeps_snapshot_and_suffix_silent(
    tmp_path: Path,
) -> None:
    @behavior(name="seed", on=["goal.created"])
    def seed(event: Event, graph: Graph, ctx: Any) -> None:
        graph.add_object("task", {"title": event.payload["goal"]})

    path = str(tmp_path / "compacted-sinks.db")
    original = Runtime(
        Graph(ids=IDGen(), clock=FrozenClock()), behaviors=[seed]
    )
    original.run_goal("before")
    original.save_state(path)
    run_id = original.run_id
    assert original.graph.store is not None
    original.graph.store.close()
    del original

    compact(path, run_id)
    suffix = Runtime.load(path, run_id=run_id, behaviors=[seed])
    suffix.run_goal("after")
    assert suffix.graph.store is not None
    suffix.graph.store.close()
    del suffix

    sink = RecordingSink()
    loaded = Runtime.load(
        path,
        run_id=run_id,
        behaviors=[seed],
        replay_strict=True,
        sinks=[sink],
    )
    try:
        assert loaded.flush_sinks(timeout=2.0)
        assert sink.deliveries == ()
        assert sorted(
            obj.data["title"] for obj in loaded.graph.objects(type="task")
        ) == ["after", "before"]

        history_len = len(loaded.graph.events)
        loaded.graph.emit(_event(loaded.graph, 99))
        assert loaded.flush_sinks(timeout=2.0)
        assert len(sink.deliveries) == 1
        assert sink.deliveries[0].context.sequence == history_len + 1
        assert sink.deliveries[0].context.mode == "live"
    finally:
        loaded.close_sinks(timeout=2.0)
        if loaded.graph.store is not None:
            loaded.graph.store.close()


def test_invalid_fork_sink_configuration_creates_no_child_run(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "fork-sink-validation.db")
    parent = Runtime(
        Graph(run_id="run_fork_validation"),
        behaviors=[],
        persist_to=path,
    )
    event = parent.graph.emit(_event(parent.graph, 1))
    before = [record.run_id for record in SQLiteEventStore.list_runs(path)]
    try:
        with pytest.raises(TypeError, match="sink must implement"):
            parent.fork(
                at_event=event.id,
                behaviors=[],
                sinks=[cast(Any, object())],
            )
        after = [record.run_id for record in SQLiteEventStore.list_runs(path)]
        assert after == before
    finally:
        if parent.graph.store is not None:
            parent.graph.store.close()


def test_promote_quiescent_delta_events_are_still_observed(tmp_path: Path) -> None:
    path = str(tmp_path / "promote-sinks.db")
    parent = Runtime(Graph(run_id="run_promote_parent"), behaviors=[])
    parent.graph.add_object("memo", {"text": "base"})
    parent.save_state(path)
    fork = parent.fork(parent.graph.events[-1].id, behaviors=[])
    created = fork.graph.add_object("note", {"text": "candidate"})
    sink = RecordingSink()
    parent.add_sink(sink)
    try:
        result = parent.promote(fork)
        assert parent.flush_sinks(timeout=2.0)
        delivered = sink.events
        assert [event.type for event in delivered] == [
            "promote.applied",
            "object.created",
        ]
        assert delivered[0].id == result.marker_event_id
        assert delivered[1].payload["object"]["id"] == created.id
        assert delivered[1].actor is not None
        assert delivered[1].actor.startswith("promote:")
    finally:
        parent.close_sinks(timeout=2.0)
        if parent.graph.store is not None:
            parent.graph.store.close()
        if fork.graph.store is not None:
            fork.graph.store.close()


def test_jsonl_is_canonical_serde_compatible_and_append_only(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    graph = Graph(run_id="run_jsonl_golden")
    first_sink = JSONLEventSink(path)
    runtime = Runtime(graph, behaviors=[], sinks=[first_sink])
    event = Event(
        id=graph.ids.event(),
        type="test.jsonl",
        payload={
            "decimal": Decimal("1.2300"),
            "date": date(2026, 7, 9),
            "datetime": datetime(2026, 7, 9, 12, 30, 0),
            "set": {"b", "a"},
            "unicode": "café — 🚀",
        },
        actor="test",
        timestamp="2026-07-09T12:30:00Z",
    )
    graph.emit(event)
    assert runtime.close_sinks(timeout=2.0)

    expected_first = json.dumps(
        {
            "context": {
                "mode": "live",
                "run_id": "run_jsonl_golden",
                "sequence": 1,
            },
            "event": {
                "actor": "test",
                "caused_by": None,
                "frame_id": None,
                "id": "evt_001",
                "payload": {
                    "date": "2026-07-09",
                    "datetime": "2026-07-09T12:30:00",
                    "decimal": "1.2300",
                    "set": ["a", "b"],
                    "unicode": "café — 🚀",
                },
                "timestamp": "2026-07-09T12:30:00Z",
                "type": "test.jsonl",
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert path.read_bytes() == (expected_first + "\n").encode("utf-8")

    second_sink = JSONLEventSink(path)
    runtime.add_sink(second_sink)
    graph.emit(_event(graph, 2))
    assert runtime.close_sinks(timeout=2.0)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == expected_first
    assert json.loads(lines[1])["context"]["sequence"] == 2


def test_sink_metric_names_kinds_and_cardinality_are_locked() -> None:
    expected = {
        "activegraph_sink_queue_depth": (
            "gauge",
            ("sink", "run_id"),
        ),
        "activegraph_sink_events_delivered_total": (
            "counter",
            ("sink",),
        ),
        "activegraph_sink_events_dropped_total": (
            "counter",
            ("sink", "reason"),
        ),
        "activegraph_sink_errors_total": (
            "counter",
            ("sink", "operation"),
        ),
    }
    for name, (kind, tags) in expected.items():
        spec = METRIC_BY_NAME[name]
        assert spec.kind == kind
        assert spec.tags == tags
        if kind == "counter":
            assert "run_id" not in tags
