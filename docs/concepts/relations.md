# Relations

A relation is a typed edge between two objects on the graph. Like
objects, relations have a type (string), an id (framework-generated),
optional data (dict of JSON-encodable values), and they live in the
event log — created by `add_relation`, removed by `remove_relation`,
each transition emitted as an event.

What makes relations distinctive in this framework is that the
relation type itself can carry behavior. A relation isn't just a
passive edge for the graph projection to render; it can be a rule
that fires when its endpoints change, or an agentic actor with its
own LLM-backed reasoning. The relation type is the unit of
coordination logic between its endpoints.

This is the framework's most differentiated primitive. Most graph
frameworks have nodes-with-behavior; relations-with-behavior is
where Active Graph diverges.

## The three relation kinds

Three flavors of relation type, on a spectrum of how much logic the
relation itself owns:

- **Passive.** No behavior attached. The relation is structural
  data — it exists, pattern subscriptions can match on it,
  behaviors on the endpoints can read it. The vast majority of
  relations are passive (`supports`, `contradicts`, `cites`,
  `depends_on`).
- **Rule.** A `@relation_behavior` attached to the type. Fires
  deterministically when an event affects either endpoint of any
  relation of that type. Used for coordination logic that
  semantically belongs to the relationship, not to either endpoint
  (e.g., a `depends_on` relation that auto-blocks the dependent
  when the dependency changes status).
- **Agentic.** A `@relation_behavior` that wraps an LLM call (same
  `@llm_behavior` machinery, but anchored on relation events). Used
  when the coordination logic needs LLM reasoning — e.g., a
  `contradicts` relation that drafts a contradiction-resolution memo
  when both endpoint claims change.

The three flavors share the same event types
(`relation.created`, `relation.removed`) and the same data
representation. The flavor is a property of the relation *type*,
not of any individual relation instance.

## The `@relation_behavior` decorator

```python
from activegraph import relation_behavior

@relation_behavior(
    name="auto_unblock",
    relation_type="depends_on",
    on=["task.completed"],
)
def auto_unblock(relation, event, graph, ctx):
    if event.payload["task_id"] == relation.source:
        graph.patch_object(relation.target, {"status": "open"})
```

The body receives the `relation` (the typed edge instance), the
triggering `event`, the `graph`, and the `ctx`. The relation
behavior fires once per relation that matches — if three
`depends_on` edges all point at the same source and the source's
`task.completed` event fires, the body runs three times, once per
edge, each call with that edge as `relation`.

The decorator's `relation_type=` argument narrows dispatch to one
type. Other arguments (`on=`, `where=`, `pattern=`) work the same
as on regular `@behavior`. See [`behaviors`](behaviors.md) for the
full activation model.

## When to use a relation behavior vs a regular behavior

The test: **does the coordination logic semantically belong to the
relationship, not to either endpoint?**

- A `depends_on` relation auto-unblocking the dependent when the
  dependency completes → relation behavior. The unblock logic is
  about the relationship, not about either task in isolation.
- A `claim` getting flagged when its `confidence` drops below 0.5
  → regular behavior on `patch.applied`. The flag is about the
  claim itself; no relationship is involved.
- A `contradicts` relation drafting a resolution memo when both
  endpoints change → agentic relation behavior. The reasoning needs
  both endpoints' state; it's relationship logic, not endpoint
  logic.

When the test is ambiguous (the logic could go either way), default
to regular behaviors. They're more discoverable — they show up
under the endpoint's type in `inspect --behaviors`, and the
coordination logic appears as a single behavior fire rather than
N fires (one per matching edge).

## Pattern subscriptions and relations

Pattern subscriptions match on relations naturally. The Cypher-subset
syntax `(a:type1)-[r:rel_type]->(b:type2)` binds both endpoints and
optionally the relation itself. See
[`patterns`](patterns.md) for the binding rules and when to use
the `r` variable vs the bare `-[:rel_type]->` form.

A behavior with a pattern subscription that mentions a relation type
fires when the pattern matches — which is a different activation
mechanism from `@relation_behavior` (which subscribes to events on
relation endpoints rather than to graph structure). Both are valid;
pick by which question you're asking: "fire when this edge plus
this surrounding structure exist" (pattern) vs "fire when something
happens to either end of any edge of this type" (relation behavior).

## What's related

- [`graph`](graph.md) — the world state relations sit on. Relations
  are projections of `relation.created` / `relation.removed`
  events, same as objects.
- [`behaviors`](behaviors.md) — the broader behavior model.
  `@relation_behavior` is a sibling of `@behavior` /
  `@llm_behavior`.
- [`patterns`](patterns.md) — pattern subscriptions that match on
  relation structure.
- [Writing relation behaviors](../guides/writing-behaviors.md) —
  practical how-to; the decision rules for relation vs regular
  behavior get more attention there.
