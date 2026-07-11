# ReservedFieldError

`graph.add_object`, `graph.add_relation`, `graph.patch_object`, or
`graph.propose_patch` received caller data whose top-level keys
collide with a framework-reserved field. Today the reserved set is
exactly `{"provenance"}`: the framework writes a `provenance` dict on
every object, relation, and patch (CONTRACT #5), and caller data can
neither set nor shadow it.

This is an **exception**, not an event, because the caller has made a
mistake the caller can fix at the call site — see
[`failure-model`](../../concepts/failure-model.md) for the
events-not-exceptions principle. Inside a behavior the framework
failure mode applies as usual: the raise is caught by the runtime and
lands as a `behavior.failed` event (CONTRACT #13).

## Why it raises instead of stripping

Before v1.10 the colliding key was **silently stripped**: a caller who
wrote

```python
graph.add_object("claim", {"text": t, "provenance": {"source": doc_id}})
```

believed they had attached provenance and had attached nothing — the
key vanished with no signal, and the audit trail they thought they
were building did not exist. There is no case where passing the
reserved key is correct, so tolerating it (with or without a warning)
only preserves the silent data loss. The framework refuses loudly at
the exact call site instead.

## Quick fix

Pick the channel that matches your intent:

```python
# To influence what provenance records, use the sanctioned kwargs:
graph.add_object(
    "claim",
    {"text": t},
    actor="extractor",          # -> provenance.created_by
    caused_by=event.id,         # -> provenance.caused_by_event
    evidence=[doc_event_id],    # -> provenance.evidence
)

# Behaviors don't even need the kwargs — their BehaviorGraph stamps
# actor / caused_by / frame_id automatically (CONTRACT #7).

# To read provenance, use the handle:
obj.provenance["created_by"]
```

If the colliding key is genuinely your domain data — a document's own
chain of custody, say — rename it so it cannot shadow the framework
field:

```python
graph.add_object("doc", {"text": t, "source_provenance": upstream_info})
```

Nested keys are fine as-is: only **top-level** keys in `data` /
`updates` / `value` are checked, so
`{"meta": {"provenance": ...}}` is legitimate caller data.

## How to diagnose

The error names the colliding field, the refusing API, and the
parameter:

```
ReservedFieldError: reserved field 'provenance' in graph.add_object() data
```

From code:

```python
try:
    graph.add_object("claim", data)
except ReservedFieldError as e:
    e.field   # "provenance"
    e.api     # "add_object"
    e.param   # "data"
```

The reserved set itself is
`activegraph.core.graph.RESERVED_DATA_FIELDS` — one table checked by
all four mutation surfaces, so a future reserved field gets the same
loud treatment.

## Related

- [Concepts: Graph](../../concepts/graph.md) — the mutation surface.
- CONTRACT #5 — provenance is framework-written; v1.10 #2 replaced the
  silent strip with this error.
- `ReservedFieldError` subclasses `ExecutionError` and `ValueError`,
  so existing `except ValueError:` call sites keep working.
