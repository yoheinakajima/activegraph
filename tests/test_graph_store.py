"""GraphStore conformance against InMemoryGraphStore + a Graph integration check.

The in-memory store is the default backend; this also guards that the
GraphStore seam in `core.graph` did not change graph semantics.
"""

from __future__ import annotations

import pytest

from activegraph.core.graph import Graph
from activegraph.core.graph_store import InMemoryGraphStore
from activegraph.store.falkordb import _resolve_connection
from activegraph.store.graph_conformance import GraphStoreConformance


class TestInMemoryGraphStoreConformance(GraphStoreConformance):
    __test__ = True

    def make_store(self):
        return InMemoryGraphStore()


def test_graph_uses_injected_graph_store():
    store = InMemoryGraphStore()
    g = Graph(graph_store=store)
    obj = g.add_object("memo", {"text": "hi"})
    # The object the Graph returns is the one the store holds.
    assert store.get_object(obj.id) is obj
    assert g.get_object(obj.id) is obj


def test_graph_patch_flow_round_trips_through_store():
    g = Graph()
    obj = g.add_object("memo", {"text": "first"})
    g.patch_object(obj.id, {"text": "second"})
    reread = g.get_object(obj.id)
    assert reread.data["text"] == "second"
    assert reread.version == 2


def test_graph_object_removal_cascades_relations():
    g = Graph()
    a = g.add_object("node", {})
    b = g.add_object("node", {})
    g.add_relation(a.id, b.id, "links")
    assert len(g.all_relations()) == 1
    g.remove_object(a.id)
    assert g.get_object(a.id) is None
    assert g.all_relations() == []


# --- FalkorDB connection resolution (no server needed) ---


@pytest.fixture(autouse=True)
def _clear_falkordb_env(monkeypatch):
    for var in (
        "FALKORDB_URL",
        "FALKORDB_HOST",
        "FALKORDB_PORT",
        "FALKORDB_USERNAME",
        "FALKORDB_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)


def test_resolve_connection_none_falls_back_to_embedded():
    assert _resolve_connection(None, None, None, None, None) is None


def test_resolve_connection_explicit_url():
    assert _resolve_connection("falkor://h:6380", None, None, None, None) == {
        "url": "falkor://h:6380"
    }


def test_resolve_connection_explicit_host_defaults_port():
    assert _resolve_connection(None, "myhost", None, None, None) == {
        "host": "myhost",
        "port": 6379,
        "username": None,
        "password": None,
    }


def test_resolve_connection_explicit_host_full():
    assert _resolve_connection(None, "myhost", 7000, "u", "p") == {
        "host": "myhost",
        "port": 7000,
        "username": "u",
        "password": "p",
    }


def test_resolve_connection_env_url(monkeypatch):
    monkeypatch.setenv("FALKORDB_URL", "falkor://env:6379")
    assert _resolve_connection(None, None, None, None, None) == {
        "url": "falkor://env:6379"
    }


def test_resolve_connection_env_host(monkeypatch):
    monkeypatch.setenv("FALKORDB_HOST", "envhost")
    monkeypatch.setenv("FALKORDB_PORT", "6500")
    monkeypatch.setenv("FALKORDB_USERNAME", "envuser")
    monkeypatch.setenv("FALKORDB_PASSWORD", "envpass")
    assert _resolve_connection(None, None, None, None, None) == {
        "host": "envhost",
        "port": 6500,
        "username": "envuser",
        "password": "envpass",
    }


def test_resolve_connection_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("FALKORDB_HOST", "envhost")
    monkeypatch.setenv("FALKORDB_URL", "falkor://env:6379")
    # explicit url wins over both env vars
    assert _resolve_connection("falkor://explicit:1", None, None, None, None) == {
        "url": "falkor://explicit:1"
    }

