"""Prompt assembler + view serializer.

CONTRACT v0.6 #6, #13, #20. This is the load-bearing module of v0.6:

  * Developers don't write prompts in user code. The runtime assembles
    every prompt from four locked sources, in this order:

        1. system     — frame goal, frame constraints, behavior role
                        description, output-schema reminder
        2. view       — serialized scoped graph view (objects + relations
                        + recent events)
        3. event      — the triggering event, serialized as JSON
        4. instruction — a single sentence derived from `creates=` and
                        `output_schema=`

  * The format of (2), the view serialization, is part of the public
    contract per decision #13. It is snapshot-tested. Changing it is a
    breaking change to the framework.

  * `AssembledPrompt.hash()` returns a stable SHA-256 over the
    canonical JSON of {model, system, messages, output_schema_name,
    temperature, max_tokens, top_p, deterministic}. This is the cache
    key used by the replay layer. Hash stability matters; tests
    snapshot it.

  * `behavior.build_prompt(event, graph)` is public for debugging
    (decision #20). A developer should be able to inspect the exact
    bytes that would have gone to the model, without making a call.

`prompt_template=` (str.format-style with {system}, {view}, {event},
{instruction}) is the only escape hatch — and it still receives the
same four runtime-assembled inputs. There is no raw string-concat
path in user code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from activegraph.core.event import Event
from activegraph.core.view import View
from activegraph.frame import Frame
from activegraph.llm.types import LLMMessage


# ---------- AssembledPrompt -------------------------------------------------


@dataclass
class AssembledPrompt:
    """A fully-assembled prompt + provider-call parameters.

    Returned by `assemble_prompt(...)` and by
    `LLMBehavior.build_prompt(event, graph)`. The runtime hashes this
    to look up a cached response before deciding to call the provider.
    """

    system: str
    messages: list[LLMMessage]
    model: str
    max_tokens: int
    temperature: float
    top_p: float
    output_schema_name: Optional[str]
    output_schema_json: Optional[dict[str, Any]]
    deterministic: bool
    # CONTRACT v1.3 #1: "native" when the runtime resolved provider-
    # native constrained decoding for this behavior; "prompt" otherwise.
    # Part of prompt identity — but only contributes to the hash when
    # native (omit-when-absent, the v1.0.3 #4 pattern), so every
    # pre-v1.3 hash stays byte-identical.
    structured_output_mode: str = "prompt"

    # Source-by-source breakdown — useful for debugging and for
    # snapshot tests that target a single section.
    sections: dict[str, str] = field(default_factory=dict)

    def to_hashable(self) -> dict[str, Any]:
        """Canonical content used for hashing. Recorded-at timestamps,
        latencies, and other run-specific data are NOT included."""

        out: dict[str, Any] = {
            "model": self.model,
            "system": self.system,
            "messages": [m.to_dict() for m in self.messages],
            "output_schema_name": self.output_schema_name,
            "output_schema_json": self.output_schema_json,
            "max_tokens": int(self.max_tokens),
            "temperature": float(self.temperature),
            "top_p": float(self.top_p),
            "deterministic": bool(self.deterministic),
        }
        if self.structured_output_mode == "native":
            out["structured_output_mode"] = "native"
        return out

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_hashable(), sort_keys=True, separators=(",", ":")
        )

    def hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


# ---------- view serialization (CONTRACT v0.6 #13 — format is locked) -------


def serialize_view(
    view: View,
    *,
    around: Optional[str] = None,
    depth: Optional[int] = None,
) -> str:
    """Render a scoped view as the prompt-ready Markdown block.

    Format is locked — snapshot-tested in `tests/test_llm_prompt.py`.
    Changing this is a breaking change to the v0.6 contract.
    """

    header_bits: list[str] = []
    if depth is not None:
        header_bits.append(f"depth={depth}")
    if around:
        header_bits.append(f"around={around}")
    header_suffix = f" ({', '.join(header_bits)})" if header_bits else ""

    lines: list[str] = [f"## Graph context{header_suffix}", ""]

    objects = view.objects()
    lines.append("### Objects")
    if not objects:
        lines.append("- (none)")
    else:
        for o in objects:
            lines.append(f"- {o.id} ({o.type}): {_canonical(o.data)}")
    lines.append("")

    relations = view.relations()
    lines.append("### Relations")
    if not relations:
        lines.append("- (none)")
    else:
        for r in relations:
            lines.append(f"- {r.source} --{r.type}--> {r.target}")
    lines.append("")

    events = view.events()
    lines.append("### Recent events")
    if not events:
        lines.append("- (none)")
    else:
        for e in events:
            summary = _event_summary(e)
            lines.append(f"- {e.id} {e.type}{summary}")

    return "\n".join(lines)


def _event_summary(e: Event) -> str:
    """One-line tail for an event in the view block.

    Stays short on purpose — the prompt should expose enough for the
    model to reason about recent activity without becoming a transcript.
    """

    p = e.payload or {}
    if e.type == "object.created":
        oid = (p.get("object") or {}).get("id", "?")
        return f" {oid}"
    if e.type == "relation.created":
        r = p.get("relation") or {}
        return f' {r.get("source", "?")} --{r.get("type", "?")}--> {r.get("target", "?")}'
    if e.type == "patch.applied":
        return f' {p.get("target", "?")}'
    if e.type == "goal.created":
        return f' "{p.get("goal", "")}"'
    return ""


def _canonical(value: Any) -> str:
    """Stable JSON for embedding inside the view block."""
    return json.dumps(value, sort_keys=True, default=str)


# ---------- system prompt ---------------------------------------------------


def build_system_prompt(
    *,
    behavior_name: str,
    description: str,
    frame: Optional[Frame],
    output_schema_name: Optional[str],
    output_schema_json: Optional[dict[str, Any]],
    structured_output_mode: str = "prompt",
) -> str:
    """The system prompt is assembled — never hand-written by the user.

    Source order: frame.goal → frame.constraints → behavior.description
    → output schema reminder. If a section is absent it is omitted; the
    section headers themselves are stable so snapshot tests are tight.
    """

    blocks: list[str] = []

    blocks.append(
        f'You are an active-graph behavior named "{behavior_name}".'
    )

    if frame is not None and frame.goal:
        blocks.append(f"Mission: {frame.goal}")

    if frame is not None and frame.constraints:
        bullets = "\n".join(f"- {c}" for c in frame.constraints)
        blocks.append(f"Constraints:\n{bullets}")

    if description:
        blocks.append(f"Role: {description}")

    if output_schema_name and structured_output_mode == "native":
        # CONTRACT v1.3 #1 #6: native constrained decoding enforces the
        # schema at generation time, so the schema dump, the example
        # instance, and the v1.0.1 #2 "Return an INSTANCE" framing (a
        # mitigation for a failure mode native mode eliminates) are
        # omitted. One stable sentence names the expected shape.
        blocks.append(
            f"Respond with JSON that matches the `{output_schema_name}` schema."
        )
    elif output_schema_name and output_schema_json is not None:
        schema_block = json.dumps(output_schema_json, indent=2, sort_keys=True)
        example = example_instance_from_schema(output_schema_json)
        example_block = json.dumps(example, indent=2, sort_keys=True)
        # v1.0.1: explicit "instance, not the schema" framing. The
        # user-test surfaced that some models echo the JSON Schema
        # definition back instead of an instance, triggering
        # llm.schema_violation. Showing an example alongside the
        # schema and naming the failure mode pulls the model toward
        # the right shape.
        blocks.append(
            f"Respond with JSON that matches the `{output_schema_name}` "
            f"schema. Return an INSTANCE that conforms to this schema, "
            f"NOT the schema itself.\n"
            f"\n"
            f"Schema:\n{schema_block}\n"
            f"\n"
            f"Example instance (the shape your response must take, with "
            f"placeholder values — replace them with real values):\n"
            f"{example_block}"
        )

    return "\n\n".join(blocks)


def example_instance_from_schema(schema: dict[str, Any]) -> Any:
    """Build a minimal example instance from a JSON Schema dict.

    Used by :func:`build_system_prompt` to render an example
    alongside the schema when ``output_schema=`` is set, so the model
    has a concrete shape to mirror rather than only the abstract
    schema (the user-test surfaced models echoing the schema back as
    their response when only the schema was shown).

    Walks the schema's ``type``, ``properties``, ``items``, ``enum``,
    ``required``, and ``$defs`` keys; produces deterministic
    placeholder values per type (``"<string>"``, ``0``, ``0.0``,
    ``true``, ``[...]``, ``{...}``, ``null``). Unrecognized shapes
    fall back to ``null``.
    """
    return _example_instance(schema, defs=schema.get("$defs") or schema.get("definitions") or {})


_PLACEHOLDER_BY_TYPE = {
    "string": "<string>",
    "integer": 0,
    "number": 0.0,
    "boolean": True,
    "null": None,
}


def _example_instance(node: Any, *, defs: dict[str, Any], depth: int = 0) -> Any:
    """Recursive worker for :func:`example_instance_from_schema`.

    Bounded recursion (``depth`` ceiling) keeps the example small for
    deeply nested schemas; the placeholder values are deterministic so
    snapshot tests over the system prompt stay stable.
    """
    if not isinstance(node, dict) or depth > 6:
        return None

    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        key = ref[len("#/$defs/"):]
        target = defs.get(key)
        if target is not None:
            return _example_instance(target, defs=defs, depth=depth + 1)

    enum_values = node.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]

    const = node.get("const")
    if const is not None:
        return const

    for variant_key in ("anyOf", "oneOf"):
        variants = node.get(variant_key)
        if isinstance(variants, list) and variants:
            non_null = [v for v in variants if isinstance(v, dict) and v.get("type") != "null"]
            choice = non_null[0] if non_null else variants[0]
            return _example_instance(choice, defs=defs, depth=depth + 1)

    schema_type = node.get("type")
    if isinstance(schema_type, list):
        non_null = [t for t in schema_type if t != "null"]
        schema_type = non_null[0] if non_null else "null"

    if schema_type == "object":
        properties = node.get("properties") or {}
        if not properties:
            return {}
        return {
            name: _example_instance(spec, defs=defs, depth=depth + 1)
            for name, spec in properties.items()
        }

    if schema_type == "array":
        items = node.get("items") or {}
        return [_example_instance(items, defs=defs, depth=depth + 1)]

    if schema_type in _PLACEHOLDER_BY_TYPE:
        return _PLACEHOLDER_BY_TYPE[schema_type]

    if "properties" in node:
        return _example_instance({"type": "object", **node}, defs=defs, depth=depth)

    return None


# ---------- user message ----------------------------------------------------


def build_user_message(
    *,
    view_block: str,
    event: Event,
    instruction: str,
) -> str:
    event_block = _serialize_event(event)
    return (
        f"{view_block}\n\n"
        f"## Triggering event\n"
        f"{event_block}\n\n"
        f"## Task\n"
        f"{instruction}"
    )


def _serialize_event(event: Event) -> str:
    # Strip volatile fields (provenance, run-specific timestamps, run_id)
    # so the prompt is content-stable across runs and forks. Without
    # this, the cache would miss on every fork because the embedded
    # object's provenance carries the parent's run_id.
    clean_payload = _strip_volatile(event.payload)
    payload_json = json.dumps(clean_payload, sort_keys=True, indent=2, default=str)
    return (
        f"- id: {event.id}\n"
        f"- type: {event.type}\n"
        f"- actor: {event.actor or '?'}\n"
        f"- payload:\n```\n{payload_json}\n```"
    )


_VOLATILE_KEYS = frozenset({"provenance", "timestamp", "run_id"})


def _strip_volatile(value: Any) -> Any:
    """Recursively drop keys whose values vary across runs/forks.

    Provenance carries `run_id` and `timestamp`; both leak into the
    embedded object payload of `object.created` events and would
    otherwise destabilize the prompt hash. Stable cache lookup
    requires content-equivalence over the parts of the prompt the
    model actually reasons about.
    """

    if isinstance(value, dict):
        return {
            k: _strip_volatile(v)
            for k, v in value.items()
            if k not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


# ---------- task instruction ------------------------------------------------


def build_instruction(
    *,
    creates: list[str],
    output_schema_name: Optional[str],
) -> str:
    """One-sentence summary of what the LLM is supposed to produce.

    Auto-derived from the decorator metadata so it cannot drift from
    the runtime's expectations. If the developer is creating multiple
    object types, list them; if there is a schema, name it.
    """

    if output_schema_name and creates:
        creates_str = ", ".join(sorted(set(creates)))
        return (
            f"Return a JSON instance of the `{output_schema_name}` schema "
            f"(NOT the schema definition itself — see the example above). "
            f"Your output will be used to create objects of type: {creates_str}."
        )
    if output_schema_name:
        return (
            f"Return a JSON instance of the `{output_schema_name}` schema "
            f"(NOT the schema definition itself — see the example above)."
        )
    if creates:
        creates_str = ", ".join(sorted(set(creates)))
        return (
            f"Describe what objects of type {creates_str} should be created "
            f"in response to this event."
        )
    return "Describe what should happen in response to this event."


# ---------- schema rendering ------------------------------------------------


def schema_to_json(schema: Optional[type]) -> Optional[dict[str, Any]]:
    """Serialize a Pydantic BaseModel class to a JSON schema dict.

    Returns None if `schema` is None. Falls back to a name-only shell
    if `schema` does not look like a Pydantic v2 model — that lets
    third-party schema systems plug in without us hard-depending on
    Pydantic at import time.
    """

    if schema is None:
        return None
    fn = getattr(schema, "model_json_schema", None)
    if callable(fn):
        return fn()
    return {"type": "object", "title": getattr(schema, "__name__", "Unknown")}


# ---------- top-level assembly ---------------------------------------------


def assemble_prompt(
    *,
    behavior_name: str,
    description: str,
    model: str,
    output_schema: Optional[type],
    creates: list[str],
    view: View,
    event: Event,
    frame: Optional[Frame],
    around: Optional[str],
    depth: Optional[int],
    max_tokens: int,
    temperature: float,
    top_p: float,
    deterministic: bool,
    prompt_template: Optional[str] = None,
    structured_output_mode: str = "prompt",
) -> AssembledPrompt:
    """Assemble a prompt for one behavior invocation.

    Returns an `AssembledPrompt` containing the system prompt, the user
    messages, and every provider-call parameter that contributes to the
    prompt-hash cache key. Pure function over its arguments — no I/O,
    no provider calls.
    """

    schema_json = schema_to_json(output_schema)
    schema_name = (
        getattr(output_schema, "__name__", None) if output_schema else None
    )

    system = build_system_prompt(
        behavior_name=behavior_name,
        description=description,
        frame=frame,
        output_schema_name=schema_name,
        output_schema_json=schema_json,
        structured_output_mode=structured_output_mode,
    )

    view_block = serialize_view(view, around=around, depth=depth)
    instruction = build_instruction(
        creates=list(creates), output_schema_name=schema_name
    )

    if prompt_template is not None:
        event_block = _serialize_event(event)
        try:
            user_text = prompt_template.format(
                system=system,
                view=view_block,
                event=event_block,
                instruction=instruction,
            )
        except KeyError as e:
            raise ValueError(
                f"prompt_template references unknown placeholder {e!r}. "
                f"Allowed: {{system}}, {{view}}, {{event}}, {{instruction}}"
            ) from e
    else:
        user_text = build_user_message(
            view_block=view_block, event=event, instruction=instruction
        )

    eff_temperature = 0.0 if deterministic else float(temperature)
    eff_top_p = 1.0 if deterministic else float(top_p)

    return AssembledPrompt(
        system=system,
        messages=[LLMMessage(role="user", content=user_text)],
        model=model,
        max_tokens=int(max_tokens),
        temperature=eff_temperature,
        top_p=eff_top_p,
        output_schema_name=schema_name,
        output_schema_json=schema_json,
        deterministic=bool(deterministic),
        structured_output_mode=structured_output_mode,
        sections={
            "system": system,
            "view": view_block,
            "event": _serialize_event(event),
            "instruction": instruction,
            "user": user_text,
        },
    )
