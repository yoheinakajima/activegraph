"""Runtime introspection — RuntimeStatus and friends. CONTRACT v0.8 #11.

``runtime.status(recent=N)`` returns a ``RuntimeStatus``: a frozen
snapshot. Cheap to call (no graph traversal, no event log scan), safe
from anywhere, returns immutable data. The CLI's ``inspect`` command is
a thin wrapper around this.

There is no ``last_error`` field. Errors are events; filter
``recent_events`` for type ``behavior.failed``, or query the store for
a window-independent view. Convenience accessors that look the same as
the source of truth but mean different things are bug-bait.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional


RuntimeState = Literal["idle", "running", "stopped", "exhausted"]


@dataclass(frozen=True)
class BudgetSnapshot:
    used: dict[str, float]
    limits: dict[str, Optional[float]]
    cost_used_usd: str
    cost_limit_usd: Optional[str]
    exhausted_by: Optional[str]


@dataclass(frozen=True)
class FrameSnapshot:
    id: Optional[str]
    name: Optional[str]


@dataclass(frozen=True)
class BehaviorInfo:
    name: str
    kind: str  # "function" | "relation" | "llm"
    subscribed_to: tuple[str, ...]
    pattern: Optional[str] = None
    activate_after: Optional[int] = None


@dataclass(frozen=True)
class EventSummary:
    id: str
    type: str
    actor: Optional[str]
    timestamp: str


@dataclass(frozen=True)
class RuntimeStatus:
    """Point-in-time snapshot of a runtime for inspection surfaces.

    What ``activegraph status`` renders: run id, coarse ``state``,
    queue depth, events processed, a budget snapshot, the frame, the
    registered behaviors, and recent events. A read-only value object
    produced by ``Runtime.status()`` and serialized by
    ``status_to_dict`` for ``--json`` consumers.
    """

    run_id: str
    state: RuntimeState
    queue_depth: int
    events_processed: int
    budget: BudgetSnapshot
    frame: Optional[FrameSnapshot]
    registered_behaviors: tuple[BehaviorInfo, ...]
    recent_events: tuple[EventSummary, ...]


def status_to_dict(status: RuntimeStatus) -> dict[str, Any]:
    """Convert a RuntimeStatus to a plain JSON-serializable dict.

    Used by the CLI's --json flag. Field names match the documented
    schema; nested dataclasses become nested dicts.
    """
    result: dict[str, Any] = _asdict_with_tuples(status)
    return result


def _asdict_with_tuples(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _asdict_with_tuples(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_asdict_with_tuples(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _asdict_with_tuples(v) for k, v in obj.items()}
    return obj
