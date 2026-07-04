"""Clock abstraction. CONTRACT #8: behaviors get time via ctx.clock."""

from __future__ import annotations

from datetime import datetime, timezone


class Clock:
    """Real wall-clock UTC. ISO 8601 second precision, Z suffix.

    The default time source for event timestamps. The runtime reads
    time only through this interface, so deterministic runs swap in
    :class:`FrozenClock` or :class:`TickingClock` and replay never
    depends on the machine clock.
    """

    def now(self) -> str:
        return (
            datetime.now(tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )


class FrozenClock(Clock):
    """Always returns the same timestamp. For tests and snapshots.

    Every ``now()`` call yields the constructor's ``t`` unchanged, so
    an event log written under a FrozenClock is byte-for-byte
    reproducible. Use :class:`TickingClock` when a test needs ordering
    across timestamps rather than strict equality.
    """

    def __init__(self, t: str = "2026-05-15T10:32:01Z") -> None:
        self._t = t

    def now(self) -> str:
        return self._t


class TickingClock(Clock):
    """Monotonically advances by ``step`` seconds on every call.

    For tests that care about ordering but don't want wall-clock
    noise: timestamps increase deterministically from the start value,
    so before/after assertions hold without sleeping or freezing time
    entirely.
    """

    def __init__(
        self,
        start: str = "2026-05-15T10:32:01Z",
        step_seconds: int = 1,
    ) -> None:
        self._t = datetime.fromisoformat(start.replace("Z", "+00:00"))
        self._step = step_seconds

    def now(self) -> str:
        out = self._t.isoformat(timespec="seconds").replace("+00:00", "Z")
        from datetime import timedelta

        self._t = self._t + timedelta(seconds=self._step)
        return out
