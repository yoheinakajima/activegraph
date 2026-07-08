# RetentionPinnedError

`compact()` or `retire()` (in `activegraph.store.retention`,
CONTRACT v1.5 #2) was refused because the run is pinned. The pin set
dominates retention policy unconditionally — no window or age policy
can override it. `.reasons` carries every pin; `pins(path, run_id)`
returns the same list without attempting anything.

## The pins

- **promoted-from** — the run is named as `from_run` by a live run's
  `promote.applied` marker. The WHOLE fork log is provenance for the
  adopted state (promoted entity → marker → fork log); it is not
  garbage and never becomes garbage while the marker lives.
- **live-lineage** — an un-retired child forked from this run.
  Retire abandoned descendants first (bottom-up).
- **pending-approvals / proposed-patches** — unresolved machinery a
  snapshot cannot carry. Resolve (approve, or apply/reject) first.

## Quick fix

```python
from activegraph.store.retention import pins
for reason in pins("runs.db", run_id):
    print(reason)
```

Resolve what can be resolved; accept what cannot: promoted-from
forks stay.

## What's related

- [`snapshot-integrity-error`](snapshot-integrity-error.md)
- [Fork, test, promote](../../guides/fork-test-promote.md) — where
  the promoted-from pin comes from.
