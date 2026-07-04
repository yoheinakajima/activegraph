"""Per-behavior policy. v0 is permissive — fields are recorded but not enforced
beyond a couple of obvious checks. Hardening lands in v0.6 alongside LLM
behaviors, where unbounded tool/cost spend is the real risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Policy:
    """Declared write/tool allowlists for a behavior.

    ``behavior`` names the behavior the lists scope to; the ``can_*``
    fields enumerate the object types, relation types, patches, and
    tool names it may touch, and ``requires_approval`` routes matching
    writes through the pending-approval flow. v0 semantics stand:
    fields are recorded with the run for audit; the actively enforced
    gate today is approval routing (pack policies, CONTRACT v0.9),
    with broader enforcement reserved for a hardening pass.
    """

    behavior: Optional[str] = None
    can_create: list[str] = field(default_factory=list)
    can_create_relation: list[str] = field(default_factory=list)
    can_propose: list[str] = field(default_factory=list)
    can_apply: list[str] = field(default_factory=list)
    can_call_tool: list[str] = field(default_factory=list)
    requires_approval: list[str] = field(default_factory=list)
