# ConcurrentWriterError

A writable EventStore handle tried to extend a run after another writer had
already moved that run's durable head. The refused event was not appended,
projected, queued, or offered to observers.

## Quick fix

Stop using the stale runtime and reload the run from its authoritative store:

```python
from activegraph import ConcurrentWriterError, Runtime

try:
    runtime.run_until_idle()
except ConcurrentWriterError:
    runtime.close_sinks()
    if runtime.graph.store is not None:
        runtime.graph.store.close()
    runtime = Runtime.load(store_url, run_id=run_id)
```

Then fix host ownership so one logical writer resumes that run. Many readers
and writers to different runs are supported; multiple schedulers extending the
same run are not.

Do not catch this error and regenerate an event id. Event spelling is not the
conflict: two schedulers have divergent projections and queue state.

## How to diagnose

The structured context contains `run_id`, `expected_head`, `actual_head`,
`expected_count`, `actual_count`, and `driver`. Use `(run_id, event_id)` when
comparing records: logical event ids intentionally repeat across runs.

SQLite enforces the comparison inside a write transaction. Postgres takes a
run-scoped transaction lock and performs the same compare-and-advance. A
future lease may improve ownership discovery, but it cannot replace this
correctness check.

## What's related

- [`DuplicateEventError`](duplicate-event-error.md) — one logical id was
  inserted twice within a run.
- [`IncompatibleRuntimeState`](incompatible-runtime-state.md) — a graph and
  store were attached at different run heads.
- [Operating in production](../../guides/operating-in-production.md) — store
  selection and runtime hosting.
