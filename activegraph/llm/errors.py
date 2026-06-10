"""LLM-side errors. v1.0 PR-D — migrated to ExecutionError.

Two surface types in the LLM layer:

- :class:`MissingProviderError` — raised at registration time when an
  ``@llm_behavior`` is invoked but no provider is wired. Stays a
  ``RuntimeError`` subclass through PR-D; PR-E (RegistrationError)
  re-parents it.

- :class:`LLMBehaviorError` — structured failure from inside an
  ``@llm_behavior`` wrapper. Carries a ``reason`` code from CONTRACT
  v0.6 #11 plus a free-form ``message``. The runtime's ``_invoke``
  catch reads ``reason`` and ``payload_extras`` off this exception and
  includes them in the emitted ``behavior.failed`` event.

PR-D re-parents ``LLMBehaviorError`` under
:class:`activegraph.errors.ExecutionError` so it joins the v1.0
hierarchy and produces a structured-format message when rendered to a
user. The ``(reason, message, payload_extras)`` constructor signature
is preserved so the ~8 internal raise sites in providers do not
change. The structured fields are auto-derived from ``reason`` via a
per-reason prose table — same pattern as PR-B's
``_KEYWORD_WORKAROUNDS``.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.errors import ExecutionError, RegistrationError


# Per-reason prose for LLMBehaviorError. The voice principle from
# CONTRACT v1.0 #3: explain the invariant being protected, not the
# mechanism of the check. Each entry produces what_failed / why /
# how_to_fix triples that the constructor uses to build the
# structured-format body.
#
# `message` from the call site is interpolated into what_failed; `why`
# and `how_to_fix` are reason-specific and stable across instances.


def _llm_prose_parse_error(message: str) -> tuple[str, str, str]:
    return (
        f"The LLM provider returned a response that the framework could not "
        f"parse as JSON:\n  {message}",
        "LLM behaviors with a structured `output_schema` expect the model to "
        "return JSON that matches the schema; the framework parses the "
        "response and constructs typed objects from it. A response that "
        "isn't valid JSON breaks the contract that downstream behaviors "
        "depend on — they receive typed objects, not raw strings — so the "
        "framework fails the call rather than guess at structure.",
        "If the provider is real, the model's response is non-deterministic; "
        "try raising the prompt's emphasis on JSON-only output, or lowering "
        "temperature. If the provider is a fixture (RecordedLLMProvider), "
        "the recorded response is malformed — re-record from a clean run.\n"
        "\n"
        "The full response is in the `behavior.failed` event's `payload_extras`; "
        "inspect it with:\n"
        "    activegraph inspect <store> --event <behavior.failed-id>",
    )


def _llm_prose_schema_violation(message: str) -> tuple[str, str, str]:
    return (
        f"The LLM provider returned valid JSON, but the JSON did not match "
        f"the behavior's declared `output_schema`:\n  {message}",
        "Pydantic validates every LLM response against the schema declared on "
        "`@llm_behavior(output_schema=...)`. Schema-violating responses are "
        "refused at the boundary so downstream behaviors receive only objects "
        "that obey the schema — replay determinism depends on this.",
        "Check whether the schema's required fields match what the model "
        "actually produces. Common causes: a required field is missing in "
        "the response, an enum value is out-of-range, or a list field "
        "contains items of the wrong type. Add the missing fields to the "
        "prompt's example output, or relax the schema (e.g. `Optional[X]`) "
        "if the field genuinely can be absent.",
    )


def _llm_prose_fixture_missing(message: str) -> tuple[str, str, str]:
    return (
        f"The RecordedLLMProvider has no fixture for this prompt:\n  {message}",
        "RecordedLLMProvider replays a directory of recorded LLM responses keyed "
        "by prompt content hash. A missing fixture means the live prompt's hash "
        "doesn't match any recorded response — either the prompt changed since "
        "the fixtures were recorded (a behavior edit, a template change, a "
        "tool input difference), or this is a new prompt that was never "
        "recorded.",
        "Re-record the fixture from a live run with the current prompt:\n"
        "    1. Switch to AnthropicProvider (set ANTHROPIC_API_KEY)\n"
        "    2. Run the goal once to produce live LLM responses\n"
        "    3. The provider records each response to the fixture directory\n"
        "    4. Subsequent runs against RecordedLLMProvider replay them\n"
        "\n"
        "Or diff the prompt against the recorded version to find the drift.",
    )


def _llm_prose_rate_limited(message: str) -> tuple[str, str, str]:
    return (
        f"The LLM provider rejected the request as rate-limited:\n  {message}",
        "Providers cap requests per minute / tokens per minute. When the cap "
        "is hit, the runtime makes a small bounded set of provider-call "
        "retries before emitting the terminal `behavior.failed` event. The "
        "retry attempts are recorded as `llm.responded` events with an "
        "`error` payload so operators can distinguish provider unavailability "
        "from a legitimate empty extraction.",
        "Wait until the rate-limit window resets (provider-specific — usually "
        "60 seconds), then re-run from the last good event. If this happens "
        "during fork/replay, the cache hit should normally prevent the live "
        "call — check whether the prompt hash matches the recorded one.",
    )


def _llm_prose_network_error(message: str) -> tuple[str, str, str]:
    return (
        f"The LLM provider call failed with a network error:\n  {message}",
        "The framework treats network failures as transient and retries the "
        "provider call a small bounded number of times before emitting the "
        "terminal `behavior.failed` event. Failed attempts are visible in "
        "the log as `llm.responded` events with an `error` payload, so a "
        "provider outage cannot be confused with a valid empty response.",
        "If the retry budget is exhausted, re-run the goal — fork-and-replay "
        "from the last successful event:\n"
        "    activegraph fork <run> --at-event <last-good> --record\n"
        "For systematic outages, the provider's status page is the canonical "
        "source. Switching to RecordedLLMProvider with previously-recorded "
        "fixtures lets the run complete offline.",
    )


_LLM_REASON_PROSE: dict[str, Any] = {
    "llm.parse_error": _llm_prose_parse_error,
    "llm.schema_violation": _llm_prose_schema_violation,
    "llm.fixture_missing": _llm_prose_fixture_missing,
    "llm.rate_limited": _llm_prose_rate_limited,
    "llm.network_error": _llm_prose_network_error,
}


def _llm_fallback_prose(reason: str, message: str) -> tuple[str, str, str]:
    """Default prose for a reason code the table doesn't enumerate. The
    voice still names the invariant ("the framework surfaces structured
    failures from @llm_behavior wrappers"); recovery prose is generic
    and points the operator at the trace.
    """
    return (
        f"An @llm_behavior wrapper failed with reason {reason!r}:\n  {message}",
        f"The runtime catches structured failures from @llm_behavior bodies "
        f"and merges them into the emitted `behavior.failed` event, where "
        f"downstream code can read `reason={reason!r}` and decide how to "
        f"proceed. The exception you're seeing is the underlying carrier.",
        f"Inspect the `behavior.failed` event in the trace:\n"
        f"    activegraph inspect <store> --tail 50\n"
        f"\n"
        f"The full message is preserved verbatim above; check the LLM "
        f"provider's documentation for reason {reason!r}.",
    )


class MissingProviderError(RegistrationError, RuntimeError):
    """Raised when an @llm_behavior is invoked but no LLM provider is
    wired on the Runtime.

    Fires at registration / startup, not at every invocation — the
    runtime validates the configuration once. Multi-inherits
    :class:`RuntimeError` for back-compat with user code catching the
    builtin around runtime construction.
    """

    _doc_slug = "missing-provider-error"

    def __init__(self, behavior_name: Optional[str] = None) -> None:
        self.behavior_name = behavior_name
        what = (
            f"An LLM-backed behavior ({behavior_name!r}) was registered, "
            f"but Runtime(...) was constructed without an `llm_provider=` "
            f"argument."
            if behavior_name
            else (
                "An @llm_behavior was registered, but Runtime(...) was "
                "constructed without an `llm_provider=` argument."
            )
        )
        ctx: dict[str, Any] = {}
        if behavior_name:
            ctx["behavior_name"] = behavior_name
        RegistrationError.__init__(
            self,
            "no LLM provider configured for @llm_behavior",
            what_failed=what,
            why=(
                "@llm_behavior dispatches LLM calls through the provider "
                "attached to the runtime at construction. Failing loud at "
                "registration rather than at first invocation is the v0.6 "
                "contract — silently no-op'ing the behavior would corrupt "
                "the audit trail (behaviors fire and produce events; a "
                "missing provider would produce events that claim to "
                "depend on an LLM call that never happened)."
            ),
            how_to_fix=(
                "Pass `llm_provider=` to the Runtime constructor:\n"
                "    from activegraph.llm.anthropic import AnthropicProvider\n"
                "    rt = Runtime(graph, llm_provider=AnthropicProvider())\n"
                "\n"
                "For offline replay or tests, use a recorded or scripted "
                "provider:\n"
                "    from activegraph.llm.recorded import RecordedLLMProvider\n"
                "    rt = Runtime(graph, llm_provider=RecordedLLMProvider(...))"
            ),
            context=ctx,
        )


class LLMBehaviorError(ExecutionError, Exception):
    """Structured failure from inside an @llm_behavior wrapper.

    The runtime's ``_invoke`` catch reads ``reason`` and ``payload_extras``
    off this exception and includes them in the emitted
    ``behavior.failed`` event. Other exception types fall through to
    the existing CONTRACT v0.6 #13 path unchanged.

    Constructor signature ``(reason, message, *, payload_extras=)`` is
    preserved from v0.6 so the ~8 internal raise sites in providers do
    not change. The structured-format fields are auto-derived from
    ``reason`` via the per-reason prose table above.
    """

    _doc_slug = "llm-behavior-error"

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        payload_extras: Optional[dict[str, Any]] = None,
    ) -> None:
        self.reason = reason
        self.payload_extras = dict(payload_extras or {})
        prose_fn = _LLM_REASON_PROSE.get(reason)
        if prose_fn is None:
            what_failed, why, how_to_fix = _llm_fallback_prose(reason, message)
        else:
            what_failed, why, how_to_fix = prose_fn(message)
        ExecutionError.__init__(
            self,
            f"{reason}: {message}",
            what_failed=what_failed,
            why=why,
            how_to_fix=how_to_fix,
            context={
                "reason": reason,
                "message": message,
                "payload_extras": self.payload_extras,
            },
        )
