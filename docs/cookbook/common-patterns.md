# Common patterns

Recurring idioms with copy-pasteable code. Each pattern is one
sub-section: the code, a short rationale, and a pointer at the
concept page that owns the underlying primitive.

If you're writing a new behavior and one of these patterns fits,
use it — the patterns are how the framework's primitives compose
to solve the everyday shapes that come up in agentic systems. If
none of them fit, you're probably reaching for a primitive in a
new way, and the [concepts](../concepts/graph.md) section is the
right next stop.

## Retry behaviors on transient failures

The canonical pattern for handling terminal LLM or tool failures
that are non-deterministic (network errors, rate limits, timeouts).
LLM provider-call transients (`llm.network_error`,
`llm.rate_limited`) already get a small bounded retry loop inside
the runtime; only the exhausted terminal failure reaches
`behavior.failed`. `behavior.failed` events carry the original
`reason` code, so a retry behavior can subscribe to them with a
`where=` filter on the codes that warrant a higher-level retry:

```python
from activegraph import behavior

@behavior(
    name="retry_transient",
    on=["behavior.failed"],
    where={
        "reason": [
            "llm.network_error",
            "llm.rate_limited",
            "tool.timeout",
            "tool.network_error",
        ],
    },
)
def retry_transient(event, graph, ctx):
    attempt = (event.payload.get("attempt") or 0) + 1
    if attempt > 3:
        return
    graph.emit("retry.requested", {
        "for_event": event.payload["triggering_event_id"],
        "attempt": attempt,
        "behavior": event.payload["behavior"],
    })
```

Retries are first-class graph citizens (CONTRACT v0.6 #13). Runtime
LLM attempts appear as `llm.requested` / `llm.responded(error=...)`
pairs; terminal retry behavior events appear in the trace and can be
forked from. Per-behavior caps live in the behavior body; the
framework's built-in provider-call retry is intentionally small. See
[`failure-model`](../concepts/failure-model.md) for why
`behavior.failed` is an event rather than an exception that
escapes to your code.

## Fork-and-diff to compare alternative hypotheses

When you want to know "what would happen if I changed this
setting," fork from a point before the setting takes effect, run
the fork with the override, and diff.

```bash
# Find the event before the setting matters (usually the goal
# event or a pack.loaded event):
activegraph inspect <store> --run-id <run> --tail 50

# Fork with the override (v1.1):
activegraph fork <store> --run-id <run> --at-event <evt> \
    --label cautious \
    --set diligence.confidence_threshold_for_review=0.9 \
    --record

# Diff the two runs:
activegraph diff <store> --run-a <parent> --run-b <fork>
```

The diff prints shared events, parent-only events, fork-only
events, and divergent objects. The first divergent object tells
you where the override started producing different work. See
[`forking`](../concepts/forking.md) for the cutoff semantics and
the `--set` rules (pack-settings-only, fail-loud-on-typo).

## Fork with a pack-setting override (v1.0 — Python API)

Use the Python API when your application already owns runtime
construction or needs to compute settings objects directly. It does
the same conceptual work as `activegraph fork --set`: copies the
parent's events up to the fork point, then resumes execution under
different pack settings.

```python
import sqlite3

from activegraph import Runtime
from activegraph.packs.diligence import DiligenceSettings, pack as diligence_pack
from activegraph.packs.diligence.fixtures import (
    RecordedDiligenceProvider,
    THREE_COMPANIES,
)
from activegraph.store import open_store

PARENT_URL = "sqlite:////tmp/activegraph_quickstart/quickstart_demo_run.db"
PARENT_RUN = "quickstart_demo_run"
FORK_RUN = "quickstart_cautious_fork"

# Remove a prior copy if you are re-running this snippet.
with sqlite3.connect("/tmp/activegraph_quickstart/quickstart_demo_run.db") as _conn:
    _conn.execute("DELETE FROM events WHERE run_id = ?", (FORK_RUN,))
    _conn.execute("DELETE FROM runs WHERE run_id = ?", (FORK_RUN,))

# Find a fork point — typically the goal.created event for the
# company you want to re-run with the override.
parent_store = open_store(PARENT_URL, run_id=PARENT_RUN)
fork_at = next(
    e.id for e in parent_store.iter_events()
    if e.type == "goal.created"
)

# Copy parent events up to the fork point into the new run.
from activegraph.store.sqlite import SQLiteEventStore
SQLiteEventStore.fork_run(
    "/tmp/activegraph_quickstart/quickstart_demo_run.db",
    parent_run_id=PARENT_RUN,
    new_run_id=FORK_RUN,
    at_event_id=fork_at,
    label="cautious",
    created_at="2026-01-01T00:00:00Z",
)

# Load the fork and run it with the override settings.
fork_rt = Runtime.load(
    PARENT_URL,
    run_id=FORK_RUN,
    llm_provider=RecordedDiligenceProvider(companies=THREE_COMPANIES),
)
fork_rt.load_pack(
    diligence_pack,
    settings=DiligenceSettings(
        llm_model="claude-sonnet-4-5",
        confidence_threshold_for_review=0.9,  # ← the override (was 0.7)
    ),
)
fork_rt.run_until_idle()
fork_rt.save_state()
```

Diff the two runs from the CLI as usual:

```bash
activegraph diff sqlite:////tmp/activegraph_quickstart/quickstart_demo_run.db \
    --run-a quickstart_demo_run \
    --run-b quickstart_cautious_fork
```

The diff shows the structural difference produced by the
threshold change. For operator workflows, prefer the CLI form above;
for embedded application code, keep the Python form.

## Pattern subscriptions for cross-object reactivity

When a behavior should fire only when a specific structural
relationship exists in the graph, use a pattern subscription
instead of an event-type filter:

```python
@behavior(
    name="risk_escalator",
    pattern="(c:claim)-[:supports]->(e:evidence) WHERE c.confidence > 0.7",
)
def risk_escalator(event, graph, ctx):
    for match in ctx.matches:
        claim = match.bindings["c"]
        evidence = match.bindings["e"]
        ...
```

The pattern matcher reads the full graph; the behavior body
operates on `ctx.matches`, one entry per binding combination that
satisfies the pattern. See [`patterns`](../concepts/patterns.md)
for the supported Cypher subset and what's deliberately refused.

## `ctx.propose_object` for policy-gated writes

When an object should require approval before landing — memos,
risks, anything an operator should review — use
`ctx.propose_object` instead of `graph.add_object`:

```python
@behavior(name="memo_synthesizer", on=["claims.complete"])
def memo_synthesizer(event, graph, ctx):
    ...
    ctx.propose_object(
        "memo",
        data={"title": "Diligence memo", "body": "..."},
        reason="diligence run complete",
    )
```

The proposal lands as `approval.proposed`. If the pack's
auto-approve setting is on, the framework approves immediately
and the object lands. If off, the proposal sits until
`rt.approve(id)` is called. See
[`policies`](../concepts/policies.md) for the full lifecycle.

The operator-side enumeration pattern:

```python
for pa in rt.pending_approvals():
    print(pa.id, pa.kind, pa.object_type, pa.reason)
    rt.approve(pa.id, approved_by="reviewer")
```

## Scoped views for cost-efficient LLM behaviors

When an LLM behavior only needs to read a few neighbors of the
triggering object, narrow the view to bound prompt size and cost:

```python
@behavior(
    name="claim_summarizer",
    on=["object.created"],
    where={"object.type": "claim"},
    view={"around": "event.payload.object.id", "depth": 1},
)
def claim_summarizer(event, graph, ctx):
    claim = ctx.view.get_object(event.payload["object"]["id"])
    for neighbor in ctx.view.objects():
        ...
```

`around=` + `depth=` scope what `ctx.view` returns. The prompt
assembler serializes the view; smaller view, smaller prompt.
LLM behaviors that pass the full graph to the prompt assembler are
the canonical source of unbounded cost growth in agentic systems
— scoping is the answer. See [`views`](../concepts/views.md).

## `@relation_behavior` for coordination logic between endpoints

When the logic semantically belongs to a relationship, not to
either endpoint, use `@relation_behavior`:

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

The behavior fires once per matching edge. See
[`relations`](../concepts/relations.md) for the decision rule
between relation behaviors and regular behaviors.

## Emit a custom event for cross-behavior signaling

When two behaviors need to coordinate but neither owns the
trigger, emit a custom event from one and subscribe from the
other:

```python
@behavior(name="produce", on=["object.created"])
def produce(event, graph, ctx):
    ...
    graph.emit("memo.ready_for_review", {"memo_id": memo.id})


@behavior(name="review", on=["memo.ready_for_review"])
def review(event, graph, ctx):
    ...
```

Custom event names use dot-namespace convention
(`my.feature.event`); behaviors subscribing by name pick them up.
The events land in the trace alongside framework events. See
[`events`](../concepts/events.md).

## Save state across processes

When a long-running goal needs to survive process restart, attach
a SQLite store and call `save_state` at quiescence:

```python
rt = Runtime(graph, persist_to="/path/to/run.db")
rt.run_goal("...")
rt.save_state()
```

To resume later:

```python
rt = Runtime.load("sqlite:////path/to/run.db", run_id=rt.run_id)
rt.run_until_idle()
```

Restoring loads the event log and replays it. Behaviors fire fresh
after the replay; the framework treats them as a continuation of
the original run. See
[Operating in production](../guides/operating-in-production.md)
for the full operator-facing surface.
