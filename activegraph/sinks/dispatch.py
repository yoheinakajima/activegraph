"""Isolated bounded worker dispatch for EventSink attachments.

The emit thread only calls the handle's private offer method. Adapter lifecycle and
delivery code run on one daemon worker per attachment (CONTRACT v1.8 #2).
"""

from __future__ import annotations

import copy
import threading
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from activegraph.core.event import Event
from activegraph.observability.metrics import Metrics
from activegraph.sinks.base import (
    DeliveryContext,
    EventSink,
    OverflowPolicy,
    SinkState,
    SinkStatus,
)


@dataclass(frozen=True)
class _Delivery:
    ticket: int
    event: Event
    context: DeliveryContext


@dataclass
class _Command:
    kind: Literal["flush", "close"]
    target_ticket: int
    done: threading.Event
    success: bool = False


@dataclass(frozen=True)
class _MetricBatch:
    delivered: int
    drops: tuple[tuple[str, int], ...]
    errors: tuple[tuple[str, int], ...]
    queue_depth: int | None


class SinkHandle:
    """Control and status handle for one EventSink attachment.

    Handles are returned by ``Graph.add_sink`` / ``Runtime.add_sink``.
    Delivery injection is runtime-internal; callers use ``status``, ``flush``,
    and ``close``.  Flush and close wait only at this explicit control-plane
    boundary and return ``False`` on timeout or adapter failure.
    """

    def __init__(
        self,
        sink: EventSink,
        *,
        name: str,
        run_id: str,
        queue_capacity: int,
        overflow_policy: OverflowPolicy,
        metrics: Metrics,
        _owner_close: Callable[[SinkHandle, float | None], bool] | None = None,
    ) -> None:
        if (
            not isinstance(queue_capacity, int)
            or isinstance(queue_capacity, bool)
            or queue_capacity < 1
        ):
            raise ValueError("queue_capacity must be a positive integer")
        self._sink = sink
        self._name = name
        self._run_id = run_id
        self._queue_capacity = queue_capacity
        self._overflow_policy = overflow_policy
        self._metrics = metrics
        self._owner_close = _owner_close

        self._condition = threading.Condition(threading.RLock())
        self._deliveries: deque[_Delivery] = deque()
        self._commands: deque[_Command] = deque()
        self._state = SinkState.OPENING
        self._accepting = True
        self._next_ticket = 1
        # Only accepted work that has not settled is tracked. This set is
        # bounded by queue_capacity + one in-flight callback even when
        # DROP_OLDEST evicts an arbitrarily long stream behind a hung event.
        self._unsettled_tickets: set[int] = set()
        self._enqueued = 0
        self._delivered = 0
        self._dropped = 0
        self._errors = 0
        self._dropped_by_reason: Counter[str] = Counter()
        self._last_error: str | None = None
        self._last_error_operation: str | None = None
        self._close_command: _Command | None = None
        self._stopped = threading.Event()
        # Metric publication is coalesced into a fixed-size batch and happens
        # only on this attachment's worker. Graph.emit therefore never calls
        # arbitrary Metrics code; a blocked backend can stall only this sink.
        self._pending_delivered = 0
        self._pending_drops: Counter[str] = Counter()
        self._pending_errors: Counter[str] = Counter()
        self._depth_dirty = True

        self._thread = threading.Thread(
            target=self._run,
            name=f"activegraph-sink-{name}",
            daemon=True,
        )
        self._thread.start()

    @property
    def name(self) -> str:
        """Return the immutable attachment name used by status and metrics."""

        return self._name

    @property
    def run_id(self) -> str:
        """Return the immutable run id associated with this attachment."""

        return self._run_id

    @property
    def queue_capacity(self) -> int:
        """Return the immutable maximum number of waiting deliveries."""

        return self._queue_capacity

    @property
    def overflow_policy(self) -> OverflowPolicy:
        """Return the immutable policy selected when the queue is full."""

        return self._overflow_policy

    def _offer(self, event: Event, context: DeliveryContext) -> bool:
        """Offer one accepted event without waiting for queue capacity.

        Returns ``True`` when the event entered the attachment queue and
        ``False`` when policy rejected it.  Adapter code is never invoked
        here, and all metric backend exceptions are swallowed.
        """

        dropped_reason: str | None = None
        enqueued = False
        with self._condition:
            if not self._accepting:
                return False
            else:
                if len(self._deliveries) >= self.queue_capacity:
                    if self.overflow_policy is OverflowPolicy.DROP_NEWEST:
                        dropped_reason = "overflow.drop_newest"
                        self._record_drop_locked(dropped_reason)
                    elif self.overflow_policy is OverflowPolicy.DROP_OLDEST:
                        evicted = self._deliveries.popleft()
                        self._unsettled_tickets.discard(evicted.ticket)
                        dropped_reason = "overflow.drop_oldest"
                        self._record_drop_locked(dropped_reason)
                    else:
                        dropped_reason = "overflow.fail_sink"
                        self._record_drop_locked(dropped_reason)
                        self._accepting = False
                        self._state = SinkState.FAILED
                        self._condition.notify_all()

                if self._accepting and (
                    dropped_reason is None
                    or self.overflow_policy is OverflowPolicy.DROP_OLDEST
                ):
                    ticket = self._next_ticket
                    self._next_ticket += 1
                    self._deliveries.append(
                        _Delivery(ticket=ticket, event=event, context=context)
                    )
                    self._unsettled_tickets.add(ticket)
                    self._enqueued += 1
                    self._depth_dirty = True
                    enqueued = True
                    self._condition.notify()
            if dropped_reason is not None:
                self._condition.notify_all()
        return enqueued

    def status(self) -> SinkStatus:
        """Return an immutable exact local status snapshot."""

        with self._condition:
            return SinkStatus(
                name=self.name,
                run_id=self.run_id,
                state=self._state,
                queue_capacity=self.queue_capacity,
                queue_depth=len(self._deliveries),
                overflow_policy=self.overflow_policy,
                enqueued=self._enqueued,
                delivered=self._delivered,
                dropped=self._dropped,
                errors=self._errors,
                dropped_by_reason=tuple(sorted(self._dropped_by_reason.items())),
                last_error=self._last_error,
                last_error_operation=self._last_error_operation,
            )

    def flush(self, timeout: float | None = 5.0) -> bool:
        """Flush deliveries accepted before this call within ``timeout``.

        The adapter's ``flush`` method executes on its worker after every
        earlier accepted delivery has either completed or been observably
        dropped.  A hanging adapter cannot make this method wait forever
        unless the caller explicitly passes ``timeout=None``.
        """

        with self._condition:
            if self._state is SinkState.CLOSED:
                return True
            if self._state is SinkState.FAILED:
                return False
            if self._state is SinkState.CLOSING:
                close_command = self._close_command
                if close_command is None:
                    return False
                command = close_command
            else:
                command = _Command(
                    kind="flush",
                    target_ticket=self._next_ticket - 1,
                    done=threading.Event(),
                )
                self._commands.append(command)
                self._condition.notify_all()
        if not command.done.wait(timeout):
            if command.kind == "flush":
                with self._condition:
                    try:
                        self._commands.remove(command)
                    except ValueError:
                        # The worker already claimed it; at most that one
                        # in-flight adapter.flush remains after timeout.
                        pass
            return False
        return command.success

    def close(self, timeout: float | None = 5.0) -> bool:
        """Drain and close this attachment within ``timeout``.

        A graph-owned handle first detaches through its owning Graph, so direct
        handle closure cannot strand a dead attachment in live fan-out.
        Closing is idempotent. On timeout the daemon worker is left alive and
        status remains ``closing`` (or ``failed``); Python cannot safely cancel
        arbitrary adapter code already executing in another thread.
        """

        owner_close = self._owner_close
        if owner_close is not None:
            return owner_close(self, timeout)
        with self._condition:
            if self._state is SinkState.CLOSED:
                return True
            if self._state is SinkState.FAILED and self._stopped.is_set():
                return False
        return self._close_worker(timeout)

    def _close_worker(self, timeout: float | None = 5.0) -> bool:
        """Close only this worker; the owning Graph performs detachment."""

        with self._condition:
            if self._state is SinkState.CLOSED:
                return True
            if self._close_command is None and self._state is not SinkState.FAILED:
                self._accepting = False
                self._state = SinkState.CLOSING
                self._close_command = _Command(
                    kind="close",
                    target_ticket=self._next_ticket - 1,
                    done=threading.Event(),
                )
                self._commands.append(self._close_command)
                self._condition.notify_all()
            command = self._close_command

        if command is None:
            # A failed worker either exits on its own after draining or is
            # stuck in adapter code.  Waiting on the stopped flag is bounded.
            self._stopped.wait(timeout)
            return False
        if not command.done.wait(timeout):
            return False
        return command.success

    def _is_terminal(self) -> bool:
        """Return whether no further worker progress is possible/required."""

        with self._condition:
            if self._state is SinkState.CLOSED:
                return True
            if self._close_command is not None and self._close_command.done.is_set():
                return True
        return self._stopped.is_set()

    def _run(self) -> None:
        opened = False
        try:
            try:
                self._sink.open()
                opened = True
            except Exception as exc:
                self._record_error("open", exc)
                self._fail_open()
                return

            with self._condition:
                if self._state is SinkState.OPENING:
                    self._state = SinkState.RUNNING
                self._condition.notify_all()

            while True:
                delivery: _Delivery | None = None
                command: _Command | None = None
                metric_batch: _MetricBatch | None = None
                should_fail_close = False
                with self._condition:
                    while (
                        delivery is None
                        and command is None
                        and metric_batch is None
                    ):
                        if (
                            self._commands
                            and self._command_ready_locked(self._commands[0])
                        ):
                            command = self._commands.popleft()
                            break
                        if self._deliveries:
                            delivery = self._deliveries.popleft()
                            self._depth_dirty = True
                            break
                        if self._state is SinkState.FAILED:
                            should_fail_close = True
                            break
                        metric_batch = self._take_metric_batch_locked()
                        if metric_batch is not None:
                            break
                        self._condition.wait()
                    if metric_batch is None:
                        metric_batch = self._take_metric_batch_locked()
                self._publish_metric_batch(metric_batch)
                if should_fail_close:
                    self._close_after_failure(opened=opened)
                    return
                if delivery is not None:
                    self._deliver(delivery)
                    continue
                if command is not None and self._execute_command(command):
                    return
        finally:
            self._stopped.set()

    def _deliver(self, delivery: _Delivery) -> None:
        try:
            isolated = Event(
                id=delivery.event.id,
                type=delivery.event.type,
                payload=copy.deepcopy(delivery.event.payload),
                actor=delivery.event.actor,
                frame_id=delivery.event.frame_id,
                caused_by=delivery.event.caused_by,
                timestamp=delivery.event.timestamp,
            )
            self._sink.on_event(isolated, delivery.context)
        except Exception as exc:
            self._record_error("on_event", exc)
        else:
            with self._condition:
                self._delivered += 1
                self._pending_delivered += 1
        finally:
            with self._condition:
                self._unsettled_tickets.discard(delivery.ticket)
                self._condition.notify_all()

    def _execute_command(self, command: _Command) -> bool:
        if command.kind == "flush":
            try:
                self._sink.flush()
            except Exception as exc:
                self._record_error("flush", exc)
                command.success = False
            else:
                command.success = True
            finally:
                self._publish_pending_metrics()
                command.done.set()
            return False

        success = True
        try:
            self._sink.flush()
        except Exception as exc:
            self._record_error("flush", exc)
            success = False
        try:
            self._sink.close()
        except Exception as exc:
            self._record_error("close", exc)
            success = False
        with self._condition:
            self._accepting = False
            self._depth_dirty = True
        self._publish_pending_metrics()
        with self._condition:
            self._state = SinkState.CLOSED if success else SinkState.FAILED
            self._complete_pending_commands_locked(success=False)
            self._condition.notify_all()
        command.success = success
        command.done.set()
        return True

    def _fail_open(self) -> None:
        with self._condition:
            self._state = SinkState.FAILED
            self._accepting = False
            while self._deliveries:
                delivery = self._deliveries.popleft()
                self._unsettled_tickets.discard(delivery.ticket)
                self._record_drop_locked("sink.open_failed")
            self._depth_dirty = True
            self._condition.notify_all()
        # open() may have acquired resources before raising; close remains
        # the adapter's idempotent best-effort cleanup hook.
        try:
            self._sink.close()
        except Exception as exc:
            self._record_error("close", exc)
        self._publish_pending_metrics()
        with self._condition:
            self._complete_pending_commands_locked(success=False)
            self._condition.notify_all()

    def _close_after_failure(self, *, opened: bool) -> None:
        if opened:
            try:
                self._sink.flush()
            except Exception as exc:
                self._record_error("flush", exc)
            try:
                self._sink.close()
            except Exception as exc:
                self._record_error("close", exc)
        with self._condition:
            self._state = SinkState.FAILED
            self._accepting = False
            self._depth_dirty = True
            self._condition.notify_all()
        self._publish_pending_metrics()
        with self._condition:
            self._complete_pending_commands_locked(success=False)
            self._condition.notify_all()

    def _record_drop_locked(self, reason: str) -> None:
        self._dropped += 1
        self._dropped_by_reason[reason] += 1
        self._pending_drops[reason] += 1

    def _record_error(self, operation: str, exc: Exception) -> None:
        with self._condition:
            self._errors += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._last_error_operation = operation
            self._pending_errors[operation] += 1
            self._condition.notify_all()

    def _command_ready_locked(self, command: _Command) -> bool:
        return not any(
            ticket <= command.target_ticket
            for ticket in self._unsettled_tickets
        )

    def _complete_pending_commands_locked(self, *, success: bool) -> None:
        while self._commands:
            command = self._commands.popleft()
            command.success = success
            command.done.set()

    def _take_metric_batch_locked(self) -> _MetricBatch | None:
        if (
            self._pending_delivered == 0
            and not self._pending_drops
            and not self._pending_errors
            and not self._depth_dirty
        ):
            return None
        batch = _MetricBatch(
            delivered=self._pending_delivered,
            drops=tuple(sorted(self._pending_drops.items())),
            errors=tuple(sorted(self._pending_errors.items())),
            queue_depth=(len(self._deliveries) if self._depth_dirty else None),
        )
        self._pending_delivered = 0
        self._pending_drops.clear()
        self._pending_errors.clear()
        self._depth_dirty = False
        return batch

    def _publish_pending_metrics(self) -> None:
        with self._condition:
            batch = self._take_metric_batch_locked()
        self._publish_metric_batch(batch)

    def _publish_metric_batch(self, batch: _MetricBatch | None) -> None:
        if batch is None:
            return
        if batch.delivered:
            self._safe_counter(
                "activegraph_sink_events_delivered_total",
                {"sink": self.name},
                float(batch.delivered),
            )
        for reason, count in batch.drops:
            self._safe_counter(
                "activegraph_sink_events_dropped_total",
                {"sink": self.name, "reason": reason},
                float(count),
            )
        for operation, count in batch.errors:
            self._safe_counter(
                "activegraph_sink_errors_total",
                {"sink": self.name, "operation": operation},
                float(count),
            )
        if batch.queue_depth is not None:
            self._safe_gauge(
                "activegraph_sink_queue_depth",
                {"sink": self.name, "run_id": self.run_id},
                float(batch.queue_depth),
            )

    def _safe_counter(
        self, name: str, tags: dict[str, str], value: float
    ) -> None:
        try:
            self._metrics.counter(name, tags, value)
        except Exception:
            return

    def _safe_gauge(
        self, name: str, tags: dict[str, str], value: float
    ) -> None:
        try:
            self._metrics.gauge(name, tags, value)
        except Exception:
            return


__all__ = ["SinkHandle"]
