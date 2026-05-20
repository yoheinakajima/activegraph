# A minimalist coding agent on Active Graph

*A thought-experiment sketch, in the spirit of [earendil-works/pi](https://github.com/earendil-works/pi).*

## What we're after

Pi's appeal is that a coding agent doesn't need a lot. At the core it's a
tight loop:

1. Give the model a goal and a list of tools.
2. Model proposes a tool call.
3. Runtime executes it.
4. Result goes back to the model.
5. Repeat until the model says "done."

Everything else — TUI, multi-provider abstraction, session sharing —
is icing. The minimalism question is: how few moving parts can you build
that loop with, and what do you get in return?

This note sketches that loop on Active Graph and argues the trade is
worth it: you give up roughly nothing in code volume, and you get
provenance, persistence, replay, and fork-and-diff for free.

## Mental model

Active Graph already speaks the right language. Active Graph's BabyAGI
example (`examples/babyagi.py`) reframes "execute → reflect → plan
next" as three reactive behaviors over a shared graph. A coding agent
is the same shape with a different vocabulary:

| BabyAGI                          | Coding agent                             |
| -------------------------------- | ---------------------------------------- |
| `task` object                    | `tool_call` object                       |
| `result` object                  | `tool_result` object                     |
| `executor` behavior (does work)  | `@tool` functions (read/write/bash)      |
| `task_creator` behavior          | `planner` LLM behavior (next tool / done)|

The loop runs as long as new `tool_call` objects keep landing on the
graph. Termination is the planner returning `done=True` instead of an
empty task list.

## Schema

Four object types, one relation type. No more.

```
file_snapshot { path, content, sha }    # what we read
tool_call     { name, args, status }    # what the model wants to run
tool_result   { call_id, output, ok }   # what came back
answer        { content }               # final message to the user

tool_call --[produced]--> tool_result
```

Object-type strings are free-form in Active Graph (no central registry),
so adding a fifth type later costs nothing.

## The agent in ~150 lines

```python
from pydantic import BaseModel, Field
from activegraph import Frame, Graph, Runtime, llm_behavior, tool
from activegraph.llm import AnthropicProvider


# ---- tools ----------------------------------------------------------

class ReadFileIn(BaseModel): path: str
class ReadFileOut(BaseModel): content: str

@tool(name="read_file", input_schema=ReadFileIn, output_schema=ReadFileOut,
      description="Read a UTF-8 file from the workspace.")
def read_file(args, ctx):
    return ReadFileOut(content=open(args.path).read())


class WriteFileIn(BaseModel):
    path: str
    content: str
class WriteFileOut(BaseModel): bytes_written: int

@tool(name="write_file", input_schema=WriteFileIn, output_schema=WriteFileOut,
      description="Overwrite a file with the given content.")
def write_file(args, ctx):
    open(args.path, "w").write(args.content)
    return WriteFileOut(bytes_written=len(args.content))


class BashIn(BaseModel): cmd: str
class BashOut(BaseModel):
    stdout: str; stderr: str; exit: int

@tool(name="bash", input_schema=BashIn, output_schema=BashOut,
      description="Run a shell command. 30s timeout.")
def bash(args, ctx):
    import subprocess
    r = subprocess.run(args.cmd, shell=True, capture_output=True,
                       text=True, timeout=30)
    return BashOut(stdout=r.stdout, stderr=r.stderr, exit=r.returncode)


# ---- the loop -------------------------------------------------------

class Step(BaseModel):
    thought: str = Field(description="One sentence: what you're doing.")
    tool: str | None = Field(description="Tool name, or null if done.")
    args: dict = Field(default_factory=dict)
    done: bool = False
    final_answer: str | None = None


@llm_behavior(
    name="planner",
    on=["goal.created", "object.created"],
    where={"object.type": "tool_result"},   # fire on goal, or after each result
    description=(
        "You are a coding agent. Look at the goal and the trace so far. "
        "Either call exactly one tool (read_file/write_file/bash) to make "
        "progress, or set done=true and provide final_answer."
    ),
    output_schema=Step,
    creates=["tool_call", "answer"],
)
def planner(event, graph, ctx, step: Step):
    if step.done:
        graph.add_object("answer", {"content": step.final_answer or ""})
        return
    call = graph.add_object("tool_call",
        {"name": step.tool, "args": step.args, "status": "pending"})
    # Active Graph's tool dispatcher sees this and emits tool.requested →
    # tool.responded → object.created(tool_result), which re-fires `planner`.
    ctx.dispatch_tool(call.id, step.tool, step.args)


def run(goal: str, *, workdir: str):
    runtime = Runtime(
        Graph(),
        frame=Frame(goal=goal,
                    constraints=[f"All paths are relative to {workdir}."]),
        llm_provider=AnthropicProvider(),
        budget={"max_events": 200, "max_seconds": 300},
        persist_to=f"traces/agent.sqlite",
    )
    runtime.run_goal(goal)
```

That's the whole agent. The `Step` schema is the structured equivalent
of pi's tool-call parser; Active Graph's `output_schema=` removes the
need to write one.

## What you get for free

Because every mutation is an event in an append-only log:

- **Audit trail.** Every file read, every file write, every bash command,
  every LLM token, every cost — recorded in order with parent/child
  causal links. `activegraph inspect traces/agent.sqlite` reads it back.
- **Resume.** Kill the process mid-run; reload the trace; the graph is
  the projection of the log. No checkpointing code to write.
- **Fork-and-diff.** "What if I had let it use `sed` instead of
  rewriting the file?" Fork at the relevant event, change the
  constraint, re-run, structurally diff the two graphs. The
  `replay_llm_cache=True` knob serves cached LLM responses for events
  prior to the fork point — zero extra API spend on the rewound prefix.
- **Budget enforcement.** `max_events` / `max_seconds` are built in.
- **Provider swap.** `AnthropicProvider() → OpenAIProvider()` is a
  one-line change; the rest of the agent doesn't know.

Pi gets some of these (state management, provider abstraction). The
graph-based audit trail and fork-and-diff are the parts you'd otherwise
have to build yourself.

## What you give up vs. pi

- **TUI.** Active Graph has no terminal UI; you'd render the graph
  yourself or live with `inspect`. For a CLI front-end this is ~50 lines
  of `rich`/`textual`.
- **Streaming output.** `@llm_behavior` waits for the structured
  response. If you want token streaming for UX, you'd add it at the
  provider layer (small surgery) or bypass `@llm_behavior` for the
  user-facing turns.
- **The "self-extensible" framing.** Pi leans into runtime tool
  registration / plugins; here tools are `@tool`-decorated Python
  functions in a file. Adding one is a 10-line edit, not a plugin.
- **One-shot speed.** The reactive runtime has a small fixed overhead
  per event (persistence, behavior matching). For a 5-tool-call task
  this is invisible; for a 500-call task it's noticeable but still
  small relative to LLM latency.

## Variations worth considering

- **Pure-reactive tools.** Skip the explicit `ctx.dispatch_tool` and let
  a `@behavior(on=["object.created"], where={"object.type":"tool_call"})`
  dispatcher fire instead. More uniformly reactive; slightly more code.
- **Relation behaviors for guardrails.** Add a
  `@relation_behavior(relation_type="modifies",
  on=["object.created"], where=...)` that blocks `write_file` on paths
  outside the workspace by emitting `behavior.failed` before the tool
  runs. The constraint lives on the edge, not inside every tool.
- **Memory as a sub-graph.** Long-running sessions: project
  `file_snapshot` objects into a separate persisted graph and load it
  as starting state on the next run. The `goal.created` event is the
  hand-off point.

## TL;DR

The pi-shaped agent loop is ~four objects, one LLM behavior, and three
tools — call it 150 lines. Reusing Active Graph's `Runtime`, `@tool`,
`@llm_behavior`, and event store means you write only the agent-specific
pieces and inherit persistence, replay, fork-and-diff, and cost
accounting for free. The minimalism survives; the infrastructure cost
of being able to debug, resume, and experiment goes to roughly zero.
