"""Bounded effect-free strict-load probe; no fixture campaign or live provider.

Only tool/function in-memory counters, a two-response fake provider and a
transient local SQLite log are used. Network interception is explicit, not an
OS sandbox. Run in an isolated credential-free process with assertions active.
"""
from __future__ import annotations
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import socket
import tempfile


def require(value, message):
    if not value:
        raise RuntimeError(message)


def main():
    if not __debug__:
        raise SystemExit("strict-effect probe refuses optimized Python")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    attempts = []
    def blocked(*a, **kw):
        attempts.append("intercepted network operation")
        raise RuntimeError("network forbidden in strict-effect probe")
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.socket.bind = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked

    import activegraph
    baseline = "7070112c63b7cffc4b3aef8b7e907c8f2f9a9165"
    pin_path = Path(__file__).parent / "runtime-inputs.json"
    pin = json.loads(pin_path.read_text())
    require(pin["baseline_commit"] == baseline, "wrong baseline pin")
    package = Path(activegraph.__file__).resolve().parent
    for r in pin["files"]:
        file = package / r["path"]
        require(file.is_file() and hashlib.sha256(file.read_bytes()).hexdigest() == r["sha256"], r["path"])
    actual = {str(p.relative_to(package)) for p in package.rglob("*") if p.is_file()
              and "__pycache__" not in p.parts and p.suffix != ".pyc" and p.name != ".DS_Store"}
    require(actual == {r["path"] for r in pin["files"]}, "package inventory differs")
    from pydantic import BaseModel
    from activegraph import Graph, Runtime, FrozenClock, behavior, llm_behavior, tool, clear_registry, clear_tool_registry, configure_logging
    from activegraph.llm import LLMResponse, ToolCall
    configure_logging(level="ERROR", json_output=False)
    clear_registry()
    clear_tool_registry()
    counts = {"tool": 0, "ordinary_behavior": 0, "fake_provider_complete": 0}
    keys = []
    class Input(BaseModel):
        n: int
    class Output(BaseModel):
        text: str
    class ToolOutput(BaseModel):
        n2: int
    @tool(name="article_effect_counter", input_schema=Input, output_schema=ToolOutput, deterministic=False)
    def counter(args, ctx):
        counts["tool"] += 1
        keys.append(ctx.idempotency_key)
        return ToolOutput(n2=args.n * 2)
    @behavior(name="article_effect_seed", on=["goal.created"])
    def seed(event, graph, ctx):
        counts["ordinary_behavior"] += 1
        graph.add_object("effect_document", {"title": "synthetic"})
    @llm_behavior(name="article_effect_model", on=["object.created"], where={"object.type": "effect_document"}, output_schema=Output, tools=[counter])
    def model_behavior(event, graph, ctx, out):
        pass
    responses = [
        LLMResponse(raw_text="", parsed=None, input_tokens=10, output_tokens=5,
                    cost_usd=Decimal("0"), latency_seconds=0, model="effect-free-fake",
                    finish_reason="tool_use", tool_calls=[ToolCall(id="c1", name="article_effect_counter", args={"n": 21})]),
        LLMResponse(raw_text="", parsed=Output(text="done"), input_tokens=10, output_tokens=5,
                    cost_usd=Decimal("0"), latency_seconds=0, model="effect-free-fake", finish_reason="end_turn")]
    class Fake:
        def complete(self, **kw):
            i = counts["fake_provider_complete"]
            require(i < len(responses), "strict verification unexpectedly requested fresh fake response")
            counts["fake_provider_complete"] += 1
            return responses[i]
        def estimate_cost(self, **kw):
            return Decimal("0")
        def count_tokens(self, **kw):
            return 100
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="strict-effects-", dir=args.output) as tmp:
        db = str(Path(tmp) / "probe.sqlite3")
        provider = Fake()
        rt = Runtime(Graph(clock=FrozenClock("2026-01-01T00:00:00Z"), run_id="article_strict_effects"),
                     llm_provider=provider, persist_to=db, seed=0)
        rt.run_goal("effect-free strict verification probe")
        before = dict(counts)
        require(before == {"tool": 1, "ordinary_behavior": 1, "fake_provider_complete": 2}, "original path did not execute expected mock bodies")
        Runtime.load(db, run_id=rt.run_id, llm_provider=provider, replay_strict=True)
        after = dict(counts)
        require(after == {"tool": 2, "ordinary_behavior": 2, "fake_provider_complete": 2}, "unexpected strict replay effect counts")
        require(len(keys) == 2 and keys[0] != keys[1], "tool context key was not regenerated")
        require(not attempts, "network operation attempted")
        result = {
            "baseline_commit": baseline, "python": platform.python_version(),
            "runtime_files_verified": len(pin["files"]), "runtime_pin_sha256": hashlib.sha256(pin_path.read_bytes()).hexdigest(),
            "before_strict_load": before, "after_strict_load": after,
            "strict_load_returned_without_divergence": True,
            "tool_context_keys_distinct": True,
            "intercepted_network_attempts": len(attempts),
            "network_interception": ["socket.connect", "socket.connect_ex", "socket.bind", "socket.create_connection", "socket.getaddrinfo"],
            "live_provider_calls": "inferred zero: only fixed Fake.complete implementation used; no provider SDK", 
            "external_effect_scope": "mock bodies only mutate in-memory counters; no external tool/network action; transient local SQLite only",
            "limitations": ["One two-turn mock path, not the diligence fixture or arbitrary custom invokers.", "Ordinary behavior reexecution shows direct I/O could repeat, not a performed external I/O test.", "Distinct per-invocation UUID keys cannot by themselves deduplicate repeated external effects.", "Socket interception is not a sandbox and does not intercept every socket API."]}
        (args.output / "strict-effects.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
