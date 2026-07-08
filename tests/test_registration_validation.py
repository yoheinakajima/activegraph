"""Registration-time handler-signature validation (v1.3).

A wrong-arity handler used to register fine and fail at first
invocation with a TypeError swallowed into a `behavior.failed` event.
Decoration now validates the positional calling convention, mirroring
the CONTRACT v1.0.3 #2 precedent (`output_schema=` strict validation
at the @llm_behavior line).

Covers the global decorators and the pack-scoped variants, plus the
permissive escape hatches: `*args`, extras with defaults, and the
pack settings-injection pattern (annotated extras).
"""

import pytest
from pydantic import BaseModel

from activegraph import behavior, llm_behavior, relation_behavior, tool
from activegraph import packs as pack_api


class _Settings(BaseModel):
    threshold: float = 0.5


# ---------------------------------------------------------------- @tool


def test_tool_accepts_two_positional_params():
    @tool(name="ok")
    def ok(args, ctx):
        return {}

    assert ok.name == "ok"


def test_tool_rejects_one_positional_param():
    with pytest.raises(TypeError, match=r"@tool.*must accept 2 positional"):

        @tool(name="bad")
        def bad(ctx):
            return {}


def test_tool_rejects_extra_required_param():
    with pytest.raises(TypeError, match=r"beyond the \(args, ctx\) contract"):

        @tool(name="bad")
        def bad(args, ctx, extra):
            return {}


def test_tool_allows_extra_param_with_default():
    @tool(name="ok")
    def ok(args, ctx, extra=None):
        return {}

    assert ok.name == "ok"


def test_tool_annotation_alone_does_not_excuse_extras():
    # Tools have no settings injection: annotated-but-required extras
    # still fail at call time, so they fail at decoration time too.
    with pytest.raises(TypeError, match=r"beyond the \(args, ctx\) contract"):

        @tool(name="bad")
        def bad(args, ctx, *, settings: _Settings):
            return {}


def test_tool_allows_var_positional():
    @tool(name="ok")
    def ok(*call_args):
        return {}

    assert ok.name == "ok"


# ------------------------------------------------------------ @behavior


def test_behavior_accepts_three_positional_params():
    @behavior(name="ok", on=["goal.created"])
    def ok(event, graph, ctx):
        pass

    assert ok.name == "ok"


def test_behavior_rejects_two_positional_params():
    with pytest.raises(TypeError, match=r"@behavior.*must accept 3 positional"):

        @behavior(name="bad", on=["goal.created"])
        def bad(event, ctx):
            pass


def test_behavior_allows_annotated_keyword_only_extra():
    # The pack settings-injection pattern: keyword-only, annotated,
    # no default. The loader injects it; decoration must not reject it.
    @behavior(name="ok", on=["goal.created"])
    def ok(event, graph, ctx, *, settings: _Settings):
        pass

    assert ok.name == "ok"


def test_behavior_rejects_bare_keyword_only_extra():
    with pytest.raises(TypeError, match=r"beyond the .* contract"):

        @behavior(name="bad", on=["goal.created"])
        def bad(event, graph, ctx, *, mystery):
            pass


def test_behavior_allows_var_keyword():
    @behavior(name="ok", on=["goal.created"])
    def ok(event, graph, ctx, **extras):
        pass

    assert ok.name == "ok"


# --------------------------------------------------- @relation_behavior


def test_relation_behavior_requires_four_positional_params():
    with pytest.raises(
        TypeError, match=r"@relation_behavior.*must accept 4 positional"
    ):

        @relation_behavior("blocks", on=["object.created"])
        def bad(event, graph, ctx):
            pass


def test_relation_behavior_accepts_four_positional_params():
    @relation_behavior("blocks", on=["object.created"], name="ok")
    def ok(relation, event, graph, ctx):
        pass

    assert ok.name == "ok"


# -------------------------------------------------------- @llm_behavior


def test_llm_behavior_requires_four_positional_params():
    with pytest.raises(
        TypeError, match=r"@llm_behavior.*must accept 4 positional"
    ):

        @llm_behavior(name="bad", on=["goal.created"], model="claude-sonnet-4-5")
        def bad(event, graph, ctx):
            pass


def test_llm_behavior_accepts_four_positional_params():
    @llm_behavior(name="ok", on=["goal.created"], model="claude-sonnet-4-5")
    def ok(event, graph, ctx, out):
        pass

    assert ok.name == "ok"


# ----------------------------------------------- pack-scoped decorators


def test_pack_behavior_validates_and_allows_settings_injection():
    @pack_api.behavior(name="ok", on=["goal.created"])
    def ok(event, graph, ctx, *, settings: _Settings):
        pass

    assert ok.name == "ok"

    with pytest.raises(TypeError, match=r"@behavior.*must accept 3 positional"):

        @pack_api.behavior(name="bad", on=["goal.created"])
        def bad(ctx):
            pass


def test_pack_llm_behavior_validates():
    with pytest.raises(
        TypeError, match=r"@llm_behavior.*must accept 4 positional"
    ):

        @pack_api.llm_behavior(name="bad", on=["goal.created"])
        def bad(event, graph, ctx):
            pass


def test_pack_relation_behavior_validates():
    with pytest.raises(
        TypeError, match=r"@relation_behavior.*must accept 4 positional"
    ):

        @pack_api.relation_behavior("blocks", on=["object.created"])
        def bad(event, graph, ctx):
            pass


def test_pack_tool_validates():
    with pytest.raises(TypeError, match=r"@tool.*must accept 2 positional"):

        @pack_api.tool(name="bad")
        def bad(ctx):
            return {}


# ------------------------------------------------------ escape hatches


def test_uninspectable_callable_passes_through():
    from activegraph._signature import validate_handler_signature

    # Builtins often have no inspectable signature; validation skips
    # rather than guessing.
    validate_handler_signature(
        print,
        expected_params=("event", "graph", "ctx"),
        decorator="@behavior",
        allow_annotated_extras=True,
    )


def test_non_callable_is_rejected():
    from activegraph._signature import validate_handler_signature

    with pytest.raises(TypeError, match="must decorate a callable"):
        validate_handler_signature(
            42,
            expected_params=("event", "graph", "ctx"),
            decorator="@behavior",
            allow_annotated_extras=True,
        )
