"""The canonical action-class authority evaluation. CONTRACT v1.9 #1–#3.

ADR 0016 (activegraph-vision) at the runtime boundary: ``action_class``
with the closed set ``R0 | R1 | R2 | R3 | R4`` is the canonical field
for authority decisions. This module holds the pure decision logic; the
:class:`~activegraph.runtime.runtime.Runtime` methods
(``authority_ceiling`` / ``set_authority_ceiling`` /
``evaluate_capability_authority``) wrap it with the event log.

Invariants, restated from the contract because they are the point:

* The class set and the ceiling set are CLOSED. No value outside them
  ever evaluates to anything but fail-closed approval.
* There is NO mapping from the legacy ``risk_class`` vocabulary
  (``low|medium|high|critical``) to action classes — this module never
  sees a risk class, by construction.
* ``R4`` routes to the dedicated governance gate at every ceiling.
* ``R3`` requires approval at every ceiling.
* Only ``R0``–``R2`` are ever auto-eligible, and only up to the
  EFFECTIVE ceiling: the stricter of the instance ceiling and any
  caller-supplied per-capability ceiling. Local policy can lower, never
  raise.
* A missing or invalid ``action_class`` fails closed to approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


ACTION_CLASSES: tuple[str, ...] = ("R0", "R1", "R2", "R3", "R4")
"""The closed canonical consequence classes, lowest to highest."""

AUTHORITY_CEILINGS: tuple[str, ...] = ("none", "R0", "R1", "R2")
"""The closed automatic-ceiling values, lowest to highest.

``R3`` and ``R4`` are deliberately unrepresentable as ceilings: no
product request can make an outward or governance action routine
(CONTRACT v1.9 #2).
"""

DECISION_AUTO_APPROVE = "auto_approve"
DECISION_REQUIRE_APPROVAL = "require_approval"
DECISION_GOVERNANCE_GATE = "governance_gate"

_CLASS_RANK = {name: rank for rank, name in enumerate(ACTION_CLASSES)}
# Ceiling ranks share the class scale so "class <= ceiling" is one
# comparison: none sits below R0 (nothing auto-approves), R0..R2 align
# with their class ranks.
_CEILING_RANK = {"none": -1, "R0": 0, "R1": 1, "R2": 2}


@dataclass(frozen=True)
class AuthorityDecision:
    """One evaluated authority decision on the new action-class path.

    Mirrors the ``authority.decision`` audit payload (CONTRACT v1.9 #3):
    ``capability`` is the caller's capability key; ``action_class`` is
    the value exactly as declared (possibly ``""``); ``ceiling`` is the
    instance ceiling in force; ``capability_ceiling`` is the
    caller-supplied stricter local ceiling (or ``None``);
    ``effective_ceiling`` is the stricter of the two; ``matched_policy``
    names the rule that decided (the closed vocabulary in
    :func:`evaluate_action_authority`); ``decision`` is one of
    ``auto_approve | require_approval | governance_gate``; ``reason`` is
    the human-readable explanation. ``event_id`` is the accepted
    ``authority.decision`` event when the runtime emitted one (empty for
    a pure evaluation).
    """

    capability: str
    action_class: str
    ceiling: str
    capability_ceiling: Optional[str]
    effective_ceiling: str
    matched_policy: str
    decision: str
    reason: str
    event_id: str = ""

    @property
    def auto_approved(self) -> bool:
        """Whether this decision grants automatic execution."""
        return self.decision == DECISION_AUTO_APPROVE


def validate_ceiling(value: str) -> None:
    """Reject a ceiling value outside ``none | R0 | R1 | R2``.

    Raises ``ValueError`` naming the closed set — used by the explicit
    ``set_authority_ceiling`` API, where a bad value is a caller error
    that must be loud (unlike evaluation-time inputs, which fail closed).
    """
    if value not in AUTHORITY_CEILINGS:
        raise ValueError(
            f"authority ceiling must be one of {' | '.join(AUTHORITY_CEILINGS)}; "
            f"got {value!r}. R3 and R4 are never valid ceilings — outward "
            f"and governance actions cannot be made routine (CONTRACT v1.9 #2)."
        )


def evaluate_action_authority(
    *,
    capability: str,
    action_class: str,
    ceiling: str,
    capability_ceiling: Optional[str] = None,
) -> AuthorityDecision:
    """The one policy decision on the new authority path, as a pure function.

    Evaluation order is FIXED (CONTRACT v1.9 #2):

    1. missing/invalid ``action_class`` (or an invalid
       ``capability_ceiling``) → ``require_approval``, fail closed;
    2. ``R4`` → ``governance_gate``, always;
    3. ``R3`` → ``require_approval``, always;
    4. ``R0``–``R2`` → ``auto_approve`` iff the class rank is at or
       below the effective ceiling (the stricter of ``ceiling`` and
       ``capability_ceiling``), else ``require_approval`` with the rule
       that blocked it (``above_ceiling`` / ``stricter_local_policy``).

    ``ceiling`` must already be a valid ceiling value — the runtime only
    ever passes a value that went through :func:`validate_ceiling`. The
    legacy ``risk_class`` vocabulary is not an input here and never
    participates.
    """
    validate_ceiling(ceiling)

    if not action_class:
        return AuthorityDecision(
            capability=capability,
            action_class=action_class,
            ceiling=ceiling,
            capability_ceiling=capability_ceiling,
            effective_ceiling=ceiling,
            matched_policy="fail_closed_missing_action_class",
            decision=DECISION_REQUIRE_APPROVAL,
            reason=(
                "no canonical action_class is declared for this capability; "
                "capabilities without one are ineligible for earned "
                "auto-approval (ADR 0016) — routed to approval"
            ),
        )
    if action_class not in _CLASS_RANK:
        return AuthorityDecision(
            capability=capability,
            action_class=action_class,
            ceiling=ceiling,
            capability_ceiling=capability_ceiling,
            effective_ceiling=ceiling,
            matched_policy="fail_closed_invalid_action_class",
            decision=DECISION_REQUIRE_APPROVAL,
            reason=(
                f"action_class {action_class!r} is outside the closed set "
                f"{' | '.join(ACTION_CLASSES)}; failing closed to approval"
            ),
        )
    if capability_ceiling is not None and capability_ceiling not in _CEILING_RANK:
        return AuthorityDecision(
            capability=capability,
            action_class=action_class,
            ceiling=ceiling,
            capability_ceiling=capability_ceiling,
            effective_ceiling=ceiling,
            matched_policy="fail_closed_invalid_capability_ceiling",
            decision=DECISION_REQUIRE_APPROVAL,
            reason=(
                f"capability_ceiling {capability_ceiling!r} is outside the "
                f"closed set {' | '.join(AUTHORITY_CEILINGS)}; a garbled "
                f"local policy must never widen anything — failing closed"
            ),
        )

    if action_class == "R4":
        return AuthorityDecision(
            capability=capability,
            action_class=action_class,
            ceiling=ceiling,
            capability_ceiling=capability_ceiling,
            effective_ceiling=ceiling,
            matched_policy="governance_gate_r4",
            decision=DECISION_GOVERNANCE_GATE,
            reason=(
                "R4 governance actions always use the dedicated governance "
                "gate; no ceiling or level makes them routine"
            ),
        )
    if action_class == "R3":
        return AuthorityDecision(
            capability=capability,
            action_class=action_class,
            ceiling=ceiling,
            capability_ceiling=capability_ceiling,
            effective_ceiling=ceiling,
            matched_policy="approval_required_r3",
            decision=DECISION_REQUIRE_APPROVAL,
            reason=(
                "R3 outward actions require approval at every ceiling and "
                "every level"
            ),
        )

    # R0–R2: compare against the effective (stricter) ceiling.
    instance_rank = _CEILING_RANK[ceiling]
    effective = ceiling
    effective_rank = instance_rank
    if capability_ceiling is not None:
        local_rank = _CEILING_RANK[capability_ceiling]
        if local_rank < effective_rank:
            effective = capability_ceiling
            effective_rank = local_rank
    class_rank = _CLASS_RANK[action_class]

    if class_rank <= effective_rank:
        return AuthorityDecision(
            capability=capability,
            action_class=action_class,
            ceiling=ceiling,
            capability_ceiling=capability_ceiling,
            effective_ceiling=effective,
            matched_policy="within_ceiling",
            decision=DECISION_AUTO_APPROVE,
            reason=(
                f"{action_class} is at or below the effective automatic "
                f"ceiling {effective!r}"
            ),
        )
    if class_rank <= instance_rank:
        return AuthorityDecision(
            capability=capability,
            action_class=action_class,
            ceiling=ceiling,
            capability_ceiling=capability_ceiling,
            effective_ceiling=effective,
            matched_policy="stricter_local_policy",
            decision=DECISION_REQUIRE_APPROVAL,
            reason=(
                f"{action_class} is within the instance ceiling {ceiling!r} "
                f"but above the stricter capability ceiling "
                f"{capability_ceiling!r}; local policy may always lower the "
                f"ceiling"
            ),
        )
    return AuthorityDecision(
        capability=capability,
        action_class=action_class,
        ceiling=ceiling,
        capability_ceiling=capability_ceiling,
        effective_ceiling=effective,
        matched_policy="above_ceiling",
        decision=DECISION_REQUIRE_APPROVAL,
        reason=(
            f"{action_class} is above the instance automatic ceiling "
            f"{ceiling!r}; routed to approval"
        ),
    )


__all__ = [
    "ACTION_CLASSES",
    "AUTHORITY_CEILINGS",
    "AuthorityDecision",
    "evaluate_action_authority",
    "validate_ceiling",
]
