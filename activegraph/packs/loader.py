"""Pack loading: conflict detection, namespace prefixing, settings
injection, prompt manifest emission.

This module is the implementation of `Runtime.load_pack(...)`. The
public surface lives there; this file holds the helpers.

Loading order:
  1. Idempotency check: same (name, version) already loaded -> no-op.
  2. Version conflict check: same name, different version -> raise.
  3. Build settings instance.
  4. Pre-emptive conflict detection: scan THIS pack's contributions
     against ALREADY-loaded packs. Raise BEFORE mutating.
  5. Compute prefixed names.
  6. Wrap behavior functions with typed-settings injection.
  7. Attach schemas to the graph (object types + relation types).
  8. Record policies, tools, behaviors.
  9. Emit `pack.loaded` event.

All of step (4) must happen before any of steps (5)-(8). If a
conflict is raised mid-mutation, the runtime would be in a partial
state. The contract guarantees a failed `load_pack` leaves the
runtime unchanged (CONTRACT v0.9 #6).
"""

from __future__ import annotations

import copy
import inspect
import json
from typing import TYPE_CHECKING, Any, Callable, Optional

from pydantic import BaseModel, ValidationError

from activegraph.behaviors.base import Behavior, LLMBehavior, RelationBehavior
from activegraph.core.event import Event
from activegraph.packs import (
    EmptySettings,
    Pack,
    PackConflictError,
    PackError,
    PackSchemaViolation,
    PackSettingsMissingError,
    PackVersionConflictError,
    PendingApproval,
)
from activegraph.tools.base import Tool

if TYPE_CHECKING:
    from activegraph.runtime.runtime import Runtime


def load_pack_into_runtime(
    rt: "Runtime",
    pack: Pack,
    settings: Optional[BaseModel] = None,
) -> bool:
    """Implementation of `Runtime.load_pack`. Returns True if the pack
    was newly loaded, False if it was already loaded (idempotency).
    """
    # ---- 1. idempotency ---------------------------------------------------
    state = _ensure_pack_state(rt)
    existing = state.loaded_packs.get(pack.name)
    if existing is not None:
        if existing.version == pack.version:
            return False  # idempotent no-op
        raise PackVersionConflictError(
            f"pack {pack.name!r}: already loaded version {existing.version!r}, attempted to load version {pack.version!r}",
            what_failed=(
                f"runtime.load_pack({pack.name!r}, version={pack.version!r}) "
                f"was rejected because the runtime already holds "
                f"{pack.name!r} version {existing.version!r}."
            ),
            why=(
                "A runtime can hold at most one version of any pack. "
                "Two versions would compete for the same canonical names "
                "in the registry — `pack.behavior_name` would resolve "
                "differently depending on dispatch order, which would "
                "silently corrupt the audit trail."
            ),
            how_to_fix=(
                f"Pick one version. If you need both behaviors, the older "
                f"version's namespace can be retained under a renamed pack: "
                f"copy the pack, change its `name=` declaration, and load "
                f"both. The two versions then have distinct canonical "
                f"namespaces.\n"
                f"\n"
                f"To unload the current version and load the new one, "
                f"construct a fresh Runtime — load_pack does not support "
                f"version swapping in place."
            ),
            context={
                "pack": pack.name,
                "loaded_version": existing.version,
                "attempted_version": pack.version,
            },
        )

    # ---- 2. settings ------------------------------------------------------
    settings_obj = _build_settings(pack, settings)
    settings_obj = _apply_recorded_settings_overrides(rt, pack, settings_obj)

    # ---- 3. pre-emptive conflict detection (no mutation yet) -------------
    pack_behavior_names = {b.name for b in pack.behaviors}
    pack_tool_names = {t.name for t in pack.tools}
    pack_object_type_names = {ot.name for ot in pack.object_types}
    pack_relation_type_names = {rt_.name for rt_ in pack.relation_types}
    pack_policy_names = {p.name for p in pack.policies}

    # Canonical (prefixed) names this pack will register
    new_canonical_behaviors = {f"{pack.name}.{n}": n for n in pack_behavior_names}
    new_canonical_tools = {f"{pack.name}.{n}": n for n in pack_tool_names}
    new_canonical_policies = {f"{pack.name}.{n}": n for n in pack_policy_names}

    for canonical in new_canonical_behaviors:
        if canonical in state.behavior_owners:
            owner = state.behavior_owners[canonical]
            raise PackConflictError(
                f"behavior name conflict: {canonical!r} declared by both pack {owner!r} and pack {pack.name!r}",
                what_failed=(
                    f"runtime.load_pack({pack.name!r}) was rejected: the "
                    f"behavior name {canonical!r} is already registered by "
                    f"pack {owner!r}."
                ),
                why=(
                    "Canonical names in the runtime registry are unique "
                    "across loaded packs. Two packs claiming the same "
                    "canonical name would silently route dispatch one way "
                    "or the other depending on pack-load order; the runtime "
                    "refuses the load instead so the conflict is visible "
                    "and the operator decides which pack to keep."
                ),
                how_to_fix=(
                    f"One of three actions:\n"
                    f"  1. Don't load both packs in the same runtime — pick one.\n"
                    f"  2. Rename one pack: copy its source, change the\n"
                    f"     `Pack(name=...)` declaration, re-install, and load\n"
                    f"     under the new name. The behaviors are then under\n"
                    f"     a different canonical prefix.\n"
                    f"  3. If both behaviors should run, the second pack's\n"
                    f"     pyproject can re-export the behavior under a\n"
                    f"     different name within its declaration."
                ),
                context={
                    "kind": "behavior",
                    "canonical": canonical,
                    "owner_pack": owner,
                    "conflicting_pack": pack.name,
                },
            )
    for canonical in new_canonical_tools:
        if canonical in state.tool_owners:
            owner = state.tool_owners[canonical]
            raise PackConflictError(
                f"tool name conflict: {canonical!r} declared by both pack {owner!r} and pack {pack.name!r}",
                what_failed=(
                    f"runtime.load_pack({pack.name!r}) was rejected: the "
                    f"tool name {canonical!r} is already registered by pack "
                    f"{owner!r}."
                ),
                why=(
                    "Tool canonical names are unique across loaded packs "
                    "for the same reason behavior names are: silent "
                    "dispatch routing would let an @llm_behavior call the "
                    "wrong pack's tool, with a potentially-different "
                    "input/output schema. Refusing the load surfaces the "
                    "conflict at registration time."
                ),
                how_to_fix=(
                    f"Same as behavior conflicts (above):\n"
                    f"  1. Pick one pack.\n"
                    f"  2. Rename one pack to namespace its tools.\n"
                    f"  3. Use a separate runtime per pack if both must run."
                ),
                context={
                    "kind": "tool",
                    "canonical": canonical,
                    "owner_pack": owner,
                    "conflicting_pack": pack.name,
                },
            )
    for type_name in pack_object_type_names:
        if type_name in state.object_type_owners:
            owner = state.object_type_owners[type_name]
            raise PackConflictError(
                f"object type conflict: {type_name!r} is already provided by "
                f"pack {owner!r}; pack {pack.name!r} also declares it"
            )
    for type_name in pack_relation_type_names:
        if type_name in state.relation_type_owners:
            owner = state.relation_type_owners[type_name]
            raise PackConflictError(
                f"relation type conflict: {type_name!r} is already provided by "
                f"pack {owner!r}; pack {pack.name!r} also declares it"
            )
    for canonical in new_canonical_policies:
        if canonical in state.policy_owners:
            owner = state.policy_owners[canonical]
            raise PackConflictError(
                f"policy name conflict: {canonical!r} is already provided by "
                f"pack {owner!r}; pack {pack.name!r} also declares it"
            )

    # Detect short-name ambiguity AGAINST other packs' short names.
    # Same short name across two packs is allowed structurally — the
    # canonical names differ. But the short-name lookup table needs to
    # mark this short name as ambiguous so unqualified lookups raise.
    pre_ambiguous_behaviors = _compute_new_ambiguous_shorts(
        state.behavior_short_to_canonical, new_canonical_behaviors
    )
    pre_ambiguous_tools = _compute_new_ambiguous_shorts(
        state.tool_short_to_canonical, new_canonical_tools
    )

    # Verify globally-exported tool short names don't collide with
    # existing global tools or other packs' globally-exported tools.
    # The runtime's `tool_registry` is rebuilt on every `_ensure_registry`
    # so we check the actual sources (`_pack_tools` + global @tool
    # registry) here.
    from activegraph.tools.decorators import get_tool_registry as _global_tool_registry
    if any(getattr(t, "_export_globally", False) for t in pack.tools):
        existing_globals = {t.name for t in _global_tool_registry()}
        existing_pack_globals = {
            (getattr(t, "_short_name", None) or t.name)
            for t in rt._pack_tools
            if getattr(t, "_export_globally", False)
        }
        for t in pack.tools:
            if not getattr(t, "_export_globally", False):
                continue
            if t.name in existing_globals or t.name in existing_pack_globals:
                raise PackConflictError(
                    f"pack {pack.name!r} declares tool {t.name!r} with "
                    f"export_globally=True, but a tool by that name is "
                    f"already registered globally"
                )

    # ---- 4. mutate. From here on we MUST succeed or the runtime is
    # in a partial state. The remaining operations are all in-memory
    # dict insertions plus one event emission.

    state.loaded_packs[pack.name] = pack
    state.pack_settings[pack.name] = settings_obj

    # behaviors: wrap with typed-settings injection, rename to canonical
    canonical_to_pack_behavior: dict[str, Any] = {}
    for b in pack.behaviors:
        canonical = f"{pack.name}.{b.name}"
        wrapped = _wrap_behavior_for_pack(b, pack, settings_obj)
        # The wrapped object is a fresh Behavior/LLMBehavior/RelationBehavior
        # with `name = canonical`.
        canonical_to_pack_behavior[canonical] = wrapped
        state.behavior_owners[canonical] = pack.name
        _add_short_name(state.behavior_short_to_canonical, b.name, canonical)

    # tools: rename to canonical, hold for merge into tool_registry
    # by `_ensure_registry` (which rebuilds the registry from scratch
    # on each call).
    for t in pack.tools:
        canonical = f"{pack.name}.{t.name}"
        renamed = _rename_tool(t, canonical)
        rt._pack_tools.append(renamed)
        state.tool_owners[canonical] = pack.name
        _add_short_name(state.tool_short_to_canonical, t.name, canonical)

    # object types: attach schemas to graph for validation
    for ot in pack.object_types:
        state.object_type_owners[ot.name] = pack.name
        state.object_type_schemas[ot.name] = ot.schema
    # relation types
    for rt_ in pack.relation_types:
        state.relation_type_owners[rt_.name] = pack.name
        state.relation_type_specs[rt_.name] = rt_
    # policies
    for p in pack.policies:
        canonical = f"{pack.name}.{p.name}"
        state.policy_owners[canonical] = pack.name
        for type_name in p.requires_approval:
            state.gated_object_types.setdefault(type_name, []).append(canonical)

    # Insert behaviors into the runtime's effective registry. The
    # Runtime treats `_pack_behaviors` as an additional source merged
    # at registry-build time (see runtime._ensure_registry).
    rt._pack_behaviors.extend(canonical_to_pack_behavior.values())  # type: ignore[attr-defined]

    # Force a registry rebuild on the next event so the new behaviors
    # are picked up. Runtime's `_ensure_registry` is the single seam.
    rt.registry = None

    # If a graph is already attached, install the schema validators
    # NOW so subsequent live add_object calls are gated.
    if rt.graph is not None:
        _install_graph_validators(rt.graph, state)

    # ---- 5. emit pack.loaded event ---------------------------------------
    payload = _build_pack_loaded_payload(pack, settings_obj)
    rt.graph.emit(
        Event(
            id=rt.graph.ids.event(),
            type="pack.loaded",
            payload=payload,
            actor="runtime",
            frame_id=rt.frame.id if rt.frame else None,
            caused_by=None,
            timestamp=rt.graph.clock.now(),
        )
    )
    return True


# ---------------------------------------------------------------- state


class PackRuntimeState:
    """Per-runtime pack bookkeeping. Lives on Runtime under
    `_pack_state` (initialized lazily by `_ensure_pack_state`).
    """

    def __init__(self) -> None:
        self.loaded_packs: dict[str, Pack] = {}
        self.pack_settings: dict[str, BaseModel] = {}
        # canonical name -> pack name
        self.behavior_owners: dict[str, str] = {}
        self.tool_owners: dict[str, str] = {}
        self.policy_owners: dict[str, str] = {}
        self.object_type_owners: dict[str, str] = {}
        self.relation_type_owners: dict[str, str] = {}
        # short name -> canonical name OR sentinel "<<AMBIGUOUS>>"
        self.behavior_short_to_canonical: dict[str, str] = {}
        self.tool_short_to_canonical: dict[str, str] = {}
        # schema registry: object type name -> Pydantic class
        self.object_type_schemas: dict[str, type] = {}
        # relation type name -> RelationType (for source/target validation)
        self.relation_type_specs: dict[str, Any] = {}
        # object type -> [canonical policy names that gate it]
        self.gated_object_types: dict[str, list[str]] = {}
        # pending approvals
        self.pending_approvals: list[PendingApproval] = []
        self._next_approval_n: int = 1


def _ensure_pack_state(rt: "Runtime") -> PackRuntimeState:
    """Initialize `rt._pack_state` if not present and return it."""
    if not hasattr(rt, "_pack_state") or rt._pack_state is None:  # type: ignore[attr-defined]
        rt._pack_state = PackRuntimeState()  # type: ignore[attr-defined]
        # Also ensure `_pack_behaviors` exists for registry merging.
        if not hasattr(rt, "_pack_behaviors"):
            rt._pack_behaviors = []  # type: ignore[attr-defined]
    return rt._pack_state  # type: ignore[attr-defined]


AMBIGUOUS = "<<AMBIGUOUS>>"


def _add_short_name(table: dict[str, str], short: str, canonical: str) -> None:
    """Insert `short -> canonical` unless `short` is already mapped to
    a DIFFERENT canonical, in which case mark as ambiguous.
    """
    existing = table.get(short)
    if existing is None:
        table[short] = canonical
    elif existing != canonical and existing != AMBIGUOUS:
        table[short] = AMBIGUOUS


def _compute_new_ambiguous_shorts(
    table: dict[str, str], new_canonicals: dict[str, str]
) -> set[str]:
    out: set[str] = set()
    for canonical, short in new_canonicals.items():
        if short in table and table[short] != canonical:
            out.add(short)
    return out


# ---------------------------------------------------------------- settings


def _build_settings(pack: Pack, settings: Optional[BaseModel]) -> BaseModel:
    """Validate the user-supplied settings against pack.settings_schema."""
    if settings is None:
        try:
            return pack.settings_schema()
        except ValidationError as e:
            raise PackSettingsMissingError(
                f"pack {pack.name!r}: settings_schema {pack.settings_schema.__name__!r} "
                f"requires values but `runtime.load_pack(...)` was called without "
                f"settings=. Underlying error: {e}"
            ) from e
    if not isinstance(settings, pack.settings_schema):
        # Allow dict-shaped settings to be coerced.
        if isinstance(settings, dict):
            try:
                return pack.settings_schema(**settings)
            except ValidationError as e:
                raise PackSettingsMissingError(
                    f"pack {pack.name!r}: settings dict failed validation: {e}"
                ) from e
        raise PackSettingsMissingError(
            f"pack {pack.name!r}: settings must be a {pack.settings_schema.__name__} "
            f"instance (got {type(settings).__name__})"
        )
    return settings


def _apply_recorded_settings_overrides(
    rt: "Runtime",
    pack: Pack,
    settings_obj: BaseModel,
) -> BaseModel:
    """Apply fork-local `pack.settings_overridden` events for this pack.

    The CLI records `--set` as events so the parent prefix remains intact
    and the fork carries an auditable settings override. Validation stays
    here, at pack registration time, because this is where the settings
    schema is known.
    """
    events = getattr(getattr(rt, "graph", None), "events", [])
    matching = [
        e for e in events
        if e.type == "pack.settings_overridden"
        and (e.payload or {}).get("pack") == pack.name
    ]
    if not matching:
        return settings_obj

    merged = _canonical_settings_dump(settings_obj)
    applied_event_ids: list[str] = []
    for event in matching:
        overrides = (event.payload or {}).get("overrides")
        if not isinstance(overrides, dict):
            continue
        _merge_settings_override(
            pack.settings_schema,
            merged,
            overrides,
            pack_name=pack.name,
            event_id=event.id,
        )
        applied_event_ids.append(event.id)
    if not applied_event_ids:
        return settings_obj
    return _build_settings(pack, merged)


def _merge_settings_override(
    schema: type[BaseModel],
    target: dict[str, Any],
    overrides: dict[str, Any],
    *,
    pack_name: str,
    event_id: str,
    path: tuple[str, ...] = (),
) -> None:
    fields = getattr(schema, "model_fields", {})
    for key, value in overrides.items():
        current_path = path + (key,)
        if key not in fields:
            dotted = ".".join((pack_name, *current_path))
            raise PackSettingsMissingError(
                f"pack {pack_name!r}: unknown settings override {dotted!r} "
                f"recorded by {event_id}"
            )
        nested_schema = _field_model_schema(fields[key])
        if isinstance(value, dict) and nested_schema is not None:
            existing = target.get(key)
            if not isinstance(existing, dict):
                existing = {}
                target[key] = existing
            _merge_settings_override(
                nested_schema,
                existing,
                value,
                pack_name=pack_name,
                event_id=event_id,
                path=current_path,
            )
        else:
            target[key] = value


def _field_model_schema(field: Any) -> type[BaseModel] | None:
    annotation = getattr(field, "annotation", None)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


# ---------------------------------------------------------------- wrap behavior


def _wrap_behavior_for_pack(b: Any, pack: Pack, settings_obj: BaseModel):
    """Return a fresh Behavior/LLMBehavior/RelationBehavior whose `name`
    is canonical and whose `fn`/`handler` has typed-settings injection
    applied.

    We do NOT mutate the original Behavior (the user kept a reference
    in their pack module). Frozen pack contents stay untouched.
    """
    canonical = f"{pack.name}.{b.name}"
    if isinstance(b, LLMBehavior):
        new_handler = _wrap_with_injection(b.handler, settings_obj, pack)
        new_b = LLMBehavior(
            name=canonical,
            fn=b.fn,  # the placeholder; runtime uses handler, not fn
            on=list(b.on),
            where=dict(b.where) if b.where else None,
            view_spec=dict(b.view_spec) if b.view_spec else None,
            creates=list(b.creates),
            budget=dict(b.budget) if b.budget else None,
            priority=b.priority,
            pattern=b.pattern,
            pattern_matcher=b.pattern_matcher,
            activate_after=b.activate_after,
            handler=new_handler,
            description=_resolve_description(b, pack),
            model=b.model,
            output_schema=b.output_schema,
            deterministic=b.deterministic,
            max_tokens=b.max_tokens,
            temperature=b.temperature,
            top_p=b.top_p,
            timeout_seconds=b.timeout_seconds,
            prompt_template=_resolve_prompt_template(b, pack),
            tools=_resolve_pack_tool_refs(b.tools, pack),
            max_tool_turns=b.max_tool_turns,
        )
        new_b._pack_local = True  # type: ignore[attr-defined]
        new_b._pack_owner = pack.name  # type: ignore[attr-defined]
        new_b._short_name = b.name  # type: ignore[attr-defined]
        return new_b
    if isinstance(b, RelationBehavior):
        new_fn = _wrap_with_injection(b.fn, settings_obj, pack, kind="relation")
        new_b = RelationBehavior(
            name=canonical,
            fn=new_fn,
            relation_type=b.relation_type,
            on=list(b.on),
            where=dict(b.where) if b.where else None,
            view_spec=dict(b.view_spec) if b.view_spec else None,
            creates=list(b.creates),
            budget=dict(b.budget) if b.budget else None,
            priority=b.priority,
            pattern=b.pattern,
            pattern_matcher=b.pattern_matcher,
            activate_after=b.activate_after,
        )
        new_b._pack_local = True  # type: ignore[attr-defined]
        new_b._pack_owner = pack.name  # type: ignore[attr-defined]
        new_b._short_name = b.name  # type: ignore[attr-defined]
        return new_b
    # plain Behavior
    new_fn = _wrap_with_injection(b.fn, settings_obj, pack)
    new_b = Behavior(
        name=canonical,
        fn=new_fn,
        on=list(b.on),
        where=dict(b.where) if b.where else None,
        view_spec=dict(b.view_spec) if b.view_spec else None,
        creates=list(b.creates),
        budget=dict(b.budget) if b.budget else None,
        priority=b.priority,
        pattern=b.pattern,
        pattern_matcher=b.pattern_matcher,
        activate_after=b.activate_after,
    )
    new_b._pack_local = True  # type: ignore[attr-defined]
    new_b._pack_owner = pack.name  # type: ignore[attr-defined]
    new_b._short_name = b.name  # type: ignore[attr-defined]
    return new_b


def _resolve_prompt_template(b: LLMBehavior, pack: Pack) -> Optional[str]:
    """Pack prompts are NOT used as `prompt_template` because markdown
    prompts commonly contain literal `{...}` patterns (JSON sketches,
    inline code) that would crash `str.format`. Pack prompts augment
    the behavior's `description` via `_resolve_description` below.

    This function preserves the developer's explicit prompt_template=
    on the @llm_behavior if they set one — that's their intent and
    the framework respects it.
    """
    return b.prompt_template


def _resolve_description(b: LLMBehavior, pack: Pack) -> str:
    """Compose the behavior's effective description from the decorator's
    `description=` plus the pack's same-named prompt body. The result
    goes into the system prompt under "Role:". The view + event are
    auto-injected into the USER message by the runtime's normal
    prompt assembly path (so the LLM still gets the live graph
    context without us mangling markdown through str.format).
    """
    parts: list[str] = []
    if b.description:
        parts.append(b.description.strip())
    for p in pack.prompts:
        if p.name == b.name:
            parts.append(p.body.strip())
            break
    return "\n\n".join(parts)


def _resolve_pack_tool_refs(tool_refs: list, pack: Pack) -> list:
    """Rename pack-local tool references on this behavior to their
    canonical form. Strings stay as-is — name resolution happens in
    Runtime._ensure_registry via the short-name table.
    """
    out = []
    for t in tool_refs:
        if isinstance(t, Tool) and getattr(t, "_pack_local", False):
            # Try to find the matching pack tool and use its canonical
            # name. We can't actually rename the Tool object here (the
            # canonical version will live in rt.tool_registry); the
            # behavior should reference by name, so substitute.
            short = t.name
            out.append(f"{pack.name}.{short}")
        else:
            out.append(t)
    return out


def _rename_tool(t: Tool, canonical: str) -> Tool:
    """Return a copy of the tool with name=canonical."""
    new_t = Tool(
        name=canonical,
        fn=t.fn,
        description=t.description,
        input_schema=t.input_schema,
        output_schema=t.output_schema,
        cost_per_call=t.cost_per_call,
        timeout_seconds=t.timeout_seconds,
        deterministic=t.deterministic,
    )
    new_t._pack_local = True  # type: ignore[attr-defined]
    new_t._short_name = t.name  # type: ignore[attr-defined]
    new_t._export_globally = getattr(t, "_export_globally", False)  # type: ignore[attr-defined]
    return new_t


def _wrap_with_injection(
    fn: Optional[Callable],
    settings_obj: BaseModel,
    pack: Pack,
    *,
    kind: str = "default",
) -> Optional[Callable]:
    """Wrap `fn` so that any extra parameter whose type annotation
    matches `type(settings_obj)` is passed by keyword at call time.

    The standard signatures are:
      - behavior:          (event, graph, ctx, **extras)
      - llm_behavior:      (event, graph, ctx, out, **extras)
      - relation_behavior: (relation, event, graph, ctx, **extras)

    Uses `typing.get_type_hints` (not raw `param.annotation`) so that
    PEP 563 / `from __future__ import annotations` string annotations
    are resolved to the actual classes.
    """
    import typing
    if fn is None:
        return None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn
    # Resolve string annotations to actual classes. `get_type_hints`
    # works when the type is visible in the function's module globals;
    # for tests with locally-scoped classes (inside a test function)
    # we fall back to a name-match against the settings class.
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    settings_cls = type(settings_obj)
    settings_cls_name = settings_cls.__name__
    inject_kwargs: dict[str, BaseModel] = {}
    standard_params = (
        {"relation", "event", "graph", "ctx"} if kind == "relation"
        else {"event", "graph", "ctx", "out"}
    )
    for pname, param in sig.parameters.items():
        if pname in standard_params:
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        ann = hints.get(pname, param.annotation)
        if ann is inspect.Parameter.empty:
            continue
        # Resolved-class match (the canonical path).
        if isinstance(ann, type) and issubclass(ann, BaseModel) and ann is settings_cls:
            inject_kwargs[pname] = settings_obj
            continue
        # String-annotation fallback for locally-scoped settings classes
        # that `get_type_hints` couldn't resolve. Safe because conflicts
        # would surface as a normal Python runtime error.
        if isinstance(ann, str) and ann == settings_cls_name:
            inject_kwargs[pname] = settings_obj
    if not inject_kwargs:
        return fn

    def wrapped(*args, **kwargs):
        merged = dict(inject_kwargs)
        merged.update(kwargs)
        return fn(*args, **merged)

    wrapped.__wrapped__ = fn  # type: ignore[attr-defined]
    wrapped.__name__ = getattr(fn, "__name__", "wrapped_pack_behavior")
    wrapped._pack_owner = pack.name  # type: ignore[attr-defined]
    return wrapped


# ---------------------------------------------------------------- graph validators


def _install_graph_validators(graph, state: PackRuntimeState) -> None:
    """Attach a schema validator hook on the graph so add_object /
    add_relation validate against loaded pack schemas.

    The hook is idempotent — calling again with the same state object
    replaces the previous validator.
    """
    graph._pack_object_validator = _make_object_validator(state)  # type: ignore[attr-defined]
    graph._pack_relation_validator = _make_relation_validator(state)  # type: ignore[attr-defined]


def _make_object_validator(state: PackRuntimeState):
    def _validate(object_type: str, data: dict) -> dict:
        schema = state.object_type_schemas.get(object_type)
        if schema is None:
            return data
        try:
            validated = schema(**data)
        except ValidationError as e:
            pack_name = state.object_type_owners.get(object_type)
            raise PackSchemaViolation.for_object(
                object_type=object_type,
                validation_error=e,
                pack_name=pack_name,
            ) from e
        return validated.model_dump()
    return _validate


def _make_relation_validator(state: PackRuntimeState):
    def _validate(relation_type: str, source_type: Optional[str], target_type: Optional[str]) -> None:
        spec = state.relation_type_specs.get(relation_type)
        if spec is None:
            return
        pack_name = state.relation_type_owners.get(relation_type)
        if spec.source_types and source_type is not None and source_type not in spec.source_types:
            raise PackSchemaViolation.for_relation_source(
                relation_type=relation_type,
                source_type=source_type,
                allowed=list(spec.source_types),
                pack_name=pack_name,
            )
        if spec.target_types and target_type is not None and target_type not in spec.target_types:
            raise PackSchemaViolation.for_relation_target(
                relation_type=relation_type,
                target_type=target_type,
                allowed=list(spec.target_types),
                pack_name=pack_name,
            )
    return _validate


# ---------------------------------------------------------------- pack.loaded payload


def _build_pack_loaded_payload(pack: Pack, settings_obj: BaseModel) -> dict:
    return {
        "name": pack.name,
        "version": pack.version,
        "description": pack.description,
        "object_types": [ot.name for ot in pack.object_types],
        "relation_types": [rt_.name for rt_ in pack.relation_types],
        "behaviors": [f"{pack.name}.{b.name}" for b in pack.behaviors],
        "tools": [f"{pack.name}.{t.name}" for t in pack.tools],
        "policies": [f"{pack.name}.{p.name}" for p in pack.policies],
        "prompts": pack.prompt_manifest(),
        "settings": _canonical_settings_dump(settings_obj),
    }


def _canonical_settings_dump(settings_obj: BaseModel) -> dict:
    """Settings dict, JSON-canonical (sorted keys, no datetime objects)."""
    raw = settings_obj.model_dump(mode="json")
    # Pass through json.loads(json.dumps(..., sort_keys=True, default=str))
    # so the result is byte-stable across runs.
    return json.loads(json.dumps(raw, sort_keys=True, default=str))
