# Quickstart

Ten minutes from install to a working custom behavior. By the end of
this tutorial you'll have run the framework, written your own code
in it, saved a run, inspected it from the outside, and used the
fork-and-diff primitive that's specific to Active Graph and uncommon
in other agent frameworks. The seven steps build on each other; do
them in order.

If you finish in less than ten minutes, the tutorial isn't broken —
you read faster than the average. If you finish in more than fifteen,
something is rough; file an issue at
[GitHub](https://github.com/yoheinakajima/activegraph/issues) with
where you got stuck.

## 1. Install

```bash
pip install activegraph
activegraph --version
```

The bare install includes the runtime, the SQLite store, and the
bundled Diligence pack. No API key needed for this tutorial.

## 2. Run the bundled demo

```bash
activegraph quickstart
```

This runs the bundled Diligence pack against recorded fixtures —
three companies, three diligence memos, no network, about twenty
seconds. The run is byte-deterministic; every machine produces the
same output, which is why the snapshot test for this command works.

You'll see four sections of output: a header naming the pack and
companies, a long trace, one of the produced memos rendered in full,
and two prose sections ("what just happened" and "try next"). Don't
worry about reading every trace line yet — step 3 is where we'll
look at the trace.

The key beat: that memo came from nothing but a fixture-backed demo,
in seconds, with the same output on your machine as on mine. The
framework's pitch is "auditable agentic systems"; the deterministic
demo is that pitch made tangible.

## 3. Read the trace

Scroll back up to the trace block in the output. The lines starting
with `[goal.created]`, `[behavior.started]`, `[llm.requested]`,
`[object.created]`, and so on are **events** — the framework's
append-only record of everything that happened. Active Graph models
the world as a graph of objects connected by typed edges; events are
how the graph changes over time.

A few specific lines to find:

- `[pack.loaded]` near the top — when the Diligence pack registered
  its behaviors, tools, and prompt templates.
- `[goal.created] user: "Diligence: Northwind Robotics"` — when the
  runtime received its first goal.
- A `[behavior.started]` for `diligence.company_planner` followed
  immediately by `[object.created] company#1` — the planner behavior
  fired in response to the goal, and produced a `company` object on
  the graph.
- `[llm.requested]` and `[llm.responded]` pairs — every LLM call
  was served by the bundled fixture provider
  (`RecordedDiligenceProvider`), so no network requests fired. The
  trace shows `cost=$0.00X latency=0.Xs` on each `llm.responded`
  line; in a production run against a real provider those numbers
  would be real costs and real latencies.
- `[runtime.idle] queue empty, budget remaining` at the end of each
  goal's events — the runtime finished all the work it could do and
  stopped.

Two layers worth distinguishing now so the vocabulary lands cleanly
later: the **provider** is what produces LLM responses (here, the
fixture provider; in production, an `AnthropicProvider`). The
runtime's **replay cache** is a separate layer that records
`llm.responded` events and serves them back when a run replays
under strict-replay mode or when `Runtime.fork(at_event=...)` is
called in-process — that's where you'll see `cache_hit=true` in
the trace. See [`concepts/replay`](concepts/replay.md) and
[`concepts/forking`](concepts/forking.md) for the deep dive.

That trace is the framework's audit trail. The same artifact you
just read for fun is what you'd read while debugging a production
incident. Most agent frameworks don't have this kind of trace, and
that's one of the things that makes Active Graph different.

We'll come back to events in more detail in
[`concepts/events`](concepts/events.md). For now: the trace is the
truth; everything else is a projection of it.

## 4. Write a custom behavior

```bash
activegraph quickstart --interactive
```

This walks you through writing your first behavior. A **behavior** is
the framework's unit of reactive code — a Python function decorated
with `@behavior` that subscribes to events and produces more events
(new objects, new relations, custom events).

The interactive command scaffolds a starter behavior at
`./activegraph_quickstart/my_first_behavior.py` and prompts you to
edit it. The TODO in the scaffold is a small problem: flag any
claim that mentions revenue growth above 25%. You can parse the
text with a regex; the framework supplies the integration with the
graph.

Open the file in your editor, replace the TODO with the parsing
logic, and save. The full file is short — fewer than twenty lines
when you're done.

Don't worry about getting the regex perfect. The goal of this step
is to feel the shape of writing a behavior: a decorator declaring
when it fires, a function body that reads from the event and writes
to the graph.

We'll go deeper on behaviors in
[`concepts/behaviors`](concepts/behaviors.md).

## 5. Run your behavior

Back in the terminal, type `continue` at the prompt. The framework
loads your file fresh, runs the Diligence pack against one company,
and reports how many times your behavior fired.

Scroll the trace and find your behavior's lines:

```
[behavior.started]    growth_flagger  (matched object.created: claim#NN)
[event.emitted]       growth.flagged claim_id=claim#NN growth=28
[behavior.completed]  growth_flagger
```

That's your code, running in the same runtime as the Diligence
pack, firing on the same events, producing events that downstream
behaviors could subscribe to. Your behavior is a first-class
citizen of the graph — there's no separate "user behavior" path.

Iterate as much as you want — edit the file, type `continue`, edit
again. When you're done, type `quit`. Your file persists at
`./activegraph_quickstart/my_first_behavior.py`; keep it, modify
it, or delete the directory.

## 6. Save and inspect

The fixture-backed run from step 2 saved itself to
`/tmp/activegraph_quickstart/quickstart_demo_run.db`. Try inspecting
it:

```bash
activegraph inspect sqlite:////tmp/activegraph_quickstart/quickstart_demo_run.db
```

You'll see a summary: run id, state, budget snapshot, registered
behaviors, the tail of recent events. The same data the trace
showed, but as a query surface — you read it from outside the run,
which means you can read it after the run finishes, after the
process exits, after a restart. Active Graph runs persist.

Try a focused query:

```bash
activegraph inspect sqlite:////tmp/activegraph_quickstart/quickstart_demo_run.db \
    --event evt_006
```

That prints the full payload of one event. The event id is what an
error message would name if something went wrong; `--event` is how
you'd start investigating.

`activegraph inspect --help` shows the full surface. The
[CLI reference](reference/cli.md) is the canonical doc; the
[debugging cookbook](cookbook/debugging.md) walks through diagnostic
workflows that build on these primitives.

## 7. Fork and diff

The closer. Forking is the framework's most differentiated
capability — most agent frameworks can't do this. A fork is a new
run that shares the parent's event log up to a chosen point, then
diverges from there. Combined with a diff against the parent, fork
answers the question "what would have happened if I'd configured
this differently?"

The full fork-with-override workflow uses both a Python snippet and
the `activegraph diff` CLI command. Drop this into a file
(`fork_and_diff.py`) and run it:

```python
import sqlite3

from activegraph import Runtime
from activegraph.packs.diligence import DiligenceSettings, pack as diligence_pack
from activegraph.packs.diligence.fixtures import (
    RecordedDiligenceProvider,
    THREE_COMPANIES,
)
from activegraph.store import open_store
from activegraph.store.sqlite import SQLiteEventStore

DB_PATH = "/tmp/activegraph_quickstart/quickstart_demo_run.db"
PARENT_URL = f"sqlite:///{DB_PATH}"
PARENT_RUN = "quickstart_demo_run"
FORK_RUN = "quickstart_cautious"

# Tutorial-only: remove any prior fork so this snippet is re-runnable.
# Real workflows handle fork-id collisions intentionally — pick a
# unique FORK_RUN per experiment instead of deleting the prior one.
with sqlite3.connect(DB_PATH) as _conn:
    deleted = _conn.execute(
        "DELETE FROM events WHERE run_id = ?", (FORK_RUN,)
    ).rowcount
    _conn.execute("DELETE FROM runs WHERE run_id = ?", (FORK_RUN,))
    if deleted:
        print(f"Removed previous fork ({deleted} events) to re-run cleanly.")

# Pick the goal event for the first company as the fork point.
parent_store = open_store(PARENT_URL, run_id=PARENT_RUN)
fork_at = next(
    e.id for e in parent_store.iter_events()
    if e.type == "goal.created"
)

# Copy the parent's events up to the fork point into a new run.
SQLiteEventStore.fork_run(
    DB_PATH,
    parent_run_id=PARENT_RUN,
    new_run_id=FORK_RUN,
    at_event_id=fork_at,
    label="cautious",
    created_at="2026-01-01T00:00:00Z",
)

# Load the fork. The provider matches the parent run's
# RecordedDiligenceProvider, so cached LLM responses from the parent
# replay byte-identically and no network or API key is needed.
fork_rt = Runtime.load(
    PARENT_URL,
    run_id=FORK_RUN,
    llm_provider=RecordedDiligenceProvider(companies=THREE_COMPANIES),
)
fork_rt.load_pack(
    diligence_pack,
    settings=DiligenceSettings(
        llm_model="claude-sonnet-4-5",
        confidence_threshold_for_review=0.9,
    ),
)
fork_rt.run_until_idle()
fork_rt.save_state()

print(f"forked: {FORK_RUN}  (parent: {PARENT_RUN})")
print(f"next:   activegraph diff {PARENT_URL} \\")
print(f"            --run-a {PARENT_RUN} --run-b {FORK_RUN}")
```

The snippet does the fork half of fork-and-diff; the diff half is a
CLI command that reads the same SQLite file from outside. The
snippet's final two `print` lines spell out the exact command — copy
it from the terminal output, or use the block below:

```bash
activegraph diff sqlite:////tmp/activegraph_quickstart/quickstart_demo_run.db \
    --run-a quickstart_demo_run \
    --run-b quickstart_cautious
```

You'll see five counts (shared events, parent-only events, fork-only
events, divergent objects, divergent relations) and a list of
objects that exist in both runs with different state. On the
bundled fixtures the diff produces 61 divergent objects and 49
divergent relations — the threshold change fans out further than
you'd guess. The first divergent object is where the threshold
change started producing different work.

What you just did: ran the same starting state through a different
decision, and got a structural comparison of the results.
Hypothesis testing on an agentic system, without losing the parent
run. This is what fork-and-diff means in this framework.

For operator workflows, the same idea can be recorded directly
with `activegraph fork --set <pack>.<setting>=<value>`; the Python
form above is useful when your application already owns runtime
construction. For the conceptual deep-dive on forks (shared
lineage, cache replay, the strict-vs-permissive replay distinction), read
[`concepts/forking`](concepts/forking.md).

## What to read next

You've now run the framework, written your own behavior, persisted
a run, queried it from outside, and forked it. That's the loop;
everything else is depth on one of these primitives.

In rough order of usefulness from here:

- [`concepts/graph`](concepts/graph.md) and
  [`concepts/behaviors`](concepts/behaviors.md) — the mental
  model. Read both in one sitting; together they take about
  fifteen minutes.
- [`cookbook/common-patterns`](cookbook/common-patterns.md) —
  recurring idioms with copy-pasteable code. Eight patterns,
  most of which apply to the kind of agentic systems you'd
  build on this framework.
- [`cookbook/debugging`](cookbook/debugging.md) — the operator-
  facing diagnostic walkthrough. Useful when something goes
  wrong; useful before something goes wrong because it teaches
  you how the framework's audit trail actually works.
- [`reference/cli`](reference/cli.md) — the full CLI surface.
- [`concepts/failure-model`](concepts/failure-model.md) — the
  framework's stance on what counts as a recoverable failure.
  Short, load-bearing, worth reading once.
- [Authoring packs](guides/writing-behaviors.md) and
  [Writing LLM behaviors](guides/writing-llm-behaviors.md) —
  for when you're ready to build something larger than a
  single behavior.

If you're back here on Monday, you found what you were looking for.
