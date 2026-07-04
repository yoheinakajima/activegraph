"""Structural diff between two runs (typically parent vs fork).

CONTRACT v0.5 #10: diff is structural only — divergent objects, divergent
relations, and which event ranges belong to each side. Semantic comparison
(e.g., "do these two claims express the same idea?") is a behavior's job,
not the runtime's.

Diff is computed by walking both graphs' final state and event logs. We
ignore lifecycle events (`behavior.*`, `runtime.*`) when computing the
event partition — they're scaffolding, not history.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional, cast

from activegraph.core.event import Event
from activegraph.core.graph import Graph, Object, Relation


_LIFECYCLE_PREFIXES = ("behavior.", "relation_behavior.", "runtime.")


def _is_lifecycle(e: Event) -> bool:
    return any(e.type.startswith(p) for p in _LIFECYCLE_PREFIXES)


@dataclass
class DivergentObject:
    """One object id whose final state differs between parent and fork.

    ``in_parent`` / ``in_fork`` are provenance-stripped ``to_dict``
    snapshots so the comparison is structural, not timestamp noise;
    ``None`` on the side where the id doesn't exist. ``summary()``
    renders the one-line form the CLI diff output prints.
    """

    id: str
    in_parent: Optional[dict[str, Any]]  # to_dict snapshot or None
    in_fork: Optional[dict[str, Any]]

    def summary(self) -> str:
        if self.in_parent is None:
            return f"{self.id} only in fork"
        if self.in_fork is None:
            return f"{self.id} only in parent"
        return f"{self.id} differs (parent v{self.in_parent.get('version')} ↔ fork v{self.in_fork.get('version')})"


@dataclass
class DivergentRelation:
    """One relation id whose final state differs between parent and fork.

    Same shape as :class:`DivergentObject`: provenance-stripped
    snapshots per side, ``None`` where the id doesn't exist, and a
    ``summary()`` that names the endpoints and relation type for
    only-in-one-side cases.
    """

    id: str
    in_parent: Optional[dict[str, Any]]
    in_fork: Optional[dict[str, Any]]

    def summary(self) -> str:
        if self.in_parent is None:
            # A divergent relation always has at least one side; cast is a
            # type-level assertion only, no runtime check.
            r = cast("dict[str, Any]", self.in_fork)
            return f"{self.id} only in fork ({r['source']} --{r['type']}--> {r['target']})"
        if self.in_fork is None:
            r = self.in_parent
            return f"{self.id} only in parent ({r['source']} --{r['type']}--> {r['target']})"
        return f"{self.id} differs"


@dataclass
class Diff:
    """Structural comparison of two runs (typically parent vs fork).

    CONTRACT v0.5 #10: structural only. Non-lifecycle events split
    into the shared prefix and each side's tail;
    ``divergent_objects`` / ``divergent_relations`` list per-id final
    states that differ; ``is_identical`` is the no-divergence check.
    Semantic comparison ("do these two claims say the same thing?")
    is a behavior's job, not the runtime's.
    """

    parent_run_id: str
    fork_run_id: str
    shared_events: list[Event] = field(default_factory=list)
    parent_only_events: list[Event] = field(default_factory=list)
    fork_only_events: list[Event] = field(default_factory=list)
    divergent_objects: list[DivergentObject] = field(default_factory=list)
    divergent_relations: list[DivergentRelation] = field(default_factory=list)

    @property
    def is_identical(self) -> bool:
        return (
            not self.parent_only_events
            and not self.fork_only_events
            and not self.divergent_objects
            and not self.divergent_relations
        )


def compute_diff(parent: Graph, fork: Graph, parent_run_id: str, fork_run_id: str) -> Diff:
    parent_events = [e for e in parent.events if not _is_lifecycle(e)]
    fork_events = [e for e in fork.events if not _is_lifecycle(e)]

    # Shared prefix: events that match by id, type AND payload. Same id with
    # different content means the fork already diverged (logical ids are
    # scoped to run_id — CONTRACT #12 — so collisions after the fork point
    # are expected and must not be flattened into "shared".)
    shared: list[Event] = []
    i = 0
    while i < len(parent_events) and i < len(fork_events):
        a, b = parent_events[i], fork_events[i]
        if a.id == b.id and a.type == b.type and a.payload == b.payload:
            shared.append(a)
            i += 1
        else:
            break
    parent_only = parent_events[i:]
    fork_only = fork_events[i:]

    # Object divergence.
    obj_diffs: list[DivergentObject] = []
    parent_objs = {o.id: o for o in parent.all_objects()}
    fork_objs = {o.id: o for o in fork.all_objects()}
    for oid in sorted(set(parent_objs) | set(fork_objs)):
        po = parent_objs.get(oid)
        fo = fork_objs.get(oid)
        pd = _normalize_object(po) if po else None
        fd = _normalize_object(fo) if fo else None
        if pd != fd:
            obj_diffs.append(DivergentObject(id=oid, in_parent=pd, in_fork=fd))

    # Relation divergence.
    rel_diffs: list[DivergentRelation] = []
    parent_rels = {r.id: r for r in parent.all_relations()}
    fork_rels = {r.id: r for r in fork.all_relations()}
    for rid in sorted(set(parent_rels) | set(fork_rels)):
        pr = parent_rels.get(rid)
        fr = fork_rels.get(rid)
        pd = _normalize_relation(pr) if pr else None
        fd = _normalize_relation(fr) if fr else None
        if pd != fd:
            rel_diffs.append(DivergentRelation(id=rid, in_parent=pd, in_fork=fd))

    return Diff(
        parent_run_id=parent_run_id,
        fork_run_id=fork_run_id,
        shared_events=shared,
        parent_only_events=parent_only,
        fork_only_events=fork_only,
        divergent_objects=obj_diffs,
        divergent_relations=rel_diffs,
    )


def _normalize_object(o: Object) -> dict[str, Any]:
    """Strip provenance (timestamps, run_id) so equality is structural."""
    d: dict[str, Any] = o.to_dict()
    d.pop("provenance", None)
    return d


def _normalize_relation(r: Relation) -> dict[str, Any]:
    d: dict[str, Any] = r.to_dict()
    d.pop("provenance", None)
    return d
