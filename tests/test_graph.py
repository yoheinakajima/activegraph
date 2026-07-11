"""Graph primitive tests. Validates CONTRACT #2 (event log truth),
#4 (versioning), #5 (provenance stamping)."""

import pytest

from activegraph import FrozenClock, Graph, IDGen


def _g():
    return Graph(ids=IDGen(), clock=FrozenClock())


def test_add_object_emits_event():
    g = _g()
    o = g.add_object("task", {"title": "x", "status": "open"})
    assert o.id == "task#1"
    assert o.version == 1
    events = g.events
    assert len(events) == 1
    assert events[0].type == "object.created"
    assert events[0].payload["object"]["id"] == "task#1"


def test_provenance_is_stamped_by_runtime_not_behavior():
    g = _g()
    # CONTRACT v1.10 #2: a "behavior" trying to inject provenance via
    # data is refused loudly (pre-v1.10 it was silently stripped, so the
    # caller believed they had attached provenance and hadn't).
    from activegraph import ReservedFieldError

    with pytest.raises(ReservedFieldError):
        g.add_object(
            "task",
            {"title": "x", "provenance": {"created_by": "evil"}},
            actor="planner",
        )
    # Nothing landed: the refused call emitted no event.
    assert g.events == []
    # The sanctioned path stamps provenance from the kwargs.
    o = g.add_object("task", {"title": "x"}, actor="planner")
    assert o.provenance["created_by"] == "planner"
    assert "provenance" not in o.data


def test_add_relation_emits_event_and_projects():
    g = _g()
    a = g.add_object("task", {"title": "a"})
    b = g.add_object("task", {"title": "b"})
    r = g.add_relation(a.id, b.id, "depends_on")
    assert r.id.startswith("rel_")
    assert r.source == a.id
    assert r.target == b.id
    types = [e.type for e in g.events]
    assert types == ["object.created", "object.created", "relation.created"]


def test_remove_object_cascades_relations():
    g = _g()
    a = g.add_object("task", {})
    b = g.add_object("task", {})
    g.add_relation(a.id, b.id, "depends_on")
    assert len(g.all_relations()) == 1
    g.remove_object(a.id)
    assert g.get_object(a.id) is None
    assert len(g.all_relations()) == 0


def test_query_filters_by_type_and_where():
    g = _g()
    g.add_object("claim", {"text": "x", "confidence": 0.9})
    g.add_object("claim", {"text": "y", "confidence": 0.4})
    g.add_object("task", {"title": "t"})

    high = g.query(object_type="claim", where={"confidence": {">": 0.5}})
    assert len(high) == 1
    assert high[0].data["text"] == "x"


# v1.0.3 #1: graph.objects(type=...) as the canonical query API,
# matching View.objects(type=...) so call sites read the same inside
# and outside behaviors. graph.query(object_type=...) stays as a
# backward-compatible alias.


def test_objects_filters_by_type():
    g = _g()
    g.add_object("claim", {"text": "x"})
    g.add_object("claim", {"text": "y"})
    g.add_object("task", {"title": "t"})

    claims = g.objects(type="claim")
    assert {o.data["text"] for o in claims} == {"x", "y"}
    assert all(o.type == "claim" for o in claims)


def test_objects_filters_by_where():
    g = _g()
    g.add_object("claim", {"text": "x", "confidence": 0.9})
    g.add_object("claim", {"text": "y", "confidence": 0.4})

    high = g.objects(type="claim", where={"confidence": {">": 0.5}})
    assert len(high) == 1
    assert high[0].data["text"] == "x"


def test_objects_with_no_kwargs_returns_every_object():
    # Same semantics as View.objects() with no args: full slice.
    # graph.objects() is a strict superset of graph.all_objects()
    # (which is kept for callers that prefer the explicit-no-filter
    # name and for backward compat).
    g = _g()
    g.add_object("claim", {"text": "x"})
    g.add_object("task", {"title": "t"})

    assert {o.id for o in g.objects()} == {o.id for o in g.all_objects()}


def test_objects_and_query_return_the_same_results():
    g = _g()
    g.add_object("claim", {"text": "x", "confidence": 0.9})
    g.add_object("claim", {"text": "y", "confidence": 0.4})
    g.add_object("task", {"title": "t"})

    new = g.objects(type="claim", where={"confidence": {">": 0.5}})
    old = g.query(object_type="claim", where={"confidence": {">": 0.5}})
    assert [o.id for o in new] == [o.id for o in old]


def test_query_alias_still_works_with_positional_arg():
    # External code that passed the type positionally to query()
    # continues to work — the alias keeps the (object_type=, where=)
    # kwarg shape and the parameter order from v1.0.2.
    g = _g()
    g.add_object("claim", {"text": "x"})
    g.add_object("task", {"title": "t"})

    assert {o.type for o in g.query("claim")} == {"claim"}


# v1.0.4 #1: graph.relations(source=, target=, type=) as the canonical
# filter API, decomposing the v0 get_relations(object_id=, direction=)
# axis into separate source/target slots. The contract claim is the
# eight filter combinations enumerated in CONTRACT v1.0.4 #1; the tests
# anchor on each combination directly, not on equivalence to the alias.
# The alias-still-works check is its own test.


def _three_node_graph():
    g = _g()
    a = g.add_object("task", {"k": "a"})
    b = g.add_object("task", {"k": "b"})
    c = g.add_object("task", {"k": "c"})
    rel_ab_dep = g.add_relation(a.id, b.id, "depends_on")
    rel_ac_dep = g.add_relation(a.id, c.id, "depends_on")
    rel_bc_blk = g.add_relation(b.id, c.id, "blocks")
    rel_ba_dep = g.add_relation(b.id, a.id, "depends_on")
    return g, a, b, c, rel_ab_dep, rel_ac_dep, rel_bc_blk, rel_ba_dep


def test_relations_no_kwargs_returns_every_relation():
    # Contract row 1: (None, None, None) -> every relation.
    g, _, _, _, *rels = _three_node_graph()
    out = g.relations()
    assert {r.id for r in out} == {r.id for r in rels}


def test_relations_source_only_returns_outgoing_from_source():
    # Contract row 2: (A, None, None) -> relations where source == A.
    g, a, b, c, rel_ab, rel_ac, _, _ = _three_node_graph()
    out = g.relations(source=a.id)
    assert {r.id for r in out} == {rel_ab.id, rel_ac.id}
    assert all(r.source == a.id for r in out)


def test_relations_target_only_returns_incoming_to_target():
    # Contract row 3: (None, B, None) -> relations where target == B.
    g, a, b, c, rel_ab, _, _, _ = _three_node_graph()
    out = g.relations(target=b.id)
    assert {r.id for r in out} == {rel_ab.id}
    assert all(r.target == b.id for r in out)


def test_relations_source_and_target_returns_intersection():
    # Contract row 4: (A, B, None) -> relations from A to B.
    g, a, b, c, rel_ab, _, _, rel_ba = _three_node_graph()
    out = g.relations(source=a.id, target=b.id)
    assert {r.id for r in out} == {rel_ab.id}
    # Not the reverse direction.
    assert rel_ba.id not in {r.id for r in out}


def test_relations_type_only_returns_every_relation_of_type():
    # Contract row 5: (None, None, T) -> every relation of type T.
    g, *_, rel_bc_blk, _ = _three_node_graph()
    out = g.relations(type="blocks")
    assert {r.id for r in out} == {rel_bc_blk.id}
    assert all(r.type == "blocks" for r in out)


def test_relations_source_and_type_returns_outgoing_of_type():
    # Contract row 6: (A, None, T) -> relations from A of type T.
    g, a, _, _, rel_ab, rel_ac, _, _ = _three_node_graph()
    out = g.relations(source=a.id, type="depends_on")
    assert {r.id for r in out} == {rel_ab.id, rel_ac.id}


def test_relations_target_and_type_returns_incoming_of_type():
    # Contract row 7: (None, B, T) -> relations to B of type T.
    g, a, b, _, rel_ab, _, _, _ = _three_node_graph()
    out = g.relations(target=b.id, type="depends_on")
    assert {r.id for r in out} == {rel_ab.id}


def test_relations_source_target_and_type_most_specific():
    # Contract row 8: (A, B, T) -> relations from A to B of type T.
    g, a, b, _, rel_ab, _, _, rel_ba = _three_node_graph()
    out = g.relations(source=a.id, target=b.id, type="depends_on")
    assert {r.id for r in out} == {rel_ab.id}
    # Different-direction same-type relation is excluded.
    assert rel_ba.id not in {r.id for r in out}
    # Wrong-type from A->B does not exist; sanity check empty for a wrong type.
    assert g.relations(source=a.id, target=b.id, type="blocks") == []


def test_get_relations_alias_still_works():
    # Backward-compat: the v0 shape continues to work byte-identically.
    # This is the alias relationship, not the contract claim — kept as
    # its own test per CONTRACT v1.0.4 #1's Standing Rule §2 carve-out.
    g, a, b, c, rel_ab, rel_ac, _, rel_ba = _three_node_graph()
    out = g.get_relations(object_id=a.id, direction="outgoing")
    assert {r.id for r in out} == {rel_ab.id, rel_ac.id}
    incoming = g.get_relations(object_id=a.id, direction="incoming")
    assert {r.id for r in incoming} == {rel_ba.id}
    both = g.get_relations(object_id=a.id, direction="both")
    assert {r.id for r in both} == {rel_ab.id, rel_ac.id, rel_ba.id}


def test_neighborhood_walks_to_depth():
    g = _g()
    a = g.add_object("task", {})
    b = g.add_object("task", {})
    c = g.add_object("task", {})
    g.add_relation(a.id, b.id, "depends_on")
    g.add_relation(b.id, c.id, "depends_on")

    objs, rels = g.neighborhood(a.id, depth=1)
    assert {o.id for o in objs} == {a.id, b.id}
    assert len(rels) == 1

    objs2, rels2 = g.neighborhood(a.id, depth=2)
    assert {o.id for o in objs2} == {a.id, b.id, c.id}
    assert len(rels2) == 2


def test_emit_is_only_mutator_for_external_state():
    """Hand-built event is fine; the projector handles it."""
    from activegraph import Event

    g = _g()
    g.emit(
        Event(
            id="evt_999",
            type="object.created",
            payload={
                "object": {
                    "id": "manual#1",
                    "type": "manual",
                    "data": {},
                    "version": 1,
                    "provenance": {},
                },
                "id": "manual#1",
            },
            actor="test",
        )
    )
    assert g.get_object("manual#1") is not None
