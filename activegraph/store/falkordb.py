"""FalkorDB-backed EventStore. v1.1 STORE-FALKORDB.

FalkorDB is a Redis-module property graph database with OpenCypher.
This backend persists the append-only event log as a small graph:

    (:Run {run_id, parent_run_id, forked_at_event_id, label,
           created_at, goal, frame_id, next_seq})
    (:Event {id, type, actor, payload, frame_id, caused_by,
             timestamp, run_id, seq})
    (:Run)-[:HAS_EVENT]->(:Event)

A single ``(:Meta {key:'schema_version', value:'1'})`` node carries
the schema version; mismatch surfaces ``SchemaVersionMismatch`` with
the same recovery prose as the other backends.

Why a graph for an append-only log? Two reasons. (1) Operators who pick
FalkorDB already speak Cypher; modelling events as nodes lets them
ad-hoc-query the audit trail with the same DSL the rest of the
framework already uses for behavior subscriptions. (2) Phase 2 of the
v1.1 FalkorDB story is a graph-projection backend — keeping the event
log in the same database as the eventual projection avoids a
multi-store deployment for users who want both.

Per-run sequence numbers
------------------------
``seq`` is the projection ordering authority (matches SQLite/Postgres).
FalkorDB has no ``BIGSERIAL`` so the Run node carries a ``next_seq``
counter that ``append`` reads-and-increments inside a single Cypher
query. FalkorDB executes queries serially per graph, which makes the
read-modify-write atomic without an explicit transaction.

Uniqueness of (id, run_id)
--------------------------
Enforced by a pre-check MATCH inside ``append``. FalkorDB's unique
constraint surface is evolving; the explicit pre-check is portable
across client versions and produces the framework's structured
``DuplicateEventError`` rather than a driver-specific exception.

Connection management is the user's job — same shape as Postgres:

* Pass a ``falkor://[user:pass@]host[:port]/<graph_name>`` URL and the
  store opens a dedicated ``FalkorDB`` client. The store owns it.
* Pass a ``falkordb.FalkorDB`` instance to reuse a client (e.g., a
  Sentinel-aware client). The store does not own its lifecycle, but
  it does call ``select_graph`` to obtain the per-graph handle.
* Pass a ``falkordb.Graph`` instance directly to skip both steps; the
  store does not own its lifecycle.

The ``falkordb`` package is required (>=1.0); install with
``pip install 'activegraph[falkordb]'``.
"""

from __future__ import annotations

from typing import Any, Iterator, Optional
from urllib.parse import urlparse

from activegraph.core.event import Event
from activegraph.store.base import RunRecord
from activegraph.store.serde import decode_event, encode_event


SCHEMA_VERSION = "1"


def _require_falkordb() -> Any:
    try:
        import falkordb  # type: ignore
    except ImportError as e:  # pragma: no cover — exercised only without dep
        from activegraph.errors import MissingOptionalDependency
        raise MissingOptionalDependency(
            package="falkordb",
            feature="FalkorDBEventStore",
            extras="falkordb",
        ) from e
    return falkordb


def _parse_falkor_url(url: str) -> dict[str, Any]:
    """Split a ``falkor://`` URL into FalkorDB() kwargs + a graph name.

    Accepted shapes:
        falkor://host                       (port 6379, db "activegraph")
        falkor://host:6379/mygraph
        falkor://user:pass@host:6379/mygraph
        falkordb://host/mygraph
    """
    parsed = urlparse(url)
    graph_name = parsed.path.lstrip("/") or "activegraph"
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
        "username": parsed.username,
        "password": parsed.password,
        "graph_name": graph_name,
    }


def _looks_like_graph(obj: Any) -> bool:
    # The falkordb client's Graph class exposes .query(); FalkorDB top-level
    # client exposes .select_graph(). We distinguish by the presence of
    # select_graph (only on the top-level client).
    return hasattr(obj, "query") and not hasattr(obj, "select_graph")


def _looks_like_client(obj: Any) -> bool:
    return hasattr(obj, "select_graph")


class _GraphSource:
    """Adapter over the three accepted target shapes — URL, client, Graph.

    Stores the resolved ``Graph`` handle in ``self.graph`` and remembers
    whether the underlying client is owned (must be closed on store
    ``close()``) or borrowed (left to the caller).
    """

    def __init__(self, target: Any, *, default_graph: str = "activegraph") -> None:
        self.graph: Any = None
        self.graph_name: str = default_graph
        self._owned_client: Any = None

        if isinstance(target, str):
            falkordb = _require_falkordb()
            kwargs = _parse_falkor_url(target)
            self.graph_name = kwargs.pop("graph_name")
            self._owned_client = falkordb.FalkorDB(**kwargs)
            self.graph = self._owned_client.select_graph(self.graph_name)
        elif _looks_like_client(target):
            # FalkorDB client — borrow it, select the named graph.
            self.graph_name = default_graph
            self.graph = target.select_graph(default_graph)
        elif _looks_like_graph(target):
            # Already a Graph handle — borrow it as-is.
            self.graph = target
            self.graph_name = getattr(target, "name", default_graph)
        else:
            from activegraph.runtime.config_errors import InvalidArgumentType
            type_name = type(target).__name__
            target_repr = repr(target)
            if len(target_repr) > 80:
                target_repr = target_repr[:77] + "..."
            raise InvalidArgumentType(
                f"FalkorDBEventStore target has wrong type (got {type_name})",
                what_failed=(
                    f"FalkorDBEventStore was constructed with a target of "
                    f"type {type_name}:\n  value: {target_repr}\n"
                    f"  type:  {type_name}\n"
                    f"Accepted types are: a `falkor://...` URL string, a "
                    f"`falkordb.FalkorDB` client, or a `falkordb.Graph` "
                    f"instance."
                ),
                why=(
                    "FalkorDBEventStore's constructor branches on the "
                    "target's type — strings open a fresh client (owned), "
                    "FalkorDB clients are borrowed without ownership and "
                    "have a graph selected from them, and Graph instances "
                    "are used directly. An unknown type has no defined "
                    "lifecycle, and a fuzzy match would silently leak "
                    "connections or double-close them."
                ),
                how_to_fix=(
                    "Pass one of:\n"
                    "    FalkorDBEventStore('falkor://host:6379/mygraph', run_id=...)\n"
                    "    FalkorDBEventStore(my_falkordb_client, run_id=...)\n"
                    "    FalkorDBEventStore(my_falkordb_graph, run_id=...)\n"
                    "\n"
                    "If you have a raw redis-py connection, construct a "
                    "falkordb.FalkorDB client from it first and pass that."
                ),
                context={"type": type_name, "repr": target_repr},
            )

    def query(self, cypher: str, params: Optional[dict[str, Any]] = None) -> Any:
        return self.graph.query(cypher, params or {})

    def close(self) -> None:
        if self._owned_client is not None:
            try:
                # Newer falkordb clients expose .close(); older ones don't.
                close = getattr(self._owned_client, "close", None)
                if close is not None:
                    close()
            except Exception:
                pass
            self._owned_client = None


def _ensure_schema(source: "_GraphSource") -> None:
    res = source.query(
        "MATCH (m:_AGMeta {key:'schema_version'}) RETURN m.value LIMIT 1"
    )
    rows = list(res.result_set or [])
    if not rows:
        source.query(
            "CREATE (:_AGMeta {key:'schema_version', value:$v})",
            {"v": SCHEMA_VERSION},
        )
        return
    found = rows[0][0]
    if found != SCHEMA_VERSION:
        from activegraph import __version__ as _aw_version
        from activegraph.store.errors import SchemaVersionMismatch
        raise SchemaVersionMismatch(
            f"falkordb store schema_version {found!r} does not match this build's expected {SCHEMA_VERSION!r}",
            what_failed=(
                f"The FalkorDB store records schema_version={found!r} in its "
                f":_AGMeta node, but activegraph {_aw_version} expects "
                f"schema_version={SCHEMA_VERSION!r}."
            ),
            why=(
                "The store schema evolves with the framework. The runtime "
                "refuses to read a store with a different schema_version rather "
                "than risk silent data loss — a newer framework might interpret "
                "node properties differently than the writer did, and an older "
                "framework might drop fields it doesn't recognize."
            ),
            how_to_fix=(
                f"One of three actions:\n"
                f"  1. Install the activegraph version that wrote this store.\n"
                f"  2. Migrate runs to a fresh graph written by this build:\n"
                f"     activegraph migrate <src-url> <new-dst-url>\n"
                f"  3. If the graph is expendable, drop it (GRAPH.DELETE) and re-create.\n"
                f"\n"
                f"Schema version history is documented in CHANGELOG.md."
            ),
            context={
                "found_version": found,
                "expected_version": SCHEMA_VERSION,
                "activegraph_version": _aw_version,
                "driver": "falkordb",
            },
        )


def _row_to_event(row: tuple) -> Event:
    # Columns: id, type, actor, payload, frame_id, caused_by, timestamp
    id_, type_, actor, payload, frame_id, caused_by, ts = row
    return decode_event(
        {
            "id": id_,
            "type": type_,
            "payload": payload,
            "actor": actor,
            "frame_id": frame_id,
            "caused_by": caused_by,
            "timestamp": ts,
        }
    )


def _row_to_run(row: tuple) -> RunRecord:
    run_id, parent_run_id, forked_at_event_id, label, created_at, goal, frame_id = row
    return RunRecord(
        run_id=run_id,
        parent_run_id=parent_run_id,
        forked_at_event_id=forked_at_event_id,
        label=label,
        created_at=created_at,
        goal=goal,
        frame_id=frame_id,
    )


# Cypher RETURN projections. Kept as constants so iter/get/list share one
# column order with _row_to_event / _row_to_run.
_EVENT_RETURN = (
    "e.id, e.type, e.actor, e.payload, e.frame_id, e.caused_by, e.timestamp"
)
_RUN_RETURN = (
    "r.run_id, r.parent_run_id, r.forked_at_event_id, r.label, "
    "r.created_at, r.goal, r.frame_id"
)


class FalkorDBEventStore:
    """Per-run view onto a FalkorDB-backed event log. v1.1 STORE-FALKORDB.

    See module docstring for the data model and connection-management
    rules. The store conforms to the ``EventStore`` Protocol in
    ``activegraph.store.base`` and runs the full ``EventStoreConformance``
    suite against a real FalkorDB instance (gated on
    ``ACTIVEGRAPH_TEST_FALKORDB_URL`` in CI).
    """

    def __init__(self, target: Any, run_id: str) -> None:
        if run_id is None:
            raise TypeError(
                "FalkorDBEventStore requires a run_id. For most cases, use "
                "Runtime(graph, persist_to='falkor://host/graphname') "
                "instead, which handles run_id automatically."
            )
        self._source = _GraphSource(target)
        self.run_id = run_id
        _ensure_schema(self._source)

    # ---------- EventStore protocol ----------

    def append(self, event: Event) -> None:
        row = encode_event(event)
        # Duplicate (id, run_id) check up front — produces the framework's
        # structured DuplicateEventError instead of a driver-specific error.
        existing = self._source.query(
            "MATCH (e:Event {id:$id, run_id:$rid}) RETURN 1 LIMIT 1",
            {"id": event.id, "rid": self.run_id},
        )
        if existing.result_set:
            from activegraph.store.errors import DuplicateEventError
            raise DuplicateEventError(
                f"event id {event.id!r} already exists in run {self.run_id!r}",
                what_failed=(
                    f"The FalkorDB store already contains an event with id "
                    f"{event.id!r} in run {self.run_id!r}; appending a "
                    f"second event with the same logical id is refused."
                ),
                why=(
                    "Logical event ids are unique within a run. A duplicate "
                    "would break addressing (every fork, diff, and replay "
                    "uses the id) and indicates a programmer error — the "
                    "runtime's id generator is monotonic, so duplicates "
                    "shouldn't arise in normal use."
                ),
                how_to_fix=(
                    "Common cause: hand-constructed events with fixed ids "
                    "in a test fixture. Mint fresh ids per event or scope "
                    "the fixture to one run."
                ),
                context={
                    "event_id": event.id,
                    "run_id": self.run_id,
                    "driver": "falkordb",
                },
            )
        # MERGE the Run node so the next_seq counter exists. The
        # read-modify-write of next_seq is atomic because FalkorDB
        # serializes queries per graph.
        self._source.query(
            """
            MERGE (r:Run {run_id:$rid})
              ON CREATE SET r.next_seq = 1
            WITH r, coalesce(r.next_seq, 1) AS s
            SET r.next_seq = s + 1
            CREATE (e:Event {
                id: $id, type: $type, actor: $actor, payload: $payload,
                frame_id: $frame_id, caused_by: $caused_by,
                timestamp: $timestamp, run_id: $rid, seq: s
            })
            CREATE (r)-[:HAS_EVENT]->(e)
            """,
            {**row, "rid": self.run_id},
        )

    def iter_events(
        self,
        after: Optional[str] = None,
        until: Optional[str] = None,
    ) -> Iterator[Event]:
        clauses = ["e.run_id = $rid"]
        params: dict[str, Any] = {"rid": self.run_id}
        if after is not None:
            clauses.append("e.seq > $after_seq")
            params["after_seq"] = self._seq_of(after)
        if until is not None:
            clauses.append("e.seq <= $until_seq")
            params["until_seq"] = self._seq_of(until)
        cypher = (
            f"MATCH (e:Event) WHERE {' AND '.join(clauses)} "
            f"RETURN {_EVENT_RETURN} ORDER BY e.seq"
        )
        res = self._source.query(cypher, params)
        for row in res.result_set or []:
            yield _row_to_event(tuple(row))

    def get_event(self, event_id: str) -> Optional[Event]:
        res = self._source.query(
            f"MATCH (e:Event {{id:$id, run_id:$rid}}) RETURN {_EVENT_RETURN} LIMIT 1",
            {"id": event_id, "rid": self.run_id},
        )
        rows = res.result_set or []
        return _row_to_event(tuple(rows[0])) if rows else None

    def count(self) -> int:
        res = self._source.query(
            "MATCH (e:Event {run_id:$rid}) RETURN count(e)",
            {"rid": self.run_id},
        )
        rows = res.result_set or []
        return int(rows[0][0]) if rows else 0

    def truncate_after(self, event_id: str) -> None:
        seq = self._seq_of(event_id)
        self._source.query(
            "MATCH (e:Event {run_id:$rid}) WHERE e.seq > $seq DETACH DELETE e",
            {"rid": self.run_id, "seq": seq},
        )

    def close(self) -> None:
        self._source.close()

    def _seq_of(self, event_id: str) -> int:
        res = self._source.query(
            "MATCH (e:Event {id:$id, run_id:$rid}) RETURN e.seq LIMIT 1",
            {"id": event_id, "rid": self.run_id},
        )
        rows = res.result_set or []
        if not rows:
            from activegraph.store.errors import EventNotFoundError
            raise EventNotFoundError(
                f"event {event_id!r} not found in run {self.run_id!r}",
                what_failed=(
                    f"The FalkorDB store has no event with id {event_id!r} "
                    f"in run {self.run_id!r}."
                ),
                why=(
                    "Event ids are the framework's addressing primitive. The "
                    "store refuses to return a default for an unknown id — that "
                    "would silently corrupt the audit trail and any downstream "
                    "fork or replay."
                ),
                how_to_fix=(
                    f"Check the event id against what's actually in the run:\n"
                    f"    activegraph inspect <store-url> --run-id {self.run_id} --tail 100\n"
                    "\n"
                    "Common causes: typo in a hand-typed id, referencing an id "
                    "from a different run, or a run truncated by an earlier fork."
                ),
                context={
                    "event_id": event_id,
                    "run_id": self.run_id,
                    "driver": "falkordb",
                },
            )
        return int(rows[0][0])

    # ---------- v0.5 helpers (per-run) ----------

    def get_run(self) -> Optional[RunRecord]:
        res = self._source.query(
            f"MATCH (r:Run {{run_id:$rid}}) RETURN {_RUN_RETURN} LIMIT 1",
            {"rid": self.run_id},
        )
        rows = res.result_set or []
        if not rows:
            return None
        # If a Run was MERGEd by append() but never had metadata written,
        # created_at will be NULL. get_run only returns a record when
        # metadata has been upserted — matching the SQLite/Postgres
        # semantics where a row exists in `runs` iff upsert_run was called.
        if rows[0][4] is None:  # created_at column
            return None
        return _row_to_run(tuple(rows[0]))

    def upsert_run(
        self,
        *,
        parent_run_id: Optional[str] = None,
        forked_at_event_id: Optional[str] = None,
        label: Optional[str] = None,
        created_at: str,
        goal: Optional[str] = None,
        frame_id: Optional[str] = None,
    ) -> None:
        # MERGE preserves any next_seq the run accumulated from append()s.
        # COALESCE on goal/frame_id mirrors the SQLite/Postgres
        # 'don't clobber with NULL' behaviour.
        self._source.query(
            """
            MERGE (r:Run {run_id:$rid})
            SET r.parent_run_id      = $parent_run_id,
                r.forked_at_event_id = $forked_at_event_id,
                r.label              = $label,
                r.created_at         = $created_at,
                r.goal               = coalesce($goal, r.goal),
                r.frame_id           = coalesce($frame_id, r.frame_id)
            """,
            {
                "rid": self.run_id,
                "parent_run_id": parent_run_id,
                "forked_at_event_id": forked_at_event_id,
                "label": label,
                "created_at": created_at,
                "goal": goal,
                "frame_id": frame_id,
            },
        )

    # ---------- graph-level helpers ----------

    @classmethod
    def list_runs(cls, target: Any) -> list[RunRecord]:
        source = _GraphSource(target)
        try:
            _ensure_schema(source)
            res = source.query(
                f"MATCH (r:Run) WHERE r.created_at IS NOT NULL "
                f"RETURN {_RUN_RETURN} ORDER BY r.created_at"
            )
            return [_row_to_run(tuple(row)) for row in (res.result_set or [])]
        finally:
            source.close()

    @classmethod
    def most_recent_run_id(cls, target: Any) -> Optional[str]:
        source = _GraphSource(target)
        try:
            _ensure_schema(source)
            res = source.query(
                """
                MATCH (r:Run) WHERE r.created_at IS NOT NULL
                OPTIONAL MATCH (r)-[:HAS_EVENT]->(e:Event)
                WITH r, max(e.seq) AS last_seq
                RETURN r.run_id
                ORDER BY (last_seq IS NULL), last_seq DESC, r.created_at DESC
                LIMIT 1
                """
            )
            rows = res.result_set or []
            return rows[0][0] if rows else None
        finally:
            source.close()

    @classmethod
    def fork_run(
        cls,
        target: Any,
        *,
        parent_run_id: str,
        new_run_id: str,
        at_event_id: str,
        label: Optional[str],
        created_at: str,
    ) -> int:
        """Copy events from parent_run_id up to and including at_event_id
        into new_run_id. CONTRACT v0.5 #11 (rows copied, not shared).
        Returns the number of events copied.
        """
        source = _GraphSource(target)
        try:
            _ensure_schema(source)
            # Resolve the cut seq up front so the error message has the
            # right context if at_event_id doesn't exist.
            cut_res = source.query(
                "MATCH (e:Event {id:$id, run_id:$rid}) RETURN e.seq LIMIT 1",
                {"id": at_event_id, "rid": parent_run_id},
            )
            cut_rows = cut_res.result_set or []
            if not cut_rows:
                from activegraph.store.errors import EventNotFoundError
                raise EventNotFoundError(
                    f"event {at_event_id!r} not found in run {parent_run_id!r}",
                    what_failed=(
                        f"Cannot fork run {parent_run_id!r} at event "
                        f"{at_event_id!r}: that event does not exist in the run."
                    ),
                    why=(
                        "Forking takes a parent run and copies events up to and "
                        "including --at-event into a new run. The framework "
                        "refuses to fork at an unknown event id rather than "
                        "guess where the user meant — that would produce a "
                        "fork that doesn't share lineage with its claimed parent."
                    ),
                    how_to_fix=(
                        f"List the events in the parent run to find a valid "
                        f"fork point:\n"
                        f"    activegraph inspect <store-url> --run-id {parent_run_id} --tail 100\n"
                        f"\n"
                        f"Then re-issue the fork with a valid event id."
                    ),
                    context={
                        "event_id": at_event_id,
                        "run_id": parent_run_id,
                        "operation": "fork",
                        "driver": "falkordb",
                    },
                )
            cut_seq = int(cut_rows[0][0])

            parent_meta = source.query(
                "MATCH (r:Run {run_id:$rid}) RETURN r.goal, r.frame_id LIMIT 1",
                {"rid": parent_run_id},
            )
            pm_rows = parent_meta.result_set or []
            goal = pm_rows[0][0] if pm_rows else None
            frame_id = pm_rows[0][1] if pm_rows else None

            # Create the new Run node with metadata.
            source.query(
                """
                MERGE (r:Run {run_id:$new_rid})
                SET r.parent_run_id      = $parent_rid,
                    r.forked_at_event_id = $at_event,
                    r.label              = $label,
                    r.created_at         = $created_at,
                    r.goal               = $goal,
                    r.frame_id           = $frame_id,
                    r.next_seq           = $next_seq
                """,
                {
                    "new_rid": new_run_id,
                    "parent_rid": parent_run_id,
                    "at_event": at_event_id,
                    "label": label,
                    "created_at": created_at,
                    "goal": goal,
                    "frame_id": frame_id,
                    "next_seq": cut_seq + 1,
                },
            )

            # Copy events up to and including cut_seq. Same logical event
            # ids; UNIQUE(id, run_id) makes that safe across runs.
            copy_res = source.query(
                """
                MATCH (parent:Run {run_id:$parent_rid})-[:HAS_EVENT]->(src:Event)
                WHERE src.seq <= $cut_seq
                WITH src
                ORDER BY src.seq
                MATCH (newr:Run {run_id:$new_rid})
                CREATE (dst:Event {
                    id: src.id, type: src.type, actor: src.actor,
                    payload: src.payload, frame_id: src.frame_id,
                    caused_by: src.caused_by, timestamp: src.timestamp,
                    run_id: $new_rid, seq: src.seq
                })
                CREATE (newr)-[:HAS_EVENT]->(dst)
                RETURN count(dst)
                """,
                {
                    "parent_rid": parent_run_id,
                    "cut_seq": cut_seq,
                    "new_rid": new_run_id,
                },
            )
            cr_rows = copy_res.result_set or []
            return int(cr_rows[0][0]) if cr_rows else 0
        finally:
            source.close()
