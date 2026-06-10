# LLMBehaviorError

An `@llm_behavior` failed during a goal run. The provider returned
something the framework can't use (couldn't parse, didn't match the
declared schema, no fixture for the prompt), or the call itself
failed (rate limit, network).

The error you see is a **carrier** — the runtime catches it inside
the behavior dispatch and emits a `behavior.failed` event with the
same `reason` and `payload_extras`. Downstream behaviors subscribed
to `behavior.failed` can react. The exception only surfaces to your
code if you're calling the behavior directly (rare; most code runs
through `runtime.run_goal()` and reads the trace).

See [`failure-model`](../../concepts/failure-model.md) for why
behavior failures are events, not exceptions you have to catch.
For the complete reason-code table, see
[`reason-codes`](../reason-codes.md).

## Quick fix by category

Group the reason codes by what you do about them — the framework
distinguishes ~5 reasons but the recovery shapes cluster.

### Failures you can't fix in code: retry

`llm.network_error`, `llm.rate_limited`. The provider is briefly
unavailable. The runtime first makes a bounded set of provider-call
retries before it emits the terminal `behavior.failed` event. Each
failed attempt appears as an `llm.responded` event with an `error`
payload; a successful later attempt is the one cached and stamped
into object provenance.

After the built-in attempts are exhausted, the right higher-level
pattern is still a **retry behavior** that subscribes to
`behavior.failed` and re-fires the work with run-specific backoff or
operator policy:

```python
@behavior(
    name="llm_retry",
    on=["behavior.failed"],
    where={
        "behavior": "your.behavior.name",
        "reason": ["llm.network_error", "llm.rate_limited"],
    },
)
def llm_retry(event, graph, ctx):
    attempt = (event.payload.get("attempt") or 0) + 1
    if attempt > 3:
        return
    graph.emit("retry.requested", {
        "for_event": event.payload["triggering_event_id"],
        "attempt": attempt,
    })
```

Terminal retries are first-class graph citizens (CONTRACT v0.6 #13).
Provider-call attempts are visible as `llm.requested` /
`llm.responded(error=...)` pairs, and higher-level retries appear as
normal behavior-emitted events that you can fork from.

### Failures from your prompt: tighten the prompt

`llm.parse_error`, `llm.schema_violation`. The provider returned
something, but it wasn't valid JSON or didn't match the behavior's
`output_schema`. Tighten the prompt so the model produces the right
shape:

- Lower `temperature` if available; reduce sampling variance.
- Add an explicit example of the expected JSON in the prompt.
- Tighten the Pydantic schema to reject ambiguous shapes earlier
  (e.g., `Literal[...]` instead of `str` for enum-shaped fields).

The full provider response is in the `behavior.failed` event's
`payload_extras`:

```bash
activegraph inspect <store> --event <behavior.failed-id>
```

#### `llm.schema_violation` — "the model returned the schema, not an instance"

A specific shape of `llm.schema_violation` worth naming: the
provider response is the JSON Schema definition itself, echoed back
verbatim, rather than an instance that conforms to the schema. In
`payload_extras["raw_response"]` the symptom is unmistakeable —
top-level keys like `"properties"`, `"type": "object"`, and `"$defs"`
appear where the schema's actual fields should appear:

```json
{
  "type": "object",
  "properties": {"claims": {"type": "array", ...}},
  "required": ["claims"]
}
```

instead of:

```json
{"claims": [{"speaker": "...", "statement": "...", "confidence": 0.9}]}
```

**Root cause:** the model treated the schema definition shown in the
prompt as the requested output shape rather than as a contract its
output should satisfy. Smaller / older / non-tool-trained models hit
this more often.

**Fix in v1.0.1+ (automatic):** the runtime now assembles the system
prompt with both the schema AND a synthesized example instance,
plus explicit "return an INSTANCE, not the schema" language. Most
schema-echo failures stop firing without any code change on your
end.

**If it still fires in v1.0.1+:** the schema is too abstract for the
auto-derived example to be useful (deeply nested generics, large
`anyOf` unions, schemas with no `properties` at the top level). In
those cases, override the prompt assembly with a `prompt_template=`
that bakes in a real example from your domain:

```python
@llm_behavior(
    output_schema=ClaimList,
    prompt_template=(
        "{system}\n\n"
        "{view}\n\n"
        "Example response (this is the shape — substitute real values):\n"
        '{{"claims": [{{"speaker": "CFO", "statement": "Revenue grew 28%.", '
        '"confidence": 0.92}}]}}\n\n'
        "{event}\n\n"
        "{instruction}"
    ),
)
def extract_claims(event, graph, ctx, llm_output): ...
```

See `@llm_behavior`'s `prompt_template=` docstring for what each
placeholder contains. If the model never recovers even with a real
example, switch to a tool-trained model (the small models that echo
schemas back rarely come from the tool-trained families).

### Failures from fork/replay: re-record

`llm.fixture_missing`. You're running against `RecordedLLMProvider`
and the prompt's hash doesn't match any recorded response. Either
the prompt changed since the fixtures were recorded or this is a
new prompt that was never recorded.

```bash
# Re-record live, then run again against the recorded provider:
ANTHROPIC_API_KEY=... python your_script.py   # records as it runs
```

This is the same fix as [`ReplayDivergenceError`](replay-divergence-error.md)'s
prompt_hash mismatch — the cache contract is the same on both sides.

## How to diagnose

The reason code is in the error's `.reason` attribute and in the
emitted event's payload:

```python
try:
    rt.run_goal("...")
except LLMBehaviorError as e:
    print(e.reason)            # 'llm.parse_error', etc.
    print(e.payload_extras)    # full provider response, raw text, etc.
```

In the trace, look for the `behavior.failed` event the runtime
emitted in your behalf:

```
[behavior.failed]   evt_NNN  your.behavior  reason=llm.parse_error
```

The recovery flow always starts there. The error's `More:` link
points at this page; the trace event points at the behavior that
fired the carrier.

For transient provider failures, inspect the preceding `llm.responded`
events as well. Failed attempts carry:

```json
{
  "error": {"reason": "llm.network_error", "message": "..."},
  "retryable": true,
  "attempt_index": 0,
  "max_attempts": 3
}
```

## When does this fire

Inside an `@llm_behavior` wrapper, after the provider returned (or
raised) and before the behavior body's output is merged back into
the graph. The framework catches it, emits `behavior.failed`, and
moves on — the goal run doesn't halt. The exception only escapes
to your code if you're invoking the behavior outside of
`runtime.run_goal()` / `run_until_idle()`.

## Why the framework refuses to continue (the behavior, not the run)

The runtime treats LLM failures as graph-level events because LLM
behavior is inherently flaky and "halt the entire goal on first
provider hiccup" is the wrong default for long-running agentic
work. The failure is captured in the audit trail with full context
(reason, payload_extras, behavior name, triggering event); downstream
code subscribes if it wants to react, ignores if it doesn't.

See [`failure-model`](../../concepts/failure-model.md) for the
broader principle and [`tool-error`](tool-error.md) for the sibling
on the tool side.

## What's related

- [`tool-error`](tool-error.md) — if the failure came from the tool
  side rather than the LLM side, see here. The carrier shape is
  symmetric.
- [`unknown-tool-error`](unknown-tool-error.md) — for the
  registration-time variant: an LLM behavior declared a tool that
  isn't registered.
- [`failure-model`](../../concepts/failure-model.md) — why
  `behavior.failed` is an event rather than an escaped exception.


---

See [Observing failures in caller code](../../concepts/failure-model.md#observing-failures-in-caller-code)
for `Runtime.errors` and the `BehaviorFailure` shape.
