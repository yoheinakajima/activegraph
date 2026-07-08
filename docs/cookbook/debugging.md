# Debugging

The framework's audit trail is the same artifact whether you're
investigating a production bug or developing a new behavior. The
event log records what happened; the trace renders it for human
scanning; `activegraph inspect` slices it for narrow questions;
the fork primitive lets you re-run with one variable changed to
isolate cause.

This page is the diagnostic walkthrough: how to use the framework's
operator surface to answer the common debugging questions in
order, from "what just happened" to "why did it happen that way."

## Read the trace first

```python
rt.print_trace()
```

The trace is the event log rendered with one event per line, tags
in brackets, short summaries. Read it top-to-bottom for a small
run, scan for tag patterns in a large one. Every event the framework
emits has a tag and a payload summary; nothing happens that isn't
in the trace.

For a saved run:

```bash
activegraph inspect <store-url> --run-id <run> --tail 100
```

The `--tail` flag bounds output. Increase it (`--tail 500`, or no
`--tail` for the full log) when the question is "what led up to
the failure" rather than "what failed."

## Narrow with `activegraph inspect`

The CLI has selectors for the three diagnostic questions that come
up repeatedly:

```bash
# Print one event's full payload (every error message names an
# event id; this is the next click):
activegraph inspect <store> --event <event-id>

# List behaviors registered in this run (compare against what
# fired in the trace to spot missing dispatch):
activegraph inspect <store> --behaviors

# Show pack versions and prompt content hashes (compare against
# replay-divergence errors to spot prompt drift):
activegraph inspect <store> --pack-version
```

The three selectors are mutually exclusive — they're focused
queries, not filters on the full status output. See the
[CLI reference](../reference/cli/) for the full surface.

## Read `behavior.failed` events

Most failures inside a goal run land as `behavior.failed` events,
not as escaped exceptions. The runtime catches the behavior's
exception, captures the type/message/reason, emits the event, and
keeps going. The trace shows them inline:

```
[behavior.failed]   evt_NNN  your.behavior  reason=llm.parse_error
```

To read the full payload (the original exception, the
provider/tool response, any `payload_extras`):

```bash
activegraph inspect <store> --event <behavior.failed-id>
```

The structured `reason` codes group failures by recovery shape;
the [error catalog](../reference/errors/llm-behavior-error.md) has
per-reason recovery prose. The [`failure-model`](../concepts/failure-model.md)
page covers why these are events rather than escaped exceptions.

### Get the full traceback in Python

Every `behavior.failed` payload carries the complete traceback of
the exception that failed the behavior (recorded since v1.0.3).
`runtime.trace.failures()` (v1.3) returns those events directly:

```python
for failure in rt.trace.failures():
    p = failure.payload
    print(f"{p['behavior']} failed on {p['event_id']}:")
    print(p["traceback"])
```

This is the fastest loop while developing a pack: run the goal,
print `rt.trace.failures()`, fix, rerun. The one-line trace entry
(`[behavior.failed] name: TypeError: ...`) stays terse by design —
the traceback lives in the payload, not the trace format.

`rt.trace.events()` is the companion accessor: the run's events as
objects, in log order — the usual way to pick an event id for
`rt.fork(at_event=...)` or to filter by type without parsing trace
lines.

## Walk the causal chain

Every event carries `caused_by` — the id of the event that
triggered the behavior that produced this one. Walking the chain
backward reconstructs the causal path from any event to its root.

```python
# Walk backward from a specific event:
event = rt.graph.get_event(event_id)
chain = []
while event is not None:
    chain.append(event)
    event = rt.graph.get_event(event.caused_by) if event.caused_by else None

for e in reversed(chain):
    print(f"{e.id}  {e.type}  {e.actor}")
```

The chain ends at a root event — usually `goal.created` (an
operator-pushed goal) or a custom event from outside the runtime
loop. Reading the chain is the answer to "why did this fire?"

## Fork-and-replay-in-isolation for narrowing bugs

When the question is "is the bug in this specific behavior, or
upstream?", fork the run at a point before the suspected behavior
fires, re-run with the behavior modified or removed, and diff:

```bash
# Fork from the event just before the suspect behavior fired:
activegraph fork <store> --run-id <run> --at-event <evt-before> \
    --label suspect-removed \
    --record

# Run the fork through your modified behavior set, then diff:
activegraph diff <store> --run-a <parent-run> --run-b <fork-run>
```

The diff output shows shared events, parent-only events, fork-only
events, and divergent objects. The first divergent object tells
you where the new behavior set started producing different work.
[`forking`](../concepts/forking.md) covers the fork primitive in
detail; the [`replay-divergence-error`](../reference/errors/replay-divergence-error.md)
page covers what fires when strict-mode fork detects drift.

## When the bug is in your prompt

LLM behaviors are debug-instrumented by default. Every call lands
two events:

```
[llm.requested]    evt_NNN  your.behavior  model=... prompt_hash=...
[llm.responded]    evt_NNN  your.behavior  cache_hit=false ...
```

To read the full prompt the framework assembled:

```bash
activegraph inspect <store> --event <llm.requested-id>
```

The payload includes the system message, the messages list, and
the tool definitions — exactly what went to the provider. If the
behavior's body parses the response wrong, the
`llm.responded` event has the raw response. If the prompt itself
is wrong, the `llm.requested` event is the source.

For prompt-hash drift across runs (the "this worked yesterday"
case), compare `--pack-version` between the two runs. The prompt
content hash is in the `pack.loaded` event; if the hashes differ,
the prompt template changed.

## Reproducing intermittent failures

If a failure only happens sometimes (rate limits, race conditions
in external systems, model temperature variance), the trace from
the failing run is still the most reliable artifact. Save it:

```bash
activegraph export-trace <store> --run-id <run> --to failure.jsonl
```

Then re-run with `RecordedLLMProvider` against the saved
fixtures — the recorded provider replays the recorded responses
deterministically, so the failing path runs the same way every
time. The fixture-missing path (no recorded response for a given
prompt) raises [`llm-behavior-error`](../reference/errors/llm-behavior-error.md)
with `reason=llm.fixture_missing`, which is informative on its own.

## What's related

- [The trace](../concepts/events.md) — the rendered form of the
  event log.
- [`failure-model`](../concepts/failure-model.md) — why most
  failures are events, not exceptions.
- [`forking`](../concepts/forking.md) — the primitive for
  fork-and-replay-in-isolation debugging.
- [`replay`](../concepts/replay.md) — the strict-vs-permissive
  modes and what each one catches.
- [Error catalog](../reference/errors/replay-divergence-error.md)
  — per-error recovery prose. Every error message links into the
  catalog.
- [Operating in production](../guides/operating-in-production.md)
  — the production-facing operator guide; this page is the
  developer-facing complement.
