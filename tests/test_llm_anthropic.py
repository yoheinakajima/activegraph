"""AnthropicProvider unit tests with a mocked SDK client.

CONTRACT v0.6 #4 / #11: exception mapping (network vs rate-limit),
no-API-key handling, structured-output extraction, pricing lookup
by family prefix.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from activegraph.llm import (
    AnthropicProvider,
    LLMBehaviorError,
    LLMMessage,
    LLMResponse,
)


class _Out(BaseModel):
    n: int


def _client_returning(text: str, *, in_tok: int = 10, out_tok: int = 5):
    raw = SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
        model="claude-sonnet-4-5",
        stop_reason="end_turn",
    )
    client = MagicMock()
    client.messages.create.return_value = raw
    client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=in_tok)
    return client


def test_complete_parses_structured_output():
    client = _client_returning('{"n": 42}')
    p = AnthropicProvider(client=client)
    r = p.complete(
        system="sys",
        messages=[LLMMessage(role="user", content="u")],
        model="claude-sonnet-4-5",
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
    assert r.finish_reason == "end_turn"
    assert r.seed is None  # Anthropic has no seed parameter


def test_complete_extracts_json_from_fenced_block():
    client = _client_returning('Here is the answer:\n```json\n{"n": 9}\n```\nDone.')
    p = AnthropicProvider(client=client)
    r = p.complete(
        system="",
        messages=[LLMMessage(role="user", content="u")],
        model="claude-sonnet-4-5",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Out,
        timeout_seconds=30,
    )
    assert r.parsed.n == 9


def test_complete_raises_parse_error_when_no_json():
    client = _client_returning("just prose, no json at all")
    p = AnthropicProvider(client=client)
    with pytest.raises(LLMBehaviorError) as exc:
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="claude-sonnet-4-5",
            max_tokens=64,
            temperature=0.0,
            top_p=1.0,
            output_schema=_Out,
            timeout_seconds=30,
        )
    assert exc.value.reason == "llm.parse_error"


def test_complete_raises_schema_violation_when_json_valid_but_wrong_shape():
    client = _client_returning('{"oops": 1}')
    p = AnthropicProvider(client=client)
    with pytest.raises(LLMBehaviorError) as exc:
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="claude-sonnet-4-5",
            max_tokens=64,
            temperature=0.0,
            top_p=1.0,
            output_schema=_Out,
            timeout_seconds=30,
        )
    assert exc.value.reason == "llm.schema_violation"


def test_complete_maps_network_exception():
    client = MagicMock()
    client.messages.create.side_effect = TimeoutError("connect timeout")
    p = AnthropicProvider(client=client)
    with pytest.raises(LLMBehaviorError) as exc:
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="claude-sonnet-4-5",
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
    client.messages.create.side_effect = RateLimitError("429 too many")
    p = AnthropicProvider(client=client)
    with pytest.raises(LLMBehaviorError) as exc:
        p.complete(
            system="",
            messages=[LLMMessage(role="user", content="u")],
            model="claude-sonnet-4-5",
            max_tokens=64,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=30,
        )
    assert exc.value.reason == "llm.rate_limited"


def test_estimate_cost_uses_family_prefix():
    p = AnthropicProvider(client=MagicMock())
    sonnet = p.estimate_cost(
        input_tokens=1_000_000, output_tokens=0, model="claude-sonnet-4-6"
    )
    haiku = p.estimate_cost(
        input_tokens=1_000_000, output_tokens=0, model="claude-haiku-4-5-20251001"
    )
    opus = p.estimate_cost(
        input_tokens=1_000_000, output_tokens=0, model="claude-opus-4-7"
    )
    assert sonnet == Decimal("3")
    assert haiku == Decimal("1")
    assert opus == Decimal("15")


def test_count_tokens_delegates_to_sdk():
    client = MagicMock()
    client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=123)
    p = AnthropicProvider(client=client)
    n = p.count_tokens(
        system="sys",
        messages=[LLMMessage(role="user", content="u")],
        model="claude-sonnet-4-5",
    )
    assert n == 123


def test_missing_api_key_raises_when_constructing_real_client(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=lambda: object()),
    )
    p = AnthropicProvider()  # no client override
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        p.count_tokens(system="", messages=[], model="claude-sonnet-4-5")
