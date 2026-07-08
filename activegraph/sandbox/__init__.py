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
    provider either way. ``env_passthrough`` is the ONLY parent environment
    forwarded beyond interpreter mechanics (PATH/PYTHONPATH) — the
    helper never forwards the environment wholesale, so parent API
    keys don't leak into candidate code by default.
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
    env = {
        key: os.environ[key]
        for key in ("PATH", "PYTHONPATH", "HOME", "LANG")
        if key in os.environ
    }
    for key in limits.env_passthrough:
        if key in os.environ:
            env[key] = os.environ[key]

    proc = subprocess.Popen(
        [sys.executable, "-m", "activegraph.sandbox._child"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
    )
    try:
        stdout, _ = proc.communicate(
            input=json.dumps(job), timeout=limits.wall_clock_seconds
        )
        exit_code: Optional[int] = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()
        exit_code = proc.returncode
        timed_out = True

    tail: dict[str, object] = {}
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict) and "outcome" in parsed:
                tail = parsed
                break
        except json.JSONDecodeError:
            continue

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
