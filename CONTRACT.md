## Document map pointer

See [`CONTRACT-INDEX.md`](CONTRACT-INDEX.md) for the current map of
contract, changelog, roadmap, future-ideas, and audit documents.
`CONTRACT.md` remains the source of truth for locked decisions and
historical archeology. Implementation scoping for the current cycle
lives in [`ROADMAP.md`](ROADMAP.md), not inline inside `CONTRACT.md`.

---

## Post-v1.0.3 contract review banner (2026-05-19)

A documentation-heavy review of the cumulative document was run after
v1.0.3 shipped, on branch `claude/review-contract-docs-CoaO6`. The
review produced two companion deliverables:

- `CONTRACT-review-findings.md` — the audit record: amendment-by-
  amendment outcomes, API coherence findings, failure-model
  consistency findings, tests-verify-contracts findings, plus
  v1.0.4 / v1.1 candidates surfaced.
- `v1.1-plan.md` — the consolidated v1.1 backlog. CONTRACT.md no
  longer carries scattered "filed for v1.1" markers in amendment
  bodies; each deferral reads "Filed in v1.1-plan.md" and points at
  one entry.

### Document semantics: this banner is a status overlay, not a rewrite

The review added, but did not rewrite, the following:

- This preamble banner.
- Per-milestone `Review status:` markers immediately after each
  milestone header. The default outcome is **STILL ACCURATE**;
  departures are surfaced inline.
- One in-place marker at v1.0.2 #1(b) recording that the prose was
  revised by v1.0.2.post1 (see standing rule §1 below).

**Pre-banner content is the original archeological record** — every
amendment from v0 through v1.0.3 reads as it did when locked
(modulo the v1.0.2 #1(b) in-place revision that this review
flagged). **Post-banner annotations are the May 2026 review
overlay** — `Review status:` markers and "Filed in v1.1-plan.md"
pointers were appended, never substituted in for prior prose.

A future reader should treat the two layers separately. The
archeology answers "what was decided when"; the overlay answers
"how does that decision read now."

### Standing rules adopted by this review

The review surfaced two process gaps that are now standing rules
for every future amendment cycle. These are framework-discipline
rules, not project-management rules; they apply to every
CONTRACT amendment from this banner forward.

**Standing rule §1 — Amendments append, never modify.** When a
post-release amendment changes or corrects an earlier amendment's
wording, the amendment is appended as a new dated section under
(or alongside) the original — never folded in by editing the
prior section's prose. Editing in place destroys the boundary
record: a future reader of the contract alone cannot reconstruct
when the change happened or what was changed. The CHANGELOG carries
shipping dates; CONTRACT carries decision provenance. Both
matter; neither replaces the other.

This rule is retroactive in one specific case: v1.0.2 #1(b) was
revised in place by v1.0.2.post1. A v1.0.4 candidate (review §5
#6) appends a proper `### v1.0.2.post1` section to CONTRACT to
restore the archeological record without rewriting the corrected
prose. The in-place marker added by this review is the
interim measure.

**Standing rule §2 — Tests anchor on the boundary the contract
names, not the implementation's path.** When an amendment locks a
boundary ("at registration time," "before any state mutation," "on
load," "at decoration time," "after the pack is loaded"), the
test suite for that amendment must exercise the boundary the
contract names — not whatever path the implementation happens to
take through that boundary. Two boundary-mismatch bugs have shipped
under this gap (v0.5 #8's `_requeue_unfired`; v1.0.2 #1(b)'s lazy
validation); the v1.0.2.post1 tests in
`tests/test_llm_default_model.py` Section (g) are the canonical
model. The future-proof shape: every test naming a binding moment
in its name AND asserting the contract behavior fires at that
specific moment.

A spec-vs-impl drift gate (v1.1 candidate D-1 / D-2) is the
mechanical complement to this discipline. Automated checking
catches what amendment-cycle discipline alone cannot.

### What this review did not change

No code changed in this pass. Code-shaped findings are filed as
v1.0.4 candidates (review §5) or v1.1 candidates (`v1.1-plan.md`).
No amendment was retired. No amendment was rewritten to make a
different claim than its original; "STILL ACCURATE" is the
overwhelming verdict.

---

# Active Graph v0 — Design Contract

**Review status (2026-05-19):** STILL ACCURATE. #11 (`token_budget`
"parsed but ignored until v0.6") and #16 (out-of-scope list) are
historical statements — every deferred item has since shipped.
Preserved as archeology; see review §1 and §5 #5 (v1.0.4 candidate
to add one-line "shipped in vX" notes).

These decisions are locked. Changing any of them is a breaking change to the
public API or to the trace format. Any change must update this file in the
same commit as the code change, with a one-line rationale appended to the
relevant section.

## 1. Identity and IDs

- Object IDs: `{type}#{n}` — e.g. `task#1`, `task#2`, `claim#3`. Single **global** monotonic counter prefixed by the object's type (so the third object created in a run gets `#3` regardless of type). This matches the README's expected quickstart trace.
- Event IDs: `evt_NNN` zero-padded to at least 3 digits, monotonic per graph, starting at `evt_001`.
- Relation IDs: `rel_NNN`.
- Patch IDs: `patch_NNN`.
- Frame IDs: `frame_NNN`.
- All IDs come from a single `IDGen` attached to the graph. Tests inject a deterministic generator. v0.5+ may swap to ULID behind the same factory; `id` stays `str`.

## 2. Event log is the source of truth (strict)

Every state change is an event first, projection second. The graph is
materialized from the log.

- `Graph.emit(event)` is the only mutation path.
- Convenience methods (`add_object`, `add_relation`, `patch_object`,
  `propose_patch`, `apply_patch`) build an event and call `emit`.
- The projector lives in `core/graph.py` and is the only code that touches
  `_objects` / `_relations`.
- No `_objects[id] = ...` outside the projector. Ever.

## 3. Event immutability

Events are append-only. Never edited, never deleted. Corrections happen by
emitting new events (e.g. `object.removed`).

## 4. Object versioning

- Field name: `version`. Integer, starts at 1.
- Increments by 1 on every successful `patch.applied`.
- Patches record `expected_version`. If the object's current version differs
  at apply time, the runtime emits `patch.rejected` with `current_version` in
  the payload and does not mutate.

## 5. Provenance

Every object, relation, and patch has a `provenance` dict written by the
runtime, never by the behavior:

```
{
  "created_by":      <behavior name | "user" | "system">,
  "caused_by_event": <event id | null>,
  "frame_id":        <frame id | null>,
  "timestamp":       <iso utc string>,
  "evidence":        [<event id>, ...]
}
```

Behaviors cannot set provenance. Any `provenance` key passed in `data` is
silently ignored.

## 6. Behavior signature

```python
def fn(event, graph, ctx) -> None
```

No `BehaviorResult` return type. The runtime observes mutations on the
constrained `graph` wrapper and synthesizes a result for tracing.

## 7. Constrained graph wrapper

Behaviors receive a `BehaviorGraph` instance, not the raw `Graph`. Allowed:

- `add_object(type, data) -> ObjectHandle`
- `add_relation(source, target, type, data=None) -> RelationHandle`
- `patch_object(id, updates) -> Patch`
- `propose_patch(target, op, value, evidence=None, rationale=None) -> Patch`
- `emit(event_type, payload) -> Event`

Disallowed: iterating over all objects, mutating provenance, modifying
events, accessing runtime internals. Reads go through `ctx.view`.

## 8. Determinism

Inside behaviors:

- No `random()` — use `ctx.random`.
- No `datetime.now()` — use `ctx.clock.now()`.
- No env reads, no file I/O, no network calls.

The runtime injects deterministic `clock` and `random` for replay.

## 9. Event namespace

`dot.lowercase`. Reserved namespaces:

- `runtime.*` — runtime lifecycle (`runtime.started`, `runtime.idle`, `runtime.budget_exhausted`).
- `behavior.*` — behavior lifecycle (`behavior.started`, `behavior.completed`, `behavior.failed`, `relation_behavior.started`, `relation_behavior.completed`, `relation_behavior.failed`).
- Standard graph events: `object.created`, `object.removed`, `relation.created`, `relation.removed`, `patch.proposed`, `patch.applied`, `patch.rejected`.
- `goal.created` is a convention emitted by `runtime.run_goal()`.
- Everything else is user-emitted.

## 10. Queue

Single in-process FIFO queue. Single-threaded loop. When multiple behaviors
match one event, they run in registration order. No async, no priority, no
parallelism in v0.

## 11. Views

Behaviors declare desired view via decorator metadata. The runtime constructs
the view before invocation. Behaviors do not build their own view from
`graph.query(...)`. Default view (if none declared) is full graph plus the
last 50 events.

v0 view spec keys: `around` (path expression), `depth` (BFS), `include_types`
(list[str]), `recent_events` (int). `token_budget` is parsed but ignored
until v0.6.

`[review overlay 2026-05-19: honored from v0.6 onward via view assembly;
the "until v0.6" clause above is the original deferral, preserved as
written. See v1.0.4 #5 for the overlay-marker pattern.]`

## 12. Patch atomicity

A patch targets exactly one object. Multi-object changes are multiple
patches. Apply is all-or-nothing for that one patch.

## 13. Failure mode

When a behavior raises an exception:

- Catch.
- Emit `behavior.failed` with payload:
  ```
  {
    "behavior":       <name>,
    "event_id":       <triggering event id>,
    "exception_type": <class name>,
    "message":        <str(exc)>,
    "traceback":      <full traceback string>
  }
  ```
- Continue the loop. Never re-raise into the runtime.

## 14. Project layout

```
activegraph/
  __init__.py          # public API surface only
  core/
    __init__.py
    ids.py
    clock.py
    event.py
    graph.py           # Object, Relation, Graph, projector
    patch.py
    view.py
  runtime/
    __init__.py
    runtime.py
    registry.py
    queue.py
    view_builder.py
    budget.py
    behavior_graph.py  # constrained wrapper
  behaviors/
    __init__.py
    decorators.py
    base.py
  policy.py
  frame.py
  trace/
    __init__.py
    printer.py
    causal.py
  store/
    __init__.py
    memory.py
tests/
examples/
```

Import direction: `core/` knows nothing about `runtime/` or `behaviors/`.
`behaviors/` imports `core/` only. `runtime/` imports `core/` and
`behaviors/`. Anything else is a layering violation.

## 15. Testing

- Every primitive: unit test.
- Every README example: integration test.
- Trace output: snapshot test. Requires deterministic `IDGen` and `clock`,
  injected via `Graph(ids=..., clock=...)`.

## 16. Out of scope for v0

- Persistence beyond in-memory (SQLite → v0.5).
- LLM behaviors (v0.6).
- Pattern subscriptions beyond `event_type + where` (Cypher → v0.7).
- Async / threading / distribution.
- Fork / diff (v0.5).
- Packs (v1.0).
- UI / server / HTTP.

If a v0 PR drifts toward any of these, reject.

`[review overlay 2026-05-19: historical scope statement; every listed
item has shipped in a subsequent milestone (SQLite at v0.5, LLM
behaviors at v0.6, Cypher at v0.7, fork/diff at v0.5, packs at v1.0).
Async / threading / distribution and UI / server / HTTP remain out of
scope. Preserved as archeology; see v1.0.4 #5.]`

## 17. API-first development

`examples/quickstart.py` is written before the runtime. The example defines
the public API surface; the runtime is built backward to make it run.

## 18. Trace format is contract

The trace output shown in the README is the contract. Snapshot-tested.
Format changes require updating the README and the snapshot in the same
commit.

### Formatter rules (v0)

Tag column is left-aligned, padded to 26 chars; if the tag itself is longer,
one space follows it.

| Event type                    | Rendering                                                          |
|-------------------------------|--------------------------------------------------------------------|
| `goal.created`                | `{actor}: "{payload.goal}"`                                        |
| `object.created`              | `{id} "{title or text}" ({status})` (parentheses omitted if no status) |
| `relation.created`            | `{source} --{type}--> {target}`                                    |
| `patch.applied`               | `{target} {field}: {old} -> {new}` (one line per changed field)    |
| `behavior.started`            | `{name}` (with `  (matched {event_type}: {object_id})` if applicable) |
| `behavior.completed`          | `{name} ({N} objects, {M} relations)`                              |
| `behavior.failed`             | `{name}: {exception_type}: {message}`                              |
| `relation_behavior.started`   | `{name}  (matched {event_type} on {relation_type} edge)`           |
| `relation_behavior.completed` | `{name}`                                                           |
| `runtime.idle`                | `queue empty, budget remaining`                                    |
| Other (user-emitted)          | rendered with tag `[event.emitted]` and content `{type} {k=v...}`  |

## Reconciliation notes

- README prose says `patch_object` emits `object.updated`; the README's
  expected trace shows `[patch.applied]`. Per #18 the trace wins.
  `patch_object` emits `patch.applied` events. `object.updated` is reserved
  but unused in v0.

---

# Active Graph v0.5 — Resumability addendum

**Review status (2026-05-19):** STILL ACCURATE. #7 known-limitation
narrowed (not closed) by v0.6 #22 — forward pointer was already
self-documented in v0.6 #22. #8 "DIFF: re-queue unfired events on
load" was the contract claim against which a latent boundary-
mismatch bug shipped from v0.5 through v1.0-rc1; v1.0-rc2 fixed
the implementation (see CHANGELOG v1.0-rc2 entry on
`_requeue_unfired`). The contract claim was correct; the
implementation relied on a false reverse-implication. See review
§4 spot-check on v0.5 #8 and §7 #2 (boundary-mismatch class). A
contract-anchored test for the zero-subscriber case is filed as
v1.0.4 candidate #4.

v0.5 makes persistence physical. The event log moves from a Python list
into a SQLite file, with save / load / fork / diff on the runtime. v0
decisions all remain in force; the items below are additions and a
couple of small reconciliations.

## v0.5 #1. Event log is the canonical source of truth, persisted

Reaffirms v0 #2. v0.5 stores append events to durable storage as they
are emitted. The graph projection is rebuilt from the log on load. No
separate "graph snapshot" file. Snapshots are a v1+ optimization.

## v0.5 #2. EventStore interface

Abstract per-run interface in `store/base.py`:
```
append(event)
iter_events(after=None, until=None)
get_event(id)
count()
truncate_after(event_id)
close()
```
Two implementations ship: `InMemoryEventStore`, `SQLiteEventStore`. No
queries, no indexes beyond what the backend gives you. This is an event
log, not a database.

## v0.5 #3. SQLite schema (locked)

```
events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
       id TEXT NOT NULL,
       type TEXT NOT NULL,
       actor TEXT,
       payload TEXT NOT NULL,
       frame_id TEXT,
       caused_by TEXT,
       timestamp TEXT NOT NULL,
       run_id TEXT NOT NULL,
       UNIQUE(id, run_id))

runs(run_id TEXT PRIMARY KEY,
     parent_run_id TEXT,
     forked_at_event_id TEXT,
     label TEXT,
     created_at TEXT NOT NULL,
     goal TEXT,
     frame_id TEXT)

meta(key TEXT PRIMARY KEY,
     value TEXT NOT NULL)        -- holds schema_version since day one.
```

`seq` is the projection ordering authority, not `timestamp`. WAL +
`synchronous=NORMAL` for write performance; safe across process crashes.

### DIFF: UNIQUE constraint scope

The locked draft said `id TEXT NOT NULL UNIQUE` (globally unique).
Decision #12 says logical ids are scoped to `run_id` — a fork preserves
the parent's `evt_017`. The constraint is therefore
`UNIQUE(id, run_id)`. Column shape unchanged.

## v0.5 #4. JSON serialization

Event payloads serialize as JSON. Not pickle, not msgpack. Decimals →
canonical strings; datetimes → ISO 8601; sets → sorted lists. Loading
does NOT reverse-coerce — payloads stay flat JSON primitives on the
read side. Non-serializable payloads raise
`NonSerializableEventError` at `Graph.emit` time, before any state
mutation.

## v0.5 #5. Save and load API

```
Runtime(graph, persist_to="run.db")   # attaches a SQLite store
Runtime(graph)                        # in-memory; late-bind via save_state
runtime.save_state()                  # flush (must have store attached)
runtime.save_state(path="run.db")     # late-bind; copies in-memory events
Runtime.load(path, run_id=None)       # rebuild + continue
```

Save with an attached store is a flush, not a snapshot. The store
already has every event because writes are continuous.

## v0.5 #6. Run identity

Every run has a `run_id` (ULID). One SQLite file holds many runs. Load
without `run_id` selects the most recently written-to run.

## v0.5 #7. Determinism becomes load-bearing

`replay_strict=True` on `Runtime.load` re-fires behaviors from the
recorded seed events (events with no `caused_by`) into a fresh Graph
and compares the new event stream to the recorded one. First mismatch
raises `ReplayDivergenceError(event_id, expected, actual)`. Debugging
mode, not always-on.

### Known limitation: payload drift

v0.5 compares the event-type stream (id, type). It catches categorical
non-determinism — different number of events, different types in
different positions — which is what actually breaks resume in v0.5.
Payload-only drift (same shape, different values, e.g. a random number
inside a payload) is NOT detected. This is deferred to v0.6, when LLM
behaviors land and payload non-determinism becomes the dominant failure
mode (temperature, sampling). The v0.6 tightening will compare full
event payloads with a configurable allow-list of fields that may drift
(e.g. timestamps).

## v0.5 #8. In-flight loss + unfired re-queue

In-flight behavior loss on crash is accepted: behaviors that started
but did not complete lose their post-crash work, unless they already
emitted events.

### DIFF: re-queue unfired events on load

The draft said "the queue starts empty on load". Taken literally, that
breaks the budget-exhaustion resume case — events sitting in the queue
when the runtime stopped never fire. v0.5 ships the spec's intent with
a useful default:

- On load, events with NO `behavior.started` referencing them are
  re-queued. They fire on the next `run_until_idle` / `run_goal`.
- Events that any behavior started on (even if it crashed mid-way)
  are NOT re-queued.

In-flight loss is preserved (the original tradeoff); resume after
budget exhaustion now works.

## v0.5 #9. Fork semantics

```
fork = runtime.fork(at_event="evt_073", label="alternative-thesis")
```

New `run_id`, copy of parent log up to and including `at_event`,
parent untouched, independent run, forks-of-forks supported.

## v0.5 #10. Diff semantics

```
diff = runtime.diff(other)
diff.shared_events / parent_only_events / fork_only_events
diff.divergent_objects / divergent_relations
```

Structural only — lifecycle events (`behavior.*`,
`relation_behavior.*`, `runtime.*`) are excluded from the event
partition. Semantic comparison is a behavior's job. Shared prefix
requires matching `(id, type, payload)` so colliding ids after the
fork point don't get flattened.

## v0.5 #11. Fork copies event rows

Copy rows under the new `run_id`. Storage doubles per fork. Acceptable
for v0.5. Copy-on-write row sharing is a v1+ optimization.

## v0.5 #12. IDs are scoped to run_id

Across a fork, object/event/relation counters are reseeded from the
forked log. Two forks from the same point can produce different
`task#5` objects with the same logical id — fine, they live in
different runs.

## v0.5 #13. Provenance carries run_id

Every object, relation, and patch records `provenance.run_id`.
Auto-stamped by the runtime, never by behaviors.

## v0.5 #14. Replay is not the same as run

Lock the boundary in code:
- `Graph.emit(event)` — live: append, project, persist (if store),
  notify listeners.
- `Graph._replay_event(event)` — replay: append, project, mark as
  replayed. No persist, no listeners. Used by `Runtime.load` and
  `Runtime.fork`.

Replayed event ids are tracked in `Graph._replayed_ids` so the trace
printer can render them distinctly.

## v0.5 #15. The projector is module-level

`apply_event(graph, event)` is the only function that mutates
`_objects` / `_relations` / `_patches`. Called by both live and replay
paths. Extracted from `Graph._project` in v0.5.

## v0.5 #16. Backward compatibility

A `Runtime(graph)` without `persist_to` behaves exactly as it did in
v0. All v0 tests pass unchanged. Persistence is strictly opt-in.

## v0.5 #17. Migrations

`meta.schema_version` exists from day one. Bumping it is the migration
hook.

## v0.5 #18. Test scope

- Round-trip, resume after budget, chaos (raise mid-behavior), fork,
  fork-of-fork, diff, replay-strict happy path, replay-strict
  divergence, JSON edge cases, 10k events under 2 seconds soft target.
- No LLM, pattern, pack, or concurrency tests.

## v0.5 #19. Out of scope

- LLM behaviors (v0.6)
- Cypher pattern subscriptions (v0.7)
- Packs (v1.0)
- Multi-process / distributed runtime
- Real-time graph subscriptions
- Remote storage backends (Postgres, FalkorDB, Neo4j)
- Any UI

## v0.5 #20. API-first

`examples/resume_and_fork.py` is written before the persistence layer;
the implementation is built backward to make it run. Same as v0 #17.

## v0.5 #21. README updates ship with the implementation

The `Replay and resume` section matches the implementation exactly.
v0 trace snapshot stays green. v0.5 adds `replay_trace.txt` as a
second snapshot.

## v0.5 #22. Trace format addition for replay

Replayed events render with `[replay.event]` and one-line summaries.
After the last replayed event:

```
[replay.complete]         N events replayed, graph reconstructed
[runtime.idle]            ready to resume
```

These trace lines are NOT events in the log — the synthetic boundary
lines are computed by the printer from `graph.replayed_ids`. Otherwise
saving + loading would recursively re-emit them.

Snapshot-tested in `tests/test_replay_trace_snapshot.py`.

---

# Active Graph v0.6 — LLM behaviors addendum

**Review status (2026-05-19):** STILL ACCURATE. #14 (trace format
for LLM events) was incrementally amended by v0.7 #18 (added
`prompt_normalized=true`) and then v0.9.1 #2 (rolled the flag up
into `[trace.flags]`) — both successors self-document the change.
#11 reason taxonomy is "closed" per v1.0.1 #5(c) clause 4; no new
codes added since. #13 failures-as-events is the foundation for
v1.0 #4b's framework-wide principle and v1.0.3 #3's user-facing
surface (`Runtime.errors`).

v0.6 puts LLMs onto the substrate without breaking what made the
substrate trustworthy. Every LLM call is two events in the log; the
parsed structured output is the only thing the developer touches; the
trace, provenance, budgets, and replay machinery all keep meaning.
v0/v0.5 decisions remain in force; the items below are additions and
a handful of explicit narrowings.

## v0.6 #1. An LLM call is an event (two events, actually)

Every LLM invocation emits a `llm.requested` (before the call —
payload includes full prompt, model, params, prompt hash) and a
`llm.responded` (after — payload includes raw text, parsed output
[JSON-dumped], tokens, cost, latency, finish reason, cache_hit flag).
These persist like any other event. Replay reads them back instead of
re-calling. This single decision is what keeps the rest of the system
working under LLMs.

`llm.requested.caused_by` = triggering event id.
`llm.responded.caused_by` = `llm.requested.id`.

`llm.*` are reserved namespaces. They flow through the queue normally
— user behaviors **can** subscribe to them (an audit-trail behavior
is a legitimate use case). The wrapper does not.

## v0.6 #2. `@llm_behavior` is sugar over `@behavior`

The decorator returns an `LLMBehavior` (subclass of `Behavior`). The
runtime owns the invocation lifecycle via `_invoke_llm`:

```
behavior.started
  build_prompt(event, graph)              # pure, no I/O
  cache lookup by prompt_hash             # if replay_llm_cache=True
  pre-call cost gate                      # if max_cost_usd and no cache hit
  emit llm.requested
  provider.complete()   |   cached response
  emit llm.responded
  handler(event, graph, ctx, parsed_output)
behavior.completed
```

The handler signature is `(event, graph, ctx, llm_output) -> None` —
the 4th arg is a Pydantic instance (or whatever `output_schema=` was).
The 3-arg `fn` field on `LLMBehavior` is unused; calling
`LLMBehavior.run()` directly raises. Per CONTRACT v0.6 invariant: the
runtime is the only thing that invokes LLM behaviors.

### DIFF: CONTRACT #6 (behavior signature)

CONTRACT #6 documents the 3-arg signature for `@behavior` /
`@relation_behavior`. `@llm_behavior` is an explicit exception: the
user-supplied function is a 4-arg handler that the decorator binds
via the runtime's `_invoke_llm` path. Regular `@behavior` and
`@relation_behavior` signatures are unchanged.

## v0.6 #3. `LLMProvider` protocol

```python
class LLMProvider(Protocol):
    def complete(self, *, system, messages, model, max_tokens,
                 temperature, top_p, output_schema, timeout_seconds) -> LLMResponse: ...
    def estimate_cost(self, *, input_tokens, output_tokens, model) -> Decimal: ...
    def count_tokens(self, *, system, messages, model) -> int: ...
```

All keyword-only. `LLMResponse` carries raw text, parsed structured
output (Pydantic instance when `output_schema` was passed and parsing
succeeded), input/output tokens, Decimal cost, latency, model id,
finish reason, optional seed (always `None` for Anthropic), cache_hit
flag, and `provider_meta`. `count_tokens` is on the protocol because
the budget gate needs official token counts (CONTRACT #10 carryover).

No streaming, no tool use, no multi-call orchestration. Deferred to
v0.7.

## v0.6 #4. Anthropic is the reference implementation

`AnthropicProvider` reads `ANTHROPIC_API_KEY`. The `anthropic` SDK is
optional (`pip install activegraph[llm]`). Pricing table is
class-level, overridable via `pricing=` kwarg; ships with current
Claude 4.x family rates (May 2026). Family-prefix lookup
(`claude-sonnet-4-6` resolves to the `claude-sonnet-4` row). Unknown
models fall back to sonnet-4 pricing.

`top_p < 1.0` is only forwarded when narrowing (the SDK default is
1.0; forwarding it forces a behavior change). `seed=` is never
forwarded — Anthropic's messages API does not accept one.

## v0.6 #5. Pydantic v2 as the schema language

Developers define output shapes as Pydantic models. The decorator
takes `output_schema=ClaimList`. The runtime serializes the schema
into the system prompt as JSON-schema, and (after the call) validates
the model response against the same class. Two distinct failure
modes:

- `llm.parse_error` — no JSON found in the response
- `llm.schema_violation` — JSON found but Pydantic rejected it

No silent retries. If the developer wants a retry, they write a
behavior that subscribes to `behavior.failed`.

## v0.6 #6. Prompt assembly is the runtime's job

The four sources, in this order, are always assembled by the runtime:

1. **system** — frame goal, frame constraints, behavior `description=`,
   output-schema reminder (in that order; absent sections are omitted)
2. **view** — serialized scoped graph view (see v0.6 #13)
3. **event** — triggering event, JSON-serialized, with volatile fields
   stripped (provenance, timestamps, run_id) so the prompt is
   content-stable across runs/forks
4. **instruction** — auto-derived from `creates=` and `output_schema=`

`prompt_template=` is the only escape hatch — a Python `str.format`
template that receives `{system}`, `{view}`, `{event}`,
`{instruction}`. There is no raw string-concat path.

`LLMBehavior.build_prompt(event, graph)` returns an `AssembledPrompt`
without making an API call. Public for debugging.

## v0.6 #7. Determinism mode is best-effort

`deterministic=True` forces `temperature=0` and `top_p=1`. The
`AssembledPrompt.deterministic` field is included in the hash so a
deterministic and a stochastic prompt with otherwise-identical
content hash differently. Anthropic's messages API has no `seed`
parameter; provider-side response drift remains best-effort.

This narrows but does not close the payload-drift limitation deferred
from v0.5 #7 — see v0.6 #22.

## v0.6 #8. Replay LLM cache

Content-keyed (`sha256(canonical_json({model, system, messages,
output_schema_name, output_schema_json, max_tokens, temperature,
top_p, deterministic}))`). The originating `llm.requested` event id
is stored alongside cached entries for trace lineage, but is NOT the
lookup key — cross-fork prompt-match requires content equality.

Population paths:
- `Runtime.load(..., replay_llm_cache=True)` — pre-populates from
  the loaded log's `llm.responded` events.
- `runtime.fork(at_event, ..., replay_llm_cache=True)` — pre-populates
  from the **parent's** full event log (not the fork's, which only
  has events up to and including `at_event`).
- `_verify_replay` — always populates the cache implicitly when
  `replay_strict=True`, regardless of the user-facing flag.
  Verification must not hit the live API.

Cached responses re-validate against the behavior's `output_schema`
before being handed to the handler (the round-trip through JSON
turns Pydantic instances into dicts).

### DIFF: prompt-hash mismatch under replay_strict

If a recorded `llm.requested` carries a `prompt_hash` that the
re-assembled prompt does NOT produce, the runtime raises
`ReplayDivergenceError(event_id=<new llm.requested id>,
expected="prompt_hash=<recorded>", actual="prompt_hash=<rebuilt>")`.
Same divergence-pinning pattern as v0.5 #7's event-stream comparison.
The cache cannot silently paper over genuine non-determinism in
prompt construction.

## v0.6 #9. Cost accounting in `Decimal`

`max_cost_usd` is tracked in `Decimal`; other budget dimensions stay
as floats. Pre-call estimate is conservative: input tokens (from
`provider.count_tokens(...)`) + `max_tokens` reservation as the
output estimate. If it would exceed the cap, the call is not made
and `behavior.failed reason="budget.cost_exhausted"` fires. After
the call, the actual usage cost (from `response.cost_usd`) is added
to `budget.cost_used`, replacing the conservative estimate.

`budget.snapshot()` exposes `cost_used_usd` and `cost_limit_usd` as
Decimal strings so consumers can choose precision.

## v0.6 #10. Token counting uses the provider's tokenizer

Anthropic's `count_tokens` is a network roundtrip. We only pay for it
when:
  (a) `budget.max_cost_usd` is set, AND
  (b) no cached response was found for this prompt hash.

Cache-hit paths skip token counting (decision-4 adjustment).
Budget-less runs skip it (no ceiling to enforce). Approximate
character-count estimation is forbidden for billing decisions.

## v0.6 #11. Failure modes are explicit `behavior.failed` events

No silent retries. No hidden backoff inside the provider. The
`behavior.failed` payload gains an optional `reason` field; absent
for v0/v0.5-shaped failures, set for LLM failures:

| `reason`                    | Source                                                 |
|-----------------------------|--------------------------------------------------------|
| `llm.network_error`         | Connection error / timeout                             |
| `llm.rate_limited`          | 429-shaped error; `retry_after_seconds` if available   |
| `llm.parse_error`           | Provider returned text with no parseable JSON          |
| `llm.schema_violation`      | Pydantic rejected the JSON                             |
| `llm.fixture_missing`       | `RecordedLLMProvider` had no fixture for the prompt    |
| `llm.prompt_assembly_error` | Prompt construction itself raised (rare)               |
| `budget.cost_exhausted`     | Pre-call estimate would exceed `max_cost_usd`          |

Wrappers signal these via `LLMBehaviorError(reason, message,
payload_extras=...)` which the runtime catches and merges into the
`behavior.failed` payload. Other exception types fall through to
CONTRACT #13 unchanged (no `reason`).

### DIFF: CONTRACT #13

CONTRACT #13's payload becomes a superset: original keys plus
optional `reason` plus optional `payload_extras` keys (free-form,
LLM-specific). No v0/v0.5 emissions change shape.

## v0.6 #12. Recorded-LLM test mode

`RecordedLLMProvider(fixtures_dir)` reads `{prompt_hash}.json`
fixtures and serves them on prompt-hash hits; misses raise
`LLMBehaviorError(reason="llm.fixture_missing")`. No silent
fallthrough to a real call.

`RecordingLLMProvider(inner, fixtures_dir)` wraps another provider
and writes each response to disk. Use once with a real provider
(under `pytest.mark.records_llm` opt-in) to seed fixtures; commit
the resulting files; run thereafter against `RecordedLLMProvider`.

### Fixture file format

```json
{
  "prompt_hash": "<sha256_hex>",
  "recorded_at": "2026-05-15T10:32:01Z",
  "model": "claude-sonnet-4-5",
  "prompt":   { ... only this hashes ... },
  "response": { ... what the provider returned ... }
}
```

`recorded_at` is intentionally OUTSIDE the hashed `prompt` payload so
it doesn't perturb lookups but stays available when fixtures drift
(decision-3 adjustment).

## v0.6 #13. View serialization format is part of the contract

```
## Graph context (depth=2, around=document#1)

### Objects
- document#1 (document): {...canonical JSON of o.data...}
- claim#2 (claim): {...}

### Relations
- claim#2 --supports--> document#1

### Recent events
- evt_017 object.created document#1
- evt_018 relation.created claim#2 --supports--> document#1
```

Object data is serialized with `json.dumps(sort_keys=True)` so byte-
stability holds. Empty sections render as `- (none)`. Snapshot-tested
in `tests/test_llm_prompt.py`. Changing the format is a breaking
change.

## v0.6 #14. Trace format addition for LLM events

```
[llm.requested]           evt_NNN  <behavior>  model=<m> tokens_in~NNN budget_remaining=$X.XXX
[llm.responded]           evt_NNN  <behavior>  tokens_in=NNN tokens_out=NN cost=$X.XXX latency=X.Xs
```

- `~` prefix marks estimates; bare numbers are actuals.
- `tokens_in~` and `budget_remaining=$...` are omitted when no
  `max_cost_usd` is set (no pre-call estimate happened).
- Cache hits render with `cache_hit=true` and omit `cost`/`latency`.

Snapshot-tested in `tests/test_llm_trace_snapshot.py`. v0 and v0.5
snapshot tests stay green.

## v0.6 #15. Provenance gets `llm_request_event_id`

Objects, relations, and patches created inside an `@llm_behavior`
handler carry `provenance.llm_request_event_id` pointing at the
`llm.requested` event that produced them. Auto-stamped by the
runtime via `BehaviorGraph._llm_request_event_id`; behaviors cannot
set it. Absent on objects/relations/patches created outside an LLM
handler (CONTRACT #5 stays backward-compatible).

`trace.causal_chain(object_id)` walks through this link first when
present, rendering the LLM round-trip (`llm.requested` with model,
`llm.responded` with cost or `cache_hit`) before continuing up the
`caused_by` chain.

## v0.6 #16. The killer demo is `examples/llm_claim_extraction.py`

Written first, locked the API. Three documents, structured-output
extraction, a low-confidence flag, a relation behavior tagging
source docs, a cached fork with `replay_llm_cache=True`, and a
causal chain that crosses the LLM boundary. Integration-tested in
`tests/test_llm_claim_extraction.py`.

## v0.6 #17. Backward compatibility

All v0 and v0.5 tests pass unchanged. `Runtime(graph)` without
`llm_provider=` works exactly as before — the new
`MissingProviderError` only fires at `_ensure_registry()` time when
an `@llm_behavior` is in the registry but no provider was passed.
v0/v0.5 trace snapshots stay byte-identical.

## v0.6 #18. Fork re-queues unfired events (extension of v0.5 #8)

In v0.5, `_requeue_unfired` ran only on `Runtime.load`. v0.6 extends
it to `runtime.fork` too — without it, forking at an early event
(e.g. `goal.created`) followed by `run_until_idle` would be a no-op
because no downstream behaviors had a chance to fire on the seed
event. The invariant from v0.5 diff #8 (single-threaded loop, no
partial fanout) still holds; behavior here is the natural
generalization.

## v0.6 #19. Out of scope

- Streaming responses (v0.7+)
- Tool use / function calling inside LLM behaviors (v0.7, separate primitive)
- Multi-model routing / fallback (v0.7+)
- Prompt caching as a first-class concern (v1+; we don't actively
  fight it but the cache key includes everything that affects
  output, so we don't get free cross-call sharing either)
- Cypher pattern subscriptions (v0.7)
- Packs (v1.0)

If a v0.6 PR drifts toward any of these, reject.

## v0.6 #20. API-first development

`examples/llm_claim_extraction.py` was written before any of the LLM
runtime existed. Same discipline as v0 #17 and v0.5 #20.

## v0.6 #21. Tests don't hit the live API

CI runs the suite without `ANTHROPIC_API_KEY` and never makes a real
call. `tests/_llm_helpers.py` provides `ScriptedProvider` and
`FailingProvider`; `tests/test_llm_anthropic.py` mocks the SDK
client. `RecordedLLMProvider` is the production fixture path.

## v0.6 #22. KNOWN LIMITATION (narrowed, not closed): replay payload drift

v0.5 #7's known limitation said replay-strict catches categorical
non-determinism (different event types, different counts) but not
payload-only drift. v0.6 narrows the gap on both axes:

- **Event-type stream check** stays in force (v0.5 #7).
- **Prompt-hash check** is added (v0.6 #8 DIFF): the runtime detects
  divergence at prompt-construction time, pinned to the offending
  `llm.requested` event id. This catches drift from non-deterministic
  view assembly, frame mutation between runs, schema rebuilds, etc.

What remains best-effort:

- **Provider-side response drift.** Anthropic's messages API has no
  seed parameter, so even with `temperature=0` two identical calls
  are not guaranteed to be bit-identical. The replay cache works
  around this in practice (forks read cached responses, not fresh
  calls), but `replay_strict=True` against a run that lacks cached
  responses for every LLM call still cannot catch sub-prompt drift.

The honest framing: drift is now detected at prompt-construction
time; provider-side response drift remains best-effort. Revisits
with v0.7 multi-provider routing, when the question becomes
unavoidable.

---

# Active Graph v0.7 — Tools + advanced matching addendum

**Review status (2026-05-19):** STILL ACCURATE. #18's per-line
`prompt_normalized=true` is hidden behind v0.9.1 #2's rollup; the
underlying contract about the flag's existence is preserved.

v0.7 makes tool use a first-class primitive (not buried inside LLM
behaviors) and lands Cypher-style pattern subscriptions plus
event-count temporal predicates. The two are bundled because they
share a hard problem: non-determinism the runtime didn't cause. A
tool call hits an external system whose response can change between
runs; a pattern subscription fires based on graph shape that depends
on past behavior. Both stress the replay and audit guarantees in
similar ways. Solving them together produces a coherent v0.7.
v0/v0.5/v0.6 decisions all remain in force; the items below are
additions and a handful of explicit narrowings.

## v0.7 #1. Tools are not LLM behaviors

Tools are a primitive. `@tool` registers a callable with metadata and
schemas; the runtime invokes them via an event pair
(`tool.requested` / `tool.responded`) the same way LLM calls flow
through `llm.requested` / `llm.responded`. An LLM behavior that uses
tools is composing two primitives: an LLM call and one or more tool
calls. The runtime orchestrates the loop between them. This is what
keeps tool-using behaviors auditable.

## v0.7 #2. `@tool` and the global tool registry

```python
@tool(
    name="web_fetch",
    description="...",
    input_schema=WebFetchInput,
    output_schema=WebFetchOutput,
    cost_per_call=Decimal("0.001"),
    timeout_seconds=10.0,
    deterministic=False,
)
def web_fetch(args, ctx): ...
```

Decorator pushes into a module-level `_TOOL_REGISTRY` parallel to the
behavior registry. Tests call `clear_tool_registry()`. `Runtime(graph,
tools=[...])` overrides the global registry, mirroring `behaviors=[...]`.
Each LLM behavior's `tools=[t1, t2, ...]` is resolved at startup;
missing tools raise `MissingToolError` at `_ensure_registry()` time.

`Tool.fn(args: InputSchema, ctx: ToolContext) -> OutputSchema` is the
signature. Inputs are validated before invocation; outputs after.
Mismatches map to `tool.invalid_input` / `tool.invalid_output`.

## v0.7 #3. Tool invocation is an event pair

`tool.requested` payload: `behavior`, `tool`, `args_hash`, `args`
(canonicalized), `call_id` (LLM-provided), `cache_hit`,
`deterministic`. `tool.responded` payload: same plus `output`,
`error` (None on success, `{reason, message, ...}` on failure),
`latency_seconds`, `cost_usd`. Cache key:
`sha256(canonical_json({tool_name, args_normalized}))`.
Args normalization: Pydantic models → `model_dump(mode="json")`,
Decimal → str, dicts sorted by key at JSON-dump time.

## v0.7 #4. LLM ↔ tool turn loop is the runtime's job

When an `@llm_behavior` declares `tools=[...]`, the runtime owns the
multi-turn loop:

1. Call LLM with tools in the request
2. If response carries `tool_calls`, dispatch each: emit
   `tool.requested`, invoke, emit `tool.responded`, append result as
   `role="tool"` message
3. Re-call LLM
4. Repeat until non-tool response or `max_tool_turns` (default 6) hits

The handler signature stays `(event, graph, ctx, llm_output) -> None`.
Handler sees only the final parsed output — never raw tool calls.

`LLMMessage` gains `role="tool"` and `tool_use_id` + `tool_name`
fields so providers can echo results back. `LLMResponse` gains
`tool_calls: Optional[list[ToolCall]]`. `LLMProvider.complete()` gains
`tools: Optional[list[dict]] = None`. All backward-compatible: omitted
defaults preserve v0.6 behavior.

## v0.7 #5. Tool context is restricted

```python
@dataclass
class ToolContext:
    behavior_name: str
    event_id: str
    frame: Optional[Frame]
    idempotency_key: str
    timeout_seconds: float
    logger: logging.Logger
```

NO graph reference. Tools that need graph read access close over a
`Graph` via a factory — see `make_graph_query_tool(graph)`. The
constraint is intentional: tools that touch the graph should be
obvious, not ambient. `idempotency_key` is an opaque pass-through:
tool authors forward it to external APIs that support idempotency
tokens; the runtime never uses it for dedupe (that's the cache's job).

Tools cannot mutate the graph directly. If a tool wants to record
information, it returns it in its output and the calling behavior's
handler writes the mutation. Same constraint as `BehaviorGraph` for
behaviors: actions are explicit, mutations are auditable.

## v0.7 #6. Tool failure modes are structured

Mirror of v0.6 #11. `ToolError(reason, message, payload_extras)` is
the carrier; the runtime maps it into `tool.responded.error` AND
`behavior.failed.reason`. Codes:

- `tool.timeout` — exceeded `timeout_seconds`
- `tool.network_error` — provider / network failure
- `tool.invalid_input` — input schema validation failed
- `tool.invalid_output` — output schema validation failed
- `tool.execution_error` — tool function raised
- `tool.unknown_tool` — LLM asked for a tool the behavior didn't declare
- `tool.fixture_missing` — `RecordedToolProvider` had no fixture
- `tool.max_turns_exhausted` — exceeded `max_tool_turns`
- `budget.tool_calls_exhausted` — would exceed `max_tool_calls`
- `budget.cost_exhausted` — tool cost would exceed `max_cost_usd`

No silent retries. If a developer wants retry behavior, they subscribe
to `behavior.failed`.

## v0.7 #7. Tool determinism — DECISION: serve-from-cache by default

**Locked decision (per push-back exchange):**

ALL tools (deterministic or not) serve from cache by default on
replay. The opt-in `Runtime(replay_reinvoke_deterministic=True)`
flag is what actually lets deterministic tools re-invoke during
replay.

The reasoning: even a deterministic tool's correctness depends on
the reconstructed graph state matching the recorded state at the
moment of the call. The runtime cannot cheaply verify that without
doing essentially the same work as the cache lookup. Cheaper and
more honest to cache-by-default; `replay_reinvoke_deterministic`
stays available for the "would this still hold?" workflow.

A divergent prior event poisons every deterministic-tool call after
it — by serving from cache by default we localize the failure to a
visible cache mismatch rather than a silent re-invocation against
wrong inputs.

## v0.7 #8. Cypher subset — LOCKED grammar

A strict subset, refused with `UnsupportedPatternError` pointing at
the offending token. The full subset:

**Supported**:
- Node patterns: `(var:type {prop: value})` — properties are EQUALITY ONLY
- Relationships: `(a)-[var:rel_type]->(b)` and `(a)<-[var:rel_type]-(b)`
- Multi-hop linear chains: `(a)-[:r1]->(b)-[:r2]->(c)`
- WHERE clauses with `AND` and `NOT`
- `NOT EXISTS { sub_match }`
- Property access: `a.confidence > 0.7` (auto-routes through `a.data.<field>`)
- Comparison operators: `=`, `<`, `>`, `<=`, `>=`, `!=`, `<>`
- Literals: int, float, string (single or double quoted), `TRUE`,
  `FALSE`, `NULL`
- Relationship variable binding `[r:type]` — included per push-back
  exchange because it's cheap and a contradiction-handler that wants
  to delete the relation needs it

**Refused** (with token-pinned error):
- `OR` in WHERE — register two behaviors instead. Disjunction makes
  matchers either back-track or eval-both-paths; register-two is
  cleaner.
- `RETURN` — patterns produce bindings via `ctx.matches`, not via a
  RETURN clause; precise parser error saves an hour of confusion
- `OPTIONAL MATCH`, `WITH`, `UNION`, `UNWIND`, `MERGE`, `CREATE`,
  `SET`, `DELETE`, `DETACH`, `REMOVE`, `FOREACH`, `CALL`, `LIMIT`,
  `SKIP`, `ORDER`
- Variable-length paths (`-[*]-`)
- Aggregation
- Subqueries beyond `NOT EXISTS`
- Undirected edges (`(a)-[:r]-(b)`) — use a directed shape
- Edges without a type (`(a)-[]->(b)`)
- Node `{prop: value}` with non-literal values — comparisons go in WHERE

## v0.7 #9. Pattern compilation happens at registration

The decorator parses + compiles immediately. Invalid syntax fails at
import time, not at runtime. The compiled `PatternMatcher` is stored
on the behavior dataclass; `runtime.Registry.match()` calls
`matcher.matches(event, graph)` on every dispatch.

## v0.7 #10. Pattern evaluation bound — DECISION: live-only

Wall-clock budgets on replay produce spurious divergence on slower
CI. Pattern-evaluation budgets are enforced live only, skipped on
replay. This matches how any timeout should be handled on replay —
replay is not subject to live-time constraints because the events
already happened. v0.7 does not actually ship a configurable
`max_pattern_evaluation_ms` knob; instead, evaluation is bounded by
the natural graph size and complexity. If pattern evaluation becomes
an actual bottleneck before v1, we add the knob (and document it as
live-only).

## v0.7 #11. Behaviors can mix event-type and pattern subscriptions

With both `on=[...]` and `pattern=...`, BOTH must hold. Pattern-only
behaviors (no `on=`) check every non-lifecycle event. Lifecycle
events (`behavior.*`, `relation_behavior.*`, `runtime.*`, `llm.*`,
`tool.*`, `pattern.*`) are excluded from pattern-only checks —
otherwise patterns would re-fire endlessly on their own scheduling
events.

Either-or semantics? Register two behaviors. Don't add OR to the
pattern grammar.

## v0.7 #12. Pattern matches are passed via `ctx.matches`

`ctx.matches: list[Match]` where each `Match` binds variable names to
object ids (for nodes) or relation ids (for `[r:type]` rels). Empty
list for behaviors without a pattern. **Fire-once-per-event**, not
once per match — iterating bindings is the developer's job.

## v0.7 #13. `activate_after` — LOCKED: event-count only

```python
@behavior(on=["object.created"], activate_after=2)        # int
@behavior(on=["object.created"], activate_after="2 events")  # string form
```

Per the push-back exchange: event-count only for v0.7. Wall-clock
delays drag in a clock-source abstraction (full subsystem), break
determinism under replay, and change nothing about the demos.
Event-count composes with the tick model and replays for free.

Wall-clock unit strings (`"5 minutes"`, `"2 seconds"`, etc.) raise
`ValueError` at decoration with a pointer to this contract item.

The runtime maintains a `DelayedQueue` alongside the main FIFO. On
schedule: emit `behavior.scheduled` and push an entry tagged with
`fire_at_event_count = current_tick + N`. The main loop drains the
queue first, then checks the delayed queue for due entries.

**At fire time, `where=` is re-checked against the current graph
state.** If it no longer holds, the invocation is silently skipped
(no extra event). The trace shows the original `behavior.scheduled`
without a matching `behavior.started` — sufficient evidence the
condition lapsed.

Escape hatch for wall-clock: the user calls `runtime.tick()` from
their own loop and injects `timer.fired` events on a wall-clock
schedule. v1+ extension point if anyone actually needs it.

## v0.7 #14. Tool budget integration

`max_tool_calls: int | None` added to known budget keys (already
shipped as a stub in v0.6). When a tool call would exceed, the tool
is not invoked, `behavior.failed reason="budget.tool_calls_exhausted"`
fires, the loop continues. `max_cost_usd` is shared between LLM and
tool costs — they're both dollars.

`_budget_reason()` maps internal limit names (e.g. `max_tool_calls`)
to v0.7 reason codes (`budget.tool_calls_exhausted`) so users see the
consistent vocabulary instead of internal keys.

## v0.7 #15. Recorded tool mode

`RecordedToolProvider(fixtures_dir)` reads `<tool_name>/<args_hash>.json`
and serves on hit; misses raise `ToolError(reason="tool.fixture_missing")`.
`RecordingToolProvider(inner_invoker, fixtures_dir)` wraps another
invoker and persists each response as a fixture. Use once with
`@pytest.mark.records_tools` opt-in to seed fixtures, then commit and
run thereafter against `RecordedToolProvider`.

Fixture file shape — `recorded_at` OUTSIDE the hashed body, same as
v0.6 LLM fixtures:

```json
{
  "tool":        "web_fetch",
  "args_hash":   "<sha256_hex>",
  "recorded_at": "2026-05-15T10:32:01Z",
  "args":        { ... only this contributes to the hash ... },
  "output":      { ... },
  "error":       null,
  "latency_seconds": 0.8,
  "cost_usd":    "0.001"
}
```

## v0.7 #16. Two reference tools

- `web_fetch` (`activegraph.tools.web_fetch`) — stdlib `urllib` HTTP
  GET. No third-party dependency. `deterministic=False`. Production
  use should generally wrap a real HTTP client.
- `graph_query` — built via `make_graph_query_tool(graph)` factory.
  Operates on the graph itself through the same event-sourced
  invocation path as any other tool. Marked `deterministic=True`
  (subject to the v0.7 #7 replay caveat).

`graph_query` is the demonstration that the tool primitive is
general — not just an "external API" escape hatch.

## v0.7 #17. Demo is the contract

`examples/diligence_with_tools.py` was written before any of the
v0.7 runtime existed. Same discipline as v0 #17, v0.5 #20, v0.6 #16.
The demo exercises:

- `@behavior` planner that bootstraps the run
- `@llm_behavior` `question_generator` (no tools)
- `@llm_behavior` `researcher` (tools=[web_fetch, graph_query])
- pattern-subscribed `@llm_behavior` `critic` for contradictions
- `@behavior` `nag` with `activate_after=2`
- save + fork with `replay_llm_cache=True` AND `replay_tool_cache=True`
- causal chain that crosses both LLM and tool boundaries
- two traces printed: parent with live calls, fork with all-cached

## v0.7 #18. Trace format additions

New `[...]` tags, all snapshot-tested:

```
[tool.requested]      evt_NNN  behavior  tool=name args_hash=AAA cache_hit=false deterministic=true
[tool.responded]      evt_NNN  behavior  tool=name latency=X.Xs cost=$X.XXX
[pattern.matched]     evt_NNN  behavior  matches=N
[behavior.scheduled]  evt_NNN  behavior  activate_after=N_events
```

`args_hash` is truncated to the first 8 hex chars in the trace for
readability (full hash is in the event payload). Cache hits omit
`latency` and `cost`. Tool errors render with `error=tool.<reason>`.

`[llm.requested]` gains `turn=N` (omitted for turn 0) and
`prompt_normalized=true` (the v0.6 follow-up: surfaces that
volatile-field stripping ran in prompt assembly, so when fork-cache
behavior surprises someone six months from now the trace tells them
what happened without them having to read the assembler source).

## v0.7 #19. Provenance gets `tool_request_event_ids`

When an object/relation/patch is created inside an `@llm_behavior`
handler whose turn loop invoked tools, its provenance includes
`tool_request_event_ids: list[str]` enumerating every `tool.requested`
event that contributed to the final LLM output. Auto-stamped by
`BehaviorGraph._tool_request_event_ids`; behaviors cannot set it.

`trace.causal_chain(object_id)` walks the LLM round-trip first
(`llm.requested` + `llm.responded`), then enumerates each tool
round-trip (`tool.requested` + `tool.responded`), then continues up
the `caused_by` chain to the source event. One walk: full lineage
from a claim → LLM call → every tool call → source documents → goal.

## v0.7 #20. Per-turn LLM/tool cache with ordering verification

Per push-back agreement: per-turn cache (not consolidated). The
sequence `(llm_response, tool_call, tool_response, llm_response, ...)`
is itself a small state machine. Strict-replay verifies each turn's
prompt hash against the recorded sequence; mismatch raises
`ReplayDivergenceError` pinned to the offending `llm.requested` event
id (same divergence-pinning pattern as v0.6 #8).

Per-turn keys preserve the fork-and-mutate-one-tool workflow.
Consolidated keys would lock the loop into all-or-nothing replay.

## v0.7 #21. LLM cache round-trips `tool_calls`

`LLMCache.get()` / `record()` / `from_events()` all preserve
`tool_calls` so a cached LLM response produces the same turn-loop
shape as a live one. Without this, a fork's cached first turn would
not trigger the tool re-dispatch and would fail with
`llm.parse_error`. Test coverage: `tests/test_tool_replay.py`.

## v0.7 #22. Backward compatibility

All v0 / v0.5 / v0.6 tests pass unchanged (147 of them). v0/v0.5/v0.6
trace snapshots stay byte-identical EXCEPT the v0.6 `llm.requested`
line, which gains the trailing `prompt_normalized=true` flag (the
v0.6 follow-up bundled into v0.7). The v0.6 snapshot was updated
in the same commit; the README's expected v0.6 trace block is updated
to match.

`Runtime(graph)` without `tools=` or `@tool` decorators behaves
exactly as v0.6 did. The new `MissingToolError` only fires when an
`@llm_behavior` declares a tool the runtime cannot find at startup.
`LLMProvider.complete(..., tools=None)` is the default — providers
that ignore the kwarg keep working.

## v0.7 #23. Out of scope

- Streaming LLM responses (v0.8+)
- Multi-model routing / fallback (v0.8+)
- Real-time graph subscriptions for external UIs (v1.0+)
- Distributed runtime (v1.0+)
- Packs (v1.0)
- Wall-clock `activate_after` (v1+ if anyone actually needs it)
- `OR` in pattern WHERE clauses (v1.0+ once indexing makes it cheap)
- Variable-length paths in patterns (v1.0+)
- Configurable `max_pattern_evaluation_ms` knob (added when an actual
  bottleneck shows up)
- Tool implementations beyond `web_fetch` + `graph_query`

If a v0.7 PR drifts toward any of these, reject.

## v0.7 #24. API-first

`examples/diligence_with_tools.py` was written before any of the
runtime existed. Same discipline as v0 #17, v0.5 #20, v0.6 #16.

## v0.7 #25. Tests don't hit live anything

CI runs the suite without `ANTHROPIC_API_KEY` and without network
access. No test makes a real LLM call or a real HTTP fetch.
`RecordedLLMProvider` + `RecordedToolProvider` are the production
fixture paths. The `@tool web_fetch` reference implementation uses
stdlib `urllib` so it's not a network dependency at import time, but
no test actually calls it against a URL — the demo's scripted
provider serves canned URL responses.


---

# Active Graph v0.8 — Persistence, observability, operator surface

**Review status (2026-05-19):** STILL ACCURATE. #6 structured
logging schema (`error_type` / `error_message` field names) is the
canonical operator contract; the v1.0.3 #3 `BehaviorFailure`
NamedTuple uses Python-conventional names
(`exception_type` / `message`) — intentional divergence, filed for
docs clarification as v1.0.4 candidate #3. #11 RuntimeStatus shape
extended additively in v1.0.3 #3 (`Runtime.errors` property, not a
frozen-dataclass field). #19 out-of-scope list is historical (same
pattern as v0 #16); items have shipped in subsequent milestones.

v0.8 is the milestone where the framework stops adding capability and
starts hardening the boundary between the runtime and the world it has
to live in. Three concerns: persistence beyond SQLite (Postgres),
operator-grade observability (structured logs + metrics), and an
operator-facing CLI. Backward compatibility with v0–v0.7 is absolute.

## v0.8 #1. PostgresEventStore mirrors SQLiteEventStore

Same schema (`events`, `runs`, `meta`), same protocol, same semantics
including `UNIQUE(id, run_id)`. Postgres-native types where they
matter: `BIGSERIAL` for `seq`, `TIMESTAMPTZ` for timestamps, `JSONB`
for `payload`. Schema version is stored in `meta` and verified on
every open; a mismatch is a hard error.

The EventStore protocol is the contract. The two implementations are
interchangeable behind it. No Postgres-specific methods leak through.

## v0.8 #2. Stores are addressed by connection URL

The framework addresses stores by URL everywhere (runtime, CLI,
library APIs). The schemes follow the SQLAlchemy convention:

- `sqlite:///relative/path.db` (three slashes — relative path)
- `sqlite:////absolute/path/to/run.db` (four slashes — absolute)
- `postgres://user:pass@host:port/dbname`
- `postgresql://user:pass@host:port/dbname` (same scheme)

A URL with no scheme is rejected with a message pointing at the right
form. `activegraph inspect run.db` fails with "use sqlite:///run.db";
the framework will not silently guess. `Runtime.load(path)` accepts
either a URL or a bare SQLite path (the v0.5–v0.7 sugar form) so
existing call sites are unchanged.

`open_store(url, run_id)` is the single library entry point that
dispatches on scheme; the Postgres dependency stays optional via lazy
import.

## v0.8 #3. Connection management is the user's job

`PostgresEventStore` accepts a URL string (opens a dedicated
connection), an existing `psycopg.Connection` (the store does not own
lifecycle), or a `psycopg_pool.ConnectionPool` (the store does
`getconn` / `putconn` per operation).

The framework does NOT ship its own pool. Production users pass
`psycopg_pool.ConnectionPool` with tuning they own. This keeps the
framework's dependency surface tiny and avoids prescribing pool
parameters we are not in a position to choose.

Pinned: `psycopg>=3.1,<4`. Async is supported by psycopg 3 but the
v0.8 runtime is sync; async is a v1+ conversation.

## v0.8 #4. The event log is not queryable through the framework

JSONB is queryable in Postgres, but we do not expose
`EventStore.query_by_payload(...)`. The temptation is real; resist it.
The event log is append-only and read-sequentially from the framework's
side. Payload querying is a database concern. Users who want to run
Postgres queries against the JSONB column directly are welcome to;
the framework does not help.

## v0.8 #5. Migration is transaction-per-run, idempotent, one-directional

`activegraph migrate --from <url> --to <url>` copies runs from source
to destination with these rules:

- Each run migrates in **a single destination transaction**. A failure
  mid-run rolls that run's destination state back. The destination
  never holds a partially-written run.
- Writes are idempotent at the event level via
  `INSERT ... ON CONFLICT (id, run_id) DO NOTHING`. Re-running
  migration after a partial failure resumes safely.
- Runs migrate **independently**. A bad run does not block the others.
  The CLI prints a per-run report; `--json` emits the same data
  machine-readably.
- Migration is **one-directional**. No `sync` mode, no rollback,
  no bidirectional reconciliation. To go back, migrate the other way.

This revises the earlier "copy + verify count" design. Verify-count
without a transaction is meaningless: both sides of the comparison
are computed against the broken intermediate state. The transaction-
per-run model is strictly better and not more complex.

## v0.8 #6. Structured logging schema

Every log line is a single JSON object on one line. Fields that don't
apply are **omitted, not nulled**. The schema is the operator
contract; dashboards built against these field names keep working
across framework versions.

Schema (`LOG_FIELDS` in `activegraph/observability/logging.py`):

  timestamp, level, logger, message, run_id, event_id, behavior,
  tool, model, cache_hit, cost_usd, latency_seconds, reason,
  error_type, error_message

The framework does NOT auto-configure logging on import. By default
it logs to `logging.getLogger("activegraph")` and lets the user's
config handle output. `configure_logging(level, json_output=True,
payload_redactor=None)` is the opinionated opt-in.

Adding a field is a schema-version change. Removing or renaming a
field is a breaking change.

## v0.8 #7. Logging level discipline

DEBUG    — view construction, prompt assembly, cache lookup, queue ops.
INFO     — every event emitted, behavior invoked, tool call.
WARNING  — budget approaching limits, retries, pattern eval slowness.
ERROR    — `behavior.failed` with non-budget reasons.
CRITICAL — event log inconsistency, schema mismatch, replay divergence.

No `print()` calls anywhere in the framework. The trace printer is a
developer tool; it writes to stdout and does not log.

## v0.8 #8. Metrics protocol is three methods

    class Metrics(Protocol):
        def counter(self, name, tags, value=1.0): ...
        def histogram(self, name, tags, value): ...
        def gauge(self, name, tags, value): ...

No timers (use a histogram with a latency value). No summaries
(Prometheus-specific). No custom types. Three methods is the entire
surface custom backends implement.

Standard tag keys: `event_type`, `behavior`, `tool`, `model`,
`reason`, `run_id` (gauges only — see #C4).

## v0.8 #9. Standard metrics (operator contract)

The runtime emits this fixed set of metrics. Adding a metric is a
public API change; the table is documented in `docs/operating.md` and
test-pinned in `activegraph/observability/metrics.py::METRIC_NAMES`.

Names follow Prometheus conventions natively (`_total` for counters,
`_seconds` for duration histograms, `_usd` for currency histograms).
This avoids the `prometheus_client` silent dot-to-underscore renaming
that would otherwise make documented names diverge from exported
names.

Counters:
  activegraph_events_emitted_total{event_type}
  activegraph_behaviors_invoked_total{behavior}
  activegraph_behaviors_failed_total{behavior, reason}
  activegraph_llm_calls_total{model}
  activegraph_llm_cache_hits_total{model}
  activegraph_llm_failed_total{model, reason}
  activegraph_tools_calls_total{tool}
  activegraph_tools_cache_hits_total{tool}
  activegraph_tools_failed_total{tool, reason}
  activegraph_patterns_evaluated_total
  activegraph_replay_divergence_detected_total{reason}

Histograms:
  activegraph_behaviors_duration_seconds{behavior}
  activegraph_llm_tokens_in{model}
  activegraph_llm_tokens_out{model}
  activegraph_llm_cost_usd{model}
  activegraph_tools_duration_seconds{tool}
  activegraph_patterns_evaluation_duration_seconds

Gauges:
  activegraph_queue_depth
  activegraph_budget_cost_remaining_usd{run_id}
  activegraph_budget_events_remaining{run_id}

Note: cache hits are a **separate counter**, not a `cache_hit=true|false`
tag on the unified call counter. This lets dashboards do
`rate(cache_hits) / rate(calls)` as a one-line query instead of
filtering by tag value. Success/failure follow the same pattern —
separate counters, no `success` tag.

## v0.8 #C4. The run_id cardinality rule (locked)

> `run_id` MAY appear as a tag on **gauges of active state** (where
> cardinality is bounded by the number of concurrently active runs).
> `run_id` MUST NOT appear as a tag on **counters or histograms**.

This rule prevents the most common Prometheus operational disaster:
unbounded cardinality from per-run labels accumulating forever. The
budget gauges are the only exception, and they live only for the
duration of a run.

The rule is enforced by `validate_cardinality_rule()` which runs at
import time and in the test suite (`test_observability_metrics.py`).
Any standard metric whose tag set violates the rule fails the build.

## v0.8 #10. NoOpMetrics is the default; PrometheusMetrics is opt-in

`NoOpMetrics` does nothing — three method bodies, each a single
`return`. The runtime is fully functional without metrics.

`PrometheusMetrics` lazy-imports `prometheus_client` (opt-in dep via
`activegraph[prometheus]`). Custom backends — OpenTelemetry, Datadog,
statsd — implement the three-method protocol directly. The framework
does not ship adapters.

## v0.8 #11. runtime.status() shape

    @dataclass(frozen=True)
    class RuntimeStatus:
        run_id: str
        state: Literal["idle", "running", "stopped", "exhausted"]
        queue_depth: int
        events_processed: int
        budget: BudgetSnapshot
        frame: FrameSnapshot | None
        registered_behaviors: tuple[BehaviorInfo, ...]
        recent_events: tuple[EventSummary, ...]

`status(recent: int = 20)` — single parameter controls the tail
length. The CLI's `inspect --tail N` passes through.

State is derived **from the event log**, not from in-memory
bookkeeping. This means a freshly-loaded runtime and the runtime that
saved the log agree on state. Walk back through events: if the most
recent terminal lifecycle event is `runtime.budget_exhausted` →
`exhausted`; if it's `runtime.idle` → `idle`; otherwise `stopped`.
`running` is reserved for cross-thread observation of an in-progress
loop (single-threaded today; documented for future async use).

There is **no `last_error` field**. Errors are events; filter
`recent_events` for type `behavior.failed`, or query the event store
directly. Convenience accessors that look like the source of truth
but mean different things are bug-bait.

## v0.8 #12. The CLI is a thin wrapper around library APIs

No business logic in the CLI itself. Each subcommand parses arguments,
calls into the library, and formats output. Programmatic callers do
exactly the same things.

Subcommands:

    activegraph inspect      <url> [--run-id <id>] [--tail N] [--json]
    activegraph replay       <url> --run-id <id> [--json]
    activegraph fork         <url> --run-id <id> --at-event <id>
                                   --label <label> [--to <url>] [--json]
    activegraph diff         <url> --run-a <id> --run-b <id> [--json]
    activegraph export-trace <url> --run-id <id>
                                   [--format text|jsonl] [-o PATH]
    activegraph migrate      --from <url> --to <url>
                             [--run-id <id>] [--json]

Built with `click`. The CLI is a hard dep (`click>=8,<9`) — it is
part of the operator surface.

## v0.8 #13. CLI exit codes (locked)

  0  success
  1  generic error
  2  usage error (bad arguments, missing options — click default)
  3  not found (run id does not exist, store path does not exist)
  4  corruption (schema version mismatch, event log inconsistency)
  5  divergence (replay-strict failure)

Operators and CI systems wrap these. Changing a code is a breaking
change.

## v0.8 #14. Backward compatibility is absolute

- Every v0–v0.7 test passes unchanged.
- Trace printer output is byte-identical for non-LLM, non-tool runs
  (snapshot-tested as before).
- SQLite remains the default; Postgres is opt-in.
- Metrics default to no-op.
- Logging defaults to the user's existing config; the framework adds
  no handlers on import.
- `Runtime`, `Runtime.load`, and `runtime.save_state` accept bare
  SQLite paths exactly as in v0.5+.

The v0.8 stance on event payload schemas: **no new fields on existing
event types**. New event types are fine; mutating existing payloads
breaks every snapshot test and is a v1+ change. None are added in
v0.8.

## v0.8 #15. Logging is opt-in via configuration

A library that auto-configures logging on import is hostile to
operators who have their own setup. The framework attaches to
`logging.getLogger("activegraph")` and propagates to whatever handler
the user installed. `configure_logging(...)` is the explicit opt-in
for the documented JSON schema.

## v0.8 #16. The killer demo is the spec

`examples/operate_a_run.py` was written before any implementation.
The runtime, the CLI, and the operator guide are built backward to
make it run. The example exercises every v0.8 surface:
SQLite-backed run → status → CLI inspect → CLI fork → CLI diff →
JSONL export → optional Postgres migration. The example is
integration-tested (`tests/test_operate_example.py`); if the example
breaks, the test catches it.

## v0.8 #17. EventStoreConformance is the reusable test suite

`activegraph.store.conformance.EventStoreConformance` is a mixin
class. Concrete subclasses override `make_store(run_id)` and
`cleanup()`; the same suite runs against InMemory, SQLite, and
Postgres. Any future store implementation gets free coverage by
subclassing.

## v0.8 #18. Postgres tests are gated, never mocked

`ACTIVEGRAPH_TEST_POSTGRES_URL` env var enables the Postgres
conformance suite. Without it, the tests skip — local dev without
Docker runs the other 320+ tests unaffected. Mocking Postgres
produces false confidence; we don't.

## v0.8 #19. What v0.8 deliberately does not add

- Web UI
- HTTP server
- Real-time graph subscriptions (websockets, SSE)
- Distributed runtime
- Multi-model LLM routing
- Streaming LLM responses
- Packs (still v1.0)

Stay scoped. v0.8 is less exciting than v0.6 and v0.7 by design; it
is what makes the framework deployable.

`[review overlay 2026-05-19: packs shipped at v1.0 (the only listed
item that has since landed). Web UI / HTTP server / websocket
subscriptions / distributed runtime / multi-model routing / streaming
LLM responses remain out of scope as of v1.0.4. Preserved as
archeology; see v1.0.4 #5.]`

---

# v0.9 — Pack format + Diligence pack

**Review status (2026-05-19):** STILL ACCURATE. Spot-checked #5
(load-order asymmetry), #10 (prompt hash as replay contract), #13
(`pack.loaded` event); tests in `tests/test_packs_*` anchor on the
contract claims. #26 deferred items (Memory pack, Research pack,
pack registry, signing, sandboxing, adaptive question generation,
contradiction resolver) remain deferred per v1.0 #10's deferral
list and `v1.1-plan.md`'s "What v1.1 deliberately does NOT
include."

v0.9 ships two things that share a single design: the **pack format**
(a way to bundle object types, relation types, behaviors, tools,
prompts, and policies for a domain) and **`activegraph.packs.diligence`**
(the first production-quality pack, evolved from v0.7's diligence
example). The pack format is defined by what the first pack needs;
the first pack is shipped in the same milestone.

Packs were originally on the v1.0 roadmap. They moved forward because
the runtime is now capable enough that the next risk is "no one knows
how to use it well." A pack is the answer to that.

## v0.9 #1. A pack is a Python package, not a manifest file

A pack is a Python package with a known entry point. The "manifest"
is a `Pack` object exported from the package, not a YAML or JSON
file. Packs need to express real logic (behaviors, prompts, policies)
and Python is the right language for that.

```python
# activegraph_diligence/__init__.py
from activegraph.packs import Pack
pack = Pack(
    name="diligence",
    version="0.1.0",
    description="Investment diligence: claims, evidence, contradictions, memos.",
    object_types=[...],
    relation_types=[...],
    behaviors=[...],
    tools=[...],
    policies=[...],
    prompts=[...],
    settings_schema=DiligenceSettings,
)
```

## v0.9 #2. The `Pack` dataclass shape is locked

```python
@dataclass(frozen=True, eq=False)
class Pack:
    name: str
    version: str
    description: str = ""
    object_types: tuple[ObjectType, ...] = ()
    relation_types: tuple[RelationType, ...] = ()
    behaviors: tuple = ()  # Behavior | LLMBehavior | RelationBehavior
    tools: tuple = ()  # Tool
    policies: tuple[PackPolicy, ...] = ()
    prompts: tuple[PackPrompt, ...] = ()
    settings_schema: type = EmptySettings  # Pydantic BaseModel subclass
```

`frozen=True` so packs are immutable after construction. `eq=False`
with explicit `__eq__` / `__hash__` keyed on `(name, version)` —
behaviors and tools are dataclasses, not hashable, so identity must
not depend on them. Idempotent load (#5) hinges on this key.

List arguments are converted to tuples in `__post_init__` via
`object.__setattr__` so authors can pass lists; the field type is
always a tuple at rest.

## v0.9 #3. No module-import side effects

(Revises the original v0.9 plan's per-pack import-time registry.)

A pack module is safe to import without a runtime. Pack-aware
decorators **do not register into any global state** — they attach
metadata to the function and return a `Behavior` / `LLMBehavior` /
`RelationBehavior` / `Tool` object. The `Pack(...)` constructor takes
explicit lists; nothing about a pack module's import has runtime
visibility.

To make this explicit, pack-aware decorators are imported from a
distinct path:

```python
from activegraph.packs import behavior, llm_behavior, relation_behavior, tool
```

These have identical signatures to the `activegraph.*` versions. The
**only** difference is that they skip the global registry. Pack
authors are told: inside a pack module, import decorators from
`activegraph.packs`; in regular user scripts, import them from
`activegraph`.

This was the only viable resolution after rejecting:
  (a) thread-local "pack-definition mode" (fragile),
  (b) Pack.__init__ removing from the global registry (mutates global
      state at construction),
  (c) a single decorator with a `pack=...` kwarg (decorators look
      identical but behave very differently — worse footgun).

The pack authoring guide documents this convention. Tests in
`tests/test_packs_no_side_effects.py` import every pack twice and
assert the global registry stays empty.

## v0.9 #4. Object and relation types are declared

A pack declares the object types it introduces, with their data
schemas as Pydantic `BaseModel` classes:

```python
class Claim(BaseModel):
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = []

object_types = [
    ObjectType(name="claim", schema=Claim, description="A factual statement with confidence."),
]
```

When a pack with object type `claim` is loaded, the runtime validates
data passed to `graph.add_object("claim", data=...)` against `Claim`.
Validation errors raise `PackSchemaViolation` — a subclass of
`ValueError` — and the object is not created.

`RelationType` declares `(name, source_types, target_types,
description)`. `source_types` and `target_types` are tuples of object
type names; empty means "any". Validation is enforced for
`graph.add_relation` against loaded relation types.

## v0.9 #5. Validation is post-load, not retroactive (load-order asymmetry, option (a))

(Decision 5 from the lock-in discussion.)

`graph.add_object` validates against a typed pack's schema ONLY for
objects created **after** the pack is loaded. Objects created before
the pack was loaded are NOT retroactively validated.

The `pack.loaded` event is part of the event log, so replay enforces
the same load order: if a run loads the diligence pack at event
`evt_005`, replay must load the same pack at the same point.
Mismatched load order is a `ReplayDivergenceError`.

Creating a typed object **before** its pack is loaded is allowed —
the type is unknown to the runtime and no schema applies. This is
consistent with backward compatibility (v0–v0.8 had no schemas).

## v0.9 #6. Pack loading is idempotent and conflict-explicit

```python
runtime.load_pack(diligence_pack)
runtime.load_pack(diligence_pack)  # no-op
```

Idempotency key: `(pack.name, pack.version)`. Loading the same
`(name, version)` twice is a no-op. Loading the same `name` with a
different `version` raises `PackVersionConflictError`.

Loading two packs that declare the same object type, relation type,
behavior name (post-prefix), tool name (post-prefix), or policy name
with different definitions raises `PackConflictError` at load time,
naming both packs. No silent overrides. No "last pack wins."

Conflict detection is performed BEFORE any state mutation — a
conflicting `load_pack` call leaves the runtime exactly as it was.

## v0.9 #7. Pack-scoped settings, primary form is typed injection

Every pack declares a `settings_schema` (Pydantic `BaseModel`). The
user provides settings at load time:

```python
runtime.load_pack(diligence_pack, settings=DiligenceSettings(
    llm_model="claude-sonnet-4-5",
    max_claims_per_document=20,
    confidence_threshold_for_review=0.7,
))
```

Three documented access forms, in order of preference:

  1. **Type-annotated parameter injection (primary)** — the runtime
     inspects the handler signature. Extra parameters (beyond
     `event, graph, ctx[, out]`) whose type annotation matches a
     loaded pack's `settings_schema` get the matching instance
     injected by keyword.
     ```python
     def claim_extractor(event, graph, ctx, out, *, settings: DiligenceSettings):
         if out.confidence < settings.confidence_threshold_for_review:
             ...
     ```
  2. **`ctx.settings` (secondary)** — returns the settings instance
     for the pack that owns the currently-executing behavior. Untyped
     and convenient. Behaviors not owned by a pack get `None`.
  3. **`ctx.pack_settings("other_pack")` (cross-pack)** — string-keyed
     lookup of another pack's settings. Returns `None` if the pack
     isn't loaded. Documented as a code smell when used for the
     behavior's own pack; intended for the rare cross-pack case.

If a pack has no configurable settings, `settings_schema =
EmptySettings`. Passing `settings=` for a pack that takes
`EmptySettings` is allowed but ignored.

`runtime.load_pack(pack)` without `settings=` is allowed only if
`settings_schema` accepts construction with no arguments. Otherwise
`PackSettingsMissingError`.

## v0.9 #8. Behaviors are namespace-prefixed: canonical strict, lookup lenient

A behavior declared in the diligence pack with `name="claim_extractor"`
is registered as `diligence.claim_extractor`. The fully-qualified form
is the **canonical** identifier and appears in:

  - the trace (`[behavior.started] diligence.claim_extractor ...`)
  - metrics labels (`{behavior="diligence.claim_extractor"}`)
  - error messages (`PackConflictError: ... diligence.claim_extractor ...`)
  - `runtime.status()`'s `registered_behaviors`
  - the replay manifest

Lookups from user code are **lenient**: a short name resolves when
unambiguous. Since #6 raises `PackConflictError` on duplicate names
at load time, "unambiguous" is a load-time invariant.

```python
runtime.get_behavior("claim_extractor")          # works if unambiguous
runtime.get_behavior("diligence.claim_extractor") # always works
```

The same rule applies to tools: `diligence.fetch_company_docs` is
canonical, short forms work when unambiguous. LLM behaviors with
`tools=["fetch_company_docs"]` resolve the short name through the
same rule.

## v0.9 #9. Pack tools are pack-scoped by default

A tool declared in the diligence pack is registered as
`diligence.fetch_company_docs`. The pack may opt to export a tool
globally with `export_globally=True` on the `@tool` decorator, but
the default is scoped. This prevents pack tools from polluting the
global tool namespace and makes it explicit when a pack provides
infrastructure intended for other packs.

## v0.9 #10. Prompts: declared version + content hash, hash is the contract

(Decision 2 from lock-in: declared version for humans, content hash
for replay.)

Pack prompts live in `prompts/` inside the pack package as `.md`
files with **TOML frontmatter** between `---` delimiters:

```markdown
---
version = "1.0.0"
name = "claim_extractor"   # optional; defaults to filename without .md
---
You extract factual claims from a document...

For each claim, return:
- text
- confidence (0.0-1.0)
- supporting evidence (verbatim quote)
```

Frontmatter parsed with `tomllib` (stdlib, Python 3.11+). YAML
deliberately not used — the codebase has avoided YAML and a tomllib
parser is one stdlib import.

Each prompt is loaded into a `PackPrompt(name, version, body,
content_hash)`. The hash is `sha256(body.encode("utf-8")).hexdigest()`
truncated to 16 hex chars (`"sha256:abcd...ef01"`). Whitespace in the
body is included in the hash — formatting changes count as content
changes.

**The hash, not the version, is the replay contract.** When a pack
loads, the runtime emits a `pack.loaded` event whose payload
includes `{"prompts": {"claim_extractor": {"version": "1.0.0",
"hash": "sha256:..."}}, ...}`. On replay, the same event must be
emitted with the same hashes. A hash mismatch raises
`ReplayDivergenceError`, and the error message includes both
declared versions so an operator sees the change at a glance:
`prompt "claim_extractor": replay expected hash sha256:abcd... (v1.0.0)
but loaded prompt hashes to sha256:beef... (v1.0.0 — version
unchanged, content drift)`.

This catches "I upgraded the diligence pack and my old run now
replays differently" *even when the author forgot to bump the
version*. Declared version is for changelogs and operator messages,
not for correctness.

Helper: `activegraph.packs.load_prompts_from_dir(path) ->
tuple[PackPrompt, ...]` scans a directory of `.md` files and
returns the tuple suitable for `Pack(prompts=...)`. Errors
(malformed frontmatter, missing version, IO failures) raise
`PackPromptLoadError`.

## v0.9 #11. Pack discovery via Python entry points

Third-party packs register themselves under the `activegraph.packs`
entry point group:

```toml
# pyproject.toml of a third-party pack
[project.entry-points."activegraph.packs"]
diligence-extension = "activegraph_diligence_extension:pack"
```

The framework can enumerate installed packs:

```python
from activegraph.packs import discover, load_by_name

for entry in discover():
    print(entry.name, entry.version)

# Load by name from any installed pack:
runtime.load_pack(load_by_name("diligence"), settings=...)
```

`discover()` uses `importlib.metadata.entry_points()` and is cached
per process. Documentation states clearly: `pip install
activegraph-diligence` + `load_by_name("diligence")` Just Works,
and that's how third-party packs are distributed.

The shipped `activegraph.packs.diligence` registers itself under
this entry point group via `[project.entry-points."activegraph.packs"]`
in the framework's own `pyproject.toml`.

## v0.9 #12. Packs use the public API. No privileged access.

Pack code uses the same public API as user code — pack-aware
decorators (#3), `graph.add_object`, `ctx.view`, etc. Packs have no
"escape hatch" to runtime internals. If a pack needs to do
something user code can't do, that's a signal to extend the public
API, not to give packs privileged access.

Packs run with the same OS-level privileges as user code. **Packs
are not sandboxed.** Installing a pack is equivalent to installing
any Python package: it can read your files, make network calls, and
exec arbitrary code in your process. Document this clearly in the
pack authoring guide; trust model is at install time, not run time.

## v0.9 #13. The pack.loaded event

`pack.loaded` is emitted exactly once per `load_pack` call. Payload:

```json
{
  "name":            "diligence",
  "version":         "0.1.0",
  "description":     "...",
  "object_types":    ["claim", "evidence", ...],
  "relation_types":  ["supports", "contradicts", ...],
  "behaviors":       ["diligence.question_generator", ...],
  "tools":           ["diligence.fetch_company_docs", ...],
  "policies":        ["memo_approval", "risk_approval", ...],
  "prompts": {
    "claim_extractor": {"version": "1.0.0", "hash": "sha256:..."},
    ...
  },
  "settings":        {<JSON-serialized settings>}
}
```

Re-loading an already-loaded pack does NOT emit a duplicate
`pack.loaded` event (#6 idempotency). The replay path verifies the
event's payload byte-for-byte (after canonical JSON serialization).
The `settings` block is included so settings drift between runs is
visible in the diff.

`pack.loaded` is in the `runtime.*`-style internal namespace from the
runtime's perspective (it's runtime bookkeeping), but it is **not
suppressed** from the queue — pack-aware behaviors can subscribe to
`pack.loaded` to bootstrap. The Diligence pack does not do this; it's
allowed.

## v0.9 #14. The pack scaffolding command

`activegraph pack new <name>` generates:

```
<name>/
  pyproject.toml          # declares dep on activegraph; activegraph.packs entry point
  <name>/                 # the Python package
    __init__.py           # exports `pack`
    object_types.py
    behaviors.py
    tools.py
    prompts/
      example_prompt.md
  tests/
    test_pack_loads.py    # smoke test: import pack, load into runtime
  README.md
```

Click handles the command; same as the rest of the CLI. The
generated `test_pack_loads.py` verifies the pack imports without
side effects and loads into an in-memory runtime.

The package name (directory and Python package) is the kebab→snake
of the pack name: `pack new diligence-extension` produces directory
`diligence-extension/` and Python package `diligence_extension/`.

## v0.9 #15. Diligence pack scope (locked)

Concretely, `activegraph.packs.diligence` provides:

**Object types** (8): `company`, `document`, `question`, `claim`,
`evidence`, `contradiction`, `risk`, `memo`.

**Relation types** (6): `supports`, `contradicts`, `references`,
`derived_from`, `addresses` (claim → question), `mitigates`
(evidence → risk).

**Behaviors** (7):
  - `company_planner` (deterministic — bootstraps a `company` object
    from `goal.created`)
  - `question_generator` (LLM, one-shot from thesis — see #16)
  - `document_researcher` (LLM + tools — fetches docs AND extracts
    claims; the researcher's `ResearchFindings` schema produces
    claims with evidence quotes in one turn loop, so a separate
    `claim_extractor` behavior is redundant)
  - `evidence_linker` (deterministic — safety net for evidence
    objects that lack a `supports` edge to their claim)
  - `contradiction_detector` (pattern subscription, deterministic)
  - `risk_identifier` (LLM, `activate_after=8` so it fires once
    claims have accumulated)
  - `memo_synthesizer` (LLM)

**Tools** (3, all pack-scoped):
  `fetch_company_docs`, `search_filings`, `summarize_document`.
For v0.9 these are stub tools backed by recorded fixtures (#17).

**Policies** (2): `memo_approval` (memo writes require approval),
`risk_approval` (risk objects require approval). Claim creation
auto-applies.

**Prompts**: one per LLM behavior, in `prompts/` with TOML
frontmatter. Four total (`question_generator`,
`document_researcher`, `risk_identifier`, `memo_synthesizer`).

**Settings** (`DiligenceSettings`):
  `llm_model: str = "claude-sonnet-4-5"`,
  `max_documents_per_company: int = 5`,
  `max_claims_per_document: int = 20`,
  `confidence_threshold_for_review: float = 0.7`,
  `min_questions: int = 8`, `max_questions: int = 15`.

## v0.9 #16. Question generation is one-shot in v0.9

(Decision 6 from lock-in.)

The question generator runs ONCE per goal, produces between
`min_questions` and `max_questions` questions from the thesis, and
the run terminates when "all questions addressed or budget
exhausted." Adaptive question generation (re-generating questions as
claims and contradictions accumulate) is **v1.0** — it requires an
evaluation story we don't have yet.

The killer demo description is updated to reflect this: "watches
questions get worked through, claims accumulate, contradictions
surface" — questions don't grow over time, they get answered.

## v0.9 #17. No contradiction resolver in v0.9

(Decision 6 from lock-in.)

The contradiction **detector** (pattern subscription on
`(c1:claim)-[r:contradicts]->(c2:claim)`) is in scope and creates
`contradiction` objects. The contradiction **resolver** (an LLM
behavior that picks a winning claim) is **deferred to v1.0**.

Why: the resolver adds a second LLM loop with its own prompt, its
own determinism story, and its own evaluation problem ("did it
resolve correctly?"). v0.9 surfaces contradictions as items
requiring human review; the memo synthesizer lists them as "open
questions / risks pending review."

This is honest about what the pack can do today.

## v0.9 #18. Recorded fixtures ship with the pack

`activegraph/packs/diligence/fixtures/` ships inside the pack
package. The fixtures contain LLM responses and tool outputs for
three small companies. `examples/diligence_real_run.py`:

  - Runs in **under 30 seconds in CI** without an API key, without
    network access.
  - Produces **three memos**, one per company, byte-for-byte
    reproducible across runs.
  - Is the integration test for the whole pack.
  - Goes in the README's main example slot.

Convention documented in the pack authoring guide: `fixtures/` is a
sub-package of the pack. Third-party packs are encouraged to follow
the same layout for reproducible demos.

## v0.9 #19. The killer demo is the spec (verifiable memo bar)

`examples/diligence_real_run.py`:

  1. Imports `activegraph.packs.diligence`.
  2. Creates a `Runtime` with Postgres backing (configurable; falls
     back to SQLite for the demo), Prometheus metrics, JSON logging.
  3. Loads the diligence pack with explicit `DiligenceSettings`.
  4. Runs a goal against three fixture companies.
  5. Watches questions get answered, claims accumulate, contradictions
     surface, risks identified, memos drafted.
  6. Inspects the trace — every diligence-owned behavior shows
     `diligence.` prefix.
  7. Demonstrates the `memo_approval` policy gating a memo write.
  8. Forks one company's run with an alternative thesis setting.
  9. Diffs to show which claims changed.
  10. Exports the trace as JSONL.

**Verifiable memo bar**:

  - Three memos produced, one per company.
  - Each memo has sections: Summary, Thesis Questions Addressed,
    Key Claims (with evidence citations), Open Contradictions, Risks.
  - Each memo cites evidence for every claim (zero uncited claims).
  - Each memo surfaces ≥1 contradiction OR explicitly states "no
    contradictions found" if fixtures don't produce any.
  - Each memo surfaces ≥1 risk.
  - The integration test asserts this structure exactly. Memo
    *content quality* is bounded by the fixtures; the test is for
    structure and provenance, not prose quality.

## v0.9 #20. runtime.status() is log-derived (re-affirm)

Already true for v0.8 but worth pinning here: `runtime.status()` is
computed from the event log, not from in-memory caches. After
`load_pack`, status reflects the `pack.loaded` event. Live runtime
and `activegraph inspect` see identical state because both read from
the same source of truth. This is the property that lets operators
trust the dashboard.

The operator guide gets a short section on this property.

## v0.9 #21. Backward compatibility is absolute

Every v0–v0.8 test passes unchanged. No existing API changes shape.
`Runtime.__init__` is unchanged. The global `@behavior` / `@tool`
decorators behave exactly as before. The `Graph.add_object` path is
unchanged in the no-packs-loaded case.

Schema validation is a load-order-asymmetric ADDITION (#5): it
applies only when a typed pack has been loaded for that object type.
Pre-load and unloaded-type behavior is the v0.8 behavior.

## v0.9 #22. v0.7 diligence example stays in place

(Decision 14 from the original plan.)

`examples/diligence_with_tools.py` from v0.7 is **not** removed and
**not** rewritten. The pack is a new, hardened version. The example
demonstrates building a custom diligence behavior from primitives;
the pack demonstrates *using* a pre-built diligence system. Two
different audiences, both supported. The README points new users at
the pack; the v0.7 example remains the canonical custom-behavior
walkthrough.

## v0.9 #23. Python version floor: 3.11

(Decision 2 from lock-in: tomllib is stdlib in 3.11+.)

`pyproject.toml`'s `requires-python` moves from `>=3.10` to
`>=3.11`. The 3.10 classifier is dropped. tomllib is used in the
prompt loader; no third-party YAML/TOML dependency is added.

## v0.9 #24. Documentation is part of the pack

Each pack ships a `docs/` directory in its package with at minimum:
README, settings reference, behavior reference, prompt reference. The
shipped Diligence pack does this. The scaffolding command creates
stubs. The pack authoring guide (`docs/pack_authoring.md`) is the
shared reference for the pack format itself.

## v0.9 #25. Trace format additions

The trace printer gains rendering for `pack.loaded` events:

```
[pack.loaded]    diligence v0.1.0 (8 object_types, 6 relation_types,
                 7 behaviors, 3 tools, 2 policies, 5 prompts)
```

Trace causal chains follow `pack.loaded` provenance back to the
`load_pack` call site (recorded `caused_by` is the lifecycle event
that triggered the load).

The JSONL export includes `pack.loaded` events verbatim.

## v0.9 #26. What v0.9 deliberately does NOT add

- Pack marketplace, registry, or distribution mechanism (v1.0+)
- Multiple reference packs — Memory pack, Research pack (v1.0)
- Pack versioning beyond a string in the manifest (v1.0+)
- Pack signing or trust (v1.0+)
- Pack sandboxing or capability restrictions (intentional; #12)
- Streaming LLM responses (v1.0+)
- Multi-model routing (v1.0+)
- Adaptive question generation (#16)
- Contradiction resolution as an LLM behavior (#17)
- Web UI (never, per prior decisions)

Stay scoped. v0.9 is the first milestone with a "product" in it —
the Diligence pack must be polished enough that a developer who
installs activegraph + activegraph.packs.diligence and follows the
README can produce a useful memo on day one. If the pack feels like
a toy, the milestone hasn't shipped. Polish over breadth.

**Review status (2026-05-19):** STILL ACCURATE. #2 self-documents
the supersession of v0.7 #22's per-line `prompt_normalized=true`
clause — exemplary cycle discipline (the successor amendment
explicitly names what it supersedes; the predecessor's claim is
preserved as archeology).

# v0.9.1 — pending follow-ups bundle

A small follow-up release with two items the v1.0 plan flagged as
worth landing before the adoption-surface work starts, so the v1.0
PR series begins from a baseline with no carryover debt. Both are
quality-of-life, not new capability.

## v0.9.1 #1. Granular approval-demo console output

`examples/diligence_real_run.py` previously printed
`pending approvals: N` followed by detail rows that left the
operator guessing what was queued. The new output names every
pending item up front and walks the gate in rounds:

```
pending approvals (1, initial): risk_northwind_001
  - risk_northwind_001           approval_001  reason='risk_approval policy: customer concentration'
pending approvals (1, round 2): memo_northwind
  - memo_northwind               approval_002  reason='memo_approval policy: company company#1'
after approval: 0 pending
```

The slug shape is `<object_type>_<company_short>` (memo) or
`<object_type>_<company_short>_<approval_seq>` (risk). Lookup is
through `runtime.graph.get_object(company_id)` so the slug uses the
human company name, not the auto-generated id. The drain loop runs
until `pending_approvals()` is empty, so the demo terminates at
zero — approving a risk surfaces the memo, approving the memo
clears the queue.

The slug helper is example-local. The runtime does not pick names
for pending approvals — that's a pack/operator concern.

## v0.9.1 #2. `prompt_normalized=true` trace flag rollup

The per-line `prompt_normalized=true` suffix on every
`llm.requested` event (added as the v0.6 follow-up bundled into
v0.7, CONTRACT v0.7 #22) clutters traces in real packs where a
single goal produces dozens of LLM calls. v0.9.1 rolls it up.

The `Trace.lines()` facade now emits a single `[trace.flags]`
header when every non-replayed `llm.requested` event in the trace
carries `prompt_normalized=true`:

```
[trace.flags]             prompt_normalized=true (27 llm requests)
[goal.created]            user: "Diligence: Northwind Robotics"
...
[llm.requested]           evt_006  extractor  model=claude-sonnet-4-5 ...
```

Per-line flags are suppressed in this mode. Mixed-state traces
(some events normalized, some not — should not happen in practice
since normalization is a pack-level invariant) keep the per-line
flag and suppress the header, so the divergence stays visible.

`format_event(event, *, hide_prompt_normalized=False)` gains the
suppress kwarg; only `Trace.lines()` sets it, and only when the
rollup applies. JSONL export still includes `prompt_normalized`
verbatim in the event payload — the rollup is a render-time
concern, not a data change.

### Snapshot drift

Two snapshot files updated in the same commit:

- `tests/snapshots/llm_trace.txt`
- `tests/snapshots/tool_trace.txt`

Both gain a leading `[trace.flags]` line and lose the trailing
`prompt_normalized=true` on each `llm.requested` line. No other
event types change. The 384 v0–v0.9 tests pass unchanged after
the snapshot update.

The v0.7 #22 backward-compat clause is now superseded for the
`llm.requested` line. v1.0+ tests that need to see the per-line
flag (mixed-state cases) call `_compute_prompt_normalized_rollup`
to confirm uniformity.

## v0.9.1 #3. Out of scope (re-affirm)

The v1.0 plan locked these as out for v0.9.1 and v1.0 both:

- Richer fixture cardinality (post-1.0 polish, not framework
  work; v0.9 ships three companies producing three memos that
  meet the verifiable bar — that is the contract)
- A `--live` quickstart mode (dropped from v1.0 per pushback;
  ships post-1.0 only if quickstart usage shows demand)
- Web UI, streaming, multi-model routing (per v0.9 #26)

# v1.0 — adoption surface (shipped; PR series)

**Review status (2026-05-19):** STILL ACCURATE. The "not yet
shipped" qualifier in the original header is historical — v1.0
shipped 2026-05-18; v1.0.1 / v1.0.2 / v1.0.2.post1 / v1.0.3 have
shipped since. The C1–C8 decisions, the #1–#10 main spec, the
PR-A-through-PR-G series, and #4b / #4c / #4d addenda all hold.
"v1.1 error-completeness milestone scope" tallies scattered through
PR-E / PR-F / PR-G are now consolidated in `v1.1-plan.md` Theme A.
The cumulative tally of 19 new exception classes migrated to the
v1.0 structured format stands as the series-final state per PR-G's
end-of-series note.

The framework runtime is done. v1.0 is the milestone that decides
whether anyone uses it. Every prior milestone improved the artifact;
v1.0 improves the path to the artifact — installation, first-run
experience, error messages, documentation site. The least technically
interesting milestone in the roadmap and probably the most
commercially important one.

Scope is a fixed list, not a moving target: a `quickstart` CLI command
that produces a working diligence run with no configuration, an audit
and rewrite of every error message in the framework against a
documented standard, an `ActiveGraphError` hierarchy, a docs site at
`docs.activegraph.dev` built with mkdocs-material, all examples
rewritten to be copy-pasteable, a "first 10 minutes" tutorial, type
stubs verified against `mypy --strict` for the public API, a
`CHANGELOG.md` covering v0 through v1.0, and the two pending
follow-ups that just landed in v0.9.1 above.

Out-of-scope deferral list (post-1.0 or never): web UI, streaming LLM
responses, multi-model routing, more reference packs (Memory pack,
Research pack), a pack registry, error message i18n, video tutorials,
adaptive question generation in Diligence, the contradiction
resolver, richer fixture cardinality.

## v1.0 contract diff vs. the v1.0 plan (eight revisions)

The v1.0 plan as authored prompted seven pieces of pushback that
changed the contract before the first line of code in the v1.0 PR
series. The eighth landed after rc1, surfaced by the user-test gate
(`#C4`). Recording all eight here so the contract diff is visible
from the start of the work, not buried in PR descriptions.

### v1.0 #C1. Error message rewrite ships as a PR series, not one PR

The error message audit covers 50+ sites. Format is uniform but
every site needs a real "what failed / why / how to fix" with
specific names — that's 50+ small design decisions, not find-replace.
One PR is unreviewable; reviewers skim and miss the bad ones.

The series:

- **PR-A (foundation):** `ActiveGraphError` hierarchy, the format
  spec, snapshot-test harness, one fully-converted category as the
  reference (smallest category by error count). Lock the format
  standard here so it cannot drift across the series.
- **PR-B through PR-F:** one error category per PR
  (`ConfigurationError`, `RegistrationError`, `ExecutionError`,
  `ReplayError`, `StorageError`, `PatternError`, `PackError`).
  Each PR includes a snapshot test of every error in its category
  so review is mechanical — reviewer reads the "what failed / why /
  how to fix" text, not the diff.

If `RegistrationError` is genuinely the smallest category by error
count, PR-A leads with it. Otherwise the smallest leads. Goal is to
set reference quality before larger categories land.

### v1.0 #C2. Docstring coverage is tiered, not a flat percentage

Flat percentage gates breed performative docstrings (you've seen the
codebases). v1.0 gates by ring:

- **Ring 0 (public surface):** symbols in `activegraph.__all__` plus
  each pack's top-level `__all__`. 100% docstring coverage. No
  exceptions. This is a small, finite, hard-to-game list.
- **Ring 1 (importable but not re-exported):** modules users import
  directly (`activegraph.trace`, `activegraph.llm`, etc.) but
  symbols not in a top-level `__all__`. 80% coverage.
- **Internals:** no gate.

CI enforces Ring 0 and Ring 1 thresholds via `interrogate`. The
list of Ring 0 symbols lives in `pyproject.toml` and changing it
requires a deliberate PR — adding a new public symbol forces a
docstring decision.

### v1.0 #C3. `--live` quickstart mode is dropped from v1.0

Cost-prompted live calls on a brand-new install is a UX trap.
Estimate accuracy depends on provider pricing we don't control; new
user hits `y` by reflex; first experience is a surprise charge.

`activegraph quickstart` ships in v1.0 with the fixture-backed
demo only. `--interactive` (walk-through tutorial) stays in scope.
`--live` ships post-1.0 only if quickstart usage shows real demand,
and only after we agree on a hard cap (likely env-var-gated, $0.01
default ceiling using the existing budget primitives).

The v1.0 quickstart command therefore exposes two modes:

```
activegraph quickstart                # fixture-backed diligence demo
activegraph quickstart --interactive  # tutorial walk-through
```

Both end with "here's what to read next" pointing at specific doc
pages.

### v1.0 #C4. v1.0 ships as `v1.0-rc1`; first-time-user gate is owned externally

The "real first-time user runs through the tutorial" gate cannot be
verified from inside the agent loop. Pretending it can produces a
passing test for a failed experience.

The v1.0 PR series therefore ships as `v1.0-rc1`. The CHANGELOG
flags the first-time-user test as the sole blocker on `v1.0` final.
The gate is run externally — a non-author developer runs through
the tutorial blind, screen-recorded, the friction points are
captured, fixes go in, then the version is cut to `v1.0`.

The agent does not claim the gate passed.

### v1.0 #C5. `mypy --strict` scope is allowlist-driven, in `pyproject.toml`

"Public surface" is too vague to gate on. v1.0 makes the allowlist
explicit:

- The allowlist is a `[tool.mypy.strict_modules]`-equivalent block in
  `pyproject.toml` enumerating every module that should pass `mypy
  --strict`. The default is everything reachable from
  `activegraph.__all__` and pack-level `__all__`.
- Internal modules get normal `mypy`. The internal/public boundary
  matches the docstring-coverage boundary (#C2), so "what we
  document publicly" and "what we strictly type" are the same set
  of symbols — satisfyingly self-consistent.
- Adding a new symbol to a top-level `__all__` forces a PR-level
  decision about typing strictness.

### v1.0 #C6. Doc site DNS is externally owned; ship with fallback URL

`docs.activegraph.dev` is a domain registration outside the agent's
reach. v1.0:

- Wires `mkdocs-material`, the GitHub Pages deploy workflow, and the
  `CNAME` file pointing at `docs.activegraph.dev`.
- Until DNS is live, error message doc URLs and the README point at
  the github.io fallback (`https://yoheinakajima.github.io/activegraph/`).
- When DNS resolves, the cutover is a search-replace in error
  message URLs plus a one-line CNAME update. Documented in the
  CHANGELOG so the swap is reproducible.

**v1.0-rc3 amendment: primary domain switched from
`activegraph.dev` to `activegraph.ai`.** Same cutover pattern,
different target. `activegraph.ai` is already owned;
`activegraph.dev` will be registered and configured as a redirect
to the primary (the registration is externally owned; the redirect
is a server-side concern, not a codebase concern). The codebase
holds exactly one primary domain — `docs.activegraph.ai` — and the
constant `DOCS_BASE_URL` is the swap point as before.

Concrete effects of the rc3 amendment:

- `docs/CNAME` updated to `docs.activegraph.ai`.
- `mkdocs.yml` `site_url` updated to `https://docs.activegraph.ai/`.
- `activegraph.errors.DOCS_BASE_URL` updated to
  `https://docs.activegraph.ai`. Every error message URL and every
  `DOCS_BASE_URL`-derived f-string updates with it.
- README, CHANGELOG, HANDOFF, and `docs/about/publishing.md`
  references updated to the primary domain.
- `tests/test_doc_links.py` continues to recognize the old
  `docs.activegraph.dev` URLs as valid (so historical CHANGELOG
  entries' .dev URLs still pass the source-presence check). The
  new primary `docs.activegraph.ai` is added; both resolve to the
  same `docs/` source tree.
- Error snapshots regenerated under the new `DOCS_BASE_URL` via
  `UPDATE_SNAPSHOTS=1`. The regenerated snapshots are the new
  contract baseline for the format gate.

Discipline note on what this amendment does NOT do:

- It does not retroactively rewrite historical CONTRACT entries
  that referenced `docs.activegraph.dev`. Those entries are records
  of what was decided at the time and stay accurate as history.
  Forward-looking spec text (the URL pattern for error pages, the
  current-state description of where the site serves) is updated.
- It does not assume the `.dev` redirect is live. The codebase
  ships with `.ai` as the primary; the `.dev` redirect is a
  user-owned operational step that lands separately. Until it
  does, users who type the old `.dev` URL by hand hit the same
  404 the rc2 user-test surfaced — the fix is the redirect, not
  more codebase changes.

### v1.0 #C7. v0.9.1 lands before v1.0 PR-A

The two follow-ups (granular approval-demo output, prompt_normalized
trace rollup) ship as v0.9.1 — not bundled into a v1.0 PR. Reasoning:
v1.0 is an adoption-surface milestone, and milestone discipline says
no milestone starts with carryover from the previous one. v0.9.1 is
committed before any v1.0 work begins. **Done. See v0.9.1 sections
above.**

### v1.0 #C8. PyPI publishing is externally owned

The v1.0-rc1 user-test gate (CONTRACT v1.0 #C4) surfaced the gap:
`pip install activegraph` is the README's first line and the
tutorial's step 1, but the package was not on PyPI. The first-time
user has no path forward. The tester fell back to a clone-and-edit-
install; a real first-time user without the repo cloned hits a wall.

Mirrors `#C4` (user-test gate) and `#C6` (DNS for the doc site):
externally-owned final steps gated behind work the agent loop can
do. The agent ships the release-publish workflow as a GitHub Action;
the maintainer runs the publish on tag push.

Workflow shape (locked):

- Trigger on git tag push matching `v*` so any future tag
  (`v1.0-rc2`, `v1.0`, `v1.0.1`) publishes automatically without
  workflow edits.
- Build sdist + wheel via `python -m build` (post-setuptools standard).
- Upload via PyPI **trusted publishing** (OIDC-based) as the default
  path — modern recommendation, no long-lived secrets to rotate or
  leak. If trusted-publishing setup on PyPI requires maintainer
  action before the workflow can succeed, that's surfaced as a
  prerequisite in the operating guide, not a workflow change.
- Workflow documented in `docs/about/publishing.md` (sibling to the
  doc-site deploy workflow) with prerequisites, the trigger, and
  post-publish verification steps (`pip install activegraph==<version>`
  from a fresh environment).

The README and tutorial do not regress to "clone and install editable";
the install instruction `pip install activegraph` stays accurate
because v1.0 final ships with the package on PyPI.

## v1.0 #1. The quickstart command is the spec

The single most important deliverable in v1.0 is the
`activegraph quickstart` command. End-to-end against bundled fixtures
with no API key, no Postgres, no configuration. Output is a single
memo to stdout, a trace summary, and a "what just happened" section
explaining what the developer saw.

The transcript at `examples/quickstart_session.txt` is the contract
for the whole milestone. Every piece of v1.0 work either supports a
line of this transcript or it doesn't belong in v1.0.

The transcript explicitly includes the fork-and-diff beat: most
evaluators won't read about that capability; they need to see it in
the quickstart flow to understand what's structurally different
about the framework. The transcript has an explicit beat for that
moment of recognition.

### v1.0 #1 errata — fixture source

The original prompt for the quickstart-command commit framed
fixtures as living under a separately-installable `activegraph_diligence`
package with a "depends on diligence pack" optional-dep check.
That was wrong: the diligence pack lives in `activegraph/packs/diligence/`
as part of the main install and cannot be uninstalled separately.
The optional-dep recovery path is dead code by construction.

The correct shape: the quickstart command imports
`from activegraph.packs.diligence import pack, fixtures` directly.
No optional-dep check; no install-instruction error path. The
pack is always present.

## v1.0 #2. Build order is fixed

1. `examples/quickstart_session.txt` (the spec)
2. `ActiveGraphError` hierarchy + format standard + PR-A reference category
3. PR-B through PR-F (one error category per PR)
4. `activegraph quickstart` CLI command (fixture + interactive modes)
5. mkdocs-material site skeleton + GitHub Pages workflow
6. Error reference pages (one per error class)
7. API auto-generation via mkdocstrings
8. 10-minute tutorial (`docs/quickstart.md`)
9. Cookbook + migration pages
10. `mypy --strict` allowlist + interrogate gates as CI requirements
11. `CHANGELOG.md` covering v0 through v1.0-rc1
12. `v1.0-rc1` tag
13. (External) first-time-user test gate clears → `v1.0` tag

Do not invert. Each step depends on the contract set by the prior
step.

## v1.0 #3. The error message format is locked

Every framework error follows this exact shape:

```
<ErrorClass>: <one-line summary>

What failed:
  <specific thing that went wrong, with names>

Why:
  <explanation of the root cause, not just the symptom>

How to fix:
  <concrete action the developer can take>

More:
  https://docs.activegraph.ai/errors/<error-class-slug>
```

Snapshot-tested per-error-class. Doc URL must resolve to a real
page; broken links fail CI. Until DNS for `docs.activegraph.ai` is
live, the URL renders as the github.io fallback (#C6) and the swap
is the documented cutover.

### Voice principle: invariant-protection, not validation

`Why:` is the section that decides how the framework feels to a
developer in the moment of failure. Locked principle from PR-A's
reference category, applies to every error in the v1.0 series:

**Explain the framework's intent — the invariant being protected — not
the mechanism of the check.** Developers reading an error want to know
"why is this framework being strict with me right now," and the answer
should almost always be "because it's protecting an invariant you
care about."

The reference instance, from `ReplayDivergenceError` (PR-A):

> The replay cache keys on the full prompt hash, so any change to an
> LLM behavior's code, a prompt template, a system message, or a tool's
> input arguments produces a mismatch. The framework refuses to silently
> substitute a stale cached response under a new prompt — that would
> break the audit trail the cache is designed to preserve.

Voice notes for PR-B through PR-G:

- **Active, declarative.** "The framework refuses…" not "It is not
  permitted to…" Passive voice erases who decided.
- **No apology.** No "unfortunately," no "sorry," no "this is a
  limitation." The framework made a decision; explain the decision.
- **No clinical noun stacks.** "validation of the input prompt against
  the cached hash failed" is clinical. "the prompt hash didn't match
  what was recorded" is direct.
- **Name the invariant.** "The audit trail," "the determinism
  guarantee," "the budget cap," "the pack's schema contract." Errors
  exist because invariants exist; name the one being protected so
  developers can decide if they care about it.
- **No internal jargon without expansion.** Terms like "behavior graph,"
  "patch lifecycle," "frame stack" are valid in `Why:` if they appear
  in the public concepts docs (CONTRACT v1.0 #5). Terms from the
  implementation (e.g., `_pack_state`, `BehaviorScheduler`) are not.

Snapshot review for PR-B through PR-G is partly a tone review against
these notes. If a `Why:` paragraph drifts toward apologetic, passive,
or implementation-detail voice, send the PR back.

## v1.0 #4. The `ActiveGraphError` hierarchy is the root

```
ActiveGraphError
├── ConfigurationError      # runtime construction problems
├── RegistrationError       # behavior/tool/pack registration
│   ├── PackConflictError
│   ├── MissingProviderError
│   └── MissingToolError
├── ExecutionError          # runtime execution
│   ├── BudgetExhaustedError
│   └── BehaviorFailedError
├── ReplayError             # replay/fork
│   └── ReplayDivergenceError
├── StorageError            # persistence
├── PatternError            # pattern subscriptions
│   └── UnsupportedPatternError
└── PackError               # pack-specific
```

`ExecutionError`, not `RuntimeError` — Python has a builtin
`RuntimeError` and shadowing it produces confusing stack traces.

`ActiveGraphError` exposes structured fields:

- `.what_failed: str`
- `.why: str`
- `.how_to_fix: str`
- `.doc_url: str`
- `.context: dict`  (error-class-specific data)

`__str__` produces the format in #3.

### v1.0 PR-A landed (foundation + ReplayError reference)

Concrete artifact under `activegraph/errors.py`:

```python
class ActiveGraphError(Exception): ...
class ConfigurationError(ActiveGraphError): ...
class RegistrationError(ActiveGraphError): ...
class ExecutionError(ActiveGraphError): ...
class ReplayError(ActiveGraphError): ...
class StorageError(ActiveGraphError): ...
class PatternError(ActiveGraphError): ...
class PackError(ActiveGraphError): ...
```

Re-exported from `activegraph.__all__`. `activegraph.packs.PackError` is
re-homed to point at `activegraph.errors.PackError` (same class object) so
the existing pack leaves (PackValidationError, PackConflictError, etc.)
inherit the new base without changing their import paths.

`ActiveGraphError.__init__` has two construction modes during the v1.0
transition:

- **Structured** (the v1.0 target): pass ``summary`` plus the three named
  fields. `__str__` produces the locked format.
- **Legacy**: pass a single positional message. `__str__` returns that
  message verbatim. Format-noncompliant but valid Python, so existing
  raises in unmigrated leaves keep working through PR-B → PR-F.

`ActiveGraphError.is_structured()` returns True for the first mode, False
for the second. Snapshot tests in `tests/test_errors_format.py` only run
on classes that are explicitly enumerated; legacy raises don't fail
format compliance until their category's PR migrates them.

Reference category in PR-A: **ReplayError**. The chosen reference
because:

- Single class (smallest by class count, tied with PatternError and
  PackError; broke the tie by stakes)
- Highest-stakes error in the framework — fires in the fork-and-diff
  flow that BEAT 4 of the v1.0 transcript depends on
- Three distinct call sites discriminated at `__init__` time
  (prompt_hash_mismatch, type_mismatch, length_mismatch), each producing
  a different "what failed / why / how to fix" — exercises the format's
  full expressive range so PR-B+ can model against a real reference

`ReplayDivergenceError` migrated from `RuntimeError` to `ReplayError`.
Constructor signature (`event_id`, `expected`, `actual`) preserved so
the 384 v0–v0.9 tests still pass unchanged.

Snapshot files under `tests/snapshots/errors/`:

- `replay_divergence__prompt_hash_mismatch.txt`
- `replay_divergence__type_mismatch.txt`
- `replay_divergence__short_live.txt`
- `replay_divergence__extra_live.txt`

Each snapshot is byte-identical; `UPDATE_SNAPSHOTS=1` regenerates them
and the doc-site reference page at `docs/reference/errors/<slug>.md`
(landing in a later v1.0 PR) must be updated in the same commit.

Tests added: 18 in `tests/test_errors_format.py`. Format check is
structural (section headers in order, 2-space body indent, doc URL in
`More:`), not regex — multi-line bodies with internal blank lines (as in
the `How to fix:` of a real ReplayDivergenceError) pass.

### Format spec amendments noted during PR-A

The `How to fix:` section in real errors often runs to multiple
paragraphs separated by blank lines (a "Identify the behavior… / Then
diff against current source…" structure). The format spec's "2-space
indent for the body" applies to every non-blank line; blank lines stay
blank. Code blocks inside the body are indented further (4 additional
spaces) for visual separation. The format snapshot tests are structural,
not regex, to accommodate this.

PR-B through PR-F will encounter similar prose conventions in their
categories. The convention: 2-space indent for body lines, 4-extra-space
indent for code blocks, blank line for paragraph breaks. Cosmetic, but
locking it now means the 50+ error pages on the doc site look uniform.

### v1.0 CLI follow-ons landed (between PR-A and PR-B)

PR-A's reference error messages point at operator flags that did not
yet exist (`activegraph fork --record`, `activegraph inspect --event`,
`activegraph inspect --pack-version`, `activegraph inspect --behaviors`).
Per the v1.0 plan review, the right tradeoff is to build the flags so
the recovery prose stays useful, rather than dumb the error messages
down to reference only what existed before PR-A.

The four flags are CLI surface over existing APIs — no new runtime
capability, in line with the v1.0 ban:

- **`activegraph inspect <run> --event <id>`** prints one event's full
  payload (text or JSON). The event lookup is `next(e for e in
  rt.graph.events if e.id == event_id)` — no new runtime API.
- **`activegraph inspect <run> --behaviors`** prints only the
  registered-behaviors section. Reuses `rt.status(recent=0)` and
  filters output.
- **`activegraph inspect <run> --pack-version`** prints every
  `pack.loaded` event in the run with the pack name, version, and
  every prompt's version + truncated content hash (the same hash that
  `ReplayDivergenceError` compares against). Filters events for
  `e.type == "pack.loaded"`.
- **`activegraph fork --record`** appends `-recording` to the fork's
  label (or sets it to `recording` if none was given) and prints
  follow-on guidance: "load this run without `replay_strict=True` to
  accept new LLM/tool cache entries." The actual recording semantics
  emerge from how the new run is later loaded — the flag is operator
  UX over the existing fork primitive, not a new runtime mode.

The three `inspect` selectors are mutually exclusive (selectors, not
filters). Combining them is a usage error.

Tests added: 10 in `tests/test_cli.py` (one happy path per flag,
JSON variants, mutual-exclusion check, not-found for unknown event id).

PR-B through PR-G can now write recovery prose pointing at flags that
exist.

### v1.0 PR-B landed (PatternError, second-smallest category)

`UnsupportedPatternError` (the sole leaf under `PatternError`) migrated
to the v1.0 structured format. Re-parented from `SyntaxError` to
`PatternError(ActiveGraphError)` with `SyntaxError` kept via multi-
inheritance so user code that catches `SyntaxError` around pattern
compilation continues to work.

16 raise sites in `activegraph/runtime/patterns.py` converted, grouped
into two factory class methods that produce the canonical voice for the
two failure modes:

- **`UnsupportedPatternError.refused_feature(feature=, workaround=, at=)`**
  for the case where a recognized Cypher feature is deliberately refused
  by the v0.7 subset. The factory provides a uniform "Why:" explaining
  the testability and audit-trail rationale; the caller passes the
  per-feature workaround.

- **`UnsupportedPatternError.syntax_error(what=, expected=, got=, at=)`**
  for parser-level failures (unexpected character, expected X got Y,
  missing relationship type, unexpected trailing tokens). Uniform
  "Why:" and "How to fix:" pointing the developer at the offending
  position and the docs reference.

Per-keyword workaround prose lives in `_KEYWORD_WORKAROUNDS` (17 keys —
every keyword in `_FORBIDDEN_KEYWORDS` has a specific "do this in the
behavior body instead" answer). Avoids the generic "use the supported
subset" failure that v0.9's messages produced.

Internal evaluator errors (`unknown operator`, `unrecognized WHERE AST
node`) use direct structured construction with prose framing them as
internal inconsistencies — the recovery is "file an issue with the
offending pattern."

Snapshot files under `tests/snapshots/errors/`:

- `unsupported_pattern__or_in_where.txt`
- `unsupported_pattern__variable_length_path.txt`
- `unsupported_pattern__undirected_relationship.txt`
- `unsupported_pattern__optional_keyword.txt`
- `unsupported_pattern__unexpected_character.txt`
- `unsupported_pattern__relationship_type_required.txt`

Tests added: 10 in `tests/test_errors_format.py`. Snapshot review
doubles as voice review per CONTRACT v1.0 #3 voice-principle clause.

Backward-compat preserved: all 38 pre-existing pattern tests pass
unchanged. Their `pytest.raises(..., match="...")` substring patterns
still match because every existing test substring (`"OR is not
supported"`, `"variable-length"`, `"undirected"`, `"unexpected
character"`, `"type required"`, the dynamic-keyword substrings) appears
in the new summary or body verbatim. Feature labels in
`refused_feature` calls were chosen to preserve these substrings.

The `at` attribute on every raise is preserved on the new class for
back-compat with user code that reads it.

422 tests pass (412 + 10 new). All v0–v0.9 tests pass unchanged.

### v1.0 PR-C landed (StorageError, audit-driven)

Audit of `activegraph/store/` surfaced **4 new concrete leaves** beyond
the 2 pre-existing storage errors. The audit-as-side-effect rationale
for the PR series (CONTRACT v1.0 #C1) paid off here: a mechanical
find-replace would have migrated only the named classes and left the
bare-`RuntimeError`/`KeyError`/`ValueError` raises as hidden surface.

**Migrated (re-parented to StorageError):**

- `NonSerializableEventError(StorageError, TypeError)` — emit-time
  failure to JSON-encode. Multi-inherits TypeError for back-compat.
  Now walks the payload to identify the offending field path and
  reports it in the structured "What failed:".
- `InvalidStoreURL(StorageError, ValueError)` — 7 raise sites with
  per-shape recovery prose (bare-path → exact corrected URL, missing
  path, missing host, unsupported scheme).

**New leaves under `activegraph/store/errors.py`:**

- `SchemaVersionMismatch` — was bare `RuntimeError` in `sqlite.py:112`
  and `postgres.py:211`. Recovery enumerates the three concrete
  actions (upgrade activegraph, migrate runs, drop expendable store).
  Includes framework version + recorded version in context for
  triage.
- `EventNotFoundError(StorageError, KeyError)` — was bare `KeyError`
  at 7 sites across `memory.py` / `sqlite.py` / `postgres.py`. Two
  shapes of recovery: lookup misses point at `inspect --tail`, fork
  misses point at `inspect --tail` against the parent run.
  Multi-inherits `KeyError` for back-compat with user code catching
  the builtin.
- `DuplicateEventError(StorageError, ValueError)` — was bare
  `ValueError` at `memory.py:22`. Frames the failure as a programmer
  error (the runtime's id generator is monotonic; duplicates in
  production usage are bugs in fixtures/tests).
- `CorruptedEventPayloadError` — new ground. `decode_payload` was
  previously bubbling `json.JSONDecodeError` from corrupted stored
  rows; now wraps it with structured prose pointing at how to inspect
  surrounding events, how to manually repair via sqlite3/psql, and
  the unrecoverable-fallback. The recovery prose deliberately does
  NOT reference unimplemented CLI surface (the discipline note from
  the PR-C review).

**Flagged-not-migrated (insufficient context — discipline note):**

- **SQLite `sqlite3.OperationalError`** under WAL contention. Multiple
  distinct failure modes (lock timeout, journal corruption, disk full,
  file-system permissions); each needs its own recovery prose. Bubbles
  unwrapped today.
- **Postgres `psycopg.OperationalError`** variants (auth, host
  unreachable, db missing, conn dropped). Same shape as above —
  per-mode recovery diverges enough that a single wrapper would lie.
  Bubbles unwrapped today.
- **`postgres.py:107` `TypeError`** (bad target type). This is
  configuration shape, not storage. Defers to **PR-F**
  (`ConfigurationError`).
- **`postgres.py:73` `ImportError`** (missing psycopg). This is
  "missing optional dependency at registration time." Defers to
  **PR-E** (`RegistrationError`).

These four are tracked as v1.0-rc1 follow-ons (dedicated DB-error PR
post-rc1, but pre-1.0-final if a contributor with hands-on experience
in the failure modes is available). If no contributor surfaces, they
ship as-is for v1.0 — bubbling the underlying driver error is honest
in the absence of correct recovery prose; making up prose would be
worse.

**Snapshot files:**

- `invalid_store_url__bare_path.txt`
- `invalid_store_url__unsupported_scheme.txt`
- `non_serializable_event.txt`
- `corrupted_event_payload.txt`
- `schema_version_mismatch.txt`
- `event_not_found.txt`
- `duplicate_event.txt`

Tests added: 11 in `tests/test_errors_format.py` (6 snapshots + 5
back-compat assertions on multi-inheritance + 2 cross-checks that
`except KeyError` / `except ValueError` still catch the new leaves).

433 tests pass (422 + 11). Backward-compat absolute: all 384 v0-v0.9
tests pass unchanged. The diligence demo (the v0.9 killer demo)
still runs end-to-end against the migrated store layer with byte-
identical trace output.

**Hidden-surface count so far (audit value tracking):**

- PR-A (ReplayError): 0 new leaves (single class)
- PR-B (PatternError): 0 new leaves (single class, but 17 keyword
  workaround branches that didn't exist as concrete prose)
- PR-C (StorageError): **4 new leaves** + 4 flagged for follow-on

Useful data for v1.1 planning. The PatternError keyword table and the
PR-C new leaves both came from audit-as-side-effect work that a
mechanical rewrite would have missed.

### v1.0 CLI follow-ons landed (between PR-C and PR-D)

Two follow-ons triggered by PR-C review:

**`activegraph migrate --skip-corrupted`** — the operator-facing recovery
tool that `CorruptedEventPayloadError`'s prose pointed at. Without this
flag, the prose was honest but the floor was too high (manual sqlite3/psql
repair). The principle: every error message that points at "manual repair"
is a finding that the framework owes the operator a tool.

Implementation (no new runtime capability):

- New optional `skip_corrupted: bool` parameter on
  `activegraph.observability.migration.migrate()` and on
  `_migrate_one_run`.
- New driver-specific helpers `_iter_sqlite_skip_corrupted` and
  `_iter_postgres_skip_corrupted`. These iterate raw rows and decode
  each one individually so a single `CorruptedEventPayloadError` is
  recorded-and-skipped rather than killing the whole iterator (Python
  generators die after raising — that's why the existing `iter_events`
  can't be wrapped in a per-row try/except).
- New `skipped_events: tuple[str, ...]` field on `MigrationRunReport`.
  Lists the event ids that were skipped. Empty on a clean migration.
- `--skip-corrupted` flag on the CLI command. Text mode prints
  `skipped (corrupted): <event_id>` per skipped row; JSON mode adds
  `skipped_events: [...]` to each run's report shape.
- `CorruptedEventPayloadError`'s recovery prose updated to reference
  the flag as the first option. Manual sqlite3/psql repair remains in
  the prose as the second option (for operators with a backup of the
  original payload) — preferable to skip-and-lose when possible.

The skip-corrupted path is a **partial recovery**: the destination run
is missing the corrupt event. The operator is on notice via both the
flag's help text and the per-run report. Idempotency keys on
`(id, run_id)` mean re-running the migration after a manual repair
won't duplicate.

**Version-sync CI gate** — new `tests/test_version_sync.py` asserts that
`activegraph.__version__` matches `pyproject.toml`'s `[project] version`.
Caught the stale `__version__ = "0.9.0"` constant during PR-B (pyproject
was already `0.9.1`); the gate prevents the next drift. Every error
message that embeds the version (PR-B internal-error contexts, PR-C
SchemaVersionMismatch) reads `activegraph.__version__`, so a drift
would produce wrong-version error reports — exactly the confusing
GitHub Issue we want to avoid.

Tests added: 3 for `--skip-corrupted` (JSON shape with skipped_events,
text-mode output naming skipped ids, default-strict behavior preserved)
plus 1 for the version-sync gate.

437 tests pass (433 + 4). All v0–v0.9 tests pass unchanged.

### v1.0 PR-D landed (ExecutionError, smallest scope of the series)

The pre-series intuition was that PR-D would have a "probable cluster
of bare-RuntimeError raises around behavior dispatch and budget
enforcement." The audit found that intuition mostly wrong: behavior
failures and budget exhaustion both use the **event-driven, not
exception-driven** pattern, so there's no exception class to migrate
for either. The bare-RuntimeError sites that exist around runtime
dispatch are about runtime configuration constraints (fork on
non-SQLite, etc.) and defer to PR-F (`ConfigurationError`).

PR-D ends up the smallest in the series — exactly the kind of finding
that makes audit-as-side-effect worthwhile. We confirm what's there,
confirm what isn't there, and move on without making up scope.

**Migrated (3 classes):**

- `LLMBehaviorError(ExecutionError, Exception)` — the LLM-side carrier.
  The ``(reason, message, payload_extras)`` constructor signature is
  preserved (~8 internal raise sites in providers do not change).
  Structured fields auto-derive from ``reason`` via the per-reason
  prose table `_LLM_REASON_PROSE` — same pattern as PR-B's
  `_KEYWORD_WORKAROUNDS`. Five reason codes have dedicated prose
  (`llm.parse_error`, `llm.schema_violation`, `llm.fixture_missing`,
  `llm.rate_limited`, `llm.network_error`); unknown reasons fall
  through to a generic-but-format-compliant fallback.

- `ToolError(ExecutionError, Exception)` — the tool-side carrier.
  Same shape as LLMBehaviorError. Six reason codes with dedicated
  prose (`tool.timeout`, `tool.network_error`, `tool.invalid_input`,
  `tool.invalid_output`, `tool.execution_error`, `tool.fixture_missing`).

- `UnknownToolError(ExecutionError, RuntimeError)` — direct
  structured construction. Multi-inherits `RuntimeError` for back-compat.
  Updated the one runtime call site to pass `tool_name`, `behavior_name`,
  and `declared_tools` so the error names the mismatch concretely
  instead of just the offending tool.

**New leaf (1 class):**

- `ApprovalNotFoundError(ExecutionError, LookupError)` — was bare
  `LookupError` at 2 sites in `runtime.approve()`. Multi-inherits
  `LookupError` for back-compat. Context includes `pending_count` so
  the message can say "There are currently 2 pending approvals;
  none match" — useful when the user has typo'd an approval id while
  approvals do exist.

**Considered-not-created (worth documenting):**

The v1.0 plan listed `BehaviorFailedError` and `BudgetExhaustedError`
as expected ExecutionError leaves. Neither exists today, and PR-D
deliberately doesn't create them. The framework's design uses
events (not exceptions) for non-fatal stops:

- Behavior failures emit ``behavior.failed`` events with the
  original exception preserved in the payload (CONTRACT v0.6 #13).
  Downstream code reads the event; there's no exception class
  escaping to user code.
- Budget exhaustion emits ``runtime.budget_exhausted`` and the
  runtime stops gracefully. No exception.

Adding exception classes here would change the design pattern, not
just rename existing surface. Out of scope for the rewrite series.

**Flag-not-migrate (deferred to other PRs):**

- `MissingProviderError` (RuntimeError) — registration-time → PR-E
- `MissingToolError` (RuntimeError) — registration-time → PR-E
- Bare ValueError / LookupError around behavior/tool/pack lookups in
  `runtime.py` — registration-time → PR-E
- Bare ValueError / TypeError around config args — PR-F
- Bare RuntimeError at `runtime.py:1928` (fork requires SQLite-backed
  runtime) — runtime configuration constraint → PR-F
- Scheduler ValueError around `activate_after` parsing — behavior
  registration → PR-E

**Snapshot files:**

- `llm_behavior_error__parse_error.txt`
- `llm_behavior_error__schema_violation.txt`
- `llm_behavior_error__fixture_missing.txt`
- `tool_error__timeout.txt`
- `tool_error__execution_error.txt`
- `unknown_tool_error.txt`
- `approval_not_found.txt`

Tests added: 13 in `tests/test_errors_format.py`.

450 tests pass (437 + 13). All v0–v0.9 tests pass unchanged. The
diligence demo still runs end-to-end with byte-identical trace.

**Hidden-surface count update:**

- PR-A (ReplayError): 0 new leaves
- PR-B (PatternError): 0 new + 17 keyword-workaround branches
- PR-C (StorageError): 4 new + 4 flagged
- PR-D (ExecutionError): **1 new** + 6 flagged (most flagged-to-other-PR
  of any PR so far; the audit confirmed the existing scope is small
  and the runtime/dispatch raise cluster is properly registration- or
  configuration-flavored, not execution-flavored)

Running total: 5 new leaves + ~17 in-message prose-table branches +
10 flagged-for-other-PRs. Audit value remains positive but trending
toward "the existing scope already covers most of the framework";
PR-E and PR-F should see the bulk of remaining migration since
they're where the bare-builtin clusters live.

### v1.0 PR-E landed (RegistrationError, largest by class count)

PR-E audit produced **8 new leaves + 7 re-parented + 22 partial
migrations + 7 flagged-not-migrated**, organized below by sub-category
as a structured audit report. Every raise in scope was confirmed
caller-actionable per CONTRACT v1.0 #4b; no events created.

#### Sub-category 1: LLM / Tool registration (2 re-parented)

- **`MissingProviderError(RegistrationError, RuntimeError)`** — was
  bare RuntimeError. Now constructed with optional `behavior_name=`;
  recovery shows both `AnthropicProvider()` and
  `RecordedLLMProvider(...)` construction.
- **`MissingToolError(RegistrationError, RuntimeError)`** — was bare
  RuntimeError. New signature `(tool_name, *, behavior_name=,
  registered=)`. The two call sites pass `registered=tuple(
  tool_registry.keys())` so the rendered message enumerates the
  available tools.

#### Sub-category 2: Pack registration (5 re-parented, 1 new)

The five Pack* registration leaves multi-inherit
`(RegistrationError, PackError)` so `except PackError` and
`except RegistrationError` both catch them. `PackSchemaViolation`
stays PackError-only — it fires at add_object, not load_pack — and
migrates to structured format in PR-G.

- `PackValidationError(RegistrationError, PackError)` — re-parented
- `PackConflictError(RegistrationError, PackError)` — re-parented;
  two high-traffic call sites in `loader.py` migrated to structured
  format (behavior + tool name conflict). Recovery enumerates the
  three concrete actions (pick one, rename, separate runtime).
- `PackVersionConflictError(RegistrationError, PackError)` —
  re-parented; the `loader.py` site migrated.
- `PackSettingsMissingError(RegistrationError, PackError)` — re-parented
- `PackPromptLoadError(RegistrationError, PackError)` — re-parented
- `PackNotFoundError(RegistrationError, LookupError)` — **new**.
  Replaces bare LookupError at `packs/__init__.py:782`. Recovery
  shows `pip show`, `discover()`, and the exact pyproject.toml
  entry-point declaration.

Each Pack* class also gained its own `_doc_slug` so the
`More:` URL points at a class-specific doc page
(`pack-conflict-error`, `pack-version-conflict-error`, etc.) instead
of the generic `registration-error`.

**Partial migration note:** 22 of the 28 Pack* registration-error
raise sites are unmigrated — they continue using the legacy
single-message ActiveGraphError __init__ branch (format-noncompliant
but valid). PR-E migrated only the 2 high-value loader.py sites where
the rich pack-conflict context was worth the prose. The classes are
in the v1.0 hierarchy; messages catch up incrementally as a
v1.0-rc1 follow-on.

#### Sub-category 3: Runtime lookup (4 new)

- **`BehaviorNotFoundError(RegistrationError, LookupError)`** — was
  bare LookupError at 3 sites in `runtime.get_behavior()`. New
  signature `(name, *, registered=, pack_state=)` carries the
  candidate list so "What failed:" names actual registered behaviors.
- **`AmbiguousBehaviorError(RegistrationError, ValueError)`** — was
  bare ValueError at 1 site. Names which packs collide. Recovery
  shows the canonical form using one conflicting pack as a copy-paste
  example.
- **`ToolNotFoundError(RegistrationError, LookupError)`** — was bare
  LookupError at 3 sites. Symmetric with `BehaviorNotFoundError`.
- **`AmbiguousToolError(RegistrationError, ValueError)`** — was bare
  ValueError at 1 site. Symmetric with `AmbiguousBehaviorError`.

#### Sub-category 4: Scheduler + Runtime construction (2 new)

- **`InvalidActivateAfter(RegistrationError, ValueError)`** — was
  bare ValueError at 5 sites in `scheduler.parse_activate_after`.
  Single class with a `kind` discriminator (bool-not-int, wall-clock,
  unparseable, wrong-type, out-of-range). Each variant has its own
  recovery prose; the "Why:" is uniform (event-count not wall-clock,
  CONTRACT v0.7 #13).
- **`InvalidToolRegistration(RegistrationError, TypeError)`** — was
  bare TypeError at `runtime.py:381`. Constructor takes the offending
  value; "What failed:" shows the value and its type. Recovery shows
  the @tool decorator pattern.

#### Sub-category 5: Optional-dependency import-time (1 new, shared)

- **`MissingOptionalDependency(RegistrationError, ImportError)`** —
  was 3 bare `raise ImportError(...)` sites:
  - `activegraph/packs/__init__.py:52` (pydantic missing)
  - `activegraph/store/postgres.py:73` (psycopg missing)
  - `activegraph/observability/prometheus.py:95` (prometheus_client
    missing)

  Single shared class; construct with `package=`, `feature=`,
  `extras=`. Recovery prose builds the install line from `extras`
  (`pip install 'activegraph[<extras>]'`). Lives in
  `activegraph/errors.py` rather than a topic module since it's
  cross-cutting — the only registration leaf at the base-module
  level.

#### Audit findings — flagged-not-migrated (deferred to other PRs)

- `postgres.py:107` TypeError (bad target type) → **PR-F**
- `runtime.py:166` RuntimeError (`ctx.propose_object` outside
  context) → **PR-F**
- `runtime.py:1938` RuntimeError (fork requires SQLite) → **PR-F**
- `runtime.py:257, 1464, 1790, 1803` ValueError (argument validation)
  → **PR-F**
- `graph.py:240` RuntimeError ("graph already has a store") → **PR-F**
- `graph.py:544` ValueError (patch not proposed) → **PR-G** or
  v1.0-rc1 follow-on (patch lifecycle, post-PR-D territory)
- `graph.py:766` ValueError (unknown where operator) — internal
  evaluator → v1.0-rc1 follow-on (warrants framework-version context)

#### Snapshot files (13, reverse-audit-order)

Written hardest-first per the PR-E discipline note: the multi-pack
interaction leaves were written first so the standard becomes the
floor for the mechanical ones, not the ceiling.

1. `pack_version_conflict.txt` — multi-pack version interaction
2. `pack_conflict__behavior.txt` — multi-pack canonical-name conflict
3. `ambiguous_behavior.txt` — short-name resolution
4. `ambiguous_tool.txt` — same, tool side
5. `pack_not_found.txt` — entry-point discovery
6. `missing_optional_dependency__postgres.txt` — cross-cutting
7. `missing_provider.txt`
8. `missing_tool.txt`
9. `behavior_not_found.txt`
10. `tool_not_found.txt`
11. `invalid_activate_after__wall_clock.txt`
12. `invalid_activate_after__unparseable.txt`
13. `invalid_tool_registration.txt`

#### Tests, suite total, smoke

Tests added: 16 in `tests/test_errors_format.py` (13 snapshots + 3
hierarchy / back-compat assertions, including a check that
`PackConflictError` and `PackVersionConflictError` retain `except
PackError` lineage).

466 tests pass (450 + 16). All v0–v0.9 tests pass unchanged. The
diligence demo runs end-to-end with byte-identical trace output.

#### Hidden-surface count update

- PR-A (ReplayError): 0 new leaves
- PR-B (PatternError): 0 new + 17 keyword-workaround branches
- PR-C (StorageError): 4 new + 4 flagged
- PR-D (ExecutionError): 1 new + 6 flagged
- **PR-E (RegistrationError): 8 new + 22 partial-migration + 7 flagged**

Running total: **13 new leaves** + ~17 prose-table branches + 22
partial-format Pack* sites + ~17 flagged for other PRs / follow-ons.

**v1.1 planning signal:** at 13 new leaves with 22 partial migrations
still pending, this is the data the running count was designed to
surface. **v1.1 should have an explicit "error-completeness"
milestone**: finish the 22 partial Pack* migrations, address the
4 deferred DB-error wrappers from PR-C, write recovery prose for the
2 internal-evaluator cases that warrant framework-version context, and
consolidate any PR-F / PR-G flagged items still pending after rc1.

The 13 new leaves alone vindicates the audit-as-series approach: a
mechanical find-replace across the 50+ pre-PR-A error sites would
have produced 0 new leaves and 0 flagged-for-follow-on findings.

### v1.0 PR-F landed (ConfigurationError + cross-category audit)

PR-F audit looked at 9 candidate raise sites that were flagged as
ConfigurationError on the PR-E hand-off list. Per the PR-F review
discipline note ("Don't force fit. If a raise site looks like a
configuration problem but is actually an execution-time invariant
violation, classify it correctly"), the audit produced a
**cross-category finding**:

- **6 sites** → genuinely `ConfigurationError` (construction-time
  argument validation, runtime backend constraints)
- **2 sites** → reclassified to `ExecutionError` (execution-time
  invariant violations that surfaced during the PR-F audit)
- **1 site** → deferred to v1.0-rc1 follow-on (internal evaluator
  inconsistency that warrants framework-version context, similar to
  PR-B's two internal cases)

This is the cleanest validation of the events-not-exceptions
principle yet — the audit's first classification was wrong, and the
principle's "exceptions interrupt control flow, events extend the
audit trail" framing surfaced the right one on second reading.

#### ConfigurationError leaves (3 new)

- **`InvalidRuntimeConfiguration(ConfigurationError, ValueError)`** —
  catch-all for argument-shape problems at construction or method-call
  time. Used by 4 sites:
  - `runtime.py:257` — conflicting `persist_to=` and `store=`
  - `runtime.py:1460` — `recent < 0` in `status()`
  - `runtime.py:1812` — `save_state(path=X)` when already attached to Y
  - `runtime.py:1825` — `save_state()` with no store and no path
  Construct with summary + structured fields; recovery prose is
  per-site, not table-driven (each misconfiguration has a different
  fix).
- **`InvalidArgumentType(ConfigurationError, TypeError)`** — wrong
  type at construction. Used by 1 site:
  - `postgres.py:107` — PostgresEventStore target not URL / Connection
    / ConnectionPool
  Multi-inherits TypeError.
- **`IncompatibleRuntimeState(ConfigurationError, RuntimeError)`** —
  operation requires a runtime state that's not satisfied. Used by
  2 sites:
  - `runtime.py:1960` — fork() requires SQLite-backed runtime
  - `graph.py:240` — attach_store when one is already attached
  Recovery prose for the fork case flags the Postgres-native-fork
  gap as a v1.1 follow-on (the primitive shape is known but needs
  Postgres operational experience to land).

#### Cross-category ExecutionError leaves (2 new)

These were flagged to PR-F by PR-E's audit, but PR-F's closer reading
reclassified them. They live in `activegraph/runtime/exec_errors.py`
alongside PR-D's `ApprovalNotFoundError`.

- **`RuntimeContextRequiredError(ExecutionError, RuntimeError)`** —
  `ctx.propose_object` (or another ctx method requiring the runtime)
  was called from a behavior whose context isn't runtime-bound.
  Fires inside a running behavior — execution-time, not
  construction-time. Recovery prose explains the "test fixture
  mocked the ctx but not the runtime" pattern that produces it.
- **`InvalidPatchLifecycleState(ExecutionError, ValueError)`** —
  `graph.apply_patch(patch_id)` called on a patch that isn't in
  `'proposed'` state. Fires during the patch lifecycle, mid-execution.
  Recovery prose explains that re-applying an applied patch would
  break the replay contract (duplicate `patch.applied` event).

Both leaves' recovery prose cross-references
`/concepts/failure-model` — the new doc page from CONTRACT v1.0 #4b
addendum. Snapshot tests verify the URL appears.

#### Deferred to v1.0-rc1 follow-on

- `graph.py:766` `ValueError("unknown where operator: X")` —
  internal evaluator inconsistency. Same shape as PR-B's two
  internal-evaluator cases that got framework-version context. Defers
  to a v1.0-rc1 follow-on commit that adds framework-version context
  to internal-bug raises across the framework (PR-B's two,
  graph.py's one, possibly more found during the doc-site phase).

#### Snapshot files (8, reverse-audit-order)

Hardest first: the two cross-category ExecutionError leaves were
written first because their classification was the substantive
finding of PR-F's audit. The mechanical ConfigurationError leaves
inherited the standard.

1. `runtime_context_required.txt` (cross-category, ExecutionError)
2. `invalid_patch_lifecycle_state.txt` (cross-category, ExecutionError)
3. `incompatible_runtime_state__fork.txt`
4. `incompatible_runtime_state__attach_store.txt`
5. `invalid_argument_type__postgres_target.txt`
6. `invalid_runtime_config__conflicting_args.txt`
7. `invalid_runtime_config__missing_arg.txt`
8. `invalid_runtime_config__out_of_range.txt`

#### Tests, suite total, smoke

Tests added: 14 in `tests/test_errors_format.py` (8 snapshots + 3
hierarchy assertions + 1 cross-category classification check + 2
back-compat builtin-base assertions). Plus a check that the two
cross-category leaves' recovery prose references
`/concepts/failure-model` per the contract addendum.

480 tests pass (466 + 14). All v0–v0.9 tests pass unchanged. The
diligence demo runs end-to-end with byte-identical trace.

#### Hidden-surface count update

- PR-A (ReplayError): 0 new
- PR-B (PatternError): 0 new + 17 prose branches
- PR-C (StorageError): 4 new + 4 flagged
- PR-D (ExecutionError): 1 new + 6 flagged
- PR-E (RegistrationError): 8 new + 22 partial + 7 flagged
- **PR-F (ConfigurationError + cross-category): 5 new (3 Config, 2 ExecutionError) + 1 flagged**

Running total: **18 new leaves** + 17 prose branches + 22 partial +
~18 flagged-for-follow-on.

**v1.1 error-completeness milestone scope (now firmly real):**

1. The 22 partial Pack* migrations (PR-E)
2. The 4 deferred DB-error wrappers (PR-C)
3. The internal-evaluator framework-version follow-on (3 sites:
   PR-B's two + PR-F's one in graph.py:766)
4. Any PR-G flagged items not done by rc1
5. Real-user-test gate output (item 1 in the rc1 → v1.0 transition)

#### Discipline win

The discipline note that the audit's job is to surface what raise
sites should have been classified as, not to force them into the
target category, produced 2 reclassifications in 9 sites — a 22%
correction rate. Both reclassifications happened because the
events-not-exceptions principle (CONTRACT v1.0 #4b) gave the audit a
sharper question than "is this Configuration?": it asks "is this
caller-actionable at static configuration time, or is the caller
already executing when it fires?" The Configuration vs. Execution
distinction collapses to that single question.

### v1.0 PR-G landed (PackError + internal-bug consistency pass)

The last error-rewrite PR. Two subsections per the PR-G discipline
note — the bundled scope covers two different question types ("what
should this raise become?" vs. "are these three already-migrated
messages consistent?") and the description keeps them separate so
reviewers know which finding belongs to which.

#### Subsection 1: PackError category — PackSchemaViolation migration

`PackSchemaViolation` is the lone runtime-shape leaf in the
PackError category (every other Pack* class is registration-time and
migrated in PR-E). It fires from `graph.add_object` and
`graph.add_relation` when data doesn't match the pack's declared
schema, after the pack has loaded.

Migrated to structured format with three factory class methods —
same shape as PR-B's `UnsupportedPatternError.refused_feature` /
`syntax_error`:

- `PackSchemaViolation.for_object(object_type, validation_error, pack_name)`
  — Pydantic ValidationError from a declared object schema
- `PackSchemaViolation.for_relation_source(relation_type, source_type, allowed, pack_name)`
  — relation source type not in declared allowed list
- `PackSchemaViolation.for_relation_target(...)` — symmetric

All three call sites in `activegraph/packs/loader.py` migrated. The
factory methods carry `pack_name` so the message names which pack
declared the schema — useful in multi-pack runtimes for triage.

Recovery prose includes a concrete code example showing how to
inspect the declared schema (`p.object_types`-style introspection),
which is the operator's fastest path from "my add_object failed" to
"oh, the field type is wrong."

#### Subsection 2: Internal-bug consistency pass

Three pre-existing internal-bug raise sites had drifted into three
slightly different prose shapes — written across PR-B (two in
`patterns.py`) and PR-F (one in `graph.py`, deferred at the time).
PR-G normalizes them.

**Shared helper:** `activegraph.errors.internal_bug_fields(...)`
produces uniform structured fields for any internal-bug raise.
Context dict shape locked at:

- `internal: True`
- `framework_version: activegraph.__version__`
- `internal_error_location: <module>:<function> (<discriminator>)`
- `report_url: <GitHub new-issue URL constant>`
- plus per-site keys from `extra_context`

Recovery prose locked at:

> This is a framework bug, not a problem with your code.
> Please file an issue and include the framework version, the
> internal error location, and the full message above:
>     https://github.com/yoheinakajima/activegraph/issues/new
>
>   framework version:   activegraph X.Y.Z
>   internal location:   <module>:<function> (<discriminator>)

**Three sites normalized:**

- `activegraph/runtime/patterns.py:_eval_where` (unknown comparison
  operator) — was an UnsupportedPatternError raise; now uses the
  helper, prose is uniform with the other two.
- `activegraph/runtime/patterns.py:_eval_where` (unrecognized AST
  node) — same.
- `activegraph/core/graph.py:evaluate_where` (unknown where
  operator) — was a bare ValueError; now an
  `InternalEvaluatorError(ExecutionError, ValueError)` (new class)
  using the helper.

**`GITHUB_NEW_ISSUE_URL` constant** in `activegraph/errors.py` is
the single source of truth for the issue-filing URL across all
internal-bug raises. Changes (if we ever migrate hosts) update one
place.

**New leaf:** `InternalEvaluatorError(ExecutionError, ValueError)`.
The pattern-evaluator's two internal-bug raises keep using
`UnsupportedPatternError` (natural PatternError category) since
that's where the user would look. The graph view-filter case gets
its own class since the natural category is ExecutionError.
Different classes, identical prose via the shared helper.

**Snapshot tests:** three for PackSchemaViolation (object, relation
source, relation target), two for the internal-bug pattern + graph
sites, plus uniformity assertions verifying all three internal-bug
sites use the same context-dict shape, same recovery prose
substrings, and the same GitHub URL constant. The uniformity
assertion is the load-bearing test for this subsection — it catches
prose drift before it ships.

#### Snapshot files (5)

PackError subsection:

- `pack_schema_violation__object.txt`
- `pack_schema_violation__relation_source.txt`
- `pack_schema_violation__relation_target.txt`

Internal-bug subsection:

- `internal_bug__pattern_unknown_op.txt`
- `internal_bug__graph_view_unknown_op.txt`

(The second pattern internal-bug case — unrecognized AST node — is
covered by the uniformity assertion rather than its own snapshot;
the prose is structurally identical to the unknown-op case, so a
third snapshot adds noise without adding signal.)

#### Tests, suite total, smoke

Tests added: 9 in `tests/test_errors_format.py` (5 snapshots + 1
uniformity assertion across the three internal-bug sites + 1
helper-shape assertion + 1 PackSchemaViolation lineage assertion +
1 InternalEvaluatorError hierarchy assertion).

489 tests pass (480 + 9). All v0–v0.9 tests pass unchanged. The
diligence demo runs end-to-end with byte-identical trace.

#### Hidden-surface count — series final tally

- PR-A (ReplayError): 0 new
- PR-B (PatternError): 0 new + 17 prose branches
- PR-C (StorageError): 4 new + 4 flagged
- PR-D (ExecutionError): 1 new + 6 flagged
- PR-E (RegistrationError): 8 new + 22 partial + 7 flagged
- PR-F (Config + cross-category Exec): 5 new + 1 flagged
- **PR-G (PackError + internal-bug): 1 new + 3 normalized**

**Series final: 19 new leaves migrated to structured v1.0 format.**

The internal-bug consistency pass closed v1.1 follow-on item #3
(the 3 internal-bug sites needing framework-version context) before
v1.0-rc1 ships — every internal-bug site now uses the shared
helper and produces uniform GitHub-Issue-ready output.

#### v1.1 error-completeness milestone scope (after PR-G)

1. The 22 partial Pack* migrations (still partial; v1.1 picks up
   with fresh attention)
2. The 4 deferred DB-error wrappers (PR-C)
3. ~~The internal-evaluator framework-version follow-on~~ —
   **closed by PR-G**
4. Real-user-test gate output (from the rc1 → v1.0 transition)

Net v1.1 scope: ~30 items remaining (down from the projected 41+
after PR-F).

#### What the PR series produced overall

Seven PRs (PR-A through PR-G), three CLI follow-ons (the inspect
flags + fork --record + migrate --skip-corrupted + version-sync gate),
two CONTRACT amendments (#4b events-not-exceptions, #5 failure-model.md):

- 19 new exception classes under the v1.0 hierarchy
- 17 keyword/reason workaround tables (PR-B + PR-D)
- 7 category bases + 1 cross-cutting MissingOptionalDependency
- 1 shared internal-bug helper closing 3 sites uniformly
- All 384 v0–v0.9 tests pass unchanged
- 105 new tests in tests/test_errors_format.py
- 33 snapshot files

Next: doc-site phase. Build mkdocs-material setup, then write
`failure-model.md` as the canonical reference for the principle that
shaped the audit, then the per-error reference pages (one per leaf;
the slug URLs from every snapshot resolve to a real page), then the
auto-generated API reference. After that: quickstart command, 10-min
tutorial, mypy gate, docstring gate, changelog, rc1.

### Series-completion note: the principle as audit instrument

CONTRACT v1.0 #4b (events-not-exceptions) was locked before PR-E to
give the audit a rule to apply. It turned out to be more than
documentation — it was the **audit instrument** that produced PR-F's
22% reclassification rate and PR-D's "considered-then-rejected
BehaviorFailedError / BudgetExhaustedError" finding. Both were
caught because the principle gave the audit a sharper question than
"is this the right category?": it asks "does this fire when the
caller can reasonably catch and act on it at construction time, or
is the caller already executing when it fires?"

Without the principle, those classifications would have been made
case-by-case across PRs, drifting silently. With it, the audit had
a single rule to point at — and the rule did work.

**Preserve the principle as audit instrument for v1.1.** The 22
partial Pack* migrations from PR-E will be re-audited under the
same principle when v1.1's error-completeness milestone runs. Some
of them may turn out to be event-shaped (a `PackPromptLoadError`
that fires during pack reload after the runtime is up may be a
candidate for emit-event-instead, for instance). Re-apply the
principle as the framing question, not just as the category map.

### v1.0 #4c. Voice consistency across error messages and doc pages

The per-error doc pages (one per leaf at `docs/reference/errors/<slug>.md`)
are the long form of the error message. A reader hitting the page
from an error message's `More:` link should feel like they're
reading the same author — same active, declarative, invariant-naming
voice locked in CONTRACT v1.0 #3.

**Voice ceiling extends from error messages to doc pages.** If a
per-error page drifts toward clinical reference-manual voice ("This
error is raised when..."), the error message starts to feel like
marketing for documentation that doesn't deliver. The doc page
should answer:

- **When does this fire?** Names the runtime condition concretely.
- **What causes it?** The invariant being protected, not the
  mechanism of the check (same as the error's `Why:`).
- **How do I diagnose?** Pointer to `activegraph inspect`-style
  commands, log fields to look at, surrounding events to check.
- **How do I fix it?** The same recovery as the error's
  `How to fix:`, expanded with examples and edge cases.
- **What's related?** Cross-references to sibling errors in the
  same category and to `/concepts/failure-model`.

Per-error pages snapshot-review the same way error snapshots do:
read the page, ask whether the voice is the same as the error
message it's the long form of. If not, send it back.

### v1.0 #4d. Broken-link CI gate (doc-site phase burndown)

`tests/test_doc_links.py` is the broken-link CI precursor. It
extracts every URL appearing in error message snapshots, CONTRACT
cross-references, README links, and inter-page doc references, then
maps each to its expected `docs/<section>/<page>.md` source path.
Missing pages fail the test loud with a per-URL report.

Initial state when the precursor landed: **33 missing pages**.
That's the doc-site phase's burndown target. Each PR that adds doc
pages turns red checks green. When the test passes, the doc site is
complete enough to deploy.

Plus a centralization check (`test_docs_base_url_is_centralized`)
that catches hardcoded `https://...` URLs in the package — every
doc-site URL must be constructed from `activegraph.errors.DOCS_BASE_URL`
so the github.io→docs.activegraph.dev cutover is a one-line edit.

Plus an orphan-page warning (`test_docs_orphans_are_reported_as_warnings`)
that lists doc pages no error message, CONTRACT section, README, or
other doc page references. Orphans are warnings, not failures —
some are landing pages — but the maintainer sees the report and
decides whether each orphan is intentional or doc-rot.

**Doc-site phase build order.** With the broken-link gate failing
loud, the order becomes a measurable burndown:

1. `tests/test_doc_links.py` lands first (the gate). **Done.**
2. mkdocs-material setup + GitHub Pages workflow + `CNAME` for
   docs.activegraph.dev (with github.io fallback).
3. `docs/concepts/failure-model.md` — the most-referenced page;
   resolves multiple cross-references in one PR.
4. Per-error reference pages in PR order:
   - `docs/reference/errors/replay-divergence-error.md` (PR-A
     reference, written first to lock the per-error voice)
   - PR-B's pages (UnsupportedPatternError)
   - PR-C's pages (5 leaves)
   - PR-D's pages (4 leaves)
   - PR-E's pages (12 leaves)
   - PR-F's pages (5 leaves)
   - PR-G's pages (PackSchemaViolation + InternalEvaluatorError)
5. The remaining concepts/ + guides/ + cookbook/ + about/ pages.
6. `mkdocstrings`-driven API reference under `docs/reference/api/`.

After the burndown reaches zero (and the orphan warnings are
resolved or accepted), the doc site is ready for v1.0-rc1.

## v1.0 #4b. Events-not-exceptions principle (framework-wide)

Surfaced by PR-D's audit and worth locking explicitly before PR-E
runs. This is a framework-wide design rule, not just a v1.0 cleanup:

> **Exceptions are for caller-facing failures the caller can
> reasonably catch and act on. Non-fatal stops — budget exhaustion,
> behavior failures, tool failures, approval denials — are events in
> the log. The distinction: exceptions interrupt control flow; events
> extend the audit trail. When in doubt, an event.**

PR-D nearly added two exception classes (`BehaviorFailedError`,
`BudgetExhaustedError`) that would have undermined this principle.
The framework has used the events-not-exceptions pattern since v0.5
but never wrote it down — load-bearing across nine milestones,
discovered via audit. Locking it now means PR-E onward applies the
rule explicitly:

- **A raise site that should migrate but the right answer is
  "delete this raise, emit an event instead":** delete and emit.
  This is its own kind of audit finding, distinct from "migrate to
  a new exception class." Count it in the hidden-surface tally as a
  "removed-raise" instead of a "new-leaf."

- **A new failure mode discovered during audit:** apply the
  when-in-doubt-an-event rule. If the caller can't reasonably catch
  and act on the failure (it's a side effect of the runtime loop,
  a budget tick, a policy decision), it's an event. If the caller
  is making a direct API call that fails (a lookup miss, a
  schema-validation rejection, a typo in a CLI flag), it's an
  exception.

- **Existing exception classes that turn out to be event-shaped:**
  PR-D considered then rejected creating `BehaviorFailedError` /
  `BudgetExhaustedError`. The negative finding stands. If a future
  PR thinks a similar class is missing, the burden is to demonstrate
  it's caller-actionable, not just to point at the absence.

This principle is downstream from CONTRACT v0.5 (the event log is the
source of truth, replay reconstructs state) and CONTRACT v0.6 #13
(behavior.failed is the canonical structured failure surface). It
unifies their implications into one rule that's easier to apply.

The doc site's `docs/concepts/events.md` (per #5 structure) gets a
paragraph derived from this principle when the doc phase runs. The
text writes itself from the rule.

### v1.0 #4b addendum: canonical doc page is `failure-model.md`

PR-F review confirmed: the principle is cross-cutting (applies to
behaviors, tools, budgets, approvals, replay) so it gets its own page
at `docs/concepts/failure-model.md` rather than being buried under
`events.md` or `behaviors.md`. A reader who hits it on
`behaviors.md` would assume it's a behavior thing; on `events.md`
they'd assume it's an event-log thing. It is neither — it's the
framework's stance on what counts as a recoverable failure.

Snapshot recovery prose in PR-F and PR-G references the URL
`/concepts/failure-model` as the canonical cross-reference target.
The URL is stable from PR-F onward; the page is written during the
doc-site phase. The cross-references work the moment the doc site
builds.

The page covers (~400-600 words):

- The events-not-exceptions principle from #4b
- The exception hierarchy and when to catch what
- The `behavior.failed` / `tool.failed` event types and their
  `reason` fields
- The `runtime.budget_exhausted` event flow
- The `approval.granted` / `approval.denied` event flow

## v1.0 #5. Doc site structure is the contract

```
docs/
  index.md                    # landing page
  quickstart.md               # 10-minute tutorial
  concepts/
    graph.md
    events.md
    behaviors.md
    relations.md
    patches.md
    views.md
    frames.md
    policies.md
    replay.md
    forking.md
    patterns.md                 # v1.0 doc-site (PR-B batch): pattern
                                # subscriptions as a first-class primitive.
                                # Added to #5 because the cross-reference
                                # from UnsupportedPatternError.syntax_error
                                # recovery prose surfaced the structural
                                # gap — pattern subscriptions have their
                                # own decorator argument, their own CONTRACT
                                # v0.7 #8 subset decisions, their own error
                                # class. Burying them in behaviors.md
                                # undersold a first-class primitive.
    failure-model.md            # v1.0 PR-F: events-not-exceptions principle,
                                # exception hierarchy, behavior.failed /
                                # tool.failed flows, budget / approval events
  guides/
    writing-behaviors.md
    writing-llm-behaviors.md
    writing-tools.md
    pattern-subscriptions.md
    operating-in-production.md
    authoring-packs.md
  reference/
    api/                      # auto-generated from docstrings
    cli/
    errors/                   # one page per error class
    metrics/
    events/
  packs/
    diligence.md
  cookbook/
    common-patterns.md
    debugging.md
    migration-from-v0-7.md
  about/
    architecture.md
    roadmap.md
    contributing.md
    changelog.md
```

Build to this structure. Adding pages is fine; removing or
restructuring is a contract change.

## v1.0 #6. Every code block in docs is a snippet inclusion

No inline code blocks in docs. Every example is a runnable file in
`examples/` that is tested in CI, and the doc page embeds the file
via mkdocs snippet inclusion. This prevents doc rot. Convention is
documented in `CONTRIBUTING.md`.

## v1.0 #7. Errors name names

Every error includes the specific names of the things involved —
behavior name, pack name, object id, event id, file path. Generic
errors are bugs.

Wrong: "Pack conflict detected"
Right: "Pack conflict: 'diligence' and 'research' both declare object type 'Claim'"

Snapshot tests lock specific instances of each error to enforce
this.

## v1.0 #8. No telemetry, no phone-home

The framework runs offline; that's a feature. No analytics in the
quickstart. No version-check pinging. Deployments that want
telemetry add it through the metrics protocol.

## v1.0 #9. Backward compatibility is absolute

All 384 v0–v0.9 tests pass unchanged through every v1.0 PR. The two
trace snapshot files updated in v0.9.1 are the new baseline; no
further drift in v1.0.

## v1.0 #10. What v1.0 deliberately does NOT add

Locked deferral list (post-1.0 or never):

- Web UI (never, per prior decisions)
- Streaming LLM responses (post-1.0)
- Multi-model routing (post-1.0)
- More packs beyond Diligence — Memory pack, Research pack (post-1.0)
- Pack registry or marketplace (post-1.0)
- Error message internationalization (never; English only)
- Video tutorials (separate project)
- Adaptive question generation in Diligence pack (post-1.0 if at all)
- Contradiction resolver in Diligence pack (post-1.0 if at all)
- Richer fixture cardinality (re-affirm: v0.9 contract is 3 companies)
- `--live` quickstart mode (post-1.0; see #C3)

If a v1.0 PR finds itself building toward any of these, stop.

v1.0 is about making the framework discoverable, learnable, and
forgiving. The developer who tries this on a Friday afternoon is
going to decide in the first 10 minutes whether they come back
Monday. Slow down on implementation choices, speed up on user-facing
polish, and remember that adoption-surface work is a different skill
from runtime engineering.


# v1.0.1 — first external user-test patch

**Review status (2026-05-19):** STILL ACCURATE. #5(c) clauses 1–4
(token-counting asymmetry, Anthropic-only tool use, instruction-
based structured output, closed reason taxonomy) all remain
honest non-promises; the v1.1 candidates they named are
consolidated in `v1.1-plan.md` (B-1 OpenAI tool-shape, B-2 native
structured output). Tests for #1–#4 anchor on contract claims
(see review §4).

The first external user-test produced three small findings, all on
the "X is confusing" UX side of the heuristic in HANDOFF.md, none
architectural. The framework's shape held; the polish around three
edges did not. v1.0.1 ships the three fixes as a patch release — no
public-API renames, no CONTRACT amendments to v1.0's own decisions,
no new runtime capability.

## v1.0.1 #1. Public `register()` + `clear_registry()` returns the list

Multi-run scripts that called `clear_registry()` between runs had
no public way to re-populate the registry — the test conftest works
because each test re-imports its own behaviors inline, but scripts
that iterate hypotheses against a captured set of behaviors had to
reach into `activegraph.behaviors.decorators._REGISTRY` directly.
Private-attribute access in user code is the smell.

Fix:

- `clear_registry()` now returns `list[Behavior | RelationBehavior]`
  in registration order — the behaviors that were cleared. v1.0
  returned `None`; callers that ignored the return still work.
- `register(behavior_obj)` is new — appends an already-constructed
  behavior to the global registry. Validates the argument is a
  `Behavior` / `RelationBehavior` / `LLMBehavior` instance.
- `docs/cookbook/multi-run-scripts.md` is new — the recipe is the
  canonical home for the pattern; `clear_registry` and `register`
  docstrings both reference it by name (no hardcoded docs URL —
  the version-sync gate forbids that and the cookbook page name is
  stable across the domain cutover).

The list-return on `clear_registry` is a `None` → `list` API change,
but since the v1.0 signature never returned a meaningful value, no
caller depended on `None`. Documented in the v1.0.1 CHANGELOG entry.

## v1.0.1 #2. LLM auto-instruction includes an example instance

`@llm_behavior(output_schema=SomeModel)` embedded the model's JSON
Schema in the system prompt. The user-test surfaced a failure mode
that v1.0's design didn't anticipate: some models — particularly
smaller / older / non-tool-trained ones — interpret a bare schema as
"return this shape" and echo the schema definition back as their
response. The framework refuses (the schema definition isn't a valid
instance of itself for most schemas), `llm.schema_violation` fires,
and the trace shows the model returning `{"type": "object",
"properties": ...}` where the actual fields should be.

Fix: the system prompt now embeds three things in the schema block
rather than one:

1. The schema (unchanged from v1.0).
2. A synthesized example instance — `example_instance_from_schema`
   walks the JSON Schema and produces a deterministic placeholder
   instance (`"<string>"` for strings, `0` for ints, first variant
   for enums, etc.). The example is content-stable across runs so
   the prompt-hash cache key stays stable across identical inputs.
3. Explicit "Return an INSTANCE that conforms to this schema, NOT
   the schema itself" language. Capitalized for emphasis; the
   doc-site entry explains the failure mode for users who hit it
   despite the prompt fix.

`build_instruction` (the single-sentence task at the end of the
user message) also gains "(NOT the schema definition itself — see
the example above)" so the framing appears in two places.

The prompt-hash changes (the system prompt is longer), but no JSON
LLM fixtures exist on disk so no replay-divergence risk. The
quickstart snapshot's `tokens_in~NNNN` values bump by ~70-130 tokens
per call to absorb the new block; that snapshot was rebaselined
in the v1.0.1 commit.

## v1.0.1 #3. `SQLiteEventStore()` constructor hints at `persist_to=`

The user-test surfaced that `SQLiteEventStore()` with missing or
partial args produced a bare Python `TypeError: missing 1 required
positional argument: 'run_id'`. The user-test reader didn't know
`run_id` was something to mint vs. something to look up vs. something
the framework would generate — three different shapes of next step,
all gated on first looking up "what is a run_id." The framework's
own answer is `Runtime(graph, persist_to='path/to/trace.sqlite')`,
which handles the `run_id` automatically.

Fix: the constructor signature is now
`SQLiteEventStore(path=None, run_id=None)` with a hand-raised
`TypeError` when either is missing:

    SQLiteEventStore requires a run_id. For most cases, use
    Runtime(graph, persist_to='path/to/trace.sqlite') instead,
    which handles run_id automatically. If you need a per-run
    handle (migration, conformance test, trace inspection), pass
    both explicitly: SQLiteEventStore('path/to/trace.sqlite',
    run_id='run_...').

The two-line hint points at the higher-level API for the common
case and names the rare cases where direct construction is the
right answer.

## v1.0.1 #4. `prompt_template=` docstring names what each placeholder contains

The `@llm_behavior(prompt_template=...)` escape hatch was
documented as "accepts `{system}`, `{view}`, `{event}`, and
`{instruction}`" without saying what each placeholder rendered to.
The v1.0.1 #2 doc-site entry needs to reference the same
placeholders by name to recommend `prompt_template=` as the
fallback for schemas the auto-example can't render usefully, so
the decorator docstring grows a four-bullet list naming what
content each placeholder carries (system block content, view
content, event content, instruction content). Concrete enough to
let a reader compose a custom template without first opening
`activegraph/llm/prompt.py`.

## v1.0.1 #5. OpenAI provider + the provider-commitment surface

The v1.0 framework shipped a single concrete `LLMProvider`
implementation (`AnthropicProvider`), which made the
provider-agnostic claim of the `LLMProvider` Protocol read as
theoretical rather than realized. The first external user-test
gate per CONTRACT v1.0 #C4 didn't surface this as a friction point
the way #1–#4 did, but the implicit cost — readers seeing one
provider and inferring a soft lock-in — is the same shape of
adoption-surface friction the rest of v1.0.1 patches. v1.0.1 #5
ships a second concrete provider (`OpenAIProvider`) and locks in
three contract clauses about how providers commit to the
framework.

### (a) Providers commit through `LLMProvider`; additions are non-breaking

The `LLMProvider` Protocol in `activegraph/llm/provider.py`
(CONTRACT v0.6 #3, extended v0.7) is the framework's commitment
surface for LLM providers. Three methods, keyword-only, narrow on
purpose: `complete()`, `estimate_cost()`, `count_tokens()`. A new
concrete provider is an additive change — every existing
`@llm_behavior` keeps working unchanged, and a runtime swapping
`AnthropicProvider()` for `OpenAIProvider()` doesn't touch any
behavior definition.

The Protocol is locked at v0.6 #3; v1.0.1 #5 doesn't widen it.
Capabilities a single provider's API exposes but the others
don't — OpenAI's native `response_format` mode, Anthropic's
server-side `count_tokens` — stay inside the concrete provider's
implementation and don't leak into the Protocol surface. New
providers join by implementing the three methods; they don't
negotiate the abstraction.

### (b) Three-extras install pattern

`pyproject.toml` extras follow a three-pattern shape:

- `[llm]` pulls every shipped provider's SDK (currently
  `anthropic>=0.40`, `openai>=1.0`, `tiktoken>=0.7`). A single line
  for the common case where the operator wants the framework's
  full LLM surface available.
- `[anthropic]` pulls Anthropic only.
- `[openai]` pulls OpenAI + `tiktoken`.

The per-provider aliases keep production installs minimal for
operators who run only one provider in deployment. The `[all]`
roll-up includes everything from `[llm]` plus the persistence and
metrics extras.

Future providers extend `[llm]` and add their own per-provider
alias. The pattern is the contract; the specific provider list
grows.

### (c) Explicit non-promises for v1.0.1

What v1.0.1 #5 does **not** commit to. Documenting limitations as
contract clauses rather than discovering them as user friction
later is the same discipline as v1.0's honesty section in the
technical essay.

1. **Token counting is provider-dependent.** `AnthropicProvider`
   uses the SDK's `messages.count_tokens` endpoint (server-side
   roundtrip, exact counts). `OpenAIProvider` uses `tiktoken`
   client-side when available and a `chars / 4` heuristic when
   not, with a one-time debug log on first heuristic call.
   Operators gating on `budget.max_cost_usd` should install
   `tiktoken` (via `[openai]` or `[llm]`) for accurate accounting;
   the heuristic is the offline fallback. The asymmetry is named,
   not hidden.

2. **Tool use is Anthropic-only in v1.0.1.** `Tool.to_definition()`
   emits the Anthropic `{name, description, input_schema}` shape;
   OpenAI's `{type:"function", function:{name, description,
   parameters}}` shape would require translation in
   `Tool.to_definition()` plus a parallel `tool_calls` extraction
   path in the provider. `OpenAIProvider.complete()` accepts the
   `tools=` kwarg for Protocol compatibility but raises
   `LLMBehaviorError(reason="llm.network_error")` with a v1.1
   pointer when the list is non-empty. Tool-shape translation is
   scheduled under v1.1 #7-and-beyond.

3. **Native structured-output modes are v1.1 candidates.** Both
   providers use the framework's instruction-based structured-
   output path: the schema and example instance are embedded in
   the system prompt by `build_system_prompt` (v1.0.1 #2), and the
   provider parses JSON out of the response via the shared
   `parse_structured_response` helper. OpenAI's
   `response_format={"type":"json_schema",...}` mode and any
   future provider's equivalent stay v1.1 candidates — picking
   them up would diverge the providers' latency profiles, cache-
   key semantics, and error paths in ways that warrant their own
   decision rather than a silent in-PR swap.

4. **No new reason codes.** The closed CONTRACT v0.6 #11 taxonomy
   (`llm.parse_error`, `llm.schema_violation`, `llm.network_error`,
   `llm.rate_limited`, `llm.fixture_missing`,
   `budget.cost_exhausted`) is unchanged. OpenAI auth failures
   land in `llm.network_error` with the exception message
   preserved verbatim — same shape Anthropic uses for the same
   failure mode. Expanding the reason taxonomy would be a CONTRACT
   change to v0.6 #11, out of scope for a provider addition.

### Why this lands in v1.0.1, not v1.1

v1.0.1's framing is "patch release based on first external user-
test." The user-test #1–#4 fixes addressed three direct friction
points and one doc-coverage gap. The provider lock-in finding is
implicit (no user test step exercises it) but adoption-surface in
the same sense: readers who scan the LLM docs see one provider and
extrapolate. Shipping the second provider in the same release
closes that implicit gap without bumping to v1.1.

This stays consistent with "What v1.0.1 deliberately does NOT
touch" — the closed reason taxonomy, the `LLMProvider` Protocol,
the prompt-construction pipeline, the public class hierarchy.
`OpenAIProvider` extends the registry of concrete providers,
which is open by design; the Protocol surface they target stays
locked.

## What v1.0.1 deliberately does NOT touch

The CONTRACT v1.0 ban on new runtime capability still applies —
v1.0.1 is a patch release on the adoption-surface milestone, not
the start of v1.1. Specifically:

- No changes to the public class hierarchy. `register()` is a new
  function on `activegraph`; it doesn't change any existing class.
- No CONTRACT amendments to v1.0 decisions. The schema-block change
  is inside the v0.6 #6 prompt-assembly contract (which already
  allowed the system prompt's content to evolve as long as the
  source order and hash-stability properties held).
- No widening of the SQLiteEventStore API. The signature change
  (`Optional[str] = None`) is internal — every existing caller
  passes both args positionally or by keyword.
- No new CI gate. The four v1.0 gates (mypy --strict, docstring
  coverage, broken-link, version-sync) plus the two rc3 gates
  (wheel-completeness, deploy-verification) all continue to gate;
  v1.0.1 doesn't add a seventh.

CONTRACT v1.1 still owns the broader v1.1 scope (CLI flags, type
completeness, docstring completeness, the v0.5 in-flight loss
follow-ons, etc.). v1.0.1 doesn't reshape that backlog.


# v1.0.2 — second external user-test patch (provider-aware default model)

**Review status (2026-05-19):** STILL ACCURATE post-revision.
**Discipline note**: #1(b) below was originally locked to fire
validation at registration time. The v1.0.2 implementation
actually fired lazily at first `run_goal()`. v1.0.2.post1
corrected the implementation AND revised the prose of #1(b) in
place to the "both binding moments" wording it carries now. The
CHANGELOG carries a dated `[v1.0.2.post1]` entry; this CONTRACT
section was edited in place rather than appended to. See review
§4 (the post1 tests in `test_llm_default_model.py` Section (g) are
the canonical model for boundary-anchored tests) and review §7 #1
(discipline finding on in-place revisions vs dated amendment
sections). Pack-load-time validation remains a third binding
moment — filed as v1.1 candidate B-3.

The v1.0.1 release added `OpenAIProvider` as the second concrete
provider and locked the provider-commitment surface (v1.0.1 #5). A
second-round external user-test surfaced three more findings; the
most urgent is the only one that ships in v1.0.2 — it directly
undermines v1.0.1's provider-agnostic claim.

The finding: a user who follows the README's `Runtime(Graph(),
llm_provider=OpenAIProvider())` pattern and writes an
`@llm_behavior` without an explicit `model=` argument gets HTTP 404
from the OpenAI API at first LLM call. The default model on the
decorator was `"claude-sonnet-4-5"` — an Anthropic name, sent
verbatim to OpenAI, which 404s because it doesn't recognize the
model. The error message that lands on `behavior.failed` is the
provider's verbatim 404 prose; nothing in it names the
cross-provider mismatch. The reader has to inspect the decorator,
trace the default, and recognize the model family conflict to
diagnose.

The other two v1.0.1-round findings — `output_schema=` accepting
plain dicts vs Pydantic models, and silent `behavior.failed` UX
when no handler observes the event — need design work and are
scheduled for a later release (v1.0.3 / v1.1). They aren't direct
credibility hits on the v1.0.1 announcement; the default-model
mismatch is.

## v1.0.2 #1. `@llm_behavior(model=)` defaults to the provider's default model

> **Retroactive revision note (added 2026-05-19 by the post-v1.0.3
> contract review).** This section's prose was revised in place by
> v1.0.2.post1 (CHANGELOG dated 2026-05-19) when an external
> spot-check discovered the v1.0.2 implementation fired the
> validation lazily at first `run_goal()` rather than at
> registration time as the original v1.0.2 #1(b) text claimed. The
> "both binding moments" wording carried in §(b) below is the
> post1-corrected form, not the original v1.0.2 lock.
>
> The CHANGELOG carries the dated v1.0.2.post1 entry with the full
> boundary-correction record. CONTRACT now carries the dated
> `### v1.0.2.post1` section below (appended retroactively in
> v1.0.4 #6), sibling to §(a)–§(d); the archeology is restored
> there without further rewriting the corrected prose. This
> top-of-section note remains as the breadcrumb; the in-place
> marker under §(b) is the second breadcrumb at the specific
> revision site.
>
> Standing rule §1 (banner) prevents this pattern from recurring:
> amendments append, never modify.

The `LLMProvider` Protocol gains two additive surfaces. v1.0.1 #5
locked "additions are non-breaking"; v1.0.2 #1 invokes that clause
with the first additive widening since the Protocol shipped at
v0.6 #3.

### (a) `LLMProvider.default_model` attribute

Each concrete provider declares a `default_model: str` — the model
name `complete()` should use when the behavior didn't pin one
explicitly. Shipped values:

- `AnthropicProvider.default_model = "claude-sonnet-4-5"`
- `OpenAIProvider.default_model = "gpt-4o-mini"`

`@llm_behavior(...)` no longer carries a hardcoded model default.
The decorator's `model=` argument becomes optional:

```python
@llm_behavior(name="extractor", output_schema=Claim)
def extractor(event, graph, ctx, llm_output):
    ...
```

is equivalent to passing `model=<provider.default_model>` at
runtime registration. Callers that pass `model="explicit-name"`
keep working byte-identically — the additive change is opt-in by
*omission*, not by reshaping existing call sites.

The runtime resolves the default at `_ensure_registry` time (once
per runtime construction) and stamps it onto the `LLMBehavior`
instance, so `behavior.build_prompt(event, graph)` keeps its
existing pure-function signature for prompt inspection.

### (b) `LLMProvider.recognizes_model(name)` method

*[Revised by v1.0.2.post1. The "both binding moments" wording
below is the post1 form; the original v1.0.2 #1(b) text described
registration-time-only validation but the implementation fired
lazily. See review §7 #1 for the discipline note. CHANGELOG
carries the dated v1.0.2.post1 entry.]*

Each provider implements `recognizes_model(name: str) -> bool`
returning True if `name` belongs to a model family the provider
serves. Shipped definitions:

- `AnthropicProvider.recognizes_model(name)` — True for names
  starting with `"claude-"`.
- `OpenAIProvider.recognizes_model(name)` — True for names
  starting with `"gpt-"`, `"o1-"`, `"o3-"`, or `"o4-"` (the
  OpenAI reasoning-model prefixes that exist in 2026).

The runtime uses these for cross-provider validation. When a
behavior pins `model=` explicitly and the configured provider
doesn't recognize the name, the runtime asks each shipped
provider whether *it* recognizes the name. If exactly one
*other* shipped provider claims it, the runtime raises
`InvalidRuntimeConfiguration` with a structured error naming
both the claimed-by provider and the configured provider and
listing the configured provider's model families.

If no shipped provider recognizes the name (custom models,
fine-tunes, experimental names, internal-deployment names), the
validation passes silently. This is the **permissive default**:
unknown model names are user responsibility, only *recognized
mismatches* fire the diagnostic.

The validation fires at **both binding moments** — whichever sees
both the provider and the behavior present first:

1. **At `Runtime(graph, llm_provider=...)` construction**, against
   every behavior already in the global registry, every explicit
   `behaviors=[...]` argument, and every pack behavior loaded
   subsequently via `runtime.load_pack(...)`. This catches the
   common case where decorators run on import (populating the
   global registry) and `Runtime(...)` is constructed afterwards.
2. **At `register()` / `@llm_behavior`-time**, against every
   live Runtime's provider via a module-level `weakref.WeakSet`
   of constructed Runtimes. This catches the README quickstart
   pattern, where `Runtime(...)` is constructed first against an
   empty registry and decorators run afterwards.

Both moments invoke the same single-behavior cross-provider
check; the bulk-at-construction path also stamps provider defaults
onto `model=None` behaviors per (a). The lazy-at-first-run path
inside `Runtime._ensure_registry()` stays in place as a defensive
double-check for code paths that don't flow through the two
binding moments above (notably pack-loaded behaviors registered
after Runtime construction, which currently fall through to
first-run validation rather than load-pack-time validation —
filed as a v1.1 follow-on if it surfaces as friction).

The `weakref.WeakSet` cleanup is automatic: a Runtime that goes
out of scope without an explicit `close()` is silently removed
from the live-set on the next GC pass. No `Runtime.close()`
method is added — the framework's Runtime lifecycle stays as it
was at v1.0.

### (c) `LLMBehavior.model` becomes `Optional[str]`

The dataclass field changes from `model: str = "claude-sonnet-4-5"`
to `model: Optional[str] = None`. Pickled behaviors from v1.0.1
deserialize cleanly (string values still load); behaviors freshly
decorated at v1.0.2 get `None` until the runtime stamps a default.

`LLMBehavior.build_prompt(event, graph)` keeps its v0.6 #20
contract: pure, no I/O, cheap, callable without a Runtime. When
called on a behavior whose `model` is still `None`, it falls back
to the v1.0.1 hardcoded default (`"claude-sonnet-4-5"`) for
inspection-time hash stability. Real LLM calls always flow through
Runtime, which resolves the configured provider's `default_model`
*before* prompt assembly — so the inspection-time fallback only
shows up when there's no Runtime to consult. Pre-v1.0.2 snapshot
tests that asserted `prompt.model == "claude-sonnet-4-5"` keep
passing.

The fallback in `build_prompt` is documented behavior, not a
hidden coupling: a user inspecting `build_prompt(...)` output to
preview what would be sent in production should construct the
Runtime they actually use first, so the inspection sees the
resolved model. The fallback exists so the v0.6 #20 "callable
without a Runtime" contract still holds for snapshot-testing and
debugging flows that don't construct one.

### (d) Why this is a Protocol widening, not a v0.6 #3 amendment

v1.0.1 #5 (a) read: "The Protocol is locked at v0.6 #3; v1.0.1 #5
doesn't widen it." v1.0.2 #1 *does* widen it — but adds two
additive members rather than reshaping the three existing ones.
Custom providers that pre-date v1.0.2 and don't implement
`default_model` or `recognizes_model` keep working at the
`complete()` / `estimate_cost()` / `count_tokens()` call sites;
the runtime uses `getattr(provider, "default_model", None)` and
`getattr(provider, "recognizes_model", lambda _name: False)` so
the additions are soft requirements. A custom provider that wants
the default-model behavior implements them; one that doesn't,
keeps requiring `model=` on every `@llm_behavior`.

`runtime_checkable` Protocol semantics: `isinstance(p, LLMProvider)`
still passes for custom providers with the three original methods,
because Protocol attribute checks against `runtime_checkable` only
consider members that don't have `default` implementations in the
Protocol body. The new members are declared as optional attribute
+ method shapes; runtime call sites guard with getattr.

### v1.0.2.post1 — validation boundary correction (appended retroactively 2026-05-19 in v1.0.4 #6)

v1.0.2 #1(b) above (post1-corrected form) names "both binding
moments" as the boundary at which cross-provider model validation
fires. That phrasing did not appear in the original v1.0.2 lock —
the original (b) text said "at registration time," and the v1.0.2
implementation fired the validation lazily at first `run_goal()` /
`run_until_idle()` / `run_until()` via `Runtime._ensure_registry()`.
Both the contract's boundary name and the implementation's boundary
were wrong: the contract said one thing, the code did another, and
the v1.0.2 test suite covered the lazy-firing path the implementation
took rather than the registration-time boundary the contract
claimed. An external spot-check during v1.0.2 caught the gap.

v1.0.2.post1 (CHANGELOG dated 2026-05-19, PR #16) corrected the
boundary in the code. The error class, the structured error
message, and the reason were left byte-identical to v1.0.2 — only
the firing boundary moved. This appended section documents the
boundary correction in CONTRACT form. Standing Rule §1 from the
post-v1.0.3 review banner codified the discipline this section
embodies: amendments append, never modify. The (b) prose above
carries the post1-corrected text by virtue of having been revised
in place at v1.0.2.post1 time, before Standing Rule §1 was
established; this appended section is the proper archeological
record that should have landed alongside the post1 release.

**The boundary correction.** Validation fires at both binding
moments — whichever sees both the provider and the behavior present
first — rather than lazily at first `run_goal`:

1. **`Runtime(graph, llm_provider=...)` construction** validates
   against every behavior currently in the global registry, every
   explicit `behaviors=[...]` argument, and every pack behavior
   loaded subsequently via `runtime.load_pack(...)`. This catches
   the import-then-construct pattern: decorators run on import,
   `Runtime(...)` is constructed afterwards.
2. **`@llm_behavior` decoration and `register()`** validate against
   every live Runtime's provider via a module-level
   `weakref.WeakSet` in `activegraph/runtime/_live.py`. This catches
   the construct-then-decorate pattern: `Runtime(...)` is
   constructed first against an empty registry, decorators run
   afterwards — the README quickstart shape.

Both moments invoke the same single-behavior cross-provider
validator factored into `_live.py`. The lazy-at-first-run path
inside `Runtime._ensure_registry()` remains as a defensive
double-check for code paths that bypass both binding moments above
(notably pack-loaded behaviors registered after Runtime
construction, which fall through to first-run validation; filed as
a v1.1 candidate under Theme B in `v1.1-plan.md`).

**The `weakref.WeakSet` pattern.** Live Runtimes are tracked in a
module-level `WeakSet` declared in `_live.py`. A Runtime that goes
out of scope without an explicit `close()` is silently removed on
the next GC pass — no `Runtime.close()` method is added; the
framework's Runtime lifecycle stays as it was at v1.0. Validation
runs *before* the new Runtime is added to the WeakSet, so a
construction that raises `InvalidRuntimeConfiguration` never leaks
into the live-set (matters for pytest exception-traceback
strong-refs, which would otherwise keep a failed Runtime visible
to subsequent `@llm_behavior` decorations in the same test
process).

**Why this section appends rather than revising further.** The
original v1.0.2 #1(b) text said "at registration time" and the
v1.0.2 implementation fired lazily. v1.0.2.post1 revised the (b)
prose in place to read "at both binding moments" so the contract
claim matched the corrected code. In-place revision destroyed the
boundary record — a reader of CONTRACT alone could not see when
the change happened or what it was correcting. Standing Rule §1
(banner) was adopted by the post-v1.0.3 contract review to
prevent this pattern from recurring; this appended section is the
retroactive restoration the rule explicitly names.

**Tests anchor on each binding moment by name.** The post1 test
suite at `tests/test_llm_default_model.py` Section (g) exercises
each binding moment separately. The seven scenarios — decorator-
after-Runtime raises at decoration time, register-after-Runtime
raises at register time, Runtime construction validates against
the registry, failed construction does not leak into the
live-set, two Runtimes with different providers both participate,
fork inherits parent provider as a no-op on the happy path, and
the README construct-then-decorate quickstart pattern — together
form the canonical model for Standing Rule §2: each test's name
carries the binding moment; each assertion exercises the boundary
the contract claim names; the suite would fail under the v1.0.2
lazy-firing implementation regardless of whether the lazy path's
own tests passed.

**What this section does not change.** The
`InvalidRuntimeConfiguration` error class, the structured message
format, the naming of both providers in the diagnostic, the
recognition predicates
(`AnthropicProvider.recognizes_model` / `OpenAIProvider.recognizes_model`),
and `LLMBehavior.model`'s `Optional[str]` type are byte-identical
to v1.0.2 #1. v1.0.2's error message was correct; only its firing
boundary was wrong.

## v1.0.2 deliberately does NOT touch

Same discipline as v1.0.1's section. The patch scope is one
finding, not three:

- No changes to `LLMProvider.complete()`, `estimate_cost()`,
  `count_tokens()` signatures. The three v0.6 #3 / v0.7 methods
  stay locked.
- No changes to the closed CONTRACT v0.6 #11 reason taxonomy. The
  cross-provider validation raises a `ConfigurationError` subclass
  at registration time, not a behavior-failure reason code.
- No changes to the v1.0.1 #2 prompt-assembly shape (schema +
  example instance + "instance not schema" language). The
  schema-block layout is unchanged.
- No changes to `output_schema=` (dict vs Pydantic — scheduled for
  v1.0.3 / v1.1 after design pass).
- No changes to `behavior.failed` UX (silent-failure follow-on
  scheduled for v1.0.3 / v1.1).
- No new CI gate.
- No new public class.
- No `register()` / `clear_registry()` surface changes from v1.0.1.

The v1.0.2 release ships one finding. The other two are tracked in
HANDOFF for the next session, not in this CONTRACT entry — they
need design before they're locked.


# v1.0.3 — comprehensive response to two user-test reports

**Review status (2026-05-19):** STILL ACCURATE. All four findings
carry contract-anchored tests (review §4). The "v1.1 backlog
candidates surfaced by v1.0.3" list at the end of this section is
consolidated in `v1.1-plan.md` Themes B and C — see B-4 (dict-form
`output_schema=`), B-5 (`on_failure=` callback), B-1 (OpenAI tool
translation, restated from v1.0.1 #5(c)), B-6 (fire-once
aggregation), B-3 (pack-load-time validation, restated from
v1.0.2 #1(b)). The `graph.relations()` doc-vs-impl gap noted in
the review work-item premise was **not** filed by v1.0.3; review
§2 Finding A surfaces it as v1.0.4 candidate #1.

v1.0.2.post1 corrected the validation boundary for v1.0.2 #1
(cross-provider model validation now fires at both binding moments,
not lazily). Two independent user-test reports surfaced four more
findings spanning the framework's user-facing API surface. v1.0.3
addresses all four in one release.

The framing — patch release on the adoption-surface milestone —
matches v1.0.1 / v1.0.2. The scope is broader: this is the largest
single release since v1.0.1 (four findings vs. one or three). The
discipline that kept earlier patches small still applies: each
finding gets its own clearly-scoped commit so review is independent
per finding, no CONTRACT amendments below v1.0 are reopened, and
all four CI gates stay green.

Backlog note: v1.0.3 surfaces several v1.1 candidates (dict-form
`output_schema=`, OpenAI tool translation, behavior-level aggregation
triggers for fire-once-on-condition semantics, the on-failure
callback parameter for `run_goal()`). Combined with the existing
v1.1 entries filed in v1.0.1 #5 (c), v1.0.2 #1 (b) (pack-load-time
validation), and the original v1.1 #1–#4 section below, the v1.1
backlog is now real and scattered. v1.0.3 does **not** consolidate
it; a contract-review work item handles backlog consolidation after
v1.0.3 ships. Each v1.0.3 amendment that defers work names the v1.1
candidate explicitly so the consolidation pass can sweep them up.

## v1.0.3 #1. `graph.objects(type=...)` is the canonical query API

External users reach for `graph.objects(type="x")` naturally —
`ctx.view.objects(type="x")` exists and behaves that way inside
behaviors (CONTRACT #11), and the consistency between view-time and
graph-time forms is the path of least surprise. The framework
shipped `graph.query(object_type="x")` instead: different name and
different parameter name. External users hit `AttributeError`
immediately when trying the natural form on a `Graph` object.

The fix is additive and small:

- **`Graph.objects(type=None, where=None) -> list[Object]`** is the
  canonical form. Same kwargs and semantics as `View.objects`
  (core/view.py:27): filter by `type` and by a `where` dict, return
  a list. Calling with no arguments returns every object — the
  behavior `View.objects()` already exhibits — which makes
  `graph.objects()` a strict superset of `graph.all_objects()`
  rather than a near-duplicate.
- **`Graph.query(object_type=None, where=None)`** stays as a
  backward-compatible alias. No deprecation warning in v1.0.3; the
  alias keeps `tools/graph_query.py` and any external code working.
  v1.1 or later can revisit whether to deprecate.
- **`Graph.all_objects()`** stays as is. Distinct intent
  (no-filter, mirror of `events` / `relations` properties); kept
  to avoid breaking the ~10 internal callsites and the documented
  inspection-debugging pattern.

The naming choice — `objects` not `query` — follows the v0.6
`View.objects` precedent. The kwarg choice — `type` not
`object_type` — matches `View.objects`'s parameter name. Both
surfaces now read the same in user code regardless of whether the
call site is inside or outside a behavior.

### Why not deprecate `graph.query` in v1.0.3

A deprecation warning costs every existing caller a stderr line.
The framework has shipped `graph.query` in three releases (v1.0,
v1.0.1, v1.0.2). The internal tool `tools/graph_query.py` calls it.
Pack examples and notebook tutorials in the wild may call it. The
v1.0.3 release is "patch release based on user-test findings," not
"reshape the query surface." Keeping the alias silent for one
release lets the docs and ecosystem migrate; v1.1 or later locks
the decision.

### Documentation

`graph.objects(type=...)` becomes the form shown in
`docs/concepts/graph.md` and `docs/reference/`. The alias is
documented once (under the same heading) as
"`graph.query(object_type=...)` is an alias for backward
compatibility; new code should use `graph.objects(type=...)`."

## v1.0.3 #2. `@llm_behavior(output_schema=)` strict-validates at decoration time

Users reasonably pass JSON-schema dicts to `output_schema=` because
that's the de facto cross-provider format for structured output.
The framework's docstring types it `Optional[type]` — implicit
"Pydantic BaseModel subclass." The runtime silently fails on dict
input: the `isinstance()` check inside `_invoke_llm` raises
`TypeError`, which is caught as a generic exception and emitted as
a `behavior.failed` event with `reason="llm.schema_violation"`.
The user sees zero results with no diagnostic naming the cause.

The decision: **strict-validate at decoration time**, not accept
dicts via conversion. The cause is named at the line where the
mistake lives (the `@llm_behavior(...)` decorator), no LLM call
burns budget, and the message hands the user the correct form
verbatim as a code example.

### Validation shape

`@llm_behavior(output_schema=X)`'s `wrap()` checks, before
constructing the `LLMBehavior` instance:

- `X is None` → pass (output_schema is optional).
- `isinstance(X, type) and issubclass(X, BaseModel)` → pass.
- Anything else → `TypeError` with a structured message naming the
  actual type passed (`type(X).__name__` for non-None) and an
  inline code example of the correct form.

Example shape:

```
TypeError: output_schema must be a Pydantic BaseModel subclass, not dict.

Example:
    from pydantic import BaseModel

    class MyOutput(BaseModel):
        result: str

    @llm_behavior(output_schema=MyOutput)
    def my_behavior(event, graph, ctx, out): ...

Dict-form output_schema (e.g., JSON Schema as a dict) is filed
as a v1.1 candidate. See CONTRACT v1.0.3 #2.
```

The "names the actual type" piece matters: passing a dict vs.
passing `None` accidentally vs. passing a string all want different
fixes. The error tells the user what they passed.

### Why not accept dicts via conversion

Three reasons accepting dicts via runtime conversion was rejected:

1. JSON-schema → Pydantic conversion at runtime has no first-class
   Pydantic API. Hand-rolling one adds a feature with its own bug
   surface in a patch release.
2. The user's pain (silent failure with confusing
   `llm.schema_violation` diagnostic) is *fully* addressed by
   failing early with a clear message. Conversion would close the
   same pain plus add ergonomics; the marginal gain is a v1.1
   design pass, not a patch.
3. Dict-form schema support as an opt-in second form is cleaner
   as a v1.1 design pass — possibly via a
   `LLMProvider.recognizes_schema_dict()` method or a separate
   `output_schema_dict=` keyword — where it can carry its weight
   rather than being squeezed in here.

**v1.1 candidate.** "Accept JSON-schema dicts as `output_schema=`,
either via a runtime converter or via a dedicated
`output_schema_dict=` kwarg. Design pass needed: which providers'
structured-output APIs accept dicts natively, what the equivalence
contract between dict-form and Pydantic-form schemas is, what the
prompt-hash treatment is for fixtures."

### What this is not

- Not a Protocol change. `LLMProvider.complete()` and the rest of
  the v0.6 #3 / v0.7 / v1.0.2 #1 surface stay locked.
- Not a behavior-failure reason code change. The v0.6 #11
  taxonomy is closed; the TypeError surfaces *before* the behavior
  runs, so no `behavior.failed` event with a new reason code is
  emitted.
- Not a runtime-side check. The check lives in the decorator
  where the mistake happens; the runtime's existing
  `isinstance(output_schema, type)` callsites stay as a defensive
  second line.

## v1.0.3 #3. `behavior.failed` carries a user-facing log signal

CONTRACT v0.6 #11 makes failures-as-events a deliberate design
choice. That stays correct and unchanged in v1.0.3. The gap the
user-test surfaced is a UX gap, not an architecture gap: the
current implementation has no user-facing signal when behaviors
fail during a run. `runtime.run_goal()` returns cleanly with zero
results; users have to know to inspect `graph._events` for
`behavior.failed` entries.

The fix is two additive affordances:

### (a) WARNING log on each `behavior.failed` emission

The centralized `Runtime._emit_behavior_failed` (runtime.py:1403)
gains a `WARNING`-level log line before emission. The function
behavior path at runtime.py:664 already logs at `ERROR` for the
exception-escape case; v1.0.3 #3 unifies the surface by logging at
`WARNING` from the centralized emitter and removing the
duplicate `ERROR` log so every `behavior.failed` produces exactly
one log line at one consistent level.

Log shape:
- Level: `WARNING`.
- Message: `"behavior failed: <behavior_name> (reason=<reason>)"`.
- Extra fields via the existing `runtime_log_extra` helper:
  `behavior`, `event_id`, `reason`, `exception_type`, `message`,
  plus `doc_url` pointing at the reason's documentation page
  (`https://docs.activegraph.ai/errors/llm-behavior-error` for
  `llm.*` reasons; the class-level slug, not a per-reason slug —
  consistent with the v1.0 #4 More: URL convention).

Users opt out via standard Python logging configuration
(`logging.getLogger("activegraph.runtime").setLevel(logging.ERROR)`
or any other filter). Existing code that already captures the
framework's logger sees the new line at the same logger name.

### (b) `Runtime.errors` property

A new read-only property on `Runtime` exposes accumulated
`behavior.failed` events from the runtime's graph in a structured
form:

```python
runtime.errors  # -> list[RuntimeError]
```

where each `RuntimeError` (a `NamedTuple`, distinct from Python's
builtin `RuntimeError`, exported under a non-conflicting name —
the locked name is `BehaviorFailure`) carries:

- `behavior: str` — the failing behavior's name.
- `event_id: str` — the triggering event id.
- `reason: Optional[str]` — the reason code from v0.6 #11 (None
  for generic exception escapes that have no structured reason).
- `exception_type: str` — the Python exception class name.
- `message: str` — the exception's `str(...)`.
- `failed_event_id: str` — the `behavior.failed` event's id.

The property reads from `runtime.graph._events` on each access
(cheap; the event list is already in memory). No caching, no
listener registration, no new state — the events are the source
of truth and the property is a structured projection.

Users inspect failures programmatically without reaching into
`graph._events` or parsing payload dicts:

```python
rt.run_goal("...")
for err in rt.errors:
    if err.reason == "llm.network_error":
        retry(err.event_id)
```

### What this is not

- **Not** an `on_failure=` callback parameter on `run_goal()` /
  `run_until_idle()` / `run_until()`. That's a larger affordance
  and a v1.1 candidate (filed below).
- **Not** a change to `behavior.failed` event emission. The new
  log line and the property are *additive*; the event payload,
  ordering, and listener semantics stay as v0.6 #11 / v1.0
  locked.
- **Not** an exception escape. Behaviors that fail still don't
  raise out of `run_goal()`. The failure-model principle
  (`docs/concepts/failure-model.md`) is unchanged.

**v1.1 candidate.** "`Runtime.run_goal(..., on_failure=callback)`
as a first-class affordance for callers who want to react to
behavior failures without subscribing a `@behavior` to
`behavior.failed`. Design pass needed: synchronous vs. async
callback shape, whether the callback can suppress the
`behavior.failed` event entirely (probably no — events are the
audit trail), whether `on_failure=` composes with explicit
`@behavior(on=['behavior.failed'])` subscribers."

### Documentation

`docs/concepts/failure-model.md` gains a "Observing failures in
caller code" section documenting the WARNING log line and the
`Runtime.errors` property as the two user-facing surfaces.
`docs/reference/` gains the `BehaviorFailure` NamedTuple shape.

## v1.0.3 #4. Multi-turn tool-use messages carry full content blocks

The runtime's tool-use turn loop (runtime.py:976-984) appends
*only* the LLM's `raw_text` to the message history after a
`tool_use`-bearing assistant turn. Anthropic's API specification
requires every `tool_result` block in a subsequent user message to
have a matching `tool_use` block in the preceding assistant
message. Appending raw_text alone drops the `tool_use` blocks and
produces messages that violate the spec.

Direct Anthropic API access apparently tolerates the violation
(responds with a successful follow-up turn). The Vertex AI proxy
enforces the spec strictly and returns HTTP 400. Any user routing
Anthropic through Vertex AI hits this bug. Future tightening of
the direct API to match the proxy would surface the same failure
for direct users.

This is a bug fix in a hot path. CONTRACT v0.7 / v1.0 didn't lock
the multi-turn message shape explicitly; v1.0.3 #4 locks it.

### The fix

`LLMMessage` (llm/types.py:35) gains one additive field:

```python
@dataclass(frozen=True)
class LLMMessage:
    role: Role
    content: str
    tool_use_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_calls: Optional[tuple[ToolCall, ...]] = None  # v1.0.3 #4
```

When the runtime appends an assistant message that carried
`tool_use` blocks, it includes the originating `ToolCall` objects
alongside the text:

```python
running_messages.append(
    LLMMessage(
        role="assistant",
        content=turn_response.raw_text or "",
        tool_calls=tuple(response_tool_calls),
    )
)
```

Provider adapters reconstruct the wire-format content blocks on
the way out:

- `_message_to_anthropic` (llm/anthropic.py:255) gains an
  `assistant + tool_calls` branch that emits a content-block list:
  `{"type": "text", "text": m.content}` (when non-empty) followed
  by `{"type": "tool_use", "id": tc.id, "name": tc.name, "input":
  dict(tc.args)}` for each `tc` in `m.tool_calls`.
- Non-tool-use assistant messages keep the string-content shape;
  the new branch only fires when `m.tool_calls` is a non-empty
  tuple. Existing single-turn tool-using behaviors and zero-tool
  assistant messages are byte-identical on the wire.

### Backward compatibility — prompt hash

`LLMMessage.to_dict()` (used by `_canonical_prompt_payload` in
recorded.py to compute the prompt hash) only emits the
`tool_calls` key when non-None. Existing single-turn fixtures
keep their hashes; new multi-turn fixtures include the field in
the hash, which is correct — the prompt shape *did* change at the
wire level for those.

The hashing-stability invariant is locked by an explicit test:
construct a non-tool-using `LLMMessage`, serialize it, assert the
absence of the `tool_calls` key.

### Adjacent fix — `RecordedLLMProvider` response roundtripping

`_response_from_fixture` (llm/recorded.py:179) previously dropped
`tool_calls` when reconstructing an `LLMResponse` from a fixture.
That meant multi-turn tool-use fixtures were unreachable via the
recorded provider: even if a recording captured a tool-using
assistant turn, the replay returned an `LLMResponse` with
`tool_calls=None`, the runtime's `response_tool_calls` branch
saw nothing, and the loop exited as if the model had returned a
final response.

v1.0.3 #4 reconstructs `tool_calls` from the fixture dict in the
same commit as the runtime fix. Without it, no test of the
runtime fix is possible through the recorded-provider path.
Filed as an adjacent fix in the same commit because the two
together are the unit of testable behavior; splitting them ships
the runtime fix with no test coverage.

### OpenAI tool support is out of scope

CONTRACT v1.0.1 #5 (c) clause 2 documented OpenAI tool support as
a v1.1 candidate. v1.0.3 #4 fixes the documented Anthropic tool
path; it does **not** add OpenAI tool translation. The new
`LLMMessage.tool_calls` field is invisible to
`OpenAIProvider._message_to_openai` (the OpenAI path doesn't
consume it), so the field addition is non-breaking for the
OpenAI provider.

**v1.1 candidate** (already filed in v1.0.1 #5 (c) clause 2;
restated here for the v1.1 backlog consolidation pass): "OpenAI
tool-use translation. `OpenAIProvider.complete()` doesn't accept
tools or reshape `LLMMessage.tool_calls` for the OpenAI
`tool_calls` wire format. Design pass needed: response-extraction
shape (OpenAI returns `tool_calls` on `choice.message`, not as
content blocks), tool-result message shape (OpenAI's `role='tool'`
with `tool_call_id`, which `_message_to_openai` already handles
for the v0.7 single-direction case)."

## v1.0.3 deliberately does NOT touch

The patch scope is the four findings, not more:

- **Finding E** from the second user-test report — fire-once-on-
  condition semantics for LLM behaviors that subscribe to
  high-volume event streams. The user worked around with a "gate
  behavior" pattern (a Python `@behavior` creates a control
  object exactly once when conditions are met; the LLM behavior
  subscribes to the control object). Real v1.1 design work; not
  attempted in v1.0.3. Filed as a v1.1 candidate (see backlog
  note below).
- No CONTRACT amendments to v1.0 decisions or earlier; v1.0.3
  amendments only.
- No changes to `LLMProvider` Protocol beyond the v1.0.2 #1
  additive widening (`default_model`, `recognizes_model`).
- No changes to `behavior.failed` event payload, ordering, or
  listener semantics.
- No changes to the closed v0.6 #11 reason-code taxonomy.
- No new public class beyond `BehaviorFailure` (the NamedTuple
  for `Runtime.errors`).
- No new CI gate.

### v1.1 backlog candidates surfaced by v1.0.3

Restated here for the post-v1.0.3 consolidation pass:

1. **Dict-form `output_schema=` support.** Design pass needed
   per v1.0.3 #2.
2. **`on_failure=` callback on `run_goal()` and siblings.** Design
   pass needed per v1.0.3 #3.
3. **OpenAI tool-use translation.** Restated from v1.0.1 #5 (c)
   clause 2 per v1.0.3 #4; design pass needed.
4. **Fire-once-on-condition aggregation triggers for LLM
   behaviors.** Finding E per v1.0.3's scope discipline; current
   workaround is the gate-behavior pattern (Python `@behavior`
   creates a control object that gates the expensive LLM
   behavior).
5. **Pack-load-time cross-provider validation.** Already filed in
   v1.0.2 #1 (b); pack behaviors loaded after Runtime
   construction currently fall through to first-run validation
   rather than load-pack-time validation.

A contract-review work item after v1.0.3 ships consolidates these
into a single v1.1 scope section alongside the existing v1.1
#1–#4 entries below.


# v1.0.4 — pre-launch foundation cleanup (six findings from post-v1.0.3 contract review)

The post-v1.0.3 contract review (see banner above and
`CONTRACT-review-findings.md` §5 for the audit record) surfaced six
small findings:

- Three documentation corrections — #2 per-error-page footers, #3
  WARNING-log field-name divergence, #5 stale forward-pointer
  prose.
- One additive API method mirroring v1.0.3 #1's pattern — #1
  `graph.relations()` canonical form.
- One test addition for a contract claim that shipped without a
  boundary-anchored test — #4 `_requeue_unfired` zero-subscriber.
- One CONTRACT archeology restoration — #6 appended
  `### v1.0.2.post1` section.

v1.0.4 absorbs all six. This release does not change abstractions,
does not add new runtime capability, does not reshape any locked
decision below v1.0.4, and does not introduce a new CI gate. Each
finding is small, mechanical, and independently reviewable.

The discipline that kept v1.0.1 / v1.0.2 / v1.0.3 small applies in
amplified form here — v1.0.4 is the pre-launch foundation cleanup
pass, not a new feature milestone. The two Standing Rules adopted
by the contract review banner are visible in every finding:

- **Standing Rule §1** (amendments append, never modify). Each
  v1.0.4 amendment below appends as its own dated section. The one
  authorized in-place edit is the forward-pointer in v1.0.2 #1's
  existing top-of-section breadcrumb, updated by #6 to point at
  the newly-appended `### v1.0.2.post1` section.
- **Standing Rule §2** (tests anchor on the contract boundary).
  Tests added for #1 (eight filter combinations of
  `graph.relations`) and #4 (the zero-subscriber requeue set)
  anchor on the contract claim, not on the implementation's
  symptom or on the alias's behavior. Test shape is named per
  finding below.

## v1.0.4 #1. `graph.relations(source=, target=, type=)` is the canonical filter API

External users reach for `graph.relations(source=claim_id)`
naturally — `docs/concepts/graph.md:43` advertised that shape
since v0, and the decomposition of an edge into (source, target,
type) reads identically to the natural user mental model. The
framework shipped `graph.get_relations(object_id=, type=,
direction=)` instead: different name, and the `object_id +
direction` pair collapsed what users decompose into `source` and
`target`. The broken doc reference has stood across every release
— users hit `AttributeError` the first time they try the
documented form.

Same shape as v1.0.3 #1's `graph.objects(type=...)` fix, with one
extension: `graph.relations` introduces a `source`/`target`
decomposition that `get_relations` did not have, because the
`direction="outgoing"|"incoming"|"both"` axis is what the
decomposition replaces.

### The canonical form

**`Graph.relations(source=None, target=None, type=None) -> list[Relation]`**.
Filter kwargs compose by AND semantics:

| `source` | `target` | `type` | Returned                                            |
|----------|----------|--------|-----------------------------------------------------|
| None     | None     | None   | every relation in the graph                         |
| A        | None     | None   | relations whose `source == A` (outgoing from A)     |
| None     | B        | None   | relations whose `target == B` (incoming to B)       |
| A        | B        | None   | relations from A to B                               |
| None     | None     | T      | every relation of type T                            |
| A        | None     | T      | relations from A of type T                          |
| None     | B        | T      | relations to B of type T                            |
| A        | B        | T      | relations from A to B of type T                     |

No-kwarg call returns every relation — same shape as
`View.relations()` (core/view.py:39) and the v1.0.3 #1 no-kwarg
shape of `graph.objects()`. The eight combinations above are the
contract claim; tests anchor on each combination.

### The decomposition versus `get_relations`

`graph.get_relations(object_id=X, direction="outgoing")` is
`graph.relations(source=X)`. `get_relations(object_id=X,
direction="incoming")` is `relations(target=X)`. The `"both"`
direction in `get_relations` (matches when `object_id` is either
the source or the target) does not have a single-call equivalent
in the new form; callers needing symmetric-endpoint lookup either
call `relations()` twice (once per slot, taking the union) or keep
using `get_relations(direction="both")`. v1.1's Graph/View
harmonization pass (Theme A in `v1.1-plan.md`) may add an
`endpoint=` convenience for symmetric lookup; v1.0.4 stays small.

### The alias

**`Graph.get_relations(object_id=None, type=None, direction="both")`**
stays as a backward-compatible alias. No deprecation warning in
v1.0.4 — the older shape keeps `examples/`, internal callers, and
any pre-v1.0.4 user code working. v1.1 or later revisits
deprecation as part of the broader Graph/View harmonization pass.

**`Graph.all_relations()`** stays as is. Same carve-out reasoning
as v1.0.3 #1 kept `all_objects()`: distinct intent (no-filter
inspection), broad internal use, removing it would be churn
without payoff.

### Documentation

`graph.relations(source=, target=, type=)` becomes the form shown
in `docs/concepts/graph.md`. The line-43 broken reference resolves.
The alias gets one line of documentation under the same heading.

### Why not deprecate `graph.get_relations` in v1.0.4

Same logic as v1.0.3 #1's parallel decision for `graph.query`. A
deprecation warning costs every existing caller a stderr line. The
framework has shipped `get_relations` in every release. v1.0.4's
scope is "absorb six findings from the contract review," not
"reshape the relations surface." Keeping the alias silent for one
release lets the docs and ecosystem migrate; v1.1 Theme A owns the
deprecation decision in the broader harmonization context.

### Tests (Standing Rule §2)

Tests anchor on the contract claim above — what
`graph.relations(...)` returns for each of the eight filter
combinations — not on whether the new method's output equals the
alias's output. The alias-still-works test is a separate
backward-compatibility test that asserts
`get_relations(object_id=A, direction="outgoing")` continues to
return what it returned in v1.0.3.

## v1.0.4 #2. Per-error pages link to `failure-model.md` observability section

A user landing on `docs/reference/errors/llm-behavior-error.md`
from the `More:` URL in a WARNING log line learns what the error
class means but does not learn that they can iterate `rt.errors`
for structured inspection of every `behavior.failed` event, or
filter the graph's event log for the same. The canonical
observability surface (`Runtime.errors`, the `BehaviorFailure`
NamedTuple, the relevant section of
`docs/concepts/failure-model.md`) is one click away on the doc
site but not linked from any per-error page.

The fix: a one-line footer at the bottom of every per-error page
whose error surfaces via `behavior.failed`. The footer text is
fixed:

> See [Observing failures in caller code](../concepts/failure-model.md#observing-failures-in-caller-code)
> for `Runtime.errors` and the `BehaviorFailure` shape.

### Which pages get the footer

Pages whose errors emit via `behavior.failed` get the footer.
Pages whose errors are raised at registration time or at framework
setup do NOT — those errors never become `behavior.failed` events
because they fire before any behavior runs. The audit identifies
behavior-emission per page by tracing each error class's raise
sites; uncertain cases are filed as v1.1 follow-on rather than
guessed (Standing Rule §2 discipline applied to the documentation
pass).

This is documentation only. No code change. No amendment to the
v1.0 #4 `ActiveGraphError` hierarchy or the v0.6 #11 reason-code
taxonomy.

## v1.0.4 #3. WARNING-log field names diverge from `BehaviorFailure` field names — documented

CONTRACT v1.0.3 #3 added two surfaces for inspecting
`behavior.failed` events: a WARNING-level structured log line, and
the `Runtime.errors` property returning `list[BehaviorFailure]`.
Both surfaces carry the same values under different field names:

| Field meaning            | WARNING log key | `BehaviorFailure` attribute |
|--------------------------|------------------|------------------------------|
| Exception class name     | `error_type`     | `exception_type`             |
| Exception message string | `error_message`  | `message`                    |

The log keys follow CONTRACT v0.8 #6's structured-logging schema
(operator-facing, grep-friendly across all framework log lines and
log-shipping pipelines). The `BehaviorFailure` attribute names
follow Python convention (matches `traceback.format_exception` and
stdlib naming). Both names are correct in their layer.

The divergence was intentional in v1.0.3 #3 — each surface picks
the convention appropriate to its consumer — but was not
documented. A user grepping logs for `error_type` finds the log
output; a user reaching for `.exception_type` on a
`BehaviorFailure` finds the structured object. The values are
identical; only the field names differ.

The fix: one sentence appended to
`docs/concepts/failure-model.md`'s "Observing failures in caller
code" section, naming the divergence and stating the values are
the same. No code change. The contract anchors for both
field-name sets stay where they were locked: v0.8 #6 for the log
schema; v1.0.3 #3 for the NamedTuple.

## v1.0.4 #4. Boundary-anchored test for `_requeue_unfired` zero-subscriber case

CONTRACT v0.5 #8's locked claim: "on load, events with NO
`behavior.started` referencing them are re-queued; events that any
behavior started on are NOT re-queued." The implementation through
v1.0-rc1 used the false reverse-implication that "no
`behavior.started` ⟹ event still in queue"; events with zero
subscribers (popped-and-discarded from the queue without any
`behavior.started`) were falsely requeued on every `Runtime.load`.
CHANGELOG v1.0-rc2 fixed the implementation by using the last
`runtime.idle` / `runtime.budget_exhausted` lifecycle event as the
high-water mark; the contract claim itself was correct since v0.5.

`tests/test_requeue_unfired.py` (added in v1.0-rc2) locks the
regression vector. It exercises the zero-subscriber case — the
fixture builds a run where `downstream.no_subscribers` events have
no subscribed behavior — and asserts `queue_depth == 0` on a
freshly loaded cleanly-drained run. That assertion catches the
bug shape, but anchors on the implementation's symptom
(`queue_depth`) rather than directly on the contract's boundary
claim ("NOT re-queued").

The fix adds a boundary-anchored sibling assertion: inspect the
requeue set that `_requeue_unfired` decided to put back in the
queue on load, and verify the zero-subscriber event IDs are
absent. The contract anchor: "on `Runtime.load`" (the boundary)
and "events with NO `behavior.started` … are re-queued; …
zero-subscriber events are popped-and-discarded … are NOT
re-queued" (the claim).

The new test's name carries the boundary; the assertion exercises
the contract claim's exact subject (the requeue set itself, not
its summary); the test would fail under the v0.5–v1.0-rc1 bug
shape regardless of whether `queue_depth` happened to summarize
the failure correctly. Standing Rule §2 shape.

No code change. No CONTRACT amendment to v0.5 #8 itself — the
contract claim was correct since v0.5; the tests catch up.

## v1.0.4 #5. Review-overlay markers on three sites of stale forward-pointer prose

Three CONTRACT sections carry forward-pointer prose that became
archaic when the referenced deferral shipped:

- **v0 #11** (`token_budget`): "`token_budget` is parsed but
  ignored until v0.6." Honored from v0.6 onward via view
  assembly; the unqualified clause reads as if v0.6 has not
  shipped.
- **v0 #16** ("Out of scope for v0"): the list (SQLite, LLM
  behaviors, Cypher patterns, packs, etc.) is historical — every
  item shipped in a subsequent milestone.
- **v0.8 #19** ("What v0.8 deliberately does not add"): same
  shape — packs and other items have since shipped.

Per Standing Rule §1 (banner), amendment prose is not rewritten in
place. The clarifications append using an explicit overlay-marker
pattern consistent with the banner's other annotations
(per-milestone `Review status:` markers, the v1.0.2 #1(b) in-place
marker, and the appended `### v1.0.2.post1` section landed by
v1.0.4 #6).

### The marker syntax

> `[review overlay 2026-05-19: …]`

Greppable by date, attributed to the post-v1.0.3 review layer,
visually distinct from amendment prose. Each of the three sites
receives one marker added immediately after the stale clause —
preserving the original prose verbatim and adding the up-to-date
status alongside.

### Standing Rule §1 compatibility

Standing Rule §1 prohibits silent in-place rewrites that destroy
the boundary record. Explicit dated overlays preserve the layer
boundary — a future reader sees "this clarification was added by
the 2026-05-19 review" alongside the original locked prose. The
banner's existing overlay annotations use the same mechanical
pattern; v1.0.4 #5 formalizes the pattern for forward-pointer
prose specifically.

No code change. CONTRACT-only.

## v1.0.4 #6. Appended `### v1.0.2.post1` section restores archeological record

v1.0.2.post1 (CHANGELOG dated 2026-05-19, PR #16) corrected the
validation boundary for v1.0.2 #1(b): cross-provider model
validation moved from lazy-at-first-`run_goal` to firing at both
binding moments — `Runtime(graph, llm_provider=...)` construction
AND `@llm_behavior` / `register()` decoration when one or more
Runtimes are alive (tracked via the `weakref.WeakSet` in
`activegraph/runtime/_live.py`).

The CHANGELOG carried the dated `[v1.0.2.post1]` entry. CONTRACT
did not — v1.0.2.post1 revised v1.0.2 #1(b)'s prose in place
rather than appending a dated amendment section. CONTRACT's
amendment history showed v1.0.2 #1 followed directly by v1.0.3,
with no trace of post1 having shipped between them.

Standing Rule §1 from the post-v1.0.3 contract review banner
codified the discipline that this pattern violated: amendments
append, never modify. The rule is retroactively invoked here.

The fix lands the proper appended section as a new
`### v1.0.2.post1` H3 placed as the fifth subsection of
v1.0.2 #1 (sibling to §(a), §(b), §(c), §(d)). The section
documents:

- The boundary the original v1.0.2 #1(b) named (registration-time)
  vs. the boundary the v1.0.2 implementation fired at (lazy).
- The correction: validation fires at both binding moments;
  `Runtime.__init__` validates against the registry,
  `@llm_behavior` and `register()` validate against the
  `weakref.WeakSet` of live Runtimes.
- The `activegraph/runtime/_live.py` module that factored the
  single-behavior validator out of the bulk path.
- A back-pointer to Standing Rule §1 establishing why this
  section appended retroactively rather than the original prose
  being further revised.
- The post1 test suite at `tests/test_llm_default_model.py`
  Section (g) (seven scenarios, each anchoring on one binding
  moment by name) as the canonical model for Standing Rule §2.

After the appended section lands, the existing top-of-section
breadcrumb in v1.0.2 #1 updates its forward pointer from "v1.0.4
candidate §5 #6 (review) will append" to past tense — "the
`### v1.0.2.post1` section below (appended retroactively in
v1.0.4 #6)." This in-place breadcrumb edit is explicitly
authorized by the contract review (the only in-place edit
Standing Rule §1 permits in v1.0.4) and is mechanically the
forward-pointer update the breadcrumb was written to anticipate.
The corrected `(b)` prose itself does not change — it already
carries the post1-correct text.

This finding is CONTRACT-documentation only. No code change.
The appended section is the archeological record being restored,
not a new contract claim.

## v1.0.4 deliberately does NOT touch

The v1.0.4 cleanup pass is bounded. Out of scope:

- No abstraction changes. Each finding is a small additive API
  method, a documentation correction, or a CONTRACT amendment.
  Findings whose diagnosis surfaced abstraction work were split
  to v1.1 — full Graph/View harmonization (Theme A), drift gates
  (Theme D), CONTRACT modularization (DOC-1).
- No reshape of any locked decision below v1.0.4. The one
  exception is the v1.0.2 #1 breadcrumb forward-pointer update
  in #6, which the contract review explicitly authorized as the
  single in-place edit Standing Rule §1 permits in this release.
- No new CI gate. All four existing gates stay green.
- No new public class. The only new public method is
  `Graph.relations(...)` per #1.
- No new runtime capability, no new event type, no new reason
  code, no new error class.
- No changes to `LLMProvider` Protocol (last widened at
  v1.0.2 #1).
- No changes to `behavior.failed` event payload, the WARNING log
  format, or the `BehaviorFailure` shape (all v1.0.3 #3 surfaces
  remain byte-identical).

### v1.1 candidates surfaced or restated by v1.0.4

Restated here for `v1.1-plan.md`'s backlog:

1. **Deprecation pass on `graph.get_relations`, `graph.query`,
   and the `object_type=` kwarg.** Part of Theme A — Graph/View
   harmonization. v1.0.4 #1 kept the new alias silent for one
   release; v1.1 owns the deprecation decision.
2. **Full Graph/View surface harmonization** (Theme A). Includes
   the method-vs-property `graph.events` asymmetry,
   `all_objects()` / `all_relations()` carve-outs, and a possible
   `endpoint=` convenience for `graph.relations(...)` covering
   the symmetric-endpoint lookup case (the only `direction=`
   shape that does not have a single-call equivalent in the
   new form).
3. **Spec-vs-impl drift gate** (Theme D — D-1 / D-2). The
   mechanical complement to Standing Rule §2: automated checking
   that tests' boundary names match contract amendment boundary
   names. v1.0.4 #4 closes the v0.5 #8 gap by hand; the gate
   would have caught it.
4. **CONTRACT modularization or executive-summary preambles**
   (DOC-1). CONTRACT.md is now over 6000 lines; navigation is
   becoming a usability concern. v1.0.4 #5's inline overlay
   markers and v1.0.4 #6's appended section add to the length
   but do not address the underlying readability.


# v1.0.5 — AI-readable docs via llms.txt support (single finding from external v1.0.4 user-test)

The v1.0.4 external user-test surfaced one finding: a developer
evaluating Active Graph in 2026 reaches the doc site through an
AI coding assistant (Claude Code, Cursor, Replit agents) far more
often than through a browser. The mkdocs-rendered HTML at
`docs.activegraph.ai` is hard for those agents to parse — they
fetch a page, get a navigation chrome wrapped around the content,
and spend tokens unwrapping it before they read.

The dominant convention for serving machine-readable docs is
**llms.txt** (proposed by Jeremy Howard, 2024, at
[llmstxt.org](https://llmstxt.org/)). Stripe, Vercel, Anthropic's
docs, Nuxt, and many others publish `/llms.txt` and
`/llms-full.txt` at the root of their doc sites. The pattern:

- **`/llms.txt`**: a structured markdown index — H1 + blockquote
  summary + H2 sections with linked pages, each with a
  one-sentence description. Navigation aid for AI tools.
- **`/llms-full.txt`**: concatenated markdown of every doc page,
  sized for large context windows. The "everything in one file"
  reference for AI tools that ingest comprehensive docs.

v1.0.5 ships both files. Single finding, single mechanical
release. No abstraction changes, no new runtime capability, no
source-markdown changes. The release operates on docs + build
infrastructure only.

## v1.0.5 #1. `/llms.txt` and `/llms-full.txt` at docs site root, generated at build time

### The contract claim

After every `mkdocs build`, the deployed site root carries two
additional files:

- **`/llms.txt`** — markdown index. Starts with `# Active Graph`,
  followed by a one-paragraph blockquote summary naming the
  framework's stance and the install command. Then H2 sections
  organizing the doc pages: Quickstart, Concepts, Guides,
  Cookbook, Reference (CLI / LLM providers / API / Errors), and
  About. Each link entry is `[Page Title](URL): one-sentence
  description`. An "Optional" H2 section at the end carries
  less-critical references (CHANGELOG, CONTRACT, etc.).
- **`/llms-full.txt`** — concatenated markdown. Same H1 and
  blockquote at the top. Then every doc page's markdown in
  reading order (concepts → quickstart → guides → cookbook →
  reference → about), with H2 separators between source pages
  so an AI reading the file can locate source-page boundaries.

Both files live at `https://docs.activegraph.ai/llms.txt` and
`https://docs.activegraph.ai/llms-full.txt` once GitHub Pages has
deployed the build artifact.

### Generation: `mkdocs-llmstxt` plugin

v1.0.5 adopts the `mkdocs-llmstxt` plugin (pawamoy, MIT, PyPI as
of 2025-11) rather than a custom build script. Three reasons:

1. **Same maintainer as `mkdocstrings`.** The framework's mkdocs
   plugin surface is already pawamoy-shaped; one author, one
   release cadence, one mental model. Adopting the same author's
   sibling plugin keeps the surface coherent.
2. **Native dual-output support.** The plugin's `full_output:
   llms-full.txt` config toggle generates both files from one
   pass over the nav tree. A custom script would re-implement
   nav-walking, markdown extraction, and HTML cleanup — work
   that already lives in the plugin and has been hardened
   against the mkdocs-internal API across releases.
3. **No drift surface.** Generation happens inside `mkdocs build`.
   The output files cannot drift from the source markdown
   because they are recomputed every build. There is no
   committed `llms.txt` to maintain by hand.

The plugin is installed via the existing `[docs]` extra in
`pyproject.toml` (same pattern that the rc3 amendment locked in
for `mkdocstrings`). Adding the dep to the extra auto-syncs the
`.github/workflows/docs.yml` install line (`pip install -e
".[docs]"`).

### Configuration shape in `mkdocs.yml`

The plugin's `sections:` config mirrors the existing `nav:` block
1:1 — Quickstart, Concepts, Guides, Cookbook, Reference (CLI +
LLM providers + API + Errors), About. The `markdown_description:`
field carries the one-paragraph framework summary (the same prose
that opens `README.md` and `docs/index.md`, kept in sync by the
same Standing-Rule-§1-style discipline that governs every other
voice-anchored prose surface). The `full_output: llms-full.txt`
toggle enables the second file.

### Drift prevention is structural, not procedural

The failure mode this fix protects against is drift between source
markdown and the published `llms.txt` / `llms-full.txt`. The
protection is **structural**: both files are generated at build
time from the same `docs/` source tree that mkdocs renders to
HTML. There is no separate hand-maintained `llms.txt` file in the
repository. The build cannot succeed without regenerating both.

This is the same pattern that protects the doc-site rendering
itself: the only way the site has stale content is for the
underlying markdown to be stale, in which case the rendered HTML
has the same staleness. Locking both surfaces to the same source
collapses the drift surface from two-of-two to one-of-one.

### Tests (Standing Rule §2)

`tests/test_llms_txt.py` is the contract anchor for v1.0.5 #1's
claim. The test invokes `mkdocs build` against the live
`mkdocs.yml` (into a temp `site/` directory) and asserts:

1. **Both files exist** at `site/llms.txt` and
   `site/llms-full.txt`.
2. **`llms.txt` well-formedness**: starts with `# Active Graph`;
   contains a blockquote summary line; contains at least one H2
   section header.
3. **`llms.txt` content coverage**: references each of the
   nav-anchor pages — `concepts/graph.md`, `quickstart.md`, and
   at least one cookbook page — so a file that exists but only
   contains the H1 doesn't pass. The assertion is on the linked
   nav anchors, not the full page list, so future page additions
   don't churn the test.
4. **`llms-full.txt` well-formedness**: starts with `# Active
   Graph`; contains the markdown body of at least one source
   page (asserted via a distinctive marker phrase from
   `docs/quickstart.md`).

The test is marked `@pytest.mark.slow` because `mkdocs build`
takes several seconds (the same cost as `test_doc_site_reachable.py`).
Local `pytest` skips it by default; the `docs.yml` workflow runs
it explicitly. Mirrors the precedent set by
`test_doc_site_reachable.py`.

The boundary the test anchors on: "both files exist after build
and are well-formed" — the exact phrase this section commits to.
Standing Rule §2.

### README and CHANGELOG

`README.md`'s `## Documentation` section gains a short note:
machine-readable docs at `/llms.txt` and `/llms-full.txt` for AI
coding assistants and agents. One sentence; the link targets
speak for themselves.

The CHANGELOG entry frames the release as "AI-readable docs via
llms.txt support" and cites the v1.0.4 user-test as the surfacing
path.

## v1.0.5 deliberately does NOT touch

The single-finding cleanup pass is tightly scoped. Out of scope:

- **No content negotiation** (Accept: text/markdown serving).
  That requires docs-host infrastructure beyond GitHub Pages;
  filed as v1.1 candidate.
- **No editorial doc-readability audit.** Individual doc pages
  may benefit from front-loaded summaries, clearer
  cross-references, or consistent terminology — but that is
  open-ended editorial work, not a release that ships two files
  at the site root.
- **No changes to existing doc pages' content.** The source
  markdown stays as is; the new files are generated from the
  existing markdown unchanged.
- **No code changes to the framework itself.** This is a docs +
  build-infrastructure release. No `activegraph/**/*.py` change
  ships.
- **No new abstraction.** No new public class, no new public
  method, no new event type, no new reason code, no new error
  class.
- **No reshape of any locked decision below v1.0.5.** Every
  surface from v1.0 through v1.0.4 stays byte-identical.

### v1.1 candidates surfaced by v1.0.5

Restated here for `v1.1-plan.md`'s backlog:

1. **Content negotiation on the docs host** (returns
   `text/markdown` for `Accept: text/markdown` requests).
   Complementary to the static `/llms.txt` and `/llms-full.txt`
   files: an AI agent that fetches an arbitrary doc page URL
   gets the markdown source rather than the rendered HTML. The
   v1.0.5 static-files approach handles the index-and-bulk case;
   content negotiation handles the per-page case. Requires
   docs-host infrastructure (a worker / edge function /
   nginx-config), which GitHub Pages does not support natively.
2. **Editorial doc-readability pass.** Front-load page summaries
   (so the first paragraph stands alone as the page abstract),
   tighten cross-references, normalize terminology across the
   per-error catalog. Open-ended and orthogonal to v1.0.5's
   mechanical file-generation scope.


# v1.0.5.post1 — pre-launch foundation: license switch and contribution surface

Pre-launch foundation pass before the repository is flipped public.
Three coupled deliverables: the licensing change drives the
contribution policy; the contribution policy references the new
license; the issue templates name the contribution policy. Bundled
in one release because shipping any one without the others would
leave a public repository in a half-stated posture.

No framework code changes. No new public API. No reshape of any
locked decision below v1.0.5. The release surface is repo-root
metadata (`LICENSE`, `NOTICE`, `pyproject.toml`'s license field,
`README.md`'s license section), contributor-facing prose
(`CONTRIBUTING.md`), and the GitHub issue-template surface
(`.github/ISSUE_TEMPLATE/`). Standing Rule §1 compliance: this
section appends; no pre-banner prose is rewritten. Standing Rule §2
compliance: `tests/test_license.py` anchors on the contract claim
named below ("Active Graph is licensed under Apache 2.0 from
v1.0.5.post1 forward"), not on any specific file's existence in
isolation.

## v1.0.5.post1 #1. License switches from MIT to Apache 2.0

### The contract claim

**Active Graph is licensed under the Apache License 2.0 from
v1.0.5.post1 forward.** The previous declared license was MIT (as
recorded in `pyproject.toml`'s `license = { text = "MIT" }` and
the matching `License :: OSI Approved :: MIT License` classifier
through v1.0.5). v1.0.5.post1 normalizes the declaration to the
SPDX identifier `Apache-2.0`, ships the canonical Apache 2.0
license text at the repository root for the first time, and adds
the Apache-convention `NOTICE` file naming the copyright holder.

### Pre-flip license-state finding

A read-only diagnosis pass before this release surfaced that the
framework had declared MIT in `pyproject.toml` and `README.md`
through twelve milestones but had never shipped a `LICENSE` file
at the repository root. This release is the first to land a
canonical license text. The framing is therefore "switch the
declared license to Apache 2.0 and ship the canonical text," not
"replace existing MIT license text with Apache text" — there was
no MIT text to replace.

### Why Apache 2.0 over MIT

Three reasons, each load-bearing for a framework about to be
flipped public:

1. **Explicit patent grant (§3 of the license).** MIT does not
   include a patent grant. Active Graph's architecture carries
   novel material — event-sourced reactive graph runtime, relation
   behaviors, the binding-moment validation discipline locked
   across v1.0.2 / v1.0.2.post1, the pack format. A framework
   whose primitives are themselves the contribution surface needs
   the patent grant to be unambiguous, both for the framework's
   contributors and for downstream adopters building on its
   abstractions.
2. **Institutional standard for foundation-shaped projects.**
   The Apache Software Foundation, CNCF, and LF AI all standardize
   on Apache 2.0 for their hosted projects. Enterprise legal review
   processes are calibrated against Apache 2.0; MIT often requires
   a one-off justification pass at the same desks. The release
   surface a contributor sees should match the license review
   surface their employer's counsel expects.
3. **Legal precision on trademark, contribution, and notice.**
   Apache 2.0 §6 (Trademarks), §5 (Submission of Contributions),
   and §4(d) (NOTICE file mechanics) name the boundaries that
   MIT leaves implicit. The framework's discipline lives in named
   boundaries — Standing Rule §1's append-vs-modify rule, Standing
   Rule §2's contract-anchored tests. A license that names its
   boundaries explicitly fits that discipline better than one
   that does not.

### Contribution licensing

From v1.0.5.post1 forward, contributions submitted to Active
Graph are received under Apache License 2.0 per Apache 2.0 §5
("Submission of Contributions"). `CONTRIBUTING.md` carries the
explicit inbound-equals-outbound statement at its License section.
The implicit-by-§5 default is reinforced by the contributor-facing
prose so a contributor reading either document arrives at the same
licensing posture.

The release deliberately does NOT add a CLA (Contributor License
Agreement) or DCO (Developer Certificate of Origin) requirement.
The Apache 2.0 §5 implicit grant is the contract; ceremony beyond
that is filed for a v1.1 candidate if contribution volume ever
makes the question concrete.

### Repository-root metadata shape

The new license surface lands as four files at the repo root:

- **`LICENSE`** — the canonical Apache 2.0 text from
  `https://www.apache.org/licenses/LICENSE-2.0.txt`, prefixed
  with a single-line copyright header (`Copyright 2026 Yohei
  Nakajima`). The text below the header is byte-identical to the
  Apache Foundation's canonical plain-text version.
- **`NOTICE`** — the Apache 2.0 §4(d) convention. Two lines:
  the project name (`Active Graph`) and the copyright line
  (`Copyright 2026 Yohei Nakajima`). The NOTICE file is the
  attribution carrier that downstream redistributors must
  preserve per §4(d).
- **`pyproject.toml`** — the `[project]` table's `license =
  { text = "MIT" }` becomes `license = "Apache-2.0"` (SPDX
  identifier per PEP 639); the `License :: OSI Approved :: MIT
  License` classifier is removed (PEP 639 mandates removal of
  `License ::` classifiers when the SPDX form is used). The
  setuptools build backend on `>=68` (already declared) supports
  the SPDX form.
- **`README.md`** — the `## License` section's "MIT." line
  becomes the Apache 2.0 reference. One sentence; the LICENSE
  file carries the legal text.

### Contribution policy and issue templates

Coupled deliverables under the same release:

- **`CONTRIBUTING.md`** — issues-first contribution policy for
  the framework's early public phase. Issues are open; code PRs
  are maintainer-only with an issue-first discussion gate;
  documentation PRs may be opened directly. Names the policy as a
  pre-launch posture that relaxes as the contributor community
  matures, not as a permanent stance. The community-tooling
  paragraph references planned community-management
  infrastructure built on Active Graph itself without committing
  to a specific URL or launch date — those details land when the
  tooling ships.
- **`.github/ISSUE_TEMPLATE/bug_report.md`**,
  **`feature_request.md`**, **`question.md`** — three structured
  templates that prompt for the information that makes a triage
  pass deterministic (reproduction, framework version, Python
  version, OS for bugs; problem statement and current workaround
  for feature requests; what-tried and what-expected for
  questions). Each template heads with a one-line pointer to
  `docs.activegraph.ai` and `CONTRIBUTING.md` so a contributor
  who opens a template still hits the docs and policy surface
  before filing.
- **`.github/ISSUE_TEMPLATE/config.yml`** — disables blank
  issues (`blank_issues_enabled: false`) and surfaces the docs
  site as a contact link. Forces every issue through one of the
  three templates; fits the project's discipline-first voice.

### Tests (Standing Rule §2)

`tests/test_license.py` is the contract anchor for v1.0.5.post1 #1.
The test anchors on the boundary the amendment names ("Active
Graph is licensed under Apache 2.0 from v1.0.5.post1 forward") by
asserting the joint state of every surface the claim binds:

1. **`LICENSE` carries Apache 2.0 canonical text** — asserts the
   `Apache License` + `Version 2.0, January 2004` heading lines
   appear in `LICENSE`, plus the §3 ("Grant of Patent License")
   section header. The patent-grant assertion is load-bearing
   because the patent grant is the named reason for the switch;
   a future LICENSE that loses the patent-grant section would
   silently violate the contract claim.
2. **`NOTICE` carries the project name and copyright line** —
   asserts both `Active Graph` and the `Copyright 2026 Yohei
   Nakajima` line appear, per the Apache 2.0 §4(d) convention.
3. **`pyproject.toml`'s license field reads SPDX `Apache-2.0`**
   — asserts the `[project]` table's `license` key resolves to
   the SPDX string. Any drift back to `{ text = "MIT" }` or to
   another SPDX identifier breaks this anchor.
4. **No `License :: OSI Approved ::` classifier remains in
   `pyproject.toml`** — PEP 639 requires removal of `License ::`
   classifiers when the SPDX form is used; the assertion guards
   against a re-introduction.
5. **`README.md`'s license section names Apache 2.0** —
   asserts the `## License` section body references `Apache
   License 2.0` and `LICENSE`. Catches a documentation drift back
   to "MIT" in the public-facing surface.

The test does not enumerate every byte of LICENSE because the
contract claim is "the framework ships under Apache 2.0," not "the
LICENSE file is byte-identical to the canonical Apache text." A
future trailing-newline adjustment or copyright-header rewording
should not fail the gate; a license-identity change should.

### Migration

Forward-compatible for downstream users. The runtime API is
unchanged. The PyPI metadata reports `Apache-2.0` for v1.0.5.post1
onward; redistributors should update their license-tracking
metadata accordingly.

## v1.0.5.post1 deliberately does NOT touch

The pre-launch foundation pass is bounded. Out of scope:

- **No CLA or DCO requirement.** Apache 2.0 §5's implicit grant
  is the contract. Filed as a v1.1 candidate if contribution
  volume makes the question concrete.
- **No `CODE_OF_CONDUCT.md`.** The contact channel that
  Contributor Covenant (the standard choice) requires for
  reporting is not yet established. Filed as a v1.1 candidate
  paired with the contact-channel decision.
- **No relicensing of historical commits.** All commits through
  v1.0.5 were authored by the project owner or by Claude on the
  project owner's instruction (no third-party contributions have
  shipped). The license change is forward-effective from
  v1.0.5.post1; pre-existing source carries the new license under
  the standard owner-relicensing-own-work pattern. Documented
  here so a future external audit of the commit history finds the
  relicensing event named.
- **No per-source-file license header.** Apache 2.0 §4 does not
  require per-file headers; the LICENSE + NOTICE pair at the
  repo root carries the attribution. Per-file headers can be
  added in a mechanical pass later if downstream redistribution
  patterns require them; the boundary the contract claim names
  is "the framework is licensed under Apache 2.0," and the
  repo-root pair satisfies that boundary.
- **No changes to the framework runtime, public API, error
  hierarchy, doc-site content, or any code under `activegraph/`.**
- **No reshape of any locked decision below v1.0.5.** Every
  surface from v1.0 through v1.0.5 stays byte-identical.

### v1.1 candidates surfaced by v1.0.5.post1

Restated here for `v1.1-plan.md`'s backlog:

1. **CLA / DCO decision.** Apache 2.0 §5's implicit grant covers
   the contract today; if contribution volume grows past the
   maintainer-review bandwidth or if enterprise legal desks
   request the ceremony, decide between a CLA (with a signing
   workflow) and a DCO (the `Signed-off-by:` discipline). v1.1
   work, not v1.0.5.post1.
2. **`CODE_OF_CONDUCT.md` paired with a contact channel.**
   Contributor Covenant v2.1 is the standard text; the missing
   piece is the contact channel for reports. v1.1 picks both up
   together — the document and the inbox.
3. **Relax the issues-first contribution policy.** The
   CONTRIBUTING.md policy is explicitly a pre-launch posture; the
   v1.1 milestone owns the decision to broaden it based on actual
   contribution patterns observed during v1.0.x's public window.


# v1.1 — implementation gaps (concrete items from v1.0-rc1 user-facing work)

**Review status (2026-05-19):** RELOCATED. The post-v1.0.3 contract
review consolidated the v1.1 backlog (this section plus the
scattered "filed for v1.1" markers from v1.0.1 / v1.0.2 / v1.0.3,
plus the v1.0 PR-G end-of-series tallies) into a single document:
**`v1.1-plan.md`** (see also `CONTRACT-review-findings.md` §2 and
§6 for the surfacing path).

The numbered entries below (#1–#9 + #7-and-beyond) are preserved
as historical context; each maps to one or more `v1.1-plan.md`
themes:

- #1 (CLI flags) → `v1.1-plan.md` Theme F (F-1 / F-2 / F-3).
- #2 (spec-vs-impl drift gate; CLI + Python snippets) →
  `v1.1-plan.md` D-1 + D-2.
- #3 (type-completeness) → `v1.1-plan.md` E-1.
- #4 (docstring-completeness) → `v1.1-plan.md` E-2 (+ E-3).
- #5 (`Runtime.load` auto-provider) → `v1.1-plan.md` B-7.
- #6 (version-tag-correspondence gate) → `v1.1-plan.md` D-3.
- #8 (wheel-completeness gate) → `v1.1-plan.md` D-4 (shipped in
  v1.0-rc3; required-status flip pending).
- #9 (deploy-verification gate) → `v1.1-plan.md` D-5 (shipped;
  required-status flip pending Pages enablement).
- #7-and-beyond (Pack* partials, DB errors, fork cache symmetry,
  OpenAI tool, native structured output) → `v1.1-plan.md` A-1,
  A-2, A-3, H-1, B-1, B-2.

When v1.1 milestone scoping begins, `v1.1-plan.md` is the
canonical backlog. The text below stays as the original framing
discipline ("turn 'we found a gap' into 'we found a gap and we
know exactly how to close it'") which the plan inherits.

The first v1.1 items that came from real user-facing work, not from
internal audit. The v1.0 error-completeness milestone (~30 items from
the PR-G end-of-series tally) is the broader v1.1 scope; this section
records the implementation gaps that surfaced during rc1.

The discipline: turn "we found a gap" into "we found a gap and we
know exactly how to close it." Each entry has a spec source, an
implementation scope, and the open design questions that need
resolution in the v1.1 PR.

## v1.1 #1. CLI flags spec'd but not implemented

Three CLI flags are documented in CONTRACT v1.0 and in
`examples/quickstart_session.txt` (the v1.0 transcript spec), but
do not exist in code. The v1.0-rc1 tutorial work
(rc1 #2/5, `docs/quickstart.md`) surfaced the first; the
spec-vs-impl audit done as part of that commit surfaced the other
two.

**The three flags:**

1. **`activegraph fork --set <pack>.<key>=<value>`** —
   override a pack setting on a fork. Most user-visible of the
   three; appears in the rendered doc-site at
   `cookbook/common-patterns.md` (the fork-and-diff workflow),
   `concepts/forking.md` (the forking conceptual model), and
   `reference/cli.md` (the CLI reference). The tutorial's step 7
   (the "fork-and-diff is the framework's mechanism for hypothesis
   testing" closer) depends on it.

2. **`activegraph inspect --memo`** — render a run's memo
   objects in the same prose form as the quickstart command's
   memo section. Spec'd in
   `examples/quickstart_session.txt` BEAT 2 implementer notes
   ("Memo output uses the same renderer the operator CLI uses
   (`activegraph inspect --memo`). Two renderers diverge; one
   renderer stays consistent.") Not currently visible in any
   user-facing doc page.

3. **`activegraph inspect --search "<query>"`** — substring or
   keyword search across a run's events. Spec'd in BEAT 3 of the
   transcript. Not currently visible in any user-facing doc page.

**Implementation scope (per flag):**

- `--set`: persist overrides on the run's metadata row at fork
  time, read them back on `Runtime.load`, plumb through to
  `load_pack(settings=...)` when packs reload. Affects the
  SQLite/Postgres schemas (a new column or sidecar JSON on
  `runs`), `Runtime.load`'s signature (optional override
  parameter), and the fork primitive's CLI surface (add the flag
  and the parsing).
- `--memo`: small read-only flag on the existing `inspect`
  command; reads memo objects from the run and prints them with
  the same renderer the quickstart command's
  `_print_memo_section` helper uses (lift the helper out of
  `activegraph/cli/quickstart.py` into a shared module so the
  two renderers don't diverge).
- `--search`: small read-only flag on `inspect`; iterates events
  and matches against payload JSON or type+actor fields. Lightest
  of the three.

**Open design questions for `--set` specifically** (the substantive
one of the three):

- **Replay semantics for overridden forks.** Does the override
  apply during replay of the shared prefix (so re-fired
  behaviors see the new setting), or only on events emitted
  after the fork point? The semantically-clean answer is
  "after the fork point only — the prefix is the parent's
  recorded history with the parent's settings, and replay must
  reproduce it." But that means a setting change pre-fork-point
  in the override is silently ignored, which is surprising.
  Alternative: refuse the fork if the prefix contains events
  the override would have changed. Pick before implementation.
- **Error timing.** Unknown override keys fail loud — but
  where? At fork-time (the CLI rejects the flag), at load-time
  (the next `Runtime.load` errors), or both? The cleanest
  answer is fork-time (fail as early as possible), but that
  requires the CLI to know the pack's settings schema at fork
  time, which means importing the pack. That's expensive on a
  CLI command that otherwise only does SQL.
- **Type coercion failure.** Pydantic does the coercion; what
  happens when the override value can't coerce? Same timing
  question as above.
- **Schema migration.** Adding the override column on `runs`
  bumps `schema_version`. The migration is straightforward
  (nullable column with default null) but it does mean the
  store's schema version advances and existing runs need
  `activegraph migrate` to upgrade.

**Affected user-facing docs that currently reference `--set`** (in
the order they need footnote admonitions until v1.1 #1 lands):

- `docs/cookbook/common-patterns.md` — the fork-and-diff recipe.
  Becomes the canonical home for the Python-API form of
  fork-with-override (new sub-recipe added in rc1 #2/5).
- `docs/concepts/forking.md` — admonition pointing at the
  cookbook recipe.
- `docs/reference/cli.md` — admonition pointing at the cookbook
  recipe.

The footnotes use mkdocs-material admonition syntax (`!!! note`)
and are single-sentence pointers, not heavy "known limitations"
sections.

**Affected internal docs (transcript spec — `--memo` and
`--search`):** no user-facing footnotes needed; the transcript is
the implementation contract, not user-facing reading. Spec entries
stay in place as the v1.1 #1 contract.

## v1.1 #2. Spec-vs-impl drift gate for CLI flags

The v1.0 broken-link CI gate (`tests/test_doc_links.py`) catches
documentation URLs that don't resolve to real pages. There's no
equivalent gate for CLI flags referenced in docs that don't exist
in code. The `--set` gap (and the `--memo` / `--search` gaps it
revealed) shipped through v1.0-rc1 because the gate didn't exist.

**Implementation scope:**

- Parse CLI flag references from doc pages. Two source forms:
  - Code blocks marked as `bash` or `shell` — regex against
    fenced blocks for `activegraph <subcommand> --<flag>`
    patterns.
  - Prose mentions in inline code spans (`` `--flag` ``) — same
    regex, narrower context.
- Build the truth set from the actual click command surface.
  Walk the `cli` group, enumerate every subcommand, and pull
  each subcommand's declared options. Click exposes this
  introspectably; no source-code regex needed.
- Compare: every flag referenced in docs must appear in the
  truth set for its named subcommand. Mismatch fails CI with a
  per-flag report (similar shape to the broken-link gate's
  per-URL report).
- Test file location: `tests/test_doc_cli_flags.py`. Same
  pytest-failure-on-gap pattern as the broken-link gate.

**Edge cases the gate should handle gracefully:**

- Flag patterns inside intentional negative examples ("don't do
  `activegraph fork --foo`"). Either annotate these with a
  comment the gate can skip, or accept that prose contradicting
  the spec is a finding worth surfacing.
- Subcommand-less flag references (`--json` mentioned without a
  subcommand context). Skip if subcommand-less; the truth-set
  check requires a known subcommand.
- New flags added in code but not yet documented. The gate
  doesn't need to fail on these (the doc-coverage discipline is
  separate); flag-existence is one-directional.

**Why this gate matters beyond the immediate gaps:** the CLI is
the framework's most operator-visible interface. A user reading
the docs and hitting a usage error on an undocumented flag loses
trust in the doc site instantly. The gate keeps the trust
contract by making spec-vs-impl drift a CI-blocking finding,
same shape as the broken-link gate did for URLs.

### v1.1 #2 expansion: executable Python snippets in docs

The v1.0-rc1 user-test gate surfaced finding **B2** (step 7 fork
snippet crashed with `MissingProviderError` — a documented-behavior-
that-doesn't-match-shipped-behavior gap of the same shape as `--set`).
The CLI-flags gate would not have caught it: the drift was in a
Python code block in `docs/quickstart.md`, not a CLI invocation.

The v1.1 #2 gate's scope expands to cover executable Python snippets
in tutorial and cookbook pages. Scope shape:

- A snippet is "executable" if it's a complete fenced `python` block
  marked with an opt-in annotation (e.g., `<!-- gate: execute -->`
  immediately above the fence) or matches a documented allowlist of
  tutorial-canonical pages.
- The gate runs each executable snippet in a subprocess against the
  bundled fixtures, asserts exit 0, optionally asserts expected stdout
  patterns (snapshot-shaped).
- The gate covers tutorial pages (currently just `docs/quickstart.md`)
  and the cookbook fork-with-override recipe. Other pages opt in via
  the annotation.
- Test file location: `tests/test_doc_python_snippets.py`. Same
  pytest-failure-on-gap pattern as the CLI-flags gate and the
  broken-link gate.

The v1.0-rc2 work lands a tactical down-payment: `tests/test_tutorial_snippets.py`
covering step 7 only (the snippet that crashed). The full v1.1 #2
gate generalizes that pattern across the doc surface.

## v1.1 #3. Type-completeness on the public-surface allowlist

CONTRACT v1.0 #C5's mypy --strict gate baselines at 22/38
allowlist modules clean (57.9% of the public surface).
``docs/reference/api/TYPE_REPORT.md`` enumerates the 16 dirty
modules with their per-module error categories. The v1.1
type-completeness follow-on closes that gap in one PR series.

**The audit's converged classification.** Mypy's view of a module
depends on which siblings are in scope: with ``follow_imports =
"skip"`` and ``files = [N clean modules]``, every import outside
``files`` is Any. Adding or removing a module from ``files`` can
flip sibling modules between clean and dirty. The audit
(``scripts/audit_types.py``) converges to the largest stable set
by iteratively removing modules with findings until the set is
fixed under one mypy invocation. The pyproject ``files`` and
``[[tool.mypy.overrides]]`` lists are exactly that converged set.

**The 16 dirty modules** (TYPE_REPORT categories shown for
triage; full breakdown in the report):

- The bulk are mechanical fixes — ``no-untyped-def`` (missing
  annotations on internal helpers), ``type-arg`` (unparameterised
  generics like bare ``dict``), ``no-any-return`` (returning a
  value typed Any from a function whose return type is
  concrete).
- A smaller tail needs per-case judgment — ``attr-defined``
  (genuine missing attribute, usually a Protocol gap),
  ``arg-type`` (a real type mismatch that needs a Union or a
  TypeGuard), ``misc`` (subclassing Any, etc.).

The mechanical categories are most of the work. The v1.1 PR
series closes them in 3-5 commits grouped by category: one
commit for ``no-untyped-def``, one for ``type-arg``, one or
two for ``no-any-return``, one for the tail.

**Re-baseline discipline.** When a dirty module is fixed:

1. Run ``python scripts/audit_types.py``. The converged clean
   set updates; the report regenerates.
2. Add the now-clean module to both ``files`` and the per-module
   ``[[tool.mypy.overrides]]`` strict block in pyproject.toml.
3. Run ``mypy`` to confirm the gate still passes with the
   expanded set.

The gate's purpose isn't to be strict everywhere — it's to
prevent regression on the surface that's already clean. Each
dirty module fixed and added to the gate is a one-way
ratchet.

**v1.1 milestone end state.** All 38 allowlist modules in the
gate; mypy --strict at 100% across the public surface; the
audit script retained as a regression check for future
allowlist expansions (new pack ``__all__`` entries, new public
re-exports).

## v1.1 #4. Docstring-completeness on the public-surface allowlist

CONTRACT v1.0 #C2's docstring gate baselines at 92/100 Ring 0
symbols not-missing (92.0%) and 84.7% Ring 1 not-missing.
``docs/reference/api/COVERAGE_REPORT.md`` is the audit; the
gate's curated exemption list is ``docstring_gaps.toml``.

**The gate's pass condition.** Ring 0: every symbol in
``activegraph.__all__`` and pack-level ``__all__``s must have
a docstring (one-line counts) OR be listed in
``docstring_gaps.toml`` with a reason. Ring 1: aggregate
not-missing coverage must stay at or above the floor (currently
80%) set in the toml's ``[ring1]`` block. A regression on
either gate fails CI.

**Sibling burndown list.** The mypy gate's per-module overrides
in ``pyproject.toml`` and the docstring gate's per-symbol
exemptions in ``docstring_gaps.toml`` are the two halves of
the public-surface completeness milestone. The same set of
modules drives both audits (``activegraph.__all__`` plus pack
``__all__``s); the same v1.1 PR series can close both gaps in
parallel.

**Two-stage v1.1 target.**

- **Wave 1** — close every Ring 0 "missing" gap by adding a
  docstring (at minimum a one-liner). Each closed gap removes
  its entry from ``docstring_gaps.toml``. Wave 1 end state: the
  exemption list is empty; the gate passes against 100% Ring 0
  not-missing.
- **Wave 2** — upgrade every one-line docstring on the public
  surface to a "full" docstring (per the audit's classification:
  ≥3 lines or has Args/Returns/Raises/Examples). Wave 2 end
  state: COVERAGE_REPORT.md shows 100% Ring 0 fully-documented.
  The gate doesn't enforce wave 2 directly — the audit is the
  measure.

Ring 1 burndown follows the same staging but the gate's
threshold is the only CI-enforced floor; per-symbol upgrades
are deliberate PR work, not gate exemptions.

**Re-baseline discipline.** When a Ring 0 symbol gets a
docstring:

1. Run ``python scripts/audit_docstrings.py`` to refresh
   ``COVERAGE_REPORT.md``.
2. Remove the symbol's entry from ``docstring_gaps.toml``.
3. Run ``python scripts/gate_docstrings.py`` to confirm the
   gate still passes (and note the "stale exemption" warning
   if you missed step 2).

When a new public symbol is added without a docstring, the
gate fails CI — same protection-against-regression shape as
the mypy gate.

**v1.1 milestone end state.** Wave 1 + Wave 2 + Ring 1
upgrades + the mypy --strict allowlist all hit 100%. The
public surface meets the CONTRACT v1.0 #C2 target end-to-end.
The audit scripts stay in place as regression checks for
future allowlist expansions.

## v1.1 #5. `Runtime.load` auto-provider ergonomics (from user-test B2)

The v1.0-rc1 user-test gate surfaced **B2**: the tutorial's step 7
fork snippet crashed because `Runtime.load(...)` was called without
`llm_provider=`, and the loaded diligence pack has LLM behaviors.
The v1.0-rc2 tactical fix passes `RecordedDiligenceProvider`
explicitly — preserves "no API key required" and matches the parent
run's provider.

The v1.1 design question this defers: should `Runtime.load(...)`
discover a provider from the loaded pack's declaration, or from a
recorded-provider sidecar in the run's metadata? The current behavior
(explicit `llm_provider=` required) is defensible — explicit over
magic — but if the tutorial's step 7 is a representative shape, real
fork workflows trip over it. The right v1.1 affordance is one of:

- **`auto_provider=True` flag on `Runtime.load`** — opt-in inference
  from the run's recorded `pack.loaded` and `llm.responded` events
  (the events name the provider class indirectly via model + fixture
  identity). Backward-compatible; default stays explicit.
- **Pack-declared default provider** — the pack manifest names a
  default provider class; `Runtime.load` picks it up. Tighter
  coupling between packs and providers; pack authors decide.
- **Sidecar in run metadata** — the parent run records its provider
  class identity at `pack.loaded` time; forks inherit unless
  overridden. Most magic; most likely to surprise.

The v1.0 ban on new runtime capability is the reason this didn't
land in rc2. v1.1 reopens the capability surface and picks one.

Open design questions for v1.1:

- Replay strictness interaction. If `auto_provider=True` infers
  `RecordedDiligenceProvider` from the parent's events but the
  caller is in `replay_strict=True` mode, the inferred provider
  has to produce byte-identical responses or the strict check fires.
  Does the inference also load the parent's recorded responses into
  the LLM cache? (Likely yes — but lock the answer in v1.1.)
- Provider-class identity. The runtime knows the provider produced
  the parent's `llm.responded` events; it doesn't know which Python
  class produced them. Inference needs a registration mechanism —
  providers self-identify on a registry, `Runtime.load` looks up by
  identifier from the parent's metadata.
- Failure mode when inference fails. Today: `MissingProviderError`
  at first LLM call. With auto-inference: same error but with
  recovery prose pointing at "register your provider on the
  registry, or pass `llm_provider=` explicitly." The error message
  carries the inference attempt's findings.

## v1.1 #6. Version-tag-correspondence CI gate (from user-test C1)

The v1.0-rc1 user-test gate surfaced **C1**: README announced
`v1.0-rc1` but `activegraph --version` returned `0.9.1`. The
existing version-sync gate (`tests/test_version_sync.py`) asserts
that `activegraph.__version__` matches `pyproject.toml`'s version;
both can be wrong together and the gate stays green.

The v1.1 gate closes the loop. Same shape as the existing version-
sync gate, the broken-link gate, the CLI-flags gate, the v1.1 #2
Python-snippets gate: small, mechanical, prevents a class of finding
from recurring.

**Implementation scope:**

- Read the latest annotated tag in the current branch
  (`git describe --tags --abbrev=0` or equivalent).
- Normalize the tag to PEP 440 form (strip leading `v`, dash-to-no-
  dash for pre-release suffixes: `v1.0-rc2` → `1.0.0rc2`).
- Assert it matches `activegraph.__version__`.
- Run only in tagged-release CI runs (skip in feature-branch CI,
  where there's intentionally no tag).
- Test file: `tests/test_version_tag_sync.py`. Skip-with-reason when
  no tag is reachable, so feature branches aren't blocked.

**Edge cases:**

- Pre-release tags before merge to main — skip cleanly.
- Tags with build metadata (`+local.123`) — strip build metadata
  before compare.
- Multi-tag commits (rare) — sort by version and compare against
  the highest-version tag.

## v1.1 #8. Package-data completeness CI gate (from user-test B3)

The v1.0-rc2 user-test gate surfaced **B3**: the published wheel
omitted `activegraph/packs/diligence/prompts/*.md`, so every fresh
`pip install activegraph` crashed in the quickstart with
`PackPromptLoadError: prompts directory does not exist`. The bug
was structurally invisible to all existing CI gates — every test
ran against the source tree (or an editable install), where the
prompts directory is present by definition. Only a clean-environment
install from the built wheel exposes the gap.

The v1.1 gate closes the loop. Same shape as the version-sync
gate, the broken-link gate, the CLI-flags gate, the v1.1 #2
Python-snippets gate, the v1.1 #6 version-tag-correspondence gate:
small, mechanical, prevents a class of finding from recurring.

**Why this gate is in v1.1 and not v1.0:**

v1.0's user-test gate (CONTRACT v1.0 #C4) was the layer that caught
what internal CI couldn't, and B3 specifically required PyPI-
installability — or at least clean-venv-wheel-installability — to
surface. The rc1 user-test ran from a cloned repo because PyPI
publication wasn't a prerequisite yet, so packaging bugs were
structurally untestable by the rc1 gate, not just not caught by
it. v1.1 #8 institutionalizes wheel-artifact verification inside
CI, which moves the user-test boundary outward by one layer. After
this gate lands, the lighter user-test verifies the PyPI artifact
(CDN, upload, distribution) — not the wheel itself. The user-test
gate doesn't disappear; its scope tightens to what only an external
user can verify.

This implicitly schedules a related-but-separate v1.x gate (not
v1.1 — too late for that scope): the lighter user-test reframed as
CI-augmented manual verification, with a structured checklist that
distinguishes "what CI verified" from "what only a human can
verify." Filed as a v1.x candidate; tracked when v1.x scope opens.

**Implementation scope:**

- New CI step that runs `python -m build --wheel`, creates a fresh
  venv, installs the built wheel (NOT `pip install -e .` — the
  gate's whole point is to test the wheel artifact, not the source
  tree), and runs a smoke command that exercises every runtime
  data path.
- Smoke command shape: `activegraph quickstart` (the default,
  non-interactive fixture-backed demo — no API key, no network).
  The quickstart is the v1.0 spec (CONTRACT v1.0 #1) and exercises
  the diligence pack end-to-end, which loads all 4 prompts. If
  any runtime data file is missing from the wheel, the smoke run
  fails with the same `PackPromptLoadError` (or analog) that the
  user-test caught.
- Test file: `tests/test_wheel_completeness.py`. Orchestrates the
  build-and-install via subprocess; the smoke run lives in the
  same test. Marked with `@pytest.mark.slow` so it runs in CI but
  not in the default local `pytest` invocation.
- New workflow file: `.github/workflows/wheel-completeness.yml`.
  Runs on every PR to main AND on push to main, same trigger
  shape as `types.yml` and `docstrings.yml`. **The CI invocation
  must explicitly pass the slow marker** (`pytest -m slow
  tests/test_wheel_completeness.py`) so the gate actually runs;
  the marker is opt-in for local-dev ergonomics, opt-in by name
  in CI.
- **Required for merge.** The version-sync gate, broken-link gate,
  mypy gate, and docstring gate are merge-blockers (configured as
  required status checks on the `main` branch); v1.1 #8 needs the
  same status. Otherwise a regression that fails the gate can still
  merge if the reviewer doesn't notice the red check.

**Why a runtime smoke test, not a wheel-RECORD assertion:**

A static check ("the wheel's RECORD file contains
`activegraph/packs/diligence/prompts/document_researcher.md`")
would catch B3 specifically but not the next analog. Future packs,
future runtime data files, future migrations — each one would
need its own RECORD assertion added. The smoke-run shape
generalizes: if the runtime exercises the data path successfully,
the data is present, regardless of category. Same discipline as
the broken-link gate (fetches every URL, doesn't enumerate URLs
by hand).

**Edge cases:**

- Optional-dep features (LLM, postgres) are not gated — the smoke
  run uses the fixture LLM provider and SQLite store, the default
  quickstart shape.
- Network-disabled CI: the smoke run must not require network
  access (no live PyPI install, no LLM provider calls). The
  fixture provider satisfies this.
- Prompt files use TOML frontmatter inside `.md` containers per
  CONTRACT v0.9 #2 (hash-as-replay-contract); the package-data
  declaration is keyed on the file extension, not the frontmatter
  format. Future packs adding alternate prompt formats (e.g.
  plain-text, JSON-Schema, YAML) need separate globs in
  `[tool.setuptools.package-data]` AND the smoke run must
  exercise them — adding a glob without exercising it would let
  the next analog of B3 slip through.
- Pre-release vs. release wheels build identically; the gate runs
  on both.

**What this gate does NOT do:**

- It does not verify the wheel matches PyPI's published artifact —
  that's a separate (and rarer) class of bug (CDN cache, broken
  upload). The gate verifies the *build* produces a runnable
  wheel; the publish workflow is responsible for uploading what
  was built.
- It does not verify documentation or developer-tool data files
  (e.g., snippets used only by the docs build). Those aren't
  shipped in the wheel and are gated by other CI steps.

## v1.1 #9. Deploy-verification CI gate (from user-test B4)

The v1.0-rc2 user-test gate surfaced **B4**: the doc site at
`https://yoheinakajima.github.io/activegraph/` (and at
`docs.activegraph.dev` per the pre-rc3 primary) returned 404, and
nobody noticed for the entire duration of the doc-site phase. Root
cause per the rc3 #2 investigation: `has_pages: false` on the repo
(GitHub Pages was never enabled) + `private: true` (private-repo
Pages requires a paid GitHub plan). Both are externally-owned
operational decisions; the agent flagged them and paused for the
user's call.

The deeper finding behind B4: `tests/test_doc_links.py` is scoped
to source-tree presence, not HTTP reachability. Its own docstring
called this out (*"a separate check can verify the rendered URLs
resolve over HTTP"*) but the separate check never landed. The 404
dwelled silently because every CI gate verified the docs `.md`
source files existed, not that they were being served.

v1.1 #9 closes the loop. Same shape as v1.1 #8: small, mechanical,
prevents a class of regression.

**Why this gate is in v1.1 and not v1.0:**

v1.0's CI verified the docs source tree (`test_doc_links.py`); the
doc site's *reachability* was assumed-without-checking. Same
boundary as the B3 case: internal source-tree checks ship green,
the external artifact is broken, the user-test catches it because
no other layer exists to catch it. v1.1 #9 institutionalizes HTTP-
reachability verification inside CI, which moves the user-test
boundary outward by one more layer. After this gate lands, the
lighter user-test verifies the published-domain experience as a
whole (does the link from the README land on a page that reads
well? does the navigation feel right?) — not the basic
reachability question of "does this URL return 200."

**Implementation scope:**

- New test file: `tests/test_doc_site_reachable.py`. Imports
  `DOCS_BASE_URL` from `activegraph.errors` so the test stays in
  sync with the cutover constant. Fetches the base URL and a
  small set of known-good page paths (`/quickstart`,
  `/reference/errors/`, one per top-level section). Asserts
  HTTP 200 and that the response body contains `Active Graph`
  (the `site_name` from `mkdocs.yml` — broad enough to survive
  theme changes, narrow enough that an unrelated catchall page
  fails the check).
- Marked `slow` so local `pytest` doesn't pay the network cost
  on every invocation. CI invokes the test explicitly via
  `pytest -m slow tests/test_doc_site_reachable.py`.
- New workflow file: `.github/workflows/deploy-verification.yml`.
  Runs on push to main (post-deploy verification) and on a
  scheduled cron (catches drift if the site goes down later
  without a code change). Pull-request trigger is intentionally
  omitted — branch PRs don't change what's published, so running
  the check per-PR is noise.
- **Required for merge once Pages is enabled.** Same status as
  the v1.1 #8 gate. The branch protection rule is the user-owned
  flip; the workflow exists immediately, runs immediately, but
  blocking only kicks in when the gate is stably green.
- **Advisory until then.** The user's rc3 visibility decision was
  "yes public but wait til fully ready" — meaning the gate WILL
  be red on every rc3 CI run. The red signal is the discipline
  call to action: the gate is green only when the doc site is
  actually live. Until then, the gate's failure message names
  the operational steps that close the gap (enable Pages,
  configure DNS, register .dev redirect).

**Failure-mode design:**

When the gate fails, the failure message must distinguish the
common cases so the maintainer doesn't have to debug:

- DNS failure (host not found) -> "DNS for $DOCS_BASE_URL is not
  resolving. Verify the CNAME record points at
  yoheinakajima.github.io."
- HTTP 404 -> "$DOCS_BASE_URL resolves but returns 404. Verify
  GitHub Pages is enabled (Settings → Pages → Source: GitHub
  Actions) and that the docs workflow's most recent deploy job
  succeeded."
- HTTP 200 but content mismatch -> "$DOCS_BASE_URL returns 200 but
  the body does not contain 'Active Graph'. Verify the deploy
  artifact is the framework's site, not a default Pages
  landing page or a catchall."

**What this gate does NOT do:**

- It does not verify every URL in the docs site (link-rot
  detection). That's a higher-frequency check (mkdocs's own link
  checker, run during `mkdocs build`) and a different scope.
  v1.1 #9 verifies the *site is reachable*; mkdocs verifies the
  *site is internally consistent*.
- It does not verify the doc site's rendering quality (broken
  CSS, missing fonts, JS errors). That's a manual user-test
  concern; the gate is a binary reachability check.
- It does not verify the .dev redirect, since the redirect is
  user-owned operational state outside the codebase. If you
  want a redirect-correctness check, it lives in the same gate
  later as a separate assertion ("GET docs.activegraph.dev
  should 301/302 to docs.activegraph.ai") — adding it now would
  fail loudly during the period before you register .dev.

**Edge cases:**

- CI without outbound network: GitHub Actions runners have
  network access; if the test ever runs in an air-gapped
  environment (some forks of the workflow), it will fail with a
  connect error, which is correct — the gate cannot verify what
  it cannot reach.
- Transient site outage: a single fetch failure shouldn't be
  enough to fail the gate. The test retries on connection
  errors (3 attempts, exponential backoff) before giving up.
  HTTP 404 / 500 status codes are not retried — those are real
  failures, not transient.
- Rate-limiting from GitHub Pages: unlikely at the gate's
  frequency (cron + post-merge), but the test uses a 10-second
  timeout per request so a hung connection fails the gate
  rather than the workflow.

## v1.1 #7 and beyond

The remaining v1.1 scope (from CONTRACT v1.0 PR-G end-of-series
tally and the existing v1.1 error-completeness milestone notes
above):

- The 22 partial Pack* migrations from CONTRACT v1.0 PR-E.
- The 4 deferred DB-error wrappers from CONTRACT v1.0 PR-C.
- The 2 internal-evaluator errors needing framework-version
  context from CONTRACT v1.0 PR-G.
- **Fork cache pre-population symmetry** (discovered during
  v1.0-rc2 #4/8 B2 fix). `Runtime.fork(at_event=...)` in-process
  pre-populates the LLM cache from the parent's recorded
  `llm.responded` events (CONTRACT v0.6 #8). The persistent shape
  (`SQLiteEventStore.fork_run()` followed by `Runtime.load(...,
  run_id=<fork>)`) does not — the loaded fork sees only the
  fork's events, which end at the fork point. Forks via the
  persistent shape replay their LLM behaviors against the live
  provider rather than the cache. The two paths should be
  symmetric: `Runtime.load` of a fork run should look up the
  parent's `forked_at_event_id` (already on the runs row) and
  pre-populate the cache from the parent's events through that
  point. New runtime behavior, banned in v1.0; opens up in v1.1.
- **OpenAI tool-shape translation in `Tool.to_definition()`** (filed
  during v1.0.1 #5 OpenAI provider work). The shipped framework's
  `Tool.to_definition()` emits the Anthropic tool shape
  (`{name, description, input_schema}`); OpenAI's chat-completions
  API expects `{type:"function", function:{name, description,
  parameters}}`, and the response carries `tool_calls` rather than
  `tool_use` content blocks. `OpenAIProvider.complete()` currently
  raises `LLMBehaviorError(reason="llm.network_error")` when
  `tools=` is non-empty, with a v1.1 pointer. v1.1 scope: provider-
  aware tool-shape translation (either parameterize
  `Tool.to_definition(provider="anthropic"|"openai")` or push the
  shape into the provider's `complete()`), parallel `tool_calls`
  extraction in `OpenAIProvider`, and parity tests across both
  providers for the same tool-using behavior.
- **Native structured-output modes for providers that support them**
  (filed during v1.0.1 #5 OpenAI provider work). Both shipped
  providers use the framework's instruction-based path: the schema
  + example instance go into the system prompt via
  `build_system_prompt` (CONTRACT v1.0.1 #2), the provider parses
  JSON via `parse_structured_response`. OpenAI's
  `response_format={"type":"json_schema",...}` mode (and analogous
  future provider modes) would force the model to emit conforming
  JSON natively, eliminating the schema-echo failure mode v1.0.1
  #2 patched in the prompt. v1.1 scope: opt-in flag on
  `@llm_behavior` (or per-provider config) toggling native mode,
  cache-key impact analysis (prompt-hash inputs change because the
  system prompt shrinks), and replay-determinism guarantees
  across the mode flip.
- Any additional v1.0-rc2 user-test findings (the lighter
  verification pass after rc2 lands).

This list is the v1.1 backlog. CONTRACT v1.1 expands it into a
proper milestone scope when v1.1 work begins.


# v1.0.5.post2 — type-system concepts page (single finding from maintainer doc-gap review)

A maintainer-driven review of the doc site found that the framework's
type system is documented across four concepts pages (`graph.md`,
`events.md`, `relations.md`, `patches.md`) plus the pack-authoring
guide, with no single page that answers the question a new reader
arrives with: *what types are framework-defined, what types are
developer-defined, and how do they compose?* The "are there
framework base object types?" question, in particular, is
unanswered by the existing pages — a reader has to assemble the
answer from `graph.md`'s "freeform if no pack declares it" line,
`events.md`'s framework-event listing, and the Diligence pack's
ObjectType declarations. A maintainer reading the README and
`docs.activegraph.ai` could not find a direct answer.

v1.0.5.post2 ships a single new concepts page. No framework code
changes. No new public API. No reshape of any locked decision
below v1.0.5.post2.

## v1.0.5.post2 #1. The type-system concepts page lands at `docs/concepts/type-system.md`

### The contract claim

After v1.0.5.post2, the doc site carries a `Type system` concepts
page at `docs/concepts/type-system.md`, slotted into the Concepts
nav between `Graph` and `Events` (the position that matches its
role: graph explains the world the framework reasons about, type
system explains the vocabulary that world is described in, events
explains the framework-defined vocabulary's mutation history).

The page commits to four claims, each restated below as a contract
boundary:

**(a) Object types are developer-defined strings; the framework
ships zero base object types.** `Graph.add_object(type, data)`
accepts any string. There is no central registry, no required
`register_object_type(...)` step, no enum. The Diligence pack's
eight object types (`claim`, `evidence`, `question`, …) are an
example ontology, not framework base types. This is the answer to
the gap the maintainer review surfaced; the page makes it explicit
because users from typed-schema backgrounds will expect a
schema-definition step and need to be told there isn't one.

**(b) Relation types follow the same model.**
`Graph.add_relation(source, target, type)` accepts any string; no
central registry. Pack-declared `RelationType` may pin endpoints
(e.g., `supports: evidence → claim`); without a pack-declared
rule, any source/target/type combination is allowed.

**(c) Event types are framework-defined.** The page enumerates
the complete set of framework-emitted event types in named
families: lifecycle (`goal.created`, `runtime.idle`,
`runtime.budget_exhausted`), graph mutations (`object.created`,
`object.removed`, `relation.created`, `relation.removed`),
behavior dispatch (`behavior.scheduled`, `behavior.started`,
`behavior.completed`, `behavior.failed`,
`relation_behavior.started`), patterns (`pattern.matched`), LLM /
tools (`llm.requested`, `llm.responded`, `tool.requested`,
`tool.responded`), patches (`patch.proposed`, `patch.applied`,
`patch.rejected`), approvals (`approval.proposed`,
`approval.granted`), pack lifecycle (`pack.loaded`). User code may
emit additional types via `graph.emit`; the framework treats
unknown types as opaque payload carriers.

**(d) Patch lifecycle states are framework-defined.** Three
values — `proposed | applied | rejected` — defined on
`activegraph/core/patch.py:30`. `proposed` is the only
non-terminal state; misuse of the lifecycle raises
[`invalid-patch-lifecycle-state`](../reference/errors/invalid-patch-lifecycle-state.md).
The page enumerates the values; `concepts/patches.md` remains the
canonical lifecycle prose. The page exists to put every
framework-defined vocabulary in one place; the single-source-of-
truth pattern (HANDOFF.md "Discipline patterns") keeps the
canonical prose on the dedicated page.

### What the page also includes

- **Composition narrative** — how the framework's event-type
  vocabulary and the developer's object/relation-type vocabulary
  interlock: events drive behaviors; behaviors create objects and
  relations of developer-chosen types; those creations emit more
  framework-defined events; the cycle continues.
- **Ontology design guidance** — three rules surfaced across the
  v0.7 / v0.9 / external-research-agent ontologies: object types
  are nouns describing roles in the domain (not data bags);
  relation types are verbs or predicates describing meaningful
  structure (not generic `related_to`); keep the vocabulary
  small. The deep-research-agent user-test finding ("object types
  should be nouns describing their role in the pipeline, not just
  data bags") is referenced explicitly as the surfacing path.
- **Diligence pack as the worked example** — every object type
  and every relation type rendered as tables, framed explicitly
  as "an example ontology, not framework base types."

### Drift surface and prevention

Two surfaces could drift between this page and the framework:

1. **The enumerated event-type list.** If a future amendment adds
   a new framework event type, the page's listing should grow
   with it. The protection is the same kind that protects every
   other framework-vocabulary doc: amendments that add a new
   event type are out of scope for v1.0.5.post2 (CONTRACT v1.0
   #4b locks "no new event type" through v1.0.5; v1.1 owns the
   first opportunity for new event types). When v1.1 lands its
   first new framework event type, the amendment naming the new
   type also updates this page — same cross-amendment discipline
   that keeps `events.md` and the trace formatter in sync.

2. **The Diligence pack ontology tables.** The tables are
   transcribed from `activegraph/packs/diligence/object_types.py`.
   If a v1.x release modifies that file (currently locked by
   CONTRACT v0.9 #15 #16 #17 plus v1.0.5.post1 "no reshape"
   discipline), the table updates in the same commit. The
   pre-existing pack-stability discipline carries the weight.

### What the page deliberately does NOT do

- **Does not reshape the existing concepts pages.** `graph.md`,
  `events.md`, `relations.md`, `patches.md` stay byte-identical.
  The new page references them; it does not subsume them.
- **Does not document the `behavior.failed` reason-code
  taxonomy.** That is a payload-field vocabulary on a single
  event type, not a fourth type-system layer; `failure-model.md`
  is the canonical home and the new page cross-links there.
- **Does not introduce a code-side contract.** No new public
  class, no new public method, no new event type, no new reason
  code, no new error class.

### Tests (Standing Rule §2)

The boundary the v1.0.5.post2 #1 amendment names is "the
type-system concepts page lands at `docs/concepts/type-system.md`,
referenced from the Concepts nav, and committed to the four claims
(a)–(d) above." The existing doc-link gate
(`tests/test_doc_links.py`) anchors on the navigation surface —
broken links from the new page to existing pages, and broken
cross-references from existing pages to the new page (none in
v1.0.5.post2; all cross-references are inbound to the new page),
fail the gate. The `tests/test_llms_txt.py` gate from v1.0.5 #1
asserts the generated `llms.txt` / `llms-full.txt` reference the
nav-anchor pages, so the new page automatically appears in both
files after `mkdocs build` (the structural drift prevention
v1.0.5 #1 already named).

No new dedicated test ships in v1.0.5.post2. The boundary the
amendment names ("a concepts page exists at this path with
these claims") is verified by the existing doc-link gate plus
the human review of the page's prose — a pure-prose docs release
does not earn a Python-side test the way an API-shape claim
would. The Standing Rule §2 question for docs-only releases is
*"would a future drift between this amendment and the file
silently pass any gate?"* — the answer for v1.0.5.post2 is no,
because the doc-link gate fails if the file is removed and the
llms-txt gate fails if the file is removed from the generated
index. Both failures point at the v1.0.5.post2 #1 boundary.

### Version bump

`pyproject.toml`'s `version = "1.0.5.post1"` becomes
`version = "1.0.5.post2"`. `activegraph/__init__.py`'s
`__version__` follows. Three error snapshots that embed the
framework version
(`tests/snapshots/errors/internal_bug__pattern_unknown_op.txt`,
`internal_bug__graph_view_unknown_op.txt`, `schema_version_
mismatch.txt`) are rebaselined to the new string — same
mechanical pattern v1.0.5.post1 used. No other version-bearing
file changes.

### Discipline note on the "no base types" finding

The diagnosis behind v1.0.5.post2 #1 surfaced one observation
that did not change but is worth recording: the framework's
**absence of base object types is itself a locked decision**,
inherited from CONTRACT v0.5 onward (graph mutation is
event-emission with no central type schema) and reaffirmed by
v0.9 #21 (backward compatibility is absolute; pre-v0.9 untyped
behavior is preserved when no pack contributes the type). The
new page documents this stance explicitly; the underlying
decision has been load-bearing since v0.5 without a single
amendment ever proposing to add a central object-type registry.
v1.0.5.post2 makes the implicit stance explicit in
user-readable prose; the framework's behavior is unchanged.

## v1.0.5.post2 deliberately does NOT touch

- **No code changes under `activegraph/`.** Pure docs + version
  bump + snapshot rebaseline (the snapshots embed the version
  string; rebaselining them is the version-bump tail).
- **No new abstraction.** No new public class, no new public
  method, no new event type, no new reason code, no new error
  class, no new CI gate.
- **No reshape of any locked decision below v1.0.5.post2.**
  Every surface from v1.0 through v1.0.5.post1 stays
  byte-identical.
- **No changes to other concepts pages.** `graph.md`,
  `events.md`, `relations.md`, `patches.md`,
  `failure-model.md`, and `authoring-packs.md` stay
  byte-identical. The new page cross-references them; it does
  not duplicate or subsume any of them.
- **No fix to the `object.patched` mention in `events.md`.** The
  diagnosis surfaced that `events.md:43` mentions `object.patched`
  as a framework event family, but the code emits `patch.applied`
  for direct `patch_object` calls and never `object.patched`.
  This is documentation drift, not a contract claim; filed as a
  v1.1 candidate (see below) rather than fixed here under the
  v1.0.5.post2 "no concepts-page reshape" discipline.

### v1.1 candidates surfaced by v1.0.5.post2

Restated here for `v1.1-plan.md`'s backlog:

1. **`object.patched` event-name drift in `events.md`.** The page
   lists `object.patched` as a framework event in the "Object
   mutations" family; the code emits `patch.applied` for the
   `patch_object` shortcut path. Either fix the doc (drop
   `object.patched` from the list, or rename the bullet to
   `patch.applied`) or fix the code (emit a separate
   `object.patched` event from `patch_object` independent of the
   `patch.applied` event). The fix shape depends on whether the
   intent was to model direct mutations distinctly from patch
   applies; v1.1 owns that design call.

2. **Reason-code taxonomy as a dedicated concepts page.** The
   `behavior.failed` / `tool.responded` `reason=` field carries a
   closed taxonomy (`llm.network_error`, `llm.parse_error`,
   `tool.unknown_tool`, `tool.max_turns_exhausted`, etc.) that is
   currently documented only across the per-error pages.
   `failure-model.md` is the closest existing home; a dedicated
   "Reason codes" reference (or an expansion of failure-model.md)
   could enumerate the codes the way v1.0.5.post2 #1 enumerates
   the event types. v1.1 owns the call between "expand
   failure-model.md" and "new reference page."

# v1.2 — GraphStore seam and FalkorDB backend (community-contributed arc, locked retroactively)

**Provenance note.** This section is a *retroactive* lock. The code
landed first — issues #38, #41, #43, #45 and PRs #39 and #46,
authored by an external contributor (dudizimber) between 2026-06-23
and 2026-06-29, plus the maintainer-side docs clarification in
PR #40 — and this amendment was written afterward, during v1.2.0
release preparation. That inverts the
amendment-in-the-same-commit discipline (HANDOFF.md), deliberately:
an inbound contribution arc moves at the contributor's pace, and
merging good external work mattered more than queueing it behind
contract prose. The debt is paid here: every architectural decision
the arc introduced is locked below, and the release does not ship
before this section exists. If a future inbound arc lands the same
way, repeat this pattern — merge, then lock before release.

## v1.2 #1. The materialized projection is pluggable behind a `GraphStore` seam

The graph state (objects / relations / patches) has always been a
projection of the event log (CONTRACT #2). v1.2 makes *where that
projection materializes* a pluggable choice, parallel to the
existing `EventStore` seam for the log itself.

- `GraphStore` is an ABC at `activegraph/core/graph_store.py`:
  upsert/get/remove/enumerate for the three entity kinds, plus
  `clear` and `close`. Deliberately small.
- `InMemoryGraphStore` is the default and is byte-for-byte the
  pre-seam behavior. No user action, no migration.
- The projector (`apply_event`) remains the **only writer**. The
  seam changes where writes land, never who writes.
- The event log remains the source of truth. A `GraphStore` is a
  **disposable projection**: wiping it loses nothing — replaying
  the log rebuilds it. Durability and audit continue to come from
  the `EventStore`. Losing a GraphStore is recoverable; losing the
  EventStore is not. This asymmetry is the seam's core invariant.
- Wiring is library-level only: `Graph(graph_store=...)`,
  `Runtime.load(..., graph_store=...)`,
  `Runtime.fork(..., graph_store=...)`.
- **No CLI flag** — deliberate. The CLI's read commands operate on
  the log and stay infrastructure-light; the projection backend is
  a programmatic choice. Revisiting this requires a new amendment.
- Public surface: `GraphStore`, `InMemoryGraphStore`, and
  `FalkorDBGraphStore` are exported in `activegraph.__all__`.

## v1.2 #2. `FalkorDBGraphStore` connection model and extras

`FalkorDBGraphStore` (`activegraph/store/falkordb.py`) backs the
projection with a FalkorDB graph.

- Connection precedence: explicit `url=`/`host=` arguments →
  `FALKORDB_URL` / `FALKORDB_HOST` (with optional `FALKORDB_PORT` /
  `FALKORDB_USERNAME` / `FALKORDB_PASSWORD`) environment variables →
  the embedded `falkordblite` engine as the zero-config fallback.
- Two extras: `activegraph[falkordb]` (server client) and
  `activegraph[falkordb-embedded]` (embedded engine; requires
  Python 3.12+ per falkordblite). Both are kept **out of**
  `[all]` and `[dev]` so opting into a graph database is always
  explicit.
- All Cypher values are bound `$params` — no string interpolation
  anywhere in the write or read paths. This is load-bearing for
  v1.2 #3's fixed-relationship-type decision.

## v1.2 #3. Relations are native edges; dangling endpoints are derived placeholders

The first FalkorDB implementation (PR #39) stored relations as
standalone `AGRelation` *nodes* to preserve dangling-relation
semantics. Issue #41 showed that modeling defeats the point of a
graph database (no native traversal, disconnected Browser view);
PR #46 landed the remodel. Locked shape:

- A relation is a native edge with the **single fixed relationship
  type `AGRelation`**; the relation's kind rides in the `type` edge
  property (with `id` / `data` / `provenance`). The fixed literal
  keeps every value a bound `$param` — relation kinds never enter
  query text. Traversal by kind is a property filter:
  `MATCH (s)-[r:AGRelation {type: $t}]->(t)`.
- Every endpoint node carries the shared structural label
  `:AGNode`; real objects additionally carry `:AGObject`. The
  shared label is required for correctness and performance (it is
  the per-label index that backs endpoint `MERGE`s), not cosmetics.
- **Placeholder state is derived, not labeled.** A dangling
  endpoint is exactly `:AGNode AND NOT :AGObject`. There is no
  `:AGPlaceholder` label; object removal on a still-referenced node
  demotes it back to a placeholder with no label bookkeeping.
- `source` / `target` are not stored properties — they fall out of
  the edge's endpoints.
- **No data migration, by construction.** The store is a disposable
  projection (v1.2 #1); a layout change re-replays the log into the
  new shape.

## v1.2 #4. Query hooks: Python defaults are the semantic source of truth

Issues #43/#45 (PR #46) added optional query hooks to `GraphStore`:
`find_objects`, `find_objects_in_types`, `find_relations`,
`neighborhood`, `match_chain` (returning `ChainMatch`). The locked
contract:

- The **base-class Python defaults define the semantics**. A
  backend MAY override a hook to push work into the database, but
  results MUST be identical to what the default would return.
  Ordering is part of the contract exactly where the hook's
  docstring says so: `find_objects_in_types` MUST preserve the
  single-pass `all_objects` order; `match_chain` MUST return the
  same matches but its enumeration order is explicitly excluded
  ("order aside"). `GraphStoreConformance` is the executable form
  of this rule.
- The rich `where` predicate language and node `{prop: value}`
  equality are **deliberately not hooks**: the structured payload
  is stored as a JSON blob, so faithful translation isn't possible;
  they evaluate in Python over the pushed-down candidate set. The
  split rule: structural filters (trivially mirrorable) push down;
  anything hard to translate faithfully stays in one place.
- Consequence on FalkorDB: cost scales with **result size**, not
  graph size — a whole pattern chain collapses into one
  index-backed Cypher query. Native `where` push-down (property
  flattening / dual-write) is named follow-on work, not part of
  this lock.

## v1.2 #5. Every third-party backend passes `GraphStoreConformance`

`activegraph/store/graph_conformance.py` ships a pytest-collectable
ABC that exercises the full `GraphStore` contract — round-trips,
removal cascades, the v1.2 #4 hook semantics, placeholder
promotion/demotion, cycle-safe neighborhood walks. `InMemoryGraphStore`
and `FalkorDBGraphStore` both inherit it. The rule for any future
backend (Neo4j and Postgres-graph are the anticipated candidates,
per the v0.5 #19 out-of-scope list): subclass the conformance
suite in `tests/`, green before merge. The suite is the extension
contract; prose in `docs/` describes it but never overrides it.

## v1.2 deliberately does NOT touch

- **No `EventStore` changes.** The log seam is untouched; the two
  seams stay parallel and independent.
- **No CLI surface.** See v1.2 #1's no-CLI-flag decision.
- **No `where` push-down.** Named follow-on (v1.2 #4); requires a
  property-flattening design pass first.
- **No change to the in-memory default.** Every run that doesn't
  pass `graph_store=` behaves byte-for-byte as before the seam.
- **No new event types, reason codes, or error classes** beyond
  `MissingOptionalDependency` reuse for the two extras.

## v1.2 #6. The test suite is a CI gate

Surfaced during v1.2.0 release preparation: none of the six existing
workflows ran `pytest` over `tests/` broadly. The gates covered docs
builds, docstring coverage, mypy on the clean allowlist, wheel
completeness, and doc-site reachability — every adoption surface
except the framework itself. The ~700-test suite ran only by
convention (locally, by whoever was driving), and the v1.2 FalkorDB
arc merged with no automated test execution. That contradicts the
repository's own gate philosophy (HANDOFF.md: "assuming spec equals
impl without verification" is a named failure pattern) and is closed
here.

`.github/workflows/tests.yml`:

- `pytest -m "not slow"` on push to main, pull requests, and manual
  dispatch. Slow-marked tests keep their dedicated opt-in-by-name
  workflows (wheel-completeness, deploy-verification); this gate
  never duplicates them.
- Matrix over Python 3.11 and 3.12 — the two versions the package
  classifiers declare. The 3.12 leg additionally installs
  `falkordb-embedded` (falkordblite requires 3.12+), so the
  FalkorDB store and `GraphStoreConformance` tests (v1.2 #5)
  execute rather than skip.
- A Postgres 16 service container provides
  `ACTIVEGRAPH_TEST_POSTGRES_URL`, so the Postgres `EventStore`
  conformance tests (CONTRACT v0.8 #20) execute rather than skip.
- The invariant the gate protects: **every non-slow test in the
  repository executes on at least one matrix leg.** A new
  optional-dependency skip that no leg exercises is a gate
  regression, not a neutral skip.
