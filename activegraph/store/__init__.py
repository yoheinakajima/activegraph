"""Pluggable event-log stores. CONTRACT v0.5 #2, v0.8 #1–#2, v1.1 STORE-FALKORDB.

Implementations:
- InMemoryEventStore  (v0.5): volatile, used by tests and ephemeral runs.
- SQLiteEventStore    (v0.5): durable single-file. Default for solo work.
- PostgresEventStore  (v0.8): shared-state, multi-process. Opt-in dep.
- FalkorDBEventStore  (v1.1): graph-database backend; opt-in dep.

The EventStore protocol lives in ``store.base``. Custom backends conform
to that protocol; nothing in the runtime imports concrete stores directly.
The ``open_store(url, run_id)`` entry point picks the right driver from a
connection URL (sqlite:///..., postgres://..., or falkor://...).
"""

from activegraph.store.base import EventStore, RunRecord, replay_into
from activegraph.store.errors import (
    CorruptedEventPayloadError,
    DuplicateEventError,
    EventNotFoundError,
    SchemaVersionMismatch,
)
from activegraph.store.memory import InMemoryEventStore
from activegraph.store.serde import NonSerializableEventError
from activegraph.store.sqlite import SQLiteEventStore
from activegraph.store.url import InvalidStoreURL, StoreURL, open_store, parse_store_url


def __getattr__(name: str):
    # Lazy export so importing `activegraph.store` doesn't require the
    # optional `falkordb` package. Mirrors the lazy-import pattern that
    # open_store uses for Postgres.
    if name == "FalkorDBEventStore":
        from activegraph.store.falkordb import FalkorDBEventStore
        return FalkorDBEventStore
    raise AttributeError(f"module 'activegraph.store' has no attribute {name!r}")


__all__ = [
    "CorruptedEventPayloadError",
    "DuplicateEventError",
    "EventNotFoundError",
    "EventStore",
    "FalkorDBEventStore",
    "InMemoryEventStore",
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
