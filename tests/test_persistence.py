"""Save / load round-trip, resume after pause, chaos recovery, replay."""

from __future__ import annotations

import os
import tempfile

import pytest

from activegraph import (
    FrozenClock,
    Graph,
    NonSerializableEventError,
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
        graph.add_object(
            "claim",
            {
                "text": "Market appears early but growing.",
                "confidence": 0.7,
                "evidence": [],
            },
        )
        graph.emit("task.completed", {"task_id": task["id"]})

    @relation_behavior(name="unblock", relation_type="depends_on", on=["task.completed"])
    def unblock(rel, event, graph, ctx):
        if event.payload["task_id"] == rel.source:
            graph.patch_object(rel.target, {"status": "open"})


# ---------- round-trip ----------


def test_save_then_load_produces_identical_graph(tmp_path):
    """Save a completed run, load it, verify objects and relations match."""
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    graph = Graph(clock=FrozenClock())
    rt = Runtime(graph, persist_to=db)
    rt.run_goal("Evaluate this startup idea")
    rt.save_state()
    run_id = rt.run_id

    loaded = Runtime.load(db, run_id=run_id)
    assert {o.id for o in loaded.graph.all_objects()} == {
        o.id for o in graph.all_objects()
    }
    assert {r.id for r in loaded.graph.all_relations()} == {
        r.id for r in graph.all_relations()
    }
    # Every original event came back, in order.
    assert [e.id for e in loaded.graph.events] == [e.id for e in graph.events]


def test_load_threads_graph_store_into_rebuilt_projection(tmp_path):
    """Runtime.load replays the log into a caller-supplied GraphStore."""
    from activegraph import InMemoryGraphStore

    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    graph = Graph(clock=FrozenClock())
    rt = Runtime(graph, persist_to=db)
    rt.run_goal("Evaluate this startup idea")
    rt.save_state()

    store = InMemoryGraphStore()
    loaded = Runtime.load(db, run_id=rt.run_id, graph_store=store)

    # The projection lives in the supplied store, not a fresh default.
    assert loaded.graph._state is store  # noqa: SLF001 — internal seam
    # And the log was actually replayed into it.
    assert {o.id for o in loaded.graph.all_objects()} == {
        o.id for o in graph.all_objects()
    }


def test_late_bound_save_writes_in_memory_events_to_sqlite(tmp_path):
    """A purely in-memory run can be saved later via save_state(path)."""
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    graph = Graph(clock=FrozenClock())
    rt = Runtime(graph)  # no persist_to
    rt.run_goal("Evaluate")
    assert graph.store is None

    rt.save_state(db)
    assert graph.store is not None

    loaded = Runtime.load(db, run_id=rt.run_id)
    assert [e.id for e in loaded.graph.events] == [e.id for e in graph.events]


def test_save_state_without_store_requires_path():
    _quickstart_behaviors()
    rt = Runtime(Graph())
    rt.run_goal("x")
    with pytest.raises(ValueError):
        rt.save_state()


def test_save_state_path_must_match_attached_store(tmp_path):
    _quickstart_behaviors()
    rt = Runtime(Graph(), persist_to=_tmp_db(tmp_path))
    rt.run_goal("x")
    with pytest.raises(ValueError):
        rt.save_state(str(tmp_path / "other.db"))


# ---------- resume ----------


def test_resume_continues_from_where_run_stopped(tmp_path):
    """Stop mid-run via tight budget, reload, finish — should match an
    uninterrupted run modulo provenance run_id (which differs by design)."""
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    # paused run
    paused_rt = Runtime(
        Graph(clock=FrozenClock()),
        persist_to=db,
        budget={"max_behavior_calls": 1},
    )
    paused_rt.run_goal("Evaluate this startup idea")
    paused_run_id = paused_rt.run_id

    resumed = Runtime.load(db, run_id=paused_run_id)
    resumed.run_until_idle()

    # uninterrupted run for comparison
    uninterrupted = Runtime(Graph(clock=FrozenClock()))
    uninterrupted.run_goal("Evaluate this startup idea")

    assert sorted(o.id for o in resumed.graph.all_objects()) == sorted(
        o.id for o in uninterrupted.graph.all_objects()
    )
    assert sorted(r.id for r in resumed.graph.all_relations()) == sorted(
        r.id for r in uninterrupted.graph.all_relations()
    )
    # The blocked task should now be open in both.
    blocked = [o for o in resumed.graph.all_objects() if o.id == "task#2"][0]
    assert blocked.data["status"] == "open"


# ---------- chaos / crash recovery ----------


def test_chaos_crash_mid_behavior_leaves_consistent_graph(tmp_path):
    """A behavior emits some events, then raises — the runtime catches it
    (CONTRACT #13). Reload: the emitted events are present, the half-done
    behavior is not re-fired (CONTRACT v0.5 #8), no half-applied state."""
    db = _tmp_db(tmp_path)

    @behavior(name="explode_mid_run", on=["goal.created"])
    def explode(event, graph, ctx):
        graph.add_object("claim", {"text": "emitted before crash"})
        graph.emit("custom.midway", {"marker": True})
        raise RuntimeError("simulated crash mid-behavior")

    @behavior(name="after", on=["custom.midway"])
    def after(event, graph, ctx):
        graph.add_object("note", {"text": "fired after crash event"})

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("trigger")
    saved_event_ids = [e.id for e in rt.graph.events]

    # Reload in a fresh runtime.
    loaded = Runtime.load(db, run_id=rt.run_id)
    assert [e.id for e in loaded.graph.events] == saved_event_ids
    # The claim emitted before the crash survived.
    assert any(o.type == "claim" for o in loaded.graph.all_objects())
    # behavior.failed event was persisted.
    types = [e.type for e in loaded.graph.events]
    assert "behavior.failed" in types


def test_in_flight_behavior_loss_is_documented(tmp_path):
    """If a behavior raises before completing, anything it would have done
    AFTER the crash point is lost. The graph is consistent with events
    that did get appended, nothing more."""
    db = _tmp_db(tmp_path)

    @behavior(name="half_done", on=["goal.created"])
    def half(event, graph, ctx):
        graph.add_object("a", {"i": 1})
        raise RuntimeError("crash")
        graph.add_object("b", {"i": 2})  # never reached

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("x")

    loaded = Runtime.load(db, run_id=rt.run_id)
    types = {o.type for o in loaded.graph.all_objects()}
    assert "a" in types
    assert "b" not in types  # unreached work is gone — by design


# ---------- run identity & multi-run files ----------


def test_multiple_runs_in_one_file_load_correctly(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    rt1 = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt1.run_goal("first")
    rt2 = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt2.run_goal("second")

    runs = SQLiteEventStore.list_runs(db)
    assert {r.run_id for r in runs} == {rt1.run_id, rt2.run_id}

    loaded_2 = Runtime.load(db, run_id=rt2.run_id)
    goal_event = next(e for e in loaded_2.graph.events if e.type == "goal.created")
    assert goal_event.payload["goal"] == "second"


def test_load_without_run_id_picks_most_recent(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)

    rt1 = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt1.run_goal("first")
    rt2 = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt2.run_goal("second")

    loaded = Runtime.load(db)
    assert loaded.run_id == rt2.run_id


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Runtime.load(str(tmp_path / "doesnotexist.db"))


# ---------- provenance run_id ----------


def test_objects_record_run_id_in_provenance(tmp_path):
    _quickstart_behaviors()
    db = _tmp_db(tmp_path)
    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("Evaluate")
    for o in rt.graph.all_objects():
        assert o.provenance["run_id"] == rt.run_id


# ---------- JSON serialization edge cases ----------


def test_unicode_and_nested_payloads_round_trip(tmp_path):
    db = _tmp_db(tmp_path)

    @behavior(name="builder", on=["goal.created"])
    def builder(event, graph, ctx):
        graph.add_object(
            "doc",
            {
                "title": "héllo wörld 漢字 🚀",
                "tags": ["α", "β"],
                "meta": {"nested": {"deeply": {"k": None}}},
                "count": 0,
                "flag": False,
            },
        )

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("x")
    loaded = Runtime.load(db, run_id=rt.run_id)
    doc = next(o for o in loaded.graph.all_objects() if o.type == "doc")
    assert doc.data["title"] == "héllo wörld 漢字 🚀"
    assert doc.data["tags"] == ["α", "β"]
    assert doc.data["meta"]["nested"]["deeply"]["k"] is None
    assert doc.data["count"] == 0
    assert doc.data["flag"] is False


def test_decimal_payload_serializes_to_string(tmp_path):
    from decimal import Decimal

    db = _tmp_db(tmp_path)

    @behavior(name="builder", on=["goal.created"])
    def builder(event, graph, ctx):
        graph.add_object("price", {"amount": Decimal("3.14")})

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("x")
    loaded = Runtime.load(db, run_id=rt.run_id)
    p = next(o for o in loaded.graph.all_objects() if o.type == "price")
    # Decimal round-trips as its string form (we don't reverse-coerce).
    assert p.data["amount"] == "3.14"


def test_non_serializable_payload_raises_at_emit_time(tmp_path):
    db = _tmp_db(tmp_path)

    class Opaque:
        pass

    @behavior(name="bad", on=["goal.created"])
    def bad(event, graph, ctx):
        graph.add_object("x", {"thing": Opaque()})

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("x")
    # The behavior raised a NonSerializableEventError inside its add_object;
    # the runtime catches it and emits behavior.failed (CONTRACT #13).
    failed = [e for e in rt.graph.events if e.type == "behavior.failed"]
    assert len(failed) == 1
    assert failed[0].payload["exception_type"] == "NonSerializableEventError"


def test_non_serializable_payload_does_not_corrupt_graph(tmp_path):
    """Validation runs BEFORE projection; bad event leaves no half-state."""
    db = _tmp_db(tmp_path)

    class Opaque:
        pass

    graph = Graph(clock=FrozenClock(), run_id="r")
    rt = Runtime(graph, persist_to=db)
    before = len(graph.all_objects())
    # Call emit directly (bypassing the runtime's try/catch).
    from activegraph.core.event import Event

    bad = Event(
        id=graph.ids.event(),
        type="object.created",
        payload={"object": {"id": "x#1", "type": "x", "data": {"k": Opaque()},
                            "version": 1, "provenance": {}}},
        actor="user",
        timestamp=graph.clock.now(),
    )
    with pytest.raises(NonSerializableEventError):
        graph.emit(bad)
    assert len(graph.all_objects()) == before


# ---------- large log soft target ----------


def test_large_log_save_and_load_under_two_seconds(tmp_path):
    import time

    db = _tmp_db(tmp_path)
    graph = Graph(clock=FrozenClock())
    rt = Runtime(graph, persist_to=db)
    # 10_000 events: emit a custom event repeatedly. Avoid behavior firing
    # so we don't generate lifecycle events.
    from activegraph.core.event import Event

    n = 10_000
    t0 = time.monotonic()
    for i in range(n):
        graph.emit(
            Event(
                id=graph.ids.event(),
                type="tick",
                payload={"i": i},
                actor="bench",
                timestamp=graph.clock.now(),
            )
        )
    write_secs = time.monotonic() - t0

    t1 = time.monotonic()
    loaded = Runtime.load(db, run_id=rt.run_id)
    load_secs = time.monotonic() - t1

    assert len(loaded.graph.events) == n
    # Soft target. Generous bound to avoid CI flakiness.
    assert write_secs < 5.0, f"write of {n} events took {write_secs:.2f}s"
    assert load_secs < 2.0, f"load of {n} events took {load_secs:.2f}s"


class TestGraphBackedApprovals:
    """v1.4: pending approvals survive Runtime.load (proposed minus
    granted, rebuilt from the log)."""

    def _runtime_with_gated_pack(self, path):
        from pydantic import BaseModel

        from activegraph import Graph, Runtime, behavior, clear_registry
        from activegraph.packs import ObjectType, Pack, PackPolicy

        clear_registry()

        class Risky(BaseModel):
            action: str

        pack = Pack(
            name="gated",
            version="1.0",
            object_types=(ObjectType(name="risky", schema=Risky),),
            policies=(
                PackPolicy(name="gate", requires_approval=("risky",)),
            ),
        )

        @behavior(name="proposer", on=["goal.created"])
        def proposer(event, graph, ctx):
            ctx.propose_object("risky", {"action": "wipe"}, reason="test")

        g = Graph()
        rt = Runtime(g, persist_to=path)
        rt.load_pack(pack)
        rt.run_goal("go")
        return rt

    def test_pending_approval_survives_reload_and_approves(self, tmp_path):
        from activegraph import Runtime

        path = str(tmp_path / "run.db")
        rt = self._runtime_with_gated_pack(path)
        assert len(rt.pending_approvals()) == 1
        aid = rt.pending_approvals()[0].id

        loaded = Runtime.load(path, run_id=rt.run_id)
        pending = loaded.pending_approvals()
        assert [pa.id for pa in pending] == [aid]
        assert pending[0].data == {"action": "wipe"}
        assert pending[0].object_type == "risky"

        obj_id = loaded.approve(aid, approved_by="owner")
        assert loaded.graph.get_object(obj_id).data["action"] == "wipe"

    def test_granted_approvals_do_not_reappear(self, tmp_path):
        from activegraph import Runtime

        path = str(tmp_path / "run.db")
        rt = self._runtime_with_gated_pack(path)
        aid = rt.pending_approvals()[0].id
        rt.approve(aid, approved_by="owner")

        loaded = Runtime.load(path, run_id=rt.run_id)
        assert loaded.pending_approvals() == []
        # And fresh proposals can't collide with the recorded id space.
        new_id = loaded._add_pending_approval(  # noqa: SLF001
            object_type="risky", data={"action": "again"}
        )
        assert new_id != aid

    def test_fork_inherits_pending_approvals(self, tmp_path):
        path = str(tmp_path / "run.db")
        rt = self._runtime_with_gated_pack(path)
        aid = rt.pending_approvals()[0].id
        fork = rt.fork(at_event=rt.trace.events()[-1].id)
        assert [pa.id for pa in fork.pending_approvals()] == [aid]
