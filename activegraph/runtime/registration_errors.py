"""Runtime-side registration error leaves. v1.0 PR-E.

These leaves fire at registration time (or at registration-adjacent
lookup time) and are grouped here rather than scattered across
runtime.py / scheduler.py / behaviors/. Topic-module raise sites
import from here; consolidation makes the category audit trivial.

Pack registration errors live in :mod:`activegraph.packs.__init__`
(alongside the other Pack* classes for back-compat). LLM-side
:class:`MissingProviderError` and tool-side :class:`MissingToolError`
stay in their topic modules and re-parent in place.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.errors import RegistrationError


class BehaviorNotFoundError(RegistrationError, LookupError):
    """``runtime.get_behavior(name)`` could not resolve the name to a
    registered behavior.

    Multi-inherits :class:`LookupError` so user code that catches the
    builtin around behavior lookups continues to work.
    """

    _doc_slug = "behavior-not-found-error"

    def __init__(
        self,
        name: str,
        *,
        registered: Optional[tuple[str, ...]] = None,
        pack_state: bool = False,
    ) -> None:
        self.name = name
        self.registered = registered or ()
        ctx: dict[str, Any] = {"name": name, "pack_state": pack_state}
        if self.registered:
            ctx["registered"] = list(self.registered)
        sample = ""
        if self.registered:
            preview = ", ".join(repr(n) for n in list(self.registered)[:6])
            extra = f" (+{len(self.registered) - 6} more)" if len(self.registered) > 6 else ""
            sample = f"\n  registered: {preview}{extra}"
        RegistrationError.__init__(
            self,
            f"no behavior named {name!r} is loaded",
            what_failed=(
                f"runtime.get_behavior({name!r}) could not resolve the name "
                f"to a registered behavior.{sample}"
            ),
            why=(
                "Behaviors are addressable by their declared name. The "
                "lookup is strict — the runtime refuses to fall back to a "
                "fuzzy match or a no-op because a wrong-behavior dispatch "
                "would silently corrupt the audit trail. Behaviors live "
                "either in the global registry (decorated with `@behavior` "
                "or `@llm_behavior` at module load) or in a loaded pack."
            ),
            how_to_fix=(
                "Check the spelling against the registered behaviors above. "
                "If the behavior comes from a pack, confirm the pack is "
                "loaded:\n"
                "    rt.load_pack(my_pack)\n"
                "If the behavior is defined in user code, confirm the "
                "decorator ran (the module is imported) before the runtime "
                "is constructed.\n"
                "\n"
                "For canonical lookups across packs, use the fully-qualified "
                "form `'pack_name.behavior_name'`."
            ),
            context=ctx,
        )


class AmbiguousBehaviorError(RegistrationError, ValueError):
    """A short behavior name resolves to more than one loaded pack.

    Fires only when both packs declare a behavior under the same short
    name. The user is asked to disambiguate by using the canonical
    `pack_name.behavior_name` form. CONTRACT v0.9 #8.
    """

    _doc_slug = "ambiguous-behavior-error"

    def __init__(
        self,
        name: str,
        *,
        packs: Optional[tuple[str, ...]] = None,
    ) -> None:
        self.name = name
        self.packs = packs or ()
        ctx: dict[str, Any] = {"name": name}
        if self.packs:
            ctx["packs"] = list(self.packs)
        pack_list = (
            ", ".join(f"{p!r}" for p in self.packs)
            if self.packs
            else "(multiple packs)"
        )
        example = (
            f"{self.packs[0]}.{name}" if self.packs else f"pack_name.{name}"
        )
        RegistrationError.__init__(
            self,
            f"behavior name {name!r} is ambiguous across loaded packs",
            what_failed=(
                f"The short behavior name {name!r} resolves to behaviors in "
                f"more than one loaded pack: {pack_list}. The runtime cannot "
                f"pick one without an explicit choice."
            ),
            why=(
                "Pack-prefixed names are canonical; short names are a "
                "convenience for the common single-pack case (CONTRACT "
                "v0.9 #8). When multiple packs declare the same short "
                "name, the convenience would have to either pick one "
                "silently (which would change behavior on pack-load order) "
                "or pick neither — both produce surprises. The runtime "
                "refuses the lookup and asks for the canonical name."
            ),
            how_to_fix=(
                f"Use the canonical form:\n"
                f"    rt.get_behavior({example!r})\n"
                f"\n"
                f"If you wanted both packs' behaviors to fire together "
                f"under the same trigger, they are registered separately "
                f"in the runtime — both will fire on a matching event "
                f"regardless of which one your lookup names. The lookup "
                f"is for explicit by-name access, not for trigger "
                f"dispatch."
            ),
            context=ctx,
        )


class ToolNotFoundError(RegistrationError, LookupError):
    """``runtime.get_tool(name)`` could not resolve the name to a
    registered tool.

    Symmetric with :class:`BehaviorNotFoundError`. Multi-inherits
    :class:`LookupError` for back-compat.
    """

    _doc_slug = "tool-not-found-error"

    def __init__(
        self,
        name: str,
        *,
        registered: Optional[tuple[str, ...]] = None,
    ) -> None:
        self.name = name
        self.registered = registered or ()
        ctx: dict[str, Any] = {"name": name}
        if self.registered:
            ctx["registered"] = list(self.registered)
        sample = ""
        if self.registered:
            preview = ", ".join(repr(n) for n in list(self.registered)[:6])
            extra = f" (+{len(self.registered) - 6} more)" if len(self.registered) > 6 else ""
            sample = f"\n  registered: {preview}{extra}"
        RegistrationError.__init__(
            self,
            f"no tool named {name!r} is loaded",
            what_failed=(
                f"runtime.get_tool({name!r}) could not resolve the name to a "
                f"registered tool.{sample}"
            ),
            why=(
                "Tools are addressable by their declared name. The runtime "
                "refuses to fall back to a fuzzy match because the tool's "
                "input/output schema is part of the contract — invoking the "
                "wrong tool with the right name would silently produce "
                "wrong-shape data."
            ),
            how_to_fix=(
                "Check the spelling against the registered tools above. If "
                "the tool comes from a pack, confirm the pack is loaded. If "
                "the tool is in user code, confirm the @tool decorator ran "
                "(module imported) before the runtime is constructed.\n"
                "\n"
                "For canonical lookups across packs, use "
                "`'pack_name.tool_name'`."
            ),
            context=ctx,
        )


class AmbiguousToolError(RegistrationError, ValueError):
    """A short tool name resolves to more than one loaded pack.

    Raised by ``runtime.get_tool("name")`` when two loaded packs both
    contribute a tool with that short name — the canonical
    ``pack.name`` form always works and is the fix. Symmetric with
    :class:`AmbiguousBehaviorError`. CONTRACT v0.9 #9.
    """

    _doc_slug = "ambiguous-tool-error"

    def __init__(
        self,
        name: str,
        *,
        packs: Optional[tuple[str, ...]] = None,
    ) -> None:
        self.name = name
        self.packs = packs or ()
        ctx: dict[str, Any] = {"name": name}
        if self.packs:
            ctx["packs"] = list(self.packs)
        pack_list = (
            ", ".join(f"{p!r}" for p in self.packs)
            if self.packs
            else "(multiple packs)"
        )
        example = (
            f"{self.packs[0]}.{name}" if self.packs else f"pack_name.{name}"
        )
        RegistrationError.__init__(
            self,
            f"tool name {name!r} is ambiguous across loaded packs",
            what_failed=(
                f"The short tool name {name!r} resolves to tools in more "
                f"than one loaded pack: {pack_list}. The runtime cannot pick "
                f"one without an explicit choice."
            ),
            why=(
                "Same rule as behavior names (CONTRACT v0.9 #9): "
                "pack-prefixed names are canonical, short names are a "
                "convenience for the single-pack case. Multi-pack "
                "ambiguity is refused because picking one would silently "
                "swap which tool's schema validates the LLM's call."
            ),
            how_to_fix=(
                f"Use the canonical form:\n"
                f"    rt.get_tool({example!r})\n"
                f"\n"
                f"If you want the @llm_behavior to be able to choose "
                f"either pack's version, list both canonical names in "
                f"its `tools=[...]` argument."
            ),
            context=ctx,
        )


class InvalidToolRegistration(RegistrationError, TypeError):
    """A value passed to ``Runtime(tools=[...])`` is not a Tool instance.

    Common cause: the developer passed a bare function instead of one
    decorated with ``@tool``. Multi-inherits :class:`TypeError` for
    back-compat.
    """

    _doc_slug = "invalid-tool-registration"

    def __init__(self, value: Any) -> None:
        self.value = value
        type_name = type(value).__name__
        repr_short = repr(value)
        if len(repr_short) > 80:
            repr_short = repr_short[:77] + "..."
        RegistrationError.__init__(
            self,
            f"tool registration value is not a Tool instance (got {type_name})",
            what_failed=(
                f"Runtime(tools=[...]) was given a value that isn't a Tool "
                f"instance:\n  value: {repr_short}\n  type:  {type_name}"
            ),
            why=(
                "The Tool wrapper carries the tool's declared name, input "
                "schema, output schema, timeout, and deterministic flag. "
                "Registering a bare function would skip those declarations "
                "and the runtime could not validate calls into the tool — "
                "schema-violating calls would reach the body and produce "
                "wrong-shape data. The check fails fast at construction."
            ),
            how_to_fix=(
                "Decorate the function with @tool, and pass the decorated "
                "object:\n"
                "    @tool(name='my_tool', input_schema=..., output_schema=...)\n"
                "    def my_tool(args, ctx): ...\n"
                "\n"
                "    rt = Runtime(graph, tools=[my_tool])\n"
                "\n"
                "If the function was already decorated, confirm you're "
                "passing the decorator's return value (the wrapped Tool), "
                "not the original function."
            ),
            context={"type": type_name, "repr": repr_short},
        )
