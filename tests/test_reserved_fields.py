"""Reserved-field collisions fail loud. CONTRACT v1.10 #2.

Until v1.10, ``graph.add_object`` (and the other three mutation
surfaces) silently stripped a reserved ``provenance`` key from caller
data — a caller who thought they attached provenance had attached
nothing. These tests lock the replacement behavior: every mutation
surface refuses the collision with an actionable error, nothing is
emitted by the refused call, and non-colliding data (including nested
keys named ``provenance``) is untouched.
"""

from __future__ import annotations

import pytest

from activegraph import (
    ExecutionError,
    FrozenClock,
    Graph,
    IDGen,
    ReservedFieldError,
    Runtime,
)
from activegraph.behaviors.base import Behavior
from activegraph.core.graph import RESERVED_DATA_FIELDS


def _g() -> Graph:
    return Graph(ids=IDGen(), clock=FrozenClock())


def test_reserved_set_covers_every_framework_written_field():
    # The one field the framework writes into caller-facing data today.
    # A future reserved field belongs in this table, not in ad-hoc
    # stripping — the table is what all four mutation surfaces check.
    assert RESERVED_DATA_FIELDS == frozenset({"provenance"})


@pytest.mark.parametrize("field", sorted(RESERVED_DATA_FIELDS))
def test_add_object_refuses_reserved_field_and_emits_nothing(field):
    g = _g()
    with pytest.raises(ReservedFieldError) as exc:
        g.add_object("task", {"title": "x", field: {"created_by": "evil"}})
    assert g.events == []  # the refused call left no trace in the log
    msg = str(exc.value)
    assert field in msg  # names the colliding field...
    assert "add_object" in msg  # ...and the API that refused it
    assert "evidence" in msg  # ...and points at the sanctioned channel


def test_add_relation_refuses_reserved_field():
    g = _g()
    a = g.add_object("task", {"title": "a"})
    b = g.add_object("task", {"title": "b"})
    before = len(g.events)
    with pytest.raises(ReservedFieldError) as exc:
        g.add_relation(a.id, b.id, "depends_on", {"provenance": {}})
    assert len(g.events) == before
    assert "add_relation" in str(exc.value)


def test_patch_object_refuses_reserved_field():
    g = _g()
    o = g.add_object("task", {"title": "a"})
    before = len(g.events)
    with pytest.raises(ReservedFieldError) as exc:
        g.patch_object(o.id, {"provenance": {"created_by": "evil"}})
    assert len(g.events) == before
    assert g.get_object(o.id).version == 1  # nothing applied
    assert "patch_object" in str(exc.value)


def test_propose_patch_refuses_reserved_field():
    g = _g()
    o = g.add_object("task", {"title": "a"})
    before = len(g.events)
    with pytest.raises(ReservedFieldError) as exc:
        g.propose_patch(
            o.id, "update", {"provenance": {}}, proposed_by="someone"
        )
    assert len(g.events) == before
    assert "propose_patch" in str(exc.value)


def test_nested_keys_named_provenance_are_legitimate_domain_data():
    g = _g()
    # Only TOP-LEVEL collisions shadow the framework field; a nested key
    # is the caller's own data and passes through untouched.
    o = g.add_object("doc", {"meta": {"provenance": "the archive"}})
    assert o.data["meta"]["provenance"] == "the archive"


def test_reserved_field_error_is_a_value_error_execution_error():
    err = ReservedFieldError(field="provenance", api="add_object", param="data")
    assert isinstance(err, ValueError)
    assert isinstance(err, ExecutionError)
    assert err.is_structured()
    assert err.doc_url.endswith("/errors/reserved-field-error")
    assert err.context == {
        "field": "provenance",
        "api": "add_object",
        "param": "data",
    }


def test_collision_inside_a_behavior_lands_as_behavior_failed():
    # CONTRACT #13: behavior exceptions become behavior.failed events —
    # the loud refusal follows the framework failure mode, it does not
    # crash the run.
    g = _g()

    def offender(event, graph, ctx):
        graph.add_object("task", {"provenance": {"created_by": "me"}})

    rt = Runtime(
        g, behaviors=[Behavior(name="offender", fn=offender, on=["goal.created"])]
    )
    rt.run_goal("go")
    failures = [e for e in g.events if e.type == "behavior.failed"]
    assert len(failures) == 1
    assert failures[0].payload["exception_type"] == "ReservedFieldError"
    assert rt.errors[0].behavior == "offender"
