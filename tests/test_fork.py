"""Fork semantics: branch at a point, run independently, fork-of-fork."""

from __future__ import annotations

import pytest

from activegraph import (
    FrozenClock,
    Graph,
    Runtime,
    SQLiteEventStore,
    behavior,
    relation_behavior,
)


def _tmp_db(tmp_path):
    return str(tmp_path / "run.db")


def _quickstart_behaviors():
    @behavior(name="planner", on=["goal.created"])
    def planner(event, graph, ctx):
        goal_text = event.payload["goal"]
        research = graph.add_object(
            "task", {"title": f"Research: {goal_text}", "status": "open"}
        )
        memo = graph.add_object(
            "task", {"title": "Draft memo", "status": "blocked"}
        )
        graph.add_relation(research.id, memo.id, "depends_on")

    @behavior(name="researcher", on=["object.created"], where={"object.type": "task"})
    def researcher(event, graph, ctx):
        task = event.payload["object"]
        if task["data"]["status"] != "open" or "Research" not in task["data"]["title"]:
            return
        graph.add_object("claim", {"text": "claim", "confidence": 0.7})
        graph.emit("task.completed", {"task_id": task["id"]})

    @relation_behavior(name="unblock", relation_type="depends_on", on=["task.completed"])
    def unblock(rel, event, graph, ctx):
        if event.payload["task_id"] == rel.source:
            graph.patch_object(rel.target, {"status": "open"})


def test_fork_creates_new_run_with_copied_events(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    parent = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    parent.run_goal("Evaluate")

    target = parent.graph.events[3].id  # some midpoint event
    fork = parent.fork(at_event=target, label="branch-A")
    assert fork.run_id != parent.run_id

    runs = {r.run_id: r for r in SQLiteEventStore.list_runs(db)}
    assert runs[fork.run_id].parent_run_id == parent.run_id
    assert runs[fork.run_id].forked_at_event_id == target
    assert runs[fork.run_id].label == "branch-A"


def test_fork_threads_graph_store_into_projection(tmp_path):
    """fork replays the copied log into a caller-supplied GraphStore."""
    from activegraph import InMemoryGraphStore

    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    parent = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    parent.run_goal("Evaluate")

    target = parent.graph.events[3].id
    store = InMemoryGraphStore()
    fork = parent.fork(at_event=target, label="branch-A", graph_store=store)

    # The fork's projection lives in the supplied store, not a fresh default.
    assert fork.graph._state is store  # noqa: SLF001 — internal seam
    # And the copied log was replayed into it.
    assert fork.graph.all_objects()


def test_parent_is_untouched_by_fork(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    parent = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    parent.run_goal("Evaluate")
    parent_object_ids_before = sorted(o.id for o in parent.graph.all_objects())
    parent_events_before = [e.id for e in parent.graph.events]

    target = parent.graph.events[3].id
    fork = parent.fork(at_event=target)
    fork.graph.add_object("claim", {"text": "counter"})
    fork.run_until_idle()

    assert sorted(o.id for o in parent.graph.all_objects()) == parent_object_ids_before
    assert [e.id for e in parent.graph.events] == parent_events_before


def test_fork_runs_independently_and_is_persisted(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    parent = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    parent.run_goal("Evaluate")
    target = next(
        e.id
        for e in parent.graph.events
        if e.type == "object.created"
        and e.payload["object"]["type"] == "claim"
    )

    fork = parent.fork(at_event=target, label="alt")
    fork.graph.add_object("claim", {"text": "counter", "confidence": 0.4})
    fork.run_until_idle()

    # Reload the fork from disk; same final state.
    reloaded = Runtime.load(db, run_id=fork.run_id)
    fork_objects = sorted((o.type, o.data.get("text")) for o in fork.graph.all_objects() if o.type == "claim")
    reloaded_objects = sorted((o.type, o.data.get("text")) for o in reloaded.graph.all_objects() if o.type == "claim")
    assert fork_objects == reloaded_objects


def test_fork_of_fork(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    parent = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    parent.run_goal("Evaluate")
    mid = parent.graph.events[2].id
    fork = parent.fork(at_event=mid, label="A")
    fork.run_until_idle()

    grand_target = fork.graph.events[1].id
    grandchild = fork.fork(at_event=grand_target, label="B")
    grandchild.run_until_idle()

    runs = {r.run_id: r for r in SQLiteEventStore.list_runs(db)}
    assert runs[grandchild.run_id].parent_run_id == fork.run_id
    assert runs[fork.run_id].parent_run_id == parent.run_id


def test_fork_requires_sqlite_backed_runtime():
    _quickstart_behaviors()
    rt = Runtime(Graph(clock=FrozenClock()))
    rt.run_goal("x")
    with pytest.raises(RuntimeError):
        rt.fork(at_event="evt_001")


def test_fork_at_unknown_event_raises(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)
    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("x")
    with pytest.raises(KeyError):
        rt.fork(at_event="evt_999")


def test_fork_preserves_id_counters_then_diverges(tmp_path):
    """CONTRACT v0.5 #12: after fork, IDs continue monotonically from where
    they were at the fork point. Two forks from the same point produce
    different objects with the same logical id."""
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)
    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("Evaluate")

    target = next(
        e.id
        for e in rt.graph.events
        if e.type == "object.created"
        and e.payload["object"]["type"] == "claim"
    )

    fork_a = rt.fork(at_event=target, label="A")
    fork_b = rt.fork(at_event=target, label="B")
    a_obj = fork_a.graph.add_object("note", {"text": "alpha"})
    b_obj = fork_b.graph.add_object("note", {"text": "beta"})
    # Same logical id; different runs; different content.
    assert a_obj.id == b_obj.id
    assert (
        fork_a.graph.get_object(a_obj.id).data["text"]
        != fork_b.graph.get_object(b_obj.id).data["text"]
    )
