"""Reusable TrialExecutor adapter conformance suite. CONTRACT v1.8 #12."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pytest

from activegraph.sandbox import TRIAL_OUTCOMES
from activegraph.sandbox.executor import (
    TrialExecutor,
    TrialResult,
    TrialSpecification,
)


class TrialExecutorConformance(ABC):
    """Mix into a pytest class to validate a TrialExecutor adapter.

    Subclasses return a fresh executor and one valid serialized
    specification per inherited test, and set ``__test__ = True``.
    """

    __test__ = False

    @abstractmethod
    def make_executor(self) -> TrialExecutor:
        """Return a fresh executor adapter for one conformance test."""

    @abstractmethod
    def make_serialized_specification(self) -> str:
        """Return one valid version-1 specification for the adapter."""

    def test_protocol_and_isolation_are_declared(self) -> None:
        executor = self.make_executor()
        assert isinstance(executor, TrialExecutor)
        guarantees = executor.isolation_guarantees
        assert guarantees.process
        assert guarantees.filesystem
        assert guarantees.network
        assert guarantees.syscalls
        assert guarantees.environment
        assert isinstance(guarantees.security_sandbox, bool)

    def test_specification_round_trip_is_canonical(self) -> None:
        serialized = self.make_serialized_specification()
        specification = TrialSpecification.from_json(serialized)
        assert TrialSpecification.from_json(specification.to_json()) == specification
        assert specification.to_json() == TrialSpecification.from_json(
            specification.to_json()
        ).to_json()

    def test_execute_returns_complete_provider_neutral_result(self) -> None:
        executor = self.make_executor()
        result = executor.execute(self.make_serialized_specification())
        assert isinstance(result, TrialResult)
        assert result.status in TRIAL_OUTCOMES
        assert result.budget_use.events_appended >= 0
        assert result.budget_use.behavior_failures >= 0
        assert isinstance(result.artifacts, tuple)
        assert result.event_log.store_path
        assert result.event_log.run_id
        assert result.isolation == executor.isolation_guarantees
        if result.status == "completed":
            assert result.failure is None
        else:
            assert result.failure is not None
            assert result.failure.kind == result.status
        report = result.to_report()
        assert report.outcome == result.status
        assert report.fork_run_id == result.event_log.run_id

    def test_malformed_specification_fails_before_execution(self) -> None:
        executor = self.make_executor()
        with pytest.raises(ValueError):
            executor.execute("{}")


__all__ = ["TrialExecutorConformance"]
