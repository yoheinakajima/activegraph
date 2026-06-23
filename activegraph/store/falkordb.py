"""FalkorDB-backed GraphStore. CONTRACT v1.1.

Stores the materialized graph projection — objects, relations, and patches —
in a FalkorDB graph instead of process memory. This is a
:class:`~activegraph.core.graph_store.GraphStore`, NOT an
:class:`~activegraph.store.base.EventStore`: it holds the *current-state*
view, not the durable event log. The event log remains the source of truth;
this store is rebuilt by replaying it.

Two connection modes, resolved in this order:

  1. **Explicit handle** — pass ``graph=`` (any object exposing
     ``query`` / ``ro_query``). The store does not own its lifecycle.
  2. **Server** (the ``falkordb`` client) — pass ``url=`` or
     ``host=``/``port=``/``username=``/``password=``, or set the
     environment variables ``FALKORDB_URL`` or ``FALKORDB_HOST`` (with
     optional ``FALKORDB_PORT`` / ``FALKORDB_USERNAME`` /
     ``FALKORDB_PASSWORD``). Connects to an existing FalkorDB instance.
  3. **Embedded** (the ``falkordblite`` client) — the fallback when no
     connection info is given. Ships a self-managed Redis + FalkorDB
     module, zero external infrastructure.

Entity modeling
---------------
Each entity kind is a labelled node keyed by ``id``:

  - ``(:AGObject  {id, type, version, data, provenance})``
  - ``(:AGRelation{id, source, target, type, data, provenance})``
  - ``(:AGPatch   {id, doc})``

``data`` / ``provenance`` (and the whole patch) are JSON-encoded into string
properties because FalkorDB node properties are scalars. Relations are stored
as nodes — not native edges — because this model permits dangling relations
(a relation may reference objects that do not exist yet), matching the
in-memory store's semantics exactly. Cascade-on-object-removal is handled by
the projector in ``core.graph``, not by the database.

Security: every value crosses the Cypher boundary as a bound ``$param``,
never via string interpolation, so object ids / types / payloads cannot
inject Cypher.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from activegraph.core.graph import Object, Relation
from activegraph.core.graph_store import GraphStore
from activegraph.core.patch import Patch


def _require_falkordb_client() -> Any:
    """Import the ``falkordb`` server client or raise a guided error."""
    try:
        from falkordb import FalkorDB  # type: ignore
    except ImportError as e:  # pragma: no cover — exercised only without dep
        from activegraph.errors import MissingOptionalDependency

        raise MissingOptionalDependency(
            package="falkordb",
            feature="FalkorDBGraphStore (server mode)",
            extras="falkordb",
        ) from e
    return FalkorDB


def _require_falkordblite() -> Any:
    """Import the embedded ``falkordblite`` client or raise a guided error."""
    try:
        from redislite.falkordb_client import FalkorDB  # type: ignore
    except ImportError as e:  # pragma: no cover — exercised only without dep
        from activegraph.errors import MissingOptionalDependency

        raise MissingOptionalDependency(
            package="falkordblite",
            feature="FalkorDBGraphStore (embedded mode)",
            extras="falkordb-embedded",
        ) from e
    return FalkorDB


def _resolve_connection(
    url: Optional[str],
    host: Optional[str],
    port: Optional[int],
    username: Optional[str],
    password: Optional[str],
) -> Optional[dict[str, Any]]:
    """Resolve server-connection settings from explicit args, then env vars.

    Returns ``None`` when no connection info is available — the caller then
    falls back to the embedded backend. Explicit arguments take precedence
    over the ``FALKORDB_*`` environment variables.
    """
    if url is not None:
        return {"url": url}
    if host is not None:
        return {
            "host": host,
            "port": int(port) if port is not None else 6379,
            "username": username,
            "password": password,
        }
    env_url = os.environ.get("FALKORDB_URL")
    if env_url:
        return {"url": env_url}
    env_host = os.environ.get("FALKORDB_HOST")
    if env_host:
        return {
            "host": env_host,
            "port": int(os.environ.get("FALKORDB_PORT", "6379")),
            "username": os.environ.get("FALKORDB_USERNAME"),
            "password": os.environ.get("FALKORDB_PASSWORD"),
        }
    return None


class FalkorDBGraphStore(GraphStore):
    """A :class:`GraphStore` backed by a FalkorDB graph.

    Parameters
    ----------
    path:
        Filesystem path for the embedded ``falkordblite`` database. Used
        only in embedded mode (no connection info given). When ``None``, an
        ephemeral embedded instance is created.
    graph_name:
        Name of the FalkorDB graph to use within the database/server.
        Defaults to ``"activegraph"``. Ignored when ``graph`` is supplied.
    graph:
        An existing FalkorDB graph handle (anything exposing ``query`` /
        ``ro_query``). When provided, all other connection parameters are
        ignored and this store does not own the connection's lifecycle.
    url:
        FalkorDB server URL (e.g. ``"falkor://host:6379"``). Triggers
        server mode via the ``falkordb`` client.
    host, port, username, password:
        FalkorDB server connection settings. Supplying ``host`` triggers
        server mode. ``port`` defaults to ``6379``.

    Connection resolution order: explicit ``graph`` → explicit ``url`` /
    ``host`` → ``FALKORDB_URL`` / ``FALKORDB_HOST`` env vars → embedded
    ``falkordblite``.

    Install the server client with ``pip install 'activegraph[falkordb]'``
    or the embedded engine with
    ``pip install 'activegraph[falkordb-embedded]'``.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        graph_name: str = "activegraph",
        *,
        graph: Any = None,
        url: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self._db: Any = None
        self._owns_db = False
        if graph is not None:
            self._g = graph
        else:
            conn = _resolve_connection(url, host, port, username, password)
            if conn is not None:
                FalkorDB = _require_falkordb_client()
                if "url" in conn:
                    self._db = FalkorDB.from_url(conn["url"])
                else:
                    self._db = FalkorDB(
                        host=conn["host"],
                        port=conn["port"],
                        username=conn["username"],
                        password=conn["password"],
                    )
            else:
                FalkorDB = _require_falkordblite()
                self._db = FalkorDB(path) if path is not None else FalkorDB()
            self._owns_db = True
            self._g = self._db.select_graph(graph_name)
        self._ensure_indexes()

    # ---- schema ----

    def _ensure_indexes(self) -> None:
        # Range/exact indexes on the id property make get/remove O(log n).
        # CREATE INDEX raises if the index already exists; swallow that so
        # re-opening an existing database is idempotent.
        for label in ("AGObject", "AGRelation", "AGPatch"):
            try:
                self._g.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")
            except Exception:  # noqa: BLE001 — index-exists is the only expected case
                pass

    # ---- objects ----

    def put_object(self, obj: Object) -> None:
        self._g.query(
            "MERGE (o:AGObject {id: $id}) "
            "SET o.type = $type, o.version = $version, "
            "o.data = $data, o.provenance = $provenance",
            params={
                "id": obj.id,
                "type": obj.type,
                "version": obj.version,
                "data": json.dumps(obj.data),
                "provenance": json.dumps(obj.provenance),
            },
        )

    def get_object(self, object_id: str) -> Optional[Object]:
        res = self._g.ro_query(
            "MATCH (o:AGObject {id: $id}) "
            "RETURN o.type, o.version, o.data, o.provenance",
            params={"id": object_id},
        )
        rows = res.result_set
        if not rows:
            return None
        type_, version, data, provenance = rows[0]
        return Object(
            id=object_id,
            type=type_,
            data=json.loads(data),
            version=int(version),
            provenance=json.loads(provenance),
        )

    def remove_object(self, object_id: str) -> None:
        self._g.query(
            "MATCH (o:AGObject {id: $id}) DELETE o",
            params={"id": object_id},
        )

    def all_objects(self) -> list[Object]:
        res = self._g.ro_query(
            "MATCH (o:AGObject) "
            "RETURN o.id, o.type, o.version, o.data, o.provenance"
        )
        return [
            Object(
                id=row[0],
                type=row[1],
                data=json.loads(row[3]),
                version=int(row[2]),
                provenance=json.loads(row[4]),
            )
            for row in res.result_set
        ]

    # ---- relations ----

    def put_relation(self, rel: Relation) -> None:
        self._g.query(
            "MERGE (r:AGRelation {id: $id}) "
            "SET r.source = $source, r.target = $target, r.type = $type, "
            "r.data = $data, r.provenance = $provenance",
            params={
                "id": rel.id,
                "source": rel.source,
                "target": rel.target,
                "type": rel.type,
                "data": json.dumps(rel.data),
                "provenance": json.dumps(rel.provenance),
            },
        )

    def get_relation(self, relation_id: str) -> Optional[Relation]:
        res = self._g.ro_query(
            "MATCH (r:AGRelation {id: $id}) "
            "RETURN r.source, r.target, r.type, r.data, r.provenance",
            params={"id": relation_id},
        )
        rows = res.result_set
        if not rows:
            return None
        source, target, type_, data, provenance = rows[0]
        return Relation(
            id=relation_id,
            source=source,
            target=target,
            type=type_,
            data=json.loads(data),
            provenance=json.loads(provenance),
        )

    def remove_relation(self, relation_id: str) -> None:
        self._g.query(
            "MATCH (r:AGRelation {id: $id}) DELETE r",
            params={"id": relation_id},
        )

    def all_relations(self) -> list[Relation]:
        res = self._g.ro_query(
            "MATCH (r:AGRelation) "
            "RETURN r.id, r.source, r.target, r.type, r.data, r.provenance"
        )
        return [
            Relation(
                id=row[0],
                source=row[1],
                target=row[2],
                type=row[3],
                data=json.loads(row[4]),
                provenance=json.loads(row[5]),
            )
            for row in res.result_set
        ]

    # ---- patches ----

    def put_patch(self, patch: Patch) -> None:
        self._g.query(
            "MERGE (p:AGPatch {id: $id}) SET p.doc = $doc",
            params={"id": patch.id, "doc": json.dumps(patch.to_dict())},
        )

    def get_patch(self, patch_id: str) -> Optional[Patch]:
        res = self._g.ro_query(
            "MATCH (p:AGPatch {id: $id}) RETURN p.doc",
            params={"id": patch_id},
        )
        rows = res.result_set
        if not rows:
            return None
        return Patch(**json.loads(rows[0][0]))

    def all_patches(self) -> list[Patch]:
        res = self._g.ro_query("MATCH (p:AGPatch) RETURN p.doc")
        return [Patch(**json.loads(row[0])) for row in res.result_set]

    def remove_patch(self, patch_id: str) -> None:
        self._g.query(
            "MATCH (p:AGPatch {id: $id}) DELETE p",
            params={"id": patch_id},
        )

    # ---- lifecycle ----

    def clear(self) -> None:
        self._g.query(
            "MATCH (n) WHERE n:AGObject OR n:AGRelation OR n:AGPatch DELETE n"
        )

    def close(self) -> None:
        if self._owns_db and self._db is not None:
            close = getattr(self._db, "close", None)
            if callable(close):
                close()
