"""Native structured-output mode. CONTRACT v1.3 #1.

What's covered, mirroring the amendment's numbered locks:

- #1/#2: mode resolution is opt-in (Runtime flag), capability-gated,
  and getattr-guarded — providers without the method resolve to
  prompt mode.
- #3: wire shapes — Anthropic ``output_config`` and OpenAI
  ``response_format`` are sent in native mode and absent in prompt
  mode; prompt-mode calls are byte-identical to pre-v1.3.
- #4: the resolved mode rides every ``llm.requested`` payload;
  fallback is silent (no new reason codes, no failure events).
- #6: the native system prompt drops the schema block and example
  instance for one stable sentence.
- #7: the mode contributes to prompt hashes and fixture payloads only
  when native (pre-v1.3 hashes byte-identical); a record-vs-replay
  mode flip raises the existing ReplayDivergenceError; the recorded
  provider replays native fixtures when constructed in native mode.
- #8: the schema pre-flight accepts the all-required subset and
  rejects optional fields, constraint keywords, and recursion.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

from activegraph import (
    Graph,
    ReplayDivergenceError,
    Runtime,
    clear_registry,
    behavior,
    llm_behavior,
)
from activegraph.llm import (
    AnthropicProvider,
    LLMMessage,
    LLMResponse,
    OpenAIProvider,
    RecordedLLMProvider,
    RecordingLLMProvider,
)
from activegraph.llm.errors import LLMBehaviorError
from activegraph.llm.native import (
    inject_additional_properties_false,
    native_schema_compatible,
)
from activegraph.llm.prompt import build_system_prompt, schema_to_json

from tests._llm_helpers import Claim, ClaimList, ScriptedProvider


class _Out(BaseModel):
    n: int


class _Loose(BaseModel):
    a: int
    b: int = 0  # optional field — outside the native subset


class _Ranged(BaseModel):
    n: int = Field(ge=1)  # numeric constraint — outside the subset


# ---------- #8: schema pre-flight -------------------------------------------


def test_preflight_accepts_all_required_nested_schema():
    assert native_schema_compatible(schema_to_json(ClaimList)) is True
    assert native_schema_compatible(schema_to_json(_Out)) is True


def test_preflight_rejects_optional_fields_and_constraints():
    assert native_schema_compatible(schema_to_json(_Loose)) is False
    assert native_schema_compatible(schema_to_json(_Ranged)) is False
    assert native_schema_compatible(None) is False
    assert native_schema_compatible({"type": "string"}) is False


def test_preflight_rejects_recursive_schema():
    recursive = {
        "type": "object",
        "properties": {"child": {"$ref": "#/$defs/Node"}},
        "required": ["child"],
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/Node"}},
                "required": ["child"],
            }
        },
    }
    assert native_schema_compatible(recursive) is False


def test_inject_additional_properties_false_reaches_nested_objects():
    schema = schema_to_json(ClaimList)
    assert schema is not None
    out = inject_additional_properties_false(schema)
    assert out["additionalProperties"] is False
    assert out["$defs"]["Claim"]["additionalProperties"] is False
    # Source dict untouched (deep copy).
    assert "additionalProperties" not in schema


# ---------- #2/#3: provider capability tables -------------------------------


def test_anthropic_capability_gating_and_override():
    p = AnthropicProvider(client=MagicMock())
    assert p.supports_native_structured_output("claude-sonnet-4-5") is True
    assert p.supports_native_structured_output("claude-haiku-4-5-20251001") is True
    assert p.supports_native_structured_output("claude-sonnet-4") is False
    custom = AnthropicProvider(
        client=MagicMock(), native_structured_output_models=("my-model",)
    )
    assert custom.supports_native_structured_output("my-model-v2") is True
    assert custom.supports_native_structured_output("claude-sonnet-4-5") is False


def test_openai_capability_gating():
    p = OpenAIProvider(client=MagicMock())
    assert p.supports_native_structured_output("gpt-4o-mini") is True
    assert p.supports_native_structured_output("gpt-3.5-turbo") is False
    assert p.supports_native_structured_output("o1-mini") is False


# ---------- #3: wire shapes --------------------------------------------------


def _anthropic_client(text: str):
    raw = SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model="claude-sonnet-4-5",
        stop_reason="end_turn",
    )
    client = MagicMock()
    client.messages.create.return_value = raw
    return client


def _complete(provider: Any, **overrides: Any) -> LLMResponse:
    kwargs: dict[str, Any] = dict(
        system="sys",
        messages=[LLMMessage(role="user", content="u")],
        model=overrides.pop("model"),
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Out,
        timeout_seconds=30,
    )
    kwargs.update(overrides)
    return provider.complete(**kwargs)


def test_anthropic_native_sends_output_config():
    client = _anthropic_client('{"n": 42}')
    p = AnthropicProvider(client=client)
    r = _complete(p, model="claude-sonnet-4-5", structured_output_mode="native")
    sent = client.messages.create.call_args.kwargs
    fmt = sent["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["n"]["type"] == "integer"
    # #5: validation boundary unmoved — parsed via the shared path.
    assert isinstance(r.parsed, _Out) and r.parsed.n == 42


def test_anthropic_prompt_mode_sends_no_output_config():
    client = _anthropic_client('{"n": 1}')
    p = AnthropicProvider(client=client)
    _complete(p, model="claude-sonnet-4-5")
    assert "output_config" not in client.messages.create.call_args.kwargs


def _openai_client(text: str):
    raw = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        model="gpt-4o-mini",
    )
    client = MagicMock()
    client.chat.completions.create.return_value = raw
    return client


def test_openai_native_sends_response_format():
    client = _openai_client('{"n": 42}')
    p = OpenAIProvider(client=client)
    r = _complete(p, model="gpt-4o-mini", structured_output_mode="native")
    sent = client.chat.completions.create.call_args.kwargs
    rf = sent["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "_Out"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False
    assert isinstance(r.parsed, _Out) and r.parsed.n == 42


def test_openai_prompt_mode_sends_no_response_format():
    client = _openai_client('{"n": 1}')
    p = OpenAIProvider(client=client)
    _complete(p, model="gpt-4o-mini")
    assert "response_format" not in client.chat.completions.create.call_args.kwargs


# ---------- #6: native system prompt -----------------------------------------


def test_native_system_prompt_drops_schema_block():
    kwargs: dict[str, Any] = dict(
        behavior_name="b",
        description="d",
        frame=None,
        output_schema_name="ClaimList",
        output_schema_json=schema_to_json(ClaimList),
    )
    prompt_mode = build_system_prompt(**kwargs)
    native = build_system_prompt(**kwargs, structured_output_mode="native")
    assert "Schema:" in prompt_mode and "Example instance" in prompt_mode
    assert "Schema:" not in native and "Example instance" not in native
    assert "Respond with JSON that matches the `ClaimList` schema." in native
    # Non-schema sections unchanged.
    assert native.startswith('You are an active-graph behavior named "b".')
    assert "Role: d" in native


# ---------- #7: hash and fixture identity ------------------------------------


def _register():
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="x",
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
        deterministic=True,
    )
    def extractor(event, graph, ctx, llm_output):
        for c in llm_output.claims:
            graph.add_object("claim", {"text": c.text, "confidence": c.confidence})


@dataclass
class _NativeScripted(ScriptedProvider):
    """ScriptedProvider that claims native capability and records the
    mode each call arrived with."""

    modes_seen: list[Optional[str]] = field(default_factory=list)

    def supports_native_structured_output(self, model: str) -> bool:
        return True

    def complete(self, **kw: Any) -> LLMResponse:
        self.modes_seen.append(kw.pop("structured_output_mode", None))
        return super().complete(**kw)


def _native_scripted(text: str = "Sample claim") -> _NativeScripted:
    return _NativeScripted(
        respond_fn=lambda m, s: ClaimList(claims=[Claim(text=text, confidence=0.9)])
    )


def test_prompt_mode_hashable_has_no_mode_key():
    clear_registry()
    _register()
    provider = _native_scripted()
    g = Graph()
    rt = Runtime(g, llm_provider=provider)  # flag defaults to False
    rt.run_goal("test")
    requested = [e for e in g.events if e.type == "llm.requested"]
    assert requested, "expected an llm.requested event"
    body = requested[0].payload["prompt"]
    assert "structured_output_mode" not in body  # byte-identical to pre-v1.3
    assert requested[0].payload["structured_output_mode"] == "prompt"
    # Flag off: the provider is never handed the kwarg.
    assert provider.modes_seen == [None]


def test_native_mode_end_to_end_and_requested_payload():
    clear_registry()
    _register()
    provider = _native_scripted()
    g = Graph()
    rt = Runtime(g, llm_provider=provider, native_structured_output=True)
    rt.run_goal("test")
    requested = [e for e in g.events if e.type == "llm.requested"]
    assert requested[0].payload["structured_output_mode"] == "native"
    assert requested[0].payload["prompt"]["structured_output_mode"] == "native"
    assert provider.modes_seen == ["native"]
    # #6 applied in the runtime path: no schema dump in the system text.
    assert "Schema:" not in requested[0].payload["prompt"]["system"]
    # #5: downstream world identical — the handler ran and projected.
    claims = g.objects(type="claim")
    assert len(claims) == 1 and claims[0].data["text"] == "Sample claim"


def test_flag_on_but_no_capability_resolves_prompt():
    clear_registry()
    _register()
    provider = ScriptedProvider(
        respond_fn=lambda m, s: ClaimList(claims=[Claim(text="t", confidence=0.5)])
    )  # no supports_native_structured_output method
    g = Graph()
    rt = Runtime(g, llm_provider=provider, native_structured_output=True)
    rt.run_goal("test")
    requested = [e for e in g.events if e.type == "llm.requested"]
    assert requested[0].payload["structured_output_mode"] == "prompt"


def test_flag_on_but_schema_outside_subset_resolves_prompt():
    clear_registry()

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T"})

    @llm_behavior(
        name="loose",
        on=["object.created"],
        description="x",
        output_schema=_Loose,
        deterministic=True,
    )
    def loose(event, graph, ctx, llm_output):
        pass

    provider = _NativeScripted(respond_fn=lambda m, s: _Loose(a=1))
    g = Graph()
    rt = Runtime(g, llm_provider=provider, native_structured_output=True)
    rt.run_goal("test")
    requested = [e for e in g.events if e.type == "llm.requested"]
    assert requested[0].payload["structured_output_mode"] == "prompt"
    assert provider.modes_seen == [None]


def test_native_and_prompt_hashes_differ():
    clear_registry()
    _register()
    hashes: dict[str, str] = {}
    for flag in (False, True):
        clear_registry()
        _register()
        provider = _native_scripted()
        g = Graph()
        rt = Runtime(g, llm_provider=provider, native_structured_output=flag)
        rt.run_goal("test")
        requested = [e for e in g.events if e.type == "llm.requested"]
        hashes["native" if flag else "prompt"] = requested[0].payload["prompt_hash"]
    assert hashes["prompt"] != hashes["native"]


# ---------- #7: recorded fixtures --------------------------------------------


def test_recorded_provider_native_fixture_round_trip():
    fixtures = tempfile.mkdtemp()
    inner = _native_scripted("recorded claim")
    recorder = RecordingLLMProvider(inner, fixtures)
    call = dict(
        system="sys",
        messages=[LLMMessage(role="user", content="u")],
        model="claude-sonnet-4-5",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=ClaimList,
        timeout_seconds=30,
    )
    recorder.complete(**call, structured_output_mode="native")

    native_replay = RecordedLLMProvider(fixtures, structured_output_mode="native")
    assert native_replay.supports_native_structured_output("any") is True
    r = native_replay.complete(**call, structured_output_mode="native")
    assert isinstance(r.parsed, ClaimList)
    assert r.parsed.claims[0].text == "recorded claim"

    # The same call hashed WITHOUT the mode field must miss: the mode is
    # part of prompt identity.
    prompt_replay = RecordedLLMProvider(fixtures)
    assert prompt_replay.supports_native_structured_output("any") is False
    with pytest.raises(LLMBehaviorError) as exc:
        prompt_replay.complete(**call)
    assert exc.value.reason == "llm.fixture_missing"


# ---------- #7: strict replay across a mode flip -----------------------------


def test_strict_replay_green_when_mode_matches_and_diverges_on_flip():
    clear_registry()
    _register()
    db = tempfile.mktemp(suffix=".db")
    try:
        provider = _native_scripted()
        g = Graph()
        rt = Runtime(
            g,
            llm_provider=provider,
            persist_to=db,
            native_structured_output=True,
        )
        rt.run_goal("test")

        # Same mode posture: strict replay is green.
        clear_registry()
        _register()
        Runtime.load(
            db,
            llm_provider=_native_scripted(),
            replay_strict=True,
            native_structured_output=True,
        )

        # Mode flip (recorded native, replayed prompt): a true divergence,
        # pinned by the existing ReplayDivergenceError. No new reason code.
        clear_registry()
        _register()
        with pytest.raises(ReplayDivergenceError):
            Runtime.load(
                db,
                llm_provider=_native_scripted(),
                replay_strict=True,
                native_structured_output=False,
            )
    finally:
        if os.path.exists(db):
            os.remove(db)


# ---------- provider parity ---------------------------------------------------


def test_native_parity_across_providers():
    """The same conforming response through both providers in native mode
    produces identical parsed output — the v1.1 tool-parity pattern."""
    a = AnthropicProvider(client=_anthropic_client('{"n": 7}'))
    o = OpenAIProvider(client=_openai_client('{"n": 7}'))
    ra = _complete(a, model="claude-sonnet-4-5", structured_output_mode="native")
    ro = _complete(o, model="gpt-4o-mini", structured_output_mode="native")
    assert ra.parsed == ro.parsed
    assert type(ra.parsed) is type(ro.parsed)
