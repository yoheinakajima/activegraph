"""Native structured-output schema pre-flight. CONTRACT v1.3 #1 (#8).

Both shipped native modes (Anthropic ``output_config`` and OpenAI
``response_format`` with ``strict: true``) constrain generation with a
compiled grammar, and both accept only a subset of JSON Schema:
``additionalProperties`` must be ``false``, every object property must
be ``required``, and constraint keywords (``minimum``, ``maxLength``,
``pattern``, ...) are rejected. The pre-flight here is deliberately
conservative: a schema qualifies only if it already satisfies the
subset — the framework never rewrites optional fields into nullable
unions or otherwise changes what the schema *means*. The single
permitted injection is ``additionalProperties: false``, a pure
narrowing (extra keys were ignored by Pydantic validation, never
produced meaning).

A schema that does not qualify resolves the behavior to prompt mode
(CONTRACT v1.3 #1 #4): silent-but-audited fallback, never an error.
"""

from __future__ import annotations

import copy
from typing import Any, Optional


# JSON Schema keywords the native grammars accept. Anything outside
# this set (numeric/string/array constraints, pattern regexes,
# oneOf/not composition) fails the pre-flight and the behavior stays
# on the prompt-embedded path.
_ALLOWED_KEYWORDS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "definitions",
        "description",
        "enum",
        "format",
        "items",
        "properties",
        "required",
        "title",
        "type",
    }
)


def native_schema_compatible(schema: Optional[dict[str, Any]]) -> bool:
    """True when ``schema`` fits the native constrained-decoding subset.

    The checks, matching CONTRACT v1.3 #1 (#8):

    - the root is an object schema;
    - every node uses only allowlisted keywords;
    - every object node lists all of its properties as ``required``
      (optional fields mean the schema needs a semantic rewrite the
      framework refuses to do silently);
    - ``additionalProperties``, where present, is already ``false``;
    - ``$ref`` targets are internal and non-recursive.
    """
    if not isinstance(schema, dict):
        return False
    if schema.get("type") != "object" and "properties" not in schema:
        return False
    defs: dict[str, Any] = {}
    for key in ("$defs", "definitions"):
        block = schema.get(key)
        if isinstance(block, dict):
            defs.update(block)
    return _node_compatible(schema, defs=defs, ref_stack=())


def _node_compatible(
    node: Any, *, defs: dict[str, Any], ref_stack: tuple[str, ...]
) -> bool:
    if not isinstance(node, dict):
        # Booleans and other bare nodes are legal JSON Schema but not
        # part of the conservative subset.
        return False

    for key in node:
        if key not in _ALLOWED_KEYWORDS:
            return False

    ref = node.get("$ref")
    if ref is not None:
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return False  # external refs are out
        name = ref.rsplit("/", 1)[-1]
        if name in ref_stack:
            return False  # recursive schema
        target = defs.get(name)
        if target is None:
            return False
        return _node_compatible(target, defs=defs, ref_stack=ref_stack + (name,))

    props = node.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            return False
        required = node.get("required", [])
        if set(props) != set(required if isinstance(required, list) else []):
            return False  # optional fields need a rewrite; refuse silently
        ap = node.get("additionalProperties")
        if ap not in (None, False):
            return False
        for sub in props.values():
            if not _node_compatible(sub, defs=defs, ref_stack=ref_stack):
                return False

    items = node.get("items")
    if items is not None:
        if not _node_compatible(items, defs=defs, ref_stack=ref_stack):
            return False

    for comb in ("anyOf", "allOf"):
        arms = node.get(comb)
        if arms is not None:
            if not isinstance(arms, list) or not arms:
                return False
            for arm in arms:
                if not _node_compatible(arm, defs=defs, ref_stack=ref_stack):
                    return False

    for key in ("$defs", "definitions"):
        block = node.get(key)
        if block is not None:
            if not isinstance(block, dict):
                return False
            for sub in block.values():
                if not _node_compatible(sub, defs=defs, ref_stack=ref_stack):
                    return False

    return True


def inject_additional_properties_false(schema: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy ``schema`` with ``additionalProperties: false`` on every
    object node.

    The one transformation CONTRACT v1.3 #1 (#8) permits: both native
    APIs require it, and it is a pure narrowing — Pydantic validation
    ignored extra keys, so forbidding them changes what the model may
    emit, never what a conforming response means.
    """
    out = copy.deepcopy(schema)
    _inject(out)
    return out


def _inject(node: Any) -> None:
    if not isinstance(node, dict):
        return
    if node.get("type") == "object" or "properties" in node:
        node.setdefault("additionalProperties", False)
    props = node.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            _inject(sub)
    _inject(node.get("items"))
    for comb in ("anyOf", "allOf"):
        arms = node.get(comb)
        if isinstance(arms, list):
            for arm in arms:
                _inject(arm)
    for key in ("$defs", "definitions"):
        block = node.get(key)
        if isinstance(block, dict):
            for sub in block.values():
                _inject(sub)
