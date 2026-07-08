"""Subprocess fork-trial isolation. CONTRACT v1.5 #1.

Implements ``trial-isolation-design.md``: candidate pack code trials
run in a **fresh interpreter**, against a fork of the parent run,
materialized from artifacts pinned by the bundle hash — so the parent
process is out of the blast radius of the T1 accident class (runaway
memory/CPU, corrupted in-process state) and the bytes trialed are the
bytes the proposal recorded.

The division of authority is the design's:

  * **The parent forks.** The child receives only the fork's run id —
    it never gets fork authority, and it appends only to that run.
  * **The child is a fresh interpreter** (``sys.executable -m
    activegraph.sandbox._child``), not ``os.fork()`` — no inherited
    Python state, no shared clients. Environment is allow-list only.
  * **Three independent nets**: ``resource.setrlimit`` (address
    space + CPU, POSIX; degrade to the other two nets on Windows),
    parent-side wall-clock kill, and the runtime's own event/LLM
    budgets inside the child.
  * **The store is the record.** The child's stdout tail is a signal;
    the parent re-reads the fork run from the store and the store's
    numbers win any disagreement.

TWO CHANNELS, KEPT SEPARATE (v1.7, the Replit soak fix):

  * **The environment allow-list stays CLOSED.** Only ``PATH``,
    ``HOME``, ``LANG`` plus an explicit ``env_passthrough`` cross the
    boundary. No ambient parent env — secrets, API keys, platform
    config like ``REPLIT_*`` — leaks into a child running hostile
    candidate code. This is a security control and does not widen.
  * **Code location is an EXPLICIT channel, not an ambient one.** A
    child must be able to import ``activegraph`` (and its deps) on any
    box where the *parent* could — but on some platforms (Replit,
    Nix) the package is discoverable only via a platform env var the
    allow-list correctly strips, so a naive child dies at import
    before running. The parent therefore RESOLVES the ``sys.path``
    entries that make its own code importable and passes them to the
    child as a computed ``PYTHONPATH`` (see :func:`_child_env`).
    ``PYTHONPATH`` is thus derived from the parent's resolved
    ``sys.path``, never forwarded from ambient env — code locations
    are not secrets, and making the child's discovery of its own code
    an explicit input keeps the allow-list closed while fixing the
    restricted-env break. Call :func:`preflight` once at boot to
    verify a child can start under this env and fail loud if not.

HONEST LIMITS (the design's §5, restated where users will read it):
this is crash/state isolation, **not a security sandbox**. A fresh
interpreter with rlimits does not stop a malicious candidate from
opening sockets or reading the filesystem — syscall and network
confinement are host territory (containers, seccomp), and this module
does not imitate them. The child can also, in principle, open the
shared SQLite file directly and touch other runs; per-run
authorization inside one SQLite file is not something SQLite gives us
honestly, so that caveat is stated rather than solved. The evolution
pack's static gates remain the pre-execution filter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


#: The closed outcome set. ``crashed`` is the implementation's one
#: addition over the design draft: a child that dies without a
#: parseable tail (segfault, os._exit, kill) is reported as what it
#: is, never guessed into a neater bucket.
TRIAL_OUTCOMES = (
    "completed",
    "scenario_failed",
    "limits_exceeded",
    "materialization_failed",
    "crashed",
)

# Child exit codes, mirrored in _child.py. Anything else → crashed.
_EXIT_TO_OUTCOME = {
    0: "completed",
    30: "scenario_failed",
    40: "limits_exceeded",
    50: "materialization_failed",
}


@dataclass(frozen=True)
class PackSource:
    """Where the child materializes the candidate pack from.

    ``expected_bundle_hash`` is the external pin (the §4 walk WITH
    ``manifest.toml``, per the v1.4 bundle-hash amendment) — the child
    verifies it via ``activegraph.packs.manifest.verify_bundle_hash``
    before importing anything, so the bytes trialed are the bytes the
    proposal recorded. ``manifest_required=True`` additionally runs
    ``load_manifest`` + ``verify_surface`` against the live ``Pack``,
    making the trial child the first consumer of the full manifest
    chain end-to-end.
    """

    root_dir: str
    expected_bundle_hash: str = ""
    manifest_required: bool = True


@dataclass(frozen=True)
class TrialLimits:
    """The trial's resource nets. Zero/None disables a given net.

    ``max_llm_calls=0`` (the default) means key-freedom is
    STRUCTURAL: the child configures no LLM provider at all, so a
    candidate with LLM behaviors fails loud at registration
    (``MissingProviderError``) rather than reaching a network. A
    positive cap is accepted and recorded for a future
    provider-wiring seam; the v1 child never configures a live
    provider either way. ``env_passthrough`` is the ONLY ambient parent
    environment forwarded beyond the closed allow-list
    (``PATH``/``HOME``/``LANG``) — the helper never forwards the
    environment wholesale, so parent API keys don't leak into
    candidate code by default. The child's ``PYTHONPATH`` is NOT an
    ambient forward: it is computed from the parent's resolved
    ``sys.path`` so the child can import its own code (see the module
    docstring's "two channels").
    """

    wall_clock_seconds: float = 120.0
    max_rss_bytes: Optional[int] = None
    max_events: Optional[int] = 2_000
    max_llm_calls: Optional[int] = 0
    env_passthrough: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrialReport:
    """What a trial produced. Store-derived numbers, child-signaled shape.

    ``events_appended`` and ``behavior_failures`` are re-read from the
    fork's run in the store by the parent AFTER the child exits — the
    stdout tail is a signal only, and any disagreement resolves in
    favor of the store. Richer evidence (tracebacks, diffs) is read
    from the fork's log directly: ``Runtime.load(store, run_id=
    report.fork_run_id)`` then ``trace.failures()`` / ``diff()``.
    """

    outcome: str
    fork_run_id: str
    events_appended: int
    behavior_failures: int
    detail: str
    exit_code: Optional[int]


class SandboxStartupError(RuntimeError):
    """A trial child could not START under the sandbox env.

    Raised by :func:`preflight` when the child fails before it can run
    any trial — the common cause is the sandbox env being unable to
    import ``activegraph`` on this box (the exact restricted-env break
    the explicit package-path channel exists to prevent). The message
    carries the child's stderr tail so the cause is never opaque.
    """


def _child_code_paths() -> list[str]:
    """The ``sys.path`` entries a child needs to import the parent's
    code, resolved in the parent (v1.7 explicit package-path channel).

    ``activegraph``'s own root comes first, resolved from
    ``activegraph.__file__`` — this is what an editable install or a
    platform that surfaces packages via a stripped env var (Replit's
    ``REPLIT_PYTHONPATH``) would otherwise hide from the child. The
    parent's real ``sys.path`` directories follow so third-party deps
    resolve too. Non-directory entries (namespace-finder hooks, the
    empty CWD entry, zip imports) are dropped: they either don't help
    a fresh child or would import from its CWD, which we do not want.
    """
    import activegraph

    ordered: list[str] = []
    ag_root = str(Path(activegraph.__file__).resolve().parent.parent)
    ordered.append(ag_root)
    for entry in sys.path:
        if entry and os.path.isdir(entry) and entry not in ordered:
            ordered.append(entry)
    return ordered


def _child_env(limits: TrialLimits) -> dict[str, str]:
    """Build the child's environment: closed allow-list + the computed
    ``PYTHONPATH`` code channel. See the module docstring's "two
    channels".

    The allow-list is ``PATH``/``HOME``/``LANG`` plus the caller's
    explicit ``env_passthrough`` — no other ambient env crosses. Then
    ``PYTHONPATH`` is set from :func:`_child_code_paths` (the parent's
    resolved code locations), prepended before any ``PYTHONPATH`` the
    caller opted to pass through, so the child imports the same code
    the parent can regardless of platform package discovery.
    """
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG")
        if key in os.environ
    }
    for key in limits.env_passthrough:
        if key in os.environ:
            env[key] = os.environ[key]
    channel = os.pathsep.join(_child_code_paths())
    passed_through = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        channel + os.pathsep + passed_through if passed_through else channel
    )
    return env


def _stderr_tail(stderr: str, *, max_lines: int = 20, max_chars: int = 1500) -> str:
    """The last few lines of a child's stderr, capped — the actual
    cause of a pre-``_report`` crash the DEVNULL default used to lose.
    """
    text = (stderr or "").strip()
    if not text:
        return ""
    lines = text.splitlines()[-max_lines:]
    tail = "\n".join(lines)
    return tail[-max_chars:]


def _parse_report_tail(stdout: str) -> dict[str, object]:
    """The child's last valid JSON report line, or ``{}``."""
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and (
            "outcome" in parsed or "preflight" in parsed
        ):
            return parsed
    return {}


def _run_child(
    job: dict[str, object],
    *,
    env: dict[str, str],
    wall_clock: float,
    python_flags: tuple[str, ...] = (),
) -> tuple[Optional[int], str, str, bool]:
    """Spawn the trial child, feed it ``job`` on stdin, and return
    ``(exit_code, stdout, stderr, timed_out)``.

    Shared by :func:`run_forked_trial` and :func:`preflight`. stderr is
    PIPED (never DEVNULL) so a pre-``_report`` crash's cause survives.
    ``python_flags`` is an interpreter-flag seam used by tests to
    reproduce a restricted env (``-S``); production passes none.
    """
    proc = subprocess.Popen(
        [sys.executable, *python_flags, "-m", "activegraph.sandbox._child"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(
            input=json.dumps(job), timeout=wall_clock
        )
        return proc.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return proc.returncode, stdout, stderr, True


def _preflight_with(
    env: dict[str, str],
    *,
    python_flags: tuple[str, ...] = (),
    timeout: float = 30.0,
) -> None:
    """The preflight check against a specific ``env`` (the testable
    seam under :func:`preflight`). Raises :class:`SandboxStartupError`
    if a child cannot start and report ``preflight: ok``.
    """
    exit_code, stdout, stderr, timed_out = _run_child(
        {"preflight": True}, env=env, wall_clock=timeout, python_flags=python_flags
    )
    tail = _parse_report_tail(stdout)
    if not timed_out and exit_code == 0 and tail.get("preflight") == "ok":
        return
    cause = _stderr_tail(stderr) or (
        "child timed out" if timed_out else "no preflight tail on stdout"
    )
    raise SandboxStartupError(
        f"trial child could not start (exit {exit_code}): {cause}"
    )


def preflight(*, limits: TrialLimits = TrialLimits(), timeout: float = 30.0) -> None:
    """Verify a trial child can START under the sandbox env on this box.

    Spawns the child with a null job (no fork, no store, no candidate):
    it imports ``activegraph``, echoes ``preflight: ok``, and exits 0.
    Returns ``None`` on success; raises :class:`SandboxStartupError`
    with the child's stderr tail otherwise. Consumers call this once at
    boot so a broken sandbox env (e.g. a platform whose package
    discovery the allow-list strips) fails LOUD here instead of showing
    up as an opaque ``crashed`` on the first real trial.
    """
    _preflight_with(_child_env(limits), timeout=timeout)


def run_forked_trial(
    store_path: str,
    *,
    parent_run_id: str,
    at_event: str,
    pack_source: PackSource,
    scenario: str = "",
    limits: TrialLimits = TrialLimits(),
    label: str = "trial",
    extra_packs: tuple[PackSource, ...] = (),
) -> TrialReport:
    """Fork the parent at ``at_event`` and trial the candidate pack in
    a fresh subprocess. CONTRACT v1.5 #1.

    The fork is created HERE, in the parent process, with full
    ``fork()`` semantics (lineage recorded, promote-block cut guard);
    the child receives only the fork's run id. ``scenario`` is
    ``"relative/path.py"`` or ``"relative/path.py::func"`` inside the
    CANDIDATE's pack root — the function (default ``main``) is called
    with the fork's ``Runtime`` and drives the trial; empty means
    just ``run_until_idle()``. The scenario contract is
    ``def main(rt): ...``.

    **The default is candidate-only isolation**: the child loads
    nothing but the candidate, so the trial exercises it apart from
    every other pack's behaviors. ``extra_packs`` is the opt-in for
    cross-pack interaction trials (CONTRACT v1.5 #1 addendum 1b):
    each entry is materialized in the child exactly like the
    candidate — bundle hash verified before import, manifest schema
    + two-way surface check when required — and loaded, in order,
    BEFORE the candidate. Any extra pack failing its pins is
    ``materialization_failed`` for the whole trial; the scenario
    still resolves inside the candidate's root only.

    Deterministic and key-free by default (``max_llm_calls=0``, empty
    environment pass-through). Returns a :class:`TrialReport`; never
    raises for in-trial failures — those are outcomes. Raises only
    for parent-side setup problems (bad store, bad fork point), with
    the same errors ``Runtime.load`` / ``fork()`` raise.
    """
    from activegraph.runtime.runtime import Runtime

    parent_rt = Runtime.load(store_path, run_id=parent_run_id, behaviors=[])
    fork_rt = parent_rt.fork(at_event=at_event, label=label, behaviors=[])
    fork_run_id = fork_rt.run_id
    initial_events = len(fork_rt.graph.events)
    del fork_rt  # the child owns the fork from here

    job = {
        "store_path": store_path,
        "fork_run_id": fork_run_id,
        "initial_events": initial_events,
        "pack_root": str(Path(pack_source.root_dir).resolve()),
        "expected_bundle_hash": pack_source.expected_bundle_hash,
        "manifest_required": pack_source.manifest_required,
        "extra_packs": [
            {
                "pack_root": str(Path(p.root_dir).resolve()),
                "expected_bundle_hash": p.expected_bundle_hash,
                "manifest_required": p.manifest_required,
            }
            for p in extra_packs
        ],
        "scenario": scenario,
        "limits": {
            "max_rss_bytes": limits.max_rss_bytes,
            "max_events": limits.max_events,
            "max_llm_calls": limits.max_llm_calls,
            "cpu_seconds": int(limits.wall_clock_seconds) + 5,
        },
    }
    exit_code, stdout, stderr, timed_out = _run_child(
        job, env=_child_env(limits), wall_clock=limits.wall_clock_seconds
    )
    tail = _parse_report_tail(stdout)

    if timed_out:
        outcome = "limits_exceeded"
        detail = (
            f"wall clock exceeded {limits.wall_clock_seconds}s; child killed"
        )
    elif tail:
        outcome = str(tail.get("outcome", "crashed"))
        if outcome not in TRIAL_OUTCOMES:
            outcome = "crashed"
        detail = str(tail.get("detail", ""))
    else:
        outcome = _EXIT_TO_OUTCOME.get(exit_code or -1, "crashed")
        if outcome == "completed":
            # Exit 0 with no tail is itself suspicious; say so.
            outcome = "crashed"
        detail = f"child exited {exit_code} with no report tail"

    # The pre-execution gate must never swallow its own failure cause:
    # when the child crashed before it could _report (module-import
    # failure, rlimit setup, anything pre-main), the actual exception
    # is on the child's stderr, not in any tail. Fold it into detail so
    # it reaches the caller's recorded gate_result (v1.7 fix 1).
    if outcome == "crashed":
        err_tail = _stderr_tail(stderr)
        if err_tail:
            detail = f"{detail}; child stderr:\n{err_tail}"

    # The store is the record: re-read the fork run for the numbers.
    events_appended = 0
    behavior_failures = 0
    try:
        fork_view = Runtime.load(store_path, run_id=fork_run_id, behaviors=[])
        events_appended = max(
            0, len(fork_view.graph.events) - initial_events
        )
        behavior_failures = len(fork_view.trace.failures())
    except Exception as e:  # noqa: BLE001 — report, never mask the trial
        detail = f"{detail} (store re-read failed: {e})".strip()

    return TrialReport(
        outcome=outcome,
        fork_run_id=fork_run_id,
        events_appended=events_appended,
        behavior_failures=behavior_failures,
        detail=detail,
        exit_code=exit_code,
    )
