"""@tool decorator + global tool registry. CONTRACT v0.7 #2.

The registry mirrors the @behavior registry: decoration pushes into
a module-level list; tests call `clear_tool_registry()` for
isolation. Runtime construction snapshots the registry — late
registrations after `_ensure_registry()` are ignored.

`Runtime(graph, tools=[...])` is the explicit override that bypasses
the global registry, mirroring how `behaviors=[...]` works.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Optional

from activegraph.tools.base import Tool


_TOOL_REGISTRY: list[Tool] = []


def clear_tool_registry() -> None:
    """Empty the global ``@tool`` registry. Test-isolation helper.

    Decoration pushes into a module-level list that persists for the
    life of the process, so tests that register tools clear it
    between cases (mirroring the ``@behavior`` registry's isolation
    pattern). Runtimes constructed *before* the clear keep working —
    the registry is snapshotted at runtime construction, not read
    per-dispatch.
    """
    _TOOL_REGISTRY.clear()


def get_tool_registry() -> list[Tool]:
    """Return a copy of the global ``@tool`` registry, in registration order.

    This is the list a new ``Runtime`` snapshots when ``tools=[...]``
    is not passed. It is a copy: mutating the returned list neither
    registers nor unregisters anything — use the ``@tool`` decorator
    to add and :func:`clear_tool_registry` to reset.
    """
    return list(_TOOL_REGISTRY)


def tool(
    *,
    name: Optional[str] = None,
    description: str = "",
    input_schema: Optional[type] = None,
    output_schema: Optional[type] = None,
    cost_per_call: Any = Decimal("0"),
    timeout_seconds: float = 30.0,
    deterministic: bool = False,
) -> Callable[[Callable[..., Any]], Tool]:
    """Register a function as a Tool.

    The decorated function's signature is
    `(args: input_schema, ctx: ToolContext) -> output_schema`. The
    runtime validates `args` against `input_schema` before invocation
    and validates the return value against `output_schema` after.

    Keyword-only on purpose — too many fields for safe positional
    binding.
    """

    cost = cost_per_call if isinstance(cost_per_call, Decimal) else Decimal(
        str(cost_per_call)
    )

    def wrap(fn: Callable[..., Any]) -> Tool:
        t = Tool(
            name=name or fn.__name__,
            fn=fn,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            cost_per_call=cost,
            timeout_seconds=float(timeout_seconds),
            deterministic=bool(deterministic),
        )
        _TOOL_REGISTRY.append(t)
        return t

    return wrap
