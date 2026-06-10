# Events

An event is an immutable record of something that happened in a
run. Events are append-only — once an event lands in the store,
nothing modifies it. The graph state is a projection of the event
log (see [`graph`](graph.md)); behaviors fire by subscribing to
events and producing more events.

The event log is the source of truth. Everything else — the graph,
the trace, the audit history — is derived from it.

## The structure

Every event has:

- `id` — framework-generated, monotonic per run, unique per run.
- `type` — a string discriminator. Framework events use a dotted
  namespace (`object.created`, `behavior.completed`,
  `runtime.idle`); user code emits custom types via `graph.emit`
  (any string is valid, but the dot-namespaced convention is
  recommended).
- `payload` — a dict of JSON-encodable values. The framework
  enforces JSON encodability at emit time; see
  [`non-serializable-event-error`](../reference/errors/non-serializable-event-error.md).
- `actor` — who or what produced the event. `"user"` for goals
  pushed in from outside, `"runtime"` for framework-emitted
  events, a behavior name for behavior-emitted events.
- `caused_by` — the id of the event that triggered the behavior
  that produced this one. The causal chain is reconstructable by
  walking `caused_by` back to a root event (`goal.created`,
  typically).
- `timestamp` — ISO 8601, set at emit time. Used for the trace
  display; behavior bodies must not depend on it for determinism
  (see [`behaviors`](behaviors.md) — the determinism contract).

## The framework event types

Events emitted by the runtime itself fall into a small set of
families:

- **Lifecycle**: `goal.created`, `runtime.idle`,
  `runtime.budget_exhausted` — boundary events around a run.
- **Object mutations**: `object.created`, `object.removed` — object
  birth and removal.
- **Relation mutations**: `relation.created`, `relation.removed`.
- **Behavior dispatch**: `behavior.started`, `behavior.completed`,
  `behavior.failed`, `behavior.scheduled` — what the runtime did
  while running behaviors.
- **Pattern matching**: `pattern.matched` — emitted before
  `behavior.started` when the behavior used a pattern subscription;
  carries the match count.
- **LLM / tool**: `llm.requested`, `llm.responded`, `tool.requested`,
  `tool.responded` — every LLM call and every tool call appears as
  a request/response pair. A successful `llm.responded` carries the
  model output; a failed transient attempt carries an `error` payload
  and can be followed by another `llm.requested` retry attempt.
- **Patches**: `patch.proposed`, `patch.applied`, `patch.rejected`
  — the patch lifecycle. Direct `graph.patch_object(...)` shortcuts
  also emit `patch.applied`.
- **Approvals**: `approval.proposed`, `approval.granted` — the
  policy-gated approval lifecycle.
- **Pack lifecycle**: `pack.loaded`, `pack.settings_overridden` —
  `pack.loaded` is emitted once per `runtime.load_pack` call and
  carries the pack name, version, settings, and prompt content
  hashes. `pack.settings_overridden` records fork-local CLI
  overrides from `activegraph fork --set`; the parent prefix stays
  append-only, and pack loading applies the override before
  post-fork execution resumes.

Custom event types from user code live alongside these and follow
the same shape. Behaviors subscribe to either set with the same
`on=` argument.

## Append-only and what that means

Once an event is in the store, it doesn't change. No edit, no
delete, no truncate (except via the explicit `truncate_after`
primitive, which is operator-side, not behavior-side). This is
the property that makes replay work:
[`Runtime.load`](../guides/operating-in-production.md) reads the
event log and produces the same graph state every time.

Three consequences:

- **There's no "current value" of an object outside its event
  history.** An object's data is the result of applying every
  `object.created` and `patch.applied` event for that object id,
  in order. The in-memory `Object.data` dict is a cache of that
  computation, not an authoritative store.
- **Operations that look like mutations are emissions.** `add_object`
  emits `object.created`; `patch_object` emits `patch.applied`;
  `remove_object` emits `object.removed`. The graph in memory
  updates as a side effect of the emit.
- **The audit trail is automatic.** Anything that happened in a
  run is in the event log. Nothing else is needed for audit —
  there's no separate audit-log subsystem because the event log
  is the audit log.

## Events vs exceptions

The framework distinguishes two failure modes: exceptions for
caller-actionable problems the caller can catch at the call site,
events for non-fatal stops the audit trail should record and the
runtime should continue past. Behavior failures, tool failures,
budget exhaustion, and approval denials are events. Construction
errors, lookup misses, replay divergence, and pattern syntax
errors are exceptions.

See [`failure-model`](failure-model.md) for the full principle and
why the framework treats them differently. The principle was
load-bearing across the v1.0 audit and is referenced from most
other concept pages — `failure-model.md` is the canonical
statement.

## Reading the event log

The event log is available three ways:

```python
# In-memory, current run:
for event in graph.events:
    ...

# From the store, by run id:
from activegraph.store import open_store
store = open_store(url, run_id)
for event in store.iter_events():
    ...

# CLI, operator-side:
# activegraph inspect <url> --run-id <run> --tail 50
# activegraph inspect <url> --event <event_id>
```

The trace printer (`Runtime.print_trace()`) is the human-readable
projection of the event log — same data, formatted with tags and
short summaries for visual scanning. The trace is informational;
the events are the data.

## What's related

- [`graph`](graph.md) — the projection of the event log. Owns the
  "graph as projection" principle.
- [`behaviors`](behaviors.md) — the reactive code that subscribes
  to events.
- [`failure-model`](failure-model.md) — the events-vs-exceptions
  distinction.
- [`replay`](replay.md) — the operation that uses the append-only
  property to reconstruct state.
