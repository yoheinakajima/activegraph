"""Conformance tests for context-read tracing. CONTRACT v1.10 #1.

The five design constraints, each locked by tests here:

1. **Batched, never per-read** — one ``context.read`` per behavior
   execution, at frame commit, regardless of how many reads the body
   performed.
2. **Deterministic and replay-stable** — first-read order, byte-equal
   logs across repeated identical runs, strict replay clean.
3. **Opt-in, default OFF** — no ``context.read`` ever appears unless
   the runtime was constructed with ``trace_context_reads=True`` (the
   untouched rest of the suite, golden logs included, is the broader
   proof).
4. **Reads only, honestly scoped** — exactly the documented access
   paths are traced (``ctx.view.objects``, ``BehaviorGraph.get_object``
   hits, LLM prompt-view objects); relation/event reads and misses are
   not.
5. **No behavior change** — tracing never alters dispatch, ordering of
   non-lifecycle events, or projected state; failed frames still commit
   their trace.
"""

from __future__ import annotations

import json

from activegraph import Graph, Runtime
from activegraph.behaviors.base import Behavior, LLMBehavior, RelationBehavior
from activegraph.core.clock import FrozenClock
from activegraph.core.ids import IDGen
from activegraph.runtime.context_reads import CONTEXT_READ_ID_CAP

from tests._llm_helpers import ScriptedProvider


def _fresh_graph(run_id: str = "run_CTXREAD") -> Graph:
    return Graph(
        ids=IDGen(), clock=FrozenClock("2026-07-11T00:00:00Z"), run_id=run_id
    )


def _context_reads(graph: Graph) -> list:
    return [e for e in graph.events if e.type == "context.read"]


def _log_lines(graph: Graph) -> list[str]:
    return [json.dumps(e.to_dict(), sort_keys=True) for e in graph.events]


# ---------- constraint 1: batched, never per-read -------------------------


def test_one_event_per_execution_regardless_of_read_count():
    g = _fresh_graph()

    def reader(event, graph, ctx):
        # Many reads through both traced surfaces...
        ctx.view.objects(type="doc")
        ctx.view.objects()  # second call, overlapping results
        for o in ctx.view.objects(type="doc"):
            graph.get_object(o.id)  # point re-reads of the same ids
        graph.add_object("summary", {"ok": True})

    rt = Runtime(
        g,
        behaviors=[Behavior(name="reader", fn=reader, on=["goal.created"])],
        trace_context_reads=True,
    )
    g.add_object("doc", {"n": 1})
    g.add_object("doc", {"n": 2})
    rt.run_goal("go")

    reads = _context_reads(g)
    # ...but exactly ONE context.read for the single execution.
    assert len(reads) == 1
    payload = reads[0].payload
    assert payload["behavior"] == "reader"
    assert payload["object_ids"] == ["doc#1", "doc#2"]
    assert payload["count"] == 2
    assert "truncated" not in payload


def test_one_event_per_execution_two_behaviors_two_events():
    g = _fresh_graph()

    def r1(event, graph, ctx):
        ctx.view.objects(type="doc")

    def r2(event, graph, ctx):
        ctx.view.objects(type="doc")

    rt = Runtime(
        g,
        behaviors=[
            Behavior(name="r1", fn=r1, on=["goal.created"]),
            Behavior(name="r2", fn=r2, on=["goal.created"]),
        ],
        trace_context_reads=True,
    )
    g.add_object("doc", {"n": 1})
    rt.run_goal("go")

    reads = _context_reads(g)
    assert [r.payload["behavior"] for r in reads] == ["r1", "r2"]


def test_emitted_at_frame_commit_right_after_terminal_lifecycle_event():
    g = _fresh_graph()

    def reader(event, graph, ctx):
        ctx.view.objects(type="doc")

    rt = Runtime(
        g,
        behaviors=[Behavior(name="reader", fn=reader, on=["goal.created"])],
        trace_context_reads=True,
    )
    g.add_object("doc", {"n": 1})
    rt.run_goal("go")

    types = [e.type for e in g.events]
    i = types.index("context.read")
    assert types[i - 1] == "behavior.completed"
    # The frame reference points at this execution's started event, and
    # causality points at the triggering event.
    read = g.events[i]
    started = next(e for e in g.events if e.type == "behavior.started")
    assert read.payload["execution_event_id"] == started.id
    assert read.caused_by == started.payload["event_id"]


def test_truncation_cap_bounds_ids_but_count_stays_exact():
    g = _fresh_graph()
    n = CONTEXT_READ_ID_CAP + 50

    def reader(event, graph, ctx):
        ctx.view.objects(type="doc")

    rt = Runtime(
        g,
        behaviors=[Behavior(name="reader", fn=reader, on=["goal.created"])],
        trace_context_reads=True,
    )
    for i in range(n):
        g.add_object("doc", {"n": i})
    rt.run_goal("go")

    (read,) = _context_reads(g)
    assert len(read.payload["object_ids"]) == CONTEXT_READ_ID_CAP
    assert read.payload["count"] == n
    assert read.payload["truncated"] is True
    # The kept prefix is the FIRST reads, in order.
    assert read.payload["object_ids"][0] == "doc#1"
    assert read.payload["object_ids"][-1] == f"doc#{CONTEXT_READ_ID_CAP}"


# ---------- constraint 2: deterministic and replay-stable -----------------


def _deterministic_scenario() -> Graph:
    g = _fresh_graph()

    def reader(event, graph, ctx):
        # Interleave both traced surfaces; order below is the contract.
        docs = ctx.view.objects(type="doc")
        graph.get_object(docs[1].id)  # re-read: dedup keeps first position
        graph.add_object("summary", {"n": len(docs)})

    def pointer(event, graph, ctx):
        graph.get_object("doc#2")
        graph.get_object("doc#1")

    rt = Runtime(
        g,
        behaviors=[
            Behavior(name="reader", fn=reader, on=["goal.created"]),
            Behavior(name="pointer", fn=pointer, on=["goal.created"]),
        ],
        trace_context_reads=True,
    )
    g.add_object("doc", {"n": 1})
    g.add_object("doc", {"n": 2})
    rt.run_goal("go")
    return g


def test_repeated_identical_runs_produce_byte_identical_logs():
    logs = [_log_lines(_deterministic_scenario()) for _ in range(3)]
    assert logs[0] == logs[1] == logs[2]
    # And the trace itself is the first-read order, deduplicated.
    reads = _context_reads(_deterministic_scenario())
    assert reads[0].payload["object_ids"] == ["doc#1", "doc#2"]
    assert reads[1].payload["object_ids"] == ["doc#2", "doc#1"]


def test_strict_replay_of_a_traced_run_is_clean(tmp_path):
    db = str(tmp_path / "traced.db")

    def reader(event, graph, ctx):
        docs = ctx.view.objects(type="doc")
        graph.add_object("summary", {"n": len(docs)})

    behaviors = [Behavior(name="reader", fn=reader, on=["goal.created"])]
    g = _fresh_graph()
    rt = Runtime(g, behaviors=behaviors, persist_to=db, trace_context_reads=True)
    g.add_object("doc", {"n": 1})
    rt.run_goal("go")
    rt.save_state()
    assert _context_reads(g)  # the recorded log really has trace markers

    # Strict replay verifies the derivation stream; context.read is
    # per-execution bookkeeping and must not diverge it — with the flag
    # on or off on the loading runtime.
    for flag in (True, False):
        loaded = Runtime.load(
            db,
            behaviors=behaviors,
            replay_strict=True,
            trace_context_reads=flag,
        )
        assert [e.type for e in loaded.graph.events] == [
            e.type for e in g.events
        ]


def test_fork_inherits_tracing_posture(tmp_path):
    db = str(tmp_path / "fork.db")

    def reader(event, graph, ctx):
        ctx.view.objects(type="doc")

    g = _fresh_graph()
    rt = Runtime(
        g,
        behaviors=[Behavior(name="reader", fn=reader, on=["goal.created"])],
        persist_to=db,
        trace_context_reads=True,
    )
    g.add_object("doc", {"n": 1})
    rt.run_goal("go")
    fork = rt.fork(g.events[0].id)
    assert fork.trace_context_reads is True


# ---------- constraint 3: opt-in, default OFF -----------------------------


def test_default_off_emits_no_context_read_events():
    g = _fresh_graph()

    def reader(event, graph, ctx):
        ctx.view.objects(type="doc")
        graph.get_object("doc#1")

    rt = Runtime(
        g, behaviors=[Behavior(name="reader", fn=reader, on=["goal.created"])]
    )
    g.add_object("doc", {"n": 1})
    rt.run_goal("go")
    assert _context_reads(g) == []


# ---------- constraint 4: reads only, honestly scoped ---------------------


def test_untraced_paths_stay_out_of_the_read_set():
    g = _fresh_graph()

    def reader(event, graph, ctx):
        ctx.view.relations()            # relation read — untraced
        ctx.view.events()               # event read — untraced
        graph.get_relation("rel_1")     # relation point read — untraced
        graph.get_object("missing#99")  # miss — read nothing
        graph.get_object("doc#2")       # the only traced read

    rt = Runtime(
        g,
        behaviors=[Behavior(name="reader", fn=reader, on=["goal.created"])],
        trace_context_reads=True,
    )
    a = g.add_object("doc", {"n": 1})
    b = g.add_object("doc", {"n": 2})
    g.add_relation(a.id, b.id, "links")
    rt.run_goal("go")

    (read,) = _context_reads(g)
    assert read.payload["object_ids"] == ["doc#2"]
    assert read.payload["count"] == 1


def test_read_free_frame_emits_no_trace_even_when_enabled():
    g = _fresh_graph()

    def writer(event, graph, ctx):
        graph.add_object("summary", {"ok": True})  # writes are not reads

    rt = Runtime(
        g,
        behaviors=[Behavior(name="writer", fn=writer, on=["goal.created"])],
        trace_context_reads=True,
    )
    rt.run_goal("go")
    assert _context_reads(g) == []


def test_llm_behavior_traces_prompt_view_objects():
    g = _fresh_graph()
    provider = ScriptedProvider(respond_fn=lambda messages, schema: "fine")

    def handler(event, graph, ctx, out):
        # The handler reads nothing itself — the prompt already did.
        pass

    b = LLMBehavior(
        name="summarizer",
        fn=lambda e, g_, c: None,
        handler=handler,
        on=["goal.created"],
        description="summarize",
        model="scripted-model",
    )
    rt = Runtime(g, behaviors=[b], llm_provider=provider, trace_context_reads=True)
    g.add_object("doc", {"n": 1})
    g.add_object("doc", {"n": 2})
    rt.run_goal("go")

    (read,) = _context_reads(g)
    # Every object serialized into the prompt's graph-context block is a
    # read — the model consumed them at decision time.
    assert read.payload["object_ids"] == ["doc#1", "doc#2"]
    started = next(e for e in g.events if e.type == "behavior.started")
    assert read.payload["execution_event_id"] == started.id


def test_relation_behavior_executions_are_traced():
    g = _fresh_graph()

    def rel_reader(relation, event, graph, ctx):
        graph.get_object(relation.source)

    rb = RelationBehavior(
        name="rel_reader",
        fn=rel_reader,
        relation_type="links",
        on=["relation.created"],
    )
    rt = Runtime(g, behaviors=[rb], trace_context_reads=True)
    a = g.add_object("doc", {"n": 1})
    b = g.add_object("doc", {"n": 2})
    g.add_relation(a.id, b.id, "links")
    rt.run_until_idle()

    (read,) = _context_reads(g)
    assert read.payload["object_ids"] == [a.id]
    started = next(
        e for e in g.events if e.type == "relation_behavior.started"
    )
    assert read.payload["execution_event_id"] == started.id


# ---------- constraint 5: no behavior change ------------------------------


def test_failed_frame_still_commits_its_read_trace():
    g = _fresh_graph()

    def doomed(event, graph, ctx):
        ctx.view.objects(type="doc")
        raise RuntimeError("boom after reading")

    rt = Runtime(
        g,
        behaviors=[Behavior(name="doomed", fn=doomed, on=["goal.created"])],
        trace_context_reads=True,
    )
    g.add_object("doc", {"n": 1})
    rt.run_goal("go")

    types = [e.type for e in g.events]
    i = types.index("context.read")
    assert types[i - 1] == "behavior.failed"
    (read,) = _context_reads(g)
    assert read.payload["object_ids"] == ["doc#1"]


def test_tracing_never_alters_dispatch_or_projected_state():
    def scenario(trace: bool) -> Graph:
        g = _fresh_graph()

        def reader(event, graph, ctx):
            docs = ctx.view.objects(type="doc")
            graph.add_object("summary", {"n": len(docs)})

        def chained(event, graph, ctx):
            # Fires on the summary reader created — proves derived
            # dispatch is identical under tracing.
            graph.patch_object(event.payload["object"]["id"], {"seen": True})

        rt = Runtime(
            g,
            behaviors=[
                Behavior(name="reader", fn=reader, on=["goal.created"]),
                Behavior(
                    name="chained",
                    fn=chained,
                    on=["object.created"],
                    where={"object.type": "summary"},
                ),
            ],
            trace_context_reads=trace,
        )
        g.add_object("doc", {"n": 1})
        rt.run_goal("go")
        return g

    on, off = scenario(True), scenario(False)
    lifecycle = ("behavior.", "relation_behavior.", "runtime.", "context.read")

    def stream(graph: Graph) -> list[str]:
        return [
            e.type
            for e in graph.events
            if not e.type.startswith(lifecycle[:3]) and e.type != "context.read"
        ]

    assert stream(on) == stream(off)
    # Projected state is identical: same objects, data, and versions.
    def state(graph: Graph) -> list[tuple]:
        return [
            (o.id, o.type, json.dumps(o.data, sort_keys=True), o.version)
            for o in graph.all_objects()
        ]

    assert state(on) == state(off)


def test_context_read_is_sink_visible_like_any_other_event():
    from activegraph.sinks.testing import RecordingSink

    g = _fresh_graph()
    sink = RecordingSink()

    def reader(event, graph, ctx):
        ctx.view.objects(type="doc")

    rt = Runtime(
        g,
        behaviors=[Behavior(name="reader", fn=reader, on=["goal.created"])],
        sinks=[sink],
        trace_context_reads=True,
    )
    g.add_object("doc", {"n": 1})
    rt.run_goal("go")
    assert sink.wait_for(len(g.events), timeout=5.0)
    delivered = [e.type for e in sink.events]
    assert delivered.count("context.read") == 1
    # The sink saw the marker at the same position as the log: right
    # after the frame's terminal lifecycle event.
    i = delivered.index("context.read")
    assert delivered[i - 1] == "behavior.completed"
    g.close_sinks()


def test_context_read_never_schedules_behaviors():
    g = _fresh_graph()
    fired: list[str] = []

    def reader(event, graph, ctx):
        ctx.view.objects(type="doc")

    def spy(event, graph, ctx):
        fired.append(event.type)  # must never fire

    rt = Runtime(
        g,
        behaviors=[
            Behavior(name="reader", fn=reader, on=["goal.created"]),
            Behavior(name="spy", fn=spy, on=["context.read"]),
        ],
        trace_context_reads=True,
    )
    g.add_object("doc", {"n": 1})
    rt.run_goal("go")
    assert _context_reads(g)  # the marker was emitted...
    assert fired == []  # ...but scheduled nothing (runtime bookkeeping)
