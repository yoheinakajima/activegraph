"""Structural diff between a parent run and its fork."""

from __future__ import annotations

from activegraph import (
    FrozenClock,
    Graph,
    Runtime,
    behavior,
    relation_behavior,
)
from activegraph.runtime.diff import Diff, DivergentObject


def _tmp_db(tmp_path):
    return str(tmp_path / "run.db")


def _quickstart_behaviors():
    @behavior(name="planner", on=["goal.created"])
    def planner(event, graph, ctx):
        goal_text = event.payload["goal"]
        research = graph.add_object(
            "task", {"title": f"Research: {goal_text}", "status": "open"}
        )
        memo = graph.add_object("task", {"title": "Draft memo", "status": "blocked"})
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


def test_diff_identical_runs_has_no_divergence(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)
    parent = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    parent.run_goal("Evaluate")

    target = parent.graph.events[-2].id  # near the end
    fork = parent.fork(at_event=target)
    fork.run_until_idle()

    diff = parent.diff(fork)
    # The fork ran to idle without divergent input; final state should match.
    assert diff.is_identical is True


def test_diff_is_identical_is_bool_property():
    diff = Diff(parent_run_id="parent", fork_run_id="fork")
    assert diff.is_identical is True

    diff.divergent_objects.append(
        DivergentObject(
            id="task#1",
            in_parent={"id": "task#1", "version": 1},
            in_fork=None,
        )
    )
    assert diff.is_identical is False


def test_diff_reports_divergent_objects(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)
    parent = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    parent.run_goal("Evaluate")

    fork_target = next(
        e.id
        for e in parent.graph.events
        if e.type == "object.created"
        and e.payload["object"]["type"] == "claim"
    )
    fork = parent.fork(at_event=fork_target)
    fork.graph.add_object("claim", {"text": "counter"})
    fork.run_until_idle()

    diff = parent.diff(fork)
    # The new counter-claim is fork-only.
    fork_only_ids = [d.id for d in diff.divergent_objects if d.in_parent is None]
    assert len(fork_only_ids) >= 1


def test_diff_partition_of_events_after_divergence(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)
    parent = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    parent.run_goal("Evaluate")

    target = parent.graph.events[3].id
    fork = parent.fork(at_event=target)
    fork.graph.add_object("claim", {"text": "x"})
    fork.run_until_idle()

    diff = parent.diff(fork)
    # The shared prefix is everything up to the divergence.
    assert len(diff.shared_events) >= 1
    # Lifecycle events are filtered out.
    for e in diff.shared_events + diff.parent_only_events + diff.fork_only_events:
        assert not e.type.startswith("behavior.")
        assert not e.type.startswith("relation_behavior.")
        assert not e.type.startswith("runtime.")


def test_diff_same_logical_event_id_different_payload_is_not_shared(tmp_path):
    """After divergence, parent and fork can emit different events with the
    same logical id (CONTRACT v0.5 #12). Diff must NOT treat them as shared."""
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)
    parent = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    parent.run_goal("Evaluate")

    # Fork early; do something different.
    target = parent.graph.events[3].id
    fork = parent.fork(at_event=target)
    fork.graph.add_object("decision", {"text": "branch decision"})

    diff = parent.diff(fork)
    # Anything in fork after the fork point with a colliding parent id must
    # not be silently treated as identical.
    parent_ids_in_shared = {e.id for e in diff.shared_events}
    fork_only_ids = {e.id for e in diff.fork_only_events}
    overlap = parent_ids_in_shared & fork_only_ids
    assert not overlap, "shared / fork-only event partitions overlapped"
