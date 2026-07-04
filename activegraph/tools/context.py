"""ToolContext — what a tool function body sees. CONTRACT v0.7 #5.

A `ToolContext` is to a tool what `BehaviorGraph` is to a behavior:
the surface deliberately omits anything a tool shouldn't touch.

Provides:
  - behavior_name:   the @llm_behavior that triggered this tool call
  - event_id:        the triggering event id (NOT the tool.requested
                     event id — that one is on the wrapping turn loop)
  - frame:           the active Frame, if any
  - idempotency_key: opaque pass-through for tools to forward to
                     external APIs that support idempotency tokens.
                     The runtime never uses this for dedupe — that's
                     the cache's job (CONTRACT v0.7 #5 / idempotency
                     decision).
  - timeout_seconds: copied from the @tool decorator; tools may
                     enforce via signal/select; the runtime does NOT
                     forcibly preempt (no thread/signal magic).
  - logger:          a `logging.Logger` named "activegraph.tools.<tool>"

Does NOT provide a graph reference. Tools that need to read graph
state (e.g. `graph_query`) close over the Graph at registration time
via the `make_graph_query_tool(graph)` factory. The constraint is
intentional: tools that need graph state should be obvious (named,
constructed deliberately) rather than every tool having ambient
graph access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from activegraph.frame import Frame


@dataclass
class ToolContext:
    """What a tool function body sees. CONTRACT v0.7 #5.

    Deliberately narrow: the triggering behavior and event id, the
    active frame, an ``idempotency_key`` to forward to external APIs
    (the runtime never uses it for dedupe — caching is the cache's
    job), the decorator's ``timeout_seconds`` (advisory; the runtime
    does not preempt), and a per-tool logger. No graph reference —
    tools that need graph state close over it explicitly at
    registration (see ``make_graph_query_tool``).
    """

    behavior_name: str
    event_id: str
    frame: Optional["Frame"]
    idempotency_key: str
    timeout_seconds: float
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("activegraph.tools")
    )
