# Operating Active Graph

This document is for **operators**: people responsible for running an
Active Graph runtime as part of a system other people depend on. The
README is for developers writing behaviors. The audience is different
and so is this document.

If you are evaluating Active Graph, read the README first. If you have
a behavior that doesn't run on your machine, the README will help. If
you have a behavior that runs fine on your machine but you need to put
it somewhere a team can rely on it, you are in the right place.

The companion example is [`examples/operate_a_run.py`](https://github.com/yoheinakajima/activegraph/blob/main/examples/operate_a_run.py).
Read it alongside this guide — every CLI command and library call shown
here appears there. If the two ever disagree, the example is right.

---

## The operator surface

The framework treats the boundary between itself and the world it
runs in as a load-bearing contract. Five primitives compose that
surface; together they make a run inspectable, observable, and
recoverable without reading source code:

1. **Postgres** as a second `EventStore`, behind the same protocol as
   SQLite. Same schema, same semantics, different driver.
2. **Structured logging** with a documented JSON schema. One log line
   per event, every line carries `run_id` / `event_id` when applicable.
3. **Metrics**: a three-method `Metrics` protocol with a `NoOpMetrics`
   default plus reference `PrometheusMetrics` and
   `OpenTelemetryMetrics` implementations. The runtime emits a fixed,
   documented set of counters, histograms, and gauges. Custom backends
   (Datadog, statsd, internal collectors) implement the protocol —
   three methods.
4. **`activegraph` CLI**: `inspect`, `replay`, `fork`, `diff`,
   `export-trace`, `migrate`, `pack`, `quickstart`. The CLI is a thin
   wrapper around library APIs; anything it does, programmatic callers
   can do too.
5. **Runtime introspection**: `runtime.status(recent=N)` returns a
   frozen snapshot of queue depth, budget remaining, registered
   behaviors, recent events, and current frame. The CLI's `inspect`
   command sits on top of this primitive.

The operator surface introduced in v0.8 has been extended additively
since: v0.9 added the pack format (and `activegraph pack` for
listing/scaffolding); v1.0 added per-error reference pages
([Reference: Errors](../reference/errors/replay-divergence-error.md))
that every error message links to, plus operator-targeted CLI
follow-on flags (`inspect --event <id>` for divergence triage,
`inspect --behaviors` for replay length mismatches, `inspect
--pack-version` for prompt-hash audits, `migrate --skip-corrupted`
for corrupted-payload recovery, `fork --record` for intentional
re-recording).

What the framework deliberately does **not** ship: a web UI, an HTTP
server, a distributed runtime, real-time subscriptions, multi-model
LLM routing, or streaming LLM responses. The framework is small,
sharp, and operable. Plug in adapters at the boundaries where you
need them.

---

## Persistence: SQLite vs Postgres

SQLite is the default and the right answer for solo work, demos,
ephemeral runs, and most single-machine production cases. The event
log fits in one file, WAL mode gives you crash-safe writes, and you
have no operational dependencies.

Postgres is the right answer when:

- More than one process or machine needs to inspect a run (the operator
  on a laptop, a dashboard, a CLI on a jump box, a CI job).
- You already operate Postgres and want one fewer storage system.
- You want to put the JSONB column behind a read replica or pipe it
  into your data warehouse.

Both stores conform to the same `EventStore` protocol. The runtime,
the CLI, and every library API treat them identically. **Migration is
one-directional and explicit** (see below).

### Connection URLs

Stores are addressed by URL throughout the framework — runtime, CLI,
library APIs. The schemes follow the SQLAlchemy convention:

- `sqlite:///relative/path.db` (**three** slashes — relative path)
- `sqlite:////absolute/path/to/run.db` (**four** slashes — absolute
  path; the leading `/` of the absolute path adds the fourth slash)
- `postgres://user:password@host:port/dbname`
- `postgresql://user:password@host:port/dbname` (same scheme)

A path with no scheme is an error. The framework will not guess.
`activegraph inspect run.db` will fail with a message pointing here.
Use `sqlite:///run.db` (relative) or `sqlite:////tmp/run.db` (absolute).

### Postgres setup

```bash
# Postgres 16 or newer, anywhere reachable from the runtime.
createdb activegraph_prod
# Schema is created lazily on first connection. No migration step.
pip install 'activegraph[postgres]'   # pulls psycopg>=3.1,<4
```

The first time the runtime opens a Postgres URL it issues
`CREATE TABLE IF NOT EXISTS` for `events`, `runs`, and `meta`,
mirroring the SQLite schema with Postgres-native types
(`BIGSERIAL`, `JSONB`, `TIMESTAMPTZ`). Schema version is stored in
`meta` and verified on every open. A schema version mismatch is a
hard error — the runtime refuses to operate on a log it does not
understand.

### Connection management

`PostgresEventStore` accepts:

1. A URL string. The store opens a single dedicated connection.
2. A `psycopg.Connection` you already have. The store does not own
   its lifecycle — you must close it.
3. A `psycopg_pool.ConnectionPool`. The store will `getconn()` /
   `putconn()` around each operation.

For production, pass a pool. The framework does not ship its own pool
because we are not in a position to make tuning decisions for your
deployment.

```python
import psycopg_pool
from activegraph.store.postgres import PostgresEventStore

pool = psycopg_pool.ConnectionPool(
    conninfo="postgres://localhost/activegraph_prod",
    min_size=2,
    max_size=10,
)
store = PostgresEventStore(pool, run_id="run_01J...")
```

### Migration (transaction-per-run)

```bash
activegraph migrate --from sqlite:///path/to/dev.db \
                    --to   postgres://localhost/activegraph_prod
```

Migration semantics:

- Each run in the source migrates in **a single transaction** against
  the destination. If a run fails partway, that run's destination
  state is unchanged (Postgres rolls back).
- Migration is **idempotent** at the event level: writes use
  `INSERT ... ON CONFLICT DO NOTHING` against the `UNIQUE(id, run_id)`
  index. Re-running migration after a partial failure resumes safely.
- Runs are migrated independently. A bad run does not block the others.
- The default migrates **all** runs in the source. To pick one:
  `--run-id <id>`.
- A per-run report is printed at the end (machine-readable with
  `--json`). Each entry is `{run_id, status, events_migrated,
  error?}`. The CLI exit code is non-zero iff any run failed.
- Migration is **not bidirectional**. There is no `sync` mode and no
  rollback. To go back, migrate the other direction.

When migration is the right tool: you are graduating a run from a
laptop SQLite file to a shared Postgres database, or moving a
historical archive between Postgres instances. When it is the wrong
tool: you are trying to keep two stores in sync. Don't.

---

## Structured logging

The framework emits structured logs through stdlib `logging`. **It does
not auto-configure logging on import** — a library that does is hostile
to operators who have already configured their own. By default the
framework logs to `logging.getLogger("activegraph")` and lets your
config handle the rest.

If you want the opinionated setup:

```python
from activegraph.observability import configure_logging
configure_logging(level="INFO", json_output=True)
```

That installs a JSON formatter on the `activegraph` logger hierarchy.
Every log line becomes one JSON object on one line, suitable for
ingestion by Loki, Splunk, BigQuery, Cloud Logging, or any other line-
oriented log aggregator.

### Log schema

Every line is a JSON object. These fields appear when applicable.
Fields that don't apply are **omitted**, not nulled:

| Field             | Type    | When                                             |
|-------------------|---------|--------------------------------------------------|
| `timestamp`       | string  | always (ISO 8601, UTC)                           |
| `level`           | string  | always (`DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`) |
| `logger`          | string  | always (e.g. `activegraph.runtime`)              |
| `message`         | string  | always                                           |
| `run_id`          | string  | any log line associated with a specific run     |
| `event_id`        | string  | log lines about a specific event                |
| `behavior`        | string  | log lines about a specific behavior invocation  |
| `tool`            | string  | log lines about a tool invocation               |
| `model`           | string  | log lines about an LLM call                     |
| `cache_hit`       | bool    | LLM/tool calls; true if served from cache       |
| `cost_usd`        | string  | LLM calls that incurred cost (Decimal-as-string)|
| `latency_seconds` | number  | LLM/tool/behavior calls with measured latency   |
| `reason`          | string  | failure log lines (see reason taxonomy)         |
| `error_type`      | string  | failure log lines                               |
| `error_message`   | string  | failure log lines                               |

The schema is **the operator contract**. Dashboards built against
these field names will keep working across framework versions.
Breaking the schema is a breaking change.

### Level discipline

| Level    | What                                                          |
|----------|---------------------------------------------------------------|
| DEBUG    | View construction, prompt assembly, cache lookup, queue ops   |
| INFO     | Every event emitted, every behavior invoked, every tool call  |
| WARNING  | Budget approaching limits, retries, pattern eval slowness    |
| ERROR    | `behavior.failed` with non-budget reasons                     |
| CRITICAL | Event log inconsistency, schema mismatch, replay divergence  |

INFO is a high-volume stream in any active run. Operators typically
filter at WARNING for production dashboards and crank to DEBUG when
debugging.

There are no `print()` calls anywhere in the framework. The trace
printer (`runtime.print_trace()`) is a developer tool, not an
operator tool — it prints to stdout, does not log, and is independent
of the logging configuration.

### Payload redaction

LLM behaviors include rendered prompts in DEBUG logs. Tool responses
include their full payloads. Goals can contain anything the user
typed. If your environment requires redaction (PII, secrets, customer
data):

```python
def redact(payload: dict) -> dict:
    return {k: ("<redacted>" if k == "email" else v) for k, v in payload.items()}

configure_logging(level="INFO", json_output=True, payload_redactor=redact)
```

The redactor runs on every payload that would otherwise appear in a
log message. It does not affect the event log itself — the source of
truth keeps the original. Redaction is a logging concern.

---

## Metrics

The framework emits metrics through a three-method `Metrics` protocol:

```python
class Metrics(Protocol):
    def counter(self, name: str, tags: dict[str, str], value: float = 1.0) -> None: ...
    def histogram(self, name: str, tags: dict[str, str], value: float) -> None: ...
    def gauge(self, name: str, tags: dict[str, str], value: float) -> None: ...
```

That's it. Three methods. No timers (use a histogram with a latency
value). No summaries (Prometheus-specific). No custom types.

```python
from activegraph.observability import PrometheusMetrics
rt = Runtime(graph, metrics=PrometheusMetrics())
```

```python
from activegraph.observability import OpenTelemetryMetrics
rt = Runtime(graph, metrics=OpenTelemetryMetrics())
```

The default is `NoOpMetrics`, which does nothing. The runtime is
fully functional with no metrics configured.

`PrometheusMetrics` lazy-imports `prometheus_client`. Install with
`pip install 'activegraph[prometheus]'`.

`OpenTelemetryMetrics` lazy-imports `opentelemetry-api` and
`opentelemetry-sdk`. Install with
`pip install 'activegraph[opentelemetry]'`. Counters and histograms
map to native OTel instruments. Synchronous `gauge()` calls are
represented as UpDownCounter deltas so the latest point-in-time value
is preserved.

For Datadog, statsd, or anything else: write a class with the same
three methods.

### Standard metrics

These metrics are emitted by the runtime. Names follow Prometheus
conventions (snake_case with underscores, `_total` for counters,
`_seconds` for duration histograms, `_usd` for cost histograms). They
are the **operator contract**: dashboards built against these names
keep working across framework versions.

| Name                                            | Type      | Tags                  |
|-------------------------------------------------|-----------|-----------------------|
| `activegraph_events_emitted_total`              | counter   | `event_type`          |
| `activegraph_behaviors_invoked_total`           | counter   | `behavior`            |
| `activegraph_behaviors_failed_total`            | counter   | `behavior`, `reason`  |
| `activegraph_behaviors_duration_seconds`        | histogram | `behavior`            |
| `activegraph_llm_calls_total`                   | counter   | `model`               |
| `activegraph_llm_cache_hits_total`              | counter   | `model`               |
| `activegraph_llm_failed_total`                  | counter   | `model`, `reason`     |
| `activegraph_llm_tokens_in`                     | histogram | `model`               |
| `activegraph_llm_tokens_out`                    | histogram | `model`               |
| `activegraph_llm_cost_usd`                      | histogram | `model`               |
| `activegraph_tools_calls_total`                 | counter   | `tool`                |
| `activegraph_tools_cache_hits_total`            | counter   | `tool`                |
| `activegraph_tools_failed_total`                | counter   | `tool`, `reason`      |
| `activegraph_tools_duration_seconds`            | histogram | `tool`                |
| `activegraph_queue_depth`                       | gauge     | (none)                |
| `activegraph_budget_cost_remaining_usd`         | gauge     | `run_id`              |
| `activegraph_budget_events_remaining`           | gauge     | `run_id`              |
| `activegraph_patterns_evaluated_total`          | counter   | (none)                |
| `activegraph_patterns_evaluation_duration_seconds` | histogram | (none)            |
| `activegraph_replay_divergence_detected_total`  | counter   | `reason`              |

**Adding a metric is a public API change.** The list is documented and
test-pinned. New metrics get added in named releases, not silently.

### Cardinality rule (locked)

> `run_id` MAY appear as a tag on **gauges of active state** (where
> cardinality is bounded by the number of concurrently active runs).
> `run_id` MUST NOT appear as a tag on **counters or histograms**.

This rule prevents the most common Prometheus operational disaster:
unbounded cardinality from per-run labels accumulating forever. The
budget gauges are the only exception, and they live only for the
duration of a run.

The conformance suite enforces this rule against the standard metric
list. If you implement a custom `Metrics` backend, do the same.

### Tag conventions

Standard tag keys are: `event_type`, `behavior`, `tool`, `model`,
`reason`, `run_id` (gauges only). Boolean tags (`cache_hit` is
modeled as a separate counter rather than a tag — see
`activegraph_llm_cache_hits_total`). If your backend distinguishes
booleans from strings, you won't have to special-case.

Custom tags beyond the standard set are fine but may explode
cardinality. The cardinality rule above is your guide.

---

## Runtime introspection

`runtime.status(recent: int = 20)` returns a `RuntimeStatus` — a
frozen dataclass. Calling it is cheap: no graph traversal, no event
log scan. It is safe to call from any thread.

```python
status = rt.status()
print(status.run_id, status.state, status.queue_depth)
for ev in status.recent_events:
    print(ev.id, ev.type)
```

Shape:

```python
@dataclass(frozen=True)
class RuntimeStatus:
    run_id: str
    state: Literal["idle", "running", "stopped", "exhausted"]
    queue_depth: int
    events_processed: int
    budget: BudgetSnapshot
    frame: FrameSnapshot | None
    registered_behaviors: list[BehaviorInfo]
    recent_events: list[EventSummary]
```

`recent_events` length is `recent` (default 20). The CLI's
`inspect --tail N` passes through to this argument.

There is **no `last_error` field**. Errors are events. Filter
`recent_events` for type `behavior.failed`, or query the event store
directly for a window-independent view.

---

## CLI

The `activegraph` binary is a thin wrapper around library APIs. Every
subcommand calls into Python; nothing is implemented in the CLI itself.
A programmatic user can do everything the CLI does.

```
activegraph inspect <url> [--run-id <id>] [--tail N] [--json]
                          [--event <id> | --behaviors | --pack-version]
activegraph replay <url> --run-id <id> [--json]
activegraph fork <url> --run-id <id> --at-event <id> --label <label>
                       [--to <url>] [--record] [--json]
activegraph diff <url> --run-a <id> --run-b <id> [--json]
activegraph export-trace <url> --run-id <id> [--format text|jsonl] [-o PATH]
activegraph migrate --from <url> --to <url> [--run-id <id>] [--skip-corrupted] [--json]
activegraph pack list
activegraph pack new <name>
```

`--event`, `--behaviors`, and `--pack-version` on `inspect` are
mutually-exclusive selectors that focus the output on one section
instead of the full snapshot. See the
[CLI reference](../reference/cli.md) for the full surface; the
[debugging cookbook](../cookbook/debugging.md) walks through
diagnostic workflows that build on these flags.

### Exit codes

| Code | Meaning                                                   |
|------|-----------------------------------------------------------|
| 0    | Success                                                   |
| 1    | Generic error                                             |
| 2    | Usage error (bad arguments, missing options)              |
| 3    | Not found (run id does not exist, file does not exist)    |
| 4    | Corruption (schema version mismatch, event log inconsistency) |
| 5    | Divergence (replay-strict failure)                        |

These are documented contract. Wrap the CLI in shell scripts or CI
jobs against these codes.

### `inspect`

Default-mode prints a human-readable snapshot of the most recent run
in the store (or `--run-id <id>` for a specific run). `--json` prints
the same data as a single JSON object — the same shape as the
`RuntimeStatus` returned by the library.

```bash
activegraph inspect sqlite:///run.db
activegraph inspect postgres://localhost/agdb --run-id run_01J... --tail 50 --json
```

Three v1.0 selectors focus the output on one section instead of the
full snapshot:

- `--event <id>` prints the full payload of one event. Used when an
  error message names an event id — `ReplayDivergenceError` always
  does.
- `--behaviors` prints only the registered-behaviors section. Used
  when diagnosing a replay length mismatch: compare which behaviors
  fire now against which fired in the recorded run.
- `--pack-version` prints every `pack.loaded` event in the run with
  prompt content-hash summaries. Used to confirm the pack version (or
  prompt drift) responsible for a divergence.

The three are mutually exclusive — they're selectors, not filters.

### `replay`

Opens the store, rebuilds the graph by replaying the log (no behaviors
fire), and prints a summary: event count, object count, relation count.
Useful for sanity-checking a run after a crash or after a migration.

```bash
activegraph replay sqlite:///run.db --run-id run_01J...
```

### `fork`

Creates a new run by copying events from `--run-id` up to and
including `--at-event`. `--to <url>` defaults to the source store;
pass a different URL to fork across stores. Prints the new run id
and the number of events copied.

```bash
activegraph fork sqlite:///run.db \
  --run-id run_01J... \
  --at-event evt_42 \
  --label investigate-alternative-thesis
```

The forked run is dormant — nothing is running it. To continue from
the fork point, load it with `Runtime.load(url, run_id=<new_run_id>)`
and call `run_until_idle()`.

Pass `--record` to mark the fork as an intentional re-recording
(used after a `ReplayDivergenceError` when the divergence was
intentional). The flag appends `-recording` to the label and prints
follow-on guidance. The fork-with-pack-setting-override workflow
(the canonical recipe behind `--set`, landing in v1.1) is documented
under [Cookbook: common patterns — Fork with a pack-setting
override](../cookbook/common-patterns.md#fork-with-a-pack-setting-override-v10-python-api).

### `diff`

Structural diff between two runs in the same store. Prints shared and
divergent event counts, divergent objects, and divergent relations.
The library equivalent is `parent.diff(other)`.

```bash
activegraph diff sqlite:///run.db --run-a run_a --run-b run_b
```

### `export-trace`

Dump a run's event log to a file or stdout.

- `--format text` (default) — the human-readable trace printer output.
- `--format jsonl` — one JSON event per line. Suitable for ingestion
  by any log aggregator.

```bash
activegraph export-trace sqlite:///run.db --run-id run_01J... --format jsonl -o run.jsonl
```

### `migrate`

See [Migration](#migration-transaction-per-run) above. The v1.0
`--skip-corrupted` flag lets a migration recover the readable subset
when source events have corrupted JSON payloads instead of failing
the whole run; the skipped event ids appear in the per-run report's
`skipped_events`. The resulting destination run is partial — the
operator is on notice.

---

## Runbook

### A run is stuck

Call `runtime.status()` (or `activegraph inspect`). Check `state`:

- `idle` — the queue is empty, the budget is fine, the run is waiting
  for new input. This is the normal terminal state for a goal-driven
  run. Not stuck.
- `exhausted` — the run hit a budget limit. The `budget` field shows
  which dimension. Raise the limit or accept the partial result.
- `running` — the run is actually working. `queue_depth` should be
  decreasing. If it's increasing or steady, a behavior is producing
  events faster than the runtime processes them. Check the trace.
- `stopped` — the runtime is loaded but no `run_until_idle()` call is
  in progress. Call it.

### A run is over budget

`runtime.status().budget` shows used vs. limits across dimensions
(events, behavior calls, LLM calls, tool calls, cost, depth, seconds).
The `activegraph_budget_*` gauges expose the same data. Set up an
alert on `cost_remaining_usd < threshold` to catch runs before they
exhaust.

To resume a budget-exhausted run with a higher limit:

```python
rt = Runtime.load(url, run_id=stuck_run_id, budget={"max_cost_usd": "10.0"})
rt.run_until_idle()
```

### Replay diverges

You called `Runtime.load(..., replay_strict=True)` and got
`ReplayDivergenceError`. The runtime's re-execution of recorded
behaviors produced different events than the log. Causes, in order
of likelihood:

1. A behavior reads from a non-deterministic source (clock,
   `random`, network) it didn't read on the original run.
2. A behavior depends on a value (an LLM response, a tool result)
   that was cached on the original run but no longer is.
3. The framework version changed and an event payload shape changed.
   Check the v0.8 schema-mismatch guard caught this; if not, file an
   issue.

The error pins the offending event id. Look at it. The fix is in your
behavior, not the framework.

### Postgres connection saturated

You are passing a `psycopg.Connection` per request. Use a
`psycopg_pool.ConnectionPool` and pass that instead. The framework
calls `getconn()` / `putconn()` around each operation.

### Trace lines do not appear in my log aggregator

`runtime.print_trace()` prints to stdout. It is not a log. To get
events into your aggregator:

- Use `activegraph export-trace --format jsonl` ad-hoc.
- Or write a behavior that subscribes to the event types of interest
  and emits a structured log record. The framework's logging will
  carry it through.

---

## Capacity planning

These are reference numbers from a single Postgres 16 instance on
commodity hardware. They are not benchmarks; they are the order of
magnitude an operator should expect.

- **Event log writes**: a single connection sustains a few thousand
  events per second. With a pool of 10 and writes spread across runs,
  tens of thousands per second is achievable.
- **Event log reads**: replay of a 100k-event run on a warm cache
  takes single-digit seconds. Plan for that on a cold start.
- **Storage**: ~1-2 KB per event in JSONB form (including indexes).
  A million-event run is around 1.5 GB.
- **Run concurrency**: bounded by your connection pool size, not by
  the framework. The runtime itself is single-threaded.

If your runs are big enough that any of this is a concern, the
framework's single-process design is the next constraint you will hit.
That is a v1.0+ conversation.

---

## What this guide is not

This guide will not tell you how to set up Postgres, configure
Prometheus, or operate Grafana. Those are well-documented elsewhere
and the framework's integration with them is intentionally generic.

This guide will not recommend SLOs, alerts, or dashboards. Your
business context determines those. The metrics list above is the
foundation; what you build on top is yours.

This guide will not stay current with every release. The locked
contracts — log schema, metric names, exit codes, status shape — will.
Examples may drift; the contracts will not.
