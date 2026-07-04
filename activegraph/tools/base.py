"""Tool dataclass. CONTRACT v0.7 #2 / #5.

A Tool is metadata + callable, mirror image of Behavior. The runtime
introspects metadata to dispatch invocations and budget enforcement;
the callable is the actual body. The tool function signature is
fixed:

    def tool_fn(args: InputSchema, ctx: ToolContext) -> OutputSchema

`input_schema` and `output_schema` are Pydantic v2 BaseModel classes;
the runtime validates inputs before invocation and outputs after.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Optional


@dataclass
class Tool:
    name: str
    fn: Callable[..., Any]
    description: str = ""
    input_schema: Optional[type[Any]] = None
    output_schema: Optional[type[Any]] = None
    cost_per_call: Decimal = field(default_factory=lambda: Decimal("0"))
    timeout_seconds: float = 30.0
    # CONTRACT v0.7 #7. False (the default) means "replay must serve
    # this tool from the recorded fixture or fail loud"; True means
    # "replay may re-invoke this tool". The runtime's replay-cache
    # policy serves from cache by default for ALL tools, deterministic
    # or not, because deterministic-tool correctness depends on the
    # graph state at the moment of the call matching exactly — and the
    # runtime cannot cheaply verify that. `replay_reinvoke_deterministic`
    # on the Runtime is the opt-in that exercises re-invocation.
    deterministic: bool = False

    def to_definition(self) -> dict[str, Any]:
        """Provider-facing tool definition.

        Sent in the `tools=` parameter to `LLMProvider.complete()`.
        Anthropic and OpenAI both accept a similar shape; the provider
        translates if needed.
        """
        from activegraph.llm.prompt import schema_to_json

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": (
                schema_to_json(self.input_schema)
                if self.input_schema is not None
                else {"type": "object", "properties": {}}
            ),
        }
