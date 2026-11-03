"""Derive review clarifications from sealed saved outputs; never import runtime."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PACKET = Path(__file__).resolve().parents[1]

def require(value, message):
    if not value:
        raise RuntimeError(message)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    pairs = []
    for rel, expected in [("evidence", 47), ("evidence/identity-checked", 48)]:
        base = PACKET / rel
        receipt = json.loads((base / "verification.json").read_text())
        require(len(receipt["differing_event_fields"]) == expected, "wrong difference count")
        require(not receipt["two_fixture_event_jsonl_equal"] and receipt["two_normalized_fixture_event_jsonl_equal"], "wrong equality contract")
        require(all(e["type"] == "tool.responded" and e["fields"] == ["payload.latency_seconds"] for e in receipt["differing_event_fields"]), "unexpected diff field")
        pairs.append({"receipt": rel + "/verification.json", "receipt_sha256": digest(base / "verification.json"),
                      "differing_events": expected, "full_event_jsonl_sha256": receipt["full_event_jsonl_sha256"],
                      "second_full_event_jsonl_sha256": receipt["second_full_event_jsonl_sha256"],
                      "normalized_event_jsonl_sha256": receipt["normalized_event_jsonl_sha256"],
                      "projection_sha256": receipt["projection_sha256"]})
    base = PACKET / "evidence/identity-checked"
    events = [json.loads(x) for x in (base / "fixture-events.jsonl").read_text().splitlines()]
    projection = json.loads((base / "fixture-projection.json").read_text())
    by_id = {e["id"]: e for e in events}
    objects = {o["id"]: o for o in projection["objects"]}
    snapshots = [{"event_id": e["id"], "snapshot": e["payload"]["snapshot"]} for e in events if e["type"] == "runtime.idle"]
    require([x["snapshot"]["used"]["max_llm_calls"] for x in snapshots] == [19, 37, 55], "invocation totals differ")
    require([x["snapshot"]["used"]["max_tool_calls"] for x in snapshots] == [16, 32, 48], "tool totals differ")
    require(snapshots[-1]["snapshot"]["cost_used_usd"] == "0.2638", "fixture cost differs")
    requests = Counter(e["payload"]["behavior"] for e in events if e["type"] == "llm.requested")
    require(sum(requests.values()) == 103 and requests["diligence.document_researcher"] == 72, "request totals differ")
    claim = objects["claim#11"]
    require(claim["data"]["source_document_id"] == "document#10", "wrong source document")
    require(claim["provenance"]["llm_request_event_id"] == "evt_027", "wrong request")
    require(claim["provenance"]["tool_request_event_ids"] == ["evt_021", "evt_025"], "wrong tools")
    require(claim["provenance"]["caused_by_event"] == "evt_009", "wrong trigger")
    require(by_id["evt_028"]["caused_by"] == "evt_027" and by_id["evt_028"]["payload"]["turn_index"] == 2, "wrong response/turn")
    require(by_id["evt_009"]["payload"]["object"]["id"] == "question#2", "wrong question")
    optimized_dir = PACKET / ".scratch/refused-optimized-output"
    require(not optimized_dir.exists(), "optimized probe output already exists")
    command = [sys.executable, "-O", "-I", str(PACKET / "verification/verify.py"), "--output", str(optimized_dir)]
    optimized = subprocess.run(command, capture_output=True, text=True)
    require(optimized.returncode != 0 and "optimized Python is refused" in optimized.stderr, "optimized verifier not rejected")
    require(not optimized_dir.exists(), "optimized verifier created output")
    result = {"mode": "static retained-output audit, not a new fixture execution", "pairs": pairs,
              "normalization_chronology": "defined after raw timing mismatch was observed; not preregistered", 
              "budget_snapshots": snapshots, "llm_request_events_by_behavior": dict(requests),
              "llm_behavior_invocations": 55, "tool_calls": 48,
              "budget_scope": "one runtime cumulative across three goals; max_llm_calls counts LLM behavior invocations, not request turns",
              "trace": {"claim": claim, "document": objects["document#10"], "question": objects["question#2"],
                        "events": [by_id[k] for k in ["evt_009", "evt_021", "evt_025", "evt_027", "evt_028"]]},
              "optimized_verifier_refusal": {"exit_code": optimized.returncode, "stderr": optimized.stderr.strip(), "no_output_created": True},
              "legacy_receipt_interpretation": {"new_live_provider_calls": "literal zero in legacy receipt; inferred from RecordedDiligenceProvider and intercepted-attempt count, not independent call telemetry", 
                                                "fork_annotation_parent_unchanged": "in-memory parent event list only; full-run final cutoff; no persisted parent reload after child edit"},
              "source_sha256": {"fixture-events.jsonl": digest(base / "fixture-events.jsonl"), "fixture-projection.json": digest(base / "fixture-projection.json")}}
    (PACKET / "evidence/review-clarifications.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"result": "PASS", "retained_pairs": len(pairs), "primary_differences": 48, "optimized_python_refused": True,
                      "no_fixture_or_runtime_import": True}, indent=2))

if __name__ == "__main__":
    main()
