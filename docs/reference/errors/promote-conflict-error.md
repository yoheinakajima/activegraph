# PromoteConflictError

`runtime.promote(fork)` found entities changed on **both** sides
since the fork point — or a promoted change that would break
referential integrity — and refused to apply anything. Promote is
fail-closed and atomic (CONTRACT v1.3 #4): one conflict fails the
whole promote, and the parent is left byte-identical to before the
call.

Multi-inherits `ValueError`. The full conflict list is on
`.conflicts` — the same records a `dry_run=True` plan exposes.

## Quick fix

Inspect the conflicts before deciding anything:

```python
plan = parent.promote(fork, dry_run=True)
for c in plan.conflicts:
    print(c.kind, c.entity, c.id)
    print("  ", c.detail)
```

Three `kind` values:

- **`both_changed`** — the entity was created, patched, or removed
  on both sides since the fork point. This includes *identical*
  concurrent edits (v1 makes no semantic judgment about whether two
  equal outcomes are safe to merge) and same-id both-created
  collisions (fork and parent minted the same next id from their
  reseeded generators — expected, CONTRACT #12 scopes ids to a run).
- **`dangling_relation`** — the fork created a relation to an object
  the parent has since removed; promoting it would leave a dangling
  edge.
- **`orphaning_removal`** — the fork removed an object that the
  parent has since attached new relations to; promoting the removal
  would cascade the parent's own work away.

## The escape hatch is re-forking, not forcing

There is deliberately no `force=` flag. A conflicted promote means
the fork's change was tested against a past the parent has since
left. Fork again from the parent's **current** tip, re-apply the
candidate change there (the replay cache makes the shared prefix
cheap — no LLM re-execution), and promote the fresh fork. That
re-fork *re-tests* the change against the parent's actual present,
which is what any merge would otherwise have to pretend to do.

## What's related

- [Fork, test, promote](../../guides/fork-test-promote.md) — the
  full loop, including the re-fork recovery pattern.
- [`promote-lineage-error`](promote-lineage-error.md) — the other
  promote refusal: the runs aren't parent and direct fork.
- [Forking](../../concepts/forking.md) — fork mechanics and the
  shared-lineage model.
