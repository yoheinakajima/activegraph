"""Outbound accepted-event observation interfaces and first-party sinks."""

from activegraph.sinks.base import (
    DeliveryContext,
    DeliveryMode,
    EventSink,
    OverflowPolicy,
    SinkConfig,
    SinkState,
    SinkStatus,
)
from activegraph.sinks.dispatch import SinkHandle
from activegraph.sinks.jsonl import JSONLEventSink
from activegraph.sinks.testing import RecordedDelivery, RecordingSink

__all__ = [
    "DeliveryContext",
    "DeliveryMode",
    "EventSink",
    "JSONLEventSink",
    "OverflowPolicy",
    "RecordedDelivery",
    "RecordingSink",
    "SinkConfig",
    "SinkHandle",
    "SinkState",
    "SinkStatus",
]
