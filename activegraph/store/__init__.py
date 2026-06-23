"""Pluggable event-log stores. CONTRACT v0.5 #2, v0.8 #1–#2.

Implementations:
- InMemoryEventStore (v0.5): volatile, used by tests and ephemeral runs.
- SQLiteEventStore   (v0.5): durable single-file. Default for solo work.
- PostgresEventStore (v0.8): shared-state, multi-process. Opt-in dep.

The EventStore protocol lives in ``store.base``. Custom backends conform
to that protocol; nothing in the runtime imports concrete stores directly.
The ``open_store(url, run_id)`` entry point picks the right driver from a
connection URL (sqlite:///... or postgres://...).
"""

from activegraph.core.graph_store import GraphStore, InMemoryGraphStore
from activegraph.store.base import EventStore, RunRecord, replay_into
from activegraph.store.errors import (
    CorruptedEventPayloadError,
    DuplicateEventError,
    EventNotFoundError,
    SchemaVersionMismatch,
)
from activegraph.store.falkordb import FalkorDBGraphStore
from activegraph.store.memory import InMemoryEventStore
from activegraph.store.serde import NonSerializableEventError
from activegraph.store.sqlite import SQLiteEventStore
from activegraph.store.url import InvalidStoreURL, StoreURL, open_store, parse_store_url

__all__ = [
    "CorruptedEventPayloadError",
    "DuplicateEventError",
    "EventNotFoundError",
    "EventStore",
    "FalkorDBGraphStore",
    "GraphStore",
    "InMemoryEventStore",
    "InMemoryGraphStore",
    "InvalidStoreURL",
    "NonSerializableEventError",
    "RunRecord",
    "SQLiteEventStore",
    "SchemaVersionMismatch",
    "StoreURL",
    "open_store",
    "parse_store_url",
    "replay_into",
]
