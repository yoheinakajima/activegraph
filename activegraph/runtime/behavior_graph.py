"""Constrained graph wrapper passed to behaviors. CONTRACT #7.

Allowed methods: add_object, add_relation, patch_object, propose_patch, emit.
The wrapper stamps actor / caused_by / frame_id automatically (CONTRACT #5)
and counts mutations so the runtime can report them in behavior.completed.

Behaviors get this object as their `graph` argument — never the raw Graph.
"""

from __future__ import annotations

from typing import Any, Optional

from typing import TYPE_CHECKING

from activegraph.core.event import Event
from activegraph.core.graph import Graph, Object, Relation
from activegraph.core.patch import Patch

if TYPE_CHECKING:
    from activegraph.runtime.context_reads import ReadRecorder


class Counters:
    __slots__ = ("objects_created", "relations_created", "patches_applied", "patches_proposed", "events_emitted")

    def __init__(self) -> None:
        self.objects_created = 0
        self.relations_created = 0
        self.patches_applied = 0
        self.patches_proposed = 0
        self.events_emitted = 0  # user-emitted via emit(), not graph mutations


class BehaviorGraph:
    def __init__(
        self,
        graph: Graph,
        *,
        actor: str,
        caused_by: Optional[str],
        frame_id: Optional[str],
        llm_request_event_id: Optional[str] = None,
        tool_request_event_ids: Optional[list[str]] = None,
        read_recorder: Optional["ReadRecorder"] = None,
    ) -> None:
        self._graph = graph
        self._actor = actor
        self._caused_by = caused_by
        self._frame_id = frame_id
        # CONTRACT v0.6 #15: when this BehaviorGraph was created for an
        # @llm_behavior handler, every object/relation/patch it creates
        # carries the originating llm.requested event id in provenance.
        self._llm_request_event_id = llm_request_event_id
        # CONTRACT v0.7 #19: when the LLM behavior's turn loop invoked
        # tools, the tool.requested event ids are stamped into the
        # provenance of every object/relation/patch the handler
        # creates. Causal-chain walks can then enumerate every tool
        # call that contributed to a claim.
        self._tool_request_event_ids: list[str] = list(
            tool_request_event_ids or []
        )
        # CONTRACT v1.10 #1: when the runtime traces context reads, point
        # reads through this wrapper land in the execution's read set.
        # None (the default) keeps the wrapper byte-identical to pre-v1.10.
        self._read_recorder = read_recorder
        self.counters = Counters()

    # ---- mutators ----

    def add_object(self, type: str, data: dict[str, Any]) -> Object:
        obj = self._graph.add_object(
            type=type,
            data=data,
            actor=self._actor,
            caused_by=self._caused_by,
            frame_id=self._frame_id,
            llm_request_event_id=self._llm_request_event_id,
            tool_request_event_ids=self._tool_request_event_ids,
        )
        self.counters.objects_created += 1
        return obj

    def add_relation(
        self,
        source: str,
        target: str,
        type: str,
        data: Optional[dict[str, Any]] = None,
    ) -> Relation:
        rel = self._graph.add_relation(
            source=source,
            target=target,
            type=type,
            data=data,
            actor=self._actor,
            caused_by=self._caused_by,
            frame_id=self._frame_id,
            llm_request_event_id=self._llm_request_event_id,
            tool_request_event_ids=self._tool_request_event_ids,
        )
        self.counters.relations_created += 1
        return rel

    def patch_object(self, target: str, updates: dict[str, Any]) -> Patch:
        patch = self._graph.patch_object(
            target=target,
            updates=updates,
            actor=self._actor,
            caused_by=self._caused_by,
            frame_id=self._frame_id,
            llm_request_event_id=self._llm_request_event_id,
            tool_request_event_ids=self._tool_request_event_ids,
        )
        self.counters.patches_applied += 1
        return patch

    def propose_patch(
        self,
        target: str,
        op: str = "update",
        value: Optional[dict[str, Any]] = None,
        rationale: Optional[str] = None,
        evidence: Optional[list[str]] = None,
    ) -> Patch:
        patch = self._graph.propose_patch(
            target=target,
            op=op,
            value=value or {},
            proposed_by=self._actor,
            rationale=rationale,
            evidence=evidence,
            caused_by=self._caused_by,
            frame_id=self._frame_id,
            llm_request_event_id=self._llm_request_event_id,
            tool_request_event_ids=self._tool_request_event_ids,
        )
        self.counters.patches_proposed += 1
        return patch

    def emit(self, event_type: str, payload: dict[str, Any]) -> Event:
        ev = Event(
            id=self._graph.ids.event(),
            type=event_type,
            payload=dict(payload),
            actor=self._actor,
            frame_id=self._frame_id,
            caused_by=self._caused_by,
            timestamp=self._graph.clock.now(),
        )
        self._graph.emit(ev)
        self.counters.events_emitted += 1
        return ev

    # ---- read passthroughs (not iteration; that goes through ctx.view) ----

    def get_object(self, id_: str) -> Optional[Object]:
        obj = self._graph.get_object(id_)
        # CONTRACT v1.10 #1: a hit is a traced object read; a miss read
        # nothing and stays out of the read set.
        if obj is not None and self._read_recorder is not None:
            self._read_recorder.record(obj.id)
        return obj

    def get_relation(self, id_: str) -> Optional[Relation]:
        # Deliberately untraced (CONTRACT v1.10 #1): this reads a
        # relation, not an object — endpoints naming object ids do not
        # make it an object read.
        return self._graph.get_relation(id_)
