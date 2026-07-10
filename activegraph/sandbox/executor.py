"""Provider-neutral trial execution interface. CONTRACT v1.8 #9–#12."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Protocol, runtime_checkable

from activegraph.sandbox import PackSource, TrialLimits, TrialReport


@dataclass(frozen=True)
class TrialIsolationGuarantees:
    """An executor's explicit process and host-isolation claims."""

    process: str
    filesystem: str
    network: str
    syscalls: str
    environment: str
    security_sandbox: bool
    notes: tuple[str, ...] = ()


LOCAL_SUBPROCESS_ISOLATION = TrialIsolationGuarantees(
    process="fresh_interpreter_subprocess",
    filesystem="shared_host_filesystem",
    network="unconfined",
    syscalls="unconfined",
    environment="closed_allowlist_plus_explicit_code_paths",
    security_sandbox=False,
    notes=(
        "crash and parent-state isolation only",
        "resource limits are platform-dependent and reported when degraded",
    ),
)


@dataclass(frozen=True)
class TrialSpecification:
    """Versioned, JSON-serializable intent for one pinned trial."""

    store_path: str
    parent_run_id: str
    at_event: str
    pack_source: PackSource
    scenario: str = ""
    limits: TrialLimits = TrialLimits()
    label: str = "trial"
    extra_packs: tuple[PackSource, ...] = ()
    schema_version: int = 1

    def to_json(self) -> str:
        """Serialize this specification as canonical versioned JSON."""

        payload = {
            "schema_version": self.schema_version,
            "store_path": self.store_path,
            "parent_run_id": self.parent_run_id,
            "at_event": self.at_event,
            "pack_source": _pack_source_payload(self.pack_source),
            "scenario": self.scenario,
            "limits": _limits_payload(self.limits),
            "label": self.label,
            "extra_packs": [
                _pack_source_payload(source) for source in self.extra_packs
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, serialized: str) -> "TrialSpecification":
        """Parse and validate a versioned serialized specification."""

        try:
            payload = json.loads(serialized)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("trial specification must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("trial specification must be a JSON object")
        version = payload.get("schema_version")
        if version != 1:
            raise ValueError(
                f"unsupported trial specification schema_version {version!r}"
            )
        store_path = _required_string(payload, "store_path")
        parent_run_id = _required_string(payload, "parent_run_id")
        at_event = _required_string(payload, "at_event")
        label = _required_string(payload, "label")
        scenario = payload.get("scenario", "")
        if not isinstance(scenario, str):
            raise ValueError("trial specification scenario must be a string")
        pack_source = _parse_pack_source(payload.get("pack_source"), "pack_source")
        extra_payload = payload.get("extra_packs", [])
        if not isinstance(extra_payload, list):
            raise ValueError("trial specification extra_packs must be a list")
        extra_packs = tuple(
            _parse_pack_source(item, f"extra_packs[{index}]")
            for index, item in enumerate(extra_payload)
        )
        limits = _parse_limits(payload.get("limits"))
        return cls(
            store_path=store_path,
            parent_run_id=parent_run_id,
            at_event=at_event,
            pack_source=pack_source,
            scenario=scenario,
            limits=limits,
            label=label,
            extra_packs=extra_packs,
            schema_version=1,
        )


@dataclass(frozen=True)
class TrialBudgetUse:
    """Store-authoritative work counts plus the requested limit set."""

    events_appended: int
    behavior_failures: int
    limits: TrialLimits


@dataclass(frozen=True)
class TrialArtifactReference:
    """Reference to one artifact produced by an executor."""

    name: str
    uri: str
    media_type: Optional[str] = None
    digest: Optional[str] = None


@dataclass(frozen=True)
class TrialEventLogReference:
    """Location of the event/log authority for one trial."""

    store_path: str
    run_id: str


@dataclass(frozen=True)
class TrialFailureDetails:
    """Structured terminal failure information."""

    kind: str
    message: str
    exit_code: Optional[int] = None


@dataclass(frozen=True)
class TrialResult:
    """Provider-neutral structured result returned by a TrialExecutor."""

    status: str
    budget_use: TrialBudgetUse
    artifacts: tuple[TrialArtifactReference, ...]
    event_log: TrialEventLogReference
    failure: Optional[TrialFailureDetails]
    isolation: TrialIsolationGuarantees
    detail: str = ""
    exit_code: Optional[int] = None
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_report(
        cls,
        report: TrialReport,
        *,
        specification: TrialSpecification,
        isolation: TrialIsolationGuarantees,
        artifacts: tuple[TrialArtifactReference, ...] = (),
    ) -> "TrialResult":
        """Lift a legacy local report into the provider-neutral result."""

        failure = (
            None
            if report.outcome == "completed"
            else TrialFailureDetails(
                kind=report.outcome,
                message=report.detail,
                exit_code=report.exit_code,
            )
        )
        return cls(
            status=report.outcome,
            budget_use=TrialBudgetUse(
                events_appended=report.events_appended,
                behavior_failures=report.behavior_failures,
                limits=specification.limits,
            ),
            artifacts=artifacts,
            event_log=TrialEventLogReference(
                store_path=specification.store_path,
                run_id=report.fork_run_id,
            ),
            failure=failure,
            isolation=isolation,
            detail=report.detail,
            exit_code=report.exit_code,
            warnings=report.warnings,
        )

    def to_report(self) -> TrialReport:
        """Return the lossless legacy ``run_forked_trial`` report shape."""

        return TrialReport(
            outcome=self.status,
            fork_run_id=self.event_log.run_id,
            events_appended=self.budget_use.events_appended,
            behavior_failures=self.budget_use.behavior_failures,
            detail=self.detail,
            exit_code=self.exit_code,
            warnings=self.warnings,
        )


@runtime_checkable
class TrialExecutor(Protocol):
    """Execute a serialized trial behind declared isolation guarantees."""

    @property
    def isolation_guarantees(self) -> TrialIsolationGuarantees:
        """Return the adapter's honest host/process isolation claims."""

        ...

    def execute(self, serialized_specification: str) -> TrialResult:
        """Execute one validated serialized specification."""

        ...


class LocalSubprocessTrialExecutor:
    """Default executor using the existing fresh-interpreter child path."""

    @property
    def isolation_guarantees(self) -> TrialIsolationGuarantees:
        """Declare crash isolation without claiming a security sandbox."""

        return LOCAL_SUBPROCESS_ISOLATION

    def execute(self, serialized_specification: str) -> TrialResult:
        """Run one specification through the behavior-preserving local path."""

        specification = TrialSpecification.from_json(serialized_specification)
        from activegraph.sandbox import _run_forked_trial_local

        report = _run_forked_trial_local(
            specification.store_path,
            parent_run_id=specification.parent_run_id,
            at_event=specification.at_event,
            pack_source=specification.pack_source,
            scenario=specification.scenario,
            limits=specification.limits,
            label=specification.label,
            extra_packs=specification.extra_packs,
        )
        return TrialResult.from_report(
            report,
            specification=specification,
            isolation=self.isolation_guarantees,
        )


class RecordingTrialExecutor:
    """Deterministic executor double that records specs and returns fixtures."""

    def __init__(
        self,
        results: Iterable[TrialResult],
        *,
        isolation_guarantees: Optional[TrialIsolationGuarantees] = None,
    ) -> None:
        self._results = list(results)
        self._isolation = isolation_guarantees or TrialIsolationGuarantees(
            process="none_test_double",
            filesystem="none",
            network="none",
            syscalls="none",
            environment="none",
            security_sandbox=False,
            notes=("records calls only; executes no candidate code",),
        )
        self.serialized_specifications: list[str] = []
        self.specifications: list[TrialSpecification] = []

    @property
    def isolation_guarantees(self) -> TrialIsolationGuarantees:
        """Declare that the double performs no external execution."""

        return self._isolation

    def execute(self, serialized_specification: str) -> TrialResult:
        """Record a validated specification and return the next fixture."""

        specification = TrialSpecification.from_json(serialized_specification)
        self.serialized_specifications.append(serialized_specification)
        self.specifications.append(specification)
        if not self._results:
            raise RuntimeError("RecordingTrialExecutor has no result remaining")
        return self._results.pop(0)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"trial specification {key} must be a non-empty string")
    return value


def _pack_source_payload(source: PackSource) -> dict[str, Any]:
    return {
        "root_dir": source.root_dir,
        "expected_bundle_hash": source.expected_bundle_hash,
        "manifest_required": source.manifest_required,
    }


def _parse_pack_source(value: Any, field_name: str) -> PackSource:
    if not isinstance(value, dict):
        raise ValueError(f"trial specification {field_name} must be an object")
    root_dir = value.get("root_dir")
    expected_hash = value.get("expected_bundle_hash", "")
    manifest_required = value.get("manifest_required", True)
    if not isinstance(root_dir, str) or not root_dir:
        raise ValueError(f"trial specification {field_name}.root_dir is required")
    if not isinstance(expected_hash, str):
        raise ValueError(
            f"trial specification {field_name}.expected_bundle_hash must be a string"
        )
    if not isinstance(manifest_required, bool):
        raise ValueError(
            f"trial specification {field_name}.manifest_required must be boolean"
        )
    return PackSource(
        root_dir=root_dir,
        expected_bundle_hash=expected_hash,
        manifest_required=manifest_required,
    )


def _limits_payload(limits: TrialLimits) -> dict[str, Any]:
    return {
        "wall_clock_seconds": limits.wall_clock_seconds,
        "max_rss_bytes": limits.max_rss_bytes,
        "max_events": limits.max_events,
        "max_llm_calls": limits.max_llm_calls,
        "env_passthrough": list(limits.env_passthrough),
    }


def _parse_limits(value: Any) -> TrialLimits:
    if not isinstance(value, dict):
        raise ValueError("trial specification limits must be an object")
    wall_clock = value.get("wall_clock_seconds", 120.0)
    max_rss = value.get("max_rss_bytes")
    max_events = value.get("max_events", 2_000)
    max_llm = value.get("max_llm_calls", 0)
    env = value.get("env_passthrough", [])
    if isinstance(wall_clock, bool) or not isinstance(wall_clock, (int, float)):
        raise ValueError("trial limits wall_clock_seconds must be numeric")
    for name, item in (
        ("max_rss_bytes", max_rss),
        ("max_events", max_events),
        ("max_llm_calls", max_llm),
    ):
        if item is not None and (isinstance(item, bool) or not isinstance(item, int)):
            raise ValueError(f"trial limits {name} must be an integer or null")
    if not isinstance(env, list) or any(not isinstance(item, str) for item in env):
        raise ValueError("trial limits env_passthrough must be a list of strings")
    return TrialLimits(
        wall_clock_seconds=float(wall_clock),
        max_rss_bytes=max_rss,
        max_events=max_events,
        max_llm_calls=max_llm,
        env_passthrough=tuple(env),
    )


__all__ = [
    "LocalSubprocessTrialExecutor",
    "RecordingTrialExecutor",
    "TrialArtifactReference",
    "TrialBudgetUse",
    "TrialEventLogReference",
    "TrialExecutor",
    "TrialFailureDetails",
    "TrialIsolationGuarantees",
    "TrialResult",
    "TrialSpecification",
]
