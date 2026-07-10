"""Legacy approval configs are byte-identical under v1.9. CONTRACT v1.9.

``tests/snapshots/legacy_approval_log.jsonl`` was produced by running
``tests/_legacy_approval_scenario.py`` on the tree at the commit BEFORE
the v1.9 authority work (risk_class-only capabilities, pack-policy
approval gate, propose → pending → approve). Re-running the identical
scenario on the current tree must reproduce that log byte-for-byte:
same events, same order, same ids, same payload keys — no new
``authority.*`` events, no ``action_class`` key materializing in
``pack.loaded``, no approval-routing change. This is the regression
proof behind "existing static approval configuration keeps behaving
exactly as today during migration" (ADR 0016 rule 3).
"""

from __future__ import annotations

import os

from tests._legacy_approval_scenario import scenario_log_lines

_GOLDEN = os.path.join(
    os.path.dirname(__file__), "snapshots", "legacy_approval_log.jsonl"
)


def test_legacy_approval_flow_is_byte_identical_to_pre_v19_golden() -> None:
    with open(_GOLDEN, encoding="utf-8") as f:
        golden = f.read().splitlines()
    current = scenario_log_lines()
    assert current == golden, (
        "the legacy approval flow produced a different event log than the "
        "pre-v1.9 golden — a config without action_class must behave "
        "byte-identically (CONTRACT v1.9, ADR 0016 rule 3)"
    )
    # Belt and braces: no authority event ever appears on the legacy path.
    assert not [line for line in current if '"authority.' in line]
