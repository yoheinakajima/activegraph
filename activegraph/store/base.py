"""EventStore interface + run metadata. CONTRACT v0.5 #2 and #6.

An EventStore is a per-run, append-only view onto an event log. Multiple
runs may share a backing file (SQLite); the EventStore instance is scoped
to one `run_id` and only sees that run's events.

Methods are deliberately minimal — append, iterate, count, lookup,
truncate-after. No queries, no indexes beyond what the backend ships. This
is an event log, not a database.

The accompanying `RunRecord` is the canonical row in the `runs` table:
parent linkage for forks, an optional label, and the original goal/frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Iterator, Optional, Protocol

from activegraph.core.event import Event

if TYPE_CHECKING:
    from activegraph.core.graph import Graph


@dataclass
class RunRecord:
    """The canonical row describing one run in a multi-run store.

    Fork lineage (``parent_run_id``, ``forked_at_event_id``), an
    optional human ``label``, and the originating ``goal`` /
    ``frame_id`` — enough for run listings and for ``Runtime.load``
    to pick the right run without replaying anything.
    """

    run_id: str
    parent_run_id: Optional[str]
    forked_at_event_id: Optional[str]
    label: Optional[str]
    created_at: str
    goal: Optional[str]
    frame_id: Optional[str]


class EventStore(Protocol):
    """Append-only per-run event log. CONTRACT v0.5 #2.

    The persistence seam: an instance is scoped to one ``run_id``
    even when runs share a backing file. Deliberately minimal —
    append, iterate, count, lookup, truncate-after, close; no
    queries, no indexes beyond what the backend ships. Backends
    (in-memory, SQLite, Postgres) implement this protocol and pass
    ``EventStoreConformance``. The log is the source of truth; the
    graph projection rebuilds from here and is never stored here.
    """

    run_id: str

    def append(self, event: Event) -> None: ...

    def iter_events(
        self,
        after: Optional[str] = None,
        until: Optional[str] = None,
    ) -> Iterator[Event]: ...

    def get_event(self, event_id: str) -> Optional[Event]: ...

    def count(self) -> int: ...

    def truncate_after(self, event_id: str) -> None: ...

    def close(self) -> None: ...


def replay_into(graph: Graph, events: Iterable[Event]) -> int:
    """Apply a stream of events to a Graph without firing listeners.

    The single replay entry point — used by `Runtime.load` and `Runtime.fork`.
    Returns the number of events replayed.
    """
    n = 0
    for ev in events:
        graph._replay_event(ev)  # noqa: SLF001 — internal seam by design
        n += 1
    return n
