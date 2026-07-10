# Active Graph

> The graph is the world. Behaviors are physics. The trace is the proof.

An event-sourced reactive graph runtime for long-running, auditable,
agentic systems. Behaviors react to a shared graph instead of talking
to each other. Every change is traceable. Every run is resumable,
forkable, and diff-able from its event log.

If chat-based agents are a group conversation, Active Graph is a
shared workspace where everyone can see what changed, who changed
it, and why.

## Try it in 30 seconds

```bash
pip install activegraph
activegraph quickstart
```

The bundled Diligence pack runs against recorded fixtures: no API
key, no configuration, byte-deterministic output. You see what the
framework does before you read about how it does it.

Then walk the 10-minute tutorial:

```bash
activegraph quickstart --interactive
```

It scaffolds a behavior, runs it against the same fixtures, and ends
with the fork-and-diff workflow — the framework's most differentiated
capability.

## Install

```bash
pip install activegraph                    # core runtime + SQLite store + Diligence pack
pip install "activegraph[llm]"             # Anthropic + OpenAI providers
pip install "activegraph[anthropic]"       # Anthropic provider only
pip install "activegraph[openai]"          # OpenAI provider only (+ tiktoken)
pip install "activegraph[postgres]"        # Postgres-backed event store
pip install "activegraph[prometheus]"      # Prometheus metrics
pip install "activegraph[opentelemetry]"   # OpenTelemetry metrics
pip install "activegraph[all]"             # everything
```

Both LLM providers expose the same `LLMProvider` Protocol surface;
swap one for the other without touching `@llm_behavior` definitions.
The [LLM providers reference](https://docs.activegraph.ai/reference/llm-providers/)
covers the side-by-side surface, including tool use and provider-specific
token counting.

Python 3.11+. Two hard dependencies (`click` for the CLI, `pydantic`
for the pack format); persistence backends and provider integrations
are opt-in extras.

## What you get

- **Event-sourced graph runtime.** Objects + typed relations + an
  append-only event log. Every mutation is an event; the trace is the
  audit trail.
- **Reactive behaviors as first-class.** Function, class, LLM-backed,
  or attached to typed edges (the relation-behavior primitive — edges
  with logic). Subscriptions are event type + predicate + a Cypher
  subset for graph-shape patterns.
- **Fork-and-diff.** Branch any run at any event into an independent
  fork, configure it differently, and structurally diff the result
  against the parent. Cache replay means the shared prefix doesn't
  re-execute (no new LLM calls). Most agent frameworks can't do this.
- **Packs.** A pack bundles object types, behaviors, tools, prompts,
  and policies for a specific domain. The bundled
  [Diligence pack](activegraph/packs/diligence) is the reference:
  8 object types, 7 behaviors, 3 tools, recorded fixtures.
- **Isolated event export.** `EventSink` streams accepted live events
  through a bounded per-sink worker without putting adapter I/O on the
  runtime hot path. `JSONLEventSink` is the first-party adapter; drops,
  queue depth, and failures are explicit in status and metrics, and
  normal replay never redelivers history.
- **Per-error reference pages.** Every error message ends with a
  `More:` link to a page that explains when it fires, why, and how to
  fix it. Catalog at [docs.activegraph.ai/reference/errors](https://docs.activegraph.ai/reference/errors/).

## Concepts at a glance

The framework's twelve primitives, in roughly the order you meet them
when reading a trace. Each links to its concept page on the doc site;
read those when you want depth on one piece.

- **Graph** — objects and typed relations forming the world the
  framework reasons about. The graph is a projection of the event log;
  every mutation is an event. [→ concepts/graph](https://docs.activegraph.ai/concepts/graph/)
- **Events** — the append-only history. Every behavior fires in
  response to events and produces more events; the trace is the
  ordered log of all of them. [→ concepts/events](https://docs.activegraph.ai/concepts/events/)
- **Behaviors** — the unit of reactive code. Function, class, or
  LLM-backed; declares what events it subscribes to and what it
  produces. The determinism contract is per-behavior. [→ concepts/behaviors](https://docs.activegraph.ai/concepts/behaviors/)
- **Relations** — typed edges between objects, with their own
  behaviors. The relation-behavior primitive — coordination logic on
  the edge, not on either endpoint — is uncommon in other agent
  frameworks. [→ concepts/relations](https://docs.activegraph.ai/concepts/relations/)
- **Patches** — proposed mutations with optimistic concurrency.
  Behaviors propose patches; the runtime applies or rejects them;
  rejections are events in their own right. [→ concepts/patches](https://docs.activegraph.ai/concepts/patches/)
- **Views** — scoped reads of the graph for behavior context. Type
  filters, depth filters, recent-event windows. Views are how
  pattern-driven behaviors see only what they need to. [→ concepts/views](https://docs.activegraph.ai/concepts/views/)
- **Frames** — bounded contexts for a run. Goal, constraints, budget,
  and the registered behaviors for this frame. A run can have one
  frame or many. [→ concepts/frames](https://docs.activegraph.ai/concepts/frames/)
- **Policies** — approval and gating for behavior capabilities. Which
  behaviors can call which tools, which mutations require human
  approval, what the runtime refuses. [→ concepts/policies](https://docs.activegraph.ai/concepts/policies/)
- **Patterns** — the Cypher subset for pattern subscriptions. Beyond
  event-type + predicate, behaviors can subscribe to graph shapes
  (claim-cited-by-evidence, task-blocks-task, …) with `NOT EXISTS`
  and temporal predicates. [→ concepts/patterns](https://docs.activegraph.ai/concepts/patterns/)
- **Replay** — re-execute a run from its event log. Strict mode
  re-fires every behavior and fails on divergence; permissive mode
  reconstructs state without re-firing. The LLM replay cache is what
  makes fork cheap. [→ concepts/replay](https://docs.activegraph.ai/concepts/replay/)
- **Forking** — branch any run at any event into an independent
  fork; structurally diff the fork against the parent. The framework's
  mechanism for hypothesis testing on agentic systems. [→ concepts/forking](https://docs.activegraph.ai/concepts/forking/)
- **Failure model** — a behavior failure is a `behavior.failed`
  event, not an exception. The audit trail captures failures as
  first-class history. Exceptions live at runtime entry points only.
  [→ concepts/failure-model](https://docs.activegraph.ai/concepts/failure-model/)

## The type system at a glance

What's fixed and what's yours. The framework speaks a small vocabulary
of event types — the verbs of what happened. The nouns and edges of
your domain are strings you choose.

**Event types — fixed.** The runtime emits these; the trace, replay,
and observability surfaces all key off them.

- **Lifecycle:** `goal.created`, `runtime.idle`, `runtime.budget_exhausted`
- **Graph:** `object.created`, `object.removed`, `relation.created`, `relation.removed`
- **Behaviors:** `behavior.scheduled`, `behavior.started`, `behavior.completed`, `behavior.failed`, `relation_behavior.started`
- **Patterns:** `pattern.matched`
- **LLM:** `llm.requested`, `llm.responded`
- **Tools:** `tool.requested`, `tool.responded`
- **Patches:** `patch.proposed`, `patch.applied`, `patch.rejected`
- **Approvals:** `approval.proposed`, `approval.granted`
- **Packs:** `pack.loaded`

Behaviors can also emit custom event types — any string. The
`task.completed` signal in the example below is one: an
application-level event the `unblock` relation behavior subscribes
to, flowing through the same log alongside the framework's own.

**Object and relation types — yours.** Any string works. There is no
central schema, no registration step, no enum to extend.
`graph.add_object("claim", {...})` creates a `claim` because you said
`claim`; `graph.add_relation(a, b, "depends_on")` makes a
`depends_on` edge because you said `depends_on`. Packs can attach
optional Pydantic validation per type; absent a pack, the data passes
through unchanged. The Diligence pack's object types (`claim`,
`evidence`, `risk`, `memo`, …) and relation types (`supports`,
`contradicts`, `references`, …) are an example ontology, not framework
base types — you design your own for your domain.

**Patch states — fixed.** `proposed` → `applied` | `rejected`. Three
values, two of them terminal.

The full model — composition, ontology design guidance, the Diligence
pack as a worked example — lives at
[→ concepts/type-system](https://docs.activegraph.ai/concepts/type-system/).

## A small example

The relation-behavior primitive — coordination logic on the edge,
not on either endpoint:

```python
from activegraph import Graph, Runtime, behavior, relation_behavior

graph = Graph()
runtime = Runtime(graph, budget={"max_events": 200, "max_seconds": 60})

@behavior(name="planner", on=["goal.created"])
def planner(event, graph, ctx):
    research = graph.add_object("task", {"title": "Research", "status": "open"})
    memo = graph.add_object("task", {"title": "Draft memo", "status": "blocked"})
    graph.add_relation(research.id, memo.id, "depends_on")

@behavior(name="researcher", on=["object.created"], where={"object.type": "task"})
def researcher(event, graph, ctx):
    task = event.payload["object"]
    if task["data"]["status"] != "open" or "Research" not in task["data"]["title"]:
        return
    graph.add_object("claim", {"text": "Market early but growing.", "confidence": 0.7})
    graph.emit("task.completed", {"task_id": task["id"]})

@relation_behavior(name="unblock", relation_type="depends_on", on=["task.completed"])
def unblock(relation, event, graph, ctx):
    if event.payload["task_id"] == relation.source:
        graph.patch_object(relation.target, {"status": "open"})

runtime.run_goal("Evaluate this startup idea")
runtime.print_trace()
```

The `unblock` relation behavior fires only for events touching one of
its edge endpoints. The conceptual deep-dive on edges-with-logic is
in [`docs/concepts/relations.md`](https://docs.activegraph.ai/concepts/relations/).

## Documentation

- **[docs.activegraph.ai](https://docs.activegraph.ai/)** — full doc site:
  concepts, guides, cookbook, CLI reference, API reference, the
  per-error catalog.
- **[10-minute tutorial](https://docs.activegraph.ai/quickstart/)** — install
  to a working custom behavior, including fork-and-diff.
- **AI coding assistants** — the docs are machine-readable at
  [docs.activegraph.ai/llms.txt](https://docs.activegraph.ai/llms.txt)
  (structured index) and
  [docs.activegraph.ai/llms-full.txt](https://docs.activegraph.ai/llms-full.txt)
  (concatenated full content), generated from the same source markdown
  as the rendered site. Built for AI agents evaluating the framework
  via Claude Code, Cursor, Replit, and similar tooling.
- **[CHANGELOG.md](CHANGELOG.md)** — every release, with per-version
  migration notes.
- **[CONTRACT.md](CONTRACT.md)** — locked design decisions, version
  by version. Useful when you want to know *why* something is the way
  it is.
- **[examples/](examples)** — runnable end-to-end demos:
  [`diligence_real_run.py`](examples/diligence_real_run.py),
  [`resume_and_fork.py`](examples/resume_and_fork.py),
  [`llm_claim_extraction.py`](examples/llm_claim_extraction.py),
  [`diligence_with_tools.py`](examples/diligence_with_tools.py),
  [`operate_a_run.py`](examples/operate_a_run.py),
  [`babyagi.py`](examples/babyagi.py) — BabyAGI's autonomous agent loop,
  rebuilt as three reactive behaviors over a shared graph.

## What this is not

- Not a chat framework. If your problem fits in one conversation, use
  a chat framework.
- Not a workflow engine. Workflows model control flow. This models
  world state.
- Not a rules engine, exactly. Rules engines forward-chain over
  facts. This event-sources over a graph and supports LLM behaviors
  as first-class.
- Not a production graph database. The event log lives in SQLite
  (default) or Postgres behind the `EventStore` protocol; the
  materialized graph lives behind the `GraphStore` protocol —
  in-memory by default, or [FalkorDB](https://docs.activegraph.ai/guides/using-falkordb/)
  for a real, traversable graph backend. For a different
  high-throughput store, plug one in behind either protocol.
- Not magic. Bad behaviors produce bad graphs. The runtime makes the
  badness inspectable, not absent.

## Status

**v1.7.1 (stable)** (2026-07). v1.0 shipped in May 2026 after a
three-rc external user-test gate per
[CONTRACT v1.0 #C4](CONTRACT.md#v10-c4-v10-ships-as-v10-rc1-first-time-user-gate-is-owned-externally);
the v1.1–v1.7 line followed, driven by a downstream self-modification
stack (a pack library and a governed fork→test→promote assistant).
See [CHANGELOG.md](CHANGELOG.md) for the full v0 → v1.7 history and
per-version migration notes.

Major shipped milestones:

- **v1.7** — subprocess trial-child hardening from downstream soaks:
  `extra_packs` for cross-pack interaction trials, the child's stderr
  captured into the trial report, code discovery made an explicit
  computed-`PYTHONPATH` channel (the env allow-list stays closed),
  `sandbox.preflight()`, and portable resource limits — `RLIMIT_AS`
  enforced on Linux, announced-off (not crashed) on macOS.
- **v1.6** — loader-side manifest validation as a warning tier at
  `load_pack` (structured, once per pack, never an error before 2.0);
  the fork-tail-removal promote invariant pinned as contract.
- **v1.5** — subprocess fork-trial isolation (`run_forked_trial`: a
  fresh-interpreter child against a fork, artifacts pinned by bundle
  hash) and compaction phase 1 (snapshot + archive tier + the
  retention pin set, never deletion).
- **v1.4** — the pack-manifest validator (`load_manifest`,
  `verify_surface`, content + bundle hashes), declarative
  `Pack.capabilities`, and `disable_pack` deregistration.
- **v1.8** — isolated `EventSink` delivery; runtime-owned embedding
  record/replay; fail-closed direct `web_fetch`; deterministic wall-stop
  replay; and the provider-neutral `TrialExecutor` seam with the existing
  local subprocess as its honest, non-security-sandbox default.
- **v1.3** — `promote`: apply a fork's net structural delta back to
  its parent, fail-closed on conflicts (the fork→test→promote loop);
  provider-compatibility hardening; traceback/DX surfacing; the
  `EmbeddingProvider` seam.
- **v1.2** — the `GraphStore` seam: the materialized projection
  becomes pluggable, with `FalkorDBGraphStore` (native edges, Cypher
  query push-down) as the first external backend, contributed by
  [@dudizimber](https://github.com/dudizimber); the test suite
  becomes a CI gate.
- **v1.1** — bounded LLM retries for transient provider failures,
  `inspect --memo` / `inspect --search`, `fork --set`, OpenAI
  tool-shape parity, `OpenTelemetryMetrics`, and the
  spec-vs-impl drift gates.
- **v1.0** — error hierarchy rewrite with per-error reference
  pages, doc site at [docs.activegraph.ai](https://docs.activegraph.ai/),
  `activegraph quickstart` command, mypy `--strict` and docstring
  coverage CI gates, wheel-completeness and deploy-verification CI
  gates.
- **v0.9** — pack format and the Diligence reference pack (8 object
  types, 7 behaviors, 3 tools, recorded fixtures).
- **v0.8** — operator surface: structured logging, Prometheus
  metrics, `runtime.status()`, full `activegraph` CLI,
  `PostgresEventStore`.
- **v0.7** — `@tool` decorator, Cypher-subset pattern subscriptions,
  temporal predicates.
- **v0.6** — `@llm_behavior` with structured output, frame-aware
  prompt construction, cost accounting.
- **v0.5** — full event-log persistence, save/load across processes,
  fork from any historical event, structural diff between runs.
- **v0** — core runtime: graph, behaviors, relation behaviors,
  patches with optimistic concurrency, views, frames, policies,
  budgets, the trace.

Roadmap items for the current cycle are tracked in
[ROADMAP.md](ROADMAP.md); unscheduled candidates live in
[FUTURE_IDEAS.md](FUTURE_IDEAS.md).

## License

Active Graph is licensed under the Apache License 2.0. See
[LICENSE](LICENSE) for the full text and [NOTICE](NOTICE) for
the attribution that downstream redistributors must preserve.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the current contribution
policy. The framework is in its early public phase: issues are open,
documentation PRs are welcome, code PRs are maintainer-only with an
issue-first discussion gate (the abstractions are still settling).
The policy relaxes as the contributor community matures.

**Test discipline:** tests must remain deterministic. No live network
calls in CI. LLM and tool tests use recorded fixtures
(`RecordedLLMProvider`, `RecordedToolProvider`). If a contribution
adds a test that would only pass with a live API key or live HTTP,
it cannot land.

---

The graph is the world. Behaviors are physics. The trace is the proof.
