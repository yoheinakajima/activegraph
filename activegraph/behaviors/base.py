"""Behavior + RelationBehavior + LLMBehavior. Plain holders for metadata
plus the callable.

A Behavior is data, not magic. The decorator wraps a function in one of
these; class-based behaviors subclass directly. The runtime introspects
the metadata to match events and build views.

CONTRACT v0.6 #2: an LLMBehavior is structurally a Behavior whose
invocation lifecycle is owned by the runtime — the developer-supplied
function is the 4-arg `handler` (event, graph, ctx, llm_output), NOT
the 3-arg `fn`. Per CONTRACT v0.6 #20, `LLMBehavior.build_prompt` is
public so a developer can inspect the exact bytes that would be sent
to the model without making a call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from activegraph.core.event import Event
    from activegraph.core.graph import Graph, Relation
    from activegraph.frame import Frame
    from activegraph.llm.prompt import AssembledPrompt
    from activegraph.runtime.runtime import Context


def _llm_behavior_fn_placeholder(
    event: "Event", graph: "Graph", ctx: "Context"
) -> None:  # pragma: no cover
    raise RuntimeError(
        "LLMBehavior.fn invoked directly. The runtime owns LLM behavior "
        "invocation via _invoke_llm; calling .run() bypasses prompt "
        "assembly, the provider, and the LLM event log. This is a bug."
    )


@dataclass
class Behavior:
    """Metadata plus the callable for an event-driven behavior.

    A Behavior is data, not magic: ``on`` (event types), ``where``
    (payload filter), ``pattern`` (Cypher-subset subscription),
    ``activate_after`` (event-count delay), and ``view_spec`` (what
    the runtime builds into ``ctx.view``) describe *when* it fires;
    the 3-arg ``fn`` — invoked as ``fn(event, graph, ctx)`` — is
    *what* runs. Instances come from ``@behavior`` or a pack; the
    runtime introspects the metadata to match events, and nothing
    here executes on its own.
    """

    name: str
    fn: Callable[..., None]
    on: list[str] = field(default_factory=list)
    where: Optional[dict[str, Any]] = None
    view_spec: Optional[dict[str, Any]] = None
    creates: list[str] = field(default_factory=list)
    budget: Optional[dict[str, Any]] = None
    priority: int = 0  # reserved; v0 ties resolved by registration order
    # CONTRACT v0.7 #8 / #9 / #11. A Cypher subset pattern string.
    # Compiled at registration time. None = event-type-only matching
    # (CONTRACT #10).
    pattern: Optional[str] = None
    pattern_matcher: Any = None  # PatternMatcher; set by Registry at startup
    # CONTRACT v0.7 #13. Event-count delay. None = fire immediately.
    activate_after: Optional[int] = None

    def run(self, event: "Event", graph: "Graph", ctx: "Context") -> None:
        self.fn(event, graph, ctx)


@dataclass
class RelationBehavior:
    """A behavior that fires once per matching relation edge.

    Same metadata surface as :class:`Behavior` plus
    ``relation_type``: when a triggering event touches a relation of
    that type, the 4-arg ``fn`` runs as
    ``fn(relation, event, graph, ctx)`` — once per (event, relation)
    pair. Deliberately not a subclass of ``Behavior``; the runtime
    dispatches the two kinds separately.
    """

    name: str
    fn: Callable[..., None]
    relation_type: str
    on: list[str] = field(default_factory=list)
    where: Optional[dict[str, Any]] = None
    view_spec: Optional[dict[str, Any]] = None
    creates: list[str] = field(default_factory=list)
    budget: Optional[dict[str, Any]] = None
    priority: int = 0
    # Pattern subscriptions also work on relation behaviors. The match
    # fires once per (event, relation) pair when the pattern matches.
    pattern: Optional[str] = None
    pattern_matcher: Any = None
    activate_after: Optional[int] = None

    def run(
        self, relation: "Relation", event: "Event", graph: "Graph", ctx: "Context"
    ) -> None:
        self.fn(relation, event, graph, ctx)


@dataclass
class LLMBehavior(Behavior):
    """A behavior whose body is an LLM call.

    The runtime owns prompt assembly, cache lookup, the provider call,
    event emission, and structured-output parsing. The developer's
    `handler` is invoked only after the LLM has responded (or a cached
    response was found) and the output was successfully parsed.

    CONTRACT v0.7: `tools=` declares a list of `Tool` objects (or
    strings naming globally-registered tools) the LLM can call during
    the turn loop. The runtime orchestrates the loop; the handler
    receives only the final structured output, never raw tool calls.
    """

    handler: Optional[Callable[..., None]] = None
    description: str = ""
    # v1.0.2 #1: model is optional. None = "use the configured provider's
    # default_model"; the runtime resolves and stamps the concrete string
    # at registration time. Existing call sites that pass an explicit
    # string keep working unchanged.
    model: Optional[str] = None
    output_schema: Optional[type[Any]] = None
    deterministic: bool = False
    max_tokens: int = 4096
    temperature: float = 0.7
    top_p: float = 1.0
    timeout_seconds: float = 60.0
    prompt_template: Optional[str] = None
    # v0.7
    tools: list[Any] = field(default_factory=list)  # Tool | str names
    max_tool_turns: int = 6

    def build_prompt(
        self,
        event: "Event",
        graph: "Graph",
        *,
        frame: Optional["Frame"] = None,
        structured_output_mode: str = "prompt",
    ) -> "AssembledPrompt":
        """Assemble the prompt that would be sent for this event.

        CONTRACT v0.6 #20 — public so a developer can inspect prompts
        without making an API call. Reproducible (pure over inputs);
        cheap (no I/O).
        """

        from activegraph.llm.prompt import assemble_prompt
        from activegraph.runtime.view_builder import (
            DEFAULT_RECENT_EVENTS,
            _resolve_event_path,
            build_view,
        )

        view = build_view(self, event, graph)
        around_id: Optional[str] = None
        depth: Optional[int] = None
        if self.view_spec:
            ar_expr = self.view_spec.get("around")
            if ar_expr:
                around_id = _resolve_event_path(ar_expr, event)
            depth = self.view_spec.get("depth")
        # v1.0.2 #1: if model is still None, the runtime hasn't stamped
        # a provider default yet — `build_prompt` is for inspection
        # (CONTRACT v0.6 #20) and must work without a Runtime, so fall
        # back to the v1.0.1 hardcoded default for inspection-time hash
        # stability. Real provider calls always go through Runtime,
        # which resolves the configured provider's default_model first.
        inspection_model = self.model or "claude-sonnet-4-5"
        return assemble_prompt(
            behavior_name=self.name,
            description=self.description,
            model=inspection_model,
            output_schema=self.output_schema,
            creates=self.creates,
            view=view,
            event=event,
            frame=frame,
            around=around_id,
            depth=depth,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            deterministic=self.deterministic,
            prompt_template=self.prompt_template,
            structured_output_mode=structured_output_mode,
        )
