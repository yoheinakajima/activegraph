"""Action-class authority: ceiling, evaluation order, audit. CONTRACT v1.9.

The boundary these tests anchor on (Standing Rule §2) is the policy
evaluation itself — ``Runtime.evaluate_capability_authority`` /
``Runtime.set_authority_ceiling`` — plus the declaration surfaces
(``Pack`` construction) and the log-backed reconstruction moments
(``Runtime.load`` / ``fork``).
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel

from activegraph import Graph, Runtime
from activegraph.packs import ObjectType, Pack, PackValidationError, behavior
from activegraph.packs.manifest import CapabilityDecl
from activegraph.runtime.authority import (
    ACTION_CLASSES,
    AUTHORITY_CEILINGS,
    evaluate_action_authority,
)


def _rt() -> Runtime:
    return Runtime(Graph(), behaviors=[])


# ------------------------------------------------- closed sets and defaults


def test_default_ceiling_is_none_and_nothing_auto_approves() -> None:
    rt = _rt()
    assert rt.authority_ceiling() == "none"
    for cls in ("R0", "R1", "R2"):
        decision = rt.evaluate_capability_authority(
            capability="x.y", action_class=cls
        )
        assert decision.decision == "require_approval"
        assert decision.matched_policy == "above_ceiling"


def test_every_closed_set_class_routes_and_nothing_else_does() -> None:
    rt = _rt()
    rt.set_authority_ceiling("R2", actor="owner", reason="max routine authority")
    expected = {
        "R0": "auto_approve",
        "R1": "auto_approve",
        "R2": "auto_approve",
        "R3": "require_approval",
        "R4": "governance_gate",
    }
    assert set(expected) == set(ACTION_CLASSES)
    for cls, want in expected.items():
        assert (
            rt.evaluate_capability_authority(
                capability="x.y", action_class=cls
            ).decision
            == want
        )
    # Anything outside the closed set fails closed — including values
    # that LOOK like classes and every legacy risk label.
    for bogus in ("r0", "R5", "R2 ", "low", "medium", "high", "critical"):
        decision = rt.evaluate_capability_authority(
            capability="x.y", action_class=bogus
        )
        assert decision.decision == "require_approval"
        assert decision.matched_policy == "fail_closed_invalid_action_class"


# ------------------------------------------------- no inference, ever


def test_evaluation_has_no_risk_class_input_by_construction() -> None:
    # The no-inference rule made structural: neither the runtime method
    # nor the pure function even accepts a risk_class — there is no
    # parameter through which a legacy label could influence authority.
    for fn in (
        Runtime.evaluate_capability_authority,
        evaluate_action_authority,
        Runtime.set_authority_ceiling,
        Runtime.authority_ceiling,
    ):
        assert "risk_class" not in inspect.signature(fn).parameters


def test_risk_class_only_capability_never_gains_automation() -> None:
    # A capability declared with every legacy risk label but no
    # action_class stays ineligible at EVERY ceiling: the declared
    # (empty) class fails closed, and no code path substitutes the
    # risk label for it.
    decls = [
        CapabilityDecl(provider="legacy", capability=f"c_{risk}", risk_class=risk)
        for risk in ("low", "medium", "high", "critical")
    ]
    rt = _rt()
    for ceiling in ("R0", "R1", "R2"):
        rt.set_authority_ceiling(ceiling, actor="owner", reason="sweep")
        for decl in decls:
            decision = rt.evaluate_capability_authority(
                capability=f"{decl.provider}.{decl.capability}",
                action_class=decl.action_class,  # "" — undeclared
            )
            assert decision.decision == "require_approval"
            assert (
                decision.matched_policy == "fail_closed_missing_action_class"
            )


# ------------------------------------------------- ceiling raise/lower


def test_ceiling_raises_and_lowers_including_none() -> None:
    rt = _rt()
    rt.set_authority_ceiling("R2", actor="owner", reason="raise to max")
    assert (
        rt.evaluate_capability_authority(
            capability="x.y", action_class="R2"
        ).decision
        == "auto_approve"
    )
    rt.set_authority_ceiling("R0", actor="owner", reason="lower")
    assert rt.authority_ceiling() == "R0"
    assert (
        rt.evaluate_capability_authority(
            capability="x.y", action_class="R1"
        ).decision
        == "require_approval"
    )
    rt.set_authority_ceiling("none", actor="owner", reason="all the way down")
    assert rt.authority_ceiling() == "none"
    assert (
        rt.evaluate_capability_authority(
            capability="x.y", action_class="R0"
        ).decision
        == "require_approval"
    )


def test_ceiling_rejects_r3_r4_and_garbage_before_any_event() -> None:
    rt = _rt()
    for bad in ("R3", "R4", "medium", "", "NONE", "r1"):
        with pytest.raises(ValueError, match="ceiling"):
            rt.set_authority_ceiling(bad, actor="owner", reason="try")
    # Rejection happens before emission: the log carries no ceiling event.
    assert not [
        e for e in rt.graph.events if e.type == "authority.ceiling_changed"
    ]
    assert rt.authority_ceiling() == "none"


def test_ceiling_change_requires_actor_and_reason() -> None:
    rt = _rt()
    with pytest.raises(ValueError, match="actor"):
        rt.set_authority_ceiling("R1", actor="  ", reason="why")
    with pytest.raises(ValueError, match="reason"):
        rt.set_authority_ceiling("R1", actor="owner", reason="")


def test_capability_ceiling_can_lower_but_never_raise() -> None:
    rt = _rt()
    rt.set_authority_ceiling("R2", actor="owner", reason="raise")
    # A stricter capability policy lowers the effective ceiling...
    decision = rt.evaluate_capability_authority(
        capability="x.y", action_class="R1", capability_ceiling="R0"
    )
    assert decision.decision == "require_approval"
    assert decision.matched_policy == "stricter_local_policy"
    assert decision.effective_ceiling == "R0"
    # ...a capability ceiling of "none" turns automation off entirely...
    decision = rt.evaluate_capability_authority(
        capability="x.y", action_class="R0", capability_ceiling="none"
    )
    assert decision.decision == "require_approval"
    # ...and a LOOSER capability ceiling cannot widen a strict instance.
    rt.set_authority_ceiling("R0", actor="owner", reason="lower")
    decision = rt.evaluate_capability_authority(
        capability="x.y", action_class="R1", capability_ceiling="R2"
    )
    assert decision.decision == "require_approval"
    assert decision.matched_policy == "above_ceiling"
    assert decision.effective_ceiling == "R0"


# ------------------------------------------------- R3/R4 invariance


def test_r3_requires_approval_under_every_ceiling() -> None:
    rt = _rt()
    for ceiling in AUTHORITY_CEILINGS:
        rt.set_authority_ceiling(ceiling, actor="owner", reason="sweep")
        decision = rt.evaluate_capability_authority(
            capability="mail.send", action_class="R3"
        )
        assert decision.decision == "require_approval"
        assert decision.matched_policy == "approval_required_r3"


def test_r4_routes_to_governance_gate_under_every_ceiling() -> None:
    rt = _rt()
    for ceiling in AUTHORITY_CEILINGS:
        rt.set_authority_ceiling(ceiling, actor="owner", reason="sweep")
        decision = rt.evaluate_capability_authority(
            capability="evolution.adopt_proposal", action_class="R4"
        )
        assert decision.decision == "governance_gate"
        assert decision.matched_policy == "governance_gate_r4"
        assert not decision.auto_approved


def test_r4_invariant_even_under_stray_capability_ceiling() -> None:
    rt = _rt()
    rt.set_authority_ceiling("R2", actor="owner", reason="max")
    decision = rt.evaluate_capability_authority(
        capability="x.y", action_class="R4", capability_ceiling="R2"
    )
    assert decision.decision == "governance_gate"


# ------------------------------------------------- fail closed


def test_missing_class_fails_closed_at_evaluation_time() -> None:
    rt = _rt()
    rt.set_authority_ceiling("R2", actor="owner", reason="max")
    decision = rt.evaluate_capability_authority(
        capability="x.y", action_class=""
    )
    assert decision.decision == "require_approval"
    assert decision.matched_policy == "fail_closed_missing_action_class"


def test_invalid_capability_ceiling_fails_closed_not_widened() -> None:
    rt = _rt()
    rt.set_authority_ceiling("R2", actor="owner", reason="max")
    decision = rt.evaluate_capability_authority(
        capability="x.y", action_class="R0", capability_ceiling="R9"
    )
    assert decision.decision == "require_approval"
    assert decision.matched_policy == "fail_closed_invalid_capability_ceiling"


# ------------------------------------------------- audit records


def test_decision_audit_event_names_class_ceiling_policy_and_decision() -> None:
    rt = _rt()
    rt.set_authority_ceiling("R1", actor="owner", reason="raise")
    decision = rt.evaluate_capability_authority(
        capability="notes.label",
        action_class="R2",
        capability_ceiling="R1",
        actor="gateway",
        caused_by=None,
    )
    events = [e for e in rt.graph.events if e.type == "authority.decision"]
    assert len(events) == 1
    payload = events[0].payload
    assert payload == {
        "capability": "notes.label",
        "action_class": "R2",
        "ceiling": "R1",
        "capability_ceiling": "R1",
        "effective_ceiling": "R1",
        "matched_policy": "above_ceiling",
        "decision": "require_approval",
        "reason": payload["reason"],  # human-readable, asserted non-empty below
    }
    assert payload["reason"]
    assert events[0].actor == "gateway"
    # The returned decision carries the accepted audit event id.
    assert decision.event_id == events[0].id


def test_ceiling_change_audit_event_names_old_new_actor_reason() -> None:
    rt = _rt()
    event_id = rt.set_authority_ceiling("R1", actor="owner", reason="turn on")
    [event] = [
        e for e in rt.graph.events if e.type == "authority.ceiling_changed"
    ]
    assert event.id == event_id
    assert event.payload == {
        "ceiling": "R1",
        "previous_ceiling": "none",
        "actor": "owner",
        "reason": "turn on",
    }


# ------------------------------------------------- log-backed durability


def test_ceiling_survives_load_and_fork(tmp_path) -> None:
    path = str(tmp_path / "authority.db")
    rt = Runtime(Graph(), behaviors=[], persist_to=path)
    event_id = rt.set_authority_ceiling("R1", actor="owner", reason="persist me")

    loaded = Runtime.load(path, run_id=rt.run_id, behaviors=[])
    assert loaded.authority_ceiling() == "R1"

    fork = rt.fork(at_event=event_id, behaviors=[])
    assert fork.authority_ceiling() == "R1"


def test_authority_events_never_schedule_behaviors() -> None:
    fired: list[str] = []

    class _Thing(BaseModel):
        name: str = ""

    @behavior(
        name="authority_listener",
        on=["authority.ceiling_changed", "authority.decision"],
        creates=[],
    )
    def authority_listener(event, graph, ctx):  # type: ignore[no-untyped-def]
        """Must never fire: authority.* is bookkeeping, not input."""
        fired.append(event.type)

    pack = Pack(
        name="authority_probe",
        version="0.1.0",
        object_types=(ObjectType(name="thing", schema=_Thing),),
        behaviors=(authority_listener,),
    )
    rt = _rt()
    rt.load_pack(pack)
    rt.set_authority_ceiling("R1", actor="owner", reason="probe")
    rt.evaluate_capability_authority(capability="x.y", action_class="R0")
    rt.run_until_idle()
    assert fired == []
    # The events themselves persisted — suppression is from scheduling,
    # not from the log.
    types = [e.type for e in rt.graph.events]
    assert "authority.ceiling_changed" in types
    assert "authority.decision" in types


# ------------------------------------------------- declaration surface


def test_pack_rejects_invalid_action_class_at_construction_time() -> None:
    with pytest.raises(PackValidationError, match="action_class"):
        Pack(
            name="bad_class",
            version="0.1.0",
            capabilities=(
                CapabilityDecl(
                    provider="x",
                    capability="y",
                    risk_class="low",
                    action_class="R9",
                ),
            ),
        )
    # Legacy risk labels are not action classes.
    with pytest.raises(PackValidationError, match="action_class"):
        Pack(
            name="bad_class",
            version="0.1.0",
            capabilities=(
                CapabilityDecl(
                    provider="x",
                    capability="y",
                    risk_class="low",
                    action_class="low",
                ),
            ),
        )


def test_pack_accepts_declared_and_undeclared_action_class() -> None:
    pack = Pack(
        name="good_class",
        version="0.1.0",
        capabilities=(
            CapabilityDecl(
                provider="x", capability="read", risk_class="low",
                action_class="R0",
            ),
            CapabilityDecl(
                provider="x", capability="legacy_only", risk_class="high",
            ),
        ),
    )
    assert pack.capabilities[0].action_class == "R0"
    assert pack.capabilities[1].action_class == ""


def test_pack_loaded_payload_carries_action_class_only_when_declared() -> None:
    pack = Pack(
        name="payload_probe",
        version="0.1.0",
        capabilities=(
            CapabilityDecl(
                provider="x", capability="read", risk_class="low",
                action_class="R0",
            ),
            CapabilityDecl(
                provider="x", capability="legacy_only", risk_class="high",
            ),
        ),
    )
    rt = _rt()
    rt.load_pack(pack)
    [loaded] = [e for e in rt.graph.events if e.type == "pack.loaded"]
    caps = {c["capability"]: c for c in loaded.payload["capabilities"]}
    assert caps["read"]["action_class"] == "R0"
    # Legacy declarations keep the exact pre-v1.9 payload shape.
    assert "action_class" not in caps["legacy_only"]
