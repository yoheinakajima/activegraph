"""Explicit, log-backed local dev.override receipts (CONTRACT v1.8 #13–#15)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from activegraph import DevOverride, Graph, Runtime, behavior
from activegraph.store import InMemoryEventStore


def test_dev_override_emits_complete_marker_before_returning_receipt() -> None:
    runtime = Runtime(Graph(), behaviors=[])
    receipt = runtime.dev_override(
        actor="local-developer",
        reason="exercise a gated fixture",
        target_gate="pack.fixture.approval",
        scope="pack:demo/object:fixture-1",
        resulting_authority="R2",
    )

    assert isinstance(receipt, DevOverride)
    event = runtime.graph.events[-1]
    assert event.type == "dev.override"
    assert event.id == receipt.event_id
    assert event.actor == "local-developer"
    assert event.payload == {
        "actor": "local-developer",
        "reason": "exercise a gated fixture",
        "target_gate": "pack.fixture.approval",
        "scope": "pack:demo/object:fixture-1",
        "resulting_authority": "R2",
    }
    assert runtime.dev_overrides() == [receipt]


def test_dev_override_marker_does_not_schedule_behaviors() -> None:
    calls = 0

    @behavior(name="must_not_run", on=["dev.override"])
    def must_not_run(event, graph, ctx):
        nonlocal calls
        calls += 1

    runtime = Runtime(Graph(), behaviors=[must_not_run])
    runtime.dev_override(
        actor="dev",
        reason="trace only",
        target_gate="pack.fixture.approval",
        scope="fixture:one",
        resulting_authority="R1",
    )
    runtime.run_until_idle()
    assert calls == 0


def test_validation_is_exact_run_local_and_authority_bounded() -> None:
    runtime = Runtime(Graph(run_id="run_local"), behaviors=[])
    receipt = runtime.dev_override(
        actor="dev",
        reason="local test",
        target_gate="pack.fixture.approval",
        scope="fixture:one",
        resulting_authority="R2",
    )

    assert runtime.validate_dev_override(
        receipt,
        target_gate="pack.fixture.approval",
        scope="fixture:one",
        required_authority="R0",
    )
    assert runtime.validate_dev_override(
        receipt,
        target_gate="pack.fixture.approval",
        scope="fixture:one",
        required_authority="R2",
    )
    assert not runtime.validate_dev_override(
        receipt,
        target_gate="pack.fixture.approval",
        scope="fixture:one",
        required_authority="R3",
    )
    assert not runtime.validate_dev_override(
        receipt,
        target_gate="pack.fixture.approval",
        scope="fixture:*",
        required_authority="R1",
    )
    assert not runtime.validate_dev_override(
        replace(receipt, reason="fabricated"),
        target_gate="pack.fixture.approval",
        scope="fixture:one",
        required_authority="R1",
    )
    other = Runtime(Graph(run_id="run_other"), behaviors=[])
    assert not other.validate_dev_override(
        receipt,
        target_gate="pack.fixture.approval",
        scope="fixture:one",
        required_authority="R1",
    )


@pytest.mark.parametrize(
    "target_gate,resulting_authority",
    [
        ("promote", "R1"),
        ("promote.conflict", "R1"),
        ("promotion.apply", "R1"),
        ("event.logging", "R1"),
        ("event_log.append", "R1"),
        ("pack.fixture.approval", "R4"),
    ],
)
def test_non_bypassable_targets_and_r4_fail_before_emission(
    target_gate: str, resulting_authority: str
) -> None:
    runtime = Runtime(Graph(), behaviors=[])
    with pytest.raises(ValueError):
        runtime.dev_override(
            actor="dev",
            reason="should fail",
            target_gate=target_gate,
            scope="fixture:one",
            resulting_authority=resulting_authority,
        )
    assert runtime.graph.events == []


def test_validator_rejects_promotion_and_r4_even_for_fabricated_receipt() -> None:
    runtime = Runtime(Graph(run_id="run_local"), behaviors=[])
    fabricated = DevOverride(
        event_id="evt_missing",
        run_id="run_local",
        actor="dev",
        reason="fabricated",
        target_gate="promote.conflict",
        scope="run:fork",
        resulting_authority="R4",
    )
    assert not runtime.validate_dev_override(
        fabricated,
        target_gate="promote.conflict",
        scope="run:fork",
        required_authority="R4",
    )


def test_receipts_rebuild_on_load_and_rebind_to_fork_run(tmp_path) -> None:
    path = str(tmp_path / "override.db")
    runtime = Runtime(Graph(), behaviors=[], persist_to=path)
    receipt = runtime.dev_override(
        actor="dev",
        reason="persist me",
        target_gate="pack.fixture.approval",
        scope="fixture:one",
        resulting_authority="R1",
    )

    loaded = Runtime.load(path, run_id=runtime.run_id, behaviors=[])
    assert loaded.dev_overrides() == [receipt]

    fork = runtime.fork(at_event=receipt.event_id, behaviors=[])
    [fork_receipt] = fork.dev_overrides()
    assert fork_receipt.event_id == receipt.event_id
    assert fork_receipt.run_id == fork.run_id
    assert not fork.validate_dev_override(
        receipt,
        target_gate=receipt.target_gate,
        scope=receipt.scope,
        required_authority="R1",
    )
    assert fork.validate_dev_override(
        fork_receipt,
        target_gate=fork_receipt.target_gate,
        scope=fork_receipt.scope,
        required_authority="R1",
    )


def test_durable_append_failure_never_returns_override_receipt() -> None:
    class FailingStore(InMemoryEventStore):
        def append(self, event):
            raise OSError("durable append failed")

    runtime = Runtime(
        Graph(run_id="run_failure"),
        behaviors=[],
        store=FailingStore(run_id="run_failure"),
    )
    with pytest.raises(OSError, match="durable append failed"):
        runtime.dev_override(
            actor="dev",
            reason="cannot become usable",
            target_gate="pack.fixture.approval",
            scope="fixture:one",
            resulting_authority="R1",
        )
