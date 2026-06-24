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
Objects and relations form a real graph; patches are standalone nodes.

  - Every endpoint is a node carrying the shared label ``:AGNode`` keyed by
    ``id``. A materialized object additionally carries ``:AGObject`` plus its
    ``type`` / ``version`` / ``data`` / ``provenance``::

        (:AGNode:AGObject {id, type, version, data, provenance})

  - Relations are **native edges** with a single fixed relationship type and
    the relation's ``type`` carried as an edge property::

        (s:AGNode)-[:AGRelation {id, type, data, provenance}]->(t:AGNode)

  - Patches stay standalone nodes: ``(:AGPatch {id, doc})``.

The shared ``:AGNode`` label is load-bearing, not cosmetic. ``put_relation``
MERGEs its endpoints by ``id`` *before* creating the edge, so a relation may
reference an object that does not exist yet — the endpoint is created as a
bare **placeholder** (``:AGNode`` without ``:AGObject``). This preserves the
in-memory store's dangling-relation semantics with true edges. A label-less
node could not be indexed, so MERGE would full-scan on every relation write;
a type-specific MERGE label would risk duplicate-identity nodes — hence the
shared label. Placeholder-ness is *derived* (``:AGNode AND NOT :AGObject``),
never stored as its own label, so there is a single source of truth and no
label churn when an object is removed but still referenced.

``data`` / ``provenance`` (and the whole patch) are JSON-encoded into string
properties because FalkorDB properties are scalars. ``source`` / ``target``
are not stored — they fall out of the edge's endpoints. Cascade-on-object-
removal is driven by the projector in ``core.graph``: ``remove_object``
demotes the node to a placeholder (keeping its edges so the cascade can still
enumerate the touching relations), then each ``remove_relation`` deletes its
edge and cleans up any endpoint left as an orphaned placeholder.

Security: relations use a fixed relationship type, so every value — ids,
types, payloads — crosses the Cypher boundary as a bound ``$param``, never
via string interpolation. Nothing can inject Cypher.
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
        # Range/exact indexes make get/remove O(log n). CREATE INDEX raises if
        # the index already exists; swallow that so re-opening an existing
        # database is idempotent. Endpoints are merged/looked up by
        # ``AGNode(id)``; objects additionally by ``AGObject(id)``; relations
        # are native edges indexed by ``AGRelation(id)`` (lookup) and
        # ``AGRelation(type)`` (kind filtering).
        statements = (
            "CREATE INDEX FOR (n:AGNode) ON (n.id)",
            "CREATE INDEX FOR (n:AGObject) ON (n.id)",
            "CREATE INDEX FOR ()-[r:AGRelation]->() ON (r.id)",
            "CREATE INDEX FOR ()-[r:AGRelation]->() ON (r.type)",
            "CREATE INDEX FOR (n:AGPatch) ON (n.id)",
        )
        for stmt in statements:
            try:
                self._g.query(stmt)
            except Exception:  # noqa: BLE001 — index-exists is the only expected case
                pass

    # ---- objects ----

    def put_object(self, obj: Object) -> None:
        # MERGE on the shared :AGNode label so a node previously created as a
        # placeholder (by a dangling relation) is promoted in place; SET the
        # :AGObject label + payload to materialize it.
        self._g.query(
            "MERGE (o:AGNode {id: $id}) "
            "SET o:AGObject, o.type = $type, o.version = $version, "
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
        # Demote the object to a bare :AGNode placeholder rather than deleting
        # it outright: the projector removes the object first, then cascades
        # over ``all_relations()`` to drop the touching relations, so the
        # node's edges must survive long enough to be enumerated. Once the
        # label + payload are stripped, delete the node only if it has no
        # remaining edges (i.e. it was not referenced by any relation).
        self._g.query(
            "MATCH (o:AGObject {id: $id}) "
            "REMOVE o:AGObject "
            "SET o.type = null, o.version = null, "
            "o.data = null, o.provenance = null "
            "WITH o "
            "OPTIONAL MATCH (o)-[e]-() "
            "WITH o, count(e) AS deg "
            "WHERE deg = 0 "
            "DELETE o",
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
        # MERGE both endpoints by id (creating placeholders if they are not
        # yet materialized objects), then MERGE a native edge keyed by the
        # relation id and SET its payload. The relationship type is the fixed
        # literal ``AGRelation`` — the relation's own ``type`` is an edge
        # property — so every caller value stays a bound $param.
        self._g.query(
            "MERGE (s:AGNode {id: $source}) "
            "MERGE (t:AGNode {id: $target}) "
            "MERGE (s)-[r:AGRelation {id: $id}]->(t) "
            "SET r.type = $type, r.data = $data, r.provenance = $provenance",
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
        # source / target are derived from the edge's endpoints, not stored.
        res = self._g.ro_query(
            "MATCH (s)-[r:AGRelation {id: $id}]->(t) "
            "RETURN s.id, t.id, r.type, r.data, r.provenance",
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
        # Delete the edge, then garbage-collect either endpoint that is left
        # as an orphaned placeholder (a :AGNode that is not an :AGObject and
        # now has no remaining edges). Materialized objects and endpoints
        # still referenced by other relations are preserved.
        self._g.query(
            "MATCH (s)-[r:AGRelation {id: $id}]->(t) "
            "DELETE r "
            "WITH [s, t] AS ends "
            "UNWIND ends AS n "
            "WITH DISTINCT n "
            "OPTIONAL MATCH (n)-[e]-() "
            "WITH n, count(e) AS deg "
            "WHERE deg = 0 AND NOT n:AGObject "
            "DELETE n",
            params={"id": relation_id},
        )

    def all_relations(self) -> list[Relation]:
        res = self._g.ro_query(
            "MATCH (s)-[r:AGRelation]->(t) "
            "RETURN r.id, s.id, t.id, r.type, r.data, r.provenance"
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
        # DETACH DELETE so native AGRelation edges are removed with their
        # endpoints; AGNode covers both materialized objects and placeholders.
        self._g.query(
            "MATCH (n) WHERE n:AGNode OR n:AGPatch DETACH DELETE n"
        )

    def close(self) -> None:
        if self._owns_db and self._db is not None:
            close = getattr(self._db, "close", None)
            if callable(close):
                close()
