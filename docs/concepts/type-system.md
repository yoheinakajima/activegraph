# Type system

Active Graph has three layers of types: **event types** (framework-
defined), **object types** (developer-defined), and **relation types**
(developer-defined). One layer is the fixed vocabulary the framework
speaks; the other two are the domain vocabulary the developer
chooses. A maintainer reading this page for the first time will most
likely arrive looking for the answer to one question: *are there
framework base types I need to know about?* The answer is no — for
objects and relations. The framework ships zero base object types
and zero base relation types. The Diligence pack's
`claim / evidence / question / memo / …` ontology is an example, not
a base.

This page covers the three layers, how they compose, the
patch-lifecycle states (the fourth small framework-defined
vocabulary), and design guidance for the developer-defined layers.

## The framework-defined layer: event types

Every event has a `type` — a string discriminator that says what
happened. The framework emits a fixed set of dotted-namespace event
types; user code may emit additional types via `graph.emit` (any
string is valid, the dot-namespaced convention is recommended). The
fixed set is the framework's vocabulary; the things you can build
on top of it.

The complete set of framework-emitted event types:

### Lifecycle

- **`goal.created`** — an operator pushed a goal into the run
  (`rt.run_goal("…")`). Behaviors subscribed to `goal.created` fire
  first; the runtime loop continues from their output.
- **`runtime.idle`** — the runtime queue is empty and there is
  budget remaining; the loop is paused, ready to resume on the
  next emit.
- **`runtime.budget_exhausted`** — the per-run budget (LLM tokens,
  wall-clock seconds, behavior fires) was hit; the loop stops with
  this event as its terminal record.

### Graph mutations

- **`object.created`** — `graph.add_object(...)` succeeded. Payload
  carries the full object — id, type, data, version, provenance.
- **`object.removed`** — `graph.remove_object(...)` succeeded.
- **`relation.created`** — `graph.add_relation(...)` succeeded.
  Payload carries source, target, type, data, provenance.
- **`relation.removed`** — `graph.remove_relation(...)` succeeded.

### Behavior dispatch

- **`behavior.scheduled`** — the runtime queued a behavior for
  dispatch. One per matching subscription on the triggering event.
- **`behavior.started`** — the behavior body began executing.
- **`behavior.completed`** — the body returned without raising.
- **`behavior.failed`** — the body raised; the runtime caught the
  exception and emitted this event. Payload carries the reason
  code and structured failure context. See
  [`failure-model`](failure-model.md) for the events-not-exceptions
  principle and [`reference/errors`](../reference/errors.md) for
  the closed reason-code taxonomy.
- **`relation_behavior.started`** — a `@relation_behavior` body
  began; sibling of `behavior.started`, carries the bound relation.

### Patterns

- **`pattern.matched`** — a Cypher-subset pattern subscription
  matched. Emitted before `behavior.started` for the matched
  bindings; carries the binding map. See
  [`patterns`](patterns.md).

### LLM and tools

- **`llm.requested`** / **`llm.responded`** — every LLM call appears
  as a request/response pair in the event log. Payload carries
  prompt content hash, model name, recorded-fixture key (in
  fixture-replay runs), and the response body. Failed transient
  attempts use the same `llm.responded` type with an `error` payload
  and retry metadata; only successful responses populate the replay
  cache.
- **`tool.requested`** / **`tool.responded`** — every tool call,
  same shape. Payload carries the tool name, input, output, and
  cache-hit status.

### Patches

- **`patch.proposed`** — `graph.propose_patch(...)` or
  `ctx.propose_object(...)` recorded a proposal. Carries the
  target id, observed version, intended diff, proposer identity.
- **`patch.applied`** — the proposal succeeded (or
  `graph.patch_object(...)` shortcut ran). Carries the resulting
  object version and the computed diff.
- **`patch.rejected`** — the proposal was refused (version
  conflict, policy refusal, or explicit `reject_patch`). Carries
  the rejection reason.

### Approvals

- **`approval.proposed`** — a policy-gated mutation produced a
  pending approval. Carries the approval id and the object/patch
  it gates.
- **`approval.granted`** — `runtime.approve(approval_id)` resolved
  a pending approval; the gated mutation lands.

### Pack lifecycle

- **`pack.loaded`** — `runtime.load_pack(...)` succeeded. Carries
  the pack name, version, object/relation types, behaviors, tools,
  policies, prompt content hashes, and the canonical settings dump.
  The pack-load order participates in the replay contract — a
  loaded run replays the same `pack.loaded` event at the same
  point in the log.

This list is the framework's stable vocabulary. The cookbook,
trace formatter, replay engine, observability metrics, and CLI
inspect command all key off these types. Custom event types from
user code live alongside them and follow the same shape; the
framework treats unknown types as opaque payload carriers.

## The developer-defined layer: object types

`graph.add_object(type, data)` accepts **any string** as the type.
There is no central enum, no required `register_object_type(...)`
call, no schema-definition step. The framework's stance is that an
object type is whatever string identifies the role an object plays
in your domain.

```python
graph.add_object("claim", {"text": "Q3 revenue grew 28% YoY.", "confidence": 0.85})
graph.add_object("memo",  {"company_id": "obj_007", "summary": "…"})
graph.add_object("topic", {"name": "battery thermal runaway"})
```

These three calls each produce an `object.created` event with the
given type string. The framework does not check the type against
anything. The data dict is JSON-encodability-validated and
otherwise opaque.

If you come from a typed-schema background (databases, Pydantic,
GraphQL, Protobuf), expect a schema-definition step and don't find
it — there isn't one. This is intentional. The framework's
abstraction surface is *events and reactions*, not
*entity-relationship diagrams*. Schemas are useful when you have
them; the optional-validation path below shows how to add one.

### Optional: pack-level schema validation

A pack can declare an object type with a Pydantic schema, and the
runtime validates `add_object(type, data)` against the schema
**after the pack is loaded**:

```python
from pydantic import BaseModel, Field
from activegraph.packs import ObjectType, Pack

class Claim(BaseModel):
    text: str
    confidence: float = Field(ge=0.0, le=1.0)

pack = Pack(
    name="my_pack",
    version="0.1.0",
    object_types=[ObjectType(name="claim", schema=Claim, description="…")],
    # …
)
```

After `runtime.load_pack(pack)`, `add_object("claim", data)`
validates `data` against `Claim`; a mismatch raises
[`pack-schema-violation`](../reference/errors/pack-schema-violation.md).
**Validation is post-load and not retroactive** — objects of type
`claim` created before the pack loaded stay as-is; objects of types
no loaded pack contributes pass through unchanged. This preserves
the no-pack default: any string works, any data shape works, you
opt into a schema by loading a pack that declares one.

See [`authoring-packs`](../guides/authoring-packs.md#4-object-types-and-relation-types)
for the full pack-side mechanics.

### Why the type lives on the data, not in a central schema

Validation, when you want it, happens at the **binding moment** —
where a behavior consumes an object type, it can declare what
fields it expects. A behavior that fires on `object.created`
filtered to `type="claim"` and reads `event.payload["object"]["data"]["text"]`
is the de facto consumer-side schema: if the field isn't there,
the behavior raises and the runtime emits `behavior.failed`. The
framework's stance is that this consumer-side discipline carries
the weight a central schema would, with the upside that domain
ontologies can evolve without a migration step.

## The developer-defined layer: relation types

Same model. `graph.add_relation(source, target, type)` accepts any
string. No central registry. A pack may declare endpoint-type
rules — "`supports` connects `evidence` to `claim`" — and the
runtime enforces them after the pack loads:

```python
from activegraph.packs import RelationType

RelationType(
    name="supports",
    source_types=("evidence",),
    target_types=("claim",),
    description="Evidence supports a claim.",
)
```

Without a pack-declared rule, any source/target/type combination
is allowed. Pack-declared rules raise
[`pack-schema-violation`](../reference/errors/pack-schema-violation.md)
on a forbidden endpoint pair.

A relation type can also carry behavior — `@relation_behavior`
attaches a rule or LLM body to a type so the type itself owns
coordination logic between its endpoints. The relation kind
(passive / rule / agentic) is a property of the *type*, not of
any individual relation instance. See
[`relations`](relations.md) for that distinction.

## How the three layers compose

The framework's vocabulary is the event types; the domain
vocabulary is the object and relation types the developer
chooses. The two interlock through behaviors:

1. An operator pushes a `goal.created` event (framework type).
2. A behavior subscribed to `goal.created` runs and creates an
   object — `graph.add_object("topic", …)` (developer type).
3. The runtime emits an `object.created` event (framework type)
   carrying the new `topic` object (developer type) in its
   payload.
4. Behaviors subscribed to `object.created` filtered to
   `type="topic"` fire — perhaps emitting `tool.requested`
   (framework type) for a web search, perhaps creating `query`
   objects (developer type).
5. The cycle continues — every developer-typed mutation produces
   a framework-typed event; every framework-typed event can
   trigger more developer-typed mutations.

The discipline: the framework speaks a small fixed vocabulary
about *what happened*; the developer speaks a domain vocabulary
about *what kind of thing it happened to*.

## Patch lifecycle states

The fourth small framework-defined vocabulary: a patch's `status`
field. Three values, defined on `core/patch.py`:

- **`proposed`** — the patch was recorded as a `patch.proposed`
  event but has not yet been applied or rejected.
- **`applied`** — the patch reached its terminal "applied" state
  via `graph.apply_patch(patch_id)` (or the `patch_object`
  auto-apply shortcut). Emits `patch.applied`.
- **`rejected`** — the patch reached its terminal "rejected"
  state via `graph.reject_patch(patch_id, reason)` or via the
  optimistic-concurrency version check at apply time. Emits
  `patch.rejected`.

`proposed` is the only non-terminal state. Re-applying or
re-rejecting a terminal patch raises
[`invalid-patch-lifecycle-state`](../reference/errors/invalid-patch-lifecycle-state.md).
See [`patches`](patches.md) for the canonical lifecycle prose;
this list exists here so the type-system page enumerates every
framework-defined vocabulary in one place.

## Designing an ontology

Because object and relation types are developer-defined, **the
ontology is part of the system you're building**. Three rules
that survive scrutiny across the v0.7 / v0.9 / external-research-
agent ontologies the framework has been built and tested
against:

**Object types are nouns describing roles in the domain, not data
bags.** `claim`, `evidence`, `question`, `risk` each name a role
something plays in a diligence workflow; a behavior that fires on
`object.created` type-filtered to one of them knows what kind of
thing it's reacting to. A generic `record` or `entity` type that
holds arbitrary data is a smell — the type discriminator has
collapsed and behaviors lose the ability to subscribe selectively.
The external user-test on a deep-research agent surfaced this
explicitly: a first pass used `data` as the type for everything,
and behaviors had to inspect payload shape to dispatch. The
second pass split into `topic / query / fact / report`, and
behaviors became one-liners on `on=["object.created"],
where=lambda e: e.payload["object"]["type"] == "topic"`.

**Relation types are verbs or predicates describing meaningful
structure.** `supports`, `contradicts`, `depends_on`, `references`,
`derived_from` each describe a relationship that something
downstream cares about. A generic `related_to` is a smell — it
collapses the type discriminator the same way a generic object
type does, and pattern subscriptions on the relation type stop
being useful. Verbs that read naturally in the call site
(`graph.add_relation(evidence, claim, "supports")` reads as
"evidence supports claim") are the heuristic.

**Keep the vocabulary small.** Eight to fifteen object types
covers most domains. The Diligence pack ships eight object types
and six relation types and is intentionally on the small end of
that range — packs that try to model everything tend to model
nothing. New types earn their place when an actual behavior or
query needs to distinguish them; future-proofing with speculative
types pollutes the ontology without adding behavior.

The discipline carries the weight that a central schema would:
the *type itself* is the consumer-side contract. When a behavior
fires on `type="claim"` it expects `claim` semantics; when it
emits a `supports` relation it commits to `supports` semantics.
Multiple behaviors agreeing on what those names mean is the
ontology, and it's encoded in the behavior bodies — not in a
schema file.

## Worked example: the Diligence pack ontology

The shipped Diligence pack is a concrete, well-designed type
vocabulary. It is **an example ontology, not framework base
types** — you would design your own for your domain. The pack is
documented here so the design pattern is visible.

Eight object types (`activegraph/packs/diligence/object_types.py`):

| Type            | Role                                                |
|-----------------|-----------------------------------------------------|
| `company`       | The target of a diligence run.                      |
| `document`      | A source document the researcher pulled in.         |
| `question`      | A research question generated from the thesis.     |
| `claim`         | A factual statement about the company.              |
| `evidence`      | A verbatim quote supporting a claim.                |
| `contradiction` | A detected conflict between two claims.             |
| `risk`          | A material risk identified during diligence.        |
| `memo`          | The final diligence memo for a company.             |

Six relation types:

| Type           | Endpoints (source → target)             | Meaning                                       |
|----------------|-----------------------------------------|-----------------------------------------------|
| `addresses`    | `claim` → `question`                    | A claim addresses a research question.        |
| `supports`     | `evidence` → `claim`                    | Evidence supports a claim.                    |
| `contradicts`  | `claim` → `claim`                       | Two claims are in conflict.                   |
| `references`   | `{claim, memo}` → `document`            | A claim or memo references a source document. |
| `derived_from` | `{claim, evidence}` → `document`        | Provenance back to a source document.         |
| `mitigates`    | `{evidence, claim}` → `risk`            | Evidence or a claim mitigates a risk.         |

Each object type carries a Pydantic schema (validated when the
pack is loaded); each relation type pins its endpoints. Together
they form a small graph ontology that a small set of behaviors
(claim extractor, contradiction detector, memo synthesizer)
operates on. None of these types are special to the framework;
load a different pack and you get a different ontology.

The Diligence pack is the [reference pack](../reference/api/packs/diligence.md);
[`authoring-packs`](../guides/authoring-packs.md) is the how-to for
building your own.

## What's related

- [`graph`](graph.md) — objects and relations as projections of
  the event log; the "graph as projection" principle.
- [`events`](events.md) — the append-only history and how
  framework event types drive behavior dispatch.
- [`relations`](relations.md) — the three relation kinds
  (passive / rule / agentic) and when to attach behavior to a
  relation type.
- [`patches`](patches.md) — the patch lifecycle in full; this
  page only enumerates the state values.
- [`failure-model`](failure-model.md) — the `behavior.failed`
  reason-code taxonomy that lives on the event payload.
- [`authoring-packs`](../guides/authoring-packs.md) — declaring
  object types, relation types, and their Pydantic schemas in a
  pack.
- [Diligence pack reference](../reference/api/packs/diligence.md) —
  the worked example ontology rendered from source.
