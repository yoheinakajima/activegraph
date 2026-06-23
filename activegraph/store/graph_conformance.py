"""GraphStore conformance suite. CONTRACT v1.1.

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
