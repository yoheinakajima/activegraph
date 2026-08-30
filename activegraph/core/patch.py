"""Patch primitives. CONTRACT #4 (versioning) and #12 (single-target atomic).

A Patch is a proposed mutation. Lifecycle:
    proposed -> applied
    proposed -> rejected
`patch_object` is the auto-apply shortcut: builds a patch, version-checks,
emits patch.applied (or patch.rejected) directly. `propose_patch` emits
patch.proposed and waits for explicit approval.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional


PATCH_OPS = frozenset({"update", "replace"})


def validate_patch_op(op: str, *, event_id: Optional[str] = None) -> None:
    """Reject an operation outside the closed patch vocabulary."""
    if op in PATCH_OPS:
        return
    from activegraph.runtime.exec_errors import InvalidPatchOperation

    event_context = f" in event {event_id!r}" if event_id is not None else ""
    raise InvalidPatchOperation(
        f"unsupported patch operation: {op!r}",
        what_failed=(
            f"A patch{event_context} declared operation {op!r}, but the "
            "runtime patch vocabulary is exactly: update, replace."
        ),
        why=(
            "Patches target one existing object and use expected_version as "
            "an optimistic concurrency check. Object creation and removal "
            "have dedicated event paths; treating them as patches previously "
            "recorded false patch.applied success."
        ),
        how_to_fix=(
            "Use op='update' to merge fields or op='replace' to replace the "
            "target data. Use graph.add_object(...) or graph.remove_object(...) "
            "for lifecycle changes. If this came from a stored run, inspect "
            "the named event; it is not silently reinterpreted."
        ),
        context={"op": op, "event_id": event_id, "allowed": sorted(PATCH_OPS)},
    )


@dataclass
class Patch:
    """A proposed single-target mutation. CONTRACT #4 and #12.

    ``op`` (``update | replace``) targets exactly
    one object; ``expected_version`` makes application an optimistic
    concurrency check against the object's current version; ``status``
    walks ``proposed -> applied`` or ``proposed -> rejected`` and an
    object is never mutated except by an applied patch's event.
    ``rationale`` and ``evidence`` carry the audit trail that approval
    flows read.
    """

    id: str
    target: str
    op: str
    value: dict[str, Any]
    expected_version: int
    proposed_by: str
    rationale: Optional[str] = None
    evidence: list[str] = field(default_factory=list)
    status: str = "proposed"  # proposed | applied | rejected
    rejection_reason: Optional[str] = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_patch_op(self.op)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "op": self.op,
            "value": copy.deepcopy(self.value),
            "expected_version": self.expected_version,
            "proposed_by": self.proposed_by,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "status": self.status,
            "rejection_reason": self.rejection_reason,
            "provenance": copy.deepcopy(self.provenance),
        }
