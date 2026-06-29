"""FalkorDBGraphStore — runs the GraphStore conformance suite + a Graph
integration check against a real FalkorDB.

The store resolves its backend the same way in tests as in production:
a running server when ``FALKORDB_URL`` / ``FALKORDB_HOST`` (+ optional
``FALKORDB_PORT`` / ``FALKORDB_USERNAME`` / ``FALKORDB_PASSWORD``) are set,
otherwise the embedded ``falkordblite`` engine. The whole module is gated on
the resolved backend actually accepting a connection and a query; when it
can't (no server configured and embedded engine unavailable), every test
skips and the rest of the suite is unaffected.

To run locally against a container::

    docker run -d --rm -p 6379:6379 falkordb/falkordb:latest
    FALKORDB_HOST=localhost pytest tests/test_falkordb_store.py

Install the server client with ``pip install 'activegraph[falkordb]'``.
"""

from __future__ import annotations

import pytest


def _probe_falkordb() -> tuple[bool, str]:
    """Return (usable, reason). Usable only if a store can be built + queried."""
    try:
        from activegraph.store.falkordb import FalkorDBGraphStore
    except Exception as e:  # noqa: BLE001
        return False, f"falkordblite not importable: {type(e).__name__}: {e}"
    store = None
    try:
        store = FalkorDBGraphStore(graph_name="ag_probe")
        store.clear()
        return True, ""
    except Exception as e:  # noqa: BLE001 — backend startup / version conflict
        return False, f"embedded FalkorDB unavailable: {type(e).__name__}: {e}"
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass


_USABLE, _REASON = _probe_falkordb()
pytestmark = pytest.mark.skipif(not _USABLE, reason=_REASON)

from activegraph.store.graph_conformance import GraphStoreConformance  # noqa: E402


class TestFalkorDBGraphStoreConformance(GraphStoreConformance):
    __test__ = True

    def setup_method(self, method):
        self._store = None

    def make_store(self):
        from activegraph.store.falkordb import FalkorDBGraphStore

        # Ephemeral embedded instance; cleared between tests via cleanup().
        self._store = FalkorDBGraphStore(graph_name="ag_conformance")
        self._store.clear()
        return self._store

    def cleanup(self):
        if self._store is not None:
            self._store.clear()
            self._store.close()
            self._store = None


def test_graph_runs_on_falkordb_backend():
    from activegraph.core.graph import Graph
    from activegraph.store.falkordb import FalkorDBGraphStore

    store = FalkorDBGraphStore(graph_name="ag_integration")
    store.clear()
    try:
        g = Graph(graph_store=store)
        a = g.add_object("memo", {"text": "first"})
        b = g.add_object("memo", {"text": "second"})
        g.add_relation(a.id, b.id, "links")
        g.patch_object(a.id, {"text": "edited"})

        reread = g.get_object(a.id)
        assert reread.data["text"] == "edited"
        assert reread.version == 2
        assert {o.id for o in g.all_objects()} == {a.id, b.id}
        assert len(g.all_relations()) == 1

        g.remove_object(a.id)
        assert g.get_object(a.id) is None
        assert g.all_relations() == []
    finally:
        store.clear()
        store.close()


# --- native-edge layout checks -------------------------------------------
#
# The conformance suite pins observable behaviour; these pin the *physical*
# model: relations are native edges, dangling endpoints are bare :AGNode
# placeholders, objects are promoted in place, and placeholders are
# garbage-collected once nothing references them.


def _make_store(name: str):
    from activegraph.store.falkordb import FalkorDBGraphStore

    store = FalkorDBGraphStore(graph_name=name)
    store.clear()
    return store


def _count(store, cypher: str) -> int:
    return store._g.ro_query(cypher).result_set[0][0]


def test_relations_are_native_edges():
    from activegraph.core.graph import Object, Relation

    store = _make_store("ag_native_edges")
    try:
        store.put_object(Object(id="a", type="memo", data={}, version=1, provenance={}))
        store.put_object(Object(id="b", type="memo", data={}, version=1, provenance={}))
        store.put_relation(
            Relation(id="r1", source="a", target="b", type="links", data={}, provenance={})
        )

        # One native AGRelation edge carrying the relation kind as a property;
        # no AGRelation *nodes* exist.
        assert _count(store, "MATCH ()-[r:AGRelation]->() RETURN count(r)") == 1
        assert _count(store, "MATCH (n:AGRelation) RETURN count(n)") == 0
        kind = store._g.ro_query(
            "MATCH ()-[r:AGRelation {id: 'r1'}]->() RETURN r.type"
        ).result_set[0][0]
        assert kind == "links"
        # Endpoints are materialized objects, not placeholders.
        assert _count(store, "MATCH (n:AGNode) RETURN count(n)") == 2
        assert _count(store, "MATCH (n:AGNode) WHERE NOT n:AGObject RETURN count(n)") == 0
    finally:
        store.clear()
        store.close()


def test_dangling_relation_creates_placeholders():
    from activegraph.core.graph import Relation

    store = _make_store("ag_placeholders")
    try:
        # Neither endpoint exists as an object yet.
        store.put_relation(
            Relation(id="r1", source="x", target="y", type="links", data={}, provenance={})
        )
        # Two placeholder :AGNode nodes (no :AGObject label), joined by the edge.
        assert _count(store, "MATCH (n:AGNode) RETURN count(n)") == 2
        assert _count(store, "MATCH (n:AGObject) RETURN count(n)") == 0
        assert _count(store, "MATCH ()-[r:AGRelation]->() RETURN count(r)") == 1
        # The relation still round-trips its source/target from the endpoints.
        rel = store.get_relation("r1")
        assert (rel.source, rel.target) == ("x", "y")
        assert store.all_objects() == []
    finally:
        store.clear()
        store.close()


def test_put_object_promotes_placeholder_in_place():
    from activegraph.core.graph import Object, Relation

    store = _make_store("ag_promote")
    try:
        store.put_relation(
            Relation(id="r1", source="x", target="y", type="links", data={}, provenance={})
        )
        # Promote the placeholder 'x' into a real object.
        store.put_object(Object(id="x", type="memo", data={"v": 1}, version=1, provenance={}))

        # Still exactly two nodes — 'x' was promoted, not duplicated.
        assert _count(store, "MATCH (n:AGNode) RETURN count(n)") == 2
        assert _count(store, "MATCH (n:AGNode {id: 'x'}) RETURN count(n)") == 1
        assert store.get_object("x").data == {"v": 1}
        # The edge still connects the (now materialized) 'x' to placeholder 'y'.
        assert store.get_relation("r1").source == "x"
        assert _count(
            store, "MATCH (n:AGNode {id: 'y'}) WHERE NOT n:AGObject RETURN count(n)"
        ) == 1
    finally:
        store.clear()
        store.close()


def test_remove_object_keeps_node_as_placeholder_while_referenced():
    from activegraph.core.graph import Object, Relation

    store = _make_store("ag_demote")
    try:
        store.put_object(Object(id="a", type="memo", data={}, version=1, provenance={}))
        store.put_object(Object(id="b", type="memo", data={}, version=1, provenance={}))
        store.put_relation(
            Relation(id="r1", source="a", target="b", type="links", data={}, provenance={})
        )

        # Mirror the projector: remove the object *before* cascading relations.
        store.remove_object("a")
        # 'a' is demoted to a placeholder, not deleted, because the edge remains.
        assert store.get_object("a") is None
        assert _count(
            store, "MATCH (n:AGNode {id: 'a'}) WHERE NOT n:AGObject RETURN count(n)"
        ) == 1
        # The relation is still enumerable (so the projector can cascade it).
        assert store.get_relation("r1") is not None

        # Now the cascade removes the relation, which GCs the orphan placeholder.
        store.remove_relation("r1")
        assert _count(store, "MATCH (n:AGNode {id: 'a'}) RETURN count(n)") == 0
        # 'b' is a real object and survives even with no relations.
        assert store.get_object("b") is not None
    finally:
        store.clear()
        store.close()


def test_remove_relation_only_gcs_orphan_endpoints():
    from activegraph.core.graph import Relation

    store = _make_store("ag_orphan_gc")
    try:
        # Shared placeholder 'b' across two dangling relations.
        store.put_relation(
            Relation(id="r1", source="a", target="b", type="links", data={}, provenance={})
        )
        store.put_relation(
            Relation(id="r2", source="b", target="c", type="links", data={}, provenance={})
        )
        assert _count(store, "MATCH (n:AGNode) RETURN count(n)") == 3

        store.remove_relation("r1")
        # 'a' had no other edges -> GC'd. 'b' still anchors r2 -> kept.
        assert _count(store, "MATCH (n:AGNode {id: 'a'}) RETURN count(n)") == 0
        assert _count(store, "MATCH (n:AGNode {id: 'b'}) RETURN count(n)") == 1
        assert {r.id for r in store.all_relations()} == {"r2"}
    finally:
        store.clear()
        store.close()


# --- pattern-match push-down parity --------------------------------------
#
# Pattern matching drives its chain traversal through the pushed-down
# Graph.objects(type=) / Graph.relations(source=, target=, type=) hooks, so on
# FalkorDB each seed and hop is a scoped Cypher query rather than a full
# projection scan. These build the *same* graph on the in-memory default and
# on FalkorDB and assert the matcher returns identical bindings for a range of
# patterns (node-type seed, both hop directions, multi-hop + node-property +
# WHERE, and a NOT EXISTS sub-pattern that recurses through the same hooks).


def _populate_pattern_graph(g) -> None:
    """Build a small fixed graph on any ``Graph``. Both backends start from a
    fresh, identically-seeded ``IDGen``, so object/relation ids line up and
    bindings can be compared directly.
    """
    a = g.add_object("person", {"team": "x", "score": 1})
    b = g.add_object("person", {"team": "y", "score": 9})
    c = g.add_object("person", {"team": "z", "score": 3})
    g.add_object("memo", {"text": "noise"})
    g.add_relation(a.id, b.id, "knows")
    g.add_relation(b.id, c.id, "knows")
    g.add_relation(a.id, c.id, "dislikes")


def _norm(matches) -> list:
    """Order-independent, comparable representation of a match list."""
    return sorted(tuple(sorted(m.bindings.items())) for m in matches)


def test_pattern_matching_parity_falkordb_vs_inmemory():
    from activegraph.core.graph import Graph
    from activegraph.store.falkordb import FalkorDBGraphStore
    from activegraph.runtime.patterns import parse

    patterns = [
        # Node-type seed -> pushed down via Graph.objects(type=).
        "(p:person)",
        # Node-type seed + equality property (property filter stays in Python).
        "(p:person {team: 'x'})",
        # One hop, right direction -> relations(source=, type=).
        "(a:person)-[:knows]->(b:person)",
        # One hop, left direction -> relations(target=, type=).
        "(b:person)<-[:knows]-(a:person)",
        # Multi-hop chain + node property + WHERE comparison.
        "(a:person {team: 'x'})-[:knows]->(b:person)-[:knows]->(c:person) "
        "WHERE c.score > 2",
        # NOT EXISTS sub-pattern recurses through the same pushed-down hooks.
        "(a:person)-[:knows]->(b:person) "
        "WHERE NOT EXISTS { (b)-[:knows]->(c:person) }",
    ]

    store = FalkorDBGraphStore(graph_name="ag_pattern_parity")
    store.clear()
    try:
        g_mem = Graph()
        g_fdb = Graph(graph_store=store)
        _populate_pattern_graph(g_mem)
        _populate_pattern_graph(g_fdb)

        for src in patterns:
            matcher = parse(src).compile()
            mem = _norm(matcher.matches(event=None, graph=g_mem))
            fdb = _norm(matcher.matches(event=None, graph=g_fdb))
            assert fdb == mem, f"pattern push-down diverged for: {src}"
    finally:
        store.clear()
        store.close()


# --- type-scoped read push-down parity -----------------------------------
#
# find_objects_in_types (OR-of-types), the view builder's include_types path,
# and RelationBehavior matching all push a type filter into the store. These
# build the *same* graph on both backends and assert identical results, so a
# FalkorDB query can never silently drift from the in-memory definition.


def _populate_mixed_graph(g) -> dict:
    """Build a mixed-type graph on any ``Graph`` and return id maps. Both
    backends start from an identically-seeded default ``IDGen`` so ids line
    up and can be compared directly.
    """
    a = g.add_object("claim", {"text": "a"})
    b = g.add_object("claim", {"text": "b"})
    c = g.add_object("risk", {"text": "c"})
    d = g.add_object("memo", {"text": "d"})
    e = g.add_object("evidence", {"text": "e"})
    g.add_relation(e.id, a.id, "supports")
    g.add_relation(e.id, b.id, "supports")
    g.add_relation(a.id, c.id, "raises")
    return {"a": a.id, "b": b.id, "c": c.id, "d": d.id, "e": e.id}


def test_find_objects_in_types_parity_falkordb_vs_inmemory():
    from activegraph.core.graph import Graph
    from activegraph.store.falkordb import FalkorDBGraphStore

    store = FalkorDBGraphStore(graph_name="ag_types_parity")
    store.clear()
    try:
        g_mem = Graph()
        g_fdb = Graph(graph_store=store)
        _populate_mixed_graph(g_mem)
        _populate_mixed_graph(g_fdb)

        type_sets = [
            [],
            ["claim"],
            ["claim", "risk"],
            ["risk", "claim", "nope"],
            ["memo", "evidence"],
            ["nope", "gone"],
        ]
        for types in type_sets:
            mem = sorted(o.id for o in g_mem.objects_in_types(types))
            fdb = sorted(o.id for o in g_fdb.objects_in_types(types))
            assert fdb == mem, f"objects_in_types diverged for: {types}"
    finally:
        store.clear()
        store.close()


def test_view_builder_include_types_parity_falkordb_vs_inmemory():
    from activegraph.behaviors.base import Behavior
    from activegraph.core.event import Event
    from activegraph.core.graph import Graph
    from activegraph.runtime.view_builder import build_view
    from activegraph.store.falkordb import FalkorDBGraphStore

    store = FalkorDBGraphStore(graph_name="ag_view_parity")
    store.clear()
    try:
        g_mem = Graph()
        g_fdb = Graph(graph_store=store)
        _populate_mixed_graph(g_mem)
        _populate_mixed_graph(g_fdb)

        event = Event(id="evt_1", type="object.created")
        specs = [
            {"include_types": ["claim"]},
            {"include_types": ["claim", "risk"]},
            {"include_types": ["nope"]},
        ]
        for spec in specs:
            beh = Behavior(name="v", fn=lambda *a: None, view_spec=spec)
            v_mem = build_view(beh, event, g_mem)
            v_fdb = build_view(beh, event, g_fdb)
            assert sorted(o.id for o in v_fdb.objects()) == sorted(
                o.id for o in v_mem.objects()
            ), f"view objects diverged for: {spec}"
            assert sorted(r.id for r in v_fdb.relations()) == sorted(
                r.id for r in v_mem.relations()
            ), f"view relations diverged for: {spec}"
    finally:
        store.clear()
        store.close()


def test_relation_behavior_matching_parity_falkordb_vs_inmemory():
    from activegraph.behaviors.base import RelationBehavior
    from activegraph.core.event import Event
    from activegraph.core.graph import Graph
    from activegraph.runtime.registry import Registry
    from activegraph.store.falkordb import FalkorDBGraphStore

    store = FalkorDBGraphStore(graph_name="ag_relmatch_parity")
    store.clear()
    try:
        g_mem = Graph()
        g_fdb = Graph(graph_store=store)
        ids_mem = _populate_mixed_graph(g_mem)
        ids_fdb = _populate_mixed_graph(g_fdb)
        assert ids_mem == ids_fdb  # ids line up across backends

        rb = RelationBehavior(
            name="r", fn=lambda *a: None, relation_type="supports", on=["claim.touched"]
        )

        def matched_ids(reg, event, g):
            triples = reg.match(event, g)
            return sorted(r.id for (_b, rels, _m) in triples for r in rels)

        # An event that references claim "a" -> only the supports edge into "a".
        event = Event(
            id="evt_1", type="claim.touched", payload={"object": {"id": ids_mem["a"]}}
        )
        reg = Registry([rb])
        assert matched_ids(reg, event, g_fdb) == matched_ids(reg, event, g_mem)

        # An event referencing the shared source "e" -> both supports edges.
        event2 = Event(
            id="evt_2", type="claim.touched", payload={"object": {"id": ids_mem["e"]}}
        )
        assert matched_ids(reg, event2, g_fdb) == matched_ids(reg, event2, g_mem)
    finally:
        store.clear()
        store.close()
