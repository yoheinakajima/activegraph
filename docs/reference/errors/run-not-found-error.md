# RunNotFoundError

`Runtime.load` was asked to resume a run that has no canonical row in
the store's `runs` catalog. Load does not create that run, append an
event, or repair a missing row.

The error subclasses `FileNotFoundError`, so existing
`except FileNotFoundError` handlers — including the CLI's not-found
exit — still catch it. New code should catch `RunNotFoundError` and
read `reason`.

## Quick fix

Create a run explicitly, then load the id it returns:

```python
from activegraph import Graph, Runtime

graph = Graph()
rt = Runtime(graph, persist_to="path/to/run.db")
loaded = Runtime.load("path/to/run.db", run_id=rt.run_id)
```

There is no load-or-create parameter. A typo in `run_id` is the usual
cause. List the runs that do exist:

```python
from activegraph import PostgresEventStore, SQLiteEventStore

SQLiteEventStore.list_runs("path/to/run.db")
PostgresEventStore.list_runs("postgres://user:pass@host/dbname")
```

`activegraph inspect` shows one run's log. It does not list runs.

## How to diagnose

`reason` says which empty lookup this was:

| `reason` | Meaning |
|---|---|
| `missing` | Explicit `run_id`, the file exists, no catalog row, no events. |
| `missing_file` | The SQLite file itself does not exist. It was not created. |
| `orphan_events` | Events exist for that id, but the catalog row does not. Load will not insert the row. |
| `empty_catalog` | `run_id` was omitted and an existing store has no runs. |

```python
from activegraph import RunNotFoundError, Runtime

try:
    rt = Runtime.load(path, run_id=run_id)
except RunNotFoundError as exc:
    print(exc.reason)        # "missing" | "missing_file" | "orphan_events" | "empty_catalog"
    print(exc.run_id)        # None when the catalog itself is empty
    print(exc.event_count)   # orphan events left untouched
    print(exc.path)
```

`empty_catalog` is what happens when you load an existing store that
has never had a run. Omitting `run_id` still selects the most recently
appended-to run when one exists.

`missing_file` is a SQLite path that is not on disk. The message says
the file does not exist. Load does not create it.

`orphan_events` is events without a catalog row. Load will not insert
the row. To opt in, register it yourself, then load again:

```python
SQLiteEventStore(path, run_id).upsert_run(created_at="<ISO-8601 timestamp>")
```

Postgres is `PostgresEventStore(url, run_id).upsert_run(created_at="<ISO-8601 timestamp>")`.
Discard the events instead if the row should stay absent. A run built
before 1.13 with `Runtime(..., store=)` and no `run_goal` or
`save_state` can look like this; construction now registers the row.

## When does this fire

- `Runtime.load(path, run_id="typo")` when that id was never created
- `Runtime.load(path)` when the SQLite file does not exist
- `Runtime.load(path)` when an existing store contains no runs
- `Runtime.load` of an id whose events remain after the catalog row
  was deleted
- CLI commands that call `Runtime.load` with an unknown `--run-id`
  (`inspect`, `replay`, `diff`, `export-trace`). They exit 3.

An explicitly created empty run — `Runtime(graph, persist_to=path)` or
`Runtime(graph, store=SQLiteEventStore(path, graph.run_id))` before any
events — still loads. That constructor registers the catalog row.

## Why the framework refuses to continue

The event log is the source of truth, and the `runs` row is the
canonical identity of a run. Creating a run because a load missed
registers an empty id that later shows up in `list_runs()` and looks
legitimate. A failed lookup must not mint identity.

See [`replay`](../../concepts/replay.md) and
[`failure-model`](../../concepts/failure-model.md).
