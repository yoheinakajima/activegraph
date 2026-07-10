"""TrialExecutor interface, local adapter, conformance, and recording double."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from activegraph.sandbox import (
    LocalSubprocessTrialExecutor,
    PackSource,
    RecordingTrialExecutor,
    TrialArtifactReference,
    TrialBudgetUse,
    TrialEventLogReference,
    TrialFailureDetails,
    TrialIsolationGuarantees,
    TrialLimits,
    TrialResult,
    TrialSpecification,
)
from activegraph.sandbox.conformance import TrialExecutorConformance
from tests.test_sandbox_trial import _candidate_dir, _parent_store


class TestLocalSubprocessTrialExecutor(TrialExecutorConformance):
    """Run the reusable adapter suite against the first-party default."""

    __test__ = True

    def setup_method(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        store, parent_run, tip, _ = _parent_store(root)
        candidate, bundle = _candidate_dir(root)
        self._specification = TrialSpecification(
            store_path=store,
            parent_run_id=parent_run,
            at_event=tip,
            pack_source=PackSource(
                root_dir=str(candidate), expected_bundle_hash=bundle
            ),
            scenario="scenario.py",
            limits=TrialLimits(wall_clock_seconds=60),
        )

    def teardown_method(self) -> None:
        self._temp.cleanup()

    def make_executor(self) -> LocalSubprocessTrialExecutor:
        return LocalSubprocessTrialExecutor()

    def make_serialized_specification(self) -> str:
        return self._specification.to_json()


def _recorded_result(isolation: TrialIsolationGuarantees) -> TrialResult:
    return TrialResult(
        status="scenario_failed",
        budget_use=TrialBudgetUse(
            events_appended=3,
            behavior_failures=1,
            limits=TrialLimits(max_events=10),
        ),
        artifacts=(
            TrialArtifactReference(
                name="summary", uri="memory://summary", media_type="text/plain"
            ),
        ),
        event_log=TrialEventLogReference(
            store_path="record.db", run_id="run_recorded"
        ),
        failure=TrialFailureDetails(
            kind="scenario_failed", message="fixture failure", exit_code=30
        ),
        isolation=isolation,
        detail="fixture failure",
        exit_code=30,
    )


def test_recording_executor_records_parsed_spec_and_returns_fixture() -> None:
    isolation = TrialIsolationGuarantees(
        process="none",
        filesystem="none",
        network="none",
        syscalls="none",
        environment="none",
        security_sandbox=False,
    )
    fixture = _recorded_result(isolation)
    executor = RecordingTrialExecutor(
        [fixture], isolation_guarantees=isolation
    )
    specification = TrialSpecification(
        store_path="record.db",
        parent_run_id="run_parent",
        at_event="evt_001",
        pack_source=PackSource(
            root_dir="/pinned/pack", expected_bundle_hash="sha256:" + "a" * 64
        ),
    )

    assert executor.execute(specification.to_json()) == fixture
    assert executor.specifications == [specification]
    assert executor.serialized_specifications == [specification.to_json()]
    with pytest.raises(RuntimeError, match="no result remaining"):
        executor.execute(specification.to_json())


def test_local_executor_declares_honest_non_security_isolation() -> None:
    guarantees = LocalSubprocessTrialExecutor().isolation_guarantees
    assert guarantees.process == "fresh_interpreter_subprocess"
    assert guarantees.filesystem == "shared_host_filesystem"
    assert guarantees.network == "unconfined"
    assert guarantees.syscalls == "unconfined"
    assert guarantees.security_sandbox is False


def test_specification_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        TrialSpecification.from_json('{"schema_version":2}')
