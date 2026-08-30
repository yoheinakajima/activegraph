# InvalidPatchOperation

A patch named an operation outside ActiveGraph's closed patch vocabulary.
Patches support exactly `update` (merge fields) and `replace` (replace the
target object's data).

## Quick fix

```python
patch = graph.propose_patch(
    object_id,
    "update",              # or "replace"
    {"status": "reviewed"},
    proposed_by="operator",
)
```

Use `graph.add_object(...)` and `graph.remove_object(...)` for object lifecycle
changes. `create` and `remove` are not patch operations.

Validation happens before a patch id or event id is consumed and before an
event is accepted. A malformed historical `patch.proposed` or `patch.applied`
record also fails during replay; it is never reinterpreted and never produces
a false version increment.

## How to diagnose

The error context contains `op`, the sorted `allowed` operations, and
`event_id` when validation came from a live or stored framework event. Inspect
that event and repair or migrate the source history explicitly rather than
skipping it.

## What's related

- [`InvalidPatchLifecycleState`](invalid-patch-lifecycle-state.md) — a valid
  patch operation was applied outside the `proposed` state.
- [Events](../../concepts/events.md) — why applied records must describe work
  that actually happened.
