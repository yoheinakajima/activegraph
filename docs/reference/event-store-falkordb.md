# FalkorDB event store

> Added in v1.1 (STORE-FALKORDB).

`FalkorDBEventStore` is the third event-log backend, alongside SQLite
and Postgres. FalkorDB is a Redis-module property graph database with
OpenCypher; activegraph models the event log as a small graph so
operators who pick FalkorDB can ad-hoc-query the audit trail using
Cypher — the same DSL the framework already exposes for behavior
subscriptions.

## When to pick FalkorDB

| Use case                                | Pick                  |
| --------------------------------------- | --------------------- |
| Single-process, single-laptop dev       | SQLite                |
| Multi-process, multi-host, shared state | Postgres              |
| Cypher-native audit-trail queries; you already run FalkorDB; eventual graph-projection (Phase 2) | **FalkorDB**          |

If you don't have a strong reason to pick FalkorDB, pick SQLite or
Postgres — both have been load-bearing since v0.5/v0.8 respectively.

## Install

```bash
pip install 'activegraph[falkordb]'
```

This pulls the official [`falkordb`](https://pypi.org/project/falkordb/)
Python client (>=1.0), which is built on `redis-py`.

## Configure

```python
from activegraph.runtime import Runtime

rt = Runtime(graph, persist_to="falkor://localhost:6379/myapp")
```

URL shape:

```
falkor://[user:pass@]host[:port]/<graph_name>
falkordb://...                                  # alias
```

* `host` is **required** — FalkorDB is a Redis-protocol service.
* `port` defaults to `6379`.
* The path component is the FalkorDB graph name. If omitted, the store
  uses the graph named `activegraph`.

## Data model

A single graph holds:

```
(:Run {run_id, parent_run_id, forked_at_event_id, label,
       created_at, goal, frame_id, next_seq})
(:Event {id, type, actor, payload, frame_id, caused_by,
         timestamp, run_id, seq})
(:Run)-[:HAS_EVENT]->(:Event)
(:_AGMeta {key, value})        // schema_version
```

`seq` is the projection-ordering authority (same role as SQLite's
`AUTOINCREMENT` and Postgres's `BIGSERIAL`). It is allocated per-run
via a `next_seq` counter on the Run node, incremented inside a single
Cypher query — FalkorDB serializes queries per graph, so the
read-modify-write is atomic without explicit transactions.

## Connection management

`FalkorDBEventStore` accepts three target shapes, mirroring
`PostgresEventStore`:

```python
FalkorDBEventStore("falkor://host:6379/mygraph", run_id=...)   # owned client
FalkorDBEventStore(my_falkordb_client, run_id=...)             # borrowed
FalkorDBEventStore(my_falkordb_graph,  run_id=...)             # borrowed
```

Owned clients are closed on `store.close()`. Borrowed handles are left
to the caller.

## Inspecting the audit trail with Cypher

Because every event is a node, the standard Cypher works:

```cypher
// Recent events for a run
MATCH (e:Event {run_id: 'run_abc'})
RETURN e.seq, e.type, e.id, e.timestamp
ORDER BY e.seq DESC
LIMIT 50

// Count events per type across all runs
MATCH (e:Event)
RETURN e.type, count(*) AS n
ORDER BY n DESC

// Fork lineage
MATCH (child:Run)
WHERE child.parent_run_id IS NOT NULL
RETURN child.run_id, child.parent_run_id, child.forked_at_event_id
```

## Limitations and known divergences vs Postgres

- **Schema constraints.** Uniqueness of `(id, run_id)` is enforced by
  a pre-check `MATCH` inside `append`, not by a database constraint.
  This produces a structured `DuplicateEventError` but doesn't survive
  out-of-band writes that bypass `FalkorDBEventStore`. Don't write to
  the same graph from non-activegraph clients.
- **Phase 2 — graph projection** of the runtime graph itself (objects
  + typed relations) into FalkorDB is scoped in `v1.1-plan.md` but
  not yet implemented. Phase 1 (this page) is the event-log backend
  only.
- **Phase 3 — subscription Cypher** sharing the FalkorDB engine is a
  future possibility, not a v1.1 commitment.

## Testing locally

```bash
docker run -d -p 6379:6379 falkordb/falkordb:latest
export ACTIVEGRAPH_TEST_FALKORDB_URL=falkor://localhost:6379/test
pytest tests/test_falkordb_store.py
```

The conformance suite (`EventStoreConformance`) is the same one that
gates SQLite and Postgres; any new backend gets free coverage by
subclassing it.
