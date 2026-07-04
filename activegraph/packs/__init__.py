"""Pack format. CONTRACT v0.9.

A pack is a bundle of object types, relation types, behaviors,
tools, prompts, and policies for a specific domain. This module
exposes:

- The `Pack` dataclass (frozen, equality by (name, version)).
- Pack-aware decorators: `@behavior`, `@llm_behavior`,
  `@relation_behavior`, `@tool`. Identical signatures to the
  decorators in `activegraph.*` except they DO NOT register
  globally — a pack module is safe to import without a runtime.
- `ObjectType`, `RelationType`, `PackPolicy`, `PackPrompt` —
  the value objects that go into a `Pack`.
- `EmptySettings` — Pydantic placeholder for packs with no
  configurable settings.
- `load_prompts_from_dir(path)` — helper that scans a directory
  of `.md` files with TOML frontmatter and returns a tuple of
  `PackPrompt` objects with content hashes.
- `discover()` / `load_by_name(name)` — Python entry point
  discovery (the `activegraph.packs` group).
- The pack exception hierarchy:
    PackError (root)
      PackValidationError
      PackConflictError
      PackVersionConflictError
      PackSchemaViolation
      PackSettingsMissingError
      PackPromptLoadError

The full contract lives in CONTRACT.md v0.9. The pack authoring
guide is `docs/pack_authoring.md`. The reference implementation is
`activegraph.packs.diligence`.
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

# tomllib is stdlib in Python 3.11+. CONTRACT v0.9 #23 raises the
# Python floor to 3.11 specifically so we don't need to vendor or
# depend on tomli/tomli-w.
import tomllib

try:
    from pydantic import BaseModel
except ImportError as _e:  # pragma: no cover — pydantic is a hard dep for packs
    from activegraph.errors import MissingOptionalDependency
    raise MissingOptionalDependency(
        package="pydantic",
        feature="activegraph.packs (the pack format)",
        extras="llm",
    ) from _e

from activegraph.behaviors.base import (
    Behavior,
    LLMBehavior,
    RelationBehavior,
    _llm_behavior_fn_placeholder,
)
from activegraph.tools.base import Tool


# ---------------------------------------------------------------- exceptions
#
# v1.0 PR-A re-homed PackError under `activegraph.errors.PackError` as the
# v1.0 category base.
#
# v1.0 PR-E multi-inherits the registration-time leaves with
# `RegistrationError` so `except RegistrationError` catches them
# alongside the LLM/tool/Pack-not-found leaves. The runtime-shape leaf
# (`PackSchemaViolation`) stays PackError-only and migrates in PR-G.
#
# The 28 existing `raise PackXError("message")` call sites continue
# using the legacy single-message ActiveGraphError __init__ branch
# (format-noncompliant but valid). PR-E migrates a few high-value
# sites (loader.py PackConflict / PackVersionConflict) to the
# structured form; the remainder is tracked as a v1.0-rc1 follow-on.

from activegraph.errors import (
    MissingOptionalDependency,
    PackError as PackError,  # re-export for back-compat
    RegistrationError,
)


class PackNotFoundError(RegistrationError, LookupError):
    """`activegraph.packs.load_by_name(name)` could not find an installed
    pack with that name in the entry-point registry.

    Multi-inherits :class:`LookupError` for back-compat with user code
    catching the builtin around pack discovery.
    """

    _doc_slug = "pack-not-found-error"

    def __init__(
        self,
        name: str,
        *,
        installed: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.installed = installed
        ctx: dict[str, Any] = {"name": name}
        if installed:
            ctx["installed"] = list(installed)
        installed_list = (
            ", ".join(repr(p) for p in installed)
            if installed
            else "(no packs installed)"
        )
        RegistrationError.__init__(
            self,
            f"no installed pack named {name!r}",
            what_failed=(
                f"activegraph.packs.load_by_name({name!r}) searched the "
                f"`activegraph.packs` entry-point group and found no pack "
                f"with that name.\n  installed: {installed_list}"
            ),
            why=(
                "Packs register via Python entry points so the framework "
                "can discover them without import-side-effect cost. A "
                "missing pack means either the pack isn't installed "
                "(pip install hasn't run), or the installed pack's "
                "entry-point group is wrong, or the name is a typo."
            ),
            how_to_fix=(
                "Confirm the pack is installed:\n"
                f"    pip show <pack-distribution-name>\n"
                "\n"
                "List currently-discovered packs:\n"
                "    from activegraph.packs import discover\n"
                "    [p.name for p in discover()]\n"
                "\n"
                "If the pack is present but not discovered, its "
                "pyproject.toml should declare:\n"
                "    [project.entry-points.\"activegraph.packs\"]\n"
                "    your_pack = \"your_pack_module:pack\""
            ),
            context=ctx,
        )


class PackValidationError(RegistrationError, PackError):
    """A `Pack(...)` constructor argument failed validation.

    Raised at construction time, not at load time. Covers things like
    duplicate behavior names, an invalid pack name, an unhashable
    settings_schema, etc. Multi-inherits RegistrationError (v1.0 PR-E)
    and PackError (v0.9 base).
    """

    _doc_slug = "pack-validation-error"


class PackConflictError(RegistrationError, PackError):
    """Two loaded packs conflict on a declared identifier.

    Raised at `runtime.load_pack` time. Pre-mutation: a failed
    `load_pack` call leaves the runtime exactly as it was.
    """

    _doc_slug = "pack-conflict-error"


class PackVersionConflictError(RegistrationError, PackError):
    """Same pack name loaded with two different versions.

    A runtime cannot hold two versions of the same pack. Pre-mutation,
    same as `PackConflictError`.
    """

    _doc_slug = "pack-version-conflict-error"


class PackSchemaViolation(PackError, ValueError):
    """`graph.add_object` or `graph.add_relation` data failed schema
    validation against a loaded pack's declared type.

    Runtime-shape error (fires at add_object / add_relation, not at
    pack load), so stays under PackError only — this is the lone
    runtime-shape leaf in the PackError category. Subclass of
    :class:`ValueError` so user code catching the builtin around graph
    mutations continues to work.

    v1.0 PR-G migrated to structured format. Three call sites are
    served by three factory class methods (object validation,
    relation source-type, relation target-type) so the recovery prose
    can be specific to each shape. Direct construction with the
    structured fields is also supported.
    """

    _doc_slug = "pack-schema-violation"

    @classmethod
    def for_object(
        cls,
        *,
        object_type: str,
        validation_error: Any,
        pack_name: Optional[str] = None,
    ) -> "PackSchemaViolation":
        """Object data failed the pack's declared schema validation."""
        pack_clause = (
            f" (declared by pack {pack_name!r})" if pack_name else ""
        )
        return cls(
            f"object_type {object_type!r}: schema validation failed",
            what_failed=(
                f"`graph.add_object({object_type!r}, data=...)` was rejected "
                f"because the data did not match the pack's declared schema "
                f"for {object_type!r}{pack_clause}.\n\n"
                f"Validation error:\n  {validation_error}"
            ),
            why=(
                "Packs declare object schemas to constrain what shape of data "
                "can flow into objects of that type. The runtime validates "
                "every add_object against the schema so downstream behaviors "
                "can rely on the shape — a malformed add would silently "
                "corrupt views and pattern matches that depend on the "
                "declared fields. CONTRACT v0.9 #4 / #5 (object types are "
                "declared; validation is post-load, not retroactive)."
            ),
            how_to_fix=(
                f"Adjust the data to match the schema. Common fixes:\n"
                f"  - missing required field: add it to the data dict\n"
                f"  - wrong type for a field: convert before passing\n"
                f"  - extra field rejected by a strict schema: remove it\n"
                f"\n"
                f"To see the declared schema for {object_type!r}:\n"
                f"    from activegraph.packs.diligence import pack as p\n"
                f"    next(ot for ot in p.object_types\n"
                f"         if ot.name == {object_type!r}).schema.model_json_schema()"
            ),
            context={
                "object_type": object_type,
                "pack": pack_name,
                "validation_error": str(validation_error),
            },
        )

    @classmethod
    def for_relation_source(
        cls,
        *,
        relation_type: str,
        source_type: str,
        allowed: list[str],
        pack_name: Optional[str] = None,
    ) -> "PackSchemaViolation":
        """Relation source object type isn't in the allowed list."""
        pack_clause = (
            f" (declared by pack {pack_name!r})" if pack_name else ""
        )
        return cls(
            f"relation_type {relation_type!r}: source type {source_type!r} not allowed",
            what_failed=(
                f"`graph.add_relation(source, target, {relation_type!r})` was "
                f"rejected because the source object has type {source_type!r}, "
                f"but the pack's declared relation type for {relation_type!r}"
                f"{pack_clause} only allows source types: "
                f"{', '.join(repr(t) for t in allowed)}."
            ),
            why=(
                "Relation types declare which object-type pairs they can "
                "connect. The constraint serves the same purpose as object "
                "schemas: behaviors and pattern subscriptions that follow "
                "this relation rely on the source/target types being what "
                "the pack documented. An out-of-spec relation would cause "
                "pattern matches to silently miss or misfire."
            ),
            how_to_fix=(
                f"Either:\n"
                f"  - Pass a source of an allowed type "
                f"({', '.join(repr(t) for t in allowed)}); or\n"
                f"  - Add {source_type!r} to the relation type's "
                f"`source_types=` list when declaring the pack:\n"
                f"      RelationType(\n"
                f"          name={relation_type!r},\n"
                f"          source_types=[{', '.join(repr(t) for t in [*allowed, source_type])}],\n"
                f"          target_types=[...],\n"
                f"      )\n"
                f"  - Or, if the constraint is wrong, remove `source_types=` "
                f"to accept any source type."
            ),
            context={
                "relation_type": relation_type,
                "source_type": source_type,
                "allowed_source_types": list(allowed),
                "pack": pack_name,
                "side": "source",
            },
        )

    @classmethod
    def for_relation_target(
        cls,
        *,
        relation_type: str,
        target_type: str,
        allowed: list[str],
        pack_name: Optional[str] = None,
    ) -> "PackSchemaViolation":
        """Relation target object type isn't in the allowed list."""
        pack_clause = (
            f" (declared by pack {pack_name!r})" if pack_name else ""
        )
        return cls(
            f"relation_type {relation_type!r}: target type {target_type!r} not allowed",
            what_failed=(
                f"`graph.add_relation(source, target, {relation_type!r})` was "
                f"rejected because the target object has type {target_type!r}, "
                f"but the pack's declared relation type for {relation_type!r}"
                f"{pack_clause} only allows target types: "
                f"{', '.join(repr(t) for t in allowed)}."
            ),
            why=(
                "Same rule as source-type validation: relation types "
                "constrain both endpoints so behaviors and pattern "
                "subscriptions that traverse the relation can rely on the "
                "endpoint shapes. An out-of-spec relation would corrupt "
                "pattern matches downstream."
            ),
            how_to_fix=(
                f"Either:\n"
                f"  - Pass a target of an allowed type "
                f"({', '.join(repr(t) for t in allowed)}); or\n"
                f"  - Add {target_type!r} to the relation type's "
                f"`target_types=` list in the pack declaration; or\n"
                f"  - Remove `target_types=` to accept any target type."
            ),
            context={
                "relation_type": relation_type,
                "target_type": target_type,
                "allowed_target_types": list(allowed),
                "pack": pack_name,
                "side": "target",
            },
        )


class PackSettingsMissingError(RegistrationError, PackError):
    """`runtime.load_pack(pack)` called without `settings=` for a pack
    whose `settings_schema` doesn't accept no-arg construction.

    Packs whose settings all carry defaults load bare; a schema with
    required fields makes the caller state its choices explicitly.
    The fix is always ``load_pack(pack, settings=Schema(...))`` with
    the required fields filled in.
    """

    _doc_slug = "pack-settings-missing-error"


class PackPromptLoadError(RegistrationError, PackError):
    """A prompt file is malformed, missing required frontmatter, or
    unreadable. Pack registration time.

    Prompts ship as ``.md`` files with TOML frontmatter (see
    ``load_prompts_from_dir``); this error names the file and what is
    missing so a pack author fixes the asset, not the loader. A pack
    that fails prompt loading does not half-register.
    """

    _doc_slug = "pack-prompt-load-error"


# ----------------------------------------------------- value objects (frozen)


class EmptySettings(BaseModel):  # type: ignore[misc]  # BaseModel is Any under follow_imports=skip
    """For packs with no configurable settings. The default
    ``settings_schema``.

    A Pydantic model with zero fields, so no-settings packs flow
    through the same typed-injection machinery as configurable ones
    (CONTRACT v0.9 #7) — behaviors can still ask for ``settings`` and
    get a real instance rather than a ``None`` special case.
    """


@dataclass(frozen=True)
class ObjectType:
    """A typed object the pack contributes.

    `schema` is a Pydantic `BaseModel` subclass. When the pack is
    loaded, `graph.add_object(name, data=...)` validates against it.
    Validation applies only to objects created AFTER the pack loads
    (CONTRACT v0.9 #5).
    """

    name: str
    schema: type
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise PackValidationError(f"ObjectType.name must be non-empty str, got {self.name!r}")
        if not (isinstance(self.schema, type) and issubclass(self.schema, BaseModel)):
            raise PackValidationError(
                f"ObjectType {self.name!r}: schema must be a pydantic BaseModel subclass"
            )


@dataclass(frozen=True)
class RelationType:
    """A typed relation the pack contributes.

    `source_types` and `target_types` are tuples of object type names;
    empty means "any".
    """

    name: str
    source_types: tuple[str, ...] = ()
    target_types: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise PackValidationError(f"RelationType.name must be non-empty str, got {self.name!r}")
        if isinstance(self.source_types, list):
            object.__setattr__(self, "source_types", tuple(self.source_types))
        if isinstance(self.target_types, list):
            object.__setattr__(self, "target_types", tuple(self.target_types))


@dataclass(frozen=True)
class PackPolicy:
    """A policy declared by a pack.

    `requires_approval`: tuple of object type names whose `add_object`
    is gated until `runtime.approve(...)` is called.
    """

    name: str
    requires_approval: tuple[str, ...] = ()
    auto_apply: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.requires_approval, list):
            object.__setattr__(self, "requires_approval", tuple(self.requires_approval))
        if isinstance(self.auto_apply, list):
            object.__setattr__(self, "auto_apply", tuple(self.auto_apply))


@dataclass(frozen=True)
class PackPrompt:
    """A versioned, content-hashed prompt.

    `version` is the declared human-readable version (for changelogs
    and operator messages). `content_hash` is the SHA-256 of the body
    truncated to 16 hex chars; this is the **replay contract** (the
    hash, not the version — see CONTRACT v0.9 #10).
    """

    name: str
    version: str
    body: str
    content_hash: str

    @staticmethod
    def compute_hash(body: str) -> str:
        h = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        return f"sha256:{h}"

    @classmethod
    def from_body(cls, name: str, version: str, body: str) -> "PackPrompt":
        return cls(name=name, version=version, body=body, content_hash=cls.compute_hash(body))


def load_prompts_from_dir(path: Union[str, Path]) -> tuple[PackPrompt, ...]:
    """Scan a directory of `*.md` prompt files with TOML frontmatter.

    Each file MUST start with:

        ---
        version = "1.0.0"
        name = "optional_name"   # defaults to filename without .md
        ---
        <body>

    Returns a tuple of `PackPrompt` sorted by name. Content hash is
    computed over the body (everything after the second `---` line
    and one separating newline), exactly as it will appear at runtime.

    Errors:
      - missing/malformed frontmatter -> PackPromptLoadError
      - missing required `version` field -> PackPromptLoadError
      - duplicate prompt name -> PackPromptLoadError
      - I/O failure -> PackPromptLoadError
    """
    p = Path(path)
    if not p.exists():
        raise PackPromptLoadError(f"prompts directory does not exist: {p}")
    if not p.is_dir():
        raise PackPromptLoadError(f"prompts path is not a directory: {p}")

    out: dict[str, PackPrompt] = {}
    for md_path in sorted(p.glob("*.md")):
        prompt = _load_one_prompt(md_path)
        if prompt.name in out:
            raise PackPromptLoadError(
                f"duplicate prompt name {prompt.name!r} (file: {md_path})"
            )
        out[prompt.name] = prompt
    return tuple(out.values())


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def _load_one_prompt(path: Path) -> PackPrompt:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PackPromptLoadError(f"cannot read {path}: {e}") from e
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise PackPromptLoadError(
            f"{path}: missing TOML frontmatter (file must start with '---' line)"
        )
    fm_text, body = m.group(1), m.group(2)
    try:
        fm = tomllib.loads(fm_text)
    except tomllib.TOMLDecodeError as e:
        raise PackPromptLoadError(f"{path}: frontmatter is not valid TOML: {e}") from e
    if "version" not in fm:
        raise PackPromptLoadError(f"{path}: frontmatter missing required 'version' key")
    version = str(fm["version"])
    name = str(fm.get("name") or path.stem)
    return PackPrompt.from_body(name=name, version=version, body=body)


# ----------------------------------------------------- the Pack itself


_PACK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, eq=False)
class Pack:
    """A frozen bundle of pack contents.

    Equality and hashing are by (name, version) — NOT by deep field
    comparison. Behaviors and tools are dataclasses (not hashable);
    full structural equality would not work and isn't what users
    care about. The identity that matters is "is this the same pack
    name and version" — that's what idempotent loading hinges on.
    """

    name: str
    version: str
    description: str = ""
    object_types: tuple[Any, ...] = ()
    relation_types: tuple[Any, ...] = ()
    behaviors: tuple[Any, ...] = ()
    tools: tuple[Any, ...] = ()
    policies: tuple[Any, ...] = ()
    prompts: tuple[Any, ...] = ()
    settings_schema: type = EmptySettings

    def __post_init__(self) -> None:
        # list → tuple conversion (frozen requires object.__setattr__)
        for f in ("object_types", "relation_types", "behaviors", "tools", "policies", "prompts"):
            v = getattr(self, f)
            if isinstance(v, list):
                object.__setattr__(self, f, tuple(v))

        # name shape
        if not isinstance(self.name, str) or not _PACK_NAME_RE.match(self.name):
            raise PackValidationError(
                f"Pack.name must match [a-z][a-z0-9_]*, got {self.name!r}"
            )
        if not isinstance(self.version, str) or not self.version:
            raise PackValidationError(f"Pack.version must be non-empty str, got {self.version!r}")

        # settings_schema shape
        if not (isinstance(self.settings_schema, type) and issubclass(self.settings_schema, BaseModel)):
            raise PackValidationError(
                f"Pack {self.name!r}: settings_schema must be a pydantic BaseModel subclass"
            )

        # within-pack uniqueness
        _check_unique([o.name for o in self.object_types], "object type", self.name)
        _check_unique([r.name for r in self.relation_types], "relation type", self.name)
        _check_unique([b.name for b in self.behaviors], "behavior", self.name)
        _check_unique([t.name for t in self.tools], "tool", self.name)
        _check_unique([p.name for p in self.policies], "policy", self.name)
        _check_unique([p.name for p in self.prompts], "prompt", self.name)

        # behaviors must be Behavior / LLMBehavior / RelationBehavior
        for b in self.behaviors:
            if not isinstance(b, (Behavior, RelationBehavior)):
                raise PackValidationError(
                    f"Pack {self.name!r}: behavior {b!r} is not a Behavior or RelationBehavior "
                    f"instance (decorate with @behavior / @llm_behavior / @relation_behavior "
                    f"from activegraph.packs)"
                )
            if getattr(b, "_pack_local", False) is not True:
                raise PackValidationError(
                    f"Pack {self.name!r}: behavior {b.name!r} was not declared via "
                    f"activegraph.packs decorators (looks like activegraph.behavior was used "
                    f"directly — that registers globally; import from activegraph.packs)"
                )
        for t in self.tools:
            if not isinstance(t, Tool):
                raise PackValidationError(
                    f"Pack {self.name!r}: tool {t!r} is not a Tool instance"
                )
            if getattr(t, "_pack_local", False) is not True:
                raise PackValidationError(
                    f"Pack {self.name!r}: tool {t.name!r} was not declared via "
                    f"activegraph.packs.tool (use activegraph.packs.tool, not activegraph.tool)"
                )

    # identity by (name, version) — CONTRACT v0.9 #2 / #6
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Pack):
            return NotImplemented
        return (self.name, self.version) == (other.name, other.version)

    def __hash__(self) -> int:
        return hash((self.name, self.version))

    def prompt_manifest(self) -> dict[str, dict[str, str]]:
        """The `pack.loaded` payload's `prompts` block. Maps prompt
        name to {"version", "hash"}. CONTRACT v0.9 #10.
        """
        return {
            p.name: {"version": p.version, "hash": p.content_hash}
            for p in self.prompts
        }


def _check_unique(names: list[str], kind: str, pack_name: str) -> None:
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise PackValidationError(
                f"Pack {pack_name!r}: duplicate {kind} name {n!r}"
            )
        seen.add(n)


# ----------------------------------------------------- pack-aware decorators
#
# Identical signatures to the activegraph.* decorators; the ONLY
# difference is `_REGISTRY.append(...)` is skipped — packs collect
# their behaviors explicitly via `Pack(behaviors=[...])`, so global
# registration would be a bug. Each returned Behavior / Tool object
# carries `_pack_local = True` so the Pack constructor can verify
# the right decorator was used (CONTRACT v0.9 #3).


def behavior(
    name: Optional[str] = None,
    on: Optional[list[str]] = None,
    where: Optional[dict[str, Any]] = None,
    view: Optional[dict[str, Any]] = None,
    creates: Optional[list[str]] = None,
    budget: Optional[dict[str, Any]] = None,
    priority: int = 0,
    *,
    pattern: Optional[str] = None,
    activate_after: Any = None,
) -> Callable[[Callable[..., None]], Behavior]:
    """Pack-aware `@behavior`. Does not register globally."""

    from activegraph.runtime.patterns import parse as _parse_pattern
    from activegraph.runtime.scheduler import parse_activate_after as _parse_aa

    compiled_matcher = None
    if pattern is not None:
        compiled_matcher = _parse_pattern(pattern).compile()
    delay_n: Optional[int] = None
    if activate_after is not None:
        delay_n = _parse_aa(activate_after)

    def wrap(fn: Callable[..., None]) -> Behavior:
        b = Behavior(
            name=name or fn.__name__,
            fn=fn,
            on=list(on or []),
            where=dict(where) if where else None,
            view_spec=dict(view) if view else None,
            creates=list(creates or []),
            budget=dict(budget) if budget else None,
            priority=priority,
            pattern=pattern,
            pattern_matcher=compiled_matcher,
            activate_after=delay_n,
        )
        b._pack_local = True  # type: ignore[attr-defined]
        # Attach metadata to the underlying function so packs can inspect
        # without instantiating a runtime.
        fn.__pack_meta__ = {  # type: ignore[attr-defined]
            "kind": "behavior",
            "name": b.name,
            "on": b.on,
            "where": b.where,
        }
        return b

    return wrap


def llm_behavior(
    *,
    name: Optional[str] = None,
    on: Optional[list[str]] = None,
    where: Optional[dict[str, Any]] = None,
    description: str = "",
    model: str = "claude-sonnet-4-5",
    output_schema: Optional[type] = None,
    view: Optional[dict[str, Any]] = None,
    creates: Optional[list[str]] = None,
    budget: Optional[dict[str, Any]] = None,
    deterministic: bool = False,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    top_p: float = 1.0,
    timeout_seconds: float = 60.0,
    prompt_template: Optional[str] = None,
    priority: int = 0,
    pattern: Optional[str] = None,
    activate_after: Any = None,
    tools: Optional[list[Any]] = None,
    max_tool_turns: int = 6,
) -> Callable[[Callable[..., None]], LLMBehavior]:
    """Pack-aware `@llm_behavior`. Does not register globally."""

    from activegraph.runtime.patterns import parse as _parse_pattern
    from activegraph.runtime.scheduler import parse_activate_after as _parse_aa

    compiled_matcher = None
    if pattern is not None:
        compiled_matcher = _parse_pattern(pattern).compile()
    delay_n: Optional[int] = None
    if activate_after is not None:
        delay_n = _parse_aa(activate_after)

    def wrap(fn: Callable[..., None]) -> LLMBehavior:
        b = LLMBehavior(
            name=name or fn.__name__,
            fn=_llm_behavior_fn_placeholder,
            on=list(on or []),
            where=dict(where) if where else None,
            view_spec=dict(view) if view else None,
            creates=list(creates or []),
            budget=dict(budget) if budget else None,
            priority=priority,
            handler=fn,
            description=description,
            model=model,
            output_schema=output_schema,
            deterministic=deterministic,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            timeout_seconds=timeout_seconds,
            prompt_template=prompt_template,
            pattern=pattern,
            pattern_matcher=compiled_matcher,
            activate_after=delay_n,
            tools=list(tools) if tools else [],
            max_tool_turns=max_tool_turns,
        )
        b._pack_local = True  # type: ignore[attr-defined]
        fn.__pack_meta__ = {  # type: ignore[attr-defined]
            "kind": "llm_behavior",
            "name": b.name,
            "on": b.on,
            "where": b.where,
            "output_schema": getattr(output_schema, "__name__", None),
        }
        return b

    return wrap


def relation_behavior(
    relation_type: str,
    on: Optional[list[str]] = None,
    name: Optional[str] = None,
    where: Optional[dict[str, Any]] = None,
    view: Optional[dict[str, Any]] = None,
    creates: Optional[list[str]] = None,
    budget: Optional[dict[str, Any]] = None,
    priority: int = 0,
    *,
    pattern: Optional[str] = None,
    activate_after: Any = None,
) -> Callable[[Callable[..., None]], RelationBehavior]:
    """Pack-aware `@relation_behavior`. Does not register globally."""

    from activegraph.runtime.patterns import parse as _parse_pattern
    from activegraph.runtime.scheduler import parse_activate_after as _parse_aa

    compiled_matcher = None
    if pattern is not None:
        compiled_matcher = _parse_pattern(pattern).compile()
    delay_n: Optional[int] = None
    if activate_after is not None:
        delay_n = _parse_aa(activate_after)

    def wrap(fn: Callable[..., None]) -> RelationBehavior:
        rb = RelationBehavior(
            name=name or fn.__name__,
            fn=fn,
            relation_type=relation_type,
            on=list(on or []),
            where=dict(where) if where else None,
            view_spec=dict(view) if view else None,
            creates=list(creates or []),
            budget=dict(budget) if budget else None,
            priority=priority,
            pattern=pattern,
            pattern_matcher=compiled_matcher,
            activate_after=delay_n,
        )
        rb._pack_local = True  # type: ignore[attr-defined]
        fn.__pack_meta__ = {  # type: ignore[attr-defined]
            "kind": "relation_behavior",
            "name": rb.name,
            "relation_type": relation_type,
        }
        return rb

    return wrap


def tool(
    *,
    name: Optional[str] = None,
    description: str = "",
    input_schema: Optional[type] = None,
    output_schema: Optional[type] = None,
    cost_per_call: Any = "0.0",
    timeout_seconds: float = 30.0,
    deterministic: bool = False,
    export_globally: bool = False,
) -> Callable[[Callable[..., Any]], Tool]:
    """Pack-aware `@tool`. Does not register globally.

    `export_globally=True` opts the tool into BOTH the pack-scoped
    name (`{pack}.{name}`) AND the global short name. Default is
    pack-scoped only.
    """
    from decimal import Decimal

    def wrap(fn: Callable[..., Any]) -> Tool:
        t = Tool(
            name=name or fn.__name__,
            fn=fn,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            cost_per_call=Decimal(str(cost_per_call)),
            timeout_seconds=timeout_seconds,
            deterministic=deterministic,
        )
        t._pack_local = True  # type: ignore[attr-defined]
        t._export_globally = bool(export_globally)  # type: ignore[attr-defined]
        fn.__pack_meta__ = {  # type: ignore[attr-defined]
            "kind": "tool",
            "name": t.name,
            "deterministic": deterministic,
            "export_globally": export_globally,
        }
        return t

    return wrap


# ------------------------------------------------------ discovery + loading


@dataclass(frozen=True)
class DiscoveredPack:
    """A pack discovered via Python entry points but not yet loaded.

    What ``discover()`` yields per installed distribution that
    registers the ``activegraph.packs`` entry-point group: the pack's
    ``name`` / ``version``, the entry-point string it came from, and
    the :class:`Pack` object itself. Discovery imports the pack module
    but changes no runtime state — loading is a separate, explicit
    ``runtime.load_pack`` call.
    """

    name: str
    version: str
    entry_point: str
    pack: Pack


_DISCOVERY_CACHE: Optional[tuple[DiscoveredPack, ...]] = None


def discover() -> tuple[DiscoveredPack, ...]:
    """Enumerate installed packs via the `activegraph.packs` entry
    point group. Cached per process; call `clear_discovery_cache()`
    to force a re-scan.
    """
    global _DISCOVERY_CACHE
    if _DISCOVERY_CACHE is not None:
        return _DISCOVERY_CACHE
    from importlib.metadata import entry_points

    found: list[DiscoveredPack] = []
    eps = entry_points()
    # Python 3.10+ API: entry_points() returns EntryPoints with select()
    try:
        selected = eps.select(group="activegraph.packs")
    except AttributeError:  # pragma: no cover — pre-3.10 fallback
        selected = eps.get("activegraph.packs", [])  # type: ignore[arg-type]

    for ep in selected:
        try:
            obj = ep.load()
        except Exception as e:  # pragma: no cover
            # Soft-fail discovery so a broken third-party pack doesn't
            # poison the whole framework. The pack just doesn't show up.
            import warnings
            warnings.warn(
                f"activegraph.packs: failed to load entry point {ep.name}: {e}",
                stacklevel=2,
            )
            continue
        if not isinstance(obj, Pack):
            import warnings
            warnings.warn(
                f"activegraph.packs: entry point {ep.name} resolved to "
                f"{type(obj).__name__}, not Pack — skipping",
                stacklevel=2,
            )
            continue
        found.append(
            DiscoveredPack(
                name=obj.name,
                version=obj.version,
                entry_point=ep.value,
                pack=obj,
            )
        )
    _DISCOVERY_CACHE = tuple(found)
    return _DISCOVERY_CACHE


def clear_discovery_cache() -> None:
    """Reset the cached entry-point scan.

    ``discover()`` memoizes its result for the life of the process
    (installed packages don't change under a normal run). Tests that
    install or mock packages dynamically call this between cases;
    normal usage never needs it.
    """
    global _DISCOVERY_CACHE
    _DISCOVERY_CACHE = None


def load_by_name(name: str) -> Pack:
    """Find a discovered pack by name.

    Resolves against the ``discover()`` scan of installed
    entry-point packs and returns the :class:`Pack` object — pass it
    to ``runtime.load_pack`` to actually load it. Raises
    ``PackNotFoundError`` (a ``LookupError``) naming the installed
    packs when the name doesn't resolve.
    """
    installed = tuple(entry.name for entry in discover())
    if name in installed:
        for entry in discover():
            if entry.name == name:
                return entry.pack
    raise PackNotFoundError(name, installed=installed)


# ----------------------------------------------------- approval primitives
#
# v0.9 ships a minimal approval surface so the diligence pack's
# memo_approval / risk_approval policies have something to gate on.
# A pending approval is a value object held in the runtime; user
# code (or a CLI subcommand) calls runtime.approve(id).


@dataclass(frozen=True)
class PendingApproval:
    """An object creation that's gated behind a policy approval.

    The `id` is unique within the runtime instance and is reused as
    the eventual object id once approved. `kind` is "object" in
    v0.9; the field exists so v1.0 can extend it to relations or
    patches without breaking the API.
    """

    id: str
    kind: str
    object_type: str
    data: dict[str, Any]
    reason: str
    pack: str  # the pack whose policy gated this


__all__ = [
    "Pack",
    "ObjectType",
    "RelationType",
    "PackPolicy",
    "PackPrompt",
    "EmptySettings",
    "DiscoveredPack",
    "PendingApproval",
    "PackError",
    "PackValidationError",
    "PackConflictError",
    "PackVersionConflictError",
    "PackSchemaViolation",
    "PackSettingsMissingError",
    "PackPromptLoadError",
    "behavior",
    "llm_behavior",
    "relation_behavior",
    "tool",
    "load_prompts_from_dir",
    "discover",
    "load_by_name",
    "clear_discovery_cache",
]
