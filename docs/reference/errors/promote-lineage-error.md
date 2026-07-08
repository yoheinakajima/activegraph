# PromoteLineageError

`runtime.promote(fork)` was called with a source run that is not a
**direct fork** of the destination run, per the store's lineage
records. Promote trusts the runs table — the `parent_run_id` /
`forked_at_event_id` row written by `fork()` — not the caller's
claim, because the three-way delta is computed against the parent's
state at that recorded fork point.

Multi-inherits `ValueError`.

## Quick fix

List the store's runs and their lineage:

```bash
activegraph inspect sqlite:///runs.db --runs
```

Then match the failure to one of these shapes:

- **Unrelated run.** The source has no `parent_run_id`, or a
  different one. There is nothing to promote — the runs share no
  fork point. Fork from the destination run, re-apply the candidate
  change there, and promote that fork.
- **Grandchild fork.** Promote goes up **one level at a time**
  (CONTRACT v1.3 #4): promote the grandchild into its own parent
  first, then that parent one level up. Each hop gets its own
  conflict check against the level it lands in.
- **Reversed direction.** The receiver is the destination (parent);
  the argument is the source (fork) — the same orientation as
  `rt.diff(fork)`.
- **Different stores.** Both runtimes must be loaded from the same
  SQLite store; `fork()` records lineage within one store, and
  promote verifies it there.

## Why promote refuses to guess

The fork point anchors the three-way comparison that separates "the
fork's work" from "the parent's own work." With a wrong or missing
anchor, promote would either adopt parent-side changes as if the
fork made them or silently drop fork-side work — both corruptions of
the audit trail. Refusing loudly is the only honest option.

## What's related

- [Forking](../../concepts/forking.md) — the shared-lineage model.
- [Fork, test, promote](../../guides/fork-test-promote.md) — the
  full loop this error guards.
- [`promote-conflict-error`](promote-conflict-error.md) — the other
  promote refusal: valid lineage, conflicting changes.
