"""SQLite-backed EventStore. CONTRACT v0.5 #3 (schema locked).

Schema lives in `_SCHEMA` below; any change requires bumping the
`schema_version` row in the `meta` table.

  events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
         id TEXT NOT NULL,
         type TEXT NOT NULL,
         actor TEXT,
         payload TEXT NOT NULL,    -- JSON
         frame_id TEXT,
         caused_by TEXT,
         timestamp TEXT NOT NULL,
         run_id TEXT NOT NULL,
         UNIQUE(id, run_id))

  runs(run_id TEXT PRIMARY KEY,
       parent_run_id TEXT,
       forked_at_event_id TEXT,
       label TEXT,
       created_at TEXT NOT NULL,
       goal TEXT,
       frame_id TEXT)

  meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)
  -- carries schema_version since day one.

WAL is enabled on every open. `seq` is the projection ordering authority,
not `timestamp` — wall clocks can lie; AUTOINCREMENT cannot.

NOTE on the UNIQUE constraint (CONTRACT v0.5 diff #3): the locked schema
said `id TEXT NOT NULL UNIQUE`. That clashes with decision #12 (logical
IDs are scoped to run_id; a fork preserves the parent's `evt_017`). We
keep the column shape and intent — IDs are unique within a run — but the
constraint is `UNIQUE(id, run_id)`. Stored ids are the logical ids; no
prefixing, no hidden scoping.

A SQLiteEventStore is scoped to ONE `run_id`. Other runs in the same file
are accessed via separate SQLiteEventStore instances pointing at the same
path. Classmethods below (`list_runs`, `most_recent_run_id`, `fork_run`)
are file-level helpers.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterator, Optional

from activegraph.core.event import Event
from activegraph.store.base import RunRecord
from activegraph.store.serde import decode_event, encode_event


SCHEMA_VERSION = "1"


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL,
        type TEXT NOT NULL,
        actor TEXT,
        payload TEXT NOT NULL,
        frame_id TEXT,
        caused_by TEXT,
        timestamp TEXT NOT NULL,
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
        created_at TEXT NOT NULL,
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
    # v1.5 compaction (CONTRACT v1.5 #2): the archive tier and the
    # snapshot sidecar. Additive IF NOT EXISTS tables — old runtimes
    # reading a new file simply never touch them, so schema_version
    # stays "1".
    """
    CREATE TABLE IF NOT EXISTS events_archive (
        seq INTEGER,
        id TEXT NOT NULL,
        type TEXT NOT NULL,
        actor TEXT,
        payload TEXT NOT NULL,
        frame_id TEXT,
        caused_by TEXT,
        timestamp TEXT NOT NULL,
        run_id TEXT NOT NULL,
        archived_at TEXT NOT NULL,
        UNIQUE(id, run_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_archive_run ON events_archive(run_id, seq)",
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        state_hash TEXT PRIMARY KEY,
        blob TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    # WAL + synchronous=NORMAL: group-committed fsyncs, no fsync per write.
    # Crash-safe across process crashes; OS crash may lose the last committed
    # transactions but never corrupts the file. Sufficient for an event log
    # and ~25x faster than the default FULL.
    conn.execute("PRAGMA synchronous=NORMAL")
    for stmt in _SCHEMA:
        conn.execute(stmt)
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
    elif row[0] != SCHEMA_VERSION:
        from activegraph import __version__ as _aw_version
        from activegraph.store.errors import SchemaVersionMismatch
        raise SchemaVersionMismatch(
            f"sqlite store schema_version {row[0]!r} does not match this build's expected {SCHEMA_VERSION!r}",
            what_failed=(
                f"The SQLite store records schema_version={row[0]!r} in its meta table, "
                f"but activegraph {_aw_version} expects schema_version={SCHEMA_VERSION!r}."
            ),
            why=(
                "The store file format evolves with the framework. The runtime "
                "refuses to read a store with a different schema_version rather "
                "than risk silent data loss — a newer framework might interpret "
                "columns differently than the writer did, and an older framework "
                "might drop fields it doesn't recognize. Either direction would "
                "corrupt the audit trail."
            ),
            how_to_fix=(
                f"One of three actions:\n"
                f"  1. Install the activegraph version that wrote this store\n"
                f"     (whichever shipped schema_version={row[0]!r}).\n"
                f"  2. Migrate the run to a store written by this build:\n"
                f"     activegraph migrate <src-url> <new-dst-url>\n"
                f"     The destination is written with the current schema.\n"
                f"  3. If the store is empty or expendable, delete and re-run.\n"
                f"\n"
                f"Schema version history is documented in CHANGELOG.md."
            ),
            context={
                "found_version": row[0],
                "expected_version": SCHEMA_VERSION,
                "activegraph_version": _aw_version,
                "driver": "sqlite",
            },
        )


def _row_to_event(row: sqlite3.Row) -> Event:
    return decode_event(
        {
            "id": row["id"],
            "type": row["type"],
            "payload": row["payload"],
            "actor": row["actor"],
            "frame_id": row["frame_id"],
            "caused_by": row["caused_by"],
            "timestamp": row["timestamp"],
        }
    )


def _row_to_run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        parent_run_id=row["parent_run_id"],
        forked_at_event_id=row["forked_at_event_id"],
        label=row["label"],
        created_at=row["created_at"],
        goal=row["goal"],
        frame_id=row["frame_id"],
    )


class SQLiteEventStore:
    """Per-run view onto a SQLite-backed event log.

    Direct construction expects an explicit ``path`` and ``run_id``.
    For most cases prefer ``Runtime(graph, persist_to=...)``, which
    opens the store, mints a ``run_id`` if needed, and wires it onto
    the runtime — the v1.0.1 user-test surfaced that constructing
    ``SQLiteEventStore`` by hand is a low-frequency operator path,
    not the happy path.
    """

    def __init__(
        self, path: Optional[str] = None, run_id: Optional[str] = None
    ) -> None:
        if path is None or run_id is None:
            missing = "run_id" if path is not None else "path and a run_id"
            raise TypeError(
                f"SQLiteEventStore requires a {missing}. For most cases, "
                f"use Runtime(graph, persist_to='path/to/trace.sqlite') "
                f"instead, which handles run_id automatically. If you "
                f"need a per-run handle (migration, conformance test, "
                f"trace inspection), pass both explicitly: "
                f"SQLiteEventStore('path/to/trace.sqlite', run_id='run_...')."
            )
        self.path = path
        self.run_id = run_id
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        _ensure_schema(self._conn)

    # ---------- EventStore protocol ----------

    def append(self, event: Event) -> None:
        row = encode_event(event)
        self._conn.execute(
            """
            INSERT INTO events (id, type, actor, payload, frame_id, caused_by, timestamp, run_id)
            VALUES (:id, :type, :actor, :payload, :frame_id, :caused_by, :timestamp, :run_id)
            """,
            {**row, "run_id": self.run_id},
        )

    def iter_events(
        self,
        after: Optional[str] = None,
        until: Optional[str] = None,
    ) -> Iterator[Event]:
        clauses = ["run_id = ?"]
        params: list[Any] = [self.run_id]
        if after is not None:
            clauses.append("seq > ?")
            params.append(self._seq_of(after))
        if until is not None:
            clauses.append("seq <= ?")
            params.append(self._seq_of(until))
        sql = "SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY seq"
        for row in self._conn.execute(sql, params):
            yield _row_to_event(row)

    def get_event(self, event_id: str) -> Optional[Event]:
        row = self._conn.execute(
            "SELECT * FROM events WHERE id = ? AND run_id = ?",
            (event_id, self.run_id),
        ).fetchone()
        return _row_to_event(row) if row else None

    def count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?", (self.run_id,)
        ).fetchone()
        return int(row[0])

    def truncate_after(self, event_id: str) -> None:
        seq = self._seq_of(event_id)
        self._conn.execute(
            "DELETE FROM events WHERE run_id = ? AND seq > ?",
            (self.run_id, seq),
        )

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            pass

    def _seq_of(self, event_id: str) -> int:
        row = self._conn.execute(
            "SELECT seq FROM events WHERE id = ? AND run_id = ?",
            (event_id, self.run_id),
        ).fetchone()
        if row is None:
            from activegraph.store.errors import EventNotFoundError
            raise EventNotFoundError(
                f"event {event_id!r} not found in run {self.run_id!r}",
                what_failed=(
                    f"The SQLite store has no event with id {event_id!r} in "
                    f"run {self.run_id!r}."
                ),
                why=(
                    "Event ids are the framework's addressing primitive. The "
                    "store refuses to return a default for an unknown id — that "
                    "would silently corrupt the audit trail and any downstream "
                    "fork or replay."
                ),
                how_to_fix=(
                    "Check the event id against what's actually in the run:\n"
                    f"    activegraph inspect <store-url> --run-id {self.run_id} --tail 100\n"
                    "\n"
                    "Common causes: typo in a hand-typed id, referencing an id "
                    "from a different run, or a run truncated by an earlier fork."
                ),
                context={
                    "event_id": event_id,
                    "run_id": self.run_id,
                    "driver": "sqlite",
                },
            )
        return int(row["seq"])

    # ---------- v0.5 helpers (per-run) ----------

    def get_run(self) -> Optional[RunRecord]:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (self.run_id,)
        ).fetchone()
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
        """Insert or update this run's row. ``None`` never clears a
        stored value (v1.3 fix): ``Runtime.load`` upserts with only
        ``created_at``, and before the COALESCEs below that erased a
        fork's ``parent_run_id`` / ``forked_at_event_id`` / ``label``
        on every reload — silently destroying the lineage records
        ``promote()`` verifies against.
        """
        self._conn.execute(
            """
            INSERT INTO runs (run_id, parent_run_id, forked_at_event_id, label, created_at, goal, frame_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                parent_run_id      = COALESCE(excluded.parent_run_id, runs.parent_run_id),
                forked_at_event_id = COALESCE(excluded.forked_at_event_id, runs.forked_at_event_id),
                label              = COALESCE(excluded.label, runs.label),
                goal               = COALESCE(excluded.goal, runs.goal),
                frame_id           = COALESCE(excluded.frame_id, runs.frame_id)
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

    # ---------- v1.5 compaction (CONTRACT v1.5 #2) ----------

    def put_snapshot(self, state_hash: str, blob: str, *, created_at: str) -> None:
        """Store a snapshot blob keyed by its state hash (idempotent)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO snapshots(state_hash, blob, created_at) "
            "VALUES (?, ?, ?)",
            (state_hash, blob, created_at),
        )

    def get_snapshot(self, state_hash: str) -> Optional[str]:
        """The snapshot blob for ``state_hash``, or None."""
        row = self._conn.execute(
            "SELECT blob FROM snapshots WHERE state_hash = ?", (state_hash,)
        ).fetchone()
        return row["blob"] if row else None

    def archive_prefix(self, before_seq: int, *, archived_at: str) -> int:
        """Move this run's rows with seq < ``before_seq`` to the archive
        tier, in one transaction. Idempotent (re-running moves nothing).
        Returns the number of rows moved."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO events_archive "
                "(seq, id, type, actor, payload, frame_id, caused_by, "
                " timestamp, run_id, archived_at) "
                "SELECT seq, id, type, actor, payload, frame_id, caused_by, "
                "       timestamp, run_id, ? FROM events "
                "WHERE run_id = ? AND seq < ?",
                (archived_at, self.run_id, before_seq),
            )
            moved = cur.rowcount
            self._conn.execute(
                "DELETE FROM events WHERE run_id = ? AND seq < ?",
                (self.run_id, before_seq),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return int(moved)

    def archive_run(self, *, archived_at: str) -> int:
        """Move ALL of this run's rows to the archive tier (retire)."""
        return self.archive_prefix(2**62, archived_at=archived_at)

    def iter_archived(self) -> Iterator[Event]:
        """This run's archived events, in original seq order."""
        cur = self._conn.execute(
            "SELECT * FROM events_archive WHERE run_id = ? ORDER BY seq",
            (self.run_id,),
        )
        for row in cur:
            yield _row_to_event(row)

    def has_archived(self) -> bool:
        """True when any of this run's events sit in the archive tier."""
        row = self._conn.execute(
            "SELECT 1 FROM events_archive WHERE run_id = ? LIMIT 1",
            (self.run_id,),
        ).fetchone()
        return row is not None

    # ---------- file-level helpers ----------

    @classmethod
    def list_runs(cls, path: str) -> list[RunRecord]:
        conn = sqlite3.connect(path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        try:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at").fetchall()
            return [_row_to_run(r) for r in rows]
        finally:
            conn.close()

    @classmethod
    def most_recent_run_id(cls, path: str) -> Optional[str]:
        conn = sqlite3.connect(path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        try:
            row = conn.execute(
                """
                SELECT runs.run_id
                FROM runs
                LEFT JOIN (
                    SELECT run_id, MAX(seq) AS last_seq FROM events GROUP BY run_id
                ) e ON e.run_id = runs.run_id
                ORDER BY e.last_seq IS NULL, e.last_seq DESC, runs.created_at DESC
                LIMIT 1
                """
            ).fetchone()
            return row["run_id"] if row else None
        finally:
            conn.close()

    @classmethod
    def fork_run(
        cls,
        path: str,
        *,
        parent_run_id: str,
        new_run_id: str,
        at_event_id: str,
        label: Optional[str],
        created_at: str,
    ) -> int:
        """Copy events from parent_run_id up to and including at_event_id
        into new_run_id (CONTRACT v0.5 #11: copy rows, no row-sharing).

        Returns the number of events copied.
        """
        conn = sqlite3.connect(path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        try:
            cut = conn.execute(
                "SELECT seq FROM events WHERE id = ? AND run_id = ?",
                (at_event_id, parent_run_id),
            ).fetchone()
            if cut is None:
                from activegraph.store.errors import EventNotFoundError
                archived = conn.execute(
                    "SELECT 1 FROM events_archive WHERE id = ? AND run_id = ?",
                    (at_event_id, parent_run_id),
                ).fetchone()
                if archived is not None:
                    # CONTRACT v1.5 #2: pre-snapshot fork points refuse
                    # loudly — compaction narrows where you can branch
                    # history, never what state is.
                    raise EventNotFoundError(
                        f"event {at_event_id!r} of run {parent_run_id!r} is "
                        f"archived below the compaction horizon",
                        what_failed=(
                            f"Cannot fork run {parent_run_id!r} at event "
                            f"{at_event_id!r}: the run was compacted and that "
                            f"event now lives in the archive tier."
                        ),
                        why=(
                            "Forking copies events up to the fork point from "
                            "the hot log. A compacted run's pre-snapshot "
                            "prefix is summarized by the snapshot; branching "
                            "below that horizon would need the archived rows "
                            "restored first (CONTRACT v1.5 #2)."
                        ),
                        how_to_fix=(
                            "Fork at the snapshot event or any later event, "
                            "or restore the archived prefix (operator action "
                            "on the events_archive table) before forking "
                            "below the horizon."
                        ),
                        context={
                            "run_id": parent_run_id,
                            "at_event": at_event_id,
                            "archived": True,
                        },
                    )
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
                        "driver": "sqlite",
                    },
                )
            parent_row = conn.execute(
                "SELECT goal, frame_id FROM runs WHERE run_id = ?", (parent_run_id,)
            ).fetchone()
            goal = parent_row["goal"] if parent_row else None
            frame_id = parent_row["frame_id"] if parent_row else None
            conn.execute(
                """
                INSERT INTO runs (run_id, parent_run_id, forked_at_event_id, label, created_at, goal, frame_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
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
            # Same logical event ids; UNIQUE(id, run_id) makes that safe.
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id = ? AND seq <= ? ORDER BY seq",
                (parent_run_id, cut["seq"]),
            ).fetchall()
            n = 0
            for r in rows:
                conn.execute(
                    """
                    INSERT INTO events (id, type, actor, payload, frame_id, caused_by, timestamp, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r["id"],
                        r["type"],
                        r["actor"],
                        r["payload"],
                        r["frame_id"],
                        r["caused_by"],
                        r["timestamp"],
                        new_run_id,
                    ),
                )
                n += 1
            return n
        finally:
            conn.close()
