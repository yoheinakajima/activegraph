"""Registration-time handler-signature validation.

Shared by the ``@behavior`` / ``@relation_behavior`` / ``@llm_behavior``
decorators (both the global registry in
:mod:`activegraph.behaviors.decorators` and the pack-scoped variants in
:mod:`activegraph.packs`) and the ``@tool`` decorators.

Rationale (July 2026 agent-readiness review): a handler with the wrong
arity — ``def h(ctx)``, ``def h(event, ctx)`` — previously registered
fine and failed at first invocation with a ``TypeError`` swallowed into
a ``behavior.failed`` event, painful to notice during pack development.
Validating at decoration time names the mistake at the line that made
it, matching the precedent set by ``@llm_behavior``'s
``output_schema=`` strict validation (CONTRACT v1.0.3 #2).

The check is deliberately permissive:

  * Objects without an inspectable signature (builtins, some callables,
    mocks with ``(*args, **kwargs)``) pass through unvalidated — the
    goal is catching the common authoring mistake, not gatekeeping
    every callable Python can construct.
  * ``*args`` satisfies any positional arity.
  * Extra parameters beyond the contract are allowed when the call can
    still succeed: they need a default, or — for behaviors, where the
    pack loader injects typed settings by keyword
    (``packs/loader.py:_wrap_with_injection``) — a type annotation
    that marks them as injection candidates.
"""

from __future__ import annotations

import inspect
from typing import Any


def validate_handler_signature(
    fn: Any,
    *,
    expected_params: tuple[str, ...],
    decorator: str,
    allow_annotated_extras: bool,
) -> None:
    """Raise ``TypeError`` when ``fn`` cannot be invoked with the
    decorator's positional calling convention.

    ``expected_params`` names the contract's positional parameters
    (documentation for the error message; only arity is checked, not
    names). ``decorator`` is the user-facing decorator name for the
    message (e.g. ``"@tool"``). ``allow_annotated_extras=True`` treats
    a type annotation on an extra parameter as evidence it will be
    keyword-injected at call time (the pack settings pattern
    ``*, settings: MyPackSettings``); ``False`` (tools) requires extras
    to carry defaults, because tools are always invoked as exactly
    ``fn(args, ctx)``.

    Callables whose signature cannot be inspected are skipped, and
    ``*args`` / ``**kwargs`` satisfy the positional / extra checks
    respectively — the validation is a loud guard for the common
    mistake, not an exhaustive gate.
    """
    if not callable(fn):
        raise TypeError(
            f"{decorator} must decorate a callable, got "
            f"{type(fn).__name__}."
        )
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return

    params = list(sig.parameters.values())
    has_var_positional = any(
        p.kind is inspect.Parameter.VAR_POSITIONAL for p in params
    )
    positional = [
        p
        for p in params
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    n_expected = len(expected_params)
    expected_sig = f"({', '.join(expected_params)})"

    if len(positional) < n_expected and not has_var_positional:
        found = (
            f"({', '.join(p.name for p in positional)})"
            if positional
            else "()"
        )
        raise TypeError(
            f"{decorator} function {_name_of(fn)!r} must accept "
            f"{n_expected} positional parameters {expected_sig}, but its "
            f"signature is {found}.\n"
            f"\n"
            f"The runtime invokes it as fn{expected_sig}. "
            f"{_extra_hint(decorator)}\n"
            f"\n"
            f"Example:\n"
            f"    {decorator}(...)\n"
            f"    def {_example_name(fn)}{expected_sig}: ..."
        )

    problems: list[str] = []
    for p in positional[n_expected:]:
        if _extra_is_satisfiable(p, allow_annotated_extras):
            continue
        problems.append(p.name)
    for p in params:
        if p.kind is not inspect.Parameter.KEYWORD_ONLY:
            continue
        if _extra_is_satisfiable(p, allow_annotated_extras):
            continue
        problems.append(p.name)

    if problems:
        if allow_annotated_extras:
            fix = (
                "give it a default value, or annotate it with your "
                "pack's settings class so the pack loader injects it "
                "(e.g. `*, settings: MyPackSettings`)"
            )
        else:
            fix = "give it a default value or remove it"
        raise TypeError(
            f"{decorator} function {_name_of(fn)!r} has required "
            f"parameter(s) beyond the {expected_sig} contract that the "
            f"runtime will never pass: {', '.join(problems)}.\n"
            f"\n"
            f"The runtime invokes it as fn{expected_sig}, so every "
            f"extra parameter must be optional at call time — {fix}."
        )


def _extra_is_satisfiable(
    p: inspect.Parameter, allow_annotated_extras: bool
) -> bool:
    if p.default is not inspect.Parameter.empty:
        return True
    if allow_annotated_extras and p.annotation is not inspect.Parameter.empty:
        return True
    return False


def _name_of(fn: Any) -> str:
    return getattr(fn, "__name__", repr(fn))


def _example_name(fn: Any) -> str:
    name = getattr(fn, "__name__", "my_handler")
    return name if name.isidentifier() else "my_handler"


def _extra_hint(decorator: str) -> str:
    if decorator == "@tool":
        return (
            "`args` is the validated input model (or a plain dict when "
            "no input_schema is set) and `ctx` is the ToolContext."
        )
    return "See the behavior-handler contract in docs/concepts/behaviors."
