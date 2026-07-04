"""Mission context for a run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Frame:
    """Mission context for a run: the goal plus its guardrails.

    ``goal`` is the one-line mission; ``constraints``,
    ``success_criteria``, and ``permissions`` are declarative lists
    stamped into assembled LLM prompts and visible to behaviors as
    ``ctx.frame``. A frame describes intent — enforcement lives in
    :class:`~activegraph.policy.Policy` and the budget, not here.
    """

    goal: str
    id: Optional[str] = None
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
