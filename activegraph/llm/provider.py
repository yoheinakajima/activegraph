"""The `LLMProvider` Protocol every provider implements.

CONTRACT v0.6 #3, extended in v0.7, additively widened in v1.0.2 #1.
Narrow, explicit, keyword-only. Shipped reference implementations are
`AnthropicProvider` and `OpenAIProvider`. Tests use
`RecordedLLMProvider` + `RecordingLLMProvider`. The demo ships its
own scripted provider.

A provider does three things plus two declarations:

  * `complete()`: run a single non-streaming completion. v0.7 adds
    an optional `tools=` parameter; when non-empty, the model is
    allowed to return tool_use blocks in the response.
  * `estimate_cost()`: turn token counts into USD (Decimal).
  * `count_tokens()`: provider-official input token count for the
    prompt that's about to be sent. Used for pre-call budget gating
    when `budget.max_cost_usd` is set; otherwise skipped (see
    CONTRACT v0.6 #4 / decision 10).
  * `default_model`: the model name to use when an `@llm_behavior`
    didn't pin one (v1.0.2 #1).
  * `recognizes_model(name)`: True when `name` belongs to a model
    family this provider serves. Used by the runtime at
    registration time to flag cross-provider mismatches before the
    first network call (v1.0.2 #1).

`default_model` and `recognizes_model` are additive: the runtime
guards their use with `getattr(...)`, so custom providers that
pre-date v1.0.2 keep working — they just require an explicit
`model=` on every `@llm_behavior` and don't participate in
cross-provider validation.

No streaming, no multi-model orchestration — those are deferred to
v0.8+. Tool use IS in v0.7, but the loop is orchestrated by the
runtime, not the provider: provider returns `tool_calls`, runtime
invokes the tool, runtime re-calls `complete()` with the result
echoed back as a `role="tool"` message.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional, Protocol, runtime_checkable

from activegraph.llm.types import LLMMessage, LLMResponse


@runtime_checkable
class LLMProvider(Protocol):
    # v1.0.2 #1: declared as an attribute on the Protocol. Concrete
    # providers set it as a class attribute; custom providers may
    # omit it (the runtime falls back to getattr-with-default).
    default_model: str

    def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        output_schema: Optional[type],
        timeout_seconds: float,
        tools: Optional[list[dict[str, Any]]] = None,
        structured_output_mode: str = "prompt",
    ) -> LLMResponse:
        ...

    def estimate_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Decimal:
        ...

    def count_tokens(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
    ) -> int:
        ...

    def supports_native_structured_output(self, model: str) -> bool:
        """True when the provider can enforce ``output_schema`` natively
        for ``model`` (constrained decoding). CONTRACT v1.3 #1.

        Additive like ``default_model`` / ``recognizes_model``: the
        runtime guards the lookup with ``getattr(...)``, so custom
        providers that pre-date v1.3 keep working and simply resolve to
        the prompt-embedded path. When this returns True for the
        resolved model (and the schema passes the offline pre-flight),
        the runtime calls ``complete()`` with
        ``structured_output_mode="native"``; prompt-mode calls omit the
        parameter entirely, staying byte-identical to pre-v1.3 calls.
        """
        ...

    def recognizes_model(self, name: str) -> bool:
        """True when `name` belongs to a model family this provider serves.

        v1.0.2 #1. Used at registration time to flag cross-provider
        mismatches (e.g. ``model="claude-sonnet-4-5"`` on a runtime
        configured with ``OpenAIProvider``). Permissive by default:
        unknown names — fine-tuned models, internal deployment names,
        experimental prefixes — should return False so the runtime
        passes them through without a diagnostic.
        """
        ...
