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

```bash
activegraph inspect sqlite:///path/to/run.db
```

## How to diagnose

`reason` says which empty lookup this was:

| `reason` | Meaning |
|---|---|
| `missing` | Explicit `run_id`, no catalog row, no events. |
| `orphan_events` | Events exist for that id, but the catalog row does not. Load will not insert the row. |
| `empty_catalog` | `run_id` was omitted and the store has no runs. |

```python
from activegraph import RunNotFoundError, Runtime

try:
    rt = Runtime.load(path, run_id=run_id)
except RunNotFoundError as exc:
    print(exc.reason)        # "missing" | "orphan_events" | "empty_catalog"
    print(exc.run_id)        # None when the catalog itself is empty
    print(exc.event_count)   # orphan events left untouched
    print(exc.path)
```

`empty_catalog` is what happens when you load a store that has never
had a run, or a file whose catalog is empty. Omitting `run_id` still
selects the most recently appended-to run when one exists. A missing
SQLite file is not created.

`orphan_events` is catalog corruption. Restore the `runs` row with an
explicit migration you control, or discard the orphan events. Do not
call `Runtime.load` to recreate the row.

## When does this fire

- `Runtime.load(path, run_id="typo")` when that id was never created
- `Runtime.load(path)` when the store contains no runs
- `Runtime.load` of an id whose events remain after the catalog row
  was deleted
- CLI commands that call `Runtime.load` with an unknown `--run-id`
  (`inspect`, `replay`, `diff`, `export-trace`). They exit 3.

An explicitly created empty run — `Runtime(graph, persist_to=path)`
before any events — still loads. That constructor is what registers
the catalog row.

## Why the framework refuses to continue

The event log is the source of truth, and the `runs` row is the
canonical identity of a run. Creating a run because a load missed
registers an empty id that later shows up in `list_runs()` and looks
legitimate. A failed lookup must not mint identity.

See [`replay`](../../concepts/replay.md) and
[`failure-model`](../../concepts/failure-model.md).
