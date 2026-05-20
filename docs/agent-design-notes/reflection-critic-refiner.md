# Reflection / critic-refiner on Active Graph

*A draft-and-improve loop. Companion to `minimalist-coding-agent.md`.*

## The pattern

The most common reliability hack for LLM output: don't trust the first
draft. Generate, critique, revise, repeat — until a critic is happy or
you've used your budget. Variants ship under names like "self-refine,"
"reflexion," and "constitutional AI."

The shape is almost embarrassingly simple:

```
writer → draft → critic → score+notes → (if low) writer with notes → …
```

The interesting part is how you spend the iterations: cheap critics
catch obvious problems; an expensive critic at the end is wasted spend
if the cheap one already failed. Active Graph makes the cost visible,
makes the iterations replayable, and lets you fork a run to compare
"two rounds with a strict critic" vs. "five rounds with a lenient one"
without paying twice.

## Schema

```
prompt    { text }                              # the seed
draft     { content, round }                    # what the writer produced
critique  { draft_id, score, notes, blocking }  # what the critic thought
accepted  { draft_id, content }                 # the final answer

draft   --[critiqued_by]--> critique
draft   --[revises]-------> draft          # round n+1 points at round n
```

## The behaviors

Two `@llm_behavior`s and a tiny rule. ~80 lines.

```python
from pydantic import BaseModel, Field
from activegraph import Frame, Graph, Runtime, behavior, llm_behavior
from activegraph.llm import AnthropicProvider


class Draft(BaseModel):
    content: str = Field(description="The full answer to the prompt.")


class Critique(BaseModel):
    score: float = Field(ge=0, le=1, description="0=unusable, 1=ship it.")
    notes: str  = Field(description="Specific, actionable issues.")
    blocking: bool = Field(description="True if score < accept_threshold.")


ACCEPT = 0.85
MAX_ROUNDS = 4


@llm_behavior(
    name="writer",
    on=["goal.created", "object.created"],
    where={"object.type": "critique"},     # fire on the prompt, or after a critique
    description=(
        "Write the best possible answer to the prompt. On revisions you "
        "will be shown the previous draft and the critic's notes — "
        "address them, do not start over."
    ),
    output_schema=Draft,
    creates=["draft"],
)
def writer(event, graph, ctx, out: Draft):
    prior = _latest_draft(graph)
    round_ = (prior["data"]["round"] + 1) if prior else 1
    draft = graph.add_object("draft", {"content": out.content, "round": round_})
    if prior:
        graph.add_relation(draft.id, prior["id"], "revises")


@llm_behavior(
    name="critic",
    on=["object.created"],
    where={"object.type": "draft"},
    description=(
        f"Score the draft 0-1. Set blocking=True iff score < {ACCEPT}. "
        "Be specific in notes — vague critique produces vague revisions."
    ),
    output_schema=Critique,
    creates=["critique"],
)
def critic(event, graph, ctx, out: Critique):
    draft = event.payload["object"]
    c = graph.add_object("critique", {
        "draft_id": draft["id"],
        "score": out.score,
        "notes": out.notes,
        "blocking": out.blocking,
    })
    graph.add_relation(draft["id"], c.id, "critiqued_by")


@behavior(name="terminator", on=["object.created"],
          where={"object.type": "critique"})
def terminator(event, graph, ctx):
    c = event.payload["object"]["data"]
    if not c["blocking"]:
        draft = graph.get_object(c["draft_id"])
        graph.add_object("accepted", {
            "draft_id": draft.id, "content": draft.data["content"]
        })
        graph.emit("run.done", {})
    elif _round_count(graph) >= MAX_ROUNDS:
        # Out of budget — accept the best-scoring draft anyway.
        best = _best_draft(graph)
        graph.add_object("accepted", {"draft_id": best.id,
                                       "content": best.data["content"]})
        graph.emit("run.done", {"reason": "max_rounds"})
```

`_latest_draft`, `_round_count`, `_best_draft` are 3-line graph view
helpers (`view.objects(type="draft", ...)` sorted by round).

## What you get for free

- **Cheap A/B on the critic.** Fork the trace after round 1. In the
  fork, replace `critic`'s system prompt or schema. Re-run with
  `replay_llm_cache=True` — the writer's round-1 draft is reused from
  cache; only the critique and downstream events are fresh. You're
  comparing critics, not paying for two full runs.
- **Cost-per-quality.** Every `llm.responded` event carries its own
  cost. `activegraph inspect` aggregates spend per behavior, so you
  see at a glance whether the critic is earning its money on the
  marginal round.
- **The revision chain is a graph, not a list.** `(draft)-[revises]->
  (draft)` is a real edge; you can walk it, diff successive drafts, or
  prune branches that scored badly.

## Variations

- **Multi-critic ensemble.** Register two `critic` behaviors with
  different `name=` and different system prompts (strict / lenient).
  `terminator` waits for both critiques on each draft before deciding.
  Lives entirely in the behavior layer — no new infrastructure.
- **Anchored revisions.** Add a `must_preserve` field to the prompt
  schema; check via a `@relation_behavior` on `revises` that fires a
  `behavior.failed` event when the new draft drops the anchor. The
  failure becomes part of the audit trail, not a silent regression.
- **Critic-as-tool.** If the critic is deterministic (e.g. runs unit
  tests, linters, format checks) it should be a `@tool`, not an
  `@llm_behavior`. Same graph, real numbers, free cache.

## TL;DR

Two `@llm_behavior`s, a 5-line terminator, four object types. The
reflection loop is just two reactive subscriptions over a shared
graph. The graph carries every draft and every critique forward,
which is what makes the pattern debuggable instead of folkloric.
