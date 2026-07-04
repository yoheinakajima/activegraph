"""Object, Relation, Graph + projector.

CONTRACT #2 (strict): Graph state is materialized from the event log.
`emit(event)` is the only mutator. Convenience methods (`add_object`,
`add_relation`, `patch_object`, `propose_patch`, `apply_patch`, etc.) all
build an Event and call emit.

CONTRACT v0.5 #15: the projector is `apply_event(graph, event)`, a
module-level function — the ONLY thing that mutates graph state. It is
called from two paths:
  - `Graph.emit` for live events (also persists + notifies listeners)
  - `Graph._replay_event` for replay (silent: no persist, no notify)
Two callers, one code path.

CONTRACT #5 (provenance): every object/relation/patch carries a provenance
dict written here, never by the behavior. Behaviors pass `data`; we strip
any `provenance` key they sneak in.

CONTRACT #4 (versioning): every object has a monotonic `version` int that
bumps on every patch.applied. Patches record `expected_version`.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional, cast

from activegraph.core.clock import Clock
from activegraph.core.event import Event
from activegraph.core.graph_store import ChainMatch, GraphStore, InMemoryGraphStore
from activegraph.core.ids import IDGen
from activegraph.core.patch import Patch

if TYPE_CHECKING:
    from activegraph.store.base import EventStore


# ---------- handles ----------


@dataclass
class Object:
    """A typed node in the materialized graph projection.

    ``data`` is the structured payload (schema-validated on
    ``add_object`` when a loaded pack owns the type); ``version``
    increments once per applied patch (CONTRACT #4); ``provenance``
    records the events that created and last touched it. A handle is
    for reading — mutations go through the graph's event surface
    (``patch_object`` / patches), never by assigning to fields.
    """

    id: str
    type: str
    data: dict[str, Any]
    version: int
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "data": copy.deepcopy(self.data),
            "version": self.version,
            "provenance": copy.deepcopy(self.provenance),
        }


@dataclass
class Relation:
    """A typed edge between two object ids in the graph projection.

    ``source`` and ``target`` hold object ids — dangling endpoints
    (ids with no object yet) are legal and queryable, so relations
    can arrive before the things they connect. ``type`` is the
    relation kind used by matching and traversal. Created via
    ``add_relation`` events; like ``Object``, a projection of the
    log, not a hand-mutated record.
    """

    id: str
    source: str
    target: str
    type: str
    data: dict[str, Any]
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "data": copy.deepcopy(self.data),
            "provenance": copy.deepcopy(self.provenance),
        }


# ---------- graph ----------


def _strip_provenance(data: dict[str, Any]) -> dict[str, Any]:
    """Behaviors do not get to set provenance (CONTRACT #5)."""
    if not isinstance(data, dict):
        return data
    return {k: v for k, v in data.items() if k != "provenance"}


def _diff(old: dict[str, Any], updates: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return per-field {old, new} for fields that actually change."""
    out: dict[str, dict[str, Any]] = {}
    for k, new_v in updates.items():
        old_v = old.get(k, _MISSING)
        if old_v != new_v:
            out[k] = {"old": None if old_v is _MISSING else old_v, "new": new_v}
    return out


_MISSING = object()


class Graph:
    """Event-sourced graph. The log is truth; objects/relations are projection.

    The only mutator is :meth:`emit`: every add/patch/remove sugar
    method builds an event, appends it to the log, projects it into
    the materialized state (a pluggable :class:`GraphStore`,
    CONTRACT v1.2 #1), persists it when an ``EventStore`` is
    attached, and notifies listeners. Read surfaces (``objects``,
    ``relations``, ``neighborhood``, ``match_chain``) delegate to
    the projection's query hooks. Wiping the projection loses
    nothing — replaying the log rebuilds it.
    """

    def __init__(
        self,
        ids: Optional[IDGen] = None,
        clock: Optional[Clock] = None,
        run_id: Optional[str] = None,
        graph_store: Optional[GraphStore] = None,
    ) -> None:
        self.ids = ids or IDGen()
        self.clock = clock or Clock()
        # CONTRACT v0.5 #6: every graph has a run_id.
        self.run_id: str = run_id or self.ids.run()

        # projected state — touched ONLY by apply_event (CONTRACT v0.5 #15).
        # v1.2: the projection lives behind a GraphStore so it can be kept
        # in process memory (the default) or pushed into an external graph
        # database. `self._state` is the materialized current-state view; it
        # is NOT the durable event log (that is `self._store`, an EventStore).
        self._state: GraphStore = graph_store or InMemoryGraphStore()

        # the log
        self._events: list[Event] = []

        # listeners (the runtime queue subscribes here)
        self._listeners: list[Callable[[Event], None]] = []

        # CONTRACT v0.5 #14: track which events were replayed (not live).
        # The trace printer renders them with a [replay.event] prefix.
        self._replayed_ids: set[str] = set()

        # Optional persistence sink (attached by Runtime when persist_to=...).
        self._store: Optional[EventStore] = None

        # v0.9: optional schema validators attached by `runtime.load_pack`.
        # `_pack_object_validator(type, data) -> validated_data` is called
        # from `add_object`. `_pack_relation_validator(type, src_type,
        # tgt_type) -> None` is called from `add_relation`. When None
        # (default), behavior is unchanged from v0.8.
        self._pack_object_validator = None
        self._pack_relation_validator = None

    # ---------- read API ----------

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    @property
    def replayed_ids(self) -> frozenset[str]:
        return frozenset(self._replayed_ids)

    def get_object(self, id_: str) -> Optional[Object]:
        return self._state.get_object(id_)

    def get_relation(self, id_: str) -> Optional[Relation]:
        return self._state.get_relation(id_)

    def get_patch(self, id_: str) -> Optional[Patch]:
        return self._state.get_patch(id_)

    def all_objects(self) -> list[Object]:
        return self._state.all_objects()

    def all_relations(self) -> list[Relation]:
        return self._state.all_relations()

    def get_relations(
        self,
        object_id: Optional[str] = None,
        type: Optional[str] = None,
        direction: str = "both",
    ) -> list[Relation]:
        # Pushed down to the store via find_relations (see graph_store.py).
        # Decompose the (object_id, direction) axis into source/target slots:
        # "outgoing"/"incoming" map to a single directional filter; "both"
        # needs source-OR-target, which find_relations (AND-only) can't express,
        # so we push the type filter and apply the membership test here. An
        # unrecognized direction keeps the v0 quirk of ignoring object_id.
        if object_id is None:
            return self._state.find_relations(type=type)
        if direction == "outgoing":
            return self._state.find_relations(source=object_id, type=type)
        if direction == "incoming":
            return self._state.find_relations(target=object_id, type=type)
        if direction == "both":
            return [
                r
                for r in self._state.find_relations(type=type)
                if object_id in (r.source, r.target)
            ]
        return self._state.find_relations(type=type)

    def relations(
        self,
        source: Optional[str] = None,
        target: Optional[str] = None,
        type: Optional[str] = None,
    ) -> list[Relation]:
        """Return relations filtered by ``source``, ``target``, and/or ``type``.

        v1.0.4 #1: the canonical filter API on ``Graph``. Decomposes the
        v0 ``get_relations(object_id=, direction=)`` axis into separate
        ``source`` and ``target`` slots so the call reads the way users
        already write it (matches ``docs/concepts/graph.md``). Filter
        kwargs compose by AND; calling with no kwargs returns every
        relation. ``Graph.get_relations(object_id=, type=, direction=)``
        stays as a backward-compatible alias.
        """
        # Pushed down to the store (find_relations applies the same AND filter).
        return self._state.find_relations(source=source, target=target, type=type)

    def neighborhood(self, object_id: str, depth: int = 1) -> tuple[list[Object], list[Relation]]:
        # Pushed down to the store (default is the same BFS; FalkorDB uses a
        # native variable-length path query). See GraphStore.neighborhood.
        return self._state.neighborhood(object_id, depth)

    def match_chain(
        self,
        node_types: list[Optional[str]],
        rels: list[tuple[Optional[str], str]],
    ) -> list[ChainMatch]:
        # Pushed down to the store: the default mirrors the matcher's
        # structural chain walk; FalkorDB resolves the whole chain in one
        # Cypher query. Node {prop: value} equality and WHERE stay in the
        # matcher (Python). See GraphStore.match_chain.
        return self._state.match_chain(node_types, rels)

    def objects_in_types(self, types: list[str]) -> list[Object]:
        # Pushed down to the store: OR-of-types in one query (FalkorDB uses
        # ``type IN [...]``); the default single-pass scan preserves order.
        # Used by the view builder for type-scoped views. See
        # GraphStore.find_objects_in_types.
        return self._state.find_objects_in_types(types)

    def objects(
        self,
        type: Optional[str] = None,
        where: Optional[dict[str, Any]] = None,
    ) -> list[Object]:
        """Return objects matching `type` and/or `where`.

        v1.0.3 #1: the canonical query API on `Graph`, mirroring
        `View.objects(type=...)` so call sites read the same inside
        and outside behaviors. `Graph.query(object_type=...)` is kept
        as a backward-compatible alias.
        """
        # Type filter is pushed down to the store via find_objects; the
        # ``where`` predicate is evaluated in Python over that subset.
        out: list[Object] = []
        for o in self._state.find_objects(type):
            if where and not _eval_where_on_object(where, o):
                continue
            out.append(o)
        return out

    def query(
        self,
        object_type: Optional[str] = None,
        where: Optional[dict[str, Any]] = None,
    ) -> list[Object]:
        """Backward-compatible alias for :meth:`objects`. v1.0.3 #1.

        New code should use ``graph.objects(type=...)`` — the kwarg
        ``type`` matches :meth:`View.objects` so the call reads the
        same in and out of behaviors.
        """
        return self.objects(type=object_type, where=where)

    def has_object_of_type(self, type_: str) -> bool:
        # Pushed down via find_objects (scopes the scan to ``type_``).
        return bool(self._state.find_objects(type_))

    # ---------- listener API (runtime hooks here) ----------

    def add_listener(self, fn: Callable[[Event], None]) -> None:
        self._listeners.append(fn)

    # ---------- store attachment (Runtime sets this) ----------

    def attach_store(self, store: EventStore) -> None:
        """Wire an EventStore as the durability sink. Idempotent on the same
        store. Calling with a *different* store after events exist is an error
        — events would be persisted in two places and you'd lose history.
        """
        if self._store is store:
            return
        if self._store is not None:
            from activegraph.runtime.config_errors import IncompatibleRuntimeState
            raise IncompatibleRuntimeState(
                "graph already has a store attached",
                what_failed=(
                    "Graph.attach_store() was called, but this graph already "
                    "has a store. Stores attach at most once per graph "
                    "lifetime."
                ),
                why=(
                    "A graph's store is the durability target for every "
                    "event it emits. Re-attaching a second store would "
                    "either (a) split the event log across two stores, "
                    "with subsequent events going to the new one and "
                    "earlier events stuck in the old, or (b) try to copy "
                    "the old log to the new store, which is a migration, "
                    "not an attach. The framework refuses re-attach so "
                    "neither failure mode is reachable silently."
                ),
                how_to_fix=(
                    "If you want to copy the graph's run to a new store, "
                    "use the migration primitive on the existing store's "
                    "URL after the run completes:\n"
                    "    activegraph migrate --from <old-url> --to <new-url>\n"
                    "\n"
                    "If the graph is fresh and the existing store is a "
                    "placeholder (e.g., from a test fixture), construct a "
                    "new Graph rather than re-attaching."
                ),
            )
        self._store = store

    @property
    def store(self) -> Optional[EventStore]:
        return self._store

    # ---------- the only mutator (live path) ----------

    def emit(self, event: Event) -> Event:
        """Append to log, project, persist (if attached), notify. CONTRACT #2."""
        # Fail-fast serialization check at emit time so bad payloads never
        # land in the in-memory log either (CONTRACT v0.5 #4).
        if self._store is not None:
            from activegraph.store.serde import validate_event

            validate_event(event)
        self._events.append(event)
        apply_event(self, event)
        if self._store is not None:
            self._store.append(event)
        for listener in self._listeners:
            listener(event)
        return event

    # ---------- the only mutator (replay path) ----------

    def _replay_event(self, event: Event) -> None:
        """Apply a recorded event WITHOUT persisting or firing listeners.

        Used only by `Runtime.load` and `Runtime.fork`. CONTRACT v0.5 #14:
        replay rebuilds graph state; it does NOT fire behaviors.
        """
        self._events.append(event)
        apply_event(self, event)
        self._replayed_ids.add(event.id)

    # ---------- convenience builders (each builds an Event and emits) ----------

    def add_object(
        self,
        type: str,
        data: dict[str, Any],
        *,
        actor: str = "system",
        caused_by: Optional[str] = None,
        frame_id: Optional[str] = None,
        evidence: Optional[list[str]] = None,
        llm_request_event_id: Optional[str] = None,
        tool_request_event_ids: Optional[list[str]] = None,
    ) -> Object:
        obj_id = self.ids.object(type)
        clean = _strip_provenance(copy.deepcopy(data))
        # v0.9: schema validation against loaded pack object types.
        # Validator is set by runtime.load_pack and is None when no
        # typed pack contributes this object type — preserving v0.8
        # untyped semantics (CONTRACT v0.9 #5 / #21).
        if self._pack_object_validator is not None:
            clean = self._pack_object_validator(type, clean)
        provenance = self._provenance(
            actor,
            caused_by,
            frame_id,
            evidence,
            llm_request_event_id,
            tool_request_event_ids,
        )
        payload = {
            "object": {
                "id": obj_id,
                "type": type,
                "data": clean,
                "version": 1,
                "provenance": provenance,
            },
            "id": obj_id,
        }
        event = Event(
            id=self.ids.event(),
            type="object.created",
            payload=payload,
            actor=actor,
            frame_id=frame_id,
            caused_by=caused_by,
            timestamp=self.clock.now(),
        )
        self.emit(event)
        return cast(Object, self._state.get_object(obj_id))

    def add_relation(
        self,
        source: str,
        target: str,
        type: str,
        data: Optional[dict[str, Any]] = None,
        *,
        actor: str = "system",
        caused_by: Optional[str] = None,
        frame_id: Optional[str] = None,
        llm_request_event_id: Optional[str] = None,
        tool_request_event_ids: Optional[list[str]] = None,
    ) -> Relation:
        rel_id = self.ids.relation()
        clean = _strip_provenance(copy.deepcopy(data or {}))
        # v0.9: relation type validation (source/target type rules).
        if self._pack_relation_validator is not None:
            src_obj = self._state.get_object(source)
            tgt_obj = self._state.get_object(target)
            self._pack_relation_validator(
                type,
                src_obj.type if src_obj else None,
                tgt_obj.type if tgt_obj else None,
            )
        provenance = self._provenance(
            actor,
            caused_by,
            frame_id,
            [],
            llm_request_event_id,
            tool_request_event_ids,
        )
        payload = {
            "relation": {
                "id": rel_id,
                "source": source,
                "target": target,
                "type": type,
                "data": clean,
                "provenance": provenance,
            },
            "id": rel_id,
            "source": source,
            "target": target,
        }
        event = Event(
            id=self.ids.event(),
            type="relation.created",
            payload=payload,
            actor=actor,
            frame_id=frame_id,
            caused_by=caused_by,
            timestamp=self.clock.now(),
        )
        self.emit(event)
        return cast(Relation, self._state.get_relation(rel_id))

    def remove_relation(
        self,
        relation_id: str,
        *,
        actor: str = "system",
        caused_by: Optional[str] = None,
        frame_id: Optional[str] = None,
    ) -> None:
        if self._state.get_relation(relation_id) is None:
            return
        event = Event(
            id=self.ids.event(),
            type="relation.removed",
            payload={"id": relation_id},
            actor=actor,
            frame_id=frame_id,
            caused_by=caused_by,
            timestamp=self.clock.now(),
        )
        self.emit(event)

    def remove_object(
        self,
        object_id: str,
        *,
        actor: str = "system",
        caused_by: Optional[str] = None,
        frame_id: Optional[str] = None,
    ) -> None:
        if self._state.get_object(object_id) is None:
            return
        event = Event(
            id=self.ids.event(),
            type="object.removed",
            payload={"id": object_id},
            actor=actor,
            frame_id=frame_id,
            caused_by=caused_by,
            timestamp=self.clock.now(),
        )
        self.emit(event)

    def patch_object(
        self,
        target: str,
        updates: dict[str, Any],
        *,
        actor: str = "system",
        caused_by: Optional[str] = None,
        frame_id: Optional[str] = None,
        rationale: Optional[str] = None,
        evidence: Optional[list[str]] = None,
        llm_request_event_id: Optional[str] = None,
        tool_request_event_ids: Optional[list[str]] = None,
    ) -> Patch:
        """Auto-apply shortcut: build patch, version-check, emit applied/rejected."""
        obj = self._state.get_object(target)
        if obj is None:
            raise KeyError(f"unknown object: {target}")
        clean = _strip_provenance(copy.deepcopy(updates))
        patch = Patch(
            id=self.ids.patch(),
            target=target,
            op="update",
            value=clean,
            expected_version=obj.version,
            proposed_by=actor,
            rationale=rationale,
            evidence=list(evidence or []),
            status="applied",
            provenance=self._provenance(
                actor,
                caused_by,
                frame_id,
                evidence,
                llm_request_event_id,
                tool_request_event_ids,
            ),
        )
        diff = _diff(obj.data, clean)
        event = Event(
            id=self.ids.event(),
            type="patch.applied",
            payload={
                "patch": patch.to_dict(),
                "target": target,
                "diff": diff,
            },
            actor=actor,
            frame_id=frame_id,
            caused_by=caused_by,
            timestamp=self.clock.now(),
        )
        self.emit(event)
        return cast(Patch, self._state.get_patch(patch.id))

    def propose_patch(
        self,
        target: str,
        op: str,
        value: dict[str, Any],
        *,
        proposed_by: str,
        rationale: Optional[str] = None,
        evidence: Optional[list[str]] = None,
        caused_by: Optional[str] = None,
        frame_id: Optional[str] = None,
        llm_request_event_id: Optional[str] = None,
        tool_request_event_ids: Optional[list[str]] = None,
    ) -> Patch:
        # Strip "object:" / "relation:" prefix if present (README sugar).
        normalized = target.split(":", 1)[1] if ":" in target else target
        obj = self._state.get_object(normalized)
        expected_version = obj.version if obj else 0
        clean = _strip_provenance(copy.deepcopy(value))
        patch = Patch(
            id=self.ids.patch(),
            target=normalized,
            op=op,
            value=clean,
            expected_version=expected_version,
            proposed_by=proposed_by,
            rationale=rationale,
            evidence=list(evidence or []),
            status="proposed",
            provenance=self._provenance(
                proposed_by,
                caused_by,
                frame_id,
                evidence,
                llm_request_event_id,
                tool_request_event_ids,
            ),
        )
        event = Event(
            id=self.ids.event(),
            type="patch.proposed",
            payload={"patch": patch.to_dict()},
            actor=proposed_by,
            frame_id=frame_id,
            caused_by=caused_by,
            timestamp=self.clock.now(),
        )
        self.emit(event)
        return cast(Patch, self._state.get_patch(patch.id))

    def apply_patch(
        self,
        patch_id: str,
        *,
        approved_by: str = "system",
        caused_by: Optional[str] = None,
        frame_id: Optional[str] = None,
    ) -> Event:
        patch = self._state.get_patch(patch_id)
        if patch is None:
            raise KeyError(f"unknown patch: {patch_id}")
        if patch.status != "proposed":
            from activegraph.runtime.exec_errors import InvalidPatchLifecycleState
            raise InvalidPatchLifecycleState(
                patch_id=patch_id, current_status=patch.status,
            )
        target_obj = self._state.get_object(patch.target)
        current_version = target_obj.version if target_obj else 0
        if current_version != patch.expected_version:
            return self._reject(
                patch_id,
                reason=f"version mismatch: expected {patch.expected_version}, got {current_version}",
                actor=approved_by,
                caused_by=caused_by,
                frame_id=frame_id,
            )
        diff = _diff(target_obj.data if target_obj else {}, patch.value)
        event = Event(
            id=self.ids.event(),
            type="patch.applied",
            payload={
                "patch": {**patch.to_dict(), "status": "applied"},
                "target": patch.target,
                "diff": diff,
                "approved_by": approved_by,
            },
            actor=approved_by,
            frame_id=frame_id,
            caused_by=caused_by,
            timestamp=self.clock.now(),
        )
        return self.emit(event)

    def reject_patch(
        self,
        patch_id: str,
        reason: str,
        *,
        actor: str = "system",
        caused_by: Optional[str] = None,
        frame_id: Optional[str] = None,
    ) -> Event:
        return self._reject(patch_id, reason, actor=actor, caused_by=caused_by, frame_id=frame_id)

    def _reject(
        self,
        patch_id: str,
        reason: str,
        *,
        actor: str,
        caused_by: Optional[str],
        frame_id: Optional[str],
    ) -> Event:
        # Type-level assertion only: a missing patch_id fails on attribute
        # access exactly as before (validation is the caller's job).
        patch = cast(Patch, self._state.get_patch(patch_id))
        current = self._state.get_object(patch.target)
        event = Event(
            id=self.ids.event(),
            type="patch.rejected",
            payload={
                "patch_id": patch_id,
                "target": patch.target,
                "reason": reason,
                "current_version": current.version if current else 0,
            },
            actor=actor,
            frame_id=frame_id,
            caused_by=caused_by,
            timestamp=self.clock.now(),
        )
        return self.emit(event)

    # ---------- provenance helper (CONTRACT v0.5 #13: includes run_id) ----------

    def _provenance(
        self,
        actor: str,
        caused_by: Optional[str],
        frame_id: Optional[str],
        evidence: Optional[list[str]],
        llm_request_event_id: Optional[str] = None,
        tool_request_event_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        p: dict[str, Any] = {
            "created_by": actor,
            "caused_by_event": caused_by,
            "frame_id": frame_id,
            "timestamp": self.clock.now(),
            "evidence": list(evidence or []),
            "run_id": self.run_id,
        }
        # CONTRACT v0.6 #15: objects/relations/patches created inside an
        # @llm_behavior handler carry the llm.requested event id so causal
        # chain walks can cross the LLM boundary.
        if llm_request_event_id is not None:
            p["llm_request_event_id"] = llm_request_event_id
        # CONTRACT v0.7 #19: when the LLM behavior's turn loop invoked
        # tools, all contributing tool.requested event ids are stamped
        # here so causal-chain walks can enumerate every tool call.
        if tool_request_event_ids:
            p["tool_request_event_ids"] = list(tool_request_event_ids)
        return p


# ---------- the projector — module-level, single mutation code path ----------


def apply_event(graph: Graph, event: Event) -> None:
    """Project an event onto the graph's in-memory state.

    The ONLY function that mutates `_objects` / `_relations` / `_patches`.
    Called from `Graph.emit` (live) and `Graph._replay_event` (replay).
    Pure projection — no I/O, no listener calls, no event log mutation.
    """
    t = event.type
    p = event.payload

    if t == "object.created":
        o = p["object"]
        graph._state.put_object(
            Object(
                id=o["id"],
                type=o["type"],
                data=copy.deepcopy(o["data"]),
                version=o["version"],
                provenance=copy.deepcopy(o["provenance"]),
            )
        )

    elif t == "object.removed":
        graph._state.remove_object(p["id"])
        # cascade: drop relations touching it. Pushed down to two scoped
        # lookups (out-edges + in-edges) instead of scanning every relation;
        # on FalkorDB these are index-backed queries. Dedupe by id so a
        # self-loop (source == target) is removed once.
        touching = graph._state.find_relations(
            source=p["id"]
        ) + graph._state.find_relations(target=p["id"])
        seen: set[str] = set()
        for r in touching:
            if r.id in seen:
                continue
            seen.add(r.id)
            graph._state.remove_relation(r.id)

    elif t == "relation.created":
        r = p["relation"]
        graph._state.put_relation(
            Relation(
                id=r["id"],
                source=r["source"],
                target=r["target"],
                type=r["type"],
                data=copy.deepcopy(r["data"]),
                provenance=copy.deepcopy(r["provenance"]),
            )
        )

    elif t == "relation.removed":
        graph._state.remove_relation(p["id"])

    elif t == "patch.proposed":
        patch_dict = p["patch"]
        graph._state.put_patch(_patch_from_dict(patch_dict))

    elif t == "patch.applied":
        patch_dict = p["patch"]
        patch = _patch_from_dict({**patch_dict, "status": "applied"})
        graph._state.put_patch(patch)
        obj = graph._state.get_object(patch.target)
        if obj is not None:
            if patch.op == "update":
                obj.data.update(patch.value)
            elif patch.op == "replace":
                obj.data = copy.deepcopy(patch.value)
            obj.version += 1
            graph._state.put_object(obj)

    elif t == "patch.rejected":
        existing = graph._state.get_patch(p["patch_id"])
        if existing is not None:
            existing.status = "rejected"
            existing.rejection_reason = p["reason"]
            graph._state.put_patch(existing)


def _patch_from_dict(d: dict[str, Any]) -> Patch:
    return Patch(
        id=d["id"],
        target=d["target"],
        op=d["op"],
        value=copy.deepcopy(d["value"]),
        expected_version=d["expected_version"],
        proposed_by=d["proposed_by"],
        rationale=d.get("rationale"),
        evidence=list(d.get("evidence", [])),
        status=d.get("status", "proposed"),
        rejection_reason=d.get("rejection_reason"),
        provenance=copy.deepcopy(d.get("provenance", {})),
    )


# ---------- where evaluator (used by query and matchers) ----------

_OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">": lambda a, b: a is not None and a > b,
    "<": lambda a, b: a is not None and a < b,
    ">=": lambda a, b: a is not None and a >= b,
    "<=": lambda a, b: a is not None and a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
}


def _resolve_path(root: Any, path: list[str]) -> Any:
    cur = root
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif hasattr(cur, p):
            cur = getattr(cur, p)
        else:
            return None
        if cur is None:
            return None
    return cur


def evaluate_where(where: dict[str, Any], root: Any) -> bool:
    """Evaluate a where dict against a root (event payload, object, etc.).

    Keys are dotted paths. Values are either literals (equality) or
    `{"op": value}` dicts for comparisons.
    """
    for key, expected in where.items():
        actual = _resolve_path(root, key.split("."))
        if isinstance(expected, dict):
            for op, value in expected.items():
                fn = _OPS.get(op)
                if fn is None:
                    from activegraph.errors import internal_bug_fields
                    from activegraph.runtime.exec_errors import (
                        InternalEvaluatorError,
                    )
                    _fields = internal_bug_fields(
                        summary=f"unknown where operator: {op!r}",
                        what_happened=(
                            f"The view-filter evaluator in graph.py received "
                            f"comparison operator {op!r}, but the operator "
                            f"table (_OPS in this module) has no handler for it."
                        ),
                        why_invariant=(
                            "The operator table is the source of truth for "
                            "which comparison operators view filters accept. "
                            "An unknown operator means either the filter was "
                            "constructed by code that bypassed the parser, or "
                            "the operator table drifted from the parser. "
                            "Either way, evaluating the filter would silently "
                            "produce wrong results — refuse instead."
                        ),
                        location="activegraph/core/graph.py:evaluate_where",
                        extra_context={"operator": op},
                    )
                    raise InternalEvaluatorError(
                        _fields["summary"],
                        what_failed=_fields["what_failed"],
                        why=_fields["why"],
                        how_to_fix=_fields["how_to_fix"],
                        context=_fields["context"],
                    )
                if not fn(actual, value):
                    return False
        else:
            if actual != expected:
                return False
    return True


def _eval_where_on_object(where: dict[str, Any], obj: Object) -> bool:
    """Where on a bare Object — keys are paths under data unless they start with one of the object fields."""
    root = {
        "id": obj.id,
        "type": obj.type,
        "data": obj.data,
        "version": obj.version,
        "provenance": obj.provenance,
        # also expose data fields at top level for convenience
        **obj.data,
    }
    return evaluate_where(where, root)
