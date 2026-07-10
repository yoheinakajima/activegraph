"""Append-only JSONL EventSink adapter. CONTRACT v1.8 #5."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TextIO

from activegraph.core.event import Event
from activegraph.sinks.base import DeliveryContext
from activegraph.store.serde import encode_payload


class JSONLEventSink:
    """Write one canonical context/event envelope per UTF-8 JSONL line.

    Files are opened in append mode and never rotated or truncated.  The
    adapter is thread-safe and reference-counts worker ``open`` / ``close``
    calls so one instance can be shared by concurrent run attachments.
    Concurrent writers to one path must share that instance; distinct adapter
    instances and processes do not coordinate a path-level lock. Store serde
    normalization is reused for Decimal, date/datetime, set, and Unicode
    payload values before keys are sorted into canonical compact JSON.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._file: TextIO | None = None
        self._open_count = 0

    def open(self) -> None:
        """Open the target in append mode, sharing one file across workers."""

        with self._lock:
            if self._file is None:
                self._file = self.path.open("a", encoding="utf-8", newline="\n")
            self._open_count += 1

    def on_event(self, event: Event, context: DeliveryContext) -> None:
        """Append one stable JSON envelope followed by exactly one newline."""

        envelope = {
            "context": context.to_dict(),
            "event": event.to_dict(),
        }
        # encode_payload is the EventStore normalization authority.  Decode
        # once to JSON primitives, then apply the JSONL adapter's canonical
        # key ordering and compact separators.
        normalized = json.loads(encode_payload(envelope))
        line = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            if self._file is None:
                raise RuntimeError("JSONLEventSink.on_event() called before open()")
            self._file.write(line + "\n")

    def flush(self) -> None:
        """Flush buffered JSONL bytes when the adapter is open."""

        with self._lock:
            if self._file is not None:
                self._file.flush()

    def close(self) -> None:
        """Release one attachment reference and close after the last one."""

        with self._lock:
            if self._open_count == 0:
                return
            self._open_count -= 1
            if self._open_count == 0 and self._file is not None:
                file = self._file
                self._file = None
                errors: list[Exception] = []
                try:
                    file.flush()
                except Exception as exc:
                    errors.append(exc)
                try:
                    file.close()
                except Exception as exc:
                    errors.append(exc)
                if len(errors) == 1:
                    raise errors[0]
                if errors:
                    raise ExceptionGroup(
                        "JSONLEventSink flush and close both failed",
                        errors,
                    )


__all__ = ["JSONLEventSink"]
