"""GraphStore: pluggable backend for the materialized graph state.

CONTRACT context: graph state (objects / relations / patches) is a
*projection* of the event log (CONTRACT #2). The GraphStore is where that
projection lives. The projector ``apply_event`` is still the only writer;
as of v1.1 it writes *through* a GraphStore instead of mutating dicts
directly. This makes the current-state view pluggable: keep it in process
memory (:class:`InMemoryGraphStore`, the default — byte-for-byte identical
to the pre-v1.1 behavior) or push it into an external graph database
(``FalkorDBGraphStore`` in ``activegraph.store.falkordb``).

A GraphStore is NOT an EventStore. The :class:`~activegraph.store.base.EventStore`
is the durable, append-only log — the source of truth. The GraphStore is
the queryable current-state view rebuilt by replaying that log. Losing a
GraphStore is recoverable (replay the log); losing the EventStore is not.

The interface is deliberately small: upsert/get/remove/enumerate for each
of the three entity kinds, plus ``clear`` and ``close``. No queries beyond
enumeration — filtering, neighborhood walks, and ``where`` evaluation stay
in :class:`~activegraph.core.graph.Graph`, which composes them over
``all_objects`` / ``all_relations``. That keeps every backend simple and
keeps query semantics in exactly one place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from activegraph.core.graph import Object, Relation
    from activegraph.core.patch import Patch


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
