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
    "CorruptedEventPayloadError",
]
