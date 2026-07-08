"""Compaction phase 1: snapshot + archive tier + the pin set.

CONTRACT v1.5 #2. The property that must hold above all others: a
promoted-from fork log survives every retention operation, because
the pin set dominates policy unconditionally.
"""

from __future__ import annotations

import pytest

from activegraph import Graph, IDGen, FrozenClock, Runtime, behavior, clear_registry
from activegraph.store.retention import (
    RetentionPinnedError,
    SnapshotIntegrityError,
    compact,
    pins,
    retire,
    verify_snapshot,
)
from activegraph.store.sqlite import SQLiteEventStore


def _seeded_runtime(tmp_path, name="run.db"):
    clear_registry()

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("task", {"title": event.payload.get("goal", "")})

    rt = Runtime(Graph(ids=IDGen(), clock=FrozenClock()))
    rt.run_goal("first")
    path = str(tmp_path / name)
    rt.save_state(path)
    return path, rt


# ------------------------------------------------------ compact


def test_compact_preserves_state_and_stays_appendable(tmp_path):
    path, rt = _seeded_runtime(tmp_path)
    objects_before = {
        o.id: o.data for o in rt.graph.all_objects()
    }
    run_id = rt.run_id
    del rt

    snapshot_id = compact(path, run_id)

    # The hot log is now snapshot-only; the prefix sits in the archive.
    store = SQLiteEventStore(path, run_id=run_id)
    hot = list(store.iter_events())
    assert [e.type for e in hot] == ["runtime.snapshot"]
    assert hot[0].id == snapshot_id
    assert store.has_archived()

    # Loading reconstructs identical state from the snapshot...
    loaded = Runtime.load(path, run_id=run_id)
    assert {o.id: o.data for o in loaded.graph.all_objects()} == objects_before

    # ...and the run stays appendable: new goals work, ids don't
    # collide with archived history, and a second load sees both.
    loaded.run_goal("second")
    again = Runtime.load(path, run_id=run_id)
    titles = sorted(o.data["title"] for o in again.graph.all_objects())
    assert titles == ["first", "second"]
    assert len({o.id for o in again.graph.all_objects()}) == 2


def test_strict_replay_verifies_the_post_snapshot_suffix(tmp_path):
    path, rt = _seeded_runtime(tmp_path)
    run_id = rt.run_id
    del rt
    compact(path, run_id)

    loaded = Runtime.load(path, run_id=run_id)
    loaded.run_goal("post-compact goal")

    verified = Runtime.load(path, run_id=run_id, replay_strict=True)
    titles = sorted(o.data["title"] for o in verified.graph.all_objects())
    assert titles == ["first", "post-compact goal"]


def test_verify_snapshot_audits_the_archive(tmp_path):
    path, rt = _seeded_runtime(tmp_path)
    run_id = rt.run_id
    del rt
    compact(path, run_id)
    assert verify_snapshot(path, run_id) is True


def test_corrupted_snapshot_blob_fails_loud_on_load(tmp_path):
    path, rt = _seeded_runtime(tmp_path)
    run_id = rt.run_id
    del rt
    compact(path, run_id)

    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("UPDATE snapshots SET blob = '{\"objects\":[],\"relations\":[]}'")
    conn.commit()
    conn.close()

    with pytest.raises(SnapshotIntegrityError):
        Runtime.load(path, run_id=run_id)


def test_fork_below_the_horizon_refuses_and_above_works(tmp_path):
    from activegraph.store.errors import EventNotFoundError

    path, rt = _seeded_runtime(tmp_path)
    run_id = rt.run_id
    pre_snapshot_event = rt.graph.events[0].id
    del rt
    compact(path, run_id)

    loaded = Runtime.load(path, run_id=run_id)
    loaded.run_goal("post-compact")

    with pytest.raises(EventNotFoundError, match="compaction horizon"):
        loaded.fork(at_event=pre_snapshot_event)

    reloaded = Runtime.load(path, run_id=run_id)
    fork = reloaded.fork(at_event=reloaded.trace.events()[-1].id)
    titles = sorted(o.data["title"] for o in fork.graph.all_objects())
    assert titles == ["first", "post-compact"]


# ------------------------------------------------------- the pin set


def test_promoted_from_fork_log_survives_retention(tmp_path):
    # THE property: a fork referenced by a live promote.applied marker
    # is pinned whole. Neither retire nor compact may touch it.
    path, parent = _seeded_runtime(tmp_path)
    fork = parent.fork(at_event=parent.trace.events()[-1].id)
    fork.graph.add_object("note", {"text": "adopted"})
    parent.promote(fork)
    parent.run_until_idle()
    fork_run = fork.run_id
    del parent, fork

    reasons = pins(path, fork_run)
    assert any("promoted-from" in r for r in reasons)

    with pytest.raises(RetentionPinnedError, match="promoted-from"):
        retire(path, fork_run)
    with pytest.raises(RetentionPinnedError, match="promoted-from"):
        compact(path, fork_run)

    # The fork's whole log is still hot and readable: two-hop
    # provenance (entity -> marker -> fork log) stays walkable.
    fork_view = Runtime.load(path, run_id=fork_run, behaviors=[])
    assert len(fork_view.graph.events) > 0
    assert any(
        o.data.get("text") == "adopted"
        for o in fork_view.graph.all_objects()
    )


def test_unpromoted_rejected_fork_retires_cleanly(tmp_path):
    path, parent = _seeded_runtime(tmp_path)
    rejected = parent.fork(at_event=parent.trace.events()[-1].id)
    rejected.graph.add_object("note", {"text": "never adopted"})
    fork_run = rejected.run_id
    parent_run = parent.run_id
    n_parent = len(parent.graph.events)
    del parent, rejected

    assert pins(path, fork_run) == []
    moved = retire(path, fork_run)
    assert moved > 0

    store = SQLiteEventStore(path, run_id=fork_run)
    assert list(store.iter_events()) == []
    assert store.has_archived()  # archived, never deleted
    # Parent untouched by the child's retirement.
    parent_view = Runtime.load(path, run_id=parent_run, behaviors=[])
    assert len(parent_view.graph.events) == n_parent


def test_live_lineage_pins_the_parent_until_children_retire(tmp_path):
    path, parent = _seeded_runtime(tmp_path)
    child = parent.fork(at_event=parent.trace.events()[-1].id)
    parent_run, child_run = parent.run_id, child.run_id
    del parent, child

    with pytest.raises(RetentionPinnedError, match="live-lineage"):
        retire(path, parent_run)
    with pytest.raises(RetentionPinnedError, match="live-lineage"):
        compact(path, parent_run)

    retire(path, child_run)  # the child itself is unpinned
    # With the child retired, the parent's lineage pin is released.
    assert not any("live-lineage" in r for r in pins(path, parent_run))
    compact(path, parent_run)  # now allowed


def test_pending_machinery_pins_compaction(tmp_path):
    from pydantic import BaseModel

    from activegraph.packs import ObjectType, Pack, PackPolicy

    clear_registry()

    class Risky(BaseModel):
        action: str

    pack = Pack(
        name="gated",
        version="1.0",
        object_types=(ObjectType(name="risky", schema=Risky),),
        policies=(PackPolicy(name="gate", requires_approval=("risky",)),),
    )

    @behavior(name="proposer", on=["goal.created"])
    def proposer(event, graph, ctx):
        ctx.propose_object("risky", {"action": "wipe"}, reason="test")

    rt = Runtime(Graph(ids=IDGen(), clock=FrozenClock()))
    rt.load_pack(pack)
    rt.run_goal("go")
    path = str(tmp_path / "run.db")
    rt.save_state(path)
    run_id = rt.run_id
    aid = rt.pending_approvals()[0].id
    del rt

    with pytest.raises(RetentionPinnedError, match="pending-approvals"):
        compact(path, run_id)

    # Resolving the approval releases the pin.
    loaded = Runtime.load(path, run_id=run_id, behaviors=[])
    loaded.approve(aid, approved_by="owner")
    del loaded
    assert not any("pending-approvals" in r for r in pins(path, run_id))


def test_trace_renders_the_snapshot_line(tmp_path):
    path, rt = _seeded_runtime(tmp_path)
    run_id = rt.run_id
    del rt
    compact(path, run_id)
    loaded = Runtime.load(path, run_id=run_id)
    loaded.run_goal("later")
    lines = "\n".join(loaded.trace.lines())
    assert "runtime.snapshot" in lines
