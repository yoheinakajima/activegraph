"""Bounded, offline article evidence; no runtime changes or live providers.

Run from repository root with the local package installed. Every output goes
under the explicit --output directory. Two fixed-fixture executions plus one
small strict-replay boundary check; no API keys, HTTP, services, or optimizers.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import socket
import sys
import tempfile

BASELINE = "7070112c63b7cffc4b3aef8b7e907c8f2f9a9165"


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    attempts = []

    def no_network(*a, **k):
        attempts.append("blocked socket operation")
        raise AssertionError("article verification forbids network access")

    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.socket.bind = no_network
    socket.create_connection = no_network
    socket.getaddrinfo = no_network

    import activegraph
    input_pin = json.loads((Path(__file__).parent / "runtime-inputs.json").read_text())
    assert input_pin["baseline_commit"] == BASELINE
    package_root = Path(activegraph.__file__).resolve().parent
    for record in input_pin["files"]:
        path = package_root / record["path"]
        assert path.is_file() and sha(path.read_bytes()) == record["sha256"], (
            "imported runtime/fixture does not match pinned baseline: " + record["path"])
    actual_paths = {str(p.relative_to(package_root)) for p in package_root.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
                    and p.name != ".DS_Store"}
    assert actual_paths == {r["path"] for r in input_pin["files"]}, "unexpected/missing package files"
    from activegraph import (FrozenClock, Graph, IDGen, Runtime, behavior,
                             clear_registry, clear_tool_registry, configure_logging,
                             ReplayDivergenceError)
    from activegraph.packs.diligence import DiligenceSettings, pack
    from activegraph.packs.diligence.fixtures import (
        RecordedDiligenceProvider, THREE_COMPANIES, company_goal)

    configure_logging(level="ERROR", json_output=False)

    def projection(graph):
        return canonical({"objects": [o.to_dict() for o in graph.all_objects()],
                          "relations": [r.to_dict() for r in graph.all_relations()]})

    def fixture_run(db):
        clear_registry()
        clear_tool_registry()
        graph = Graph(ids=IDGen(), clock=FrozenClock("2026-01-01T00:00:00Z"),
                      run_id="ieee_fixture_run")
        runtime = Runtime(graph, llm_provider=RecordedDiligenceProvider(companies=THREE_COMPANIES),
                          persist_to=str(db), seed=0,
                          budget={"max_llm_calls": 80, "max_tool_calls": 100,
                                  "max_cost_usd": "5.00"})
        runtime.load_pack(pack, settings=DiligenceSettings(
            llm_model="claude-sonnet-4-5", max_documents_per_company=5,
            max_claims_per_document=10, confidence_threshold_for_review=0.7,
            min_questions=8, max_questions=12))
        for company in THREE_COMPANIES:
            runtime.run_goal(company_goal(company))
        runtime.save_state()
        return runtime

    with tempfile.TemporaryDirectory(prefix="ieee-fixture-", dir=args.output) as scratch:
        scratch = Path(scratch)
        first = fixture_run(scratch / "first.sqlite3")
        first_events = b"".join(canonical(e.to_dict()) for e in first.graph.events)
        first_projection = projection(first.graph)
        second = fixture_run(scratch / "second.sqlite3")
        second_events = b"".join(canonical(e.to_dict()) for e in second.graph.events)
        second_projection = projection(second.graph)
        differing_fields = []
        def differences(a, b, path=""):
            if isinstance(a, dict) and isinstance(b, dict):
                for key in sorted(set(a) | set(b)):
                    if key not in a or key not in b:
                        yield path + "." + key
                    else:
                        yield from differences(a[key], b[key], path + "." + key)
            elif a != b:
                yield path.lstrip(".")
        assert len(first.graph.events) == len(second.graph.events)
        for left, right in zip(first.graph.events, second.graph.events):
            fields = list(differences(left.to_dict(), right.to_dict()))
            if fields:
                differing_fields.append({"event_id": left.id, "type": left.type, "fields": fields})
                assert left.type == "tool.responded" and fields == ["payload.latency_seconds"], fields
        def normalized_events(events):
            values = []
            for event in events:
                value = event.to_dict()
                if value["type"] == "tool.responded":
                    value["payload"].pop("latency_seconds", None)
                values.append(value)
            return b"".join(canonical(value) for value in values)
        normalized_first = normalized_events(first.graph.events)
        normalized_second = normalized_events(second.graph.events)
        assert normalized_first == normalized_second, "non-telemetry fixture events differ"
        assert first_projection == second_projection, "fixture projection differs"

        loaded = Runtime.load(str(scratch / "first.sqlite3"), run_id=first.run_id)
        assert projection(loaded.graph) == first_projection, "projection reload differs"
        saved_events = first_events
        point = first.graph.events[-1].id
        fork = first.fork(at_event=point, replay_llm_cache=True,
                          replay_tool_cache=True, label="article-fixture-fork")
        copied_prefix = [e.to_dict() for e in fork.graph.events[:len(first.graph.events)]]
        assert copied_prefix == [e.to_dict() for e in first.graph.events]
        fork.graph.add_object("article_note", {"text": "isolated branch annotation"})
        assert b"".join(canonical(e.to_dict()) for e in first.graph.events) == saved_events
        assert len(fork.graph.all_objects()) == len(first.graph.all_objects()) + 1

        clear_registry()
        clear_tool_registry()
        state = {"text": "original", "extra": False}

        @behavior(name="article_payload_boundary", on=["goal.created"])
        def payload_boundary(event, graph, ctx):
            graph.add_object("note", {"text": state["text"]})
            if state["extra"]:
                graph.add_object("note", {"text": "extra event"})

        small = Runtime(Graph(clock=FrozenClock("2026-01-01T00:00:00Z"),
                              run_id="ieee_strict_boundary"),
                        behaviors=[payload_boundary], persist_to=str(scratch / "strict.sqlite3"))
        small.run_goal("check the verification boundary")
        state["text"] = "different payload, same event types"
        Runtime.load(str(scratch / "strict.sqlite3"), run_id=small.run_id,
                     behaviors=[payload_boundary], replay_strict=True)
        state["extra"] = True
        divergence = False
        try:
            Runtime.load(str(scratch / "strict.sqlite3"), run_id=small.run_id,
                         behaviors=[payload_boundary], replay_strict=True)
        except ReplayDivergenceError:
            divergence = True
        assert divergence, "extra-event drift was not rejected"
        assert not attempts, "a forbidden network operation was attempted"

        types = Counter(e.type for e in first.graph.events)
        objtypes = Counter(o.type for o in first.graph.all_objects())
        result = {
            "schema_version": 1, "baseline_commit": BASELINE,
            "imported_runtime_identity_verified": True,
            "runtime_files_verified": len(input_pin["files"]),
            "runtime_pin_sha256": sha((Path(__file__).parent / "runtime-inputs.json").read_bytes()),
            "runtime_resolution": "activegraph.__file__.resolve().parent, all expected package/fixture bytes and exact file inventory checked",
            "python": platform.python_version(), "scope": "fixed synthetic fixture; not a live diligence study",
            "fixture_provider": "RecordedDiligenceProvider", "companies": len(THREE_COMPANIES),
            "events": len(first.graph.events), "objects": len(first.graph.all_objects()),
            "relations": len(first.graph.all_relations()), "object_types": dict(sorted(objtypes.items())),
            "event_types": dict(sorted(types.items())), "llm_request_events": types["llm.requested"],
            "tool_request_events": types["tool.requested"], "network_attempts": len(attempts),
            "new_live_provider_calls": 0,
            "full_event_jsonl_sha256": sha(first_events), "projection_sha256": sha(first_projection),
            "second_full_event_jsonl_sha256": sha(second_events),
            "two_fixture_event_jsonl_equal": first_events == second_events,
            "normalization": "omit only payload.latency_seconds on tool.responded events",
            "normalized_event_jsonl_sha256": sha(normalized_first),
            "two_normalized_fixture_event_jsonl_equal": True,
            "differing_event_fields": differing_fields,
            "two_fixture_projections_equal": True,
            "reload_projection_equal": True, "fork_copied_inclusive_completed_prefix": True,
            "fork_annotation_parent_unchanged": True,
            "strict_payload_only_drift_accepted": True,
            "strict_extra_event_drift_rejected": True,
            "limitations": ["Raw event bytes differ in measured tool latency; projection and explicitly normalized event equality only.",
                            "Not proof of arbitrary behavior determinism or truth of generated claims.",
                            "Fork check uses a completed valid cutoff and no changed model call.",
                            "No latency, cost, scale, failure-recovery, or competitor superiority measurement.",
                            "Recorded fixture cost/latency fields are fixture values, not this run's bill."]}
        (args.output / "fixture-events.jsonl").write_bytes(first_events)
        (args.output / "fixture-events-second.jsonl").write_bytes(second_events)
        (args.output / "fixture-events-normalized.jsonl").write_bytes(normalized_first)
        (args.output / "fixture-projection.json").write_bytes(first_projection)
        (args.output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k: v for k, v in result.items() if k not in ("object_types", "event_types", "limitations", "differing_event_fields")}, indent=2))


if __name__ == "__main__":
    main()
