"""In-memory EventStore. CONTRACT v0.5 #2.

Volatile, dict-backed. Useful for tests, for the default in-memory Runtime,
and as the reference implementation of the EventStore protocol.
"""

from __future__ import annotations

from typing import Iterator, Optional

from activegraph.core.event import Event
from activegraph.store.errors import DuplicateEventError, EventNotFoundError


def _event_not_found(event_id: str, *, run_id: str, where: str) -> EventNotFoundError:
    return EventNotFoundError(
        f"event {event_id!r} not found in run {run_id!r}",
        what_failed=(
            f"The in-memory store was asked for event {event_id!r} (in {where}) "
            f"but no event with that id exists in run {run_id!r}."
        ),
        why=(
            "Event ids are the addressing primitive for the entire framework — "
            "the replay cache, the causal-chain walk, and the fork primitive "
            "all reference events by id. A lookup against an unknown id is a "
            "bug in the caller; returning a default would silently corrupt the "
            "audit trail."
        ),
        how_to_fix=(
            "Check the event id against the events that actually exist in this "
            "run. Common causes:\n"
            "  - typo in a hand-typed event id (evt_42 vs evt_042)\n"
            "  - referencing an id from a different run\n"
            "  - the run was truncated by an earlier fork or replay\n"
            "\n"
            "Inspect the run's event log to see the valid ids:\n"
            "    activegraph inspect <store-url> --run-id " + run_id
        ),
        context={"event_id": event_id, "run_id": run_id, "where": where},
    )


class InMemoryEventStore:
    """Volatile, list-backed event store. CONTRACT v0.5 #2.

    The reference implementation of the ``EventStore`` protocol:
    append-only, id-addressed, iteration in append order. Everything
    lives in process memory and nothing survives the interpreter —
    exactly right for tests and ephemeral runs, and exactly wrong for
    anything that must be resumed, forked, or audited later; use
    ``SQLiteEventStore`` or ``PostgresEventStore`` for those.
    """

    def __init__(self, run_id: str = "run_mem") -> None:
        self.run_id = run_id
        self._events: list[Event] = []
        self._by_id: dict[str, int] = {}

    def append(self, event: Event) -> None:
        if event.id in self._by_id:
            raise DuplicateEventError(
                f"duplicate event id: {event.id}",
                what_failed=(
                    f"An event with id {event.id!r} already exists in this "
                    f"in-memory store. Appends are id-unique."
                ),
                why=(
                    "Event ids are the addressing primitive for the entire "
                    "framework — behaviors reference events by id, the replay "
                    "cache keys on them, the causal chain walks them. A "
                    "duplicate id would silently reroute one of those "
                    "references, corrupting the audit trail. The store refuses "
                    "the append rather than risk it."
                ),
                how_to_fix=(
                    "Event ids in normal use come from the runtime's monotonic "
                    "id generator (IDGen) and cannot collide. A duplicate almost "
                    "always means a test fixture is hand-constructing events with "
                    "fixed ids and a previous test left state behind. Use IDGen "
                    "to generate ids, or call `clear_registry()` / construct a "
                    "fresh Graph between tests."
                ),
                context={"event_id": event.id, "run_id": self.run_id},
            )
        self._by_id[event.id] = len(self._events)
        self._events.append(event)

    def iter_events(
        self,
        after: Optional[str] = None,
        until: Optional[str] = None,
    ) -> Iterator[Event]:
        start = 0
        end = len(self._events)
        if after is not None:
            if after not in self._by_id:
                raise _event_not_found(after, run_id=self.run_id, where="iter_events(after=)")
            start = self._by_id[after] + 1
        if until is not None:
            if until not in self._by_id:
                raise _event_not_found(until, run_id=self.run_id, where="iter_events(until=)")
            end = self._by_id[until] + 1
        for i in range(start, end):
            yield self._events[i]

    def get_event(self, event_id: str) -> Optional[Event]:
        idx = self._by_id.get(event_id)
        if idx is None:
            return None
        return self._events[idx]

    def count(self) -> int:
        return len(self._events)

    def truncate_after(self, event_id: str) -> None:
        if event_id not in self._by_id:
            raise _event_not_found(event_id, run_id=self.run_id, where="truncate_after(event_id=)")
        cut = self._by_id[event_id] + 1
        dropped = self._events[cut:]
        self._events = self._events[:cut]
        for ev in dropped:
            del self._by_id[ev.id]

    def close(self) -> None:
        pass

    def __len__(self) -> int:
        return len(self._events)
