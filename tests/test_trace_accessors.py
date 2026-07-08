"""Trace structured accessors: events() and failures() (v1.3).

The trace facade previously exposed only formatted output (lines /
print / export / causal_chain); an external evaluation had to read the
SQLite events table directly to get an event id for fork(). These
tests pin the structured surface.
"""

from activegraph import FrozenClock, Graph, IDGen, Runtime, behavior


def test_trace_events_returns_event_objects_with_ids():
    @behavior(name="maker", on=["goal.created"])
    def maker(event, graph, ctx):
        graph.add_object("task", {"title": "x"})

    g = Graph(ids=IDGen(), clock=FrozenClock())
    rt = Runtime(g)
    rt.run_goal("hi")

    events = rt.trace.events()
    assert events, "a completed run has events"
    assert events == g.events
    # Every event carries the id fork(at_event=...) expects.
    assert all(e.id for e in events)
    # It's a copy: mutating the returned list changes nothing.
    events.clear()
    assert rt.trace.events()


def test_trace_events_ids_are_usable_as_fork_points(tmp_path):
    @behavior(name="maker", on=["goal.created"])
    def maker(event, graph, ctx):
        graph.add_object("task", {"title": "x"})

    g = Graph(ids=IDGen(), clock=FrozenClock())
    rt = Runtime(g)
    rt.run_goal("hi")
    rt.save_state(str(tmp_path / "run.db"))

    fork_point = rt.trace.events()[0].id
    fork = rt.fork(at_event=fork_point)
    assert fork.run_id != rt.run_id
    assert fork.trace.events()[0].id == fork_point


def test_trace_failures_surfaces_traceback():
    @behavior(name="broken", on=["goal.created"])
    def broken(event, graph, ctx):
        raise ValueError("kaboom")

    g = Graph(ids=IDGen(), clock=FrozenClock())
    rt = Runtime(g)
    rt.run_goal("hi")

    failures = rt.trace.failures()
    assert len(failures) == 1
    payload = failures[0].payload
    assert payload["behavior"] == "broken"
    assert payload["exception_type"] == "ValueError"
    assert payload["message"] == "kaboom"
    # The full traceback is recorded (v1.0.3) and now discoverable.
    assert "ValueError: kaboom" in payload["traceback"]
    assert 'raise ValueError("kaboom")' in payload["traceback"]


def test_trace_failures_empty_on_clean_run():
    @behavior(name="fine", on=["goal.created"])
    def fine(event, graph, ctx):
        graph.add_object("task", {"title": "x"})

    g = Graph(ids=IDGen(), clock=FrozenClock())
    rt = Runtime(g)
    rt.run_goal("hi")
    assert rt.trace.failures() == []
