# ToolError

A tool invocation failed mid-execution. The tool body raised, timed
out, hit a network error, returned data that didn't match its
declared output schema, or hit the runtime's tool budget.

Like [`LLMBehaviorError`](llm-behavior-error.md), this is a
**carrier**. The runtime catches it inside the tool dispatch, emits
a `tool.responded` event with `error.reason` set, and the calling
behavior reads the structured failure from that event. The exception
only escapes to your code if you're invoking the tool outside of an
LLM behavior's tool-loop.

See [`failure-model`](../../concepts/failure-model.md) for the
broader principle. For the complete reason-code table, see
[`reason-codes`](../reason-codes.md).

## Quick fix by category

The error message names the specific `reason` (e.g., `tool.timeout`)
and gives per-reason recovery prose inline. The doc page groups
those reasons by what you do about them.

### Failures you can't fix in code: retry

`tool.timeout`, `tool.network_error`. The tool reached an external
system that was briefly slow or unavailable. Write a retry behavior
on `behavior.failed`:

```python
@behavior(
    name="tool_retry",
    on=["behavior.failed"],
    where={
        "reason": ["tool.timeout", "tool.network_error"],
    },
)
def tool_retry(event, graph, ctx):
    attempt = (event.payload.get("attempt") or 0) + 1
    if attempt > 3:
        return
    graph.emit("retry.requested", {
        "for_event": event.payload["triggering_event_id"],
        "attempt": attempt,
    })
```

Retries are graph citizens (CONTRACT v0.6 #13), not framework
middleware. Every retry appears in the trace.

### Failures from your inputs: tighten the call site

`tool.invalid_input`. The LLM (or your code) called the tool with
arguments that didn't match its declared Pydantic input schema. Two
fixes:

- If the LLM is producing the args, tighten the prompt with an
  example of correctly-shaped input.
- If your schema is too strict, relax the relevant field (e.g., a
  required field that's actually optional in practice).

### Failures from the tool author: fix the tool

`tool.invalid_output`. The tool body returned a value that didn't
match its declared Pydantic output schema. This is a bug in the
tool, not in the caller — fix the tool body to return data matching
the schema, or relax the schema if the actual return shape is right.

`tool.execution_error` is the catch-all for unexpected exceptions
inside the tool body. The original exception type and traceback are
preserved in `payload_extras` for diagnosis:

```python
try:
    rt.run_goal("...")
except ToolError as e:
    print(e.reason)             # 'tool.execution_error'
    print(e.payload_extras)     # {'exception_type': '...', ...}
```

### Failures from fork/replay: re-record

`tool.fixture_missing`. You're running against `RecordedTool` and
the live arguments don't match any recorded invocation. Re-record
from a clean run with the current arguments, same flow as the LLM
case.

### Failures from budget: recalibrate

`budget.tool_calls_exhausted`, `budget.cost_exhausted`. The runtime
hit its declared limit before the behavior finished. Either raise
the budget on the next run or accept the partial result the trace
records.

## How to diagnose

The reason code is on the exception and in the `tool.responded`
event the runtime emits in your behalf:

```
[tool.responded]    evt_NNN  your.behavior  tool=your_tool error=tool.timeout
```

The full `payload_extras` includes whatever the tool body recorded
before failing (input args, partial output, original exception trace
for execution errors). Inspect it directly:

```bash
activegraph inspect <store> --event <tool.responded-id>
```

## When does this fire

Inside an `@tool`-decorated body, or in the runtime's tool-loop
when validating inputs/outputs against the declared schemas. The
runtime catches and emits a `tool.responded` event with the error,
then the calling LLM behavior continues — usually the LLM sees the
failure in the conversation and decides what to do next, or the
calling behavior body itself reads the error and branches.

## Why the framework refuses to continue (the tool, not the run)

Tools that reach external systems will fail intermittently. Halting
the goal run on first tool failure would make the framework brittle
in exactly the case it was designed for (long-running, multi-LLM,
multi-tool agentic work). The structured failure in the event log
is the right surface: the LLM can see it, retry behaviors can react,
the audit trail records what happened.

## What's related

- [`LLMBehaviorError`](llm-behavior-error.md) — the sibling on the
  LLM side of an LLM behavior's call/response loop. The carrier
  shape is symmetric.
- [`unknown-tool-error`](unknown-tool-error.md) — fires when the
  LLM asks for a tool the behavior didn't declare. Distinct from
  ToolError, which fires when a declared tool fails to execute.
- [`failure-model`](../../concepts/failure-model.md) — why
  `tool.responded` carries failures as structured events rather
  than escaping exceptions.


---

See [Observing failures in caller code](../../concepts/failure-model.md#observing-failures-in-caller-code)
for `Runtime.errors` and the `BehaviorFailure` shape.
