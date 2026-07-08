"""Promote: apply a fork's net structural delta to its parent.

CONTRACT v1.3 #4 (design: ``promote-design.md``). The third piece of
the fork → test → promote loop: after a fork tested a change against
replayed history, the parent adopts the fork's *outcome* — not its
event history — as ordinary, auditable parent events.

Semantics locked by the 2026-07-08 design review:

  * **State application, not event replay.** The delta is a
    three-way comparison: base (the parent's state at the fork
    point), parent-now, fork-now. Fork-only changes promote;
    both-sides changes conflict; parent-only changes are left alone.
  * **Fail-closed, atomic, no semantic merge.** One conflict fails
    the whole promote before any mutation. Identical concurrent
    edits still conflict. The escape hatch is re-fork, not
    ``force=``.
  * **Referential integrity.** A promoted relation whose endpoints
    won't exist post-promote conflicts; so does a promoted object
    removal that would cascade away a parent-post-fork relation.
  * **Quiescent apply.** Delta events project and persist but never
    enqueue for behavior matching; the single reaction point is the
    ``promote.applied`` marker.
  * **Plan/apply staleness.** ``dry_run`` plans are advisory; apply
    always recomputes against parent-now. ``computed_against``
    records the parent tip event id the plan saw.

"State" here is type + data (+ endpoints for relations): version
counters and provenance are bookkeeping, not state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from activegraph.core.event import Event
from activegraph.core.graph import Graph, Object, Relation
from activegraph.core.ids import IDGen


@dataclass
class PromoteConflict:
    """One entity that blocks a promote.

    ``kind`` is a stable discriminator:

    - ``"both_changed"`` — the entity changed on both sides since the
      fork point (includes same-id both-created collisions,
      remove/modify pairs, and identical concurrent edits).
    - ``"dangling_relation"`` — a promoted relation's endpoint would
      not exist in parent-post-promote state.
    - ``"orphaning_removal"`` — a promoted object removal would
      cascade away a parent relation the delta doesn't remove.

    ``in_base`` / ``in_parent`` / ``in_fork`` are normalized state
    snapshots (``None`` where the entity doesn't exist on that side);
    ``detail`` is one human-readable sentence.
    """

    kind: str
    entity: str  # "object" | "relation"
    id: str
    detail: str
    in_base: Optional[dict[str, Any]] = None
    in_parent: Optional[dict[str, Any]] = None
    in_fork: Optional[dict[str, Any]] = None


@dataclass
class PromotePlan:
    """What a promote would apply, computed against ``computed_against``.

    Returned by ``runtime.promote(fork, dry_run=True)``. Advisory
    only: apply always recomputes against parent-now, so a plan that
    was clean when computed can conflict by apply time
    (``promote-design.md`` §3). ``is_promotable`` is the no-conflict
    check; ``warnings`` lists adjacent state promote deliberately
    does not move (fork-only pack loads / settings overrides —
    design §5).
    """

    from_run: str
    into_run: str
    forked_at_event: str
    computed_against: str  # parent tip event id at plan time
    object_creates: list[dict[str, Any]] = field(default_factory=list)
    object_patches: list[dict[str, Any]] = field(default_factory=list)
    object_removes: list[str] = field(default_factory=list)
    relation_creates: list[dict[str, Any]] = field(default_factory=list)
    relation_removes: list[str] = field(default_factory=list)
    conflicts: list[PromoteConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_promotable(self) -> bool:
        """True when nothing conflicts — the plan can apply as-is."""
        return not self.conflicts

    @property
    def is_empty(self) -> bool:
        """True when the fork's delta is empty (nothing to promote)."""
        return not (
            self.object_creates
            or self.object_patches
            or self.object_removes
            or self.relation_creates
            or self.relation_removes
        )


@dataclass
class PromoteResult:
    """A completed promote: the applied plan plus its audit anchors.

    ``marker_event_id`` is the ``promote.applied`` event; every
    applied delta event is ``caused_by`` it and listed in
    ``applied_event_ids`` in emission order.
    """

    plan: PromotePlan
    marker_event_id: str
    applied_event_ids: list[str] = field(default_factory=list)

    @property
    def computed_against(self) -> str:
        """The parent tip event id the applied plan was computed
        against (design §3, review amendment #3). Delegates to the
        plan; surfaced here so results record it directly."""
        return self.plan.computed_against


# ---- normalization ---------------------------------------------------------


def _object_state(o: Object) -> dict[str, Any]:
    """Comparable object state: type + data. No version, no provenance."""
    d = o.to_dict()
    return {"type": d["type"], "data": d["data"]}


def _relation_state(r: Relation) -> dict[str, Any]:
    """Comparable relation state: endpoints + type + data."""
    d = r.to_dict()
    return {
        "source": d["source"],
        "target": d["target"],
        "type": d["type"],
        "data": d.get("data", {}),
    }


def _snapshot(
    graph: Graph,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    objects = {o.id: _object_state(o) for o in graph.all_objects()}
    relations = {r.id: _relation_state(r) for r in graph.all_relations()}
    return objects, relations


def build_base_graph(parent: Graph, forked_at_event: str) -> Graph:
    """Reconstruct the parent's state at the fork point.

    Replays the parent's log (projection-only, the ``fork()``
    mechanics — no persistence, no listeners, no behaviors) up to and
    including ``forked_at_event``. Raises ``LookupError`` when the id
    isn't in the parent's log — callers translate to the structured
    lineage error.
    """
    base = Graph(ids=IDGen(), run_id=f"__promote_base__{parent.run_id}")
    found = False
    for ev in parent.events:
        base._replay_event(ev)  # noqa: SLF001 — same seam fork() uses
        if ev.id == forked_at_event:
            found = True
            break
    if not found:
        raise LookupError(
            f"fork point {forked_at_event!r} not found in run "
            f"{parent.run_id!r}"
        )
    return base


# ---- plan computation ------------------------------------------------------


def compute_promote_plan(
    parent: Graph,
    fork: Graph,
    *,
    forked_at_event: str,
    warnings: Optional[list[str]] = None,
) -> PromotePlan:
    """Three-way structural comparison producing the fork's promotable
    delta and every conflict, per ``promote-design.md`` §4.

    Pure: reads both graphs, mutates nothing. Deterministic: sorted
    by entity id throughout (the ``compute_diff`` convention).
    """
    base = build_base_graph(parent, forked_at_event)
    base_obj, base_rel = _snapshot(base)
    parent_obj, parent_rel = _snapshot(parent)
    fork_obj, fork_rel = _snapshot(fork)

    plan = PromotePlan(
        from_run=fork.run_id,
        into_run=parent.run_id,
        forked_at_event=forked_at_event,
        computed_against=parent.events[-1].id if parent.events else "",
        warnings=list(warnings or []),
    )

    def classify(
        entity: str,
        ids: set[str],
        base_s: dict[str, dict[str, Any]],
        parent_s: dict[str, dict[str, Any]],
        fork_s: dict[str, dict[str, Any]],
    ) -> tuple[list[str], list[str], list[str]]:
        """Split ids into (creates, patches, removes) of the fork-only
        delta, appending conflicts to the plan as found."""
        creates: list[str] = []
        patches: list[str] = []
        removes: list[str] = []
        for id_ in sorted(ids):
            b, p, f = base_s.get(id_), parent_s.get(id_), fork_s.get(id_)
            fork_changed = f != b
            parent_changed = p != b
            if not fork_changed:
                continue  # parent-only change or no change: not ours
            if parent_changed:
                if b is None:
                    detail = (
                        f"{id_} was created independently on both sides "
                        f"after the fork point (id collision from the "
                        f"reseeded generators)"
                    )
                elif p is None and f is None:
                    # Both removed it. Still both-changed; fail closed.
                    detail = f"{id_} was removed on both sides"
                elif p == f:
                    detail = (
                        f"{id_} was changed identically on both sides — "
                        f"still a conflict in v1 (no semantic judgment)"
                    )
                else:
                    detail = f"{id_} changed on both sides since the fork point"
                plan.conflicts.append(
                    PromoteConflict(
                        kind="both_changed",
                        entity=entity,
                        id=id_,
                        detail=detail,
                        in_base=b,
                        in_parent=p,
                        in_fork=f,
                    )
                )
                continue
            if f is None:
                removes.append(id_)
            elif b is None:
                creates.append(id_)
            else:
                patches.append(id_)
        return creates, patches, removes

    all_obj_ids = set(base_obj) | set(parent_obj) | set(fork_obj)
    all_rel_ids = set(base_rel) | set(parent_rel) | set(fork_rel)
    obj_creates, obj_patches, obj_removes = classify(
        "object", all_obj_ids, base_obj, parent_obj, fork_obj
    )
    rel_creates, rel_patches, rel_removes = classify(
        "relation", all_rel_ids, base_rel, parent_rel, fork_rel
    )
    # A relation whose endpoints/type/data changed is remove+create in
    # apply terms; the projection has no relation-patch event. Fold
    # patches into both lists, keeping determinism.
    rel_removes = sorted(set(rel_removes) | set(rel_patches))
    rel_creates = sorted(set(rel_creates) | set(rel_patches))

    # ---- referential integrity (design §4, review amendment #2) ----

    removed_objects = set(obj_removes)
    created_objects = set(obj_creates)
    delta_removed_relations = set(rel_removes)

    def endpoint_survives(oid: str) -> bool:
        if oid in created_objects:
            return True
        return oid in parent_obj and oid not in removed_objects

    for rid in list(rel_creates):
        state = fork_rel[rid]
        missing = [
            ep for ep in (state["source"], state["target"])
            if not endpoint_survives(ep)
        ]
        if missing:
            plan.conflicts.append(
                PromoteConflict(
                    kind="dangling_relation",
                    entity="relation",
                    id=rid,
                    detail=(
                        f"{rid} ({state['source']} --{state['type']}--> "
                        f"{state['target']}) references "
                        f"{', '.join(sorted(missing))}, which would not "
                        f"exist in the parent after this promote"
                    ),
                    in_fork=state,
                )
            )

    for oid in obj_removes:
        orphaned = sorted(
            rid
            for rid, state in parent_rel.items()
            if oid in (state["source"], state["target"])
            and rid not in delta_removed_relations
        )
        if orphaned:
            plan.conflicts.append(
                PromoteConflict(
                    kind="orphaning_removal",
                    entity="object",
                    id=oid,
                    detail=(
                        f"removing {oid} would cascade away parent "
                        f"relation(s) {', '.join(orphaned)} that the fork "
                        f"never touched"
                    ),
                    in_base=base_obj.get(oid),
                    in_parent=parent_obj.get(oid),
                )
            )

    # ---- materialize the delta payloads (sorted, deterministic) ----

    plan.object_creates = [
        {"id": oid, **fork_obj[oid]} for oid in obj_creates
    ]
    plan.object_patches = [
        {"id": oid, **fork_obj[oid]} for oid in obj_patches
    ]
    plan.object_removes = obj_removes
    plan.relation_creates = [
        {"id": rid, **fork_rel[rid]} for rid in rel_creates
    ]
    plan.relation_removes = rel_removes
    plan.conflicts.sort(key=lambda c: (c.kind, c.entity, c.id))
    return plan


def promote_warnings(
    parent_rt: Any, fork_rt: Any, *, forked_at_event: str
) -> list[str]:
    """Adjacent state promote surfaces but never applies (design §5).

    Currently: packs loaded in the fork but not the parent, and
    fork-tail ``pack.settings_overridden`` events (``fork --set``).
    Fork-only-ness is positional — everything after ``forked_at_event``
    in the fork's log is the fork's own — never an event-id membership
    check: ids are scoped to a run (CONTRACT #12), so the same id can
    name different events on the two sides.
    """
    warnings: list[str] = []
    parent_packs = {
        (getattr(p, "name", "?"), getattr(p, "version", "?"))
        for p in parent_rt.loaded_packs()
    }
    for p in fork_rt.loaded_packs():
        key = (getattr(p, "name", "?"), getattr(p, "version", "?"))
        if key not in parent_packs:
            warnings.append(
                f"fork has pack {key[0]}@{key[1]} loaded that the parent "
                f"does not; promote never adopts code — call "
                f"parent.load_pack(...) explicitly if the promoted state "
                f"depends on it"
            )
    in_tail = False
    for e in fork_rt.graph.events:
        if not in_tail:
            if e.id == forked_at_event:
                in_tail = True
            continue
        if e.type != "pack.settings_overridden":
            continue
        payload = e.payload or {}
        assignments = payload.get("assignments") or []
        rendered = (
            ", ".join(str(a) for a in assignments)
            if assignments
            else str(payload.get("overrides", "?"))
        )
        warnings.append(
            f"fork carries a settings override for pack "
            f"{payload.get('pack', '?')} ({rendered}) that promote does "
            f"not transfer; re-apply it on the parent explicitly if the "
            f"promoted state depends on it"
        )
    return warnings
