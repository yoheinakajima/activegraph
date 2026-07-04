"""Patch primitives. CONTRACT #4 (versioning) and #12 (single-target atomic).

A Patch is a proposed mutation. Lifecycle:
    proposed -> applied
    proposed -> rejected
`patch_object` is the auto-apply shortcut: builds a patch, version-checks,
emits patch.applied (or patch.rejected) directly. `propose_patch` emits
patch.proposed and waits for explicit approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


PATCH_OPS = {"create", "update", "replace", "remove"}


@dataclass
class Patch:
    """A proposed single-target mutation. CONTRACT #4 and #12.

    ``op`` (``create | update | replace | remove``) targets exactly
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "op": self.op,
            "value": self.value,
            "expected_version": self.expected_version,
            "proposed_by": self.proposed_by,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "status": self.status,
            "rejection_reason": self.rejection_reason,
            "provenance": dict(self.provenance),
        }
