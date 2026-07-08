# SnapshotIntegrityError

Loading (or forking) a compacted run found a snapshot problem: the
blob referenced by the run's `runtime.snapshot` event is missing from
the sidecar table, or does not hash to the state hash the event
pinned. The runtime refuses to project it — a corrupted snapshot
silently producing wrong state would defeat the entire honesty model
(CONTRACT v1.5 #2).

## Quick fix

The archived prefix is the ground truth the snapshot summarizes.
Audit it:

```python
from activegraph.store.retention import verify_snapshot
verify_snapshot("runs.db", run_id)
```

- If it **raises too**: the archive itself no longer reproduces the
  pinned hash — restore the store file from backup.
- If it **passes**: the archive is intact and only the sidecar row
  is damaged; re-run `compact` semantics by rebuilding the blob from
  the archive (or restore the file from backup — simpler and
  equally correct).

## What's related

- [`retention-pinned-error`](retention-pinned-error.md)
- [Replay](../../concepts/replay.md) — the projection-from-log model
  the snapshot short-circuits.
