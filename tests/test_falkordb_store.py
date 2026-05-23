"""FalkorDBEventStore — runs the conformance suite against a live FalkorDB.

Gated by ACTIVEGRAPH_TEST_FALKORDB_URL. If not set, the tests skip.
Mirrors the Postgres pattern: contributors with Docker can run
``docker run -p 6379:6379 falkordb/falkordb:latest`` and export
``ACTIVEGRAPH_TEST_FALKORDB_URL=falkor://localhost:6379/test`` to
exercise this suite locally. v1.1 STORE-FALKORDB.
"""

from __future__ import annotations

import os
import uuid

import pytest

from activegraph.store.conformance import EventStoreConformance

FALKOR_URL = os.environ.get("ACTIVEGRAPH_TEST_FALKORDB_URL")
pytestmark = pytest.mark.skipif(
    FALKOR_URL is None,
    reason="set ACTIVEGRAPH_TEST_FALKORDB_URL to run FalkorDB tests",
)


@pytest.mark.falkordb
class TestFalkorDBConformance(EventStoreConformance):
    __test__ = True

    def setup_method(self, method):
        # Each test uses a unique run_id so conformance tests are
        # independent in a shared graph.
        self._created_run_ids: list[str] = []

    def make_store(self, run_id):
        from activegraph.store.falkordb import FalkorDBEventStore

        # Append a uuid suffix so multiple test runs against the same
        # FalkorDB instance don't collide on run_id.
        scoped = f"{run_id}_{uuid.uuid4().hex[:8]}"
        self._created_run_ids.append(scoped)
        return FalkorDBEventStore(FALKOR_URL, run_id=scoped)

    def cleanup(self):
        from activegraph.store.falkordb import _GraphSource

        source = _GraphSource(FALKOR_URL)
        try:
            for rid in self._created_run_ids:
                # DETACH DELETE removes both the Event nodes and the
                # HAS_EVENT edges; the Run node itself is then removed.
                source.query(
                    "MATCH (e:Event {run_id:$rid}) DETACH DELETE e",
                    {"rid": rid},
                )
                source.query(
                    "MATCH (r:Run {run_id:$rid}) DETACH DELETE r",
                    {"rid": rid},
                )
        finally:
            source.close()
