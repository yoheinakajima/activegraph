"""Context-read tracing for behavior executions. CONTRACT v1.10 #1.

When a runtime is constructed with ``trace_context_reads=True``, every
behavior execution gets a :class:`ReadRecorder` threaded through the two
read surfaces a behavior legitimately has — the constrained
``BehaviorGraph`` point reads and ``ctx.view`` (wrapped in
:class:`TracedView`) — plus, for LLM behaviors, the objects serialized
into the assembled prompt. When the execution commits (its
``behavior.completed`` / ``behavior.failed`` lifecycle event lands), the
runtime emits ONE batched ``context.read`` event carrying the ordered,
deduplicated list of object ids the execution read.

Traced access paths (the complete list — anything else is NOT traced):

- ``ctx.view.objects(...)`` — every :class:`~activegraph.core.graph.Object`
  each call returns, in returned order.
- ``graph.get_object(id)`` on the behavior's constrained graph wrapper —
  recorded only when the id resolves to an object (a miss reads nothing).
- LLM behaviors only: every object serialized into the prompt's graph
  context block, recorded at prompt-assembly time (the model reads them
  whether or not the handler later touches ``ctx.view``).

Deliberately untraced (reads that do not flow through the behavior's
object accessors, or that are not object reads at all):

- ``ctx.view.relations()`` / ``ctx.view.events()`` and
  ``graph.get_relation(id)`` — relation and event reads, even though
  relation endpoints name object ids and event payloads embed object
  snapshots.
- The triggering ``event`` argument and a relation behavior's
  ``relation`` argument — pushed to the behavior, not read by it.
- Reads a tool performs through a closed-over raw ``Graph``
  (e.g. ``make_graph_query_tool``) — tools run outside the frame seam.
- Runtime-internal reads: pattern matching, view construction itself
  (building ``ctx.view`` is not reading it; only accessor calls count),
  and projection/store queries behaviors cannot legitimately reach.

Determinism: the read set derives purely from the behavior's actual
accessor calls in first-read order — no timestamps, no set iteration —
so a replayed execution reproduces its ``context.read`` byte for byte.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.core.graph import Object
from activegraph.core.view import View

# CONTRACT v1.10 #1: `object_ids` on a context.read payload is bounded.
# `count` stays exact past the cap; `truncated: true` marks the cut.
CONTEXT_READ_ID_CAP = 200


class ReadRecorder:
    """Ordered, deduplicated object-id read set for ONE behavior execution.

    First read wins the position; later reads of the same id are no-ops.
    Plain list+set — insertion order is the only order, so the recorded
    sequence is replay-stable by construction.
    """

    __slots__ = ("_seen", "_order")

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._order: list[str] = []

    def record(self, object_id: str) -> None:
        """Record one object read (idempotent per id)."""
        if object_id not in self._seen:
            self._seen.add(object_id)
            self._order.append(object_id)

    def record_objects(self, objects: list[Object]) -> None:
        """Record a batch of read objects in the order they were returned."""
        for obj in objects:
            self.record(obj.id)

    @property
    def object_ids(self) -> list[str]:
        """The read set in first-read order (a copy)."""
        return list(self._order)

    def __len__(self) -> int:
        return len(self._order)

    def __bool__(self) -> bool:
        return bool(self._order)


class TracedView(View):
    """A :class:`~activegraph.core.view.View` that records object reads.

    Substituted for the plain view as ``ctx.view`` when the runtime has
    ``trace_context_reads=True``. Only :meth:`objects` records — the
    ids of exactly the objects each call returns (post-filter), so the
    trace reflects what the behavior actually saw, not the view's whole
    scope. ``relations()`` and ``events()`` are inherited untraced:
    they read relations and events, not objects.
    """

    def __init__(self, view: View, recorder: ReadRecorder) -> None:
        super().__init__(view._objects, view._relations, view._events)
        self._recorder = recorder

    def objects(
        self,
        type: Optional[str] = None,
        where: Optional[dict[str, Any]] = None,
    ) -> list[Object]:
        out = super().objects(type=type, where=where)
        self._recorder.record_objects(out)
        return out


def context_read_payload(
    *,
    behavior_name: str,
    event_id: str,
    execution_event_id: str,
    recorder: ReadRecorder,
) -> dict[str, Any]:
    """Build the ``context.read`` payload for one committed execution.

    ``execution_event_id`` is the id of the execution's
    ``behavior.started`` / ``relation_behavior.started`` event — the
    frame reference that ties the read set to one specific invocation.
    ``object_ids`` is capped at :data:`CONTEXT_READ_ID_CAP`; ``count``
    is always the exact deduplicated total, and ``truncated`` appears
    (as ``True``) only when ids were dropped.
    """
    ids = recorder.object_ids
    payload: dict[str, Any] = {
        "behavior": behavior_name,
        "event_id": event_id,
        "execution_event_id": execution_event_id,
        "object_ids": ids[:CONTEXT_READ_ID_CAP],
        "count": len(ids),
    }
    if len(ids) > CONTEXT_READ_ID_CAP:
        payload["truncated"] = True
    return payload


__all__ = [
    "CONTEXT_READ_ID_CAP",
    "ReadRecorder",
    "TracedView",
    "context_read_payload",
]
