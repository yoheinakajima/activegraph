# Patches

A patch is a proposed mutation to the graph, recorded as an event
before the mutation happens. Patches are how the framework keeps
the audit trail honest about who proposed what change, what version
of the target they observed, and whether the change succeeded or
was refused.

A direct `graph.patch_object(target, diff)` call also lands in the
event log as `patch.applied`, but the patch primitive is different:
it's a **two-phase** operation. The first phase records
the proposal as a `patch.proposed` event, with the proposer's
identity, the version of the target they observed, and the
intended diff. The second phase applies (success) or rejects
(refusal), emitting `patch.applied` or `patch.rejected`.

The two phases let policies, behaviors, or operators sit between
proposal and application. A pack's `memo_approval` policy is the
canonical example — `ctx.propose_object` produces a pending
approval, the operator (or an auto-approve setting) calls
`runtime.approve(id)`, and the object lands only at that point.
Without the two-phase shape, the policy would have to fire after
the mutation, which is too late.

## The lifecycle

A patch begins in `'proposed'` and ends in exactly one of two
terminal states:

```
            proposed ──apply──> applied
                |
                └──reject────> rejected
```

Both transitions are one-shot. A `'proposed'` patch becomes
`'applied'` exactly once (via `graph.apply_patch(patch_id)`) or
`'rejected'` exactly once (via `graph.reject_patch(patch_id, reason)`).
Re-calling either on an already-terminal patch raises
[`invalid-patch-lifecycle-state`](../reference/errors/invalid-patch-lifecycle-state.md)
— the framework refuses to emit a duplicate `patch.applied` event
because that would break the replay contract.

Each transition emits an event:

- `patch.proposed` — carries the proposer, target id, observed
  version, diff, and any provenance metadata.
- `patch.applied` — carries the patch id, the resulting object
  version, and the mutation outcome.
- `patch.rejected` — carries the patch id and the rejection reason.

The events sit in the log alongside everything else. Downstream
behaviors can subscribe to them, the trace renders them, and
replay reconstructs the full proposal-and-decision sequence.

## Optimistic concurrency on object versions

Every object carries a version that increments on each mutation.
When a behavior proposes a patch, the proposal records the version
of the target at proposal time. When `apply_patch` runs, it checks
whether the target's current version still matches the recorded
one. If not, the patch is refused with a version-conflict reason.

The rule: **two behaviors that observed the same starting version
can both propose patches, but only the first to apply succeeds.**
The second sees the version drifted and reads its own outcome
from the rejected event — usually re-reading the target and
proposing a new patch against the new version.

The concurrency model is optimistic by design. Locks would
serialize behavior dispatch and break the parallel-firing model
that pattern subscriptions and event fan-out depend on. Version
checks at apply-time keep the audit trail honest without
serializing.

## When to use patches vs direct mutation

The test: **is this change durable or audit-critical?**

- **Yes** — use a patch. Pack policies gating writes, multi-step
  workflows where the proposal needs to survive operator review,
  any state change a downstream behavior might subscribe to via
  `patch.proposed`. The two-phase shape is the right primitive
  here.
- **No** — direct mutation is fine. Adding a new object,
  appending to a graph that has no concurrency contention,
  emitting an event whose payload doesn't represent durable
  state. `graph.add_object` and `graph.emit` cover most of this.

The default is direct mutation. Patches are for the cases where
the two-phase shape earns its weight — when proposal and decision
are semantically distinct operations the audit trail should
record separately. Most behaviors mutate directly; a small number
of policy-gated behaviors propose.

## The events-not-exceptions principle applied to patches

Patch rejection is a `patch.rejected` event, not an exception. A
behavior that proposes a patch and finds it rejected reads the
rejection from the event log; the runtime continues without
interrupting. The rejection is not a failure — it's a normal
outcome of the two-phase shape.

The exception case is misuse of the primitive: calling
`apply_patch` on a patch that's already in a terminal state. That
fires [`invalid-patch-lifecycle-state`](../reference/errors/invalid-patch-lifecycle-state.md)
because the caller can fix the bug at the call site (check status
before applying) and silently no-op'ing would emit a duplicate
event.

See [`failure-model`](failure-model.md) for the broader
principle.

## What's related

- [`graph`](graph.md) — the world state patches modify. Patches
  are projections of `patch.proposed`, `patch.applied`, and
  `patch.rejected` events, same as objects and relations.
- [`events`](events.md) — the append-only history that records
  every patch transition.
- [`behaviors`](behaviors.md) — what proposes and applies
  patches. `ctx.propose_object` is the policy-gated path.
- [`policies`](policies.md) — the mechanism that gates patches
  through approval flows.
- [`replay`](replay.md) — the operation that reconstructs the
  full proposal-and-decision sequence from the event log.
- [`failure-model`](failure-model.md) — why patch rejection is an
  event but patch-lifecycle misuse is an exception.
- [`invalid-patch-lifecycle-state`](../reference/errors/invalid-patch-lifecycle-state.md)
  — the exception for misuse of the patch primitive.
