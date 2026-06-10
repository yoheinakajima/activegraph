# ReplayDivergenceError

The replay or fork you just ran produced an event stream that doesn't
match the recorded log. The framework refuses to silently use a stale
result — that would break the audit trail the cache is designed to
preserve. The fix depends on which kind of divergence fired; the
error message itself names the kind in its summary line and the
recovery command is below.

## Quick fix by kind

### Prompt hash mismatch

The recorded prompt hash for an `llm.requested` event doesn't match
what the live re-run produced. Something changed in an LLM behavior:
its code, a prompt template, a system message, or a tool's input
arguments.

```bash
# If the change was intentional (you edited a behavior or template),
# re-record from the divergence point:
activegraph fork <parent-run> --at-event <event-id> --record

# If the change was unintentional, diff your code against the
# recorded run's pack version and revert:
activegraph inspect <parent-run> --pack-version
```

To see the full recorded prompt for the offending event:

```bash
activegraph inspect <parent-run> --event <event-id>
```

The recorded prompt hash and the live hash are both in the error
message body so you can confirm at a glance whether the prompt is
the same modulo whitespace or genuinely different.

### Event-type mismatch

At the pinned event id, the live re-run produced a different event
type than recorded. The behavior graph took a different branch —
usually because a behavior's `where` filter, a pattern subscription,
or a conditional `graph.emit` changed since the recording.

```bash
# Identify the behavior that produced the recorded event:
activegraph inspect <parent-run> --event <event-id>

# Then diff that behavior against your current source. If the change
# was intentional, fork with --record to refresh the recording.
```

### Length mismatch (short live or extra live)

Either the live re-run finished before the recorded log did (a
behavior that used to fire no longer fires), or the live re-run
produced an event the recorded log doesn't have (a new behavior was
added, or a pattern subscription was loosened).

```bash
# Compare what's currently registered against what fired in the
# recorded run:
activegraph inspect <parent-run> --behaviors

# Re-record from the divergence point if the new behavior set is
# intentional:
activegraph fork <parent-run> --at-event <event-id> --record
```

## How to diagnose deeper

If the quick fixes above haven't isolated the cause, three diagnostic
commands cover most cases:

```bash
# Pack versions loaded in the recorded run (and their prompt content
# hashes — these are what the replay cache compares against):
activegraph inspect <parent-run> --pack-version

# Full payload of the offending event:
activegraph inspect <parent-run> --event <event-id>

# Tail of events near the divergence point to see what came before:
activegraph inspect <parent-run> --tail 50
```

The error message's `.context` dict carries the event id, the kind
discriminator, and both expected and actual values. Code catching
the exception can read these directly:

```python
try:
    rt = Runtime.load(url, run_id=parent_run, replay_strict=True)
except ReplayDivergenceError as e:
    print(e.event_id, e.kind, e.expected, e.actual)
```

`e.kind` is one of `"prompt_hash_mismatch"`, `"type_mismatch"`, or
`"length_mismatch"` — the same discriminator the quick-fix sections
above are organized around.

## When does this fire

Replay reconstructs a run by re-applying its event log. The framework
offers two modes:

- **Permissive replay** (`replay_strict=False`, the default for
  `Runtime.load`). Events are re-emitted from the log; the runtime
  trusts the recording. `ReplayDivergenceError` never fires here.
- **Strict replay** (`replay_strict=True`). Behaviors re-fire against
  the recorded seed and the framework compares the live event stream
  against the recorded one. Any drift fires this error pinned to the
  first divergent event id.

The fork primitive runs strict by default — a fork's value is its
shared lineage with its parent, and shared lineage requires the early
events to replay identically. The error fires most often during fork
operations after behavior or prompt edits, which is the workflow it
was designed for.

## Why the framework refuses to continue

The replay cache keys on the full prompt hash (for LLM behaviors)
and on the event type stream (for everything else). A cache hit
that silently substituted a different recorded response under a
different prompt would corrupt the audit trail — the trace would
claim a specific LLM call produced a specific response when the
recorded response came from different input. Replay determinism is
a property the cache exists to preserve, not a constraint the cache
fights against.

This is the same invariant-protection stance the framework takes
everywhere: see [`failure-model`](../../concepts/failure-model.md)
for the broader principle.

## Why this is not `behavior.failed`

`ReplayDivergenceError` is part of strict replay verification, not a
behavior body failure. The caller asked the runtime to prove that the
recorded event stream still matches the current behaviors; a mismatch
is the answer to that call. Routing it through `behavior.failed` would
append a new event to the log being verified and make the verifier
harder to reason about.

The replay model is covered in [Replay](../../concepts/replay.md).
Fork-specific recovery is covered in [Forking](../../concepts/forking.md),
especially the `--record` workflow for intentional prompt or behavior
changes.

## What's related

- **[failure-model](../../concepts/failure-model.md)** — why the
  framework prefers exceptions over silent substitutions in cases
  like this.
- **[forking](../../concepts/forking.md)** — the operation
  ReplayDivergenceError fires most often during.
- **[replay](../../concepts/replay.md)** — the broader replay model
  and the two modes (permissive vs strict).
- **`activegraph fork --record`** in the [CLI
  reference](../cli/) — the canonical recovery command.
