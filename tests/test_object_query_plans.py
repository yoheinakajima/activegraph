"""v1.12 object-query planning, residual semantics, and Falkor compilation."""

from __future__ import annotations

from typing import Any

import pytest

from activegraph import Graph, Object, ObjectQuery, ObjectQueryResult
from activegraph.core.graph_store import InMemoryGraphStore
from activegraph.store.falkordb import FalkorDBGraphStore


class _RecordingStore(InMemoryGraphStore):
    def __init__(self) -> None:
        super().__init__()
        self.plans: list[ObjectQuery] = []

    def query_objects(self, plan: ObjectQuery) -> ObjectQueryResult:
        self.plans.append(plan)
        return super().query_objects(plan)


def test_graph_objects_query_and_existence_share_one_plan_boundary() -> None:
    store = _RecordingStore()
    graph = Graph(graph_store=store)
    graph.add_object("", {"score": 1})
    graph.add_object("claim", {"score": 3})

    assert [o.type for o in graph.objects(type="claim", where={"score": 3})] == [
        "claim"
    ]
    assert [o.type for o in graph.query(object_type="claim")] == ["claim"]
    assert graph.has_object_of_type("") is True
    assert graph.has_object_of_type(None) is True

    assert [p.result_mode for p in store.plans] == [
        "objects",
        "objects",
        "exists",
        "exists",
    ]
    assert store.plans[0].where == {"score": 3}
    assert store.plans[2].limit == 1
    assert store.plans[2].type == ""
    assert store.plans[3].type is None


def test_canonical_residual_semantics_cover_missing_none_and_mixed_clauses() -> None:
    graph = Graph()
    present = graph.add_object("claim", {"score": 3, "status": "open"})
    explicit_none = graph.add_object("claim", {"score": None, "status": "open"})
    missing = graph.add_object("claim", {"status": "open"})
    graph.add_object("claim", {"score": 1, "status": "closed"})

    assert [o.id for o in graph.objects(type="claim", where={"score": 3})] == [
        present.id
    ]
    assert {o.id for o in graph.objects(type="claim", where={"score": None})} == {
        explicit_none.id,
        missing.id,
    }
    assert [
        o.id
        for o in graph.objects(
            type="claim",
            where={"score": {">": 2}, "status": "open"},
        )
    ] == [present.id]


def test_object_query_rejects_negative_limits() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ObjectQuery(limit=-1)


class _Result:
    def __init__(self, rows: list[list[Any]]) -> None:
        self.result_set = rows


class _FakeFalkorGraph:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.reads: list[tuple[str, dict[str, Any]]] = []
        self.scalar_exists = True

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> _Result:
        self.writes.append((cypher, dict(params or {})))
        return _Result([])

    def ro_query(
        self, cypher: str, params: dict[str, Any] | None = None
    ) -> _Result:
        self.reads.append((cypher, dict(params or {})))
        if "RETURN count(o)" in cypher:
            return _Result([[0]])
        if "RETURN 1 LIMIT 1" in cypher:
            return _Result([[1]] if self.scalar_exists else [])
        return _Result([])


def _fake_falkor_store() -> tuple[FalkorDBGraphStore, _FakeFalkorGraph]:
    graph = _FakeFalkorGraph()
    store = FalkorDBGraphStore(
        graph=graph,
        indexed_fields={"claim": ["confidence", "status", "tags"]},
    )
    graph.writes.clear()  # discard index creation from assertions below
    graph.reads.clear()   # discard projection-configuration probe
    return store, graph


def test_falkor_dual_writes_only_configured_scalar_fields() -> None:
    store, backend = _fake_falkor_store()
    store.put_object(
        Object(
            id="claim#1",
            type="claim",
            data={
                "confidence": 0.9,
                "status": "open",
                "tags": ["not", "a", "scalar"],
                "unconfigured": 7,
            },
            version=1,
            provenance={},
        )
    )

    cypher, params = backend.writes[-1]
    assert 0.9 in params.values()
    assert "open" in params.values()
    assert ["not", "a", "scalar"] not in params.values()
    assert 7 not in params.values()
    assert "__ag_idx_v_" in cypher
    assert params["index_config"] == store._index_config_token


def test_falkor_compiler_consumes_only_exact_clauses_and_returns_residual() -> None:
    store, _ = _fake_falkor_store()
    plan = ObjectQuery(
        type="claim",
        where={
            "confidence": 0.9,
            "status": {"!=": "closed"},
            "data.confidence": {">": 0.5},
            "tags": ["a"],
            "missing": None,
            "nested.value": 1,
        },
    )

    conditions, params, residual = store._compile_indexed_where(plan)

    assert "confidence" not in residual
    assert "status" not in residual
    assert residual == {
        "data.confidence": {">": 0.5},
        "tags": ["a"],
        "missing": None,
        "nested.value": 1,
    }
    assert len(conditions) == 3  # equality, inequality, ordered prefilter
    assert 0.9 in params.values()
    assert "closed" in params.values()
    assert 0.5 in params.values()


def test_falkor_type_existence_returns_one_scalar_and_decodes_no_payload(
    monkeypatch,
) -> None:
    store, backend = _fake_falkor_store()

    def fail_decode(value: str) -> Any:
        raise AssertionError(f"existence query decoded payload: {value}")

    monkeypatch.setattr("activegraph.store.falkordb.json.loads", fail_decode)
    result = store.query_objects(
        ObjectQuery(type="claim", limit=1, result_mode="exists")
    )

    assert result.exists is True
    assert result.candidates == []
    cypher, params = backend.reads[-1]
    assert "RETURN 1 LIMIT 1" in cypher
    assert "o.data" not in cypher
    assert params == {"type": "claim"}

    empty = store.query_objects(
        ObjectQuery(type="", limit=1, result_mode="exists")
    )
    assert empty.exists is True
    empty_cypher, empty_params = backend.reads[-1]
    assert "toString(o.type) = $type" in empty_cypher
    assert empty_params == {"type": ""}


def test_falkor_disables_indexed_predicates_for_an_unrebuilt_projection() -> None:
    store, _ = _fake_falkor_store()
    store._query_pushdown_ready = False

    conditions, params, residual = store._compile_indexed_where(
        ObjectQuery(type="claim", where={"confidence": 0.9})
    )

    assert conditions == []
    assert params == {}
    assert residual == {"confidence": 0.9}
