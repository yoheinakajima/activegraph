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
