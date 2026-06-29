"""Construct a View for a behavior invocation. CONTRACT #11."""

from __future__ import annotations

from typing import Any, Optional

from activegraph.behaviors.base import Behavior, RelationBehavior
from activegraph.core.event import Event
from activegraph.core.graph import Graph
from activegraph.core.view import View


DEFAULT_RECENT_EVENTS = 50


def build_view(
    behavior: Behavior | RelationBehavior, event: Event, graph: Graph
) -> View:
    spec = behavior.view_spec
    if not spec:
        return View(
            objects=graph.all_objects(),
            relations=graph.all_relations(),
            events=graph.events[-DEFAULT_RECENT_EVENTS:],
        )

    around_path = spec.get("around")
    depth = spec.get("depth", 1)
    include_types: Optional[list[str]] = spec.get("include_types")
    recent_events: int = spec.get("recent_events", DEFAULT_RECENT_EVENTS)

    if around_path:
        center_id = _resolve_event_path(around_path, event)
        objs, rels = graph.neighborhood(center_id, depth=depth) if center_id else ([], [])
        if include_types:
            type_set = set(include_types)
            objs = [o for o in objs if o.type in type_set]
    elif include_types:
        # No anchor: push the type filter into the store (FalkorDB resolves it
        # as ``type IN [...]`` instead of materializing every object). The
        # default single-pass scan preserves the same order as a full scan.
        objs = graph.objects_in_types(include_types)
        rels = graph.all_relations()
    else:
        objs = graph.all_objects()
        rels = graph.all_relations()

    return View(
        objects=objs,
        relations=rels,
        events=graph.events[-recent_events:] if recent_events > 0 else [],
    )


def _resolve_event_path(expr: str, event: Event) -> Any:
    """Resolve `event.payload.object.id` against an Event."""
    parts = expr.split(".")
    if not parts or parts[0] != "event":
        return None
    cur: Any = event
    for p in parts[1:]:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif hasattr(cur, p):
            cur = getattr(cur, p)
        else:
            return None
        if cur is None:
            return None
    return cur
