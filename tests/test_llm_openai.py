"""OpenAIProvider unit tests with a mocked SDK client.

CONTRACT v1.0.1 #5 / CONTRACT v0.6 #11: exception mapping (network vs
rate-limit), no-API-key handling, structured-output extraction
through the shared `parse_structured_response` path, family-prefix
pricing lookup, tool-use parity with the runtime loop, count_tokens fallback.

Mirrors `tests/test_llm_anthropic.py`. Same fake-client shape; the
only intentional divergences are the OpenAI response structure
(`choices[0].message.content`, `usage.prompt_tokens`) and the tool-
call wire shape.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from activegraph import (
    Graph,
    Runtime,
    clear_registry,
    clear_tool_registry,
    behavior,
    llm_behavior,
    tool,
)
from activegraph.llm import (
    LLMBehaviorError,
    LLMMessage,
    OpenAIProvider,
    ToolCall,
)


class _Out(BaseModel):
    n: int


class _ToolIn(BaseModel):
    q: str


class _ToolOut(BaseModel):
    answer: str


def _raw_response(
    text: str | None,
    *,
    in_tok: int = 10,
    out_tok: int = 5,
    tool_calls=None,
    finish_reason: str = "stop",
):
    raw = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(prompt_tokens=in_tok, completion_tokens=out_tok),
        model="gpt-4o-mini",
    )
    return raw


def _client_returning(text: str, *, in_tok: int = 10, out_tok: int = 5):
    client = MagicMock()
    client.chat.completions.create.return_value = _raw_response(
        text, in_tok=in_tok, out_tok=out_tok
    )
    return client


def test_complete_parses_structured_output():
    client = _client_returning('{"n": 42}')
    p = OpenAIProvider(client=client)
    r = p.complete(
        system="sys",
        messages=[LLMMessage(role="user", content="u")],
        model="gpt-4o-mini",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Out,
        timeout_seconds=30,
    )
    assert isinstance(r.parsed, _Out)
    assert r.parsed.n == 42
    assert r.input_tokens == 10
    assert r.output_tokens == 5
    assert r.finish_reason == "stop"
    assert r.seed is None


def test_complete_extracts_json_from_fenced_block():
    client = _client_returning('Here:\n```json\n{"n": 9}\n```\nDone.')
    p = OpenAIProvider(client=client)
    r = p.complete(
        system="",
        messages=[LLMMessage(role="user", content="u")],
        model="gpt-4o-mini",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Out,
        timeout_seconds=30,
    )
    assert r.parsed.n == 9


def test_complete_raises_parse_error_when_no_json():
    client = _client_returning("just prose, no json at all")
    p = OpenAIProvider(client=client)
    with pytest.raises(LLMBehaviorError) as exc:
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="gpt-4o-mini",
            max_tokens=64,
            temperature=0.0,
            top_p=1.0,
            output_schema=_Out,
            timeout_seconds=30,
        )
    assert exc.value.reason == "llm.parse_error"


def test_complete_raises_schema_violation_when_wrong_shape():
    client = _client_returning('{"oops": 1}')
    p = OpenAIProvider(client=client)
    with pytest.raises(LLMBehaviorError) as exc:
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="gpt-4o-mini",
            max_tokens=64,
            temperature=0.0,
            top_p=1.0,
            output_schema=_Out,
            timeout_seconds=30,
        )
    assert exc.value.reason == "llm.schema_violation"


def test_complete_maps_network_exception():
    client = MagicMock()
    client.chat.completions.create.side_effect = TimeoutError("connect timeout")
    p = OpenAIProvider(client=client)
    with pytest.raises(LLMBehaviorError) as exc:
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="gpt-4o-mini",
            max_tokens=64,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=30,
        )
    assert exc.value.reason == "llm.network_error"


def test_complete_maps_rate_limit_exception():
    class RateLimitError(Exception):
        pass

    client = MagicMock()
    client.chat.completions.create.side_effect = RateLimitError("429 too many")
    p = OpenAIProvider(client=client)
    with pytest.raises(LLMBehaviorError) as exc:
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="gpt-4o-mini",
            max_tokens=64,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=30,
        )
    assert exc.value.reason == "llm.rate_limited"


def test_complete_maps_auth_failure_to_network_error():
    # CONTRACT v1.0.1 #5: closed reason taxonomy; auth failures land
    # in llm.network_error with the message preserved verbatim.
    class AuthenticationError(Exception):
        pass

    client = MagicMock()
    client.chat.completions.create.side_effect = AuthenticationError(
        "Invalid API key provided"
    )
    p = OpenAIProvider(client=client)
    with pytest.raises(LLMBehaviorError) as exc:
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="gpt-4o-mini",
            max_tokens=64,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=30,
        )
    assert exc.value.reason == "llm.network_error"
    assert "Invalid API key" in str(exc.value)


def test_complete_translates_framework_tools_and_extracts_openai_tool_calls():
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="lookup", arguments='{"q": "northwind"}'),
    )
    client = MagicMock()
    client.chat.completions.create.return_value = _raw_response(
        None,
        tool_calls=[tool_call],
        finish_reason="tool_calls",
    )
    p = OpenAIProvider(client=client)
    r = p.complete(
        system="",
        messages=[LLMMessage(role="user", content="u")],
        model="gpt-4o-mini",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Out,
        timeout_seconds=30,
        tools=[
            {
                "name": "lookup",
                "description": "Find a thing",
                "input_schema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            }
        ],
    )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Find a thing",
                "parameters": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            },
        }
    ]
    assert r.parsed is None
    assert r.finish_reason == "tool_calls"
    assert r.tool_calls == [
        ToolCall(id="call_1", name="lookup", args={"q": "northwind"})
    ]


def test_complete_accepts_openai_style_tool_definition():
    client = _client_returning('{"n": 1}')
    p = OpenAIProvider(client=client)
    p.complete(
        system="",
        messages=[LLMMessage(role="user", content="u")],
        model="gpt-4o-mini",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Out,
        timeout_seconds=30,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Find a thing",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    assert client.chat.completions.create.call_args.kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Find a thing",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_messages_echo_assistant_tool_calls_in_openai_shape():
    client = _client_returning('{"n": 1}')
    p = OpenAIProvider(client=client)
    p.complete(
        system="",
        messages=[
            LLMMessage(role="user", content="u"),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=(
                    ToolCall(id="call_1", name="lookup", args={"q": "x"}),
                ),
            ),
            LLMMessage(role="tool", content='{"answer": "ok"}', tool_use_id="call_1"),
        ],
        model="gpt-4o-mini",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Out,
        timeout_seconds=30,
    )
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "lookup",
                    "arguments": '{"q": "x"}',
                },
            }
        ],
    }
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"answer": "ok"}',
    }


def test_runtime_tool_loop_with_openai_provider():
    clear_registry()
    clear_tool_registry()

    @tool(
        name="lookup",
        description="Find a thing",
        input_schema=_ToolIn,
        output_schema=_ToolOut,
        deterministic=True,
    )
    def lookup(args, ctx):
        return _ToolOut(answer=f"answer:{args.q}")

    @behavior(name="seed_openai_tool_test", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("doc", {"title": "Northwind"})

    received = []

    @llm_behavior(
        name="openai_tool_user",
        on=["object.created"],
        where={"object.type": "doc"},
        output_schema=_Out,
        tools=[lookup],
    )
    def openai_tool_user(event, graph, ctx, out):
        received.append(out)

    first = _raw_response(
        None,
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                function=SimpleNamespace(name="lookup", arguments='{"q": "northwind"}'),
            )
        ],
        finish_reason="tool_calls",
    )
    second = _raw_response('{"n": 42}', finish_reason="stop")
    client = MagicMock()
    client.chat.completions.create.side_effect = [first, second]

    g = Graph()
    Runtime(g, llm_provider=OpenAIProvider(client=client)).run_goal("g")

    assert len(received) == 1
    assert received[0].n == 42
    assert client.chat.completions.create.call_count == 2
    first_kwargs = client.chat.completions.create.call_args_list[0].kwargs
    assert first_kwargs["tools"][0]["type"] == "function"
    assert first_kwargs["tools"][0]["function"]["name"] == "lookup"

    second_messages = client.chat.completions.create.call_args_list[1].kwargs["messages"]
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in second_messages)
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "call_1" for m in second_messages)
    assert any(e.type == "tool.requested" for e in g.events)
    assert any(e.type == "tool.responded" for e in g.events)


def test_estimate_cost_uses_family_prefix():
    p = OpenAIProvider(client=MagicMock())
    mini = p.estimate_cost(
        input_tokens=1_000_000, output_tokens=0, model="gpt-4o-mini-2024-07-18"
    )
    base = p.estimate_cost(
        input_tokens=1_000_000, output_tokens=0, model="gpt-4o-2024-11-20"
    )
    turbo = p.estimate_cost(
        input_tokens=1_000_000, output_tokens=0, model="gpt-4-turbo-preview"
    )
    assert mini == Decimal("0.15")
    assert base == Decimal("2.5")
    assert turbo == Decimal("10")


def test_count_tokens_heuristic_fallback_when_tiktoken_missing(monkeypatch):
    # Force the tiktoken import to fail and verify the char/4 heuristic.
    import builtins

    real_import = builtins.__import__

    def fail_tiktoken(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("tiktoken not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_tiktoken)
    p = OpenAIProvider(client=MagicMock())
    n = p.count_tokens(
        system="0123",  # 4 chars
        messages=[LLMMessage(role="user", content="01234567")],  # 8 chars
        model="gpt-4o-mini",
    )
    # (4 + 8) // 4 = 3
    assert n == 3


def test_missing_api_key_raises_when_constructing_real_client(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda: object()),
    )
    p = OpenAIProvider()  # no client override
    # The error message must name OPENAI_API_KEY so the operator
    # knows which env var to set.
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="gpt-4o-mini",
            max_tokens=64,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=30,
        )
