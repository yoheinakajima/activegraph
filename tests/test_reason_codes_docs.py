"""Reason-code taxonomy docs stay aligned with framework-emitted codes."""

from __future__ import annotations

from pathlib import Path

from activegraph.llm.errors import _LLM_REASON_PROSE
from activegraph.tools.errors import _TOOL_REASON_PROSE


DOC = Path("docs/reference/reason-codes.md")


def test_reason_code_reference_lists_framework_codes():
    text = DOC.read_text()
    expected = set(_LLM_REASON_PROSE) | set(_TOOL_REASON_PROSE) | {
        "llm.prompt_assembly_error",
        "tool.unknown_tool",
        "tool.max_turns_exhausted",
        "budget.cost_exhausted",
        "budget.llm_calls_exhausted",
        "budget.tool_calls_exhausted",
        "budget.behavior_calls_exhausted",
        "budget.events_exhausted",
        "budget.seconds_exhausted",
        "budget.patches_exhausted",
        "budget.depth_exhausted",
        "budget.exhausted",
        "exception.<ClassName>",
    }
    missing = sorted(code for code in expected if code not in text)
    assert not missing
