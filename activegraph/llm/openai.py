"""OpenAI provider. CONTRACT v1.0.1 #5.

Surface parity with :class:`activegraph.llm.anthropic.AnthropicProvider`:
both providers expose the same :class:`LLMProvider` Protocol surface
(``complete``, ``estimate_cost``, ``count_tokens``) so a runtime
swapping one for the other doesn't reshape any call site.

What this provider does:

  * ``complete()`` — single non-streaming ``chat.completions.create``
    call. Structured output uses the instruction-based path: the
    framework's :func:`build_system_prompt` already embeds the schema
    + example instance upstream of the provider (CONTRACT v1.0.1 #2),
    and this provider parses the resulting JSON out of the response
    via the shared :func:`parse_structured_response` helper. OpenAI's
    native ``response_format={"type":"json_schema",...}`` mode is a
    v1.1 candidate (CONTRACT v1.0.1 #5 (c)).
  * ``count_tokens()`` — client-side via ``tiktoken`` when available,
    char/4 heuristic fallback when not. The asymmetry with Anthropic
    (server-side count_tokens) is documented in CONTRACT v1.0.1 #5
    (c) rather than hidden — budget gating against ``max_cost_usd``
    sees the heuristic when tiktoken is missing, with a one-time
    debug-level log on the first heuristic call.
  * ``estimate_cost()`` — table-driven family-prefix lookup, same
    shape as Anthropic's. Defaults track GPT-4o-family rates current
    in 2026; override via the ``pricing=`` constructor kwarg.

Tool use follows the runtime's shared LLM/tool-loop contract. The
provider translates the framework's Anthropic-shaped tool definitions
(``{name, description, input_schema}``) into OpenAI Chat Completions
``tools=[{type:"function", function:{...}}]`` entries and extracts
returned function tool calls into :class:`ToolCall` objects.

API key comes from ``OPENAI_API_KEY`` — never from code, never from
a checked-in config. Same loud-failure shape as ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import logging
import json
import time
from decimal import Decimal
from typing import Any, Mapping, Optional

from activegraph.llm.errors import LLMBehaviorError
from activegraph.llm.parsing import parse_structured_response as _parse_structured
from activegraph.llm.provider import LLMProvider
from activegraph.llm.types import LLMMessage, LLMResponse, ToolCall


_log = logging.getLogger("activegraph.llm.openai")


# Per-million-token pricing in USD. Tracks the rates of the GPT-4 / 4o
# family available in 2026. Provide your own `pricing=` to override.
_DEFAULT_PRICING: dict[str, dict[str, str]] = {
    "gpt-4o-mini": {"input": "0.15", "output": "0.6"},
    "gpt-4o": {"input": "2.5", "output": "10"},
    "gpt-4-turbo": {"input": "10", "output": "30"},
    "gpt-4": {"input": "30", "output": "60"},
    "gpt-3.5-turbo": {"input": "0.5", "output": "1.5"},
}


def _pricing_for(
    model: str, pricing: Mapping[str, Mapping[str, str]]
) -> tuple[Decimal, Decimal]:
    """Lookup by longest matching family prefix.

    ``gpt-4o-2024-11-20`` resolves to the ``gpt-4o`` family entry;
    ``gpt-4o-mini-2024-07-18`` resolves to ``gpt-4o-mini`` because
    that key is longer. Unknown models fall back to ``gpt-4o`` pricing.
    """
    best_key: Optional[str] = None
    for key in pricing:
        if model.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        best_key = "gpt-4o"
    entry = pricing[best_key]
    return Decimal(str(entry["input"])), Decimal(str(entry["output"]))


class OpenAIProvider(LLMProvider):
    # v1.0.2 #1: provider-aware default model. @llm_behavior(model=None)
    # resolves to this string at registration time. gpt-4o-mini is the
    # cheap, fast member of the GPT-4o family — matches Anthropic's
    # default-model shape (cheapest sensible default; override for prod).
    default_model: str = "gpt-4o-mini"

    # Model families with structured-output (response_format
    # json_schema, strict) support (CONTRACT v1.3 #1 #3). Table-driven
    # like the pricing table; override via the
    # `native_structured_output_models=` constructor kwarg. The o1-era
    # preview/mini models are deliberately absent.
    _NATIVE_STRUCTURED_OUTPUT_PREFIXES: tuple[str, ...] = (
        "gpt-4o",
        "gpt-4.1",
        "gpt-5",
        "o3",
        "o4",
    )

    # v1.0.2 #1: the model-name prefixes this provider recognizes.
    # `gpt-` covers the GPT-3.5 / GPT-4 / GPT-4o families; the
    # `o1-` / `o3-` / `o4-` prefixes cover the reasoning-model line
    # (o1, o3, o4 and their dated variants). Names matching none of
    # these pass through registration silently per v1.0.2 #1 (b)'s
    # permissive-default rule.
    _RECOGNIZED_PREFIXES: tuple[str, ...] = ("gpt-", "o1-", "o3-", "o4-")

    def __init__(
        self,
        *,
        api_key_env: str = "OPENAI_API_KEY",
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
            else self._NATIVE_STRUCTURED_OUTPUT_PREFIXES
        )
        self._client_cached: Any = None
        # One-time debug-level log when count_tokens falls back to the
        # char/4 heuristic. Per-instance flag so a long-running runtime
        # logs once, not once per LLM call.
        self._heuristic_warned: bool = False

    # ---- client lazy-load ----

    def _client(self) -> Any:
        if self._client_override is not None:
            return self._client_override
        if self._client_cached is not None:
            return self._client_cached
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "OpenAIProvider requires the `openai` SDK. "
                "Install with `pip install activegraph[llm]` "
                "or `pip install activegraph[openai]`."
            ) from e
        import os

        if os.environ.get(self._api_key_env) is None:
            raise RuntimeError(
                f"OpenAIProvider needs {self._api_key_env} in the environment."
            )
        self._client_cached = OpenAI()
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
        openai_messages: list[dict[str, Any]] = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        for m in messages:
            openai_messages.append(_message_to_openai(m))

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "timeout": timeout_seconds,
        }
        if top_p < 1.0:
            kwargs["top_p"] = float(top_p)
        if tools:
            kwargs["tools"] = [_tool_definition_to_openai(t) for t in tools]
        if structured_output_mode == "native" and output_schema is not None:
            # CONTRACT v1.3 #1 #3: Chat Completions structured outputs.
            # The runtime passes native mode only after the capability +
            # schema pre-flight resolved it.
            from activegraph.llm.native import inject_additional_properties_false
            from activegraph.llm.prompt import schema_to_json

            schema_json = schema_to_json(output_schema)
            assert schema_json is not None  # output_schema is not None
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": getattr(output_schema, "__name__", "OutputSchema"),
                    "schema": inject_additional_properties_false(schema_json),
                    "strict": True,
                },
            }

        t0 = time.monotonic()
        try:
            raw = client.chat.completions.create(**kwargs)
        except Exception as e:
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
        if output_schema is not None and not tool_calls:
            parsed = _parse_structured(text, output_schema)

        usage = getattr(raw, "usage", None)
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        cost = self.estimate_cost(
            input_tokens=in_tok, output_tokens=out_tok, model=model
        )

        # Surface OpenAI's finish_reason verbatim. The framework doesn't
        # gate on specific strings — "stop", "length", "content_filter"
        # all flow through as-is; downstream callers can inspect.
        finish = "stop"
        choices = getattr(raw, "choices", None) or []
        if choices:
            finish = str(getattr(choices[0], "finish_reason", None) or "stop")

        return LLMResponse(
            raw_text=text,
            parsed=parsed,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_seconds=latency,
            model=getattr(raw, "model", model),
            finish_reason=finish,
            seed=None,
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
        # tiktoken-if-available; chars/4 fallback. The asymmetry vs
        # AnthropicProvider's server-side count is documented in
        # CONTRACT v1.0.1 #5 (c).
        try:
            import tiktoken  # type: ignore
        except ImportError:
            return self._heuristic_count(system, messages)

        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            # Unknown model — tiktoken's `cl100k_base` is the GPT-4
            # family encoder; safe default.
            try:
                enc = tiktoken.get_encoding("cl100k_base")
            except Exception:
                return self._heuristic_count(system, messages)

        total = 0
        if system:
            total += len(enc.encode(system))
        for m in messages:
            total += len(enc.encode(m.content))
            # Each message carries ~4 tokens of OpenAI chat-formatting
            # overhead per the published "How to count tokens" guide.
            total += 4
        return max(1, total)

    def recognizes_model(self, name: str) -> bool:
        """True for OpenAI model families (``gpt-*``, ``o1-*``, ``o3-*``,
        ``o4-*``). v1.0.2 #1.
        """
        return any(name.startswith(p) for p in self._RECOGNIZED_PREFIXES)

    def supports_native_structured_output(self, model: str) -> bool:
        """True when ``model`` belongs to a family with structured-output
        support. CONTRACT v1.3 #1 #3.

        Prefix lookup against the model-family table (constructor-
        overridable, the pricing-table pattern). Consulted by the
        runtime — getattr-guarded — before resolving native mode.
        """
        return model.startswith(self._native_prefixes)

    def _heuristic_count(self, system: str, messages: list[LLMMessage]) -> int:
        if not self._heuristic_warned:
            _log.debug(
                "OpenAIProvider.count_tokens using chars/4 heuristic "
                "(tiktoken not installed). Token counts feed "
                "budget.max_cost_usd gating; install tiktoken for "
                "accurate accounting: pip install activegraph[openai]."
            )
            self._heuristic_warned = True
        total = len(system) + sum(len(m.content) for m in messages)
        return max(1, total // 4)


# ---- helpers ---------------------------------------------------------------


def _extract_text(raw: Any) -> str:
    """Pull assistant text from an OpenAI ``ChatCompletion``."""
    choices = getattr(raw, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # Some SDK versions return a list of content blocks; concatenate text.
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None) or (
            block.get("text") if isinstance(block, dict) else None
        )
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _message_to_openai(m: LLMMessage) -> dict[str, Any]:
    """Convert an LLMMessage to the OpenAI chat-message shape.
    """
    if m.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": m.tool_use_id or "",
            "content": m.content,
        }
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.args, sort_keys=True),
                    },
                }
                for tc in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}


def _tool_definition_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    """Translate framework/Anthropic tool definitions to OpenAI tools."""
    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        fn = dict(tool["function"])
        parameters = fn.get("parameters")
        if parameters is None:
            parameters = fn.get("input_schema")
        return {
            "type": "function",
            "function": {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": parameters or {"type": "object", "properties": {}},
            },
        }
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": (
                tool.get("parameters")
                or tool.get("input_schema")
                or {"type": "object", "properties": {}}
            ),
        },
    }


def _extract_tool_calls(raw: Any) -> list[ToolCall]:
    choices = getattr(raw, "choices", None) or []
    if not choices:
        return []
    message = getattr(choices[0], "message", None)
    if message is None:
        return []
    raw_calls = getattr(message, "tool_calls", None)
    if raw_calls is None and isinstance(message, dict):
        raw_calls = message.get("tool_calls")
    out: list[ToolCall] = []
    for call in raw_calls or []:
        call_id = _get(call, "id") or ""
        fn = _get(call, "function") or {}
        name = _get(fn, "name") or ""
        args_raw = _get(fn, "arguments") or {}
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw)
            except Exception:
                args = {"_raw": args_raw}
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {}
        out.append(ToolCall(id=call_id, name=name, args=dict(args)))
    return out


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _classify_provider_exception(e: Exception) -> str:
    name = type(e).__name__.lower()
    if "ratelimit" in name or "429" in str(e):
        return "llm.rate_limited"
    if "timeout" in name or "connect" in name:
        return "llm.network_error"
    # Auth failures (`AuthenticationError`, etc.) land here — CONTRACT
    # v0.6 #11 taxonomy is closed; no `llm.auth_error` reason code,
    # message preserved verbatim. See CONTRACT v1.0.1 #5.
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
