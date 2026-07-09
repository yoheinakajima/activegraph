# Behaviors

A behavior is the framework's unit of reactive code. It subscribes
to events, runs when its subscription matches, and produces more
events (new objects, new relations, patches, custom events). The
runtime dispatches behaviors against events in the queue until the
queue is empty.

Behaviors are how a developer adds custom logic to the framework.
Most code that ships with a pack is behaviors. Most code a developer
writes is behaviors.

A behavior is **not an agent.** It doesn't decide what to do — it
reacts. The decision is the subscription rule; the work is the
body. An agentic-feeling system emerges from many small behaviors
firing in response to each other's outputs, not from one
agent-orchestrator behavior calling everything else.

## The decorator

```python
from activegraph import behavior

@behavior(
    name="contradiction_detector",
    on=["object.created"],
    where={"object.type": "claim"},
    pattern="(c:claim)-[:contradicts]->(other:claim)",
    view={"around": "event.payload.object.id", "depth": 1},
    activate_after=1,
)
def contradiction_detector(event, graph, ctx):
    for match in ctx.matches:
        ...
```

Every argument is a separate activation condition; the behavior
fires when **all** of them hold:

- `on=` — event types the behavior subscribes to. Most behaviors
  subscribe to a single type (`object.created`, `goal.created`,
  custom event names). Match-all is allowed with `on=["*"]` but
  rarely useful.
- `where=` — a dict-shaped filter on the event payload. Equality
  on values; nested keys via dotted paths.
- `pattern=` — a Cypher-subset pattern subscription. The behavior
  fires only when the pattern matches the graph at event time.
  See [`patterns`](patterns.md) for the locked subset and grammar.
- `view=` — a scoped view of the graph passed to the behavior body
  via the `ctx.view` accessor. Default is the full graph; narrow
  via `around=` + `depth=` to limit what the behavior reads.
- `activate_after=` — schedule the behavior to fire N events after
  the triggering event. Integer event count only; wall-clock units
  are refused (see
  [`invalid-activate-after`](../reference/errors/invalid-activate-after.md)).

## The signature

```python
def my_behavior(event, graph, ctx):
    ...
```

- `event` — the triggering event, with `id`, `type`, `payload`,
  `actor`, `caused_by`, `timestamp`.
- `graph` — the graph as it existed at event time, scoped by the
  `view=` argument.
- `ctx` — the runtime-bound context, with `.matches` (pattern
  bindings), `.view` (the scoped graph), `.propose_object` (the
  approval-gated add path), and a few framework-internal hooks.

The body mutates the graph by calling `graph.add_object`,
`graph.patch_object`, `graph.add_relation`, `graph.remove_object`,
or emits arbitrary events via `graph.emit(type, payload)`. Each
mutation lands as an event in the log; downstream behaviors react.

The signature is validated at decoration time (v1.3): a handler
that can't accept the three positional parameters — `def h(ctx)`,
`def h(event, ctx)` — raises `TypeError` at the `@behavior` line
instead of failing at first invocation with the error buried in a
`behavior.failed` event. Extra parameters are allowed when the call
can still succeed: give them defaults, or (in packs) annotate them
with your settings class for keyword injection
(`*, settings: MyPackSettings` — see
[Authoring packs](../guides/authoring-packs.md)). The same check
covers `@relation_behavior` (`(relation, event, graph, ctx)`),
`@llm_behavior` (`(event, graph, ctx, llm_output)`), and `@tool`
(`(args, ctx)`).

## The three behavior kinds

- **Regular `@behavior`** (function or class) — the workhorse.
  Reacts to events, mutates the graph. Synchronous, deterministic.
- **`@llm_behavior`** — wraps a function whose return value comes
  from an LLM call. The framework handles the prompt assembly, the
  provider call, the cache, the tool loop, and the schema
  validation; the body receives the parsed LLM output and turns it
  into graph mutations. See the [LLM providers reference](../reference/llm-providers.md).
- **`@relation_behavior`** — attached to a relation type rather
  than an event type. Fires when an event affects an endpoint of
  the relation. See [`relations`](relations.md).

## The determinism contract

Behavior bodies must be **deterministic given their inputs**. Same
event, same graph state, same view → same mutations. This is the
load-bearing assumption that makes replay and forking work. Two
practical consequences:

- **No `random`, no `datetime.now()`, no `uuid.uuid4()` in
  behavior bodies.** If you need randomness or wall-clock time,
  get it from the event (which carries the recorded timestamp) or
  from the runtime's deterministic id generator (`graph.ids`).
- **No I/O outside the framework's primitives.** Network calls go
  through `@tool` so the framework can cache and replay them. LLM
  calls go through `@llm_behavior` so the prompt-hash cache works.
  Direct `requests.get` in a behavior body breaks replay
  determinism in a way the framework can't recover from.

The framework doesn't enforce determinism with static analysis; the
discipline is on the developer. The cost of breaking it is a fork
that produces a different result from its parent — see
[`replay-divergence-error`](../reference/errors/replay-divergence-error.md).

## The failure model

When a behavior body raises, the runtime catches the exception and
emits a `behavior.failed` event with the original exception's type,
message, and (for LLM/tool errors) the structured `reason` code.
The exception does NOT escape to your code — the loop continues,
other behaviors keep firing, and the operator sees the failure in
the trace.

Code that wants to react to failures subscribes to
`behavior.failed`. The retry-behavior pattern is the canonical
idiom:

```python
@behavior(
    on=["behavior.failed"],
    where={"reason": ["llm.network_error", "tool.timeout"]},
)
def retry_transient(event, graph, ctx):
    ...
```

See [`failure-model`](failure-model.md) for the
events-not-exceptions principle and
[`llm-behavior-error`](../reference/errors/llm-behavior-error.md) /
[`tool-error`](../reference/errors/tool-error.md) for the
LLM/tool failure shapes specifically.

## What's related

- [`graph`](graph.md) — the world state behaviors react to and
  mutate.
- [`events`](events.md) — the append-only history behaviors
  subscribe to.
- [`relations`](relations.md) — the typed-edge primitive and
  `@relation_behavior`.
- [`patterns`](patterns.md) — the Cypher-subset pattern
  subscription primitive.
- [`failure-model`](failure-model.md) — what happens when a
  behavior body raises.
- [Behaviors API reference](../reference/api/behaviors.md) — the
  `@behavior` / `@llm_behavior` / `@relation_behavior` decorators
  and base classes.
