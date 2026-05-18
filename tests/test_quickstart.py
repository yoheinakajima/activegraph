"""``activegraph quickstart`` — tests. v1.0-rc1 #1/5.

Fixture mode: snapshot test of the full byte-deterministic output.
The quickstart command is a demo (FrozenClock + fixed run id +
seeded behaviors); its output is the same on every machine. If the
output drifts, regenerate the snapshot with ``UPDATE_SNAPSHOTS=1``
AND confirm the change is intentional — the transcript at
``examples/quickstart_session.txt`` is the contract for what the
output should say.

Interactive mode: scripted-stdin test via click's CliRunner. The
interactive REPL has three branches worth covering — happy path
(create file, run, quit), quit-without-running, and the collision
handler when ``activegraph_quickstart/`` already exists.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from activegraph.cli.quickstart import (
    _INTERACTIVE_SUBDIR,
    _QUICKSTART_DB_PATH,
    cmd_quickstart,
    run_fixture_mode,
    run_interactive_mode,
)


SNAPSHOT_PATH = (
    Path(__file__).parent / "snapshots" / "quickstart_fixture.txt"
)


# ---------- fixture mode -------------------------------------------------


def test_fixture_mode_snapshot() -> None:
    """The full output is byte-identical across machines. If this
    drifts and the change is intentional, run with UPDATE_SNAPSHOTS=1
    AND review the transcript spec to confirm the new output still
    matches the locked shape from examples/quickstart_session.txt.
    """
    # Clear the demo DB before running so the test starts clean. The
    # production command does this too — quickstart re-runs always
    # overwrite the previous demo.
    if os.path.exists(_QUICKSTART_DB_PATH):
        os.remove(_QUICKSTART_DB_PATH)

    buf = io.StringIO()
    rc = run_fixture_mode(stream=buf)
    assert rc == 0
    actual = buf.getvalue()

    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(actual)
    assert SNAPSHOT_PATH.exists(), (
        f"missing snapshot {SNAPSHOT_PATH}. Run with UPDATE_SNAPSHOTS=1 "
        f"to create it."
    )
    expected = SNAPSHOT_PATH.read_text()
    assert actual == expected, (
        "quickstart fixture-mode output drifted from snapshot. If "
        "intentional, run with UPDATE_SNAPSHOTS=1 AND confirm the "
        "change still matches the locked transcript spec at "
        "examples/quickstart_session.txt."
    )


def test_fixture_mode_writes_demo_db() -> None:
    """Fixture mode persists to a known path. The 'try next' footer
    tells the user to inspect it; the path has to exist after the run."""
    if os.path.exists(_QUICKSTART_DB_PATH):
        os.remove(_QUICKSTART_DB_PATH)
    buf = io.StringIO()
    run_fixture_mode(stream=buf)
    assert os.path.exists(_QUICKSTART_DB_PATH), (
        f"quickstart should have written to {_QUICKSTART_DB_PATH}"
    )


def test_fixture_mode_re_run_is_idempotent() -> None:
    """Re-running quickstart overwrites the previous demo cleanly.
    The fixed run id means the demo is one-shot; we don't accumulate
    N database files in /tmp on repeated runs."""
    buf1 = io.StringIO()
    run_fixture_mode(stream=buf1)
    buf2 = io.StringIO()
    run_fixture_mode(stream=buf2)
    # Output is byte-identical across runs.
    assert buf1.getvalue() == buf2.getvalue()


def test_fixture_mode_cli_invocation() -> None:
    """The click-wrapped command resolves and returns success."""
    runner = CliRunner()
    result = runner.invoke(cmd_quickstart, [])
    assert result.exit_code == 0, result.output
    # Spot-check the headline section — full content covered by the
    # snapshot test above.
    assert "activegraph quickstart" in result.output
    assert "Diligence pack" in result.output
    assert "What just happened" in result.output
    assert "Try next" in result.output


# ---------- interactive mode --------------------------------------------


class _ScriptedPrompt:
    """Scripted stdin replacement for click.prompt. Pops responses
    from a queue; raises on exhaustion so a test that under-scripts
    fails loud instead of hanging."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._returned: list[str] = []

    def __call__(self, question: str, *, default: str = "") -> str:
        if not self._responses:
            raise AssertionError(
                f"scripted prompt exhausted; last question was {question!r}\n"
                f"prior responses: {self._returned}"
            )
        r = self._responses.pop(0)
        self._returned.append(r)
        return r


def test_interactive_quit_at_initial_prompt(tmp_path, monkeypatch) -> None:
    """Quit at the first `continue/quit` prompt — verifies the
    behavior file is still left on disk for the developer to keep."""
    monkeypatch.chdir(tmp_path)
    buf = io.StringIO()
    rc = run_interactive_mode(stream=buf, prompt_fn=_ScriptedPrompt(["quit"]))
    assert rc == 0
    out = buf.getvalue()
    assert "Created" in out
    assert "Goodbye" in out
    behavior_file = tmp_path / _INTERACTIVE_SUBDIR / "my_first_behavior.py"
    assert behavior_file.exists(), "behavior file should persist after quit"


def test_interactive_collision_offer_suffix(tmp_path, monkeypatch) -> None:
    """If activegraph_quickstart/ exists, the command offers
    overwrite/suffix/quit. Defaulting to suffix produces
    my_first_behavior_2.py (and beyond if 2 also exists)."""
    monkeypatch.chdir(tmp_path)
    subdir = tmp_path / _INTERACTIVE_SUBDIR
    subdir.mkdir()
    (subdir / "my_first_behavior.py").write_text("# pre-existing\n")

    buf = io.StringIO()
    rc = run_interactive_mode(
        stream=buf,
        prompt_fn=_ScriptedPrompt(["s", "quit"]),
    )
    assert rc == 0
    out = buf.getvalue()
    assert "my_first_behavior_2.py" in out
    assert (subdir / "my_first_behavior.py").read_text() == "# pre-existing\n"
    assert (subdir / "my_first_behavior_2.py").exists()


def test_interactive_collision_offer_overwrite(tmp_path, monkeypatch) -> None:
    """Choosing overwrite replaces the existing file."""
    monkeypatch.chdir(tmp_path)
    subdir = tmp_path / _INTERACTIVE_SUBDIR
    subdir.mkdir()
    original = subdir / "my_first_behavior.py"
    original.write_text("# pre-existing\n")

    buf = io.StringIO()
    rc = run_interactive_mode(
        stream=buf,
        prompt_fn=_ScriptedPrompt(["o", "quit"]),
    )
    assert rc == 0
    # File is now the scaffold, not the original content.
    assert "pre-existing" not in original.read_text()
    assert "growth_flagger" in original.read_text()


def test_interactive_collision_quit_preserves_existing(tmp_path, monkeypatch) -> None:
    """Quit at the collision prompt leaves the existing directory
    untouched and returns non-zero."""
    monkeypatch.chdir(tmp_path)
    subdir = tmp_path / _INTERACTIVE_SUBDIR
    subdir.mkdir()
    original = subdir / "my_first_behavior.py"
    original.write_text("# pre-existing\n")

    buf = io.StringIO()
    rc = run_interactive_mode(
        stream=buf,
        prompt_fn=_ScriptedPrompt(["q"]),
    )
    assert rc == 1
    assert original.read_text() == "# pre-existing\n"


def test_interactive_happy_path_runs_user_behavior(tmp_path, monkeypatch) -> None:
    """Continue once, then quit. The scaffold's growth_flagger
    behavior is unedited (the TODO is still a pass), so it fires but
    does no work — the count of fires is meaningful regardless."""
    monkeypatch.chdir(tmp_path)
    buf = io.StringIO()
    rc = run_interactive_mode(
        stream=buf,
        prompt_fn=_ScriptedPrompt(["continue", "quit"]),
    )
    assert rc == 0
    out = buf.getvalue()
    # The "your behavior fired N time(s)" line is the load-bearing
    # output of the run-user-behavior path. N is positive because the
    # scaffold's behavior subscribes to object.created where
    # object.type=claim, and the one-company fixture produces several
    # claims.
    assert "your behavior fired" in out
    assert "Step 3 of 4" in out


def test_interactive_cli_invocation_with_quit(tmp_path, monkeypatch) -> None:
    """End-to-end through click's CliRunner with stdin scripted."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # click.prompt reads from stdin; piping the responses with newlines
    # walks through the prompts in order.
    result = runner.invoke(cmd_quickstart, ["--interactive"], input="quit\n")
    assert result.exit_code == 0, result.output
    assert "Created" in result.output
