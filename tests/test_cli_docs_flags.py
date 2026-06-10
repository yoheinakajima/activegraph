"""CLI reference drift gate.

CONTRACT v1.1 #2: flags documented in the CLI reference should exist in
Click, and long Click options should be documented. This catches the class
of release-readiness bug where docs advertise a flag that the parser never
registered, or a shipped flag stays invisible to operators.
"""

from __future__ import annotations

import re
from pathlib import Path

import click

from activegraph.cli.main import cli


REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_DOC = REPO_ROOT / "docs" / "reference" / "cli.md"

COMMAND_HEADINGS = (
    "inspect",
    "replay",
    "fork",
    "diff",
    "export-trace",
    "migrate",
    "pack new",
    "pack list",
    "quickstart",
)


def test_cli_reference_flags_match_click_options() -> None:
    text = CLI_DOC.read_text()
    for heading in COMMAND_HEADINGS:
        documented = _documented_long_options(text, heading)
        actual = _click_long_options(heading)
        assert documented == actual, (
            f"CLI reference drift for `activegraph {heading}`.\n"
            f"documented-only: {sorted(documented - actual)}\n"
            f"click-only:      {sorted(actual - documented)}"
        )


def _documented_long_options(text: str, heading: str) -> set[str]:
    section = _section(text, heading)
    return {
        token.split()[0]
        for token in re.findall(r"`(--[A-Za-z0-9][^`]*)`", section)
        if not token.startswith("--help")
    }


def _section(text: str, heading: str) -> str:
    marker = f"## `{heading}`"
    start = text.find(marker)
    assert start != -1, f"{CLI_DOC} is missing section {marker!r}"
    next_heading = text.find("\n## `", start + len(marker))
    if next_heading == -1:
        return text[start:]
    return text[start:next_heading]


def _click_long_options(heading: str) -> set[str]:
    command: click.Command = cli
    for part in heading.split():
        assert isinstance(command, click.Group), f"{heading!r} is not a command path"
        command = command.commands[part]
    out: set[str] = set()
    for param in command.params:
        if not isinstance(param, click.Option):
            continue
        for opt in [*param.opts, *param.secondary_opts]:
            if opt.startswith("--") and opt != "--help":
                out.add(opt)
    return out
