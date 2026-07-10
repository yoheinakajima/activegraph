"""Public EventSink protocol and delivery configuration values.

CONTRACT v1.8: sinks observe already-accepted events.  They never run on
the graph's emit thread and have no graph handle or behavioral return path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol, runtime_checkable

from activegraph.core.event import Event


DeliveryMode = Literal["live", "replay_export"]


class OverflowPolicy(str, Enum):
    """Non-blocking policy used when one sink's private queue is full.

    ``DROP_NEWEST`` preserves the queued prefix, ``DROP_OLDEST`` keeps the
    newest observation by evicting the oldest waiting item, and
    ``FAIL_SINK`` disables the attachment after counting the overflow.
    None of the policies waits for capacity on the runtime hot path.
    """

    DROP_NEWEST = "drop_newest"
    DROP_OLDEST = "drop_oldest"
    FAIL_SINK = "fail_sink"


class SinkState(str, Enum):
    """Lifecycle state reported for one sink worker.

    Opening and running attachments accept offers; closing attachments are
    detached. Closed and failed are terminal worker outcomes; graph status
    retains terminal failures for diagnosis.
    """

    OPENING = "opening"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True)
class DeliveryContext:
    """Immutable metadata accompanying one event offered to a sink.

    ``sequence`` is the event's one-based position in the graph's currently
    materialized accepted log.  ``mode`` is ``"live"`` for the R1 dispatch
    path; ``"replay_export"`` is reserved for a future explicit historical
    export API and is never produced by normal load, fork, or strict replay.
    """

    run_id: str
    sequence: int
    mode: DeliveryMode = "live"

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("DeliveryContext.run_id must not be empty")
        if self.sequence < 1:
            raise ValueError("DeliveryContext.sequence must be at least 1")
        if self.mode not in ("live", "replay_export"):
            raise ValueError(
                "DeliveryContext.mode must be 'live' or 'replay_export'"
            )

    def to_dict(self) -> dict[str, str | int]:
        """Return the stable JSON-ready delivery metadata shape."""

        return {
            "mode": self.mode,
            "run_id": self.run_id,
            "sequence": self.sequence,
        }


@runtime_checkable
class EventSink(Protocol):
    """Outbound observer of accepted runtime events.

    The dispatcher invokes all four methods on the attachment's dedicated
    worker.  Implementations must not rely on return values influencing the
    runtime: every return is ignored, and every exception is isolated and
    exposed through sink status/metrics instead of crossing into execution.
    """

    def open(self) -> None:
        """Acquire adapter resources before the first delivery."""

        ...

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        """Observe one isolated event copy and its delivery metadata."""

        ...

    def flush(self) -> None:
        """Make all previously handled deliveries externally visible."""

        ...

    def close(self) -> None:
        """Release adapter resources; implementations should be idempotent."""

        ...


@dataclass(frozen=True)
class SinkConfig:
    """Configuration for one isolated EventSink attachment.

    A capacity of 1024 and ``DROP_NEWEST`` are the locked defaults.  Supply
    ``name`` when attaching two instances of the same sink class to one
    graph; attachment names are unique status keys and metric label values.
    """

    sink: EventSink
    name: str | None = None
    queue_capacity: int = 1024
    overflow_policy: OverflowPolicy = OverflowPolicy.DROP_NEWEST

    def __post_init__(self) -> None:
        if self.name is not None and not self.name.strip():
            raise ValueError("SinkConfig.name must not be empty")
        if (
            not isinstance(self.queue_capacity, int)
            or isinstance(self.queue_capacity, bool)
            or self.queue_capacity < 1
        ):
            raise ValueError(
                "SinkConfig.queue_capacity must be a positive integer"
            )
        if not isinstance(self.overflow_policy, OverflowPolicy):
            object.__setattr__(
                self,
                "overflow_policy",
                OverflowPolicy(self.overflow_policy),
            )


@dataclass(frozen=True)
class SinkStatus:
    """Point-in-time health and loss accounting for one sink attachment.

    Counts remain available with ``NoOpMetrics`` and are therefore the exact
    local observability surface.  ``queue_depth`` excludes an item currently
    executing inside ``on_event``.  ``dropped_by_reason`` is sorted so status
    snapshots and JSON renderings are deterministic.
    """

    name: str
    run_id: str
    state: SinkState
    queue_capacity: int
    queue_depth: int
    overflow_policy: OverflowPolicy
    enqueued: int
    delivered: int
    dropped: int
    errors: int
    dropped_by_reason: tuple[tuple[str, int], ...]
    last_error: str | None
    last_error_operation: str | None


__all__ = [
    "DeliveryContext",
    "DeliveryMode",
    "EventSink",
    "OverflowPolicy",
    "SinkConfig",
    "SinkState",
    "SinkStatus",
]
