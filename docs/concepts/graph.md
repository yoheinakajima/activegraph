# Graph

The graph is the world state of an Active Graph run. Objects sit on
it as typed nodes; relations connect them as typed edges. Behaviors
react to changes in the graph by emitting more changes. Goals are
the inputs operators push in from the outside.

The graph isn't a control-flow structure. It models what the system
**knows about**, not what the system **does next**. That's the
load-bearing distinction between Active Graph and workflow-graph
frameworks (LangGraph, the various DAG runners) — the nodes here
are facts and entities, not steps. Steps are behaviors, and
behaviors live alongside the graph, not inside it.

## Graph as projection of the event log

The graph is the projection of an append-only event log. Every
mutation — `add_object`, `patch_object`, `add_relation`, every
behavior fire — emits an event. The event lands in the store, and
the graph in memory is updated. `Runtime.load(url, run_id=...)`
reconstructs the graph by replaying the events; nothing else is
persisted.

This is the framework's most foundational invariant. Other concepts
pages link here for it:

- [`events`](events.md) documents the event types that drive the
  projection.
- [`replay`](replay.md) is the operation that uses the projection
  property to reconstruct state.
- [`forking`](forking.md) creates a new run by copying a prefix of
  the event log; the forked graph is the projection of that
  prefix.
- [`failure-model`](failure-model.md) is why the framework refuses
  to silently produce events that don't represent real work — the
  projection would lie.

You can read the graph state at any time:

```python
graph.all_objects()                       # every object
graph.objects(type="claim")               # filtered by type
graph.objects(type="claim", where={"confidence": {">": 0.8}})
graph.relations(source=claim_id)          # outgoing edges
graph.relations(target=claim_id)          # incoming edges
graph.relations(type="depends_on")        # by edge type
graph.get_object(object_id)               # by id
```

`graph.relations(source=, target=, type=)` is the canonical filter
API on `Graph`; all three kwargs compose by AND, and calling with
no kwargs returns every relation.
`graph.get_relations(object_id=, type=, direction=)` is an alias
preserved for backward compatibility; new code should use
`graph.relations(...)`.

Object reads cross one structured `ObjectQuery` boundary. A GraphStore may
consume type and parity-proven predicate clauses, but it returns the exact
residual predicate it did not consume; `Graph` evaluates that residual with
the canonical Python evaluator. Existence checks use the same plan with a
one-result mode, so capable backends do not materialize an entire type.

But you can't mutate it except through events. There's no
`graph.objects["x"] = ...` setter; every mutation goes through a
method that emits an event.

## Objects

Objects are typed entities. The type is a string declared by the
pack that owns the object type (`@pack(object_types=[...])`) or
freeform if no pack declares it. The data is a dict of JSON-encodable
values:

```python
claim = graph.add_object("claim", {
    "text": "Q3 revenue grew 28% YoY.",
    "confidence": 0.85,
})
```

Object ids are framework-generated (`IDGen`), monotonic per run,
and unique per run. The pack format can declare a schema (Pydantic
model) for the object type; if so, the data is validated at
`add_object` — see
[`pack-schema-violation`](../reference/errors/pack-schema-violation.md).

## Relations

Relations are typed edges between objects. The type is a string,
the endpoints are object ids, and optional data is a dict on the
edge itself:

```python
graph.add_relation(claim.id, evidence.id, "supports", {"strength": 0.9})
```

Relations have ids too (also framework-generated). A relation type
can carry a behavior — see [`relations`](relations.md) for the
distinction between passive, rule, and agentic relations.

## Goals

Goals are the inputs operators push in from outside. A goal isn't
an object on the graph; it's an event of type `goal.created` that
behaviors subscribed to it react to:

```python
rt.run_goal("Diligence: Northwind Robotics")
```

Behaviors on `goal.created` fire first; their output (objects,
relations, more events) triggers other behaviors, and the runtime
loop continues until the queue is empty.

## What's NOT on the graph

- **Control flow.** The runtime's behavior dispatch is not modeled
  as graph nodes. The graph models the work product (objects,
  relations); behaviors are the framework's reactive code.
- **Configuration.** Pack settings, budget limits, the runtime's
  store URL — none of these are graph state. They're constructor
  arguments.
- **The event log itself.** The graph is a *projection* of the log;
  the log itself lives in the store. Read it via
  `graph.events` (in-memory) or `activegraph inspect` (operator-side).

## What's related

- [`events`](events.md) — the append-only history that drives the
  graph projection.
- [`behaviors`](behaviors.md) — the reactive code that mutates the
  graph in response to events.
- [`relations`](relations.md) — the typed-edge primitive and its
  optional behaviors.
- [`failure-model`](failure-model.md) — why the framework refuses
  to silently bypass the event log.
