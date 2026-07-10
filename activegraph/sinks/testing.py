"""Thread-safe EventSink test doubles for applications and conformance tests."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass

from activegraph.core.event import Event
from activegraph.sinks.base import DeliveryContext


@dataclass(frozen=True)
class RecordedDelivery:
    """One event/context pair captured by :class:`RecordingSink`.

    Both values are isolated snapshots, suitable for deterministic assertions
    after the live runtime and its sink workers have continued or closed.
    """

    event: Event
    context: DeliveryContext


class RecordingSink:
    """Thread-safe in-memory EventSink for downstream tests.

    One instance may be shared across concurrent runtimes.  ``deliveries``
    returns an immutable snapshot; ``wait_for`` provides deterministic test
    synchronization without sleeps.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._deliveries: list[RecordedDelivery] = []
        self._open_count = 0

    def open(self) -> None:
        """Register one worker attachment."""

        with self._condition:
            self._open_count += 1

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        """Capture a defensive event copy and wake waiting tests."""

        recorded = RecordedDelivery(
            event=Event(
                id=event.id,
                type=event.type,
                payload=copy.deepcopy(event.payload),
                actor=event.actor,
                frame_id=event.frame_id,
                caused_by=event.caused_by,
                timestamp=event.timestamp,
            ),
            context=context,
        )
        with self._condition:
            self._deliveries.append(recorded)
            self._condition.notify_all()

    def flush(self) -> None:
        """No-op: in-memory deliveries are visible immediately."""

        return

    def close(self) -> None:
        """Release one worker attachment; repeated closes are harmless."""

        with self._condition:
            if self._open_count:
                self._open_count -= 1

    @property
    def deliveries(self) -> tuple[RecordedDelivery, ...]:
        """Return a stable snapshot of every captured delivery."""

        with self._condition:
            return tuple(copy.deepcopy(self._deliveries))

    @property
    def events(self) -> tuple[Event, ...]:
        """Return only the captured events, in delivery order."""

        return tuple(delivery.event for delivery in self.deliveries)

    def wait_for(self, count: int, timeout: float | None = 5.0) -> bool:
        """Wait until at least ``count`` deliveries exist, returning success."""

        if count < 0:
            raise ValueError("count must not be negative")
        with self._condition:
            return self._condition.wait_for(
                lambda: len(self._deliveries) >= count,
                timeout=timeout,
            )

    def clear(self) -> None:
        """Remove captured deliveries without changing attachment lifecycle."""

        with self._condition:
            self._deliveries.clear()


__all__ = ["RecordedDelivery", "RecordingSink"]
