"""Object, Relation, Graph + projector.

CONTRACT #2 (strict): Graph state is materialized from the event log.
`emit(event)` is the only mutator. Convenience methods (`add_object`,
`add_relation`, `patch_object`, `propose_patch`, `apply_patch`, etc.) all
build an Event and call emit.

CONTRACT v0.5 #15: the projector is `apply_event(graph, event)`, a
module-level function — the ONLY thing that mutates graph state. It is
called from two paths:
  - `Graph.emit` for live events (persists, offers sinks, notifies listeners)
  - `Graph._replay_event` for replay (silent: no persist, sinks, or listeners)
Two callers, one code path.

CONTRACT #5 (provenance): every object/relation/patch carries a provenance
dict written here, never by the behavior. Behaviors pass `data`; a
`provenance` key in it raises ReservedFieldError (CONTRACT v1.10 #2 —
until v1.10 it was silently stripped, which meant a caller who thought
they attached provenance had attached nothing).

CONTRACT #4 (versioning): every object has a monotonic `version` int that
bumps on every patch.applied. Patches record `expected_version`.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional, cast

from activegraph.core.clock import Clock
from activegraph.core.event import Event
from activegraph.core.graph_store import (
    ChainMatch,
    GraphStore,
    InMemoryGraphStore,
    ObjectQuery,
)
from activegraph.core.ids import IDGen
from activegraph.core.patch import Patch, validate_patch_op

if TYPE_CHECKING:
    from activegraph.observability.metrics import Metrics
    from activegraph.sinks.base import EventSink, OverflowPolicy, SinkStatus
    from activegraph.sinks.dispatch import SinkHandle
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


# CONTRACT v1.10 #2: top-level data keys the framework owns on every
# object, relation, and patch value. Caller data colliding with one of
# these raises ReservedFieldError instead of the pre-v1.10 silent strip.
# One table for all four mutation surfaces — a future reserved key gets
# the same loud treatment by being added here.
RESERVED_DATA_FIELDS: frozenset[str] = frozenset({"provenance"})


def _reject_reserved_fields(
    data: dict[str, Any], *, api: str, param: str
) -> dict[str, Any]:
    """Refuse caller data that collides with a reserved field.

    CONTRACT #5 / v1.10 #2: provenance is framework-written. Until
    v1.10 a colliding key was silently stripped, so a caller who
    thought they attached provenance had attached nothing; now the
    collision fails loudly at the call site. Returns ``data`` unchanged
    when clean (non-dict input passes through, matching the old
    stripper's tolerance).
    """
    if not isinstance(data, dict):
        return data
    for key in data:
        if key in RESERVED_DATA_FIELDS:
            from activegraph.runtime.exec_errors import ReservedFieldError

            raise ReservedFieldError(field=key, api=api, param=param)
    return data


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
    attached, offers isolated sinks, and notifies listeners. Read surfaces (``objects``,
    ``relations``, ``neighborhood``, ``match_chain``) delegate to
    the projection's query hooks. Wiping the projection loses
    nothing — replaying the log rebuilds it. Accepted live events fan out
    through bounded ``EventSink`` workers; replay never does.
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

        # Serialize each graph's live acceptance + sink-offer boundary. RLock
        # preserves the supported re-entrant listener shape while concurrent
        # callers cannot reverse log/sink order. Legacy listeners run after
        # this lock is released so their historical synchronous behavior does
        # not make unrelated emitting threads wait on one another.
        self._emit_lock = threading.RLock()

        # CONTRACT v1.8: outbound accepted-event observers use a separate,
        # isolated dispatch path.  They are not synchronous listeners and
        # never run adapter code on emit's thread.
        self._sinks: dict[str, SinkHandle] = {}
        self._closing_sinks: dict[str, SinkHandle] = {}
        self._terminal_sink_statuses: dict[str, SinkStatus] = {}

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
        from activegraph.store.serde import clone_event

        return [clone_event(event) for event in self._events]

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
        result = self._state.query_objects(
            ObjectQuery(type=type, where=where, result_mode="objects")
        )
        if result.exists is not None:
            raise RuntimeError(
                "GraphStore returned a scalar existence result for an object query"
            )
        out: list[Object] = []
        for o in result.candidates:
            if result.residual_where and not _eval_where_on_object(
                result.residual_where, o
            ):
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

    def has_object_of_type(self, type_: Optional[str]) -> bool:
        """Whether at least one object has ``type_`` (``None`` means any)."""
        result = self._state.query_objects(
            ObjectQuery(
                type=type_,
                where={},
                limit=1,
                result_mode="exists",
            )
        )
        if result.exists is not None:
            if result.residual_where:
                raise RuntimeError(
                    "GraphStore returned scalar existence with a residual predicate"
                )
            return result.exists
        for obj in result.candidates:
            if not result.residual_where or _eval_where_on_object(
                result.residual_where, obj
            ):
                return True
        return False

    # ---------- listener API (runtime hooks here) ----------

    def add_listener(self, fn: Callable[[Event], None]) -> None:
        with self._emit_lock:
            self._listeners.append(fn)

    def _remove_listener(self, fn: Callable[[Event], None]) -> bool:
        """Remove one runtime-internal listener if it is attached."""

        with self._emit_lock:
            try:
                self._listeners.remove(fn)
            except ValueError:
                return False
            return True

    # ---------- accepted-event sink API ----------

    def add_sink(
        self,
        sink: EventSink,
        *,
        name: str | None = None,
        queue_capacity: int = 1024,
        overflow_policy: OverflowPolicy | str = "drop_newest",
        metrics: Metrics | None = None,
    ) -> SinkHandle:
        """Attach one isolated outbound observer of accepted live events.

        The returned handle owns a bounded FIFO and daemon worker.  A class
        name is used when ``name`` is omitted; names must be unique within
        the graph because they key both status and metrics.  Historical
        events already present in the graph are never delivered.
        """

        from activegraph.observability.metrics import NoOpMetrics
        from activegraph.sinks.base import EventSink as EventSinkProtocol
        from activegraph.sinks.base import OverflowPolicy as SinkOverflowPolicy
        from activegraph.sinks.dispatch import SinkHandle

        if not isinstance(sink, EventSinkProtocol):
            raise TypeError(
                "sink must implement open(), on_event(), flush(), and close()"
            )
        resolved_name = name if name is not None else type(sink).__name__
        if not resolved_name.strip():
            raise ValueError("sink name must not be empty")
        policy = SinkOverflowPolicy(overflow_policy)
        with self._emit_lock:
            if resolved_name in self._sinks or resolved_name in self._closing_sinks:
                raise ValueError(
                    f"sink name {resolved_name!r} is already attached to this graph"
                )
            handle = SinkHandle(
                sink,
                name=resolved_name,
                run_id=self.run_id,
                queue_capacity=queue_capacity,
                overflow_policy=policy,
                metrics=metrics if metrics is not None else NoOpMetrics(),
                _owner_close=self._close_owned_sink,
            )
            self._terminal_sink_statuses.pop(resolved_name, None)
            self._sinks[resolved_name] = handle
        return handle

    def remove_sink(
        self,
        sink: str | SinkHandle,
        *,
        timeout: float | None = 5.0,
    ) -> bool:
        """Detach, drain, and close one sink within ``timeout``."""

        name = sink if isinstance(sink, str) else sink.name
        with self._emit_lock:
            handle = self._sinks.get(name)
            if handle is not None:
                if not isinstance(sink, str) and handle is not sink:
                    return False
                del self._sinks[name]
                self._closing_sinks[name] = handle
            else:
                handle = self._closing_sinks.get(name)
            if handle is None or (not isinstance(sink, str) and handle is not sink):
                if isinstance(sink, str) and name in self._terminal_sink_statuses:
                    del self._terminal_sink_statuses[name]
                    return True
                if not isinstance(sink, str):
                    return sink.status().state.value == "closed"
                return False
        closed = handle._close_worker(  # noqa: SLF001 — graph owns handles
            timeout=timeout
        )
        if closed or handle._is_terminal():  # noqa: SLF001 — graph owns handles
            with self._emit_lock:
                if self._closing_sinks.get(name) is handle:
                    del self._closing_sinks[name]
                    if not closed:
                        self._terminal_sink_statuses[name] = handle.status()
                    handle._owner_close = None  # noqa: SLF001 — release graph cycle
        return closed

    def _close_owned_sink(
        self, handle: SinkHandle, timeout: float | None
    ) -> bool:
        """Detach and close a handle through its owning graph."""

        return self.remove_sink(handle, timeout=timeout)

    def sink_statuses(self) -> tuple[SinkStatus, ...]:
        """Return active, closing, and retained failed status snapshots.

        A timed-out close remains visible and retryable.  A terminal close
        failure is retained until its name is reused or explicitly removed.
        """

        with self._emit_lock:
            handles = tuple(self._sinks.values())
            closing = tuple(self._closing_sinks.values())
            terminal = tuple(self._terminal_sink_statuses.values())
        return (
            tuple(handle.status() for handle in handles)
            + tuple(handle.status() for handle in closing)
            + terminal
        )

    def _sink_names_in_use(self) -> frozenset[str]:
        """Return names reserved by active or still-closing attachments."""

        with self._emit_lock:
            return frozenset(self._sinks) | frozenset(self._closing_sinks)

    def flush_sinks(self, timeout: float | None = 5.0) -> bool:
        """Flush every attached sink, with ``timeout`` applied per sink."""

        with self._emit_lock:
            handles = tuple(self._sinks.values()) + tuple(
                self._closing_sinks.values()
            )
        results = [handle.flush(timeout=timeout) for handle in handles]
        return all(results)

    def close_sinks(self, timeout: float | None = 5.0) -> bool:
        """Detach and close every sink, with ``timeout`` applied per sink."""

        with self._emit_lock:
            for name, handle in tuple(self._sinks.items()):
                self._closing_sinks[name] = handle
            self._sinks.clear()
            handles = tuple(self._closing_sinks.items())
        results: list[bool] = []
        for name, handle in handles:
            closed = handle._close_worker(  # noqa: SLF001 — graph owns handles
                timeout=timeout
            )
            results.append(closed)
            if closed or handle._is_terminal():  # noqa: SLF001 — graph owns handles
                with self._emit_lock:
                    if self._closing_sinks.get(name) is handle:
                        del self._closing_sinks[name]
                        if not closed:
                            self._terminal_sink_statuses[name] = handle.status()
                        handle._owner_close = None  # noqa: SLF001 — release graph cycle
        return all(results)

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
        store_run_id = getattr(store, "run_id", self.run_id)
        store_count = (
            store.count() if hasattr(store, "count") else len(self._events)
        )
        if store_run_id != self.run_id or store_count != len(self._events):
            from activegraph.runtime.config_errors import IncompatibleRuntimeState

            raise IncompatibleRuntimeState(
                "graph and event store are not at the same run head",
                what_failed=(
                    f"Graph.attach_store() found graph run {self.run_id!r} at "
                    f"{len(self._events)} accepted event(s), while the store is "
                    f"scoped to run {store_run_id!r} at {store_count} event(s)."
                ),
                why=(
                    "Attaching does not load, copy, merge, or truncate history. "
                    "A graph and its authoritative EventStore must describe the "
                    "same run head before future events can be committed safely."
                ),
                how_to_fix=(
                    "Use Runtime.load(...) to resume an existing run. To persist "
                    "an in-memory graph, use runtime.save_state(path=...), which "
                    "copies its accepted prefix before attaching. Otherwise, "
                    "construct a fresh empty store with the graph's run_id."
                ),
                context={
                    "graph_run_id": self.run_id,
                    "store_run_id": store_run_id,
                    "graph_event_count": len(self._events),
                    "store_event_count": store_count,
                },
            )
        self._store = store

    @property
    def store(self) -> Optional[EventStore]:
        return self._store

    # ---------- the only mutator (live path) ----------

    def emit(self, event: Event) -> Event:
        """Canonicalize, persist, project, offer sinks, then notify listeners."""
        from activegraph.store.serde import canonicalize_event, clone_event

        accepted = canonicalize_event(event)
        _validate_framework_event(accepted)
        with self._emit_lock:
            # CONTRACT v1.11 #2: the EventStore is authoritative. A failed
            # append changes no graph/log/queue/observer-visible state.
            if self._store is not None:
                self._store.append(accepted)
            self._events.append(accepted)
            apply_event(self, accepted)
            # CONTRACT v1.8 #2: offer only after projection + durable append,
            # and before legacy listeners. The position preserves order when
            # a listener re-enters emit and prevents listener failure from
            # suppressing observation of an already-accepted event.
            if self._sinks:
                from activegraph.sinks.base import DeliveryContext

                context = DeliveryContext(
                    run_id=self.run_id,
                    sequence=len(self._events),
                    mode="live",
                )
                for sink in tuple(self._sinks.values()):
                    try:
                        sink._offer(  # noqa: SLF001 — graph-only seam
                            clone_event(accepted), context
                        )
                    except Exception:
                        # SinkHandle._offer is designed non-throwing; keep this
                        # final containment boundary so an observer bug can
                        # never turn accepted observation into graph control
                        # flow.
                        continue
            listeners = tuple(self._listeners)
        # Keep legacy callbacks synchronous, exception-propagating, and outside
        # the sink-order lock. In particular, an existing listener may start a
        # second emitting thread and wait for it without deadlocking Graph.
        for listener in listeners:
            listener(clone_event(accepted))
        return clone_event(accepted)

    # ---------- the only mutator (replay path) ----------

    def _replay_event(self, event: Event) -> None:
        """Apply a recorded event WITHOUT persisting or firing listeners.

        Used only by `Runtime.load` and `Runtime.fork`. CONTRACT v0.5 #14:
        replay rebuilds graph state; it does NOT fire behaviors.
        """
        from activegraph.store.serde import canonicalize_event

        accepted = canonicalize_event(event)
        _validate_framework_event(accepted)
        self._events.append(accepted)
        apply_event(self, accepted)
        self._replayed_ids.add(accepted.id)

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
        clean = copy.deepcopy(
            _reject_reserved_fields(data, api="add_object", param="data")
        )
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
        clean = copy.deepcopy(
            _reject_reserved_fields(data or {}, api="add_relation", param="data")
        )
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
        clean = copy.deepcopy(
            _reject_reserved_fields(updates, api="patch_object", param="updates")
        )
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
        validate_patch_op(op)
        normalized = target.split(":", 1)[1] if ":" in target else target
        obj = self._state.get_object(normalized)
        expected_version = obj.version if obj else 0
        clean = copy.deepcopy(
            _reject_reserved_fields(value, api="propose_patch", param="value")
        )
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
            else:  # validate_patch_op should make this unreachable.
                validate_patch_op(patch.op, event_id=event.id)
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


def _validate_framework_event(event: Event) -> None:
    """Validate closed runtime-owned event payloads before acceptance."""
    if event.type not in {"patch.proposed", "patch.applied"}:
        return
    patch = event.payload.get("patch")
    if not isinstance(patch, dict):
        from activegraph.runtime.exec_errors import InternalEvaluatorError

        raise InternalEvaluatorError(
            f"{event.type} event has no patch object",
            what_failed=(
                f"Framework event {event.id!r} ({event.type}) did not carry "
                "a dictionary at payload['patch']."
            ),
            why=(
                "Patch lifecycle events are closed runtime records. Projecting "
                "one without its patch would make replay depend on a malformed "
                "success or proposal fact."
            ),
            how_to_fix=(
                "Do not emit patch.* events directly. Use graph.patch_object, "
                "graph.propose_patch, and graph.apply_patch. For a stored run, "
                "inspect or migrate the malformed event rather than skipping it."
            ),
            context={"event_id": event.id, "event_type": event.type},
        )
    validate_patch_op(str(patch.get("op")), event_id=event.id)


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
