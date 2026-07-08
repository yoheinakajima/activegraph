"""Provider-boundary helpers shared by the shipped LLM providers.

Two concerns live here, both raised by the July 2026 agent-readiness
review (CONTRACT v1.3 #3):

**Tool-name sanitization.** Pack-scoped tools carry canonical dotted
names (``diligence.fetch_company_docs``, CONTRACT v0.9 #9). Both the
OpenAI and Anthropic APIs enforce ``^[a-zA-Z0-9_-]+$`` on tool names,
so a dotted name is a guaranteed request rejection. Canonical names
stay canonical everywhere inside the runtime — the event log, the tool
registry, ``get_tool()`` — and are rewritten only on the wire:
``.`` becomes ``__`` on the way out, and returned tool calls are
mapped back through an explicit per-request table (never a blind
string replace, so a tool legitimately named with ``__`` cannot be
mangled by the reverse mapping).

**Exception classification.** The v0.6 #11 reason taxonomy collapsed
every non-rate-limit provider failure into ``llm.network_error``,
which is in the runtime's transient-retry set — so a bad API key was
retried with exponential backoff before failing. CONTRACT v1.3 #3
splits the taxonomy: ``llm.auth_error`` (credentials/permissions) and
``llm.request_error`` (the request itself is invalid) are terminal;
``llm.network_error`` and ``llm.rate_limited`` stay transient.
Classification prefers the SDK exception's ``status_code`` attribute
(both the ``openai`` and ``anthropic`` SDKs set it) and falls back to
type-name heuristics; anything unrecognized stays
``llm.network_error`` so unknown failure shapes keep their pre-v1.3
retry behavior.
"""

from __future__ import annotations

from typing import Any, Optional


_WIRE_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def sanitize_tool_name(name: str) -> str:
    """Rewrite a canonical tool name into the providers' wire alphabet.

    ``.`` (the pack-scope separator) becomes ``__``; any other
    character outside ``[a-zA-Z0-9_-]`` becomes ``_``. Names already
    wire-safe pass through unchanged, so non-pack tools are
    byte-identical to pre-v1.3 requests.
    """
    if all(c in _WIRE_SAFE for c in name):
        return name
    out: list[str] = []
    for c in name:
        if c == ".":
            out.append("__")
        elif c in _WIRE_SAFE:
            out.append(c)
        else:
            out.append("_")
    return "".join(out)


def build_tool_name_map(tools: list[dict[str, Any]]) -> dict[str, str]:
    """Map wire-safe names back to canonical names for one request.

    Collisions (two canonical names sanitizing to the same wire name,
    e.g. ``pack.tool`` alongside a literal ``pack__tool``) raise
    ``ValueError`` — silently dispatching the wrong tool would corrupt
    the event log's causality.
    """
    mapping: dict[str, str] = {}
    for t in tools:
        original = _definition_name(t)
        wire = sanitize_tool_name(original)
        existing = mapping.get(wire)
        if existing is not None and existing != original:
            raise ValueError(
                f"tool names {existing!r} and {original!r} both sanitize "
                f"to {wire!r} on the provider wire. Rename one of them — "
                f"the provider API only accepts [a-zA-Z0-9_-] names, and "
                f"an ambiguous reverse mapping would dispatch the wrong "
                f"tool."
            )
        mapping[wire] = original
    return mapping


def restore_tool_name(name: str, name_map: Optional[dict[str, str]]) -> str:
    """Reverse-map a wire tool name to its canonical form.

    Unknown names pass through unchanged — the model can only call
    tools it was offered, so a miss means the name was already
    canonical (no sanitization happened this request).
    """
    if not name_map:
        return name
    return name_map.get(name, name)


def _definition_name(tool: dict[str, Any]) -> str:
    """Extract the name from either tool-definition shape.

    The framework shape is ``{name, description, input_schema}``; the
    OpenAI passthrough shape nests it under ``function``.
    """
    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        return str(tool["function"].get("name", ""))
    return str(tool.get("name", ""))


# ---- exception classification (CONTRACT v1.3 #3) ---------------------------


def classify_provider_exception(e: Exception) -> str:
    """Map a provider-SDK exception to a v0.6 #11 / v1.3 #3 reason code.

    Order matters: rate-limit first (it is also a 4xx), then auth,
    then other 4xx request errors, then network. The fallback for
    anything unrecognized is ``llm.network_error`` — the transient
    code — so unknown failure shapes keep their pre-v1.3 retry
    behavior rather than being silently promoted to terminal.
    """
    name = type(e).__name__.lower()
    text = str(e)
    status = getattr(e, "status_code", None)

    if "ratelimit" in name or status == 429 or "429" in text:
        return "llm.rate_limited"
    if (
        "authentication" in name
        or "permissiondenied" in name
        or status in (401, 403)
    ):
        return "llm.auth_error"
    if isinstance(status, int) and 400 <= status < 500:
        # 400/404/422/...: the request itself is invalid for this
        # provider or model. Retrying identical bytes cannot succeed.
        return "llm.request_error"
    if "badrequest" in name or "unprocessableentity" in name or "notfounderror" in name:
        return "llm.request_error"
    return "llm.network_error"
