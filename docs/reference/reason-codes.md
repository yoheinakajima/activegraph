# Reason Codes

Reason codes are stable string discriminators on failure-shaped event
payloads. They let behaviors, operators, and tests branch on failure
kind without parsing human prose.

The framework uses reason codes in three places:

- `behavior.failed.payload.reason`
- `tool.responded.payload.error.reason`
- `llm.responded.payload.error.reason` for failed provider attempts

Patch rejection reasons are not part of this taxonomy. A
`patch.rejected` reason is domain or policy prose attached to that
specific patch decision.

## LLM

| Code | Where | Meaning |
| --- | --- | --- |
| `llm.network_error` | `llm.responded.error`, `behavior.failed` | Provider call failed before usable output. The runtime retries this code before terminal failure. |
| `llm.rate_limited` | `llm.responded.error`, `behavior.failed` | Provider rejected the request as rate-limited. The runtime retries this code before terminal failure. |
| `llm.parse_error` | `llm.responded.error`, `behavior.failed` | Provider returned text that could not be parsed as JSON for a structured-output behavior. |
| `llm.schema_violation` | `llm.responded.error`, `behavior.failed` | Provider returned JSON that did not satisfy the declared `output_schema`. |
| `llm.fixture_missing` | `behavior.failed` | `RecordedLLMProvider` has no fixture for the prompt hash. |
| `llm.prompt_assembly_error` | `behavior.failed` | Runtime prompt construction failed before any provider request was emitted. |

Custom providers may raise `LLMBehaviorError` with a provider-specific
`llm.*` code. Framework code emits only the codes above.

## Tools

| Code | Where | Meaning |
| --- | --- | --- |
| `tool.timeout` | `tool.responded.error`, `behavior.failed` | Tool exceeded its declared timeout. |
| `tool.network_error` | `tool.responded.error`, `behavior.failed` | Tool's network dependency failed. |
| `tool.invalid_input` | `tool.responded.error`, `behavior.failed` | Tool arguments failed the tool input schema. |
| `tool.invalid_output` | `tool.responded.error`, `behavior.failed` | Tool return value failed the tool output schema. |
| `tool.execution_error` | `tool.responded.error`, `behavior.failed` | Tool body raised an exception. |
| `tool.fixture_missing` | `tool.responded.error`, `behavior.failed` | Recorded tool cache has no fixture for the tool-call hash. |
| `tool.unknown_tool` | `behavior.failed` | LLM asked for a tool the behavior did not declare. |
| `tool.max_turns_exhausted` | `behavior.failed` | LLM/tool turn loop reached `max_tool_turns` without a final non-tool response. |

## Budget

Budget codes appear on `behavior.failed` when a behavior cannot start
or continue because a configured limit was hit.

| Code | Budget dimension |
| --- | --- |
| `budget.cost_exhausted` | `max_cost_usd` |
| `budget.llm_calls_exhausted` | `max_llm_calls` |
| `budget.tool_calls_exhausted` | `max_tool_calls` |
| `budget.behavior_calls_exhausted` | `max_behavior_calls` |
| `budget.events_exhausted` | `max_events` |
| `budget.seconds_exhausted` | `max_seconds` |
| `budget.patches_exhausted` | `max_patches` |
| `budget.depth_exhausted` | `max_depth` |
| `budget.exhausted` | Fallback when the exhausted dimension is unavailable. |

`runtime.budget_exhausted` uses `payload.exhausted_by` with the raw
budget dimension name (`max_events`, `max_seconds`, and so on). That
is a lifecycle stop record, not a `behavior.failed` reason code.

## Raw Exceptions

When a normal Python behavior raises an exception that is not one of
the structured LLM/tool/budget carriers, the runtime emits
`behavior.failed` with no payload `reason`. The WARNING log and
`Runtime.errors` projection use the synthetic pattern
`exception.<ClassName>` for operator filtering, for example:

| Code | Meaning |
| --- | --- |
| `exception.ValueError` | A behavior raised `ValueError`. |
| `exception.RuntimeError` | A behavior raised `RuntimeError`. |

The class-name suffix is open-ended because it reflects user code.

## Related Pages

- [Failure model](../concepts/failure-model.md)
- [LLMBehaviorError](errors/llm-behavior-error.md)
- [ToolError](errors/tool-error.md)
- [UnknownToolError](errors/unknown-tool-error.md)
