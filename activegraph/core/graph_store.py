"""GraphStore: pluggable backend for the materialized graph state.

CONTRACT context: graph state (objects / relations / patches) is a
*projection* of the event log (CONTRACT #2). The GraphStore is where that
projection lives. The projector ``apply_event`` is still the only writer;
as of v1.2 it writes *through* a GraphStore instead of mutating dicts
directly. This makes the current-state view pluggable: keep it in process
memory (:class:`InMemoryGraphStore`, the default — byte-for-byte identical
to the pre-v1.2 behavior) or push it into an external graph database
(``FalkorDBGraphStore`` in ``activegraph.store.falkordb``).

A GraphStore is NOT an EventStore. The :class:`~activegraph.store.base.EventStore`
is the durable, append-only log — the source of truth. The GraphStore is
the queryable current-state view rebuilt by replaying that log. Losing a
GraphStore is recoverable (replay the log); losing the EventStore is not.

The interface is deliberately small: upsert/get/remove/enumerate for each
of the three entity kinds, plus ``clear`` and ``close``. On top of that it
exposes a few **optional query hooks** — :meth:`GraphStore.find_objects`,
:meth:`GraphStore.find_objects_in_types`, :meth:`GraphStore.find_relations`,
:meth:`GraphStore.neighborhood`, and :meth:`GraphStore.match_chain` — which
default to a plain Python scan over ``all_objects`` / ``all_relations`` (so the
base class itself is the single source of truth for their semantics) but which
a backend MAY override to push the filter/traversal down to the database. The
rich ``where`` predicate language is *not* a hook:
it stays in :class:`~activegraph.core.graph.Graph`, evaluated in Python over
whatever :meth:`find_objects` returns. That keeps the structural filters
(which are trivial to mirror exactly) pushable, while the parts that are
hard to translate faithfully stay in one place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from activegraph.core.graph import Object, Relation
    from activegraph.core.patch import Patch


@dataclass(frozen=True)
class ChainMatch:
    """One structural match of a linear node→rel→node chain.

    ``objects[i]`` is the object bound to node position ``i`` (length ==
    number of node positions). ``relations[j]`` is the relation traversed
    for hop ``j`` (length == ``len(objects) - 1``). Produced by
    :meth:`GraphStore.match_chain`; the pattern matcher layers node
    ``{prop: value}`` equality, variable-consistency, and ``WHERE`` on top.
    """

    objects: list["Object"]
    relations: list["Relation"]


class GraphStore(ABC):
    """Backend for the materialized graph projection.

    Implementations store objects, relations, and patches keyed by id.
    ``put_*`` is an upsert (insert or overwrite the entity with the same
    id). ``get_*`` returns ``None`` when the id is unknown. ``remove_*``
    is a no-op when the id is unknown (the projector already guards
    against double-removal, but tolerating it keeps backends simple).
    """

    # ---- objects ----

    @abstractmethod
    def put_object(self, obj: "Object") -> None:
        """Insert or overwrite ``obj`` keyed by ``obj.id``."""

    @abstractmethod
    def get_object(self, object_id: str) -> Optional["Object"]:
        """Return the object with ``object_id`` or ``None``."""

    @abstractmethod
    def remove_object(self, object_id: str) -> None:
        """Drop the object with ``object_id`` if present."""

    @abstractmethod
    def all_objects(self) -> list["Object"]:
        """Return every object. Order is unspecified."""

    # ---- relations ----

    @abstractmethod
    def put_relation(self, rel: "Relation") -> None:
        """Insert or overwrite ``rel`` keyed by ``rel.id``."""

    @abstractmethod
    def get_relation(self, relation_id: str) -> Optional["Relation"]:
        """Return the relation with ``relation_id`` or ``None``."""

    @abstractmethod
    def remove_relation(self, relation_id: str) -> None:
        """Drop the relation with ``relation_id`` if present."""

    @abstractmethod
    def all_relations(self) -> list["Relation"]:
        """Return every relation. Order is unspecified."""

    # ---- patches ----

    @abstractmethod
    def put_patch(self, patch: "Patch") -> None:
        """Insert or overwrite ``patch`` keyed by ``patch.id``."""

    @abstractmethod
    def get_patch(self, patch_id: str) -> Optional["Patch"]:
        """Return the patch with ``patch_id`` or ``None``."""

    @abstractmethod
    def all_patches(self) -> list["Patch"]:
        """Return every patch. Order is unspecified."""

    # ---- query hooks (optional pushdown) ----
    #
    # These have working Python defaults computed over all_objects /
    # all_relations, so the base class is the canonical definition of their
    # semantics. A backend MAY override them to push the work into the
    # database; an override MUST return exactly what the default would (the
    # GraphStoreConformance suite pins this). They use only Object / Relation
    # attributes — never the ``where`` evaluator — so backends stay decoupled
    # from query-language details and there is no import cycle with core.graph.

    def find_objects(self, type: Optional[str] = None) -> list["Object"]:
        """Return objects, optionally filtered to those whose ``type`` equals
        ``type``. ``None`` means "every object". Type-equality only — the
        ``where`` predicate is applied by the caller, not here.
        """
        if type is None:
            return self.all_objects()
        return [o for o in self.all_objects() if o.type == type]

    def find_objects_in_types(self, types: list[str]) -> list["Object"]:
        """Return objects whose ``type`` is any of ``types`` (OR), preserving
        :meth:`all_objects` enumeration order. An empty ``types`` returns
        ``[]``. Like :meth:`find_objects` this is type-equality only — the
        ``where`` predicate stays with the caller. The default scans once;
        a backend MAY push a ``type IN [...]`` filter into the database, but
        MUST preserve the single-pass order so it matches this base class.
        """
        if not types:
            return []
        type_set = set(types)
        return [o for o in self.all_objects() if o.type in type_set]

    def find_relations(
        self,
        source: Optional[str] = None,
        target: Optional[str] = None,
        type: Optional[str] = None,
    ) -> list["Relation"]:
        """Return relations matching every provided filter (AND). A ``None``
        filter is unconstrained; passing none returns every relation.
        """
        out: list["Relation"] = []
        for r in self.all_relations():
            if source is not None and r.source != source:
                continue
            if target is not None and r.target != target:
                continue
            if type is not None and r.type != type:
                continue
            out.append(r)
        return out

    def neighborhood(
        self, object_id: str, depth: int = 1
    ) -> tuple[list["Object"], list["Relation"]]:
        """Undirected breadth-first walk from ``object_id`` out to ``depth``
        edges. Returns ``(objects, relations)`` where ``objects`` are the
        reachable materialized objects (the start included; endpoints that
        are not objects are skipped) and ``relations`` are every relation
        traversed. Returns ``([], [])`` if ``object_id`` is not an object.
        """
        if self.get_object(object_id) is None:
            return ([], [])
        seen_objs = {object_id}
        frontier = {object_id}
        seen_rels: set[str] = set()
        for _ in range(depth):
            next_frontier: set[str] = set()
            for r in self.all_relations():
                if r.source in frontier or r.target in frontier:
                    seen_rels.add(r.id)
                    if r.source not in seen_objs:
                        next_frontier.add(r.source)
                    if r.target not in seen_objs:
                        next_frontier.add(r.target)
            seen_objs |= next_frontier
            frontier = next_frontier
            if not frontier:
                break
        objs = [self.get_object(i) for i in seen_objs]
        rels = [self.get_relation(i) for i in seen_rels]
        return (
            [o for o in objs if o is not None],
            [r for r in rels if r is not None],
        )

    def match_chain(
        self,
        node_types: list[Optional[str]],
        rels: list[tuple[Optional[str], str]],
    ) -> list["ChainMatch"]:
        """Enumerate every structural match of a linear node\u2192rel\u2192node chain.

        ``node_types`` is one entry per node position (``None`` = any type);
        ``rels`` is one ``(rel_type, direction)`` per hop, where ``direction``
        is ``"right"`` (``(a)-[]->(b)``) or ``"left"`` (``(a)<-[]-(b)``) and
        ``rel_type`` ``None`` means any. ``len(rels)`` must be
        ``len(node_types) - 1``.

        Only the *structural* filters \u2014 node types, relation types, and
        directions \u2014 are applied here; node ``{prop: value}`` equality and
        ``WHERE`` are layered on by the caller. Semantics are **homomorphic**:
        a single object or relation may fill more than one position (matching
        the pattern matcher and FalkorDB, neither of which enforces
        node/relationship uniqueness). The default walks the chain
        depth-first via :meth:`find_objects` / :meth:`find_relations`; a
        backend MAY override to resolve the whole chain in one query, but MUST
        return exactly what this default would (order aside).
        """
        if not node_types:
            return []
        results: list[ChainMatch] = []
        for seed in self.find_objects(node_types[0]):
            self._extend_chain_match(node_types, rels, [seed], [], results)
        return results

    def _extend_chain_match(
        self,
        node_types: list[Optional[str]],
        rels: list[tuple[Optional[str], str]],
        objs: list["Object"],
        rel_chain: list["Relation"],
        results: list["ChainMatch"],
    ) -> None:
        i = len(objs) - 1
        if i == len(rels):
            results.append(ChainMatch(objects=list(objs), relations=list(rel_chain)))
            return
        rel_type, direction = rels[i]
        next_type = node_types[i + 1]
        src = objs[-1]
        if direction == "right":
            for r in self.find_relations(source=src.id, type=rel_type):
                neighbor = self.get_object(r.target)
                if neighbor is None:
                    continue
                if next_type is not None and neighbor.type != next_type:
                    continue
                self._extend_chain_match(
                    node_types, rels, objs + [neighbor], rel_chain + [r], results
                )
        else:  # "left" \u2014 (src)<-[:r]-(neighbor) means r.target == src.id
            for r in self.find_relations(target=src.id, type=rel_type):
                neighbor = self.get_object(r.source)
                if neighbor is None:
                    continue
                if next_type is not None and neighbor.type != next_type:
                    continue
                self._extend_chain_match(
                    node_types, rels, objs + [neighbor], rel_chain + [r], results
                )

    # ---- lifecycle ----

    def clear(self) -> None:
        """Drop all objects, relations, and patches. Default: per-kind removal."""
        for o in self.all_objects():
            self.remove_object(o.id)
        for r in self.all_relations():
            self.remove_relation(r.id)
        for p in self.all_patches():
            self.remove_patch(p.id)

    def remove_patch(self, patch_id: str) -> None:
        """Drop the patch with ``patch_id`` if present.

        Not part of the projector's hot path (patches are never deleted by
        events — only superseded), but :meth:`clear` needs it. Subclasses
        that store patches must override.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release any backend resources. Default: no-op."""


class InMemoryGraphStore(GraphStore):
    """Volatile, dict-backed GraphStore. The default backend.

    Stores the live :class:`~activegraph.core.graph.Object` /
    :class:`~activegraph.core.graph.Relation` / :class:`~activegraph.core.patch.Patch`
    instances by id and returns them directly (no copy), so the projector's
    in-place mutations (``obj.data.update(...)``, ``obj.version += 1``)
    behave exactly as they did before the GraphStore seam existed.
    """

    def __init__(self) -> None:
        self._objects: dict[str, "Object"] = {}
        self._relations: dict[str, "Relation"] = {}
        self._patches: dict[str, "Patch"] = {}

    # ---- objects ----

    def put_object(self, obj: "Object") -> None:
        self._objects[obj.id] = obj

    def get_object(self, object_id: str) -> Optional["Object"]:
        return self._objects.get(object_id)

    def remove_object(self, object_id: str) -> None:
        self._objects.pop(object_id, None)

    def all_objects(self) -> list["Object"]:
        return list(self._objects.values())

    # ---- relations ----

    def put_relation(self, rel: "Relation") -> None:
        self._relations[rel.id] = rel

    def get_relation(self, relation_id: str) -> Optional["Relation"]:
        return self._relations.get(relation_id)

    def remove_relation(self, relation_id: str) -> None:
        self._relations.pop(relation_id, None)

    def all_relations(self) -> list["Relation"]:
        return list(self._relations.values())

    # ---- patches ----

    def put_patch(self, patch: "Patch") -> None:
        self._patches[patch.id] = patch

    def get_patch(self, patch_id: str) -> Optional["Patch"]:
        return self._patches.get(patch_id)

    def all_patches(self) -> list["Patch"]:
        return list(self._patches.values())

    def remove_patch(self, patch_id: str) -> None:
        self._patches.pop(patch_id, None)

    # ---- lifecycle ----

    def clear(self) -> None:
        self._objects.clear()
        self._relations.clear()
        self._patches.clear()
