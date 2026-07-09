# Views

A view is a scoped read of the graph. Behaviors observe the graph
through views; patches and direct mutations are how they write
back. Patches and views are the read/write counterparts in the
framework's behavior model — patches own the write side and the
audit trail, views own the read side and the cost surface.

A view is computed per-invocation. The framework doesn't cache
views across behavior fires; each call to a behavior receives a
freshly-scoped view of the graph as it exists at event time.
That's the read-side equivalent of patches' optimistic concurrency
on the write side — both primitives let parallel behaviors
operate on consistent snapshots without locks.

## The scoping arguments

Views are declared on the behavior decorator and accessed via
`ctx.view` in the body:

```python
@behavior(
    on=["object.created"],
    where={"object.type": "claim"},
    view={"around": "event.payload.object.id", "depth": 2},
)
def claim_with_neighbors(event, graph, ctx):
    claim = ctx.view.get_object(event.payload["object"]["id"])
    for related in ctx.view.objects():
        ...
```

Two arguments control the scope:

- `around=` — an expression evaluated against the triggering
  event that names the object the view centers on. Most commonly
  the triggering object's id (`event.payload.object.id`); also
  accepts a literal id, a list of ids, or `None` for a full-graph
  view.
- `depth=` — how many relation hops to include from the
  `around=` center. `depth=0` includes only the center object;
  `depth=1` includes its direct neighbors; `depth=2` includes
  neighbors of neighbors.

The full graph is available via `ctx.view` regardless of scope —
the scope determines what the view's accessor methods return by
default, not what's reachable. A scoped view's `objects()`
returns objects in the scope; the underlying `graph` is still
accessible if a behavior needs the unscoped read.

## Read-only contract

Views never mutate. The view accessor methods (`objects()`,
`relations()`, `get_object()`) return existing graph data; there's
no write path through `ctx.view`. Mutations go through `graph`
(or `ctx.propose_object` for the policy-gated path), not through
the view.

The separation is intentional. A behavior that observes through a
narrow view and mutates through the full graph is the common
pattern; the framework refuses to fuzz the read/write surfaces
because mutations through a scoped accessor would silently miss
relevant state outside the scope.

## How views compose with patterns

Pattern subscriptions and view scoping serve different jobs:

- The **pattern** selects which events fire the behavior. The
  pattern matcher reads the full graph (it has to, to evaluate
  the structural conditions), and produces `ctx.matches` — one
  entry per binding combination that satisfies the pattern.
- The **view** scopes what the behavior body reads during
  execution. Once the behavior is firing, the view determines
  what `ctx.view.objects()` returns.

The two can be different scopes. A pattern can match on a
two-hop structural condition while the view is one-hop — the
match identifies the event, the view bounds the work.

Pattern bindings (`ctx.matches[i].bindings`) are object ids; the
behavior can look them up against `ctx.view` when they're in
scope, or against `graph` directly when the pattern matched on
objects outside the view's scope.

See [`patterns`](patterns.md) for the pattern subscription model
in detail.

## Why scope views

Scoping is the framework's main cost-efficiency lever for LLM
behaviors. An LLM behavior passes its view to the prompt
assembler as serialized objects; the bigger the view, the bigger
the prompt, the higher the cost per call.

A behavior on a single claim probably doesn't need the full
diligence pack's graph in its prompt — `view={"around":
"event.payload.object.id", "depth": 1}` keeps the prompt focused
and predictable. The cost saving compounds: 100 claim-extraction
calls × 50% smaller prompt × $X/token adds up.

Non-LLM behaviors benefit too, more subtly — narrow views are
cheaper to construct and iterate. The cost is smaller per-call
but the rule still holds: scope to what the behavior actually
needs.

## What a view is not

Three things views explicitly are not:

- **Not a query language.** The framework deliberately doesn't
  have a query language beyond pattern subscriptions. Views are
  scoping declarations, not queries. If you find yourself wanting
  to filter view results by complex conditions, you're reaching
  for the wrong primitive — use a pattern subscription instead.
- **Not a graph snapshot.** Views are computed per-invocation,
  not cached. A behavior firing twice on two events gets two
  fresh views; the framework doesn't cache or invalidate.
- **Not a subscription primitive.** Patterns subscribe; views
  scope. The behavior fires because of `on=` / `where=` /
  `pattern=`; the view only determines what the body reads after
  it fires.

The negative space matters because views are easy to
over-interpret as "the LangChain retriever" or "the query DSL."
They're neither. They're scoping declarations on the read side of
behaviors.

## What's related

- [`graph`](graph.md) — the world state views observe.
- [`behaviors`](behaviors.md) — where `view=` is declared and
  `ctx.view` is used.
- [`patterns`](patterns.md) — the subscription primitive that
  determines when behaviors fire; views determine what they read.
- [`patches`](patches.md) — the write-side counterpart. Behaviors
  read through views and write through patches (or direct
  mutation).
- [LLM providers reference](../reference/llm-providers.md) —
  where view scoping matters most, since a view bounds what an
  `@llm_behavior` sends to the provider.
