"""Hard limits on a run. When any limit is hit the runtime stops gracefully
and emits runtime.budget_exhausted.

CONTRACT v0.6 #9: `max_cost_usd` is tracked in `Decimal` so cents don't
drift across thousands of LLM calls. The rest of the dimensions stay
float — they're integer-shaped (event counts, behavior calls). The
pre-call cost check is conservative (uses `max_tokens` as the output
estimate) and is *only* performed when `max_cost_usd` is finite AND no
cached response was found (decision-4 adjustment).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Optional


KNOWN_LIMITS = (
    "max_events",
    "max_behavior_calls",
    "max_llm_calls",
    "max_tool_calls",
    "max_patches",
    "max_depth",
    "max_seconds",
    "max_cost_usd",
)


def _as_decimal(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


class Budget:
    """Hard limits on a run. Budgets end runs gracefully; they don't raise.

    Construct with a dict over the ``KNOWN_LIMITS`` dimensions
    (``max_events``, ``max_behavior_calls``, ``max_llm_calls``,
    ``max_tool_calls``, ``max_patches``, ``max_depth``,
    ``max_seconds``, ``max_cost_usd``); any omitted dimension is
    unlimited. When a limit is hit the runtime stops dispatching and
    emits ``runtime.budget_exhausted`` — the log records which
    dimension ended the run. ``max_cost_usd`` accumulates in
    ``Decimal`` so per-call sub-cent costs don't drift across
    thousands of LLM calls (CONTRACT v0.6 #9).
    """

    def __init__(self, limits: Optional[dict[str, Any]] = None) -> None:
        self.limits: dict[str, float] = {}
        self.cost_limit: Optional[Decimal] = None
        for k in KNOWN_LIMITS:
            v = (limits or {}).get(k)
            if k == "max_cost_usd":
                # Track cost as a Decimal so per-call sub-cent costs add
                # up cleanly. None / missing → no cost ceiling.
                self.cost_limit = _as_decimal(v) if v is not None else None
                # Mirror as float for snapshot / has-it semantics.
                self.limits[k] = (
                    float(self.cost_limit) if self.cost_limit is not None else float("inf")
                )
            else:
                self.limits[k] = float(v) if v is not None else float("inf")
        self.used: dict[str, float] = {k: 0.0 for k in self.limits}
        self.cost_used: Decimal = Decimal("0")
        self._start: Optional[float] = None
        self._exhausted_by: Optional[str] = None

    # ---- generic counter dimensions ----

    def start(self) -> None:
        self._start = time.monotonic()

    def consume(self, key: str, amount: float = 1.0) -> None:
        self.used[key] = self.used.get(key, 0.0) + amount

    def exhausted_by(self) -> Optional[str]:
        return self._exhausted_by

    def remaining(self) -> bool:
        for k, limit in self.limits.items():
            if k == "max_seconds":
                if self._start is None:
                    continue
                if time.monotonic() - self._start >= limit:
                    self._exhausted_by = k
                    return False
                continue
            if k == "max_cost_usd":
                if self.cost_limit is not None and self.cost_used >= self.cost_limit:
                    self._exhausted_by = k
                    return False
                continue
            if self.used.get(k, 0.0) >= limit:
                self._exhausted_by = k
                return False
        return True

    # ---- cost dimension (Decimal) ----

    def has_cost_limit(self) -> bool:
        return self.cost_limit is not None

    def add_cost(self, amount: Decimal) -> None:
        self.cost_used += _as_decimal(amount)
        # Mirror to the float snapshot view so existing readers see it.
        self.used["max_cost_usd"] = float(self.cost_used)

    def cost_remaining(self, prospective_cost: Decimal) -> bool:
        """Would `prospective_cost` push us past the ceiling? Returns
        True if it's safe to spend, False if it would exceed."""
        if self.cost_limit is None:
            return True
        return (self.cost_used + _as_decimal(prospective_cost)) <= self.cost_limit

    def cost_remaining_amount(self) -> Optional[Decimal]:
        if self.cost_limit is None:
            return None
        remaining = self.cost_limit - self.cost_used
        return remaining if remaining > 0 else Decimal("0")

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {"used": dict(self.used), "limits": {}}
        for k, v in self.limits.items():
            out["limits"][k] = None if v == float("inf") else v
        # Expose cost as Decimal-string too so consumers can choose precision.
        out["cost_used_usd"] = str(self.cost_used)
        out["cost_limit_usd"] = (
            str(self.cost_limit) if self.cost_limit is not None else None
        )
        return out
