"""runtime.disable_pack: deregistration, not unload (CONTRACT v1.4 #3).

Behaviors stop firing immediately, tools stop resolving, typed
validation reverts to untyped, state stays, memory is not reclaimed
(restart to evict), re-enable = load_pack, idempotent second disable.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from activegraph import Graph, Runtime, clear_registry
from activegraph import ToolNotFoundError
from activegraph.packs import (
    ObjectType,
    Pack,
    PackNotFoundError,
    behavior as pack_behavior,
)


class _NoteSchema(BaseModel):
    text: str


def _candidate_pack(hits: list[str]) -> Pack:
    @pack_behavior(name="echo", on=["goal.created"])
    def echo(event, graph, ctx):
        hits.append(event.payload.get("goal", ""))
        graph.add_object("echo_note", {"text": "hi"})

    from activegraph.packs import tool as pack_tool

    @pack_tool(name="shout")
    def shout(args, ctx):
        return {"ok": True}

    return Pack(
        name="candidate",
        version="1.0",
        object_types=(ObjectType(name="typed_note", schema=_NoteSchema),),
        behaviors=(echo,),
        tools=(shout,),
    )


def test_disable_stops_behaviors_and_tools_immediately():
    clear_registry()
    hits: list[str] = []
    rt = Runtime(Graph())
    rt.load_pack(_candidate_pack(hits))

    rt.run_goal("first")
    assert hits == ["first"]
    assert rt.get_tool("candidate.shout").name == "candidate.shout"

    assert rt.disable_pack("candidate") is True
    rt.run_goal("second")
    assert hits == ["first"]  # behavior no longer fires
    with pytest.raises(ToolNotFoundError):
        rt.get_tool("candidate.shout")
    with pytest.raises(ToolNotFoundError):
        rt.get_tool("shout")
    assert rt.loaded_packs() == []


def test_disable_leaves_state_and_reverts_typing():
    clear_registry()
    hits: list[str] = []
    rt = Runtime(Graph())
    rt.load_pack(_candidate_pack(hits))
    rt.run_goal("go")
    created = next(
        o for o in rt.graph.all_objects() if o.type == "echo_note"
    )

    # Typed while loaded: schema enforced.
    from activegraph.packs import PackSchemaViolation

    with pytest.raises(PackSchemaViolation):
        rt.graph.add_object("typed_note", {"wrong_field": 1})

    rt.disable_pack("candidate")
    # Pack-created state is untouched...
    assert rt.graph.get_object(created.id) is not None
    # ...and the type reverts to v0.9 untyped semantics.
    obj = rt.graph.add_object("typed_note", {"wrong_field": 1})
    assert rt.graph.get_object(obj.id) is not None


def test_disable_emits_event_and_is_idempotent():
    clear_registry()
    rt = Runtime(Graph())
    rt.load_pack(_candidate_pack([]))

    assert rt.disable_pack("candidate") is True
    ev = next(e for e in rt.graph.events if e.type == "pack.disabled")
    assert ev.payload["name"] == "candidate"
    assert ev.payload["behaviors"] == ["candidate.echo"]
    assert ev.payload["tools"] == ["candidate.shout"]
    assert ev.payload["object_types"] == ["typed_note"]

    # Idempotent second call: False, no second event.
    assert rt.disable_pack("candidate") is False
    assert (
        len([e for e in rt.graph.events if e.type == "pack.disabled"]) == 1
    )


def test_disable_unknown_pack_is_loud():
    clear_registry()
    rt = Runtime(Graph())
    with pytest.raises(PackNotFoundError):
        rt.disable_pack("never_loaded")


def test_reload_reenables():
    clear_registry()
    hits: list[str] = []
    pack = _candidate_pack(hits)
    rt = Runtime(Graph())
    rt.load_pack(pack)
    rt.disable_pack("candidate")

    assert rt.load_pack(pack) is True  # fresh load, not idempotent skip
    rt.run_goal("after re-enable")
    assert hits == ["after re-enable"]
    assert rt.get_tool("candidate.shout").name == "candidate.shout"
    # And it can be disabled again.
    assert rt.disable_pack("candidate") is True


def test_disable_resolves_short_name_ambiguity():
    # Two packs export a behavior with the same short name; disabling
    # one must RESOLVE the ambiguity, not leave a stale sentinel.
    clear_registry()

    def mk(pack_name):
        @pack_behavior(name="worker", on=["goal.created"])
        def worker(event, graph, ctx):
            pass

        return Pack(name=pack_name, version="1.0", behaviors=(worker,))

    rt = Runtime(Graph())
    rt.load_pack(mk("alpha"))
    rt.load_pack(mk("beta"))
    from activegraph.runtime.registration_errors import AmbiguousBehaviorError

    with pytest.raises(AmbiguousBehaviorError):
        rt.get_behavior("worker")

    rt.disable_pack("alpha")
    assert rt.get_behavior("worker").name == "beta.worker"
