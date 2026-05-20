# Plan-and-execute (DAG) on Active Graph

*A planner emits a task graph; relations carry the coordination.
Companion to `minimalist-coding-agent.md`.*

## The pattern

BabyAGI plans one task at a time; that's fine for exploratory work but
wasteful when the work is parallelizable or when downstream tasks need
specific upstream outputs. Plan-and-execute (sometimes "task-DAG
agents," "LLM Compiler," "Tree-of-Plans") splits the problem:

1. A **planner** decomposes the goal into tasks with explicit
   dependencies — a DAG, not a list.
2. **Executors** pick up tasks whose deps are satisfied and run them.
3. As each task finishes, downstream tasks unblock.
4. A **synthesizer** assembles the final answer once the leaves are
   done.

This is the place where Active Graph's **relation behaviors** earn
their keep. The coordination logic — "unblock the dependent when the
prereq completes" — lives on the edge, not duplicated inside every
executor.

## Schema

Three object types, one carrying relation. The whole plan is a graph,
literally.

```
task   { title, status }       # status: blocked | open | running | done
result { task_id, content }
answer { content }

task --[depends_on]--> task    # B depends on A
task --[produced]----> result
```

## Behaviors

```python
from pydantic import BaseModel, Field
from activegraph import Frame, Graph, Runtime, llm_behavior, relation_behavior
from activegraph.llm import AnthropicProvider


class PlannedTask(BaseModel):
    id: str = Field(description="A short slug, used to wire dependencies.")
    title: str
    depends_on: list[str] = Field(default_factory=list,
                                  description="Slugs of prereq tasks.")

class Plan(BaseModel):
    tasks: list[PlannedTask]


@llm_behavior(
    name="planner",
    on=["goal.created"],
    description=(
        "Decompose the goal into 3-10 concrete tasks with explicit "
        "depends_on edges. Leaf tasks have no deps; aggregation tasks "
        "depend on their inputs. Do NOT chain tasks unnecessarily — "
        "parallel is better than serial."
    ),
    output_schema=Plan,
    creates=["task"],
)
def planner(event, graph, ctx, plan: Plan):
    slug_to_id = {}
    # First pass: create all tasks; status=blocked unless they have no deps.
    for t in plan.tasks:
        status = "open" if not t.depends_on else "blocked"
        obj = graph.add_object("task", {"title": t.title, "status": status})
        slug_to_id[t.id] = obj.id
    # Second pass: wire depends_on edges.
    for t in plan.tasks:
        for dep_slug in t.depends_on:
            graph.add_relation(slug_to_id[t.id], slug_to_id[dep_slug],
                               "depends_on")


@llm_behavior(
    name="executor",
    on=["object.created", "object.updated"],
    where={"object.type": "task", "object.data.status": "open"},
    description="Execute the task. Return a concrete result.",
    output_schema=type("R", (BaseModel,), {"__annotations__": {"content": str}}),
    creates=["result"],
)
def executor(event, graph, ctx, out):
    task = event.payload["object"]
    graph.patch_object(task["id"], {"status": "done"})
    r = graph.add_object("result", {"task_id": task["id"], "content": out.content})
    graph.add_relation(task["id"], r.id, "produced")


# This is the whole coordination layer. One edge-level behavior.
@relation_behavior(
    name="unblock",
    relation_type="depends_on",
    on=["object.updated"],
    where={"object.type": "task", "object.data.status": "done"},
)
def unblock(relation, event, graph, ctx):
    # A dep just completed. Check if every dep of `relation.source` is now done.
    src = graph.get_object(relation.source)
    if src.data["status"] != "blocked":
        return
    deps = graph.relations(source=src.id, type="depends_on")
    if all(graph.get_object(d.target).data["status"] == "done" for d in deps):
        graph.patch_object(src.id, {"status": "open"})


@llm_behavior(
    name="synthesizer",
    on=["object.updated"],
    where={"object.type": "task", "object.data.status": "done"},
    description="Assemble the final answer from all task results.",
    output_schema=type("A", (BaseModel,), {"__annotations__": {"content": str}}),
    creates=["answer"],
    # Only fires when ALL tasks are done; see guard below.
)
def synthesizer(event, graph, ctx, out):
    open_or_blocked = graph.view().objects(
        type="task", where={"status": {"in": ["open", "blocked", "running"]}})
    if open_or_blocked:
        return                                  # not yet
    graph.add_object("answer", {"content": out.content})
```

The five-line `unblock` is the whole reason the pattern is small. In a
hand-rolled framework you'd be tracking `pending_deps[task_id]` in a
dict, decrementing on completion, and worrying about races. Here the
edge fires when one end changes and the runtime gives you a consistent
view of the other.

## What you get for free

- **Causal trace, not just temporal.** `activegraph inspect` shows each
  task with parents = its `depends_on` predecessors. You can answer
  "why did task X run when it did" by walking edges, not by reading
  timestamps.
- **Parallelism for free if you want it.** The runtime is
  single-threaded by default, but every `open` task is independent;
  swapping in a thread-pool dispatcher is a runtime config change, not
  a refactor of the agent logic.
- **Replay the plan, vary the executor.** Fork after the planner
  emits its DAG, swap the executor's model from Sonnet to Haiku in the
  fork, re-run, diff the two answer objects. The plan structure stays
  identical; only execution quality varies.

## Variations

- **Plan revision mid-flight.** Add a `replanner` `@llm_behavior` that
  fires on `task.failed` (a wrapper event you emit when an executor's
  output looks wrong). It can add new tasks and new `depends_on` edges
  to the existing graph — the same `unblock` keeps working.
- **Heterogeneous executors.** Several executor behaviors with
  different `where={}` filters (e.g. `where={"object.data.kind":
  "research"}` vs. `"code"`). Routing is a pattern match, not a switch
  statement inside one executor.
- **Critical-path costing.** Because every `llm.responded` is an event
  with cost, you can compute the cost of the longest dependency chain
  vs. the total cost of the run — useful when you're deciding whether
  the parallel structure is actually paying off.

## TL;DR

Two `@llm_behavior`s, one `@relation_behavior`, two `@tool`-free object
types. The DAG itself is the state, the edge is the coordinator, and
the trace is the postmortem. Coordination across tasks ends up costing
five lines instead of a scheduler.
