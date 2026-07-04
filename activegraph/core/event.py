"""Event records. CONTRACT #3: append-only, never modified.

Events are dataclasses, frozen at the Python level. Their `payload` dict is
not deeply frozen — by convention nothing mutates it after `emit`. The runtime
treats the event log as the source of truth (CONTRACT #2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Event:
    """One immutable record in the append-only log. CONTRACT #3.

    An event is a fact: ``type`` names what happened, ``payload``
    carries the data, ``actor`` says who caused it, ``caused_by``
    links the causal parent event, and ``frame_id`` scopes it to a
    mission frame. Events are never modified after ``emit`` —
    objects, relations, patches, and views are all projections
    derived from the sequence of these records (CONTRACT #2).
    """

    id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: Optional[str] = None
    frame_id: Optional[str] = None
    caused_by: Optional[str] = None
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "payload": self.payload,
            "actor": self.actor,
            "frame_id": self.frame_id,
            "caused_by": self.caused_by,
            "timestamp": self.timestamp,
        }
