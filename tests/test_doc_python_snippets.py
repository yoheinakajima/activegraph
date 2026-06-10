"""Executable Python snippet gate for copy-paste docs.

CONTRACT v1.1 #2 expands the tutorial-only down payment into a small
allowlist of complete snippets that promise end-to-end behavior. Each
case runs in a subprocess against the bundled quickstart fixture.
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SnippetCase:
    path: Path
    heading: str
    expected_stdout: str | None = None


SNIPPETS = (
    SnippetCase(
        path=REPO_ROOT / "docs" / "quickstart.md",
        heading="## 7. Fork and diff",
        expected_stdout="forked: quickstart_cautious",
    ),
    SnippetCase(
        path=REPO_ROOT / "docs" / "cookbook" / "common-patterns.md",
        heading="## Fork with a pack-setting override",
    ),
)


@pytest.mark.parametrize("case", SNIPPETS, ids=lambda c: c.path.name + "::" + c.heading)
def test_allowlisted_python_snippet_runs(case: SnippetCase, tmp_path: Path) -> None:
    _run_quickstart_fixture_mode()
    snippet = _extract_first_python_block(case.path, case.heading)
    script = tmp_path / "doc_snippet.py"
    script.write_text(snippet)

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{case.path}:{case.heading} snippet failed with exit "
        f"{result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    if case.expected_stdout is not None:
        assert case.expected_stdout in result.stdout, (
            f"{case.path}:{case.heading} did not print "
            f"{case.expected_stdout!r}.\nstdout:\n{result.stdout}"
        )


def _extract_first_python_block(path: Path, heading: str) -> str:
    text = path.read_text()
    idx = text.find(heading)
    if idx == -1:
        pytest.fail(f"could not locate heading {heading!r} in {path}")
    section = text[idx:]
    match = re.search(r"```python\n(.*?)\n```", section, re.DOTALL)
    if match is None:
        pytest.fail(f"could not find a python code block under {heading!r} in {path}")
    return match.group(1)


def _run_quickstart_fixture_mode() -> None:
    from activegraph.cli.quickstart import run_fixture_mode

    buf = io.StringIO()
    rc = run_fixture_mode(stream=buf)
    assert rc == 0, "fixture-mode quickstart did not exit 0"
