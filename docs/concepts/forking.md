# Forking

A fork is a branch from a parent run at a specific event. The fork
shares the parent's event log up to the fork point; from there, it
has its own independent log. The two runs can be diffed,
configured differently, and inspected side-by-side without
touching the parent's state.

Forking is what lets the framework answer "what would have
happened if I'd done X differently?" — a question agentic systems
need to answer routinely and most frameworks can't answer at all.
The shared-lineage model plus the [cache layer](replay.md) makes
fork cheap (no LLM re-execution for the shared prefix) and
honest (the fork's lineage is verifiable from the event log).

## The shared-lineage model

A fork copies events from the parent run, in order, up to the
`--at-event` cutoff. The cutoff is **inclusive** — events at the
cutoff id and before are in the fork; events after are not.

```
parent:  evt_001 ... evt_042 evt_043 evt_044 ... (continues)
                            |
                            +- fork from evt_042
                            v
fork:    evt_001 ... evt_042 evt_045 evt_046 ... (fork's own work)
```

The fork's events 1 through 42 are the parent's. Event 45 onward
is the fork's own; event ids don't collide because the fork has
its own run id and its own monotonic id generator.

The shared prefix doesn't re-execute when the fork starts. The
framework [replays](replay.md) the prefix against the fork's
in-memory graph, then resumes live execution from the cutoff.
LLM and tool responses for the shared prefix are served from the
cache — no new LLM calls, no new tool calls — which keeps fork
cheap.

## The CLI surface

```bash
activegraph fork <parent-url> \
    --run-id <parent-run> \
    --at-event <event-id> \
    --label <human-readable> \
    --set <pack>.<setting>=<value> \
    --record
```

Three flags shape the fork:

- **`--at-event`** — the cutoff. Required.
- **`--set <key>=<value>`** — override a pack setting in the
  fork. The key is a dotted path into pack settings only
  (`diligence.confidence_threshold_for_review=0.9` is in scope;
  `runtime.budget.max_cost_usd=10` is out of scope). Multiple
  `--set` flags compose. Syntax and "pack was not loaded by this
  fork point" errors fail at fork time; unknown setting paths and
  type coercion errors fail when the pack reloads and Pydantic
  validates the resulting settings.
- **`--record`** — mark the fork as a re-recording. Behaviors
  whose prompts changed since the parent run will be re-recorded
  rather than cache-hit; new cache entries land in the fork's
  events.

`--set` records a fork-local `pack.settings_overridden` event,
then normal pack loading applies and validates that override before
post-fork behaviors run. The semantics — pack settings only,
auditable event-log override, fail loud on typos — are documented in the
[CLI reference](../reference/cli/).

## How the cache replays

For events before the fork point, the cache serves recorded
responses by content hash:

- **LLM call with the same prompt hash** → cached response from
  the parent run's `llm.responded` event.
- **Tool call with the same args hash** → cached response from
  the parent run's `tool.responded` event.
- **LLM or tool call whose hash drifted from the parent** —
  expected only after `--set` changed something upstream. Without
  `--record`, the fork's strict replay fires
  [`replay-divergence-error`](../reference/errors/replay-divergence-error.md);
  with `--record`, the fork accepts the new responses and records
  them as its own.

The cache is per-store, indexed by run id. A fork that needs to
re-execute the same prompt the parent already ran in a different
run can't reach across — caches don't cross runs. (Migration is
the primitive for moving runs across stores; see
[Operating in production](../guides/operating-in-production.md).)

## When to fork vs when to use frames

Both let parallel computations proceed in isolation. The
difference is durability and replay:

- **Fork** — a separate run with shared event-log lineage up to
  the fork point. Forks are durable, replayable, diffable. Use
  fork when the parallel branches might diverge permanently or
  need independent persistence.
- **Frames** ([`frames.md`](frames.md)) — sub-contexts within
  one run. The frame's events live in the same event log as
  everything else; replay is the whole run. Use frames when the
  parallel contexts are short-lived or semantically belong
  together.

The decision rule: **if you'd want to inspect, diff, or migrate
the two branches independently after the fact, use fork. If the
branches converge back to a single output within the same run,
use frames.**

A common pattern is to start parallel work in frames, then fork
only the branches worth keeping. Frames are the cheap parallel
primitive; forks are the durable one.

## What's related

- [`graph`](graph.md) — the world state the fork projects from
  its event log.
- [`events`](events.md) — the append-only history forks share
  up to the cutoff.
- [`replay`](replay.md) — the operation that reconstructs the
  shared prefix in the fork.
- [`frames`](frames.md) — the in-run parallel primitive that
  complements fork.
- [`patches`](patches.md) — patches in a fork are independent of
  the parent's patches once the fork point passes.
- [`replay-divergence-error`](../reference/errors/replay-divergence-error.md)
  — the error case when strict-mode replay finds a divergent
  prompt hash or event stream.
- [CLI reference](../reference/cli/) — the full surface for
  `activegraph fork` and the surrounding commands.
