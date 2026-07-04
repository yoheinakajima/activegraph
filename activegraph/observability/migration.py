"""Cross-store migration. CONTRACT v0.8 #5 (revised: transaction-per-run),
+ v1.0 CLI follow-on (--skip-corrupted).

Each run migrates in a single transaction against the destination. If a
run fails partway, that run's destination state is unchanged. Writes
use ``INSERT ... ON CONFLICT DO NOTHING`` against ``UNIQUE(id, run_id)``
so re-running after a failure is idempotent. Runs migrate independently
— a bad run does not block the others. A structured per-run report is
returned (and printed by the CLI; ``--json`` dumps the same shape).

Migration is one-directional and explicit. No sync mode, no rollback,
no automatic recovery. To go back, migrate the other direction.

v1.0 adds opt-in ``skip_corrupted`` mode: a corrupted-payload row no
longer halts a run's migration. The corrupt row is recorded in the
per-run report's ``skipped_events`` list; surrounding events still
migrate. Driver-specific raw row iteration is required because Python
generators die after raising — see ``_iter_*_skip_corrupted`` helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from activegraph.core.event import Event
from activegraph.store.base import EventStore, RunRecord
from activegraph.store.errors import CorruptedEventPayloadError
from activegraph.store.serde import decode_event
from activegraph.store.url import parse_store_url


@dataclass(frozen=True)
class MigrationRunReport:
    """Outcome of migrating one run between stores.

    ``status`` is ``ok`` (copied), ``skipped`` (already present at the
    destination), or ``failed`` (with ``error`` set);
    ``events_migrated`` counts rows written this invocation, so a
    rerun after a partial failure reports only the delta.
    ``skipped_events`` lists ids dropped under ``--skip-corrupted``.
    """

    run_id: str
    status: str  # "ok" | "skipped" | "failed"
    events_migrated: int
    error: Optional[str] = None
    # v1.0 CLI follow-on: event ids that could not be decoded and were
    # skipped under --skip-corrupted. Empty on a clean migration.
    skipped_events: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationReport:
    """Aggregate result of a store-to-store migration.

    One :class:`MigrationRunReport` per run found at the source;
    ``ok`` is True when nothing failed (skips are fine), and
    ``failures`` filters the runs that need attention. Value object
    returned by ``migrate`` and rendered by the CLI.
    """

    source_url: str
    dest_url: str
    runs: tuple[MigrationRunReport, ...]

    @property
    def ok(self) -> bool:
        return all(r.status != "failed" for r in self.runs)

    @property
    def failures(self) -> tuple[MigrationRunReport, ...]:
        return tuple(r for r in self.runs if r.status == "failed")


def migrate(
    source_url: str,
    dest_url: str,
    *,
    only_run_ids: Optional[list[str]] = None,
    on_progress: Optional[Callable[[MigrationRunReport], None]] = None,
    skip_corrupted: bool = False,
) -> MigrationReport:
    """Copy every run (or a subset) from ``source_url`` into ``dest_url``.

    Args:
        source_url: e.g. ``sqlite:///dev.db``
        dest_url: e.g. ``postgres://localhost/prod``
        only_run_ids: if given, migrate only these runs.
        on_progress: called after each run finishes (success or failure)
            with the ``MigrationRunReport`` for that run.
        skip_corrupted: if True, rows whose payload fails JSON decode
            are skipped (not migrated, not failing the run). The
            skipped event ids appear in the per-run report's
            ``skipped_events``. The resulting destination run is
            **partial** — the operator is on notice.

    Returns:
        A ``MigrationReport``. The overall operation is considered
        successful iff every run's status is ``"ok"`` or ``"skipped"``.
    """
    src = _resolve(source_url)
    dst = _resolve(dest_url)

    run_records = src.list_runs()
    if only_run_ids is not None:
        wanted = set(only_run_ids)
        run_records = [r for r in run_records if r.run_id in wanted]

    reports: list[MigrationRunReport] = []
    for rec in run_records:
        report = _migrate_one_run(src, dst, rec, skip_corrupted=skip_corrupted)
        reports.append(report)
        if on_progress is not None:
            on_progress(report)

    return MigrationReport(
        source_url=source_url, dest_url=dest_url, runs=tuple(reports)
    )


# ---- internal helpers ----------------------------------------------------


@dataclass
class _StoreFacade:
    """Uniform surface over SQLite and Postgres for migration.

    Hides the driver-specific call shapes (positional path vs URL) and
    the per-run transaction semantics.
    """

    url: str
    kind: str  # "sqlite" | "postgres"
    target: Any  # path string (sqlite) or URL string (postgres)

    def list_runs(self) -> list[RunRecord]:
        if self.kind == "sqlite":
            from activegraph.store.sqlite import SQLiteEventStore

            return SQLiteEventStore.list_runs(self.target)
        from activegraph.store.postgres import PostgresEventStore

        runs: list[RunRecord] = PostgresEventStore.list_runs(self.target)
        return runs

    def open_run(self, run_id: str) -> EventStore:
        if self.kind == "sqlite":
            from activegraph.store.sqlite import SQLiteEventStore

            return SQLiteEventStore(self.target, run_id=run_id)
        from activegraph.store.postgres import PostgresEventStore

        store: EventStore = PostgresEventStore(self.target, run_id=run_id)
        return store

    def write_run_transactionally(
        self, rec: RunRecord, events: list[Event]
    ) -> int:
        """Insert run row + events in one transaction.

        Returns the number of events newly written (idempotent: rerunning
        after a partial failure returns only the count of rows that were
        actually inserted this time).
        """
        if self.kind == "sqlite":
            return _write_run_sqlite(self.target, rec, events)
        return _write_run_postgres(self.target, rec, events)


def _resolve(url: str) -> _StoreFacade:
    parsed = parse_store_url(url)
    if parsed.scheme == "sqlite":
        return _StoreFacade(url=url, kind="sqlite", target=parsed.sqlite_path or "")
    return _StoreFacade(url=url, kind="postgres", target=parsed.raw)


def _migrate_one_run(
    src: _StoreFacade, dst: _StoreFacade, rec: RunRecord,
    *, skip_corrupted: bool = False,
) -> MigrationRunReport:
    src_store = src.open_run(rec.run_id)
    skipped_ids: list[str] = []
    try:
        if skip_corrupted:
            events = []
            for ev, err, raw_id in _iter_events_skip_corrupted(src, rec.run_id):
                if err is None and ev is not None:
                    events.append(ev)
                else:
                    skipped_ids.append(raw_id or "<unknown>")
        else:
            events = list(src_store.iter_events())
    except Exception as e:
        return MigrationRunReport(
            run_id=rec.run_id,
            status="failed",
            events_migrated=0,
            error=f"read failure: {e}",
            skipped_events=tuple(skipped_ids),
        )
    finally:
        src_store.close()

    try:
        n = dst.write_run_transactionally(rec, events)
    except Exception as e:
        return MigrationRunReport(
            run_id=rec.run_id,
            status="failed",
            events_migrated=0,
            error=f"write failure: {e}",
            skipped_events=tuple(skipped_ids),
        )

    return MigrationRunReport(
        run_id=rec.run_id,
        status="ok",
        events_migrated=n,
        skipped_events=tuple(skipped_ids),
    )


# ---- skip-corrupted iteration (v1.0 CLI follow-on) ----------------------
#
# Python generators die after raising, so iter_events() can't be wrapped
# in a per-row try/except to skip a corrupt event mid-stream. The
# skip-corrupted path iterates raw rows (driver-specific) and decodes
# each one individually, yielding (event, None, id) for clean rows and
# (None, error, id) for corrupted ones. Callers collect the skipped ids
# into the per-run report.


def _iter_events_skip_corrupted(
    facade: _StoreFacade, run_id: str
) -> Iterator[tuple[Optional[Event], Optional[CorruptedEventPayloadError], str]]:
    if facade.kind == "sqlite":
        yield from _iter_sqlite_skip_corrupted(facade.target, run_id)
        return
    yield from _iter_postgres_skip_corrupted(facade.target, run_id)


def _iter_sqlite_skip_corrupted(
    path: str, run_id: str
) -> Iterator[tuple[Optional[Event], Optional[CorruptedEventPayloadError], str]]:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
        ):
            row_id = row["id"]
            try:
                yield decode_event({
                    "id": row["id"],
                    "type": row["type"],
                    "payload": row["payload"],
                    "actor": row["actor"],
                    "frame_id": row["frame_id"],
                    "caused_by": row["caused_by"],
                    "timestamp": row["timestamp"],
                }), None, row_id
            except CorruptedEventPayloadError as e:
                yield None, e, row_id
    finally:
        conn.close()


def _iter_postgres_skip_corrupted(
    url: str, run_id: str
) -> Iterator[tuple[Optional[Event], Optional[CorruptedEventPayloadError], str]]:
    from activegraph.store.postgres import _ConnectionSource

    source = _ConnectionSource(url)
    try:
        with source.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, type, actor, payload, frame_id, caused_by, timestamp
                    FROM events
                    WHERE run_id = %s
                    ORDER BY seq
                    """,
                    (run_id,),
                )
                for row in cur.fetchall():
                    row_id = row[0]
                    try:
                        # Postgres returns JSONB as Python dict already;
                        # decode_event expects a JSON string in payload.
                        # Re-encode if needed so the decode path is uniform.
                        payload = row[3]
                        if isinstance(payload, (dict, list)):
                            import json
                            payload_s = json.dumps(payload)
                        else:
                            payload_s = payload
                        yield decode_event({
                            "id": row[0],
                            "type": row[1],
                            "actor": row[2],
                            "payload": payload_s,
                            "frame_id": row[4],
                            "caused_by": row[5],
                            "timestamp": row[6],
                        }), None, row_id
                    except CorruptedEventPayloadError as e:
                        yield None, e, row_id
    finally:
        source.close()


# ---- driver-specific transactional writers ------------------------------


def _write_run_sqlite(path: str, rec: RunRecord, events: list[Event]) -> int:
    """Single-transaction SQLite write with INSERT OR IGNORE for idempotency."""
    import sqlite3

    from activegraph.store.serde import encode_event
    from activegraph.store.sqlite import _ensure_schema

    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    n = 0
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT INTO runs (run_id, parent_run_id, forked_at_event_id, label, created_at, goal, frame_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO NOTHING
            """,
            (
                rec.run_id,
                rec.parent_run_id,
                rec.forked_at_event_id,
                rec.label,
                rec.created_at,
                rec.goal,
                rec.frame_id,
            ),
        )
        for ev in events:
            row = encode_event(ev)
            cur = conn.execute(
                """
                INSERT INTO events (id, type, actor, payload, frame_id, caused_by, timestamp, run_id)
                VALUES (:id, :type, :actor, :payload, :frame_id, :caused_by, :timestamp, :run_id)
                ON CONFLICT(id, run_id) DO NOTHING
                """,
                {**row, "run_id": rec.run_id},
            )
            if cur.rowcount > 0:
                n += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return n


def _write_run_postgres(url: str, rec: RunRecord, events: list[Event]) -> int:
    """Single-transaction Postgres write with ON CONFLICT DO NOTHING."""
    import json

    from activegraph.store.postgres import (
        _EVENT_COLUMNS,
        _ConnectionSource,
        _ensure_schema,
    )

    source = _ConnectionSource(url)
    try:
        _ensure_schema(source)
        with source.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO runs (run_id, parent_run_id, forked_at_event_id, label, created_at, goal, frame_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    (
                        rec.run_id,
                        rec.parent_run_id,
                        rec.forked_at_event_id,
                        rec.label,
                        rec.created_at,
                        rec.goal,
                        rec.frame_id,
                    ),
                )
                n = 0
                for ev in events:
                    cur.execute(
                        f"""
                        INSERT INTO events ({_EVENT_COLUMNS}, run_id)
                        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                        ON CONFLICT (id, run_id) DO NOTHING
                        """,
                        (
                            ev.id,
                            ev.type,
                            ev.actor,
                            json.dumps(ev.payload),
                            ev.frame_id,
                            ev.caused_by,
                            ev.timestamp,
                            rec.run_id,
                        ),
                    )
                    if cur.rowcount > 0:
                        n += 1
                return n
    finally:
        source.close()
