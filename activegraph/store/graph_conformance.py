"""GraphStore conformance suite. CONTRACT v1.2 #5.

A reusable, pytest-collectable base class that exercises any
:class:`~activegraph.core.graph_store.GraphStore` implementation against the
interface. Concrete subclasses override ``make_store()`` and ``cleanup()``;
the tests run identically.

Mirrors :mod:`activegraph.store.conformance` (the EventStore suite). The
in-memory and FalkorDB graph stores both run through this suite; any future
backend gets free coverage by subclassing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from activegraph.core.graph import Object, Relation
from activegraph.core.patch import Patch


class GraphStoreConformance(ABC):
    """Mix into a pytest test class to inherit the full suite.

    Subclasses MUST implement::

        def make_store(self) -> GraphStore: ...

    and MAY override ``cleanup()`` to tear down resources after each test.
    """

    __test__ = False  # do not collect the base; subclasses override.

    @abstractmethod
    def make_store(self) -> Any:
        """Return a fresh, empty GraphStore. Called per test."""

    def cleanup(self) -> None:
        """Tear down any resources after a test. Default: no-op."""

    # ---- helpers ----

    def _obj(self, oid: str, *, type_: str = "memo", version: int = 1) -> Object:
        return Object(
            id=oid,
            type=type_,
            data={"text": f"hello {oid}", "n": 1},
            version=version,
            provenance={"created_by": "test", "run_id": "run_x"},
        )

    def _rel(self, rid: str, src: str, tgt: str, *, type_: str = "links") -> Relation:
        return Relation(
            id=rid,
            source=src,
            target=tgt,
            type=type_,
            data={"weight": 0.5},
            provenance={"created_by": "test"},
        )

    def _patch(self, pid: str, target: str, *, status: str = "proposed") -> Patch:
        return Patch(
            id=pid,
            target=target,
            op="update",
            value={"text": "new"},
            expected_version=1,
            proposed_by="test",
            rationale="because",
            evidence=["evt_1"],
            status=status,
            provenance={"created_by": "test"},
        )

    # ---- objects ----

    def test_put_get_object_round_trip(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("obj_1"))
            got = store.get_object("obj_1")
            assert got is not None
            assert got.id == "obj_1"
            assert got.type == "memo"
            assert got.data == {"text": "hello obj_1", "n": 1}
            assert got.version == 1
            assert got.provenance["created_by"] == "test"
        finally:
            self.cleanup()

    def test_get_unknown_object_returns_none(self) -> None:
        try:
            store = self.make_store()
            assert store.get_object("nope") is None
        finally:
            self.cleanup()

    def test_put_object_overwrites(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("obj_1", version=1))
            store.put_object(self._obj("obj_1", version=2))
            got = store.get_object("obj_1")
            assert got is not None
            assert got.version == 2
            assert len(store.all_objects()) == 1
        finally:
            self.cleanup()

    def test_remove_object(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("obj_1"))
            store.remove_object("obj_1")
            assert store.get_object("obj_1") is None
            # removing an unknown id is a no-op, not an error
            store.remove_object("obj_1")
        finally:
            self.cleanup()

    def test_all_objects(self) -> None:
        try:
            store = self.make_store()
            assert store.all_objects() == []
            store.put_object(self._obj("obj_1"))
            store.put_object(self._obj("obj_2"))
            ids = sorted(o.id for o in store.all_objects())
            assert ids == ["obj_1", "obj_2"]
        finally:
            self.cleanup()

    # ---- relations ----

    def test_put_get_relation_round_trip(self) -> None:
        try:
            store = self.make_store()
            store.put_relation(self._rel("rel_1", "obj_1", "obj_2"))
            got = store.get_relation("rel_1")
            assert got is not None
            assert got.source == "obj_1"
            assert got.target == "obj_2"
            assert got.type == "links"
            assert got.data == {"weight": 0.5}
        finally:
            self.cleanup()

    def test_remove_relation_and_all(self) -> None:
        try:
            store = self.make_store()
            store.put_relation(self._rel("rel_1", "a", "b"))
            store.put_relation(self._rel("rel_2", "b", "c"))
            assert sorted(r.id for r in store.all_relations()) == ["rel_1", "rel_2"]
            store.remove_relation("rel_1")
            assert store.get_relation("rel_1") is None
            assert [r.id for r in store.all_relations()] == ["rel_2"]
        finally:
            self.cleanup()

    # ---- patches ----

    def test_put_get_patch_round_trip(self) -> None:
        try:
            store = self.make_store()
            store.put_patch(self._patch("patch_1", "obj_1"))
            got = store.get_patch("patch_1")
            assert got is not None
            assert got.target == "obj_1"
            assert got.op == "update"
            assert got.value == {"text": "new"}
            assert got.status == "proposed"
            assert got.evidence == ["evt_1"]
        finally:
            self.cleanup()

    def test_patch_status_overwrite(self) -> None:
        try:
            store = self.make_store()
            store.put_patch(self._patch("patch_1", "obj_1", status="proposed"))
            store.put_patch(self._patch("patch_1", "obj_1", status="applied"))
            got = store.get_patch("patch_1")
            assert got is not None
            assert got.status == "applied"
            assert len(store.all_patches()) == 1
        finally:
            self.cleanup()

    # ---- lifecycle ----

    def test_clear(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("obj_1"))
            store.put_relation(self._rel("rel_1", "obj_1", "obj_2"))
            store.put_patch(self._patch("patch_1", "obj_1"))
            store.clear()
            assert store.all_objects() == []
            assert store.all_relations() == []
            assert store.all_patches() == []
        finally:
            self.cleanup()

    # ---- query hooks (pushdown) ----

    def test_find_objects(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("o1", type_="memo"))
            store.put_object(self._obj("o2", type_="memo"))
            store.put_object(self._obj("o3", type_="note"))
            # None -> every object.
            assert sorted(o.id for o in store.find_objects()) == ["o1", "o2", "o3"]
            # Type filter.
            assert sorted(o.id for o in store.find_objects("memo")) == ["o1", "o2"]
            assert [o.id for o in store.find_objects("note")] == ["o3"]
            # Unknown type -> empty.
            assert store.find_objects("nope") == []
        finally:
            self.cleanup()

    def test_find_objects_in_types(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("o1", type_="memo"))
            store.put_object(self._obj("o2", type_="note"))
            store.put_object(self._obj("o3", type_="memo"))
            store.put_object(self._obj("o4", type_="task"))

            def ids(objs):
                return sorted(o.id for o in objs)

            # OR across the requested types.
            assert ids(store.find_objects_in_types(["memo"])) == ["o1", "o3"]
            assert ids(store.find_objects_in_types(["memo", "task"])) == [
                "o1",
                "o3",
                "o4",
            ]
            # A type with no objects contributes nothing; order of the type
            # list does not change membership.
            assert ids(store.find_objects_in_types(["task", "memo", "nope"])) == [
                "o1",
                "o3",
                "o4",
            ]
            # Empty request -> empty result (not "every object").
            assert store.find_objects_in_types([]) == []
            # Wholly unknown types -> empty.
            assert store.find_objects_in_types(["nope", "gone"]) == []
        finally:
            self.cleanup()

    def test_find_relations(self) -> None:
        try:
            store = self.make_store()
            # Dangling endpoints on purpose: filters must not depend on the
            # endpoints existing as objects.
            store.put_relation(self._rel("r1", "a", "b", type_="links"))
            store.put_relation(self._rel("r2", "a", "c", type_="cites"))
            store.put_relation(self._rel("r3", "b", "a", type_="links"))

            def ids(rels):
                return sorted(r.id for r in rels)

            # No filter -> all.
            assert ids(store.find_relations()) == ["r1", "r2", "r3"]
            # By source.
            assert ids(store.find_relations(source="a")) == ["r1", "r2"]
            # By target.
            assert ids(store.find_relations(target="a")) == ["r3"]
            # By type.
            assert ids(store.find_relations(type="links")) == ["r1", "r3"]
            # Combined AND.
            assert ids(store.find_relations(source="a", type="links")) == ["r1"]
            assert ids(store.find_relations(source="a", target="b")) == ["r1"]
            # No match.
            assert store.find_relations(source="z") == []
        finally:
            self.cleanup()

    def test_neighborhood_not_an_object(self) -> None:
        try:
            store = self.make_store()
            # Unknown start, and a start that exists only as a placeholder.
            store.put_relation(self._rel("r1", "ghost", "other"))
            assert store.neighborhood("missing") == ([], [])
            assert store.neighborhood("ghost") == ([], [])
        finally:
            self.cleanup()

    def test_neighborhood_depth_zero(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("o1"))
            store.put_object(self._obj("o2"))
            store.put_relation(self._rel("r1", "o1", "o2"))
            objs, rels = store.neighborhood("o1", depth=0)
            assert [o.id for o in objs] == ["o1"]
            assert rels == []
        finally:
            self.cleanup()

    def test_neighborhood_walks_through_placeholder(self) -> None:
        try:
            store = self.make_store()
            # o1 -> p (placeholder) -> o2 ; plus a far object o3 two hops past o2.
            store.put_object(self._obj("o1"))
            store.put_object(self._obj("o2"))
            store.put_object(self._obj("o3"))
            store.put_relation(self._rel("r1", "o1", "p"))
            store.put_relation(self._rel("r2", "p", "o2"))
            store.put_relation(self._rel("r3", "o2", "o3"))

            # depth 1: edges touching o1 -> r1; reachable objects exclude the
            # placeholder p (not an object) but include o1.
            objs, rels = store.neighborhood("o1", depth=1)
            assert sorted(o.id for o in objs) == ["o1"]
            assert sorted(r.id for r in rels) == ["r1"]

            # depth 2: walk o1 -> p -> o2 ; edges touching the depth<=1 frontier
            # are r1 and r2; o2 is now reachable (distance 2).
            objs, rels = store.neighborhood("o1", depth=2)
            assert sorted(o.id for o in objs) == ["o1", "o2"]
            assert sorted(r.id for r in rels) == ["r1", "r2"]

            # depth 3: reaches o3; r3 is now traversed too.
            objs, rels = store.neighborhood("o1", depth=3)
            assert sorted(o.id for o in objs) == ["o1", "o2", "o3"]
            assert sorted(r.id for r in rels) == ["r1", "r2", "r3"]
        finally:
            self.cleanup()

    def test_neighborhood_handles_cycle(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("a"))
            store.put_object(self._obj("b"))
            store.put_object(self._obj("c"))
            # Cycle a -> b -> c -> a.
            store.put_relation(self._rel("ab", "a", "b"))
            store.put_relation(self._rel("bc", "b", "c"))
            store.put_relation(self._rel("ca", "c", "a"))

            objs, rels = store.neighborhood("a", depth=1)
            # Undirected: a touches ab (a->b) and ca (c->a); b and c are real
            # objects one hop away, so both appear. bc touches neither a-frontier
            # node yet, so it is not collected at depth 1.
            assert sorted(o.id for o in objs) == ["a", "b", "c"]
            assert sorted(r.id for r in rels) == ["ab", "ca"]

            objs, rels = store.neighborhood("a", depth=2)
            assert sorted(o.id for o in objs) == ["a", "b", "c"]
            assert sorted(r.id for r in rels) == ["ab", "bc", "ca"]
        finally:
            self.cleanup()

    # ---- match_chain (pushdown) ----

    @staticmethod
    def _chain_ids(chains):
        """Normalize chains to a comparable, order-independent set of
        ``(object_ids, relation_ids)`` tuples."""
        return sorted(
            (tuple(o.id for o in c.objects), tuple(r.id for r in c.relations))
            for c in chains
        )

    def test_match_chain_single_node(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("o1", type_="memo"))
            store.put_object(self._obj("o2", type_="memo"))
            store.put_object(self._obj("o3", type_="note"))
            # No hops: one chain per object, relations empty.
            assert self._chain_ids(store.match_chain([None], [])) == [
                (("o1",), ()),
                (("o2",), ()),
                (("o3",), ()),
            ]
            # Type-scoped seed.
            assert self._chain_ids(store.match_chain(["memo"], [])) == [
                (("o1",), ()),
                (("o2",), ()),
            ]
        finally:
            self.cleanup()

    def test_match_chain_one_hop_directions(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("a", type_="memo"))
            store.put_object(self._obj("b", type_="note"))
            store.put_relation(self._rel("ab", "a", "b", type_="links"))

            # Right hop a -[links]-> b.
            assert self._chain_ids(
                store.match_chain([None, None], [("links", "right")])
            ) == [(("a", "b"), ("ab",))]
            # Same edge seen from b via a left hop.
            assert self._chain_ids(
                store.match_chain([None, None], [("links", "left")])
            ) == [(("b", "a"), ("ab",))]
            # Node-type and rel-type filters compose.
            assert self._chain_ids(
                store.match_chain(["memo", "note"], [("links", "right")])
            ) == [(("a", "b"), ("ab",))]
            assert (
                store.match_chain(["memo", "memo"], [("links", "right")]) == []
            )
            assert store.match_chain([None, None], [("cites", "right")]) == []
        finally:
            self.cleanup()

    def test_match_chain_multi_hop(self) -> None:
        try:
            store = self.make_store()
            for oid in ("a", "b", "c", "d"):
                store.put_object(self._obj(oid))
            store.put_relation(self._rel("ab", "a", "b", type_="links"))
            store.put_relation(self._rel("bc", "b", "c", type_="links"))
            store.put_relation(self._rel("cd", "c", "d", type_="cites"))

            # a -links-> b -links-> c is the only two-hop all-links chain.
            assert self._chain_ids(
                store.match_chain(
                    [None, None, None], [("links", "right"), ("links", "right")]
                )
            ) == [(("a", "b", "c"), ("ab", "bc"))]
            # Mixed types along the chain.
            assert self._chain_ids(
                store.match_chain(
                    [None, None, None], [("links", "right"), ("cites", "right")]
                )
            ) == [(("b", "c", "d"), ("bc", "cd"))]
        finally:
            self.cleanup()

    def test_match_chain_homomorphic_self_loop(self) -> None:
        try:
            store = self.make_store()
            store.put_object(self._obj("a"))
            # Self-loop a -[r]-> a.
            store.put_relation(self._rel("r", "a", "a", type_="links"))

            # Homomorphic: the single self-loop fills both hops, binding every
            # node position to "a" and reusing "r" twice. This pins that no
            # backend silently switches to relationship-isomorphism.
            assert self._chain_ids(
                store.match_chain(
                    [None, None, None], [("links", "right"), ("links", "right")]
                )
            ) == [(("a", "a", "a"), ("r", "r"))]
        finally:
            self.cleanup()

    def test_match_chain_branching_and_cycles(self) -> None:
        try:
            store = self.make_store()
            for oid in ("a", "b", "c"):
                store.put_object(self._obj(oid))
            # a fans out to b and c; b also points to c; c closes back to a.
            store.put_relation(self._rel("ab", "a", "b", type_="links"))
            store.put_relation(self._rel("ac", "a", "c", type_="links"))
            store.put_relation(self._rel("bc", "b", "c", type_="links"))
            store.put_relation(self._rel("ca", "c", "a", type_="links"))

            # One hop out of a -> two chains (order-independent comparison).
            assert self._chain_ids(
                store.match_chain([None, None], [("links", "right")])
            ) == [
                (("a", "b"), ("ab",)),
                (("a", "c"), ("ac",)),
                (("b", "c"), ("bc",)),
                (("c", "a"), ("ca",)),
            ]
            # Every two-hop links chain, including ones that revisit a node
            # (a->c->a, c->a->c) — node reuse is allowed (homomorphic).
            assert self._chain_ids(
                store.match_chain(
                    [None, None, None], [("links", "right"), ("links", "right")]
                )
            ) == [
                (("a", "b", "c"), ("ab", "bc")),
                (("a", "c", "a"), ("ac", "ca")),
                (("b", "c", "a"), ("bc", "ca")),
                (("c", "a", "b"), ("ca", "ab")),
                (("c", "a", "c"), ("ca", "ac")),
            ]
        finally:
            self.cleanup()

