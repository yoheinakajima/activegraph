# Multi-agent debate / judge on Active Graph

*Two personas argue; a judge decides.
Companion to `minimalist-coding-agent.md`.*

## The pattern

For questions where the failure mode is "the model is confidently
wrong," a single-shot answer is risky. Debate — popularized in
Anthropic's "AI Safety via Debate" line of work and in practical
ensembles like LangChain's MAD/Society-of-Mind setups — runs two LLM
personas with opposing positions across N rounds, then asks a third
("judge") to pick the better-supported side.

What makes it useful in practice isn't the philosophical framing; it's
that the **transcript** is more inspectable than a chain-of-thought
monologue. You can read where each side conceded, where the judge's
verdict came from, and whether the loser raised points the winner
never answered.

Active Graph's contribution: each persona is just a behavior with its
own `Frame`. Personas read the same graph and the same trace; nothing
about the runtime privileges one side.

## Schema

```
question { text }
turn     { side, round, content, replies_to }   # one utterance
verdict  { winner, reasoning, confidence }

question --[debated_in]----> turn
turn     --[replies_to]----> turn               # n+1 → n
turn     --[judged_by]-----> verdict            # last turn → verdict
```

## Behaviors

Two `@llm_behavior`s with distinct frames + one judge. ~120 lines.

```python
from pydantic import BaseModel, Field
from activegraph import Frame, Graph, Runtime, behavior, llm_behavior
from activegraph.llm import AnthropicProvider


ROUNDS = 3


class Turn(BaseModel):
    content: str = Field(
        description="Your argument or rebuttal. 2-5 sentences, concrete.")


@behavior(name="kickoff", on=["goal.created"])
def kickoff(event, graph, ctx):
    q = graph.add_object("question", {"text": event.payload["goal"]})
    # Seed: both sides need to write a round-1 turn before they can rebut.
    graph.emit("debate.opening", {"question_id": q.id})


def _make_debater(name: str, side: str, persona: str):
    @llm_behavior(
        name=name,
        on=["debate.opening", "object.created"],
        where={"object.type": "turn", "object.data.side": _opposite(side)},
        description=(
            f"You are debating the {side} position. {persona} "
            "On opening, state your position with the strongest concrete "
            "argument. On rebuttals, identify the opponent's weakest "
            "claim and refute it with evidence or a counterexample. "
            "Do NOT repeat earlier points."),
        output_schema=Turn,
        creates=["turn"],
        # Frame override: each debater carries its own constraints layered
        # on top of the runtime frame.
        frame_overrides={"role": side},
    )
    def debater(event, graph, ctx, out: Turn):
        round_ = _current_round(graph, side) + 1
        if round_ > ROUNDS:
            return
        turn = graph.add_object("turn", {
            "side": side, "round": round_, "content": out.content,
        })
        prev = _last_opponent_turn(graph, side)
        if prev:
            graph.add_relation(turn.id, prev["id"], "replies_to")
        if round_ == ROUNDS and _both_sides_done(graph):
            graph.emit("debate.closed", {})
    return debater


pro = _make_debater("pro_debater", "pro",
    "You believe the proposition. Argue rigorously, but concede points "
    "you cannot defend rather than evading.")
con = _make_debater("con_debater", "con",
    "You believe the negation. Argue rigorously, but concede points "
    "you cannot defend rather than evading.")


class Verdict(BaseModel):
    winner: str = Field(description="'pro', 'con', or 'tie'.")
    reasoning: str = Field(description="Cite specific turns by round and side.")
    confidence: float = Field(ge=0, le=1)


@llm_behavior(
    name="judge",
    on=["debate.closed"],
    description=(
        "Read the full transcript. Pick the side whose strongest "
        "unrebutted claim is most decisive. Reward concrete evidence; "
        "penalize unaddressed counterarguments. Do not bring in outside "
        "knowledge — judge the debate as held."),
    output_schema=Verdict,
    creates=["verdict"],
)
def judge(event, graph, ctx, v: Verdict):
    last_turn = _last_turn(graph)
    verdict = graph.add_object("verdict", v.dict())
    graph.add_relation(last_turn.id, verdict.id, "judged_by")
```

`_current_round`, `_last_opponent_turn`, `_both_sides_done`,
`_last_turn` are 2-3 line view helpers.

The interesting trick is `frame_overrides={"role": side}`. Active
Graph's frame is the contextual envelope every LLM call carries; by
layering a per-behavior override on top of the runtime frame, each
debater sees the same shared graph but argues from a different stance.
Same trace, two voices, no separate sessions to coordinate.

## What you get for free

- **Symmetric audit.** Every turn from both sides is in one trace. You
  can post-hoc compute "rounds where pro conceded" or "topics con
  refused to engage with" with a single graph query.
- **Stage swap.** Want to test a stronger judge? Fork after
  `debate.closed`, swap the judge's model, re-run with
  `replay_llm_cache=True`. Same debate, fresh verdict, no extra debater
  spend.
- **Replay attack on yourself.** Forking with one debater's prompt
  modified (e.g. forced to use only formal logic) and replaying the
  other from cache lets you stress-test how robust the verdict was to
  the losing side's style. Cheap, deterministic.
- **No coordination protocol.** Debater A doesn't "send" anything to
  Debater B. A writes a `turn`; B's subscription fires. The graph is
  the protocol.

## Variations

- **More than two sides.** N debaters with distinct personas, each
  `where={"object.data.side": {"in": [all-other-sides]}}`. Same shape.
- **Jury, not judge.** Replace the single `judge` with three
  `@llm_behavior`s under different frames, plus a deterministic
  majority-vote `@behavior` on their verdicts. Cheaper hedge against
  judge bias than a stronger judge model.
- **Open evidence.** Add a `@tool(name="lookup")` that either side can
  call mid-debate; tool results land as `evidence` objects, and
  `(turn)-[cites]->(evidence)` relations make the appeal-to-evidence
  chain queryable.
- **Anytime termination.** A `should_stop` behavior on each new turn
  asks the judge "is this round decisive yet?" — if yes, emit
  `debate.closed` early. Saves budget on lopsided debates.

## When NOT to use this

Debate adds 3-5× the cost of a single-shot answer for questions where
a single shot would have been fine. It earns its keep on:
- High-stakes correctness (medical, legal, infra)
- Genuinely contested questions
- Anywhere the failure mode is "confidently wrong, plausibly worded"

It is overkill for routine summarization, classification, or
instruction-following.

## TL;DR

Two parameterized `@llm_behavior`s + a judge + a graph that holds
the transcript. Personas are frame overrides on top of one runtime;
turns are nodes; replies are edges. The debate's persuasive structure
becomes a real graph you can query, not a string you have to re-parse.
