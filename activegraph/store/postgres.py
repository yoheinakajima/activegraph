"""Postgres-backed EventStore. CONTRACT v0.8 #1.

Mirrors the SQLite schema with Postgres-native types: BIGSERIAL for
seq, TIMESTAMPTZ for timestamps, JSONB for payloads. Same UNIQUE
constraint (id, run_id) so logical event ids stay scoped to runs and
forks preserve them (v0.5 #12).

Schema is the contract; both implementations conform. The EventStore
protocol surface is identical to SQLiteEventStore.

Connection management is the user's job:
  - Pass a URL  → store owns one dedicated connection.
  - Pass a psycopg.Connection → store does not own its lifecycle.
  - Pass a psycopg_pool.ConnectionPool → store getconn / putconn per op.

psycopg 3.x is required (>=3.1,<4). Install with
``pip install 'activegraph[postgres]'``.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional

from activegraph.core.event import Event
from activegraph.store.base import RunRecord


SCHEMA_VERSION = "1"


_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS events (
        seq BIGSERIAL PRIMARY KEY,
        id TEXT NOT NULL,
        type TEXT NOT NULL,
        actor TEXT,
        payload JSONB NOT NULL,
        frame_id TEXT,
        caused_by TEXT,
        timestamp TIMESTAMPTZ NOT NULL,
        run_id TEXT NOT NULL,
        UNIQUE(id, run_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)",
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        parent_run_id TEXT,
        forked_at_event_id TEXT,
        label TEXT,
        created_at TIMESTAMPTZ NOT NULL,
        goal TEXT,
        frame_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
]


def _require_psycopg() -> Any:
    try:
        import psycopg  # type: ignore
    except ImportError as e:  # pragma: no cover — exercised only without dep
        from activegraph.errors import MissingOptionalDependency
        raise MissingOptionalDependency(
            package="psycopg",
            feature="PostgresEventStore",
            extras="postgres",
        ) from e
    return psycopg


class _ConnectionSource:
    """Adapter over the three accepted connection shapes.

    Hides whether we're holding a URL, a single connection, or a pool.
    Every public method on PostgresEventStore goes through
    ``with self._source.cursor() as cur:`` so the underlying lifecycle
    is uniform.
    """

    def __init__(self, target: Any) -> None:
        psycopg = _require_psycopg()
        self._psycopg = psycopg
        self._owned_conn: Any = None  # We created it; we close it.
        self._pool: Any = None
        self._conn: Any = None

        if isinstance(target, str):
            # URL — open a dedicated connection. We own it.
            self._owned_conn = psycopg.connect(target, autocommit=True)
            self._conn = self._owned_conn
        elif hasattr(target, "getconn") and hasattr(target, "putconn"):
            # Looks like a pool.
            self._pool = target
        elif hasattr(target, "cursor"):
            # Looks like a Connection. Don't take ownership.
            self._conn = target
        else:
            from activegraph.runtime.config_errors import InvalidArgumentType
            type_name = type(target).__name__
            target_repr = repr(target)
            if len(target_repr) > 80:
                target_repr = target_repr[:77] + "..."
            raise InvalidArgumentType(
                f"PostgresEventStore target has wrong type (got {type_name})",
                what_failed=(
                    f"PostgresEventStore was constructed with a target of "
                    f"type {type_name}:\n  value: {target_repr}\n"
                    f"  type:  {type_name}\n"
                    f"Accepted types are: a `postgres://...` URL string, a "
                    f"`psycopg.Connection`, or a `psycopg_pool.ConnectionPool`."
                ),
                why=(
                    "PostgresEventStore's constructor branches on the "
                    "target's type — strings open a fresh connection, "
                    "Connections are borrowed without ownership, and "
                    "ConnectionPools are checked out per operation. An "
                    "unknown type has no defined connection lifecycle, and "
                    "a fuzzy match would silently leak connections or "
                    "double-close them."
                ),
                how_to_fix=(
                    "Pass one of:\n"
                    "    PostgresEventStore('postgres://host/dbname', run_id=...)\n"
                    "    PostgresEventStore(my_psycopg_connection, run_id=...)\n"
                    "    PostgresEventStore(my_connection_pool, run_id=...)\n"
                    "\n"
                    "If you already have a SQLAlchemy engine or another "
                    "abstraction, extract a raw psycopg.Connection from it "
                    "and pass that."
                ),
                context={"type": type_name, "repr": target_repr},
            )

    # ---- ctx mgrs ----

    def cursor(self):  # returns a context manager yielding a cursor
        return _CursorCtx(self)

    def transaction(self):
        return _TxCtx(self)

    # ---- lifecycle ----

    def close(self) -> None:
        if self._owned_conn is not None:
            try:
                self._owned_conn.close()
            except Exception:
                pass
            self._owned_conn = None
            self._conn = None


class _CursorCtx:
    def __init__(self, src: _ConnectionSource) -> None:
        self._src = src
        self._conn: Any = None
        self._cur: Any = None
        self._from_pool = False

    def __enter__(self):
        if self._src._pool is not None:
            self._conn = self._src._pool.getconn()
            self._from_pool = True
            # Pool conns are typically autocommit=False by default. Set
            # autocommit per-op so single statements don't sit in an
            # implicit txn. Within transaction() we override.
            self._conn.autocommit = True
        else:
            self._conn = self._src._conn
        self._cur = self._conn.cursor()
        return self._cur

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._cur is not None:
                self._cur.close()
        finally:
            if self._from_pool and self._conn is not None:
                self._src._pool.putconn(self._conn)


class _TxCtx:
    """Run a block in a single transaction; commit on success, rollback on
    exception. For pool-backed sources this borrows one connection and
    keeps autocommit off for the duration.
    """

    def __init__(self, src: _ConnectionSource) -> None:
        self._src = src
        self._conn: Any = None
        self._from_pool = False

    def __enter__(self):
        if self._src._pool is not None:
            self._conn = self._src._pool.getconn()
            self._from_pool = True
            self._conn.autocommit = False
        else:
            self._conn = self._src._conn
            # If the source connection is in autocommit mode, switching is
            # cheap and reversible. Save and restore.
            self._prev_autocommit = getattr(self._conn, "autocommit", True)
            self._conn.autocommit = False
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            if self._from_pool:
                self._conn.autocommit = True  # reset before returning
                self._src._pool.putconn(self._conn)
            else:
                self._conn.autocommit = self._prev_autocommit


def _ensure_schema(source: _ConnectionSource) -> None:
    with source.cursor() as cur:
        for stmt in _SCHEMA_STATEMENTS:
            cur.execute(stmt)
        cur.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', %s)",
                (SCHEMA_VERSION,),
            )
        elif row[0] != SCHEMA_VERSION:
            from activegraph import __version__ as _aw_version
            from activegraph.store.errors import SchemaVersionMismatch
            raise SchemaVersionMismatch(
                f"postgres store schema_version {row[0]!r} does not match this build's expected {SCHEMA_VERSION!r}",
                what_failed=(
                    f"The Postgres store records schema_version={row[0]!r} in its meta "
                    f"table, but activegraph {_aw_version} expects "
                    f"schema_version={SCHEMA_VERSION!r}."
                ),
                why=(
                    "The store schema evolves with the framework. The runtime "
                    "refuses to read a store with a different schema_version rather "
                    "than risk silent data loss — a newer framework might interpret "
                    "columns differently than the writer did, and an older framework "
                    "might drop fields it doesn't recognize."
                ),
                how_to_fix=(
                    f"One of three actions:\n"
                    f"  1. Install the activegraph version that wrote this store.\n"
                    f"  2. Migrate runs to a fresh database written by this build:\n"
                    f"     activegraph migrate <src-url> <new-dst-url>\n"
                    f"  3. If the database is expendable, drop and re-create.\n"
                    f"\n"
                    f"Schema version history is documented in CHANGELOG.md."
                ),
                context={
                    "found_version": row[0],
                    "expected_version": SCHEMA_VERSION,
                    "activegraph_version": _aw_version,
                    "driver": "postgres",
                },
            )


def _row_to_event(row: tuple) -> Event:
    # Columns: id, type, actor, payload, frame_id, caused_by, timestamp
    id_, type_, actor, payload, frame_id, caused_by, ts = row
    if isinstance(payload, (bytes, str)):
        payload = json.loads(payload)
    # TIMESTAMPTZ comes back as a datetime; ISO-format for consistency
    # with SQLite's text storage.
    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    return Event(
        id=id_,
        type=type_,
        payload=payload,
        actor=actor,
        frame_id=frame_id,
        caused_by=caused_by,
        timestamp=ts_str,
    )


def _row_to_run(row: tuple) -> RunRecord:
    run_id, parent_run_id, forked_at_event_id, label, created_at, goal, frame_id = row
    return RunRecord(
        run_id=run_id,
        parent_run_id=parent_run_id,
        forked_at_event_id=forked_at_event_id,
        label=label,
        created_at=created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        goal=goal,
        frame_id=frame_id,
    )


_EVENT_COLUMNS = "id, type, actor, payload, frame_id, caused_by, timestamp"
_RUN_COLUMNS = (
    "run_id, parent_run_id, forked_at_event_id, label, created_at, goal, frame_id"
)


class PostgresEventStore:
    """Per-run view onto a Postgres-backed event log. CONTRACT v0.8 #1."""

    def __init__(self, target: Any, run_id: str) -> None:
        self._source = _ConnectionSource(target)
        self.run_id = run_id
        _ensure_schema(self._source)

    # ---------- EventStore protocol ----------

    def append(self, event: Event) -> None:
        psycopg = self._source._psycopg
        with self._source.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO events ({_EVENT_COLUMNS}, run_id)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                """,
                (
                    event.id,
                    event.type,
                    event.actor,
                    json.dumps(event.payload),
                    event.frame_id,
                    event.caused_by,
                    event.timestamp,
                    self.run_id,
                ),
            )

    def iter_events(
        self,
        after: Optional[str] = None,
        until: Optional[str] = None,
    ) -> Iterator[Event]:
        clauses = ["run_id = %s"]
        params: list[Any] = [self.run_id]
        if after is not None:
            clauses.append("seq > %s")
            params.append(self._seq_of(after))
        if until is not None:
            clauses.append("seq <= %s")
            params.append(self._seq_of(until))
        sql = (
            f"SELECT {_EVENT_COLUMNS} FROM events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY seq"
        )
        # Materialize rather than streaming so the cursor closes promptly
        # under pooled connections. Event logs are bounded.
        with self._source.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        for row in rows:
            yield _row_to_event(row)

    def get_event(self, event_id: str) -> Optional[Event]:
        with self._source.cursor() as cur:
            cur.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE id = %s AND run_id = %s",
                (event_id, self.run_id),
            )
            row = cur.fetchone()
        return _row_to_event(row) if row else None

    def count(self) -> int:
        with self._source.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = %s", (self.run_id,)
            )
            row = cur.fetchone()
        return int(row[0])

    def truncate_after(self, event_id: str) -> None:
        seq = self._seq_of(event_id)
        with self._source.cursor() as cur:
            cur.execute(
                "DELETE FROM events WHERE run_id = %s AND seq > %s",
                (self.run_id, seq),
            )

    def close(self) -> None:
        self._source.close()

    def _seq_of(self, event_id: str) -> int:
        with self._source.cursor() as cur:
            cur.execute(
                "SELECT seq FROM events WHERE id = %s AND run_id = %s",
                (event_id, self.run_id),
            )
            row = cur.fetchone()
        if row is None:
            from activegraph.store.errors import EventNotFoundError
            raise EventNotFoundError(
                f"event {event_id!r} not found in run {self.run_id!r}",
                what_failed=(
                    f"The Postgres store has no event with id {event_id!r} in "
                    f"run {self.run_id!r}."
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
                    "driver": "postgres",
                },
            )
        return int(row[0])

    # ---------- v0.5 helpers (per-run) ----------

    def get_run(self) -> Optional[RunRecord]:
        with self._source.cursor() as cur:
            cur.execute(
                f"SELECT {_RUN_COLUMNS} FROM runs WHERE run_id = %s",
                (self.run_id,),
            )
            row = cur.fetchone()
        return _row_to_run(row) if row else None

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
        with self._source.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO runs ({_RUN_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    parent_run_id      = COALESCE(EXCLUDED.parent_run_id, runs.parent_run_id),
                    forked_at_event_id = COALESCE(EXCLUDED.forked_at_event_id, runs.forked_at_event_id),
                    label              = COALESCE(EXCLUDED.label, runs.label),
                    goal               = COALESCE(EXCLUDED.goal, runs.goal),
                    frame_id           = COALESCE(EXCLUDED.frame_id, runs.frame_id)
                """,
                (
                    self.run_id,
                    parent_run_id,
                    forked_at_event_id,
                    label,
                    created_at,
                    goal,
                    frame_id,
                ),
            )

    # ---------- url-level helpers ----------

    @classmethod
    def list_runs(cls, target: Any) -> list[RunRecord]:
        source = _ConnectionSource(target)
        try:
            _ensure_schema(source)
            with source.cursor() as cur:
                cur.execute(
                    f"SELECT {_RUN_COLUMNS} FROM runs ORDER BY created_at"
                )
                rows = cur.fetchall()
            return [_row_to_run(r) for r in rows]
        finally:
            source.close()

    @classmethod
    def most_recent_run_id(cls, target: Any) -> Optional[str]:
        source = _ConnectionSource(target)
        try:
            _ensure_schema(source)
            with source.cursor() as cur:
                cur.execute(
                    """
                    SELECT runs.run_id
                    FROM runs
                    LEFT JOIN (
                        SELECT run_id, MAX(seq) AS last_seq
                        FROM events GROUP BY run_id
                    ) e ON e.run_id = runs.run_id
                    ORDER BY (e.last_seq IS NULL), e.last_seq DESC,
                             runs.created_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
            return row[0] if row else None
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
        into new_run_id. CONTRACT v0.5 #11 (rows are copied, not shared).
        Runs in a single transaction; returns the number of events copied.
        """
        source = _ConnectionSource(target)
        try:
            _ensure_schema(source)
            with source.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT seq FROM events WHERE id = %s AND run_id = %s",
                        (at_event_id, parent_run_id),
                    )
                    cut = cur.fetchone()
                    if cut is None:
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
                                "driver": "postgres",
                            },
                        )
                    cur.execute(
                        "SELECT goal, frame_id FROM runs WHERE run_id = %s",
                        (parent_run_id,),
                    )
                    pr = cur.fetchone()
                    goal = pr[0] if pr else None
                    frame_id = pr[1] if pr else None
                    cur.execute(
                        f"""
                        INSERT INTO runs ({_RUN_COLUMNS})
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            new_run_id,
                            parent_run_id,
                            at_event_id,
                            label,
                            created_at,
                            goal,
                            frame_id,
                        ),
                    )
                    cur.execute(
                        f"""
                        INSERT INTO events ({_EVENT_COLUMNS}, run_id)
                        SELECT {_EVENT_COLUMNS}, %s
                        FROM events
                        WHERE run_id = %s AND seq <= %s
                        ORDER BY seq
                        """,
                        (new_run_id, parent_run_id, cut[0]),
                    )
                    n = cur.rowcount
                    return int(n)
        finally:
            source.close()
