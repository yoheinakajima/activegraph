# Promote: closing the fork → test → promote loop

**Status: REVIEWED AND LOCKED (CONTRACT v1.3 #4).**
Downstream review (2026-07-08) approved the core decisions as
written — state application over event replay, three-way base
reconstruction, entity-granular fail-closed conflicts with
identical-concurrent-edits conflicting in v1, re-fork as the escape
hatch, §5's exclusions — and required three amendments, all folded
in below: quiescent apply semantics (§6), the referential-integrity
conflict rule (§4), and the plan/apply staleness rule (§3). The §8
open questions are answered inline.

## 1. Why the runtime needs this

The runtime already ships two-thirds of a self-modification loop:

- `rt.fork(at_event=...)` — branch a run at an event into an
  isolated child with the parent's history, cache-served
  (CONTRACT v0.5 #9 / v0.8 #5).
- `rt.diff(fork)` — structural comparison: event partition plus
  divergent objects/relations (CONTRACT v0.5 #10).

What's missing is the third step: after a fork has hot-loaded a
candidate change, run against replayed history, and produced results
worth keeping — how does the parent **adopt** them? Today the answer
is "re-do the work by hand on the parent," which discards the
isolation guarantee exactly when it proved its worth.

`promote` is domain-neutral runtime physics: "apply this fork's net
structural delta to its parent, auditable, atomically, or not at
all." What a product layers on top (approval gates, test
thresholds, rollback policy) stays out of the runtime.

## 2. The core decision: promote state, not events

Two candidate semantics were considered:

**(a) Event replay** — copy the fork's fork-only events onto the
parent's log, remapping ids.
**(b) State application (chosen)** — compute the fork's net
structural delta and apply it to the parent as ordinary,
newly-emitted parent events.

Event replay was rejected for v1 on honesty grounds. The fork's tail
contains `llm.requested/responded` and `tool.requested/responded`
pairs the **parent never executed**; copying them into the parent's
log would fabricate history the parent doesn't own, and every replay
and audit primitive (strict replay, causal chains, cache
reconstruction) trusts the log to record what actually happened to
*this* run. It also requires remapping every fork-generated id
(events, objects, relations) around collisions with ids the parent
minted after the fork point — mechanical, but a large surface of
subtle bugs.

State application keeps both logs honest: the fork's full history
stays queryable under its own `run_id` in the same store (nothing is
moved or rewritten), and the parent's log records what *actually*
happened to the parent — "at this point I adopted the following
delta from fork `run_b`, which forked from me at `evt_042`." That IS
the true history. The delta events are ordinary `object.created` /
`patch.applied` / `object.removed` / `relation.*` events, so replay,
projection, GraphStore backends, and every existing behavior trigger
work on promoted state with zero special cases.

Event-level promote (with id remapping) remains a possible future
amendment if the evolution pack finds state-level provenance too
coarse; nothing in this design forecloses it. See §8.

## 3. API surface

```python
plan = parent_rt.promote(fork_rt, dry_run=True)   # inspect, no mutation
result = parent_rt.promote(fork_rt)               # apply atomically
```

- `promote(fork, *, dry_run=False) -> PromotePlan | PromoteResult`
  on `Runtime`. The receiver is the **destination** (parent), the
  argument the **source** (fork) — same orientation as `rt.diff(fork)`.
- `PromotePlan` (dry run): `creates` / `patches` / `removes` for
  objects and relations (provenance-stripped snapshots, the
  `Diff` convention), `conflicts` (empty on a promotable plan),
  `warnings` (see §5: pack-load and settings differences surfaced
  but not applied), and `is_promotable`.
- `PromoteResult`: everything in the plan, plus the applied event
  ids and the `promote.applied` marker event id.
- **Plan/apply staleness (review amendment #3).** `promote()` always
  recomputes the plan against **parent-now at apply time**; a
  `PromotePlan` from an earlier `dry_run=True` call is advisory and
  is never handed back in for application. If the parent advanced
  between dry-run and apply, the apply-time recomputation sees the
  new parent state — new conflicts fail the promote exactly as if
  the dry run had never happened. `PromotePlan` and `PromoteResult`
  both record `computed_against`: the parent's tip event id at the
  moment the plan was computed, so callers (and audits) can detect
  that a dry-run plan went stale before apply.
- CLI: `activegraph promote <url> --from-run <fork-run> [--dry-run]`
  — SHOULD, same release if small; the Python API is the MUST.

Preconditions, all fail-loud with the house structured errors:

1. Same SQLite store for both runtimes (`IncompatibleRuntimeState`,
   the `fork()` precedent).
2. `fork_rt`'s run is a **direct fork** of `parent_rt`'s run — the
   store's `runs` table already records `parent_run_id` and
   `forked_at_event_id` per fork, so lineage is verified from
   durable state, not caller claims (`PromoteLineageError`, new).
   Promoting a grandchild directly to a grandparent is out of scope
   for v1: promote up one level at a time.
3. No conflicts under §4's rules (`PromoteConflictError`, new,
   carrying the full conflict list).

## 4. Conflict semantics (v1: structural, fail-closed, no merge)

Promote is a three-way comparison. The **base** is the parent's
state at the fork point — reconstructed by replaying the parent's
log up to and including `forked_at_event_id` into a scratch
projection (cheap: pure graph mutations, no LLM/tool re-execution;
same mechanics `fork()` already uses). The two tips are
**parent-now** and **fork-now**. All comparisons use the
provenance-stripped normalization `compute_diff` already applies, so
timestamps and run-ids don't manufacture conflicts.

Per entity (object or relation id, both sides' unions):

| vs base: fork | vs base: parent | outcome |
| --- | --- | --- |
| unchanged | unchanged | not in delta |
| changed/created/removed | unchanged | **promoted** |
| unchanged | changed/created/removed | left alone (parent's own work) |
| changed/created/removed | changed/created/removed | **conflict** |

Rules:

- "Changed" includes creation and removal; a fork-modified /
  parent-removed pair is a conflict, as is fork-removed /
  parent-modified, and same-id both-created (id collision from the
  reseeded generators — possible and expected, CONTRACT #12 scopes
  logical ids to a run).
- Identical concurrent edits (both sides changed an entity to the
  same normalized state) are **still conflicts in v1**. Detecting
  equal outcomes is easy; deciding they're safe is semantics, and v1
  does no semantics. Fail-closed keeps the invariant simple: a
  conflict means *a human or a higher layer decides*.
- Field-level merging within one object: out of scope. The unit of
  conflict is the entity, matching `Diff`'s granularity.
- **Referential integrity (review amendment #2).** A promoted
  relation whose endpoint objects would not exist in
  parent-post-promote state — endpoints neither surviving in
  parent-now (present and not delta-removed) nor created by the
  delta — is a conflict. This catches the fork relating to a parent
  object the parent has since removed. The rule is applied in both
  directions: a promoted **object removal** that would orphan a
  parent-now relation not itself removed by the delta is also a
  conflict, because the projection cascades `object.removed` into
  removal of touching relations — promoting such a removal would
  silently delete the parent's own post-fork work, the exact
  mutation class fail-closed exists to prevent. (Relation removals
  the fork performed via its own cascade arrive in the delta as
  ordinary relation removals and promote cleanly.)
- "State" for comparison purposes is type + data (+ endpoints for
  relations). Version counters and provenance are bookkeeping, not
  state: a fork that patched a field twice back to the same value
  differs from base in version only and contributes nothing to the
  delta.
- **Atomicity**: conflicts are computed on the full plan before any
  mutation; one conflict fails the whole promote, applying nothing —
  the `load_pack` pre-mutation precedent. There is no partial
  promote.
- Determinism: same store contents → same plan, same conflict list,
  same ordering (sorted by entity id, the `compute_diff` convention).

The escape hatch for a conflicted promote is not a `force=` flag
(easy to reach for, impossible to audit) but the loop itself:
re-fork from the parent's current tip, re-apply the candidate change
there (caches make this cheap), and promote the fresh fork. That
re-fork replays the change against the parent's *actual* present,
which is what a merge would have had to pretend to do.

## 5. What promote deliberately does not move

Surfaced in `PromotePlan.warnings` where relevant; never applied:

- **Pack loads.** Packs are code; the fork hot-loading
  `candidate_pack` does not entitle the parent to import it
  silently. The plan warns when the fork has packs (or pack
  settings overrides, e.g. from `fork --set`) the parent lacks —
  adopting those is an explicit `parent_rt.load_pack(...)` by the
  caller. This is also the governance seam: a product gates *that*
  call behind approval.
- **LLM/tool cache entries.** Caches are per-run recorded history;
  the parent didn't make those calls. (The fork's entries stay
  reachable for future forks *of the fork*, unchanged.)
- **Lifecycle events** (`behavior.*`, `runtime.*`, replay markers) —
  scaffolding, excluded exactly as `compute_diff` excludes them.
- **Budget state, pending approvals, frames** — runtime-local
  posture, not graph state.

## 6. Audit trail and apply semantics

An applied promote emits, on the parent, in order:

1. `promote.applied` — one marker event:
   `{from_run, forked_at_event, computed_against, objects_created,
   objects_patched, objects_removed, relations_created,
   relations_removed, warnings}` (id lists), `actor="runtime"`.
2. The delta as ordinary mutation events, `actor="promote:<fork_run_id>"`,
   each `caused_by` the marker event — so `trace.causal_chain()`
   walks any promoted object back to the promote, and the trace
   shows the adoption as one visually grouped block. Object patches
   use `op="replace"` with the fork's full data, so post-promote
   state is byte-equal to fork state (merge-style `update` could not
   express a removed field).

**Quiescent apply (review amendment #1).** The delta events are
applied projection-only — the same posture as replay: they append to
the log, project onto graph state, and persist, but they are **not**
enqueued for behavior matching, neither live at apply time nor
retroactively (load-time requeue recovery skips
`actor="promote:*"` events). Re-firing triggers per delta event
would duplicate side effects the fork already processed when it ran
those mutations live, and any behavior-emitted events would
interleave new history into the middle of the atomic block. The one
reaction point is the `promote.applied` marker itself: it is
queue-visible (like `pack.loaded`, unlike `approval.*`), so a
behavior subscribed to `promote.applied` fires once, after the full
delta is in place, seeing post-promote state.

Replaying the parent's log reproduces promoted state with no special
cases, because promoted state *is* ordinary events. `trace.lines()`
gets a `[promote.applied]` rendering (one line, counts + source run).

## 7. Tests that must exist before this ships

All offline/deterministic: clean promote (creates/patches/removes,
each kind); dry-run mutates nothing; conflict on each row of the §4
table including both-created id collision and remove/modify pairs;
atomicity on a mixed clean+conflicted plan; lineage rejection
(unrelated run, grandchild, cross-store); warnings for fork-only
pack loads and `--set` overrides; causal chain from promoted object
to marker event; replay of a log containing a promote; fork-of-fork
promoted up one level then the other; trace snapshot of the promoted
block; `Diff` of parent vs fork being `is_identical`-equivalent for
promoted entities after promote.

Added with the review amendments: a behavior registered on
`object.created` does **not** fire during apply while one registered
on `promote.applied` fires once with post-promote state visible; the
dangling-relation conflict (fork relates to a parent object the
parent has since removed) and its reverse (promoted removal
orphaning a parent-post-fork relation); and a staleness case where
the parent advances between `dry_run=True` and apply so the
recomputed plan conflicts and `computed_against` differs between
plan and attempted apply.

## 8. Open questions — RESOLVED by the 2026-07-08 review

1. **Granularity of provenance: fast-follow, not v1.** The marker
   plus the fork's intact log gives two-hop auditability (promoted
   entity → marker → fork run → fork's own event history), and that
   is sufficient. Per-entity fork-event provenance is additive to
   the marker payload when the evolution pack asks for it.
2. **Approval integration: caller-side, confirmed.** Downstream
   wraps `promote` in a gated capability; the runtime stays
   domain-neutral.
3. **Selective promote: deferred, on correctness grounds.** The fork
   was tested as a whole; a partial promote is an untested state.
   This is stronger than the simplicity argument — cherry-picking is
   fork-again-and-prune, which re-tests.
4. **Postgres: SQLite-only v1 confirmed.** Follows `fork()`: same
   structured error, same migration pointer.

## 9. Relationship to the incoming pack-manifest spec

The downstream manifest formalization will land a spec draft for
review; loader-side validation belongs in this repo
(`packs/loader.py`). That work is independent of promote — promote
treats pack loads as warnings, not payload (§5) — but the two meet
at the evolution pack: a manifest gives promote's pack-load warnings
a stable identity (`name`, `version`, content hash) to report
against. The loader review will be handled as its own contract item
when the draft arrives; nothing here blocks on it.
