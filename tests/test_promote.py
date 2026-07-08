"""Promote: apply a fork's net structural delta to its parent.

CONTRACT v1.3 #4 (design: promote-design.md §7). Covers the clean
paths, every conflict class in the §4 table, the three review
amendments (quiescent apply, referential integrity, plan/apply
staleness), atomicity, lineage validation, audit trail, and replay.
All offline and deterministic.
"""

from __future__ import annotations

import pytest

from activegraph import (
    FrozenClock,
    Graph,
    IDGen,
    IncompatibleRuntimeState,
    PromoteConflictError,
    PromoteLineageError,
    PromoteResult,
    Runtime,
    behavior,
)
from activegraph.packs import Pack


def _parent(tmp_path, name="run.db") -> Runtime:
    """A parent runtime with one seeded task, saved to SQLite, idle."""

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("task", {"title": "base", "status": "open"})

    g = Graph(ids=IDGen(), clock=FrozenClock())
    rt = Runtime(g)
    rt.run_goal("hello")
    rt.save_state(str(tmp_path / name))
    return rt


def _fork_at_tip(rt: Runtime) -> Runtime:
    return rt.fork(at_event=rt.trace.events()[-1].id, label="candidate")


def _task_id(rt: Runtime) -> str:
    return next(o.id for o in rt.graph.all_objects() if o.type == "task")


# ------------------------------------------------------- clean promotes


def test_promote_creates_objects_and_relations(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    a = fork.graph.add_object("note", {"text": "from the fork"})
    b = fork.graph.add_object("note", {"text": "second"})
    rel = fork.graph.add_relation(a.id, b.id, "references")

    result = parent.promote(fork)
    assert isinstance(result, PromoteResult)
    assert [o["id"] for o in result.plan.object_creates] == sorted([a.id, b.id])
    assert [r["id"] for r in result.plan.relation_creates] == [rel.id]

    # Promoted entities keep their fork ids and their fork state.
    promoted = parent.graph.get_object(a.id)
    assert promoted is not None
    assert promoted.data == {"text": "from the fork"}
    got_rel = parent.graph.get_relation(rel.id)
    assert got_rel is not None
    assert (got_rel.source, got_rel.target) == (a.id, b.id)


def test_promote_patches_shared_object_to_fork_state(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.patch_object(task, {"status": "done", "confidence": 0.9})

    result = parent.promote(fork)
    assert [o["id"] for o in result.plan.object_patches] == [task]
    obj = parent.graph.get_object(task)
    assert obj.data == {"title": "base", "status": "done", "confidence": 0.9}


def test_promote_removes_object_removed_in_fork(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.remove_object(task)

    result = parent.promote(fork)
    assert result.plan.object_removes == [task]
    assert parent.graph.get_object(task) is None


def test_promote_empty_delta_still_emits_marker(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    result = parent.promote(fork)
    assert result.plan.is_empty
    assert result.applied_event_ids == []
    marker = next(
        e for e in parent.graph.events if e.type == "promote.applied"
    )
    assert marker.id == result.marker_event_id
    assert marker.payload["from_run"] == fork.run_id


def test_promoted_ids_do_not_collide_with_future_mints(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    promoted = fork.graph.add_object("note", {"text": "x"})
    parent.promote(fork)
    fresh = parent.graph.add_object("note", {"text": "parent's own"})
    assert fresh.id != promoted.id
    assert parent.graph.get_object(promoted.id).data == {"text": "x"}


# ----------------------------------------------------------- dry run


def test_dry_run_returns_plan_and_mutates_nothing(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "x"})

    before = len(parent.graph.events)
    plan = parent.promote(fork, dry_run=True)
    assert plan.is_promotable
    assert len(plan.object_creates) == 1
    assert len(parent.graph.events) == before
    assert plan.computed_against == parent.graph.events[-1].id


# --------------------------------------------------------- conflicts


def test_both_patched_conflicts(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.patch_object(task, {"status": "done"})
    parent.graph.patch_object(task, {"status": "cancelled"})

    with pytest.raises(PromoteConflictError) as exc:
        parent.promote(fork)
    assert exc.value.conflicts[0].kind == "both_changed"
    assert exc.value.conflicts[0].id == task
    # Fail-closed: parent state untouched.
    assert parent.graph.get_object(task).data["status"] == "cancelled"


def test_identical_concurrent_edits_still_conflict(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.patch_object(task, {"status": "done"})
    parent.graph.patch_object(task, {"status": "done"})

    with pytest.raises(PromoteConflictError) as exc:
        parent.promote(fork)
    assert "identically" in exc.value.conflicts[0].detail


def test_same_id_both_created_collision_conflicts(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    # Reseeded generators mint the same next id on both sides.
    fork_obj = fork.graph.add_object("note", {"text": "fork's"})
    parent_obj = parent.graph.add_object("note", {"text": "parent's"})
    assert fork_obj.id == parent_obj.id  # the collision is real

    with pytest.raises(PromoteConflictError) as exc:
        parent.promote(fork)
    assert exc.value.conflicts[0].kind == "both_changed"
    assert "both sides" in exc.value.conflicts[0].detail


def test_remove_modify_pair_conflicts_both_directions(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.remove_object(task)
    parent.graph.patch_object(task, {"status": "active"})
    with pytest.raises(PromoteConflictError):
        parent.promote(fork)

    parent2 = _parent(tmp_path, name="run2.db")
    task2 = _task_id(parent2)
    fork2 = _fork_at_tip(parent2)
    fork2.graph.patch_object(task2, {"status": "done"})
    parent2.graph.remove_object(task2)
    with pytest.raises(PromoteConflictError):
        parent2.promote(fork2)


def test_atomicity_conflict_blocks_clean_parts_too(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    clean = fork.graph.add_object("note", {"text": "clean create"})
    fork.graph.patch_object(task, {"status": "done"})
    parent.graph.patch_object(task, {"status": "cancelled"})

    before = len(parent.graph.events)
    with pytest.raises(PromoteConflictError):
        parent.promote(fork)
    assert len(parent.graph.events) == before
    assert parent.graph.get_object(clean.id) is None


# ------------------- referential integrity (review amendment #2) ----


def test_promoted_relation_to_parent_removed_object_conflicts(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    note = fork.graph.add_object("note", {"text": "n"})
    fork.graph.add_relation(note.id, task, "annotates")
    parent.graph.remove_object(task)  # parent's own post-fork removal

    with pytest.raises(PromoteConflictError) as exc:
        parent.promote(fork)
    kinds = {c.kind for c in exc.value.conflicts}
    assert "dangling_relation" in kinds


def test_promoted_removal_orphaning_parent_relation_conflicts(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.remove_object(task)
    # Parent's own post-fork work now depends on the object.
    other = parent.graph.add_object("note", {"text": "depends on task"})
    parent.graph.add_relation(other.id, task, "annotates")

    with pytest.raises(PromoteConflictError) as exc:
        parent.promote(fork)
    kinds = {c.kind for c in exc.value.conflicts}
    assert "orphaning_removal" in kinds


def test_fork_cascade_removals_promote_cleanly(tmp_path):
    # The fork removed an object whose relations IT created; the
    # cascade-removed relations arrive in the delta and promote fine.
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    note = fork.graph.add_object("note", {"text": "n"})
    rel = fork.graph.add_relation(note.id, task, "annotates")
    # Promote the additions first so both sides share them.
    parent.promote(fork)
    # Second round: the fork removes the note (cascades the relation).
    fork2 = _fork_at_tip(parent)
    fork2.graph.remove_object(note.id)
    result = parent.promote(fork2)
    assert result.plan.object_removes == [note.id]
    assert rel.id in result.plan.relation_removes
    assert parent.graph.get_object(note.id) is None
    assert parent.graph.get_relation(rel.id) is None


# --------------------- plan/apply staleness (review amendment #3) ----


def test_parent_advancing_after_dry_run_is_recaught_at_apply(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.patch_object(task, {"status": "done"})

    plan = parent.promote(fork, dry_run=True)
    assert plan.is_promotable
    stale_tip = plan.computed_against

    # The parent moves between dry-run and apply.
    parent.graph.patch_object(task, {"status": "cancelled"})

    with pytest.raises(PromoteConflictError):
        parent.promote(fork)
    # And the fresh plan sees the new tip, proving recomputation.
    fresh = parent.promote(fork, dry_run=True)
    assert fresh.computed_against != stale_tip
    assert not fresh.is_promotable


# ---------------------- quiescent apply (review amendment #1) --------


def test_apply_is_quiescent_and_marker_is_the_reaction_point(tmp_path):
    fired_on_created: list[str] = []
    fired_on_marker: list[dict] = []

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("task", {"title": "base"})

    @behavior(
        name="note_watcher",
        on=["object.created"],
        where={"object.type": "note"},
    )
    def note_watcher(event, graph, ctx):
        fired_on_created.append(event.payload["id"])

    @behavior(name="promote_watcher", on=["promote.applied"])
    def promote_watcher(event, graph, ctx):
        # Fires once, after the full delta: promoted state is visible.
        oid = event.payload["objects_created"][0]
        fired_on_marker.append(
            {"marker": event.id, "sees": graph.get_object(oid) is not None}
        )

    g = Graph(ids=IDGen(), clock=FrozenClock())
    parent = Runtime(g)
    parent.run_goal("hello")
    parent.save_state(str(tmp_path / "run.db"))

    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "promoted"})

    parent.promote(fork)
    parent.run_until_idle()

    assert fired_on_created == []  # no per-delta-event behavior firing
    assert len(fired_on_marker) == 1
    assert fired_on_marker[0]["sees"] is True


def test_load_does_not_requeue_promote_delta_events(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "x"})
    parent.promote(fork)
    # Deliberately NO run_until_idle: the marker is still undrained
    # when the runtime "stops". A reload must recover the marker but
    # never the quiescent delta events.
    path = str(tmp_path / "run.db")

    loaded = Runtime.load(path, run_id=parent.run_id)
    queued = []
    while loaded._queue:  # noqa: SLF001 — asserting on the internal queue
        queued.append(loaded._queue.pop())  # noqa: SLF001
    assert [e.type for e in queued] == ["promote.applied"]


# ------------------------------------------------------------ lineage


def test_promote_rejects_unrelated_and_reversed_lineage(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    # Reversed: the parent is not a fork of the fork.
    with pytest.raises(PromoteLineageError):
        fork.promote(parent)


def test_promote_rejects_grandchild(tmp_path):
    parent = _parent(tmp_path)
    child = _fork_at_tip(parent)
    grandchild = _fork_at_tip(child)
    grandchild.graph.add_object("note", {"text": "deep"})
    with pytest.raises(PromoteLineageError):
        parent.promote(grandchild)


def test_fork_of_fork_promotes_one_level_at_a_time(tmp_path):
    parent = _parent(tmp_path)
    child = _fork_at_tip(parent)
    grandchild = _fork_at_tip(child)
    note = grandchild.graph.add_object("note", {"text": "deep"})

    child.promote(grandchild)
    assert child.graph.get_object(note.id) is not None
    parent.promote(child)
    assert parent.graph.get_object(note.id) is not None


def test_promote_requires_sqlite_store(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    bare = Runtime(Graph(ids=IDGen(), clock=FrozenClock()))
    with pytest.raises(IncompatibleRuntimeState):
        bare.promote(fork)


# ---------------------------------------------------------- warnings


def test_fork_only_pack_load_surfaces_as_warning(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    fork.load_pack(Pack(name="candidate", version="0.1"))
    fork.graph.add_object("note", {"text": "x"})

    plan = parent.promote(fork, dry_run=True)
    assert any("candidate@0.1" in w for w in plan.warnings)
    # Warnings inform; they don't block.
    result = parent.promote(fork)
    assert any("candidate@0.1" in w for w in result.plan.warnings)
    assert parent.loaded_packs() == []  # code is never adopted silently


# --------------------------------------------------------- audit trail


def test_promoted_events_carry_actor_and_cause(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    note = fork.graph.add_object("note", {"text": "x"})
    result = parent.promote(fork)

    delta_events = [
        e for e in parent.graph.events if e.id in result.applied_event_ids
    ]
    assert delta_events, "delta events are in the parent log"
    for e in delta_events:
        assert e.actor == f"promote:{fork.run_id}"
        assert e.caused_by == result.marker_event_id

    # Causal chain from the promoted object reaches the marker.
    chain = parent.trace.causal_chain(note.id)
    assert result.marker_event_id in chain


def test_trace_renders_promote_block(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "x"})
    parent.promote(fork)
    lines = "\n".join(parent.trace.lines())
    assert "[promote.applied]" in lines
    assert fork.run_id in lines


def test_promote_survives_reload_and_replay(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    note = fork.graph.add_object("note", {"text": "x"})
    fork.graph.patch_object(task, {"status": "done"})
    parent.promote(fork)
    parent.run_until_idle()

    loaded = Runtime.load(str(tmp_path / "run.db"), run_id=parent.run_id)
    assert loaded.graph.get_object(note.id).data == {"text": "x"}
    assert loaded.graph.get_object(task).data["status"] == "done"


def test_promoted_state_matches_fork_state(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "x"})
    fork.graph.patch_object(task, {"status": "done"})
    parent.promote(fork)

    from activegraph.runtime.promote import _object_state

    fork_states = {o.id: _object_state(o) for o in fork.graph.all_objects()}
    parent_states = {o.id: _object_state(o) for o in parent.graph.all_objects()}
    assert parent_states == fork_states


# ------------------- lineage survives reload (store upsert fix) ------


def test_fork_lineage_survives_reload_and_promote_works_after_load(tmp_path):
    # Regression: Runtime.load() upserts the run row with only
    # created_at; the blind ON CONFLICT overwrite used to null
    # parent_run_id / forked_at_event_id / label on every reload,
    # destroying the lineage promote() verifies against.
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    note = fork.graph.add_object("note", {"text": "x"})
    path = str(tmp_path / "run.db")

    reloaded_parent = Runtime.load(path, run_id=parent.run_id)
    reloaded_fork = Runtime.load(path, run_id=fork.run_id)

    record = reloaded_fork.graph.store.get_run()
    assert record.parent_run_id == parent.run_id
    assert record.forked_at_event_id is not None
    assert record.label == "candidate"

    result = reloaded_parent.promote(reloaded_fork)
    assert [o["id"] for o in result.plan.object_creates] == [note.id]
    assert reloaded_parent.graph.get_object(note.id) is not None


# ---------------------------- strict replay of a promoted log --------


def test_strict_replay_accepts_promoted_log(tmp_path):
    # The promote block is a recorded runtime action: strict replay
    # projects it verbatim at its recorded position and excludes it
    # from the re-derivation comparison. Before the fix, any promoted
    # log failed replay_strict=True at the marker.
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    note = fork.graph.add_object("note", {"text": "x"})
    fork.graph.patch_object(task, {"status": "done"})
    parent.promote(fork)
    parent.run_until_idle()

    loaded = Runtime.load(
        str(tmp_path / "run.db"), run_id=parent.run_id, replay_strict=True
    )
    assert loaded.graph.get_object(note.id).data == {"text": "x"}
    assert loaded.graph.get_object(task).data["status"] == "done"


def test_strict_replay_still_catches_real_divergence_after_promote(tmp_path):
    # The promote-block exclusion must not blind strict replay to a
    # genuinely changed behavior.
    from activegraph import clear_registry
    from activegraph.runtime.errors import ReplayDivergenceError

    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "x"})
    parent.promote(fork)
    parent.run_until_idle()

    clear_registry()

    @behavior(name="seed", on=["goal.created"])
    def drifted(event, graph, ctx):
        graph.add_object("task", {"title": "DIFFERENT"})
        graph.add_object("task", {"title": "EXTRA"})  # drift: 2 objects now

    with pytest.raises(ReplayDivergenceError):
        Runtime.load(
            str(tmp_path / "run.db"), run_id=parent.run_id, replay_strict=True
        )


# ------------------------- review-workflow follow-up coverage --------


def test_settings_override_in_fork_tail_surfaces_correctly(tmp_path):
    # The `fork --set` event shape is {pack, overrides, assignments};
    # detection is positional (fork tail), never event-id membership.
    from activegraph.core.event import Event

    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    fork.graph.emit(
        Event(
            id=fork.graph.ids.event(),
            type="pack.settings_overridden",
            payload={
                "pack": "diligence",
                "overrides": {"confidence_threshold_for_review": 0.9},
                "assignments": ["diligence.confidence_threshold_for_review=0.9"],
            },
            actor="runtime",
            frame_id=None,
            caused_by=fork.trace.events()[-1].id,
            timestamp=fork.graph.clock.now(),
        )
    )
    fork.graph.add_object("note", {"text": "x"})
    # The parent advancing past the fork point must not suppress the
    # warning (ids are run-scoped; same id can exist on both sides).
    parent.graph.add_object("decoy", {"n": 1})

    plan = parent.promote(fork, dry_run=True)
    override_warnings = [w for w in plan.warnings if "settings override" in w]
    assert len(override_warnings) == 1
    assert "diligence" in override_warnings[0]
    assert "confidence_threshold_for_review=0.9" in override_warnings[0]
    assert "?=?" not in override_warnings[0]


def test_both_removed_is_a_conflict(tmp_path):
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.remove_object(task)
    parent.graph.remove_object(task)

    with pytest.raises(PromoteConflictError) as exc:
        parent.promote(fork)
    assert "removed on both sides" in exc.value.conflicts[0].detail


def test_promote_rejects_truly_unrelated_run_same_store(tmp_path):
    parent = _parent(tmp_path)
    unrelated_graph = Graph(ids=IDGen(), clock=FrozenClock())
    unrelated = Runtime(
        unrelated_graph, persist_to=str(tmp_path / "run.db")
    )
    unrelated.run_goal("independent")
    with pytest.raises(PromoteLineageError):
        parent.promote(unrelated)


def test_promote_rejects_cross_store_runs(tmp_path):
    parent_a = _parent(tmp_path, name="a.db")
    parent_b = _parent(tmp_path, name="b.db")
    fork_b = _fork_at_tip(parent_b)
    with pytest.raises(PromoteLineageError, match="different stores"):
        parent_a.promote(fork_b)


def test_result_records_computed_against_directly(tmp_path):
    parent = _parent(tmp_path)
    tip = parent.trace.events()[-1].id
    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "x"})
    result = parent.promote(fork)
    assert result.computed_against == tip


def test_diff_after_promote_diverges_only_in_version_bookkeeping(tmp_path):
    # Design §7 (as amended): post-promote, parent vs fork diff shows
    # no data divergence for promoted entities — version counters may
    # differ because they are bookkeeping, not state.
    parent = _parent(tmp_path)
    task = _task_id(parent)
    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "x"})
    fork.graph.patch_object(task, {"status": "done"})
    parent.promote(fork)

    diff = parent.diff(fork)
    assert not diff.divergent_relations
    for d in diff.divergent_objects:
        assert d.in_parent is not None and d.in_fork is not None
        a = {k: v for k, v in d.in_parent.items() if k != "version"}
        b = {k: v for k, v in d.in_fork.items() if k != "version"}
        assert a == b, f"{d.id} diverges beyond version bookkeeping"


def test_fork_cannot_slice_a_promote_block(tmp_path):
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "a"})
    fork.graph.add_object("note", {"text": "b"})
    result = parent.promote(fork)
    parent.run_until_idle()

    # Cutting at the marker or mid-delta slices the atomic block.
    with pytest.raises(IncompatibleRuntimeState, match="slice the promote"):
        parent.fork(at_event=result.marker_event_id)
    with pytest.raises(IncompatibleRuntimeState, match="slice the promote"):
        parent.fork(at_event=result.applied_event_ids[0])
    # The block's final event is a valid cut (block fully included).
    ok = parent.fork(at_event=result.applied_event_ids[-1])
    assert ok.graph.get_object("note#2") is not None


def test_strict_replay_of_multi_goal_run_no_longer_diverges(tmp_path):
    # Pre-existing fragility fixed by the drain-where-recorded rule:
    # goal1, derived1, goal2, derived2 used to replay as
    # goal1, goal2, derived1, derived2 and falsely diverge.
    parent = _parent(tmp_path)
    parent.run_goal("second goal")
    parent.graph.store.flush() if hasattr(parent.graph.store, "flush") else None

    loaded = Runtime.load(
        str(tmp_path / "run.db"), run_id=parent.run_id, replay_strict=True
    )
    assert len([o for o in loaded.graph.all_objects() if o.type == "task"]) == 2


def test_pack_warning_survives_runtime_load(tmp_path):
    # A Runtime.load'ed runtime has no live pack state (packs are
    # code); warnings must derive from pack.loaded events in the logs
    # or the whole CLI path silently loses the governance signal.
    parent = _parent(tmp_path)
    fork = _fork_at_tip(parent)
    fork.load_pack(Pack(name="candidate", version="0.1"))
    fork.graph.add_object("note", {"text": "x"})
    path = str(tmp_path / "run.db")

    reloaded_parent = Runtime.load(path, run_id=parent.run_id)
    reloaded_fork = Runtime.load(path, run_id=fork.run_id)
    assert reloaded_fork.loaded_packs() == []  # no live state, by design

    plan = reloaded_parent.promote(reloaded_fork, dry_run=True)
    assert any("candidate@0.1" in w for w in plan.warnings)


def test_shared_pack_produces_no_false_warning_after_reload(tmp_path):
    # Reverse case: parent and fork both carry the pack (parent loaded
    # it before the fork point, so it's in the shared prefix). A
    # live-state comparison used to false-positive when the parent
    # was reloaded.
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("task", {"title": "base"})

    g = Graph(ids=IDGen(), clock=FrozenClock())
    parent = Runtime(g)
    parent.load_pack(Pack(name="shared", version="1.0"))
    parent.run_goal("hello")
    path = str(tmp_path / "run.db")
    parent.save_state(path)
    fork = _fork_at_tip(parent)
    fork.graph.add_object("note", {"text": "x"})

    reloaded_parent = Runtime.load(path, run_id=parent.run_id)
    plan = reloaded_parent.promote(
        Runtime.load(path, run_id=fork.run_id), dry_run=True
    )
    assert not any("shared@1.0" in w for w in plan.warnings)


def test_strict_replay_pre_promote_behavior_sees_pre_promote_state(tmp_path):
    # The verify run drains derivation BEFORE projecting the promote
    # block, so a behavior that originally fired pre-promote re-fires
    # against pre-promote state — its output stays byte-identical and
    # strict replay passes.
    from activegraph import clear_registry

    clear_registry()

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("task", {"title": "base", "status": "open"})

    @behavior(
        name="snapshotter",
        on=["object.created"],
        where={"object.type": "task"},
    )
    def snapshotter(event, graph, ctx):
        # Output depends on the task's CURRENT status: pre-promote
        # this is "open"; if the verify run projected the promote
        # block too early, it would read "done" and diverge.
        task = graph.get_object(event.payload["id"])
        graph.add_object("snapshot", {"saw_status": task.data["status"]})

    g = Graph(ids=IDGen(), clock=FrozenClock())
    parent = Runtime(g)
    parent.run_goal("hello")
    path = str(tmp_path / "run.db")
    parent.save_state(path)

    task = _task_id(parent)
    fork = parent.fork(at_event=parent.trace.events()[-1].id)
    fork.graph.patch_object(task, {"status": "done"})
    parent.promote(fork)
    parent.run_until_idle()

    loaded = Runtime.load(path, run_id=parent.run_id, replay_strict=True)
    snap = next(o for o in loaded.graph.all_objects() if o.type == "snapshot")
    assert snap.data == {"saw_status": "open"}
    assert loaded.graph.get_object(task).data["status"] == "done"


# ----------------- apply-time schema validation (v1.3 follow-up) -----


def _typed_pack():
    from pydantic import BaseModel, Field

    from activegraph.packs import ObjectType

    class Insight(BaseModel):
        text: str
        confidence: float = Field(ge=0.0, le=1.0)

    return Pack(
        name="typed",
        version="1.0",
        object_types=(ObjectType(name="insight", schema=Insight),),
    )


def test_promote_validates_delta_against_parent_schemas(tmp_path):
    # The evolution loop's inverted case: the fork created data WITHOUT
    # the pack loaded (forks don't inherit pack state), the parent HAS
    # the typed pack. Apply must fail loud pre-mutation on data that
    # violates the parent's schema — never write unvalidated state.
    from activegraph.packs import PackSchemaViolation

    parent = _parent(tmp_path)
    parent.load_pack(_typed_pack())
    fork = _fork_at_tip(parent)
    # No pack in the fork: this invalid insight (confidence > 1) is
    # accepted there as untyped data.
    bad = fork.graph.add_object("insight", {"text": "x", "confidence": 5.0})
    assert fork.graph.get_object(bad.id) is not None

    before = len(parent.graph.events)
    with pytest.raises(PackSchemaViolation):
        parent.promote(fork)
    # Pre-mutation: no marker, no delta, nothing.
    assert len(parent.graph.events) == before
    assert parent.graph.get_object(bad.id) is None


def test_promote_canonicalizes_valid_typed_delta(tmp_path):
    parent = _parent(tmp_path)
    parent.load_pack(_typed_pack())
    fork = _fork_at_tip(parent)
    ok = fork.graph.add_object("insight", {"text": "x", "confidence": 0.5})

    result = parent.promote(fork)
    assert ok.id in [o["id"] for o in result.plan.object_creates]
    promoted = parent.graph.get_object(ok.id)
    # Stored exactly as add_object would have stored it (validated,
    # canonicalized through the schema).
    assert promoted.data == {"text": "x", "confidence": 0.5}


def test_promote_of_unknown_types_passes_untyped(tmp_path):
    # Types no loaded pack declares keep v0.9 untyped semantics —
    # identical to add_object of an undeclared type.
    parent = _parent(tmp_path)
    parent.load_pack(_typed_pack())
    fork = _fork_at_tip(parent)
    n = fork.graph.add_object("freeform", {"anything": ["goes", 1]})
    parent.promote(fork)
    assert parent.graph.get_object(n.id).data == {"anything": ["goes", 1]}
