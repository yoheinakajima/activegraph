# LLM providers

Active Graph ships two concrete `LLMProvider` implementations.
Both expose identical Protocol surface — `complete()`,
`estimate_cost()`, `count_tokens()` — so a runtime swapping one for
the other doesn't reshape any call site. Choose by the model family
you want; everything else is the same.

```python
from activegraph import Graph, Runtime
from activegraph.llm import AnthropicProvider, OpenAIProvider

rt = Runtime(Graph(), llm_provider=AnthropicProvider())  # or:
rt = Runtime(Graph(), llm_provider=OpenAIProvider())
```

## Installing

Pick one of three extras. They install cleanly and don't conflict.

```bash
pip install "activegraph[anthropic]"   # AnthropicProvider only
pip install "activegraph[openai]"      # OpenAIProvider only
pip install "activegraph[llm]"         # both providers
```

The `[openai]` extra also pulls in `tiktoken` so client-side token
counting is accurate; see the count_tokens row below for what
happens when tiktoken is missing.

## API keys

Both providers read their API key from the environment, never from
code or a checked-in config:

```bash
export ANTHROPIC_API_KEY='...'
export OPENAI_API_KEY='...'
```

Override the env-var name via the `api_key_env=` constructor kwarg
if you need a different one (per-environment key rotation, for
example).

## Default model resolution

Each provider declares a `default_model` — the model name the
runtime uses when an `@llm_behavior` doesn't pin one explicitly:

```python
@llm_behavior(name="extractor", output_schema=Claim)
def extractor(event, graph, ctx, llm_output):
    ...
```

With `AnthropicProvider()` this resolves to `"claude-sonnet-4-5"`;
with `OpenAIProvider()` it resolves to `"gpt-4o-mini"`. The
runtime stamps the resolved name onto the behavior at
registration time (inside `Runtime(...)`'s first registry
materialization), so swapping providers is a one-line change:

```python
rt = Runtime(Graph(), llm_provider=OpenAIProvider())  # gpt-4o-mini
rt = Runtime(Graph(), llm_provider=AnthropicProvider())  # claude-sonnet-4-5
```

Pass `model="..."` on the decorator to override:

```python
@llm_behavior(name="extractor", output_schema=Claim, model="gpt-4o")
def extractor(event, graph, ctx, llm_output):
    ...
```

## Cross-provider model-name validation

When a behavior pins `model="..."` explicitly, the runtime checks
the name against each shipped provider's `recognizes_model()`:

| Provider | Recognized prefixes |
| --- | --- |
| `AnthropicProvider` | `claude-` |
| `OpenAIProvider` | `gpt-`, `o1-`, `o3-`, `o4-` |

If the configured provider doesn't recognize the name but a
*different* shipped provider does, the runtime raises
`InvalidRuntimeConfiguration` at registration time with a
structured error naming both providers. This catches the most
common shape of provider-swap misconfiguration — an `@llm_behavior`
copied from an Anthropic example into an OpenAI-configured runtime
— before the first network call, instead of letting the provider
404 silently.

Names no shipped provider recognizes (custom deployments, OpenAI
fine-tunes like `ft:gpt-4o-mini:org::id`, internal naming
conventions) pass through silently. The validation is permissive
by design: only *recognized* cross-provider mismatches fire.

## Side-by-side

| Aspect | `AnthropicProvider` | `OpenAIProvider` |
| --- | --- | --- |
| `default_model` (used when `@llm_behavior` omits `model=`) | `"claude-sonnet-4-5"` | `"gpt-4o-mini"` |
| Recognized model families (per `recognizes_model()`) | `claude-*` | `gpt-*`, `o1-*`, `o3-*`, `o4-*` |
| API key env | `ANTHROPIC_API_KEY` | `OPENAI_API_KEY` |
| SDK | `anthropic>=0.40` | `openai>=1.0` |
| Structured output | Instruction-based by default: schema + example instance embedded in the system prompt by [`build_system_prompt`](api/index.md); provider parses JSON via the shared `parse_structured_response` helper. Opt into native constrained decoding with `Runtime(native_structured_output=True)` — sends Messages API `output_config` on supported `claude-*` families | Same default path. Native mode sends Chat Completions `response_format={"type": "json_schema", ..., "strict": true}` on supported families (`gpt-4o`, `gpt-4.1`, `gpt-5`, `o3`, `o4`) |
| `count_tokens()` | Server-side via `messages.count_tokens` (1 roundtrip per call when `budget.max_cost_usd` is set and no cache hit) | Client-side via `tiktoken` when available; char/4 heuristic fallback with a one-time debug log if tiktoken is missing |
| Tool use | Supported (`Tool.to_definition()` emits Anthropic shape) | Supported. The provider translates framework/Anthropic-shaped tool definitions into OpenAI Chat Completions `function` tools and extracts returned `tool_calls` into the shared `ToolCall` shape |
| Exception mapping | `llm.rate_limited` on 429-shaped errors; `llm.network_error` for everything else (timeouts, connection errors, **auth failures**) | Same mapping |
| Pricing | Family-prefix lookup; override with `pricing=` kwarg | Family-prefix lookup; override with `pricing=` kwarg |

## Native structured output (opt-in)

CONTRACT v1.3 #1. With `Runtime(native_structured_output=True)`, the
runtime resolves a structured-output mode per behavior at
registration time: native constrained decoding when the provider
supports it for the resolved model **and** the behavior's
`output_schema` fits the native subset (every field required, no
numeric/string constraint keywords, no recursion); the
prompt-embedded path otherwise. Nothing changes at the
`@llm_behavior` surface — the schema stays `output_schema=`.

Three things to know before flipping the flag:

- **Prompt hashes change.** Native mode drops the schema block from
  the system prompt and adds a mode field to the hash input, so LLM
  caches recorded in prompt mode won't be hit, and
  `replay_strict=True` will (correctly) raise
  `ReplayDivergenceError` replaying a log recorded under the other
  mode. Match the flag to how the log was recorded.
- **Fallback is silent but audited.** A behavior that can't go
  native (provider capability, model family, or schema subset) uses
  the prompt path; the resolved mode is recorded on every
  `llm.requested` event's `structured_output_mode` payload field.
- **Validation doesn't move.** Responses still flow through
  `parse_structured_response`, so `llm.parse_error` /
  `llm.schema_violation` semantics are identical in both modes.

`RecordedLLMProvider` replays whatever mode the fixture was recorded
in — construct it with `structured_output_mode="native"` to serve
native-mode fixtures (the default `"prompt"` keeps every pre-v1.3
fixture reachable unchanged).

## Mixing with [`RecordedLLMProvider`](api/index.md)

The fixture-backed provider is provider-agnostic: fixtures are keyed
by prompt-content hash, and the model name (`claude-…` or `gpt-…`)
is part of the hash input. Fixtures recorded against one provider
replay against `RecordedLLMProvider` regardless of which live
provider you switch to next.

```python
from activegraph.llm import RecordingLLMProvider, OpenAIProvider

inner = OpenAIProvider()
provider = RecordingLLMProvider(inner, fixtures_dir="tests/fixtures/llm")
```

`RecordingLLMProvider` wraps either concrete provider the same way.
Record once against a live key, commit the fixtures, run tests
against `RecordedLLMProvider` thereafter.

## Writing a custom provider

`LLMProvider` is a runtime-checkable `Protocol`. Any class with the
three methods is a provider — no inheritance required, no
registration step:

```python
from decimal import Decimal
from activegraph.llm import LLMMessage, LLMResponse, LLMProvider

class MyProvider:
    default_model = "my-model-name"   # v1.0.2 #1 — used when @llm_behavior omits model=

    def complete(self, *, system, messages, model, max_tokens,
                 temperature, top_p, output_schema, timeout_seconds,
                 tools=None, structured_output_mode="prompt") -> LLMResponse:
        ...

    def estimate_cost(self, *, input_tokens, output_tokens, model) -> Decimal:
        ...

    def count_tokens(self, *, system, messages, model) -> int:
        ...

    def recognizes_model(self, name: str) -> bool:  # v1.0.2 #1
        return name.startswith("my-")

    def supports_native_structured_output(self, model: str) -> bool:  # v1.3 #1
        return False

assert isinstance(MyProvider(), LLMProvider)
```

`default_model`, `recognizes_model`, and
`supports_native_structured_output` are additive (v1.0.2 #1 /
v1.3 #1). Custom providers that omit them keep working at every
call site — the runtime guards each lookup with `getattr(...)` —
they just require an explicit `model=` on every `@llm_behavior`,
don't participate in cross-provider validation, and resolve to the
prompt-embedded structured-output path. (The `isinstance` check
above requires the full current method set; the runtime itself
never isinstance-checks providers.)

If your provider exposes the framework's instruction-based
structured-output path (most do), reuse
`parse_structured_response(text, schema)` from
`activegraph.llm.parsing` for byte-identical error semantics with
the shipped providers — same `llm.parse_error` and
`llm.schema_violation` reason codes for the same response shapes.

See [CONTRACT v1.0.1 #5](https://github.com/yoheinakajima/activegraph/blob/main/CONTRACT.md)
for the provider-commitment surface: which methods are stable and
which behaviors are provider-dependent (`count_tokens`). Native
structured-output mode is specified in CONTRACT v1.3 #1.
