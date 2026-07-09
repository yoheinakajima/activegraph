"""Subprocess fork-trial isolation (CONTRACT v1.5 #1).

Each test spawns a real fresh-interpreter child against a real SQLite
store. Deterministic, key-free (max_llm_calls=0, empty env
pass-through). The property every failure case re-proves: the PARENT
run is byte-untouched no matter what the candidate does.
"""

from __future__ import annotations

import functools
import os

import pytest

from activegraph import Graph, Runtime, behavior, clear_registry
from activegraph import sandbox
from activegraph.packs.manifest import compute_bundle_hash, compute_content_hash
from activegraph.sandbox import (
    PackSource,
    SandboxStartupError,
    TrialLimits,
    TrialReport,
    preflight,
    run_forked_trial,
)

PACK_INIT = '''
from activegraph.packs import Pack, behavior


@behavior(name="greeter", on=["goal.created"])
def greeter(event, graph, ctx):
    graph.add_object("greeting", {"text": event.payload.get("goal", "")})


pack = Pack(name="trial_candidate", version="0.1.0", behaviors=(greeter,))
'''

MANIFEST_TEMPLATE = """
[pack]
name = "trial_candidate"
version = "0.1.0"
description = "A candidate under trial."
license = ""

[pack.provenance]
authored_by = "agent"
generator = "test"

[pack.integrity]
content_hash = "{content_hash}"

[dependencies]
activegraph = ">=1.4,<2.0"
python-deps = []

[surface]
object_types = []
relation_types = []
behaviors = ["greeter"]
tools = []
settings_schema = ""

[fixtures]
entrypoint = "fixtures/run_fixtures.py"
deterministic = true
"""

HAPPY_SCENARIO = '''
def main(rt):
    rt.run_goal("trial run")
'''


def _candidate_dir(tmp_path, scenario=HAPPY_SCENARIO, init=PACK_INIT):
    root = tmp_path / "trial_candidate"
    root.mkdir()
    (root / "__init__.py").write_text(init)
    (root / "scenario.py").write_text(scenario)
    content = compute_content_hash(root)
    (root / "manifest.toml").write_text(
        MANIFEST_TEMPLATE.format(content_hash=content)
    )
    return root, compute_bundle_hash(root)


def _parent_store(tmp_path) -> tuple[str, str, str, int]:
    """A saved parent run: (path, run_id, tip_event_id, event_count)."""
    clear_registry()

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("task", {"title": "base"})

    rt = Runtime(Graph())
    rt.run_goal("hello")
    path = str(tmp_path / "run.db")
    rt.save_state(path)
    return path, rt.run_id, rt.trace.events()[-1].id, len(rt.graph.events)


def _parent_untouched(path, run_id, expected_events):
    loaded = Runtime.load(path, run_id=run_id, behaviors=[])
    assert len(loaded.graph.events) == expected_events


def test_happy_path_trial_completes_in_isolation(tmp_path):
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    root, bundle = _candidate_dir(tmp_path)

    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
        limits=TrialLimits(wall_clock_seconds=60),
    )
    assert isinstance(report, TrialReport)
    assert report.outcome == "completed", report.detail
    assert report.exit_code == 0
    assert report.events_appended > 0
    assert report.behavior_failures == 0

    # The candidate's behavior fired IN THE FORK: its greeting exists
    # there and nowhere else.
    fork = Runtime.load(path, run_id=report.fork_run_id, behaviors=[])
    greetings = [o for o in fork.graph.all_objects() if o.type == "greeting"]
    assert [g.data["text"] for g in greetings] == ["trial run"]
    _parent_untouched(path, parent_run, n_parent)


def test_bundle_hash_mismatch_refuses_before_import(tmp_path):
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    root, _ = _candidate_dir(tmp_path)

    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(
            root_dir=str(root), expected_bundle_hash="sha256:" + "1" * 64
        ),
        scenario="scenario.py",
    )
    assert report.outcome == "materialization_failed"
    assert "bundle hash mismatch" in report.detail
    # Nothing loaded, nothing ran: the fork carries zero trial events.
    assert report.events_appended == 0
    fork = Runtime.load(path, run_id=report.fork_run_id, behaviors=[])
    assert not [e for e in fork.graph.events if e.type == "pack.loaded"]
    _parent_untouched(path, parent_run, n_parent)


def test_scenario_crash_is_an_outcome_not_a_parent_problem(tmp_path):
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    root, bundle = _candidate_dir(
        tmp_path,
        scenario='def main(rt):\n    raise RuntimeError("candidate blew up")\n',
    )
    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
    )
    assert report.outcome == "scenario_failed"
    assert "candidate blew up" in report.detail
    _parent_untouched(path, parent_run, n_parent)


def test_event_budget_blow_is_limits_exceeded(tmp_path):
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    root, bundle = _candidate_dir(
        tmp_path,
        scenario=(
            "def main(rt):\n"
            "    for i in range(100):\n"
            '        rt.run_goal(f"g{i}")\n'
        ),
    )
    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
        limits=TrialLimits(max_events=15),
    )
    assert report.outcome == "limits_exceeded"
    assert "budget" in report.detail
    _parent_untouched(path, parent_run, n_parent)


def test_wall_clock_blow_kills_the_child(tmp_path):
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    root, bundle = _candidate_dir(
        tmp_path,
        scenario="import time\n\ndef main(rt):\n    time.sleep(60)\n",
    )
    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
        limits=TrialLimits(wall_clock_seconds=3.0),
    )
    assert report.outcome == "limits_exceeded"
    assert "wall clock" in report.detail
    _parent_untouched(path, parent_run, n_parent)


def test_hard_child_crash_reads_as_crashed_and_parent_survives(tmp_path):
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    root, bundle = _candidate_dir(
        tmp_path,
        scenario="import os\n\ndef main(rt):\n    os._exit(9)\n",
    )
    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
    )
    assert report.outcome == "crashed"
    assert report.exit_code == 9
    # The store is the record: whatever the child appended before
    # dying is still readable, and the parent is untouched.
    Runtime.load(path, run_id=report.fork_run_id, behaviors=[])
    _parent_untouched(path, parent_run, n_parent)


def test_undeclared_surface_fails_materialization(tmp_path):
    # The candidate registers a behavior its manifest never declared:
    # verify_surface refuses inside the child, before load_pack.
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    sneaky_init = PACK_INIT.replace(
        'pack = Pack(name="trial_candidate", version="0.1.0", behaviors=(greeter,))',
        '''
@behavior(name="undeclared", on=["object.created"])
def undeclared(event, graph, ctx):
    pass


pack = Pack(
    name="trial_candidate",
    version="0.1.0",
    behaviors=(greeter, undeclared),
)
''',
    )
    root, bundle = _candidate_dir(tmp_path, init=sneaky_init)
    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
    )
    assert report.outcome == "materialization_failed"
    assert "undeclared" in report.detail
    _parent_untouched(path, parent_run, n_parent)


REPLAY_PACK_INIT = '''
from activegraph.packs import Pack, behavior


@behavior(name="responder", on=["chat.message"])
def responder(event, graph, ctx):
    graph.add_object(
        "reply", {"to": event.payload.get("text", ""), "text": "ack"}
    )


pack = Pack(name="trial_candidate", version="0.1.0", behaviors=(responder,))
'''

REPLAY_MANIFEST = MANIFEST_TEMPLATE.replace(
    'behaviors = ["greeter"]', 'behaviors = ["responder"]'
)

REPLAY_SCENARIO = '''
from activegraph.core.event import Event


def main(rt):
    # The recorded input segment IS the fork's history: read it back
    # and re-inject each input as a fresh event so the candidate's
    # behaviors process it live inside the trial.
    segment = [e for e in rt.trace.events() if e.type == "chat.message"]
    for recorded in segment:
        rt.graph.emit(
            Event(
                id=rt.graph.ids.event(),
                type="chat.message",
                payload=dict(recorded.payload),
                actor="trial.replay",
                frame_id=None,
                caused_by=None,
                timestamp=rt.graph.clock.now(),
            )
        )
    rt.run_until_idle()
'''


def test_recorded_segment_replay_inside_the_trial(tmp_path):
    # THE consumer use case (evolution stage 3): fork a run, replay a
    # recorded input segment against the fork inside the child, read
    # failures + counts from the fork's run in the store afterward.
    from activegraph.core.event import Event

    clear_registry()
    rt = Runtime(Graph())
    # The parent's recorded inputs: three chat messages, no subscriber
    # in the parent (they are raw recorded history).
    for text in ("hello", "what's on today?", "thanks"):
        rt.graph.emit(
            Event(
                id=rt.graph.ids.event(),
                type="chat.message",
                payload={"text": text},
                actor="user",
                frame_id=None,
                caused_by=None,
                timestamp=rt.graph.clock.now(),
            )
        )
    rt.run_until_idle()
    path = str(tmp_path / "run.db")
    rt.save_state(path)
    parent_run, tip, n_parent = (
        rt.run_id,
        rt.trace.events()[-1].id,
        len(rt.graph.events),
    )
    del rt

    root = tmp_path / "trial_candidate"
    root.mkdir()
    (root / "__init__.py").write_text(REPLAY_PACK_INIT)
    (root / "scenario.py").write_text(REPLAY_SCENARIO)
    content = compute_content_hash(root)
    (root / "manifest.toml").write_text(
        REPLAY_MANIFEST.format(content_hash=content)
    )

    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(
            root_dir=str(root),
            expected_bundle_hash=compute_bundle_hash(root),
        ),
        scenario="scenario.py",
    )
    assert report.outcome == "completed", report.detail
    assert report.behavior_failures == 0

    # The candidate processed exactly the recorded segment, in order,
    # inside the fork — and the evidence is ordinary fork history.
    fork = Runtime.load(path, run_id=report.fork_run_id, behaviors=[])
    replies = [
        o.data["to"] for o in fork.graph.all_objects() if o.type == "reply"
    ]
    assert replies == ["hello", "what's on today?", "thanks"]
    _parent_untouched(path, parent_run, n_parent)


# ----------------------------------------- extra_packs (addendum 1b)

TRUSTED_PACK_INIT = '''
from activegraph.packs import Pack, behavior


@behavior(name="context_builder", on=["goal.created"])
def context_builder(event, graph, ctx):
    graph.add_object("context", {"topic": event.payload.get("goal", "")})


pack = Pack(name="trusted_helper", version="0.1.0", behaviors=(context_builder,))
'''

TRUSTED_MANIFEST = MANIFEST_TEMPLATE.replace(
    'name = "trial_candidate"', 'name = "trusted_helper"'
).replace('behaviors = ["greeter"]', 'behaviors = ["context_builder"]')

INTERACTING_CANDIDATE_INIT = '''
from activegraph.packs import Pack, behavior


@behavior(name="annotator", on=["object.created"])
def annotator(event, graph, ctx):
    obj = event.payload.get("object", {})
    if obj.get("type") == "context":
        graph.add_object("annotation", {"about": obj["data"]["topic"]})


pack = Pack(name="trial_candidate", version="0.1.0", behaviors=(annotator,))
'''


def _trusted_dir(tmp_path):
    root = tmp_path / "trusted_helper"
    root.mkdir()
    (root / "__init__.py").write_text(TRUSTED_PACK_INIT)
    content = compute_content_hash(root)
    (root / "manifest.toml").write_text(
        TRUSTED_MANIFEST.format(content_hash=content)
    )
    return root, compute_bundle_hash(root)


def test_extra_packs_enable_cross_pack_interaction_trials(tmp_path):
    # Addendum 1b: the trusted pack's behavior produces the context
    # object; the CANDIDATE reacts to it. Neither alone produces the
    # annotation — the trial is genuinely cross-pack.
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    trusted_root, trusted_bundle = _trusted_dir(tmp_path)
    root = tmp_path / "trial_candidate"
    root.mkdir()
    (root / "__init__.py").write_text(INTERACTING_CANDIDATE_INIT)
    (root / "scenario.py").write_text(
        'def main(rt):\n    rt.run_goal("cross-pack trial")\n'
    )
    annotator_manifest = MANIFEST_TEMPLATE.replace(
        'behaviors = ["greeter"]', 'behaviors = ["annotator"]'
    )
    (root / "manifest.toml").write_text(
        annotator_manifest.format(content_hash=compute_content_hash(root))
    )
    bundle = compute_bundle_hash(root)

    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
        extra_packs=(
            PackSource(
                root_dir=str(trusted_root),
                expected_bundle_hash=trusted_bundle,
            ),
        ),
    )
    assert report.outcome == "completed", report.detail
    fork = Runtime.load(path, run_id=report.fork_run_id, behaviors=[])
    contexts = [o for o in fork.graph.all_objects() if o.type == "context"]
    annotations = [
        o for o in fork.graph.all_objects() if o.type == "annotation"
    ]
    assert [c.data["topic"] for c in contexts] == ["cross-pack trial"]
    assert [a.data["about"] for a in annotations] == ["cross-pack trial"]
    # Both packs loaded, trusted FIRST, then the candidate.
    loaded = [
        e.payload["name"]
        for e in fork.graph.events
        if e.type == "pack.loaded"
    ]
    assert loaded == ["trusted_helper", "trial_candidate"]
    _parent_untouched(path, parent_run, n_parent)


def test_extra_pack_bundle_mismatch_fails_materialization(tmp_path):
    # An extra pack is pinned exactly like the candidate: a wrong
    # bundle hash refuses the WHOLE trial before anything imports.
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    trusted_root, _ = _trusted_dir(tmp_path)
    root, bundle = _candidate_dir(tmp_path)

    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
        extra_packs=(
            PackSource(
                root_dir=str(trusted_root),
                expected_bundle_hash="sha256:" + "2" * 64,
            ),
        ),
    )
    assert report.outcome == "materialization_failed"
    assert "bundle hash mismatch" in report.detail
    assert report.events_appended == 0
    fork = Runtime.load(path, run_id=report.fork_run_id, behaviors=[])
    assert not [e for e in fork.graph.events if e.type == "pack.loaded"]
    _parent_untouched(path, parent_run, n_parent)


# ------------------- diagnosability + startup channel (v1.7 soak fix)

def _bare_env():
    """The closed allow-list env WITHOUT the package-path channel — the
    restricted shape a platform like Replit leaves after stripping its
    own discovery var (REPLIT_PYTHONPATH)."""
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}


def test_child_import_crash_surfaces_the_cause_in_detail(tmp_path, monkeypatch):
    # Fix 1: a child that dies BEFORE _report (here: cannot import
    # activegraph) must carry its real cause in TrialReport.detail, not
    # an opaque "exited N with no report tail". We reproduce the import
    # death faithfully: strip the code channel and skip site.py (-S),
    # exactly the restricted env the soak hit.
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    root, bundle = _candidate_dir(tmp_path)

    monkeypatch.setattr(sandbox, "_child_env", lambda limits: _bare_env())
    orig_run_child = sandbox._run_child
    monkeypatch.setattr(
        sandbox,
        "_run_child",
        functools.partial(orig_run_child, python_flags=("-S",)),
    )

    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
    )
    assert report.outcome == "crashed"
    # The actual exception reached the report, not an opaque string.
    assert "ModuleNotFoundError" in report.detail
    assert "activegraph" in report.detail
    # The fork still exists and the parent is untouched — a broken
    # sandbox env is diagnosable, not destructive.
    Runtime.load(path, run_id=report.fork_run_id, behaviors=[])
    _parent_untouched(path, parent_run, n_parent)


def test_preflight_succeeds_on_a_working_env():
    # Returns () (no raise, no degradation) when a child can start AND
    # apply limits under the sandbox env — the normal case on Linux.
    assert preflight(timeout=30.0) == ()


def test_preflight_fails_loud_with_the_cause_on_a_restricted_env():
    # Fix 2 diagnosability: when a child cannot even import activegraph,
    # preflight raises with the real cause rather than letting the first
    # real trial report an opaque crash.
    with pytest.raises(SandboxStartupError) as excinfo:
        sandbox._preflight_with(_bare_env(), python_flags=("-S",), timeout=30.0)
    assert "ModuleNotFoundError" in str(excinfo.value)


def test_explicit_code_channel_rescues_a_restricted_child():
    # THE fix: the parent-resolved PYTHONPATH channel lets a child start
    # in an env whose DEFAULT path cannot find activegraph — without
    # widening the allow-list. Bare (-S) fails; the same interpreter
    # with the computed channel starts clean.
    with pytest.raises(SandboxStartupError):
        sandbox._preflight_with(_bare_env(), python_flags=("-S",), timeout=30.0)
    # _child_env adds only the code channel; still -S, still no ambient
    # discovery var — the child imports activegraph purely via the
    # explicit channel.
    assert (
        sandbox._preflight_with(
            sandbox._child_env(TrialLimits()),
            python_flags=("-S",),
            timeout=30.0,
        )
        == ()
    )


def test_env_allow_list_stays_closed_secrets_do_not_leak(monkeypatch):
    # The channel fix must not widen the boundary: an ambient secret
    # never crosses into the child, and only the code path is added.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("REPLIT_PYTHONPATH", "/nix/ambient/should-not-cross")
    env = sandbox._child_env(TrialLimits())
    assert "OPENAI_API_KEY" not in env
    assert "REPLIT_PYTHONPATH" not in env
    assert set(env) <= {"PATH", "HOME", "LANG", "PYTHONPATH"}
    # PYTHONPATH is the computed channel, and it makes activegraph's
    # root discoverable.
    import activegraph
    from pathlib import Path

    ag_root = str(Path(activegraph.__file__).resolve().parent.parent)
    assert ag_root in env["PYTHONPATH"].split(os.pathsep)


def test_env_passthrough_still_forwards_named_vars(monkeypatch):
    # The one explicit opt-in still works, and composes with the channel.
    monkeypatch.setenv("MY_TRIAL_FLAG", "1")
    env = sandbox._child_env(TrialLimits(env_passthrough=("MY_TRIAL_FLAG",)))
    assert env["MY_TRIAL_FLAG"] == "1"
    assert "PYTHONPATH" in env


# ------------------- portable RLIMIT_AS (v1.7.1 macOS soak fix)

resource = pytest.importorskip("resource")
from activegraph.sandbox import _child  # noqa: E402


def test_rlimit_as_applies_cleanly_on_linux():
    # Linux shape: a settable RLIMIT_AS applies with no degradation.
    warnings = _child._apply_rlimits(
        {"max_rss_bytes": 1024 * 2**20, "cpu_seconds": 60}
    )
    assert warnings == []


def test_rlimit_as_rejection_degrades_not_crashes(monkeypatch):
    # macOS shape (simulated): Darwin rejects setrlimit(RLIMIT_AS) with
    # exactly this ValueError. The child must NOT crash and must NOT
    # silently skip — it degrades with a loud, announced warning. This
    # is the regression guard for the macOS rotation-1 crash.
    real = resource.setrlimit

    def darwin(which, pair):
        if which == resource.RLIMIT_AS:
            raise ValueError("current limit exceeds maximum limit")
        return real(which, pair)

    monkeypatch.setattr(resource, "setrlimit", darwin)
    warnings = _child._apply_rlimits(
        {"max_rss_bytes": 256 * 2**20, "cpu_seconds": 60}
    )
    assert len(warnings) == 1
    assert "memory net is OFF" in warnings[0]
    assert "RLIMIT_AS" in warnings[0]
    # The CPU cap (which Darwin DOES accept) still applied — only the
    # unsupported net degraded.
    assert "RLIMIT_CPU" not in warnings[0]


def test_rlimit_never_raises_the_hard_limit(monkeypatch):
    # The Darwin crash was setrlimit raising the hard limit; we clamp to
    # the existing hard limit so the call only ever LOWERS. Prove the
    # target passed to setrlimit never exceeds the current hard limit.
    seen = {}
    real_get = resource.getrlimit
    monkeypatch.setattr(
        resource, "getrlimit", lambda w: (0, 512 * 2**20)  # finite hard
    )

    def capture(which, pair):
        seen[which] = pair

    monkeypatch.setattr(resource, "setrlimit", capture)
    _child._apply_rlimits({"max_rss_bytes": 4096 * 2**20})  # ask for MORE
    soft, hard = seen[resource.RLIMIT_AS]
    assert soft == hard == 512 * 2**20  # clamped to existing hard, not raised


def test_preflight_exercises_the_limit_path():
    # Fix 2: preflight must send a limits block so it reaches
    # _apply_rlimits — a null job that skipped it passed on macOS where
    # every real trial then crashed.
    probe = sandbox._limits_job_block(TrialLimits(), probe=True)
    assert probe["max_rss_bytes"] == sandbox._PREFLIGHT_PROBE_RSS
    # A caller's explicit cap is probed as-is.
    probe2 = sandbox._limits_job_block(
        TrialLimits(max_rss_bytes=333 * 2**20), probe=True
    )
    assert probe2["max_rss_bytes"] == 333 * 2**20
    # A real trial's block keeps the caller's None (no probe fill).
    real = sandbox._limits_job_block(TrialLimits(), probe=False)
    assert real["max_rss_bytes"] is None


def test_trial_report_surfaces_degraded_warning(tmp_path, monkeypatch):
    # End-to-end parent surfacing: a child that reports a degraded net
    # must land it in TrialReport.warnings AND detail (loud), on an
    # otherwise-completed trial.
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    root, bundle = _candidate_dir(tmp_path)

    degraded = "memory cap (RLIMIT_AS) could not be applied (darwin: ...); the memory net is OFF for this trial"

    def fake_run_child(job, *, env, wall_clock, python_flags=()):
        tail = {
            "outcome": "completed",
            "fork_run_id": job["fork_run_id"],
            "events_appended": 1,
            "behavior_failures": 0,
            "detail": "",
            "warnings": [degraded],
        }
        import json as _json

        return 0, _json.dumps(tail), "", False

    monkeypatch.setattr(sandbox, "_run_child", fake_run_child)
    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
        limits=TrialLimits(max_rss_bytes=256 * 2**20),
    )
    assert report.outcome == "completed"
    assert report.warnings == (degraded,)
    assert "degraded" in report.detail
    assert "memory net is OFF" in report.detail


def test_real_trial_with_memory_cap_completes_on_linux(tmp_path):
    # Integration: a real trial carrying a memory cap runs clean on
    # Linux (the net applied, no degradation) — proving the portable
    # path did not break the platform where RLIMIT_AS works.
    path, parent_run, tip, n_parent = _parent_store(tmp_path)
    root, bundle = _candidate_dir(tmp_path)

    report = run_forked_trial(
        path,
        parent_run_id=parent_run,
        at_event=tip,
        pack_source=PackSource(root_dir=str(root), expected_bundle_hash=bundle),
        scenario="scenario.py",
        limits=TrialLimits(max_rss_bytes=1024 * 2**20, wall_clock_seconds=60),
    )
    assert report.outcome == "completed", report.detail
    assert report.warnings == ()
    _parent_untouched(path, parent_run, n_parent)
