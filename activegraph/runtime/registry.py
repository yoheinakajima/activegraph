"""Match events to behaviors. CONTRACT #10: registration order for ties.

v0.7 (CONTRACT v0.7 #11): a behavior with both `on=[...]` and
`pattern=...` requires BOTH conditions — the event type matches AND
the pattern matches against the post-event graph state. A behavior
with only `pattern=...` (empty `on`) matches on every non-lifecycle
event.

`match()` returns `(behavior, relations, matches)` triples. The
`matches` list is the pattern matcher's bindings (empty for
behaviors without `pattern=`). The runtime forwards `matches` as
`ctx.matches`.
"""

from __future__ import annotations

from typing import Any, Iterable, Union

from activegraph.behaviors.base import Behavior, RelationBehavior
from activegraph.core.event import Event
from activegraph.core.graph import Graph, Relation, evaluate_where


BehaviorLike = Union[Behavior, RelationBehavior]


class Registry:
    def __init__(self, behaviors: Iterable[BehaviorLike]) -> None:
        self._behaviors: list[BehaviorLike] = list(behaviors)

    def all(self) -> list[BehaviorLike]:
        return list(self._behaviors)

    def index_of(self, behavior: BehaviorLike) -> int:
        for i, b in enumerate(self._behaviors):
            if b is behavior:
                return i
        return -1

    def match(
        self, event: Event, graph: Graph
    ) -> list[tuple[BehaviorLike, list[Relation], list[Any]]]:
        """Return (behavior, matching_relations, pattern_matches) triples
        in registration order. Pattern matches are empty for behaviors
        without `pattern=`.
        """
        out: list[tuple[BehaviorLike, list[Relation], list[Any]]] = []
        for b in self._behaviors:
            # Event-type filter: required only when `on=` is non-empty.
            # Pattern-only behaviors (empty `on`) skip this gate.
            if b.on and event.type not in b.on:
                continue
            # Suppress lifecycle events for pattern-only behaviors so a
            # pattern doesn't fire on behavior.started, etc.
            if not b.on and _is_lifecycle(event):
                continue
            pattern_matches: list[Any] = []
            if b.pattern_matcher is not None:
                pattern_matches = b.pattern_matcher.matches(event, graph)
                if not pattern_matches:
                    continue
            if isinstance(b, RelationBehavior):
                rels = _matching_relations(b, event, graph)
                if rels:
                    out.append((b, rels, pattern_matches))
            else:
                if b.where and not evaluate_where(b.where, event.payload):
                    continue
                out.append((b, [], pattern_matches))
        return out


def _is_lifecycle(event: Event) -> bool:
    return (
        event.type.startswith("behavior.")
        or event.type.startswith("relation_behavior.")
        or event.type.startswith("runtime.")
        or event.type.startswith("llm.")
        or event.type.startswith("tool.")
    )


def _matching_relations(
    rb: RelationBehavior, event: Event, graph: Graph
) -> list[Relation]:
    # Push the relation-type filter down to the store (find_relations) instead
    # of scanning every edge in Python; the reference/where checks stay here.
    candidates = graph.relations(type=rb.relation_type)
    referenced = _collect_string_values(event.payload)
    out: list[Relation] = []
    for r in candidates:
        if r.source in referenced or r.target in referenced:
            if rb.where and not evaluate_where(rb.where, event.payload):
                continue
            out.append(r)
    return out


def _collect_string_values(obj) -> set[str]:
    out: set[str] = set()
    _walk(obj, out)
    return out


def _walk(obj, out: set[str]) -> None:
    if isinstance(obj, str):
        out.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)
