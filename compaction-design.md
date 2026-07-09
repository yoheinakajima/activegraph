# Event-log compaction and retention: design

**Status: IMPLEMENTED (phase 1) — shipped in v1.5.0 as CONTRACT
v1.5 #2** (`activegraph.store.retention`: `compact`, `retire`, `pins`,
`verify_snapshot`; tests in `tests/test_compaction.py`). Originally
opened as v1.4 work (the deferred "item 14" from the July 2026
agent-readiness review) and proposed as v1.4 #3; that slot went to
`disable_pack`, and compaction locked a cycle later as v1.5 #2. This
doc is the aspirational design; where phase 1 deliberately shipped
less (or a simplification), the notes below and
`activegraph/store/retention.py`'s module docstring are authoritative
for what actually shipped.

## 0. Normative inputs

Three facts this design must not violate:

1. **Promoted-from fork logs are retention-pinned** (evolution-pack
   consumer sign-off, 2026-07-08). A fork run referenced by any live
   run's `promote.applied` marker is not garbage: two-hop provenance
   — promoted entity → marker → fork log — depends on the fork's log
   outliving the trial. Verbatim requirement, recorded here so it
   cannot get lost.
2. **Always-on assistants append forever.** The target deployment is
   a personal assistant whose SQLite log grows monotonically for
   months. Compaction is not an optimization; it is what makes the
   architecture honest at that horizon.
3. **Fork lineage is load-bearing.** The `runs` table's
   `parent_run_id` / `forked_at_event_id` row is the authority for
   `promote()` (CONTRACT v1.3 #4), and the v1.3 `upsert_run` fix
   exists because losing it silently broke real machinery. Retention
   decisions must treat lineage rows as part of the data they
   protect.

## 1. Shape: snapshot events + archive tier, never deletion

The log is truth; the projection is derived (CONTRACT #2). Compaction
therefore cannot mean "delete old events" — it means **moving the
truth boundary**: a snapshot event that captures projected state at a
log position becomes the new replay base, and the prefix it
summarizes moves to a colder tier. Two primitives:

- **`compact(run)`** — emit a `runtime.snapshot` event, then archive
  the run's pre-snapshot prefix.
- **`retire(run)`** — archive an entire *closed, unpinned* run
  (typically: fork trials that were rejected and never promoted).

Nothing is ever deleted by the runtime. Deletion of archives is an
operator action outside the runtime's API, the same posture as
deleting a `.db` file today.

### The snapshot event

`runtime.snapshot`, emitted like any event (persisted, in the log,
auditable), with a payload of: the log position it covers (last
summarized event id + count), the id counters at snapshot time, a
**state hash**, and a reference to the snapshot blob. The blob itself
— the full projected state, JSON — lives in a sidecar `snapshots`
table keyed by hash, NOT inline in the event payload: events stay
small, and the hash in the event keeps the blob honest (a snapshot
that doesn't hash-match its event is corruption, fail-loud on load).

**What shipped (simplification over this draft).** The state hash is
computed over the canonical blob *bytes themselves* — objects and
relations only, sorted by id with sorted keys, **provenance
INCLUDED** (the snapshot must reconstruct state faithfully, audit
fields and all) — rather than a separately provenance-normalized
hash over objects/relations/patches. There is no `patches` key in
the blob: patch records from the archived prefix are not
reconstructed post-compaction (see §2 and the phase-1 boundaries),
so the snapshot captures projected object/relation state, not patch
history. Equally deterministic, and nothing that survives compaction
is left uncovered. `activegraph.store.retention._canonical_state_blob`
/ `state_hash_of` are authoritative.

Replay of a compacted run: load the snapshot blob, verify its hash
against the snapshot event, replay the post-snapshot suffix. The
projection is identical to full replay by construction, and
`verify_snapshot(run)` (replay the archived prefix, compare hashes)
is the audit tool that proves it on demand.

### The archive tier

Same store file, second table (`events_archive`, identical schema
plus an `archived_at`), or an operator-selected sidecar file for
true cold storage. `iter_events` reads the hot table only;
explicitly-audit-shaped APIs (`causal_chain`, archive-aware event
lookup) may reach the archive read-only. Moving rows hot → archive is
one transaction; the snapshot event is written before the move and
the move is idempotent, so a crash between the two leaves a
recoverable, never-wrong state.

## 2. What compaction must refuse (the pin set)

A run or prefix is **pinned** — `compact`/`retire` refuse it, loudly,
with the pin reason — when any of:

1. **Promoted-from** (input #1): the run id appears as `from_run` in
   any `promote.applied` marker of any non-retired run. The WHOLE
   fork log is pinned, not a prefix — provenance walks read arbitrary
   depths of it.
2. **Live lineage**: the run has children (`parent_run_id` points at
   it) that are not themselves retired. Forkability of recorded
   history is a contract (`fork()` copies from the parent's log);
   archiving a parent's prefix below a child's `forked_at_event_id`
   would orphan the child's provenance.
3. **Replay-cache dependence**: `llm.responded` / `tool.responded`
   events in the prefix are what `replay_llm_cache` / fork-with-cache
   serve from. Snapshotting DOES archive them (state is captured),
   which means **a fork at a pre-snapshot event of a compacted run is
   refused** with a structured error naming the archive and the
   dearchive path. Fork points at or after the snapshot work
   unchanged. This is the deliberate trade: compaction narrows
   *where you can branch history*, never *what state is*.
4. **Pending machinery**: unresolved approvals **and patches still in
   `proposed` status** (both are recorded state a state-snapshot
   cannot carry — the shipped `pins()` enforces each as a distinct
   pin reason), and un-promoted live forks' parents (case 2 covers
   this). The current run of an *attached* runtime is also refused,
   but not via `pins()`: it is enforced out-of-band by the per-run
   offline rule (no self-compaction under a live dispatcher — see
   CONTRACT v1.5 #2 addendum 2b) plus the `UNIQUE(id, run_id)`
   collision a live runtime would hit on the snapshot event.
   Compaction is an offline/idle-time operation.

Retention windows (e.g. "archive prefixes older than N events / days")
are policy sugar over `compact`; the pin set always dominates policy.

## 3. Contract interactions, stated honestly

- **`fork(at_event=...)`**: pre-snapshot fork points refuse (case 3
  above). The error names the snapshot event and the archived range.
- **`promote`**: base reconstruction replays the parent up to
  `forked_at_event_id`. If that point is pre-snapshot, promote of
  that fork refuses the same way — in practice forks are short-lived
  relative to compaction windows, and the pin set (case 2) prevents
  compacting under a live fork in the first place.
- **`replay_strict`**: verification of a compacted run covers the
  post-snapshot suffix; the archived prefix is verified by
  `verify_snapshot` instead (hash equality, not behavior re-firing).
  Documented as the compaction analogue of the v0.5 #7 posture.
- **`causal_chain` / provenance**: chains crossing the snapshot
  boundary either read the archive (default, read-only) or, if the
  archive was operator-moved offline, terminate with an explicit
  `[archived at runtime.snapshot evt_N]` marker — never silently.
- **Trace format**: `[runtime.snapshot]` gets a one-line rendering
  (events covered, state hash prefix); replayed compacted runs show
  the snapshot as the replay base.

## 4. Deliberately out of scope (v1 of compaction)

- Cross-store archival transport (move archives to S3/elsewhere) —
  operator tooling; the runtime defines the table, not the truck.
- Compaction of the *archive* (re-snapshotting) — meaningful only
  after the first tier exists.
- Automatic scheduling — hosts decide when; the runtime ships the
  primitives plus a CLI (`activegraph compact <url> --run-id ...`).
- Sub-run redaction (removing a single event's payload for privacy) —
  a different problem with different invariants (it breaks hashes by
  design); needs its own design if it's ever wanted.

## 5. Open questions for review

1. Snapshot blob format: JSON of the projection (simple, readable) vs
   a compressed columnar form (smaller). Position: JSON first;
   compression is a storage detail the sidecar table can grow.
2. Should `retire(run)` require the run to be lineage-terminal (no
   descendants at all), or is "all descendants also retired" enough?
   Position: the latter; retire whole abandoned subtrees in one call.
3. Does the evolution pack need an API to *enumerate* pins ("why
   can't I retire this run?") beyond the structured error? Position:
   yes, cheap: `store.pins(run_id)` returning the pin reasons — the
   same computation the refusal runs.
