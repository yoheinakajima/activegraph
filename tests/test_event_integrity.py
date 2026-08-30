"""v1.11 trust-boundary regressions for event and writer integrity."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from activegraph import (
    ConcurrentWriterError,
    Event,
    Graph,
    InMemoryEventStore,
    IncompatibleRuntimeState,
    InvalidPatchOperation,
    SQLiteEventStore,
)


def _event(event_id: str, payload: dict) -> Event:
    return Event(
        id=event_id,
        type="user.fact",
        payload=payload,
        actor="test",
        timestamp="2026-08-29T00:00:00Z",
    )


def test_emit_without_store_accepts_the_persistence_canonical_value() -> None:
    graph = Graph(run_id="run_canonical")

    accepted = graph.emit(
        _event(
            "evt_001",
            {
                "decimal": Decimal("12.50"),
                "date": date(2026, 8, 29),
                "set": {"b", "a"},
            },
        )
    )

    expected = {
        "decimal": "12.50",
        "date": "2026-08-29",
        "set": ["a", "b"],
    }
    assert accepted.payload == expected
    assert graph.events[0].payload == expected


def test_emit_owns_nested_input_and_every_read_is_detached() -> None:
    graph = Graph(run_id="run_detached")
    submitted_payload = {"nested": {"items": [1]}}

    accepted = graph.emit(_event("evt_001", submitted_payload))
    submitted_payload["nested"]["items"].append(2)
    accepted.payload["nested"]["items"].append(3)
    first_read = graph.events
    first_read[0].payload["nested"]["items"].append(4)

    assert graph.events[0].payload == {"nested": {"items": [1]}}


def test_each_listener_receives_an_independent_event_value() -> None:
    graph = Graph(run_id="run_listener_values")
    observed: list[list[int]] = []

    def mutating_listener(event: Event) -> None:
        event.payload["items"].append(99)

    def observing_listener(event: Event) -> None:
        observed.append(list(event.payload["items"]))

    graph.add_listener(mutating_listener)
    graph.add_listener(observing_listener)
    graph.emit(_event("evt_001", {"items": [1]}))

    assert observed == [[1]]
    assert graph.events[0].payload == {"items": [1]}


class _RejectingStore:
    run_id = "run_rejected"

    def append(self, event: Event) -> None:
        raise RuntimeError("durable append refused")


def test_append_failure_leaves_log_projection_and_listeners_untouched() -> None:
    graph = Graph(run_id="run_rejected")
    graph.attach_store(_RejectingStore())  # type: ignore[arg-type]
    observed: list[Event] = []
    graph.add_listener(observed.append)
    event = Event(
        id="evt_001",
        type="object.created",
        payload={
            "object": {
                "id": "memo#1",
                "type": "memo",
                "data": {"text": "not accepted"},
                "version": 1,
                "provenance": {},
            },
            "id": "memo#1",
        },
        timestamp="2026-08-29T00:00:00Z",
    )

    with pytest.raises(RuntimeError, match="durable append refused"):
        graph.emit(event)

    assert graph.events == []
    assert graph.get_object("memo#1") is None
    assert observed == []


def test_attach_requires_the_same_run_and_log_head() -> None:
    store = InMemoryEventStore(run_id="run_existing")
    store.append(_event("evt_001", {"value": 1}))

    with pytest.raises(IncompatibleRuntimeState, match="same run head"):
        Graph(run_id="run_existing").attach_store(store)
    with pytest.raises(IncompatibleRuntimeState, match="same run head"):
        Graph(run_id="run_other").attach_store(InMemoryEventStore("run_existing"))


def test_sqlite_stale_writer_fails_before_graph_projection(tmp_path) -> None:
    path = str(tmp_path / "shared-run.sqlite")
    first_store = SQLiteEventStore(path, run_id="run_shared")
    stale_store = SQLiteEventStore(path, run_id="run_shared")
    first = Graph(run_id="run_shared")
    stale = Graph(run_id="run_shared")
    first.attach_store(first_store)
    stale.attach_store(stale_store)

    first.add_object("memo", {"owner": "first"})
    with pytest.raises(ConcurrentWriterError) as excinfo:
        stale.add_object("memo", {"owner": "stale"})

    assert excinfo.value.context["run_id"] == "run_shared"
    assert excinfo.value.context["expected_head"] is None
    assert excinfo.value.context["actual_head"] is not None
    assert stale.events == []
    assert stale.all_objects() == []
    assert first_store.count() == 1


@pytest.mark.parametrize("op", ["create", "remove", "typo", "UPDATE"])
def test_patch_operations_are_closed_and_validation_consumes_no_ids(op: str) -> None:
    graph = Graph(run_id="run_patch_ops")
    obj = graph.add_object("memo", {"text": "original"})
    before = graph.events

    with pytest.raises(InvalidPatchOperation):
        graph.propose_patch(obj.id, op, {"text": "changed"}, proposed_by="test")

    assert graph.events == before
    assert graph.get_object(obj.id).version == 1  # type: ignore[union-attr]
    patch = graph.propose_patch(
        obj.id, "update", {"text": "changed"}, proposed_by="test"
    )
    assert patch.id == "patch_001"
    assert graph.events[-1].id == "evt_002"


def test_malformed_applied_patch_is_rejected_before_acceptance_and_replay() -> None:
    event = Event(
        id="evt_bad",
        type="patch.applied",
        payload={
            "patch": {
                "id": "patch_001",
                "target": "memo#1",
                "op": "remove",
                "value": {},
                "expected_version": 1,
                "proposed_by": "test",
            }
        },
        timestamp="2026-08-29T00:00:00Z",
    )

    live = Graph(run_id="run_bad_live")
    with pytest.raises(InvalidPatchOperation):
        live.emit(event)
    assert live.events == []

    replay = Graph(run_id="run_bad_replay")
    with pytest.raises(InvalidPatchOperation):
        replay._replay_event(event)  # noqa: SLF001 - historical validation seam
    assert replay.events == []
