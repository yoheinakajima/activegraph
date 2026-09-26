# Replay

Replay is reconstructing graph state from the event log. The graph
is a projection of the log (see [`graph`](graph.md)); replay is
the operation that computes the projection. Every time you load a
run from a store, fork a run, or strict-check a run, replay is
what runs underneath.

The framework guarantees that **replay is deterministic** given
the event log. Two replays of the same log produce byte-identical
graph state. That guarantee is the foundation for forking,
strict-mode validation, and the audit-trail contract.

## What replay does

Three operations trigger replay:

- **`Runtime.load(url, run_id=...)`** — loads a persisted run.
  Replay reads every event from the store and rebuilds the graph.
  Load does not create a run. An explicit `run_id` with no canonical
  `runs` row raises
  [`run-not-found-error`](../reference/errors/run-not-found-error.md)
  and leaves the catalog unchanged, including when orphan events exist
  for that id. Omitting `run_id` still selects the most recently
  appended-to run when one exists. An empty catalog raises the same
  error and inserts nothing; a missing SQLite file is left uncreated.
  Create a run with `Runtime(..., persist_to=)` or `store=`
  first. There is no load-or-create mode.
- **`graph_store=`** — where the projection is materialized. The store
  must already be empty (`GraphStore.is_empty()`). A store that holds
  objects, relations, patches, or other leftover projection state raises
  [`non-empty-graph-store-error`](../reference/errors/non-empty-graph-store-error.md)
  before any event is applied. Load does not clear the store. Pass a
  new `InMemoryGraphStore` or a FalkorDB graph name that has not been
  replayed into. Reusing a store that already holds a projection, or
  clearing it and hoping replay finishes, can publish facts the log
  does not contain or leave a partial projection if replay fails.
- **`runtime.fork(at_event=...)`** — creates a new run sharing
  the parent's events up to the fork point. Replay reconstructs
  the shared prefix in the fork; new behavior fires after the
  fork point execute fresh. See [`forking`](forking.md).
- **`runtime.replay()`** (explicit) — re-applies the in-memory
  event log. Less common; used by tests and migration scripts
  that need to verify replay determinism without going through
  the store.

The framework doesn't separate "load" from "replay" in the
public API — `Runtime.load` is the canonical entry point. Replay
is the verb the load uses.

## The cache layer

For LLM and tool calls to replay deterministically, the framework
caches their responses by content hash:

- **LLM responses** are keyed on the prompt's full content hash
  (system message + user messages + model + tool definitions +
  output schema). Replay reads `llm.responded` events from the
  log, indexes them by their corresponding `llm.requested`'s
  prompt hash, and returns the cached response when a behavior
  re-fires with the same prompt.
- **Tool responses** are keyed on the tool's name plus a
  deterministic hash of its arguments. Same mechanism — replay
  reads `tool.responded` events and serves them to re-firing
  behaviors.

The cache makes replay cheap: no LLM calls, no tool execution,
just event-log reads. The cost is the disk space for the responses
in the store, which is bounded by the run's size.

## Strict mode vs permissive mode

Replay runs in one of two modes:

- **Permissive replay** (`replay_strict=False`, the default for
  `Runtime.load`). Events are re-emitted from the log; the runtime
  trusts the recording. The cache serves responses for any
  behavior whose prompt hash matches a recorded one. Behaviors
  whose prompt hash doesn't match get fresh LLM/tool calls (with
  the caveat that those calls land as new events in the new
  run's log, not the parent's).
- **Strict replay** (`replay_strict=True`). Behaviors re-fire
  against the recorded seed and the framework compares the live
  event stream against the recorded one. Any drift fires
  [`replay-divergence-error`](../reference/errors/replay-divergence-error.md)
  pinned to the first divergent event id.

Strict mode is for verifying that the run is replayable — a
green strict replay proves the run is reproducible. Permissive
mode is for development workflows where behaviors are still
being edited and divergence is expected. The fork primitive
runs strict by default because a fork's value is its shared
lineage with its parent.

## The determinism contract

Replay determinism rests on the [`behaviors`](behaviors.md)
determinism contract: same event, same graph state, same view →
same mutations. Three rules from that contract that replay
specifically depends on:

- **No `random`, `datetime.now()`, or `uuid.uuid4()` in behavior
  bodies.** If the body needs these, get them from the event
  (which carries the recorded timestamp) or from the runtime's
  deterministic id generator.
- **No I/O outside the framework's primitives.** Direct
  `requests.get` in a behavior body breaks replay — the response
  isn't in the cache.
- **No mutable global state across behavior fires.** A counter
  in a module-level variable that increments per fire would
  diverge under replay.

The framework doesn't statically enforce these rules. A behavior
that breaks them runs fine on first execution; replay or fork
discovers the violation as
[`replay-divergence-error`](../reference/errors/replay-divergence-error.md).

## When replay is invoked

The triggers, restated for reference:

- **Store load** — every `Runtime.load(url, run_id=...)` runs
  replay during construction. The graph state is rebuilt from
  the event log before any new work happens.
- **Fork** — `runtime.fork(at_event=...)` runs replay up to the
  fork point in the new run, then resumes live execution from
  there.
- **Explicit replay** — `runtime.replay()` rebuilds graph state
  from the current in-memory event log. Uncommon outside of
  tests and migration code.

## What's related

- [`graph`](graph.md) — the projection replay computes. Owns the
  "graph as projection of event log" principle.
- [`events`](events.md) — the append-only history replay reads.
- [`behaviors`](behaviors.md) — the determinism contract that
  makes replay work.
- [`forking`](forking.md) — the operation that runs replay up to
  the fork point.
- [`failure-model`](failure-model.md) — events vs exceptions; why
  divergence is an exception rather than a silent event.
- [`replay-divergence-error`](../reference/errors/replay-divergence-error.md)
  — the strict-mode error case.
