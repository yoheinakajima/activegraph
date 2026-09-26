# NonEmptyGraphStoreError

`Runtime.load(..., graph_store=store)` or
`Runtime.fork(..., graph_store=store)` was pointed at a GraphStore
that already holds projection state. The call raises before applying
any event and does not call `clear()`. A refused fork also writes no
fork row and copies no events. `operation` is `"load"` or `"fork"`.

Replay only upserts and removes entities the log names. Records
already in the store would survive, so the rebuilt view could contain
objects, relations, or patches that never happened. Clearing first is
not safe either: if replay then fails, readers are left with an empty
or partial projection.

## Quick fix

Pass a store that `is_empty()` says is empty. A new in-memory store,
or a FalkorDB graph name that has never been replayed into, qualifies:

```python
from activegraph import InMemoryGraphStore, Runtime

store = InMemoryGraphStore()
rt = Runtime.load(path, run_id=run_id, graph_store=store)
```

Do not reuse a store that already holds this run or another run.
Indexes created when a FalkorDB store opens are not projection state;
leftover objects, relations, patches, and placeholder nodes are.

## How to diagnose

The message names the store class and how many objects, relations, and
patches it already held. Context carries the same numbers:

```python
from activegraph import NonEmptyGraphStoreError, Runtime

try:
    rt = Runtime.load(path, run_id=run_id, graph_store=store)
except NonEmptyGraphStoreError as exc:
    print(exc.store_type)
    print(exc.objects, exc.relations, exc.patches)
    print(exc.run_id)
```

If all three counts are zero, the backend still reported hidden
projection state (for example FalkorDB placeholder nodes that
`all_objects()` does not list). That state would survive replay, so
load refuses.

After the error, the GraphStore is unchanged and the event log has
not gained events.

## When does this fire

Any `Runtime.load` or `Runtime.fork` whose `graph_store` is not empty,
including:

- an in-memory store seeded by an earlier `Graph(graph_store=store)`
- a FalkorDB graph that already holds another projection
- the same store used for a second load after the first one filled it
- a compacted run (snapshot plus suffix) replayed into a dirty store

The check is the same for every built-in GraphStore. Third-party
stores answer through `GraphStore.is_empty()`. Objects are probed
with `query_objects(ObjectQuery(result_mode="exists"))`; relations
and patches use `all_relations` and `all_patches`. A backend with
state those reads cannot see must override `is_empty` and return
`False` while that state would survive replay.

`graph_store=None` is a fresh in-memory store and does not raise.

## Why the framework refuses to continue

The GraphStore is a disposable projection of one event log. A
successful rebuild must not expose facts the log does not contain.
Publishing that rebuild by clearing the live store and replaying in
place is failure-prone, so this release refuses the dirty target
instead. Building into an isolated store and swapping it in
atomically is a later change.

See [`replay`](../../concepts/replay.md) and
[`using FalkorDB`](../../guides/using-falkordb.md).
