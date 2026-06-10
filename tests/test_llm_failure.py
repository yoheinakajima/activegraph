"""Failure-mode coverage for @llm_behavior (CONTRACT v0.6 #11, #21)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from activegraph import Graph, MissingProviderError, Runtime, behavior, llm_behavior
from activegraph.llm import LLMBehaviorError, LLMMessage, LLMResponse
from decimal import Decimal

from tests._llm_helpers import Claim, ClaimList, FailingProvider, ScriptedProvider


class _FlakyThenSucceedsProvider:
    default_model = "claude-sonnet-4-5"

    def __init__(self, failures: int, reason: str = "llm.network_error"):
        self.failures = failures
        self.reason = reason
        self.call_log: list[dict] = []

    def recognizes_model(self, name: str) -> bool:
        return True

    def complete(self, **kwargs):
        self.call_log.append(kwargs)
        if len(self.call_log) <= self.failures:
            raise LLMBehaviorError(
                self.reason,
                "temporary provider failure",
                payload_extras={"retry_after_seconds": 0},
            )
        return LLMResponse(
            raw_text='{"claims":[{"text":"recovered","confidence":0.8}]}',
            parsed=ClaimList(claims=[Claim(text="recovered", confidence=0.8)]),
            input_tokens=10,
            output_tokens=5,
            cost_usd=Decimal("0.001"),
            latency_seconds=0.01,
            model=kwargs["model"],
            finish_reason="end_turn",
        )

    def estimate_cost(self, *, input_tokens, output_tokens, model):
        return Decimal("0.001")

    def count_tokens(self, *, system, messages, model):
        return 10


def _seed_doc():
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})


def test_missing_provider_raises_at_registration_time():
    @llm_behavior(name="x", on=["goal.created"], output_schema=ClaimList)
    def x(event, graph, ctx, out):
        pass

    g = Graph()
    rt = Runtime(g)  # no llm_provider
    with pytest.raises(MissingProviderError, match="x"):
        rt.run_goal("test")


def test_network_error_becomes_behavior_failed_with_reason():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    g = Graph()
    Runtime(g, llm_provider=FailingProvider(ConnectionError("boom"))).run_goal("g")
    failed = next(
        e
        for e in g.events
        if e.type == "behavior.failed" and e.payload["behavior"] == "extractor"
    )
    assert failed.payload["reason"] == "llm.network_error"


def test_transient_llm_network_error_retries_before_handler_runs():
    """#27: a transient provider failure is not treated as an empty
    extraction. The runner retries before invoking the handler or
    emitting the terminal behavior.failed event."""

    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        for claim in out.claims:
            graph.add_object(
                "claim", {"text": claim.text, "confidence": claim.confidence}
            )

    provider = _FlakyThenSucceedsProvider(failures=1)
    g = Graph()
    Runtime(
        g,
        llm_provider=provider,
        llm_retry_max_attempts=3,
        llm_retry_initial_delay_seconds=0,
    ).run_goal("g")

    assert len(provider.call_log) == 2
    assert not any(
        e.type == "behavior.failed" and e.payload["behavior"] == "extractor"
        for e in g.events
    )
    claim = next(o for o in g.all_objects() if o.type == "claim")
    assert claim.data["text"] == "recovered"

    requests = [e for e in g.events if e.type == "llm.requested"]
    responses = [e for e in g.events if e.type == "llm.responded"]
    assert len(requests) == 2
    assert len(responses) == 2
    assert responses[0].payload["error"]["reason"] == "llm.network_error"
    assert responses[0].payload["retryable"] is True
    assert responses[0].caused_by == requests[0].id
    assert responses[1].payload.get("error") is None
    assert responses[1].caused_by == requests[1].id
    assert requests[1].payload["retry_of"] == requests[0].id
    assert claim.provenance["llm_request_event_id"] == requests[1].id


def test_transient_llm_network_error_exhausts_after_max_attempts():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        graph.add_object("claim", {"text": "should not happen"})

    provider = _FlakyThenSucceedsProvider(failures=99)
    g = Graph()
    Runtime(
        g,
        llm_provider=provider,
        llm_retry_max_attempts=2,
        llm_retry_initial_delay_seconds=0,
    ).run_goal("g")

    assert len(provider.call_log) == 2
    assert not any(o.type == "claim" for o in g.all_objects())
    assert len([e for e in g.events if e.type == "llm.requested"]) == 2
    errored = [
        e for e in g.events
        if e.type == "llm.responded" and e.payload.get("error")
    ]
    assert len(errored) == 2
    failed = next(
        e
        for e in g.events
        if e.type == "behavior.failed" and e.payload["behavior"] == "extractor"
    )
    assert failed.payload["reason"] == "llm.network_error"
    assert failed.payload["attempts"] == 2
    assert failed.payload["max_attempts"] == 2
    assert failed.payload["retry_exhausted"] is True


def test_non_transient_llm_error_does_not_retry():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    provider = _FlakyThenSucceedsProvider(failures=1, reason="llm.parse_error")
    g = Graph()
    Runtime(
        g,
        llm_provider=provider,
        llm_retry_max_attempts=3,
        llm_retry_initial_delay_seconds=0,
    ).run_goal("g")

    assert len(provider.call_log) == 1
    failed = next(
        e
        for e in g.events
        if e.type == "behavior.failed" and e.payload["behavior"] == "extractor"
    )
    assert failed.payload["reason"] == "llm.parse_error"


def test_schema_violation_becomes_behavior_failed():
    _seed_doc()

    class _WrongShape(ScriptedProvider):
        pass

    # Provider returns text that isn't valid JSON / doesn't match schema.
    class _NonparseResponseProvider:
        def complete(self, *, system, messages, model, max_tokens, temperature,
                     top_p, output_schema, timeout_seconds, tools=None):
            # Force a schema_violation by raising from the provider as
            # the SDK-flavored providers (Anthropic) do.
            raise LLMBehaviorError(
                "llm.schema_violation",
                "no claims field",
                payload_extras={"raw_text": "{\"oops\": 1}", "schema": "ClaimList"},
            )

        def estimate_cost(self, *, input_tokens, output_tokens, model):
            return Decimal("0")

        def count_tokens(self, *, system, messages, model):
            return 1

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    g = Graph()
    Runtime(g, llm_provider=_NonparseResponseProvider()).run_goal("g")
    failed = next(
        e
        for e in g.events
        if e.type == "behavior.failed" and e.payload["behavior"] == "extractor"
    )
    assert failed.payload["reason"] == "llm.schema_violation"
    assert "raw_text" in failed.payload


def test_handler_exception_falls_through_as_plain_behavior_failed():
    """Non-LLM exceptions inside the handler still flow as
    behavior.failed but WITHOUT a `reason` (CONTRACT #13 unchanged)."""

    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        raise ValueError("handler bug")

    provider = ScriptedProvider(
        respond_fn=lambda m, s: ClaimList(claims=[Claim(text="x", confidence=0.5)])
    )
    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("g")
    failed = next(
        e
        for e in g.events
        if e.type == "behavior.failed" and e.payload["behavior"] == "extractor"
    )
    # Plain exception inside the handler: no reason field
    assert failed.payload.get("reason") is None
    assert failed.payload["exception_type"] == "ValueError"
    assert "handler bug" in failed.payload["message"]


def test_no_objects_created_on_failure():
    """When the handler fails, the partial mutations it made WOULD still
    land (the projector is event-by-event). What we assert here is that
    the failure path itself doesn't create spurious objects."""

    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        raise ValueError("nope")

    provider = ScriptedProvider(
        respond_fn=lambda m, s: ClaimList(claims=[Claim(text="x", confidence=0.5)])
    )
    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("g")
    assert not any(o.type == "claim" for o in g.all_objects())


def test_provider_kwargs_are_threaded_through():
    """Sanity: the provider receives the prompt assembly we expect."""

    captured: list = []

    class _Capture:
        def complete(self, *, system, messages, model, max_tokens, temperature,
                     top_p, output_schema, timeout_seconds, tools=None):
            captured.append(
                dict(
                    system_starts_with=system[:30],
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    schema_name=getattr(output_schema, "__name__", None),
                )
            )
            return LLMResponse(
                raw_text="{}",
                parsed=ClaimList(claims=[]),
                input_tokens=1,
                output_tokens=1,
                cost_usd=Decimal("0"),
                latency_seconds=0.0,
                model=model,
                finish_reason="end_turn",
            )

        def estimate_cost(self, **k):
            return Decimal("0")

        def count_tokens(self, **k):
            return 1

    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
        deterministic=True,
        max_tokens=1024,
    )
    def extractor(event, graph, ctx, out):
        pass

    g = Graph()
    Runtime(g, llm_provider=_Capture()).run_goal("g")
    [call] = captured
    assert call["model"] == "claude-sonnet-4-5"
    assert call["max_tokens"] == 1024
    assert call["temperature"] == 0.0  # determinism forces 0
    assert call["top_p"] == 1.0
    assert call["schema_name"] == "ClaimList"
    assert call["system_starts_with"].startswith('You are an active-graph')
