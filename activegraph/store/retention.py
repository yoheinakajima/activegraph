"""Compaction and retention: snapshot + archive tier, never deletion.

CONTRACT v1.5 #2, phase 1 of ``compaction-design.md``. Three offline
operations over a SQLite store (no self-compaction under a live
dispatcher — run these against runs no runtime is attached to):

  * :func:`compact` — emit a ``runtime.snapshot`` event capturing the
    run's projected state, store the blob in the snapshot sidecar,
    and move the pre-snapshot prefix to the archive tier.
  * :func:`retire` — archive an entire closed, unpinned run (the
    typical case: fork trials that were rejected and never promoted).
  * :func:`pins` — the reasons a run cannot be compacted or retired.
    **The pin set dominates retention policy unconditionally**;
    any window/age policy a host builds is sugar over these calls
    and can never override a pin.

The pin set (design §2), headed by the normative retention pin from
the evolution-pack sign-off: a fork run referenced by any live run's
``promote.applied`` marker is pinned WHOLE — two-hop provenance
(promoted entity → marker → fork log) reads arbitrary depths of it.

Phase-1 boundaries, stated plainly: the archive tier is a table in
the same store file (operator-selected sidecar files are unbuilt);
``causal_chain`` does not yet read the archive (chains crossing the
horizon stop at it); there is no CLI yet; and patch records from the
archived prefix are not reconstructed after compaction (``compact``
refuses runs with patches still in ``proposed`` status, and applied/
rejected patch history remains queryable via the archive).

**What "offline" means — the attachment rule is per-RUN, not
per-file** (v1.6.x ruling; CONTRACT v1.5 #2 addendum 2b). These
operations must not run against a run that has a live runtime
attached; OTHER runs in the same store file may hold live runtimes
throughout. Why that is safe, from the implementation: every event-
row statement is scoped ``WHERE run_id = ?``; the store runs in WAL
mode, so the readers these functions open never block a live run's
writer and never see torn state; a live runtime appends via single-
statement autocommit transactions and never re-reads its own log
mid-dispatch; and the archive move is one short ``BEGIN IMMEDIATE``
transaction that a contending writer simply waits out (sqlite3's
default 5-second busy timeout — exhausted, it raises
``OperationalError``, never corrupting). What per-run attachment
actually protects against is the SAME-run case: a runtime attached
to the run being compacted mints the same event id as the snapshot
event (``UNIQUE(id, run_id)`` — ``IntegrityError``) and never learns
the horizon exists; a runtime attached to a run being retired would
keep appending to a log the operator believes empty.

Two caveats stay the caller's responsibility:

  * ``pins`` → archive is check-then-act, not one atomic unit. Do
    not race a pin-creating operation against retirement of the
    same run — a promote from the fork, or a new fork of it, landing
    between the pin check and the archive move would be archived-
    while-pinned. Retire after decisions on that run are final. (A
    lost race degrades audit walks — ``causal_chain`` does not read
    the archive — but destroys no bytes: archiving is a move, and
    the rows stay readable via ``iter_archived``.)
  * A very large run's archive move holds the write lock for the
    duration of its one transaction; a concurrent writer that
    exhausts its busy timeout gets ``OperationalError``, not
    corruption. Size housekeeping windows accordingly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from activegraph.core.event import Event
from activegraph.errors import StorageError
from activegraph.store.sqlite import SQLiteEventStore


class RetentionPinnedError(StorageError, ValueError):
    """A compact/retire was refused because the run is pinned.

    ``reasons`` carries every pin (the same list :func:`pins`
    returns), so one look answers "why can't I retire this run?".
    """

    _doc_slug = "retention-pinned-error"

    def __init__(self, *, run_id: str, operation: str, reasons: list[str]) -> None:
        self.run_id = run_id
        self.reasons = list(reasons)
        listing = "\n".join(f"    - {r}" for r in self.reasons)
        StorageError.__init__(
            self,
            f"{operation} refused: run {run_id!r} is pinned",
            what_failed=(
                f"{operation}({run_id!r}) was refused because the run is "
                f"pinned:\n{listing}"
            ),
            why=(
                "The pin set dominates retention policy unconditionally "
                "(CONTRACT v1.5 #2). Promoted-from fork logs are provenance "
                "for adopted state; runs with live children anchor their "
                "descendants' lineage; unresolved machinery (approvals, "
                "proposed patches) is recorded state a summary cannot "
                "carry. Archiving any of these would break audit walks "
                "that are contractual."
            ),
            how_to_fix=(
                "Resolve each pin first: retire abandoned descendant runs "
                "before their parent, resolve pending approvals and "
                "proposed patches, and accept that promoted-from forks "
                "stay — they are not garbage, they are provenance."
            ),
            context={"run_id": run_id, "operation": operation, "reasons": self.reasons},
        )


class SnapshotIntegrityError(StorageError, ValueError):
    """A snapshot blob does not hash-match its ``runtime.snapshot``
    event — corruption, refused loudly at load (CONTRACT v1.5 #2)."""

    _doc_slug = "snapshot-integrity-error"

    def __init__(self, *, run_id: str, expected: str, detail: str) -> None:
        StorageError.__init__(
            self,
            f"snapshot integrity failure in run {run_id!r}",
            what_failed=(
                f"Loading run {run_id!r}: the snapshot referenced by its "
                f"runtime.snapshot event failed verification. {detail}"
            ),
            why=(
                "A compacted run's projected state is reconstructed from "
                "the snapshot blob; the state hash recorded in the event "
                "is what keeps the blob honest. A mismatch means the "
                "sidecar was corrupted or tampered with, and replaying "
                "from it would silently produce wrong state."
            ),
            how_to_fix=(
                "Verify the archived prefix is intact and rebuild the "
                "snapshot from it:\n"
                "    from activegraph.store.retention import verify_snapshot\n"
                "    verify_snapshot(path, run_id)\n"
                "If the archive replays clean, re-compact; if not, restore "
                "the store file from backup."
            ),
            context={"run_id": run_id, "expected_hash": expected},
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_state_blob(graph: Any) -> str:
    """The snapshot blob: full projected state, canonical JSON.

    Provenance is INCLUDED — the snapshot must reconstruct state
    faithfully, audit fields and all. Determinism comes from sorted
    ids and sorted keys, and the state hash is computed over exactly
    these bytes (a deliberate simplification over the design draft's
    "provenance-normalized hash": hashing the canonical blob itself
    is equally deterministic and leaves nothing un-covered).
    """
    state = {
        "objects": sorted(
            (o.to_dict() for o in graph.all_objects()), key=lambda d: d["id"]
        ),
        "relations": sorted(
            (r.to_dict() for r in graph.all_relations()), key=lambda d: d["id"]
        ),
    }
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def state_hash_of(blob: str) -> str:
    """``"sha256:" + hex`` over the canonical blob bytes."""
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def pins(path: str, run_id: str) -> list[str]:
    """Every reason ``run_id`` cannot be compacted or retired.

    Empty list = unpinned. The same computation the refusals in
    :func:`compact` / :func:`retire` run — design §5's "why can't I
    retire this run?" API.

    Read-only, safe to call while any run in the file holds a live
    runtime (WAL readers never block or tear). The answer is
    point-in-time: a pin created after it returns is not in it.
    """
    reasons: list[str] = []
    store = SQLiteEventStore(path, run_id=run_id)

    # Pin 1 (the normative retention pin): promoted-from. Scan every
    # OTHER run's hot events for promote.applied markers naming us.
    for record in SQLiteEventStore.list_runs(path):
        if record.run_id == run_id:
            continue
        other = SQLiteEventStore(path, run_id=record.run_id)
        for e in other.iter_events():
            if (
                e.type == "promote.applied"
                and (e.payload or {}).get("from_run") == run_id
            ):
                reasons.append(
                    f"promoted-from: run {record.run_id!r} adopted this "
                    f"run's state at marker {e.id!r}; the whole fork log "
                    f"is provenance for that promote"
                )
                break

    # Pin 2: live lineage — children whose hot log still exists.
    for record in SQLiteEventStore.list_runs(path):
        if record.parent_run_id != run_id:
            continue
        child = SQLiteEventStore(path, run_id=record.run_id)
        if next(iter(child.iter_events()), None) is not None:
            reasons.append(
                f"live-lineage: run {record.run_id!r} forked from this run "
                f"at {record.forked_at_event_id!r} and is not retired"
            )

    # Pin 4: pending machinery — unresolved approvals, proposed patches.
    proposed_approvals: set[str] = set()
    granted: set[str] = set()
    proposed_patches: dict[str, str] = {}
    for e in store.iter_events():
        p = e.payload or {}
        if e.type == "approval.proposed":
            proposed_approvals.add(str(p.get("approval_id", "")))
        elif e.type == "approval.granted":
            granted.add(str(p.get("approval_id", "")))
        elif e.type == "patch.proposed":
            patch = p.get("patch") or {}
            proposed_patches[str(patch.get("id", ""))] = "proposed"
        elif e.type == "patch.applied":
            patch = p.get("patch") or {}
            proposed_patches.pop(str(patch.get("id", "")), None)
        elif e.type == "patch.rejected":
            proposed_patches.pop(str(p.get("patch_id", "")), None)
    pending = sorted(proposed_approvals - granted)
    if pending:
        reasons.append(
            f"pending-approvals: {', '.join(pending)} are unresolved"
        )
    if proposed_patches:
        reasons.append(
            f"proposed-patches: {', '.join(sorted(proposed_patches))} are "
            f"neither applied nor rejected"
        )
    return reasons


def compact(path: str, run_id: str) -> str:
    """Snapshot ``run_id`` and archive its pre-snapshot prefix.

    Refuses pinned runs (:class:`RetentionPinnedError`) and runs that
    are already compacted with no new events since. Order is the
    design's crash-safe one: snapshot event, then blob, then the
    archive move (idempotent). Returns the snapshot event id.

    Offline operation, per-RUN: no live runtime may be attached to
    ``run_id`` itself — the snapshot event would collide with the
    ids a live runtime mints (``UNIQUE(id, run_id)``), and that
    runtime's projection would never learn the horizon exists. Other
    runs in the same file may hold live runtimes; see the module
    docstring for the full reasoning.
    """
    reasons = pins(path, run_id)
    if reasons:
        raise RetentionPinnedError(
            run_id=run_id, operation="compact", reasons=reasons
        )
    from activegraph.runtime.runtime import Runtime

    rt = Runtime.load(path, run_id=run_id, behaviors=[])
    blob = _canonical_state_blob(rt.graph)
    digest = state_hash_of(blob)
    counters = rt.graph.ids  # snapshot carries the id counters (see load)
    covered = len(rt.graph.events)
    last_id = rt.graph.events[-1].id if rt.graph.events else None

    snapshot_event = rt.graph.emit(
        Event(
            id=rt.graph.ids.event(),
            type="runtime.snapshot",
            payload={
                "state_hash": digest,
                "covers_through": last_id,
                "events_covered": covered,
                "id_counters": {
                    "object": counters._object_counter,  # noqa: SLF001
                    "event": counters._event_counter,  # noqa: SLF001
                    "relation": counters._relation_counter,  # noqa: SLF001
                    "patch": counters._patch_counter,  # noqa: SLF001
                    "frame": counters._frame_counter,  # noqa: SLF001
                },
            },
            actor="runtime",
            frame_id=None,
            caused_by=None,
            timestamp=rt.graph.clock.now(),
        )
    )
    store = rt.graph.store
    assert isinstance(store, SQLiteEventStore)
    store.put_snapshot(digest, blob, created_at=_now_iso())
    snapshot_seq = store._seq_of(snapshot_event.id)  # noqa: SLF001
    store.archive_prefix(snapshot_seq, archived_at=_now_iso())
    return snapshot_event.id


def retire(path: str, run_id: str) -> int:
    """Archive an entire closed, unpinned run. Returns rows moved.

    The typical subject is a rejected fork trial that was never
    promoted. Pinned runs refuse with the full reason list.

    Offline operation, per-RUN: no live runtime may be attached to
    ``run_id`` itself, and no pin-creating operation (a promote from
    this run, a fork of it) may race the retirement. Other runs in
    the same file may hold live runtimes throughout — retiring a
    finished fork while the parent run's runtime is live is
    sanctioned usage (see the module docstring for the transaction
    reasoning, and ``test_compaction.py``'s
    ``test_retire_fork_per_run_while_parent_runtime_is_live`` for
    the pinned behavior).
    """
    reasons = pins(path, run_id)
    if reasons:
        raise RetentionPinnedError(
            run_id=run_id, operation="retire", reasons=reasons
        )
    store = SQLiteEventStore(path, run_id=run_id)
    return store.archive_run(archived_at=_now_iso())


def verify_snapshot(path: str, run_id: str) -> bool:
    """Replay the archived prefix and prove it reproduces the snapshot.

    The design's audit tool: rebuilds the projection from
    ``events_archive`` and compares its canonical state hash to the
    one recorded in the run's ``runtime.snapshot`` event. Returns
    True on match; raises :class:`SnapshotIntegrityError` on
    mismatch; raises ``LookupError`` when the run has no snapshot.
    """
    from activegraph.core.graph import Graph
    from activegraph.core.ids import IDGen

    store = SQLiteEventStore(path, run_id=run_id)
    snapshot_event = next(
        (e for e in store.iter_events() if e.type == "runtime.snapshot"),
        None,
    )
    if snapshot_event is None:
        raise LookupError(f"run {run_id!r} has no runtime.snapshot event")
    expected = str(snapshot_event.payload.get("state_hash", ""))

    scratch = Graph(ids=IDGen(), run_id=f"__verify_snapshot__{run_id}")
    for e in store.iter_archived():
        scratch._replay_event(e)  # noqa: SLF001 — the load/fork seam
    actual = state_hash_of(_canonical_state_blob(scratch))
    if actual != expected:
        raise SnapshotIntegrityError(
            run_id=run_id,
            expected=expected,
            detail=f"archived prefix replays to {actual}, event pins {expected}.",
        )
    return True
