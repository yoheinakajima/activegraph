"""Runtime-owned embedding record/replay semantics (CONTRACT v1.8 #6)."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from activegraph import Graph, ReplayDivergenceError, Runtime, behavior, clear_registry
from activegraph.llm import EmbeddingCache


class _Provider:
    default_model = "test-embedding-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, *, texts: list[str], model: str) -> list[list[float]]:
        self.calls.append((list(texts), model))
        return [[float(len(text)), float(index)] for index, text in enumerate(texts)]


class _NoContactProvider:
    default_model = "test-embedding-v1"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, *, texts: list[str], model: str) -> list[list[float]]:
        self.calls += 1
        raise AssertionError("strict replay contacted the embedding provider")


def test_embedding_cache_harvests_recorded_vectors_defensively() -> None:
    provider = _Provider()
    runtime = Runtime(Graph(), embedding_provider=provider)
    vectors = runtime.embed(["alpha", "beta"])

    events = runtime.graph.events
    assert [event.type for event in events] == [
        "embedding.requested",
        "embedding.responded",
    ]
    request, response = events
    assert "alpha" not in json.dumps(request.payload)
    assert response.caused_by == request.id
    assert response.payload["vectors"] == vectors

    cache = EmbeddingCache.from_events(events)
    cached = cache.get(request.payload["inputs_hash"])
    assert cached == vectors
    assert cached is not None
    cached[0][0] = 999.0
    assert cache.get(request.payload["inputs_hash"]) == vectors


def test_loaded_runtime_embed_reuses_direct_recorded_return(tmp_path) -> None:
    path = str(tmp_path / "direct.db")
    provider = _Provider()
    runtime = Runtime(Graph(), embedding_provider=provider, persist_to=path)
    runtime.run_goal("seed")
    expected = runtime.embed(["alpha"], model="test-embedding-v1")
    assert len(provider.calls) == 1

    replay_provider = _NoContactProvider()
    loaded = Runtime.load(
        path,
        behaviors=[],
        embedding_provider=replay_provider,
        replay_embedding_cache=True,
        replay_strict=True,
    )
    assert loaded.embed(["alpha"], model="test-embedding-v1") == expected
    assert replay_provider.calls == 0


def test_fork_embedding_replay_makes_zero_external_contact(tmp_path) -> None:
    clear_registry()

    @behavior(name="embedder", on=["goal.created"])
    def embedder(event, graph, ctx):
        [vector] = ctx.embed([event.payload["goal"]])
        graph.add_object("embedding", {"vector": vector})

    path = str(tmp_path / "embedding.db")
    provider = _Provider()
    runtime = Runtime(
        Graph(),
        behaviors=[embedder],
        embedding_provider=provider,
        persist_to=path,
    )
    runtime.run_goal("alpha")
    assert len(provider.calls) == 1

    goal = next(event for event in runtime.graph.events if event.type == "goal.created")
    replay_provider = _NoContactProvider()
    fork = runtime.fork(
        at_event=goal.id,
        embedding_provider=replay_provider,
        replay_embedding_cache=True,
    )
    fork.run_until_idle()

    assert replay_provider.calls == 0
    response = next(
        event for event in fork.graph.events if event.type == "embedding.responded"
    )
    assert response.payload["cache_hit"] is True
    embedded = [obj for obj in fork.graph.all_objects() if obj.type == "embedding"]
    assert embedded[0].data["vector"] == [5.0, 0.0]


def test_strict_embedding_replay_is_offline(tmp_path) -> None:
    clear_registry()

    @behavior(name="embedder", on=["goal.created"])
    def embedder(event, graph, ctx):
        [vector] = ctx.embed([event.payload["goal"]])
        graph.add_object("embedding", {"vector": vector})

    path = str(tmp_path / "embedding.db")
    runtime = Runtime(
        Graph(),
        behaviors=[embedder],
        embedding_provider=_Provider(),
        persist_to=path,
    )
    runtime.run_goal("alpha")

    replay_provider = _NoContactProvider()
    Runtime.load(
        path,
        behaviors=[embedder],
        embedding_provider=replay_provider,
        replay_strict=True,
    )
    assert replay_provider.calls == 0


def test_strict_embedding_replay_detects_input_hash_drift(tmp_path) -> None:
    clear_registry()
    query = {"text": "alpha"}

    @behavior(name="embedder", on=["goal.created"])
    def embedder(event, graph, ctx):
        ctx.embed([query["text"]])

    path = str(tmp_path / "embedding.db")
    runtime = Runtime(
        Graph(),
        behaviors=[embedder],
        embedding_provider=_Provider(),
        persist_to=path,
    )
    runtime.run_goal("go")
    query["text"] = "changed"

    replay_provider = _NoContactProvider()
    with pytest.raises(ReplayDivergenceError) as exc_info:
        Runtime.load(
            path,
            behaviors=[embedder],
            embedding_provider=replay_provider,
            replay_strict=True,
        )
    assert exc_info.value.kind == "embedding_hash_mismatch"
    assert replay_provider.calls == 0


def test_malformed_embedding_response_records_error_not_cache() -> None:
    class BadProvider:
        default_model = "bad"

        def embed(self, *, texts, model):
            return [[1.0], [2.0]]

    runtime = Runtime(Graph(), embedding_provider=BadProvider())
    with pytest.raises(ValueError, match="wrong vector count"):
        runtime.embed(["one"])

    request, response = runtime.graph.events
    assert request.type == "embedding.requested"
    assert response.type == "embedding.responded"
    assert response.payload["error"]["type"] == "ValueError"
    assert len(EmbeddingCache.from_events(runtime.graph.events)) == 0


def test_wall_budget_strict_replay_uses_recorded_stop_not_clock(
    tmp_path, monkeypatch
) -> None:
    from activegraph.runtime import budget as budget_module

    clear_registry()

    @behavior(name="loop", on=["goal.created", "object.created"])
    def loop(event, graph, ctx):
        graph.add_object("step", {"source": event.type})

    clock_calls = 0

    def live_monotonic() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 0.0 if clock_calls <= 6 else 2.0

    monkeypatch.setattr(
        budget_module, "time", SimpleNamespace(monotonic=live_monotonic)
    )
    path = str(tmp_path / "wall.db")
    runtime = Runtime(
        Graph(),
        behaviors=[loop],
        budget={"max_seconds": 1.0},
        persist_to=path,
    )
    runtime.run_goal("go")
    marker = next(
        event
        for event in runtime.graph.events
        if event.type == "runtime.budget_exhausted"
    )
    assert marker.payload["exhausted_by"] == "max_seconds"
    assert marker.payload["stop_position"]["accepted_sequence"] == (
        len(runtime.graph.events) - 1
    )
    recorded_steps = len(
        [obj for obj in runtime.graph.all_objects() if obj.type == "step"]
    )

    def forbidden_monotonic() -> float:
        raise AssertionError("strict replay read the ambient wall clock")

    monkeypatch.setattr(
        budget_module, "time", SimpleNamespace(monotonic=forbidden_monotonic)
    )
    loaded = Runtime.load(path, behaviors=[loop], replay_strict=True)
    assert len([obj for obj in loaded.graph.all_objects() if obj.type == "step"]) == (
        recorded_steps
    )


def test_strict_wall_replay_rejects_missing_stop_position(
    tmp_path, monkeypatch
) -> None:
    from activegraph.runtime import budget as budget_module

    clear_registry()

    @behavior(name="once", on=["goal.created"])
    def once(event, graph, ctx):
        graph.add_object("step", {})

    calls = 0

    def live_monotonic() -> float:
        nonlocal calls
        calls += 1
        return 0.0 if calls <= 2 else 2.0

    monkeypatch.setattr(
        budget_module, "time", SimpleNamespace(monotonic=live_monotonic)
    )
    path = str(tmp_path / "wall.db")
    Runtime(
        Graph(),
        behaviors=[once],
        budget={"max_seconds": 1.0},
        persist_to=path,
    ).run_goal("go")

    connection = sqlite3.connect(path)
    event_id, payload_json = connection.execute(
        "SELECT id, payload FROM events WHERE type='runtime.budget_exhausted'"
    ).fetchone()
    payload = json.loads(payload_json)
    payload.pop("stop_position")
    connection.execute(
        "UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), event_id)
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(
        budget_module,
        "time",
        SimpleNamespace(monotonic=lambda: (_ for _ in ()).throw(AssertionError())),
    )
    with pytest.raises(ReplayDivergenceError, match="wall_stop_position"):
        Runtime.load(path, behaviors=[once], replay_strict=True)
