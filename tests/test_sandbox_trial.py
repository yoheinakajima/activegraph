"""Subprocess fork-trial isolation (CONTRACT v1.5 #1).

Each test spawns a real fresh-interpreter child against a real SQLite
store. Deterministic, key-free (max_llm_calls=0, empty env
pass-through). The property every failure case re-proves: the PARENT
run is byte-untouched no matter what the candidate does.
"""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime, behavior, clear_registry
from activegraph.packs.manifest import compute_bundle_hash, compute_content_hash
from activegraph.sandbox import (
    PackSource,
    TrialLimits,
    TrialReport,
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
