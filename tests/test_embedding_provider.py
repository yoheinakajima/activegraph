"""EmbeddingProvider seam (v1.3): protocol, test double, runtime plumbing.

The runtime holds an embedding provider for packs; it never calls it
itself. HashEmbeddingProvider is the deterministic, dependency-free
double for testing embedding plumbing.
"""

import math

from activegraph import FrozenClock, Graph, IDGen, Runtime, behavior
from activegraph.llm import EmbeddingProvider, HashEmbeddingProvider


def test_hash_provider_conforms_to_protocol():
    assert isinstance(HashEmbeddingProvider(), EmbeddingProvider)


def test_hash_provider_is_deterministic_and_normalized():
    p = HashEmbeddingProvider(dimensions=32)
    [a], [b] = (
        p.embed(texts=["the teal bakery"], model=p.default_model),
        p.embed(texts=["the teal bakery"], model=p.default_model),
    )
    assert a == b
    assert len(a) == 32
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-9)


def test_hash_provider_preserves_order_and_handles_empty():
    p = HashEmbeddingProvider(dimensions=8)
    vecs = p.embed(texts=["alpha", "", "beta"], model=p.default_model)
    assert len(vecs) == 3
    assert vecs[1] == [0.0] * 8  # empty text → zero vector, no NaN
    assert vecs[0] != vecs[2]


def test_hash_provider_token_overlap_raises_cosine():
    p = HashEmbeddingProvider()
    q, near, far = p.embed(
        texts=[
            "what color do I like",
            "my favorite color is teal",
            "quarterly revenue grew",
        ],
        model=p.default_model,
    )

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))

    assert cos(q, near) > cos(q, far)


def test_hash_provider_rejects_bad_dimensions():
    import pytest

    with pytest.raises(ValueError, match="dimensions"):
        HashEmbeddingProvider(dimensions=0)


def test_runtime_holds_embedding_provider_and_fork_inherits(tmp_path):
    @behavior(name="maker", on=["goal.created"])
    def maker(event, graph, ctx):
        graph.add_object("task", {"title": "x"})

    embedder = HashEmbeddingProvider()
    g = Graph(ids=IDGen(), clock=FrozenClock())
    rt = Runtime(g, embedding_provider=embedder)
    assert rt.embedding_provider is embedder

    rt.run_goal("hi")
    rt.save_state(str(tmp_path / "run.db"))
    fork_point = rt.trace.events()[0].id

    fork = rt.fork(at_event=fork_point)
    assert fork.embedding_provider is embedder

    other = HashEmbeddingProvider(dimensions=16)
    fork2 = rt.fork(at_event=fork_point, embedding_provider=other)
    assert fork2.embedding_provider is other


def test_runtime_load_accepts_embedding_provider(tmp_path):
    @behavior(name="maker", on=["goal.created"])
    def maker(event, graph, ctx):
        graph.add_object("task", {"title": "x"})

    g = Graph(ids=IDGen(), clock=FrozenClock())
    rt = Runtime(g)
    assert rt.embedding_provider is None  # unset by default
    rt.run_goal("hi")
    path = str(tmp_path / "run.db")
    rt.save_state(path)

    embedder = HashEmbeddingProvider()
    loaded = Runtime.load(path, embedding_provider=embedder)
    assert loaded.embedding_provider is embedder
