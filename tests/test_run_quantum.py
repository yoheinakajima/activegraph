"""CONTRACT v1.10 #3: cooperative bounded drains for single-writer hosts."""

from __future__ import annotations

import pytest

from activegraph import (
    FrozenClock,
    Graph,
    IDGen,
    Runtime,
    behavior,
    clear_registry,
    get_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _chain_runtime(*, persist_to=None) -> Runtime:
    @behavior(name="chain", on=["object.created"], where={"object.type": "unit"})
    def chain(event, graph, ctx):
        del ctx
        data = ((event.payload or {}).get("object") or {}).get("data") or {}
        ordinal = int(data.get("ordinal") or 0)
        if ordinal < 5:
            graph.add_object("unit", {"ordinal": ordinal + 1})

    graph = Graph(
        ids=IDGen(),
        clock=FrozenClock("2026-07-12T00:00:00+00:00"),
        run_id="run_quantum",
    )
    runtime = Runtime(graph, persist_to=persist_to)
    runtime.graph.add_object("unit", {"ordinal": 0})
    return runtime


def _canonical_events(runtime: Runtime) -> list[dict]:
    return [event.to_dict() for event in runtime.graph.events]


def test_quantum_yields_without_a_false_idle_and_finishes_once():
    runtime = _chain_runtime()

    first = runtime.run_quantum(max_queue_events=1, max_seconds=1.0)
    assert first.queue_events_processed == 1
    assert first.queue_depth == 1
    assert first.idle is False
    assert first.budget_exhausted is False
    assert not [event for event in runtime.graph.events if event.type == "runtime.idle"]

    quanta = 1
    while not first.idle:
        first = runtime.run_quantum(max_queue_events=1, max_seconds=1.0)
        quanta += 1

    assert quanta == 6
    assert first.queue_depth == 0
    assert len([event for event in runtime.graph.events if event.type == "runtime.idle"]) == 1


def test_cooperative_and_full_drains_are_byte_identical():
    cooperative = _chain_runtime()
    while not cooperative.run_quantum(
        max_queue_events=2, max_seconds=1.0
    ).idle:
        pass

    clear_registry()
    full = _chain_runtime()
    full.run_until_idle()

    assert _canonical_events(cooperative) == _canonical_events(full)


def test_partial_quantum_remains_restart_recoverable(tmp_path):
    path = str(tmp_path / "quantum.db")
    runtime = _chain_runtime(persist_to=path)
    result = runtime.run_quantum(max_queue_events=1, max_seconds=1.0)
    assert result.idle is False
    run_id = runtime.graph.run_id
    runtime.graph.store.close()

    clear_registry()
    _chain_runtime()
    behaviors = get_registry()
    resumed = Runtime.load(path, run_id=run_id, behaviors=behaviors)
    assert resumed.status().queue_depth > 0
    resumed.run_until_idle()
    assert [obj.data["ordinal"] for obj in resumed.graph.objects(type="unit")] == list(range(6))


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"max_queue_events": 0}, ValueError),
        ({"max_queue_events": True}, TypeError),
        ({"max_seconds": 0}, ValueError),
        ({"max_seconds": float("inf")}, ValueError),
        ({"max_seconds": True}, TypeError),
    ],
)
def test_quantum_bounds_fail_loud(kwargs, error):
    runtime = _chain_runtime()
    with pytest.raises(error):
        runtime.run_quantum(**kwargs)
