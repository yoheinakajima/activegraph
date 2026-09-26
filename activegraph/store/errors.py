"""Storage-layer error leaves. v1.0 PR-C — StorageError category.

The classes here are the v1.0-format leaves under
:class:`activegraph.errors.StorageError`. The two pre-v1.0 storage
errors (:class:`NonSerializableEventError` in ``serde.py`` and
:class:`InvalidStoreURL` in ``url.py``) stay in their topic modules
and are re-parented in this PR; everything new lives here.

Multi-inheritance with Python builtins (KeyError, ValueError) is used
where existing user code conventionally catches the builtin —
``except KeyError`` around a store lookup, ``except ValueError`` around
event insertion. Preserves the catch site.

DB-driver errors (sqlite3.OperationalError, psycopg.OperationalError)
are NOT wrapped in this PR. The failure modes are driver-specific and
the recovery prose varies enough per mode (WAL contention, auth, host
unreachable, db missing, conn dropped) that a dedicated DB-error PR
will cover them with the right per-mode "Why:" and "How to fix:"
prose. Flagged in CONTRACT v1.0 PR-C section, not silently dropped.
"""

from __future__ import annotations

from activegraph.errors import StorageError


class SchemaVersionMismatch(StorageError):
    """The store's recorded ``schema_version`` doesn't match what this
    activegraph build expects.

    Fires on store open. The store file is intact; it was just written
    by a different (older or newer) activegraph build. Recovery is
    one of three things: upgrade activegraph, downgrade the store via
    migration, or migrate the run to a fresh store with the current
    build.
    """

    _doc_slug = "schema-version-mismatch"


class EventNotFoundError(StorageError, KeyError):
    """An event id wasn't found in the run's event log.

    Multi-inherits :class:`KeyError` so user code that does
    ``except KeyError`` around store lookups keeps working. Fires from
    every ``store.get_event(event_id)`` and from the fork primitive
    when ``--at-event`` names a missing id.
    """

    _doc_slug = "event-not-found-error"


class DuplicateEventError(StorageError, ValueError):
    """Two events with the same id were appended to the same run.

    Multi-inherits :class:`ValueError` for back-compat with user code
    catching ValueError around appends. Fires only on programmer error:
    the runtime's id generator is monotonic so duplicates shouldn't
    arise in normal use. Common cause: hand-constructing events with
    fixed ids in a test fixture.
    """

    _doc_slug = "duplicate-event-error"


class ConcurrentWriterError(StorageError):
    """A stale writer tried to extend a run whose head already moved."""

    _doc_slug = "concurrent-writer-error"

    def __init__(
        self,
        *,
        run_id: str,
        expected_head: str | None,
        actual_head: str | None,
        expected_count: int,
        actual_count: int,
        driver: str,
    ) -> None:
        super().__init__(
            f"run {run_id!r} advanced behind this writer",
            what_failed=(
                f"The {driver} EventStore handle expected run {run_id!r} to "
                f"have head {expected_head!r} ({expected_count} event(s)), "
                f"but the atomic append check found head {actual_head!r} "
                f"({actual_count} event(s)). No event was appended by this writer."
            ),
            why=(
                "Another Runtime or operator changed the same run after this "
                "writer observed its head. ActiveGraph permits many readers "
                "and concurrent writers to different runs, but exactly one "
                "logical writer may extend a given run so event order and "
                "projection remain deterministic."
            ),
            how_to_fix=(
                "Discard or make this Runtime read-only, close its store, and "
                "reload the run from the authoritative log. Coordinate the "
                "host so only one writer resumes it. Do not catch this error "
                "and merely mint another event id; that would interleave two "
                "schedulers into one run."
            ),
            context={
                "run_id": run_id,
                "expected_head": expected_head,
                "actual_head": actual_head,
                "expected_count": expected_count,
                "actual_count": actual_count,
                "driver": driver,
            },
        )


class RunNotFoundError(StorageError, FileNotFoundError):
    """``Runtime.load`` was asked to resume a run that has no canonical row.

    Multi-inherits :class:`FileNotFoundError` so existing
    ``except FileNotFoundError`` sites, including the CLI's not-found
    exit, keep working. Load does not insert a ``runs`` row, append an
    event, or replay. Create a run with ``Runtime(..., persist_to=)``
    or ``Runtime(..., store=)``; both register a catalog row when the
    attached store implements ``upsert_run``. There is no load-or-create
    mode.

    ``reason`` is one of ``"missing"`` (explicit id, no row, no events),
    ``"missing_file"`` (the SQLite file itself does not exist),
    ``"orphan_events"`` (events exist but the catalog row does not), or
    ``"empty_catalog"`` (``run_id`` omitted and an existing store has no
    runs).
    """

    _doc_slug = "run-not-found-error"

    def __init__(
        self,
        *,
        path: str,
        run_id: str | None,
        reason: str,
        event_count: int = 0,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self.reason = reason
        self.event_count = event_count
        summary, what_failed, why, how_to_fix = _run_not_found_message(
            path=path,
            run_id=run_id,
            reason=reason,
            event_count=event_count,
        )
        super().__init__(
            summary,
            what_failed=what_failed,
            why=why,
            how_to_fix=how_to_fix,
            context={
                "path": path,
                "run_id": run_id,
                "reason": reason,
                "event_count": event_count,
            },
        )


def _run_not_found_message(
    *,
    path: str,
    run_id: str | None,
    reason: str,
    event_count: int,
) -> tuple[str, str, str, str]:
    """Return ``(summary, what_failed, why, how_to_fix)``."""
    if reason == "empty_catalog":
        return (
            f"no runs found in {path}",
            (
                f"Runtime.load({path!r}) was called without run_id, and the "
                "store's runs catalog is empty. No run row was inserted and "
                "no event was accepted."
            ),
            (
                "With no run_id, load selects the most recently appended-to "
                "run. An empty catalog has nothing to select. Runtime.load "
                "does not create a default run — an empty catalog must not "
                "become a real-looking empty run."
            ),
            (
                "Create a run explicitly, then load the id it returns:\n"
                "    graph = Graph()\n"
                "    rt = Runtime(graph, persist_to='path/to/run.db')\n"
                "    loaded = Runtime.load('path/to/run.db', run_id=rt.run_id)\n"
                "\n"
                "Passing a brand-new run_id to Runtime.load also fails. "
                "There is no load-or-create parameter."
            ),
        )
    if reason == "missing_file":
        if run_id is None:
            asked = (
                f"Runtime.load({path!r}) was called without run_id, but that "
                "SQLite file does not exist."
            )
        else:
            asked = (
                f"Runtime.load was asked to open run {run_id!r} in {path}, "
                "but that SQLite file does not exist."
            )
        return (
            f"database file does not exist: {path}",
            (
                f"{asked} The file was not created and no run was registered."
            ),
            (
                "Load resumes a run from an existing database. A path that "
                "is not a file has no catalog to read. Runtime.load does "
                "not create the database."
            ),
            (
                "Create a run explicitly, then load the id it returns:\n"
                "    graph = Graph()\n"
                "    rt = Runtime(graph, persist_to='path/to/run.db')\n"
                "    loaded = Runtime.load('path/to/run.db', run_id=rt.run_id)\n"
                "\n"
                "Passing a brand-new run_id to Runtime.load also fails. "
                "There is no load-or-create parameter."
            ),
        )
    if reason == "orphan_events":
        recovery = _orphan_recovery_line(path, run_id or "")
        return (
            f"run {run_id!r} has events but no canonical run row in {path}",
            (
                f"Runtime.load was asked for run {run_id!r} in {path}. "
                f"The event log has {event_count} event(s) for that id, but "
                "the runs catalog has no row. Nothing was inserted, repaired, "
                "or replayed."
            ),
            (
                "A loadable run is a canonical runs row. Events without that "
                "row are catalog corruption, not a resume target. Repairing "
                "the row inside Runtime.load would hide the damage and "
                "recreate the silent-create bug. A run built before 1.13 "
                "with Runtime(..., store=...) that never called run_goal "
                "or save_state can look like this; construction now "
                "registers the row."
            ),
            (
                "Do not call Runtime.load to recreate the row. To opt in "
                "and register this run, call the store's public upsert "
                "once, then load again:\n"
                f"    {recovery}\n"
                "\n"
                "Discard the events instead if the row should stay absent. "
                "Load itself will not repair the catalog."
            ),
        )
    if reason == "missing":
        return (
            f"run {run_id!r} does not exist in {path}",
            (
                f"Runtime.load was asked to open run {run_id!r} in {path}. "
                "The runs catalog has no row for that id and the event log "
                "has no events for it. No run was registered and no event "
                "was accepted."
            ),
            (
                "Load resumes an existing run. Creating a run for an unknown "
                "id would register an empty run that later looks legitimate "
                "in list_runs() — usually the result of a typo."
            ),
            (
                "Create a run explicitly, then load the id it returns:\n"
                "    graph = Graph()\n"
                "    rt = Runtime(graph, persist_to='path/to/run.db')\n"
                "    loaded = Runtime.load('path/to/run.db', run_id=rt.run_id)\n"
                "\n"
                "To see the runs that already exist:\n"
                "    SQLiteEventStore.list_runs(path)\n"
                "    PostgresEventStore.list_runs(url)\n"
                "\n"
                "A typo in run_id is the usual cause. There is no "
                "load-or-create parameter."
            ),
        )
    raise ValueError(f"unknown RunNotFoundError reason: {reason!r}")


def _orphan_recovery_line(path: str, run_id: str) -> str:
    """One public call that registers an orphan run. Load never invokes it."""
    lowered = path.lower()
    if lowered.startswith("postgres://") or lowered.startswith("postgresql://"):
        opener = "PostgresEventStore"
    else:
        opener = "SQLiteEventStore"
    return (
        f"{opener}({path!r}, {run_id!r}).upsert_run("
        'created_at="<ISO-8601 timestamp>")'
    )


class CorruptedEventPayloadError(StorageError):
    """A stored event payload couldn't be decoded as JSON.

    Fires at load-time when a row's payload column contains invalid
    JSON. Distinct from :class:`NonSerializableEventError`, which fires
    at emit-time when a Python value can't be encoded to JSON.
    Corruption-on-load means the bytes on disk don't parse — a
    different failure mode requiring a different recovery.
    """

    _doc_slug = "corrupted-event-payload-error"


__all__ = [
    "SchemaVersionMismatch",
    "EventNotFoundError",
    "DuplicateEventError",
    "ConcurrentWriterError",
    "RunNotFoundError",
    "CorruptedEventPayloadError",
]
