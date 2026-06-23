# Store

Event stores, URL parsing, and migration. For the conceptual
model see [`concepts/graph`](../../concepts/graph.md) (graph as
projection of the event log) and
[`concepts/replay`](../../concepts/replay.md).

## Stores

::: activegraph.EventStore

::: activegraph.InMemoryEventStore

::: activegraph.SQLiteEventStore

## Graph stores

The materialized graph projection (objects, relations, patches) lives
behind a `GraphStore`, distinct from the durable `EventStore` above. See
the [Using the FalkorDB graph store](../../guides/using-falkordb.md) guide.

::: activegraph.GraphStore

::: activegraph.InMemoryGraphStore

::: activegraph.FalkorDBGraphStore

## URL parsing + helpers

::: activegraph.open_store

::: activegraph.parse_store_url

## Migration

::: activegraph.migrate

::: activegraph.MigrationReport

::: activegraph.MigrationRunReport

::: activegraph.RunRecord
