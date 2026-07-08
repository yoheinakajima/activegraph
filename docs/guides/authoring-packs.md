# Pack Authoring Guide

A **pack** is a Python package that bundles object types, relation
types, behaviors, tools, prompts, and policies for a specific
domain. Packs are how a developer goes from "I installed activegraph"
to "I have a working diligence system in ten minutes."

This document is the canonical reference for the pack format. It is
companion reading to `examples/diligence_real_run.py` (the killer
demo / executable spec) and
[`CONTRACT.md`](https://github.com/yoheinakajima/activegraph/blob/main/CONTRACT.md)
(the locked design decisions — v0.9 introduced the pack format; v1.0
added the per-pack-error reference catalog under
[Reference: Errors](../reference/errors/pack-conflict-error.md)).
When this guide and the contract disagree, the contract wins.

---

## TL;DR

```python
# my_pack/__init__.py
from pathlib import Path
from pydantic import BaseModel, Field
from activegraph.packs import (
    Pack, ObjectType, RelationType, PackPolicy,
    behavior, llm_behavior, tool,        # pack-aware decorators
    load_prompts_from_dir,
)

class MyPackSettings(BaseModel):
    threshold: float = 0.5

class Insight(BaseModel):
    text: str
    confidence: float = Field(ge=0.0, le=1.0)

@llm_behavior(
    name="insight_extractor",
    on=["object.created"],
    where={"object.type": "document"},
    output_schema=Insight,
)
def insight_extractor(event, graph, ctx, out, *, settings: MyPackSettings):
    if out.confidence >= settings.threshold:
        graph.add_object("insight", out.model_dump())

pack = Pack(
    name="my_pack",
    version="0.1.0",
    description="Extracts insights from documents.",
    object_types=[ObjectType(name="insight", schema=Insight)],
    behaviors=[insight_extractor],
    prompts=load_prompts_from_dir(Path(__file__).parent / "prompts"),
    settings_schema=MyPackSettings,
)
```

```toml
# pyproject.toml
[project.entry-points."activegraph.packs"]
my-pack = "my_pack:pack"
```

```python
# user code
from activegraph import Runtime
from activegraph.packs import load_by_name

rt = Runtime(graph)
rt.load_pack(load_by_name("my_pack"), settings=MyPackSettings(threshold=0.8))
rt.run_goal("...")
```

That's the whole contract. The rest of this guide explains why each
piece is shaped the way it is, and the conventions third-party pack
authors are expected to follow.

---

## 1. A pack is a Python package, not a manifest

There is no `pack.yaml`. There is no `manifest.json`. There is a
Python module that exports a single `pack` symbol of type `Pack`.

Why: packs need to express real logic (behaviors, prompts, policies)
and Python is the right language for that. A declarative manifest
would shove logic into prose comments or jinja templates, which is
how every "configuration as data" framework eventually grows a
half-broken DSL. Python is the DSL.

Convention: a pack package has the layout

```
my_pack/
  pyproject.toml
  my_pack/
    __init__.py         # exports `pack`
    object_types.py     # Pydantic schemas + ObjectType list
    relation_types.py   # RelationType list (optional)
    behaviors.py        # @behavior / @llm_behavior / @relation_behavior
    tools.py            # @tool
    settings.py         # the Pydantic settings model
    prompts/
      <prompt_name>.md  # one per LLM behavior, with TOML frontmatter
    fixtures/           # recorded LLM responses + tool outputs (optional)
      __init__.py
      <fixtures>.py
    docs/
      README.md
      settings.md
      behaviors.md
      prompts.md
  tests/
    test_pack_loads.py  # smoke test
  README.md
```

The scaffolding command (`activegraph pack new <name>`) generates
this layout.

---

## 2. Pack-aware decorators: import path matters

Pack code uses **pack-aware** decorators imported from
`activegraph.packs`:

```python
from activegraph.packs import behavior, llm_behavior, relation_behavior, tool
```

These have **identical signatures** to the decorators imported from
`activegraph`. The only behavioral difference is that pack-aware
decorators do not register anything globally — they attach metadata
to the function, return a `Behavior` / `LLMBehavior` /
`RelationBehavior` / `Tool` object, and that's it.

Why: a pack module is safe to import without a runtime. Importing
the diligence pack must not put `claim_extractor` into the global
behavior registry, where it would silently fire in any
`Runtime(graph)` call regardless of whether the pack was loaded.

Pack tests can construct a pack, assert its shape, and verify it
loads cleanly without ever instantiating a runtime.

**Inside a pack, never import decorators from `activegraph` directly.**
The `tests/test_pack_loads.py` smoke test verifies this by importing
the pack and checking that `activegraph.behaviors.decorators._REGISTRY`
and `activegraph.tools.decorators._TOOL_REGISTRY` are empty.

---

## 3. The `Pack` dataclass

```python
@dataclass(frozen=True, eq=False)
class Pack:
    name: str
    version: str
    description: str = ""
    object_types: tuple[ObjectType, ...] = ()
    relation_types: tuple[RelationType, ...] = ()
    behaviors: tuple = ()
    tools: tuple = ()
    policies: tuple[PackPolicy, ...] = ()
    prompts: tuple[PackPrompt, ...] = ()
    settings_schema: type = EmptySettings
```

**Frozen**: mutation after construction raises. This forces packs to
be declarative even though they're written in Python.

**`eq=False`**: equality and hashing are based on `(name, version)`,
not on field-by-field comparison. Behaviors are dataclasses and are
not hashable; full structural equality would not work. The `(name,
version)` key is what idempotent loading and replay hinge on.

**Tuples, not lists**: tuples are hashable and signal immutability.
List arguments are converted to tuples in `__post_init__` for
convenience.

`Pack.__post_init__` validates:
  - `name` is a non-empty lowercase ASCII identifier (matches
    `^[a-z][a-z0-9_]*$`)
  - `version` is non-empty
  - object types have unique names within the pack
  - relation types have unique names within the pack
  - behavior names are unique within the pack
  - tool names are unique within the pack
  - prompts have unique names within the pack
  - `settings_schema` is a Pydantic `BaseModel` subclass

Validation failures raise `PackValidationError` at construction —
not at load.

---

## 4. Object types and relation types

A pack declares its object types with Pydantic schemas:

```python
from pydantic import BaseModel, Field
from activegraph.packs import ObjectType

class Claim(BaseModel):
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_url: str | None = None

object_types = [
    ObjectType(
        name="claim",
        schema=Claim,
        description="A factual statement with confidence.",
    ),
]
```

When the pack is loaded, `graph.add_object("claim", data=...)`
validates `data` against `Claim`. Validation errors raise
`PackSchemaViolation` (subclass of `ValueError`) and no object is
created. The exception lists the field name, the violating value,
and the constraint that failed.

**Load-order asymmetry** (v0.9 #5): validation applies only to
objects created **after** the pack loads. Objects created before
the `pack.loaded` event are not retroactively validated. The
`pack.loaded` event is part of the event log, so replay enforces
the same load order.

Relation types are simpler:

```python
from activegraph.packs import RelationType

relation_types = [
    RelationType(
        name="addresses",
        source_types=("claim",),
        target_types=("question",),
        description="A claim addresses a question.",
    ),
    RelationType(
        name="supports",
        source_types=("evidence",),
        target_types=("claim",),
    ),
]
```

`source_types` and `target_types` are tuples of object type names.
Empty (the default) means "any". Mismatches raise `PackSchemaViolation`
at `graph.add_relation` time, same as object types.

Object types and relation types declared by a pack are **global to
the runtime**, not pack-scoped. Two packs declaring object type
`claim` with different schemas raise `PackConflictError` at load time
— you cannot have two definitions of `claim` in one runtime.

---

## 5. Behaviors are namespace-prefixed

A behavior declared in a pack with `name="claim_extractor"` is
registered as `diligence.claim_extractor`. The fully-qualified form
is the **canonical** identifier:

  - the trace prints `[behavior.started] diligence.claim_extractor`
  - metrics labels read `{behavior="diligence.claim_extractor"}`
  - error messages name the prefixed form
  - `runtime.status().registered_behaviors` lists prefixed names
  - the replay manifest uses prefixed names

Lookups from user code are **lenient**. A short name resolves when
unambiguous; the load-time conflict check makes "unambiguous" a
load-time invariant:

```python
rt.get_behavior("claim_extractor")           # works when unambiguous
rt.get_behavior("diligence.claim_extractor") # always works
```

Same rule for tools (`diligence.fetch_company_docs`). LLM behaviors
with `tools=["fetch_company_docs"]` resolve the short name through
the same rule — short forms work when only one pack declares the
tool.

Why this asymmetry: the canonical form is what shows up in
operational artifacts where ambiguity is dangerous (a trace, a
metric query, an error log). User code, on the other hand, is
checked at load time, so leniency is safe — the runtime guarantees
the short name is unambiguous before any user lookup happens.

---

## 6. Tools are pack-scoped by default

```python
from activegraph.packs import tool

@tool(name="fetch_company_docs", input_schema=FetchInput, output_schema=FetchOutput)
def fetch_company_docs(args, ctx):
    ...
```

This tool is registered as `diligence.fetch_company_docs`. To opt
into the global tool namespace:

```python
@tool(name="public_helper", export_globally=True, ...)
def public_helper(args, ctx):
    ...
```

`export_globally=True` registers the tool under its short name
**also**. The pack-prefixed name is always available. This is
intended for infrastructure packs that explicitly provide tools for
other packs to use. The default is scoped so that pack tools cannot
silently collide with each other or with user-defined tools.

---

## 7. Settings: three forms, typed injection is primary

Every pack declares a `settings_schema` — a Pydantic `BaseModel`
subclass. If a pack has no configurable settings, use the shipped
`EmptySettings`:

```python
from activegraph.packs import EmptySettings, Pack

pack = Pack(..., settings_schema=EmptySettings)
```

The user provides settings at load time:

```python
rt.load_pack(pack, settings=DiligenceSettings(
    llm_model="claude-sonnet-4-5",
    confidence_threshold_for_review=0.7,
))
```

If `settings_schema` accepts construction with no arguments (all
fields default), `settings=` may be omitted. Otherwise omitting
raises `PackSettingsMissingError`.

Behaviors access settings in **one of three forms**, in order of
preference:

### Form 1: typed parameter injection (primary)

The runtime inspects the handler's signature. Parameters beyond the
standard `(event, graph, ctx)` or `(event, graph, ctx, out)` whose
type annotation matches a loaded pack's `settings_schema` are
injected by keyword:

```python
@llm_behavior(name="claim_extractor", ...)
def claim_extractor(event, graph, ctx, out, *, settings: DiligenceSettings):
    if out.confidence < settings.confidence_threshold_for_review:
        return
    ...
```

Type-checker-friendly. IDE-friendly. Refactor-safe. **Recommended
for all new in-pack behaviors.** Use keyword-only (`*,`) so the
runtime always invokes by keyword and the parameter name is clear.

### Form 2: `ctx.settings` (secondary)

`ctx.settings` returns the settings instance for the pack that owns
the currently-executing behavior. Convenient when you don't want a
type annotation:

```python
def claim_extractor(event, graph, ctx, out):
    if out.confidence < ctx.settings.confidence_threshold_for_review:
        return
```

Equivalent to Form 1 at runtime. Use when the type is obvious from
the file context.

### Form 3: `ctx.pack_settings("other_pack")` (cross-pack, rare)

```python
def my_behavior(event, graph, ctx):
    memory_settings = ctx.pack_settings("memory")
    if memory_settings is None:
        return  # memory pack not loaded
    ...
```

String-keyed. Returns `None` for unloaded packs. **Using
`ctx.pack_settings("diligence")` from inside the diligence pack is
a code smell** — use Form 1 or Form 2. This form exists for the rare
case where a behavior needs to read another pack's settings.

---

## 8. Prompts: TOML frontmatter, content-hash replay

Pack prompts live in `prompts/` inside the pack package. Each prompt
is a markdown file with TOML frontmatter between `---` delimiters:

```markdown
---
version = "1.0.0"
name = "claim_extractor"   # optional; defaults to filename without .md
---
You extract factual claims from a document.

For each claim, return:
- text (verbatim, ≤ 200 chars)
- confidence (0.0–1.0, calibrated)
- supporting evidence (verbatim quote)

Do not invent claims. If the document does not support a claim, do not return it.
```

Parsed with `tomllib` (stdlib, Python 3.11+). No external YAML
parser is used; the codebase deliberately stays YAML-free.

The frontmatter MUST include `version`. Other keys are advisory.

Load prompts with the helper:

```python
from pathlib import Path
from activegraph.packs import load_prompts_from_dir

pack = Pack(
    ...,
    prompts=load_prompts_from_dir(Path(__file__).parent / "prompts"),
)
```

`load_prompts_from_dir`:
  - scans `*.md` files in the directory
  - parses TOML frontmatter (raises `PackPromptLoadError` on malformed)
  - computes a SHA-256 hash of the body, truncated to 16 hex chars
    (`"sha256:abcd...ef01"`)
  - returns a tuple of `PackPrompt(name, version, body, content_hash)`

### The hash, not the version, is the replay contract

When the pack loads, the runtime emits a `pack.loaded` event whose
payload includes a `prompts` map: `{prompt_name: {"version": "1.0.0",
"hash": "sha256:..."}, ...}`.

On replay, the same event must be emitted with the same hashes. If
you edit a prompt and don't bump the version, replay fires
`ReplayDivergenceError` — the hash caught it. The error message
includes the declared version on both sides so an operator sees
"v1.0.0 → v1.0.0 — version unchanged, content drift," not just an
opaque hash mismatch.

Bumping the declared version is good operator practice (it shows up
in the trace and in `pack.loaded` payloads), but it is not the
source of truth for correctness. The hash is. This is by design:
humans forget; hashes don't.

### Referencing prompts from behaviors

Each `@llm_behavior` resolves its prompt by name. If the behavior is
declared in a pack and the pack has a prompt with the same `name=`,
that prompt is used as the behavior's prompt template:

```python
# prompts/claim_extractor.md   ← frontmatter version=1.0.0
@llm_behavior(name="claim_extractor", ...)
def claim_extractor(...):
    ...
```

If you need an explicit override, pass `prompt_template="..."` to
`@llm_behavior` directly. Inline templates are also content-hashed
and pinned in `pack.loaded`.

---

## 9. Policies

```python
from activegraph.packs import PackPolicy

policies = [
    PackPolicy(
        name="memo_approval",
        requires_approval=("memo",),  # object types
    ),
    PackPolicy(
        name="risk_approval",
        requires_approval=("risk",),
    ),
]
```

Loaded policies modify how `graph.add_object` behaves: objects of
the listed types are emitted as `object.proposed` (not
`object.created`) and require `rt.approve(id)` before becoming
visible in the projected graph.

Policy names are pack-scoped via the same prefixing rule:
`diligence.memo_approval`.

`DiligenceSettings.auto_approve_memos: bool = True` (default true so
the demo flows without manual intervention) lets the pack flip the
gating off. Set to `False` to see the approval flow.

---

## 10. Discovery via Python entry points

Packs register themselves under the `activegraph.packs` entry point
group:

```toml
# pyproject.toml of any pack
[project.entry-points."activegraph.packs"]
diligence = "activegraph.packs.diligence:pack"
```

The framework can enumerate installed packs:

```python
from activegraph.packs import discover, load_by_name

for entry in discover():
    print(entry.name, entry.version)
```

`pip install activegraph-my-extension` then `runtime.load_pack(
load_by_name("my-extension"))` Just Works. This is the third-party
distribution mechanism.

`discover()` is cached per process; call `clear_discovery_cache()`
to force a re-scan (useful in tests that install packages
dynamically).

---

## 11. Fixtures and reproducible demos

A pack that ships a demo should ship recorded fixtures alongside,
so the demo runs without API keys and produces byte-for-byte
identical output:

```
activegraph_my_pack/
  fixtures/
    __init__.py
    companies.py   # canned LLM responses + tool outputs
```

The convention is:

  - Fixtures live inside the pack package, NOT in the framework
    and NOT in the user's `tests/` directory.
  - A `RecordedProvider` class (matching the `LLMProvider` protocol)
    is exported from `pack.fixtures` and is used by the demo.
  - Fixture builders are pure-Python — no I/O at import time, no
    network, no sleeping.
  - The demo runs in under 30 seconds in CI.

The shipped Diligence pack does this. Look at
`activegraph/packs/diligence/fixtures/` for the reference layout.

---

## 12. Pack discovery and loading: idempotency

`runtime.load_pack(pack, settings=...)` is **idempotent on `(name,
version)`**. Calling it twice with the same `(name, version)` is a
no-op (no second `pack.loaded` event, no re-prefixing).

Loading the same `name` with a different `version` raises
`PackVersionConflictError` — install conflicts. The runtime cannot
hold two versions of the same pack.

Loading two distinct packs that conflict on object types, relation
types, behavior names, tool names, or policy names raises
`PackConflictError`. The error names both packs and the conflicting
identifier. **Conflict detection runs before any state mutation** —
a failed `load_pack` leaves the runtime unchanged.

### Disabling a pack (v1.4)

`runtime.disable_pack(name)` deregisters a loaded pack **now**: its
behaviors stop matching, its tools stop resolving, its typed schemas
stop enforcing (the types revert to untyped semantics), and a
queue-visible `pack.disabled` event records the deregistered surface
for audit and boot-time loaders. What it deliberately does not do:
touch pack-created state (history is never rewritten) or reclaim
memory (Python cannot honestly unload imported code — restart to
evict). Idempotent — a second disable returns `False`; a never-loaded
name raises `PackNotFoundError`. Re-enable by calling `load_pack`
again.

---

## 13. The `pack.loaded` event

```json
{
  "id": "evt_005",
  "type": "pack.loaded",
  "payload": {
    "name": "diligence",
    "version": "0.1.0",
    "description": "Investment diligence ...",
    "object_types": ["company", "document", "question", "claim", ...],
    "relation_types": ["supports", "contradicts", ...],
    "behaviors": ["diligence.question_generator", "diligence.researcher", ...],
    "tools": ["diligence.fetch_company_docs", ...],
    "policies": ["diligence.memo_approval", "diligence.risk_approval"],
    "prompts": {
      "question_generator": {"version": "1.0.0", "hash": "sha256:..."},
      ...
    },
    "settings": {<JSON-serialized settings>}
  }
}
```

`pack.loaded` lives in the event log. The trace renders it; the
JSONL export includes it; `activegraph inspect` surfaces it. It is
NOT suppressed from the queue — pack-aware behaviors can subscribe to
`pack.loaded` to bootstrap (the shipped Diligence pack does not,
but the option exists).

Re-loading an already-loaded pack does not emit a second
`pack.loaded`. The settings payload is canonical-JSON-serialized so
diffs between runs surface settings drift.

---

## 14. Pack scaffolding: `activegraph pack new <name>`

```sh
activegraph pack new my-pack
cd my-pack
pip install -e .
pytest                                # smoke test passes
python -c "import my_pack; print(my_pack.pack)"
```

The scaffolding command produces a package that:
  - declares `activegraph` as a dependency
  - registers itself under the `activegraph.packs` entry point
  - has empty stubs for object types, behaviors, tools, settings
  - has a `tests/test_pack_loads.py` smoke test that imports the
    pack, asserts no global registry side effects, loads it into a
    fresh runtime, and asserts the `pack.loaded` event appears

The package name (directory and Python package) is the
kebab-to-snake transformation of the pack name: `pack new
diligence-extension` produces `diligence-extension/` with internal
package `diligence_extension/`.

`activegraph pack list` enumerates every pack the framework can
discover in the current Python environment (entry-point name,
version, and dotted import path). Useful for verifying that
`pip install activegraph-extension` registered correctly before
calling `load_by_name`.

---

## 15. Trust model and packs as code

**Packs are not sandboxed.** A pack is a Python package. Installing
a pack is equivalent to installing any Python package: it can read
your files, make network calls, exec arbitrary code in your process.
Trust at install time, not at runtime.

The runtime does not enforce any pack-specific privilege
restrictions. There is no allowlist, no capability system, no
syscall filter. If you don't trust a pack's source, don't install
it. This is the same model as `pip` and as Python itself.

This decision is locked. See CONTRACT v0.9 #12.

---

## 16. Backward compatibility

The pack format is a strict addition. All v0–v0.9 tests pass
unchanged in v1.0. Global decorators behave exactly as before. The
`Graph.add_object` path is unchanged in the no-packs-loaded case.

If you have a v0.7-era custom diligence example (`examples/
diligence_with_tools.py`), it continues to work. The pack does not
replace it; the pack is a different audience (using a pre-built
system) than the example (building a custom system from primitives).

---

## 17. Where to look in the reference implementation

  - `activegraph/packs/__init__.py` — public Pack API, decorators,
    exceptions, prompt loader.
  - `activegraph/packs/loader.py` — `Runtime.load_pack` internals,
    conflict detection, namespace prefixing, settings injection.
  - `activegraph/packs/discovery.py` — entry point enumeration.
  - `activegraph/packs/scaffold.py` — `activegraph pack new`.
  - `activegraph/packs/diligence/` — the reference pack. Read this
    end-to-end before writing your own.
  - `examples/diligence_real_run.py` — the killer demo / executable
    spec for the pack format.
  - `tests/test_packs_*.py` — every property in this document is
    tested.

Implementation details may evolve. The contract in `CONTRACT.md`
v0.9 is the binding reference. This guide explains the *why*.
