"""Run-local, log-backed developer override receipts. CONTRACT v1.8 #13–#15."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from activegraph.core.event import Event


DEV_AUTHORITIES = ("R0", "R1", "R2", "R3")
_AUTHORITY_RANK = {authority: rank for rank, authority in enumerate(DEV_AUTHORITIES)}
_FORBIDDEN_EXACT_GATES = {
    "event_log",
    "event.logging",
    "event.log",
    "logging",
    "log",
}


@dataclass(frozen=True)
class DevOverride:
    """One accepted ``dev.override`` receipt scoped to a run and gate.

    The receipt is evidence a local operator recorded an exact bypass intent;
    it grants nothing until that same gate validates it through the runtime.
    """

    event_id: str
    run_id: str
    actor: str
    reason: str
    target_gate: str
    scope: str
    resulting_authority: str


def validate_override_request(
    *,
    actor: str,
    reason: str,
    target_gate: str,
    scope: str,
    resulting_authority: str,
) -> None:
    """Reject broad, untraceable, promotion, logging, or R4 requests."""

    for name, value in (
        ("actor", actor),
        ("reason", reason),
        ("target_gate", target_gate),
        ("scope", scope),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"dev override {name} must be a non-empty string")
    if resulting_authority not in DEV_AUTHORITIES:
        raise ValueError(
            "dev override resulting_authority must be one of R0, R1, R2, R3; "
            "R4 governance authority is never available"
        )
    if gate_is_forbidden(target_gate):
        raise ValueError(
            f"dev override cannot target non-bypassable gate {target_gate!r}"
        )


def gate_is_forbidden(target_gate: str) -> bool:
    """Whether a gate belongs to promotion or event-log authority."""

    normalized = target_gate.strip().lower()
    return (
        normalized in _FORBIDDEN_EXACT_GATES
        or normalized == "promote"
        or normalized.startswith("promote.")
        or normalized == "promotion"
        or normalized.startswith("promotion.")
        or normalized.startswith("event_log.")
        or normalized.startswith("event.logging.")
    )


def receipt_from_event(event: Event, *, run_id: str) -> Optional[DevOverride]:
    """Hydrate one valid receipt from an accepted event, else ``None``."""

    if event.type != "dev.override":
        return None
    payload = event.payload
    actor = payload.get("actor")
    reason = payload.get("reason")
    target_gate = payload.get("target_gate")
    scope = payload.get("scope")
    resulting_authority = payload.get("resulting_authority")
    if (
        not isinstance(actor, str)
        or not isinstance(reason, str)
        or not isinstance(target_gate, str)
        or not isinstance(scope, str)
        or not isinstance(resulting_authority, str)
    ):
        return None
    if event.actor != actor:
        return None
    try:
        validate_override_request(
            actor=actor,
            reason=reason,
            target_gate=target_gate,
            scope=scope,
            resulting_authority=resulting_authority,
        )
    except ValueError:
        return None
    return DevOverride(
        event_id=event.id,
        run_id=run_id,
        actor=actor,
        reason=reason,
        target_gate=target_gate,
        scope=scope,
        resulting_authority=resulting_authority,
    )


def authority_allows(granted: str, required: str) -> bool:
    """Whether a local grant covers ``required`` without reaching R4."""

    granted_rank = _AUTHORITY_RANK.get(granted)
    required_rank = _AUTHORITY_RANK.get(required)
    return (
        granted_rank is not None
        and required_rank is not None
        and required_rank <= granted_rank
    )


__all__ = ["DEV_AUTHORITIES", "DevOverride"]
