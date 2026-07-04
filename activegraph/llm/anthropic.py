"""Reference provider: Anthropic.

CONTRACT v0.6 #4. The SDK is imported lazily so the rest of the LLM
package works without `pip install anthropic`. API key comes from
ANTHROPIC_API_KEY — never from code, never from a checked-in config.

What this provider does:
  * `complete()` — single non-streaming `messages.create` call.
    Structured output is handled by the wrapper, not the provider —
    we just hand back raw text plus token counts.
  * `count_tokens()` — Anthropic's official `count_tokens` API.
    Network roundtrip; the runtime only calls it when
    `budget.max_cost_usd` is set AND no cached response was found
    (decision-4 adjustment).
  * `estimate_cost()` — table-driven, USD Decimals. Pricing as of
    the model-family rates current in 2026; override via the
    `pricing=` constructor kwarg to keep it accurate over time.

No tool-use, no streaming, no multi-message orchestration. v0.6
keeps the surface narrow on purpose.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any, Mapping, Optional

from activegraph.llm.errors import LLMBehaviorError
from activegraph.llm.parsing import parse_structured_response as _parse_structured
from activegraph.llm.provider import LLMProvider
from activegraph.llm.types import LLMMessage, LLMResponse, ToolCall


# Per-million-token pricing in USD. Tracks the rates of the Claude 4.x
# family available in May 2026. Provide your own `pricing=` to override.
_DEFAULT_PRICING: dict[str, dict[str, str]] = {
    "claude-opus-4": {"input": "15", "output": "75"},
    "claude-sonnet-4": {"input": "3", "output": "15"},
    "claude-haiku-4-5": {"input": "1", "output": "5"},
}


def _pricing_for(model: str, pricing: Mapping[str, Mapping[str, str]]) -> tuple[Decimal, Decimal]:
    """Lookup by longest matching family prefix.

    `claude-sonnet-4-6` resolves to the `claude-sonnet-4` family entry.
    Unknown models fall back to sonnet-4 pricing and emit a warning
    via the returned `Decimal` (caller can detect by comparing to
    family default).
    """

    best_key: Optional[str] = None
    for key in pricing:
        if model.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        best_key = "claude-sonnet-4"
    entry = pricing[best_key]
    return Decimal(str(entry["input"])), Decimal(str(entry["output"]))


# Model families with GA structured-output support on the Claude API
# (CONTRACT v1.3 #1 #3). Table-driven like the pricing table; override
# via the `native_structured_output_models=` constructor kwarg to track
# the docs over time. Claude 4.0-era families are deliberately absent.
_NATIVE_STRUCTURED_OUTPUT_PREFIXES: tuple[str, ...] = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-sonnet-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
)


class AnthropicProvider(LLMProvider):
    # v1.0.2 #1: provider-aware default model. @llm_behavior(model=None)
    # resolves to this string at registration time.
    default_model: str = "claude-sonnet-4-5"

    def __init__(
        self,
        *,
        api_key_env: str = "ANTHROPIC_API_KEY",
        client: Any = None,
        pricing: Optional[Mapping[str, Mapping[str, str]]] = None,
        native_structured_output_models: Optional[tuple[str, ...]] = None,
    ) -> None:
        self._api_key_env = api_key_env
        self._client_override = client
        self._pricing: dict[str, dict[str, str]] = dict(pricing or _DEFAULT_PRICING)
        self._native_prefixes: tuple[str, ...] = (
            native_structured_output_models
            if native_structured_output_models is not None
            else _NATIVE_STRUCTURED_OUTPUT_PREFIXES
        )
        self._client_cached: Any = None

    # ---- client lazy-load ----

    def _client(self) -> Any:
        if self._client_override is not None:
            return self._client_override
        if self._client_cached is not None:
            return self._client_cached
        try:
            from anthropic import Anthropic  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "AnthropicProvider requires the `anthropic` SDK. "
                "Install with `pip install activegraph[llm]` "
                "or `pip install anthropic`."
            ) from e
        import os

        if os.environ.get(self._api_key_env) is None:
            raise RuntimeError(
                f"AnthropicProvider needs {self._api_key_env} in the environment."
            )
        self._client_cached = Anthropic()
        return self._client_cached

    # ---- LLMProvider methods ----

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
        client = self._client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_tokens),
            "messages": [_message_to_anthropic(m) for m in messages],
            "temperature": float(temperature),
        }
        if system:
            kwargs["system"] = system
        # top_p of 1.0 is the model default; only forward when narrowing.
        if top_p < 1.0:
            kwargs["top_p"] = float(top_p)
        if tools:
            # Anthropic's tools shape: {"name", "description", "input_schema"}.
            # Our Tool.to_definition() already emits this shape.
            kwargs["tools"] = list(tools)
        if structured_output_mode == "native" and output_schema is not None:
            # CONTRACT v1.3 #1 #3: GA structured outputs. The runtime
            # passes native mode only after the capability + schema
            # pre-flight resolved it, so this shape is always valid here.
            from activegraph.llm.native import inject_additional_properties_false
            from activegraph.llm.prompt import schema_to_json

            schema_json = schema_to_json(output_schema)
            assert schema_json is not None  # output_schema is not None
            kwargs["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": inject_additional_properties_false(schema_json),
                }
            }

        t0 = time.monotonic()
        try:
            raw = client.messages.create(timeout=timeout_seconds, **kwargs)
        except Exception as e:
            # Map provider exceptions to reason codes per CONTRACT v0.6 #11.
            reason = _classify_provider_exception(e)
            extras: dict[str, Any] = {
                "model": model,
                "exception_type": type(e).__name__,
                "message": str(e),
            }
            ra = _retry_after_seconds(e)
            if ra is not None:
                extras["retry_after_seconds"] = ra
            raise LLMBehaviorError(reason, str(e), payload_extras=extras) from e
        latency = time.monotonic() - t0

        text = _extract_text(raw)
        tool_calls = _extract_tool_calls(raw)
        parsed: Any = None
        # If the model returned tool_use blocks the loop isn't done yet
        # — the runtime will dispatch the tools and re-call. Parsing
        # structured output happens on the final turn, not mid-loop.
        if output_schema is not None and not tool_calls:
            parsed = _parse_structured(text, output_schema)

        in_tok = int(getattr(raw.usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(raw.usage, "output_tokens", 0) or 0)
        cost = self.estimate_cost(
            input_tokens=in_tok, output_tokens=out_tok, model=model
        )
        return LLMResponse(
            raw_text=text,
            parsed=parsed,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_seconds=latency,
            model=getattr(raw, "model", model),
            finish_reason=str(getattr(raw, "stop_reason", "end_turn") or "end_turn"),
            seed=None,  # Anthropic messages API has no seed parameter.
            cache_hit=False,
            tool_calls=tool_calls or None,
        )

    def estimate_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Decimal:
        in_price, out_price = _pricing_for(model, self._pricing)
        million = Decimal("1000000")
        return (Decimal(input_tokens) * in_price / million) + (
            Decimal(output_tokens) * out_price / million
        )

    def count_tokens(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
    ) -> int:
        client = self._client()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
        }
        if system:
            kwargs["system"] = system
        result = client.messages.count_tokens(**kwargs)
        return int(getattr(result, "input_tokens", 0) or 0)

    def recognizes_model(self, name: str) -> bool:
        """True for the ``claude-*`` model family. v1.0.2 #1."""
        return name.startswith("claude-")

    def supports_native_structured_output(self, model: str) -> bool:
        """True when ``model`` belongs to a family with GA structured
        outputs. CONTRACT v1.3 #1 #3.

        Prefix lookup against the model-family table (constructor-
        overridable, the pricing-table pattern). The runtime consults
        this — getattr-guarded, so its absence on a custom provider
        just means prompt mode — before resolving native mode.
        """
        return model.startswith(self._native_prefixes)


# ---- helpers ---------------------------------------------------------------


def _extract_text(raw: Any) -> str:
    """Concatenate text from a `Message.content` block list."""
    content = getattr(raw, "content", None)
    if content is None:
        return ""
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _extract_tool_calls(raw: Any) -> list[ToolCall]:
    """Extract `tool_use` blocks from a `Message.content` list. v0.7."""
    content = getattr(raw, "content", None)
    if content is None:
        return []
    out: list[ToolCall] = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type != "tool_use":
            continue
        call_id = getattr(block, "id", None) or ""
        name = getattr(block, "name", None) or ""
        args = getattr(block, "input", None) or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"_raw": args}
        out.append(ToolCall(id=call_id, name=name, args=dict(args)))
    return out


def _message_to_anthropic(m: LLMMessage) -> dict[str, Any]:
    """Convert an LLMMessage to Anthropic message-list shape.

    For role="tool", Anthropic wants a "user" message with a
    `tool_result` content block. For role in {"user","assistant"} the
    standard {role, content: str} shape works — except for the
    multi-turn tool-use case (v1.0.3 #4), where an assistant message
    that triggered tool_use must echo back its full content blocks
    (text + tool_use) so the subsequent user tool_result blocks
    reference matching tool_use_ids in the preceding assistant turn.
    Direct Anthropic API access tolerated raw_text-only echo; the
    Vertex AI proxy enforces the spec strictly and 400s without it.
    """
    if m.role == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_use_id or "",
                    "content": m.content,
                }
            ],
        }
    if m.role == "assistant" and m.tool_calls:
        blocks: list[dict[str, Any]] = []
        if m.content:
            blocks.append({"type": "text", "text": m.content})
        for tc in m.tool_calls:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": dict(tc.args),
                }
            )
        return {"role": "assistant", "content": blocks}
    return {"role": m.role, "content": m.content}


def _classify_provider_exception(e: Exception) -> str:
    name = type(e).__name__.lower()
    if "ratelimit" in name or "429" in str(e):
        return "llm.rate_limited"
    if "timeout" in name or "connect" in name:
        return "llm.network_error"
    return "llm.network_error"


def _retry_after_seconds(e: Exception) -> Optional[float]:
    response = getattr(e, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        return None
    ra = headers.get("retry-after") if hasattr(headers, "get") else None
    if ra is None:
        return None
    try:
        return float(ra)
    except (TypeError, ValueError):
        return None
