"""Provider-boundary helpers: name sanitization + exception taxonomy.

CONTRACT v1.3 #3. Unit coverage for activegraph/llm/wire.py plus the
retry-set integration: llm.auth_error / llm.request_error are terminal
(never retried), llm.network_error / llm.rate_limited stay transient.
"""

from __future__ import annotations

import pytest

from activegraph import Graph, Runtime, behavior, llm_behavior
from activegraph.llm.wire import (
    build_tool_name_map,
    classify_provider_exception,
    restore_tool_name,
    sanitize_tool_name,
)
from tests._llm_helpers import ClaimList
from tests.test_llm_failure import _FlakyThenSucceedsProvider


# ------------------------------------------------------- sanitization


def test_sanitize_rewrites_pack_separator():
    assert sanitize_tool_name("diligence.fetch_docs") == "diligence__fetch_docs"


def test_sanitize_is_identity_for_wire_safe_names():
    for name in ("fetch_docs", "fetch-docs", "Fetch2", "a__b"):
        assert sanitize_tool_name(name) is name or sanitize_tool_name(name) == name


def test_sanitize_replaces_other_invalid_characters():
    assert sanitize_tool_name("a b/c") == "a_b_c"


def test_name_map_round_trips_canonical_names():
    tools = [
        {"name": "diligence.lookup", "input_schema": {}},
        {"name": "plain", "input_schema": {}},
    ]
    m = build_tool_name_map(tools)
    assert m == {"diligence__lookup": "diligence.lookup", "plain": "plain"}
    assert restore_tool_name("diligence__lookup", m) == "diligence.lookup"
    assert restore_tool_name("plain", m) == "plain"
    # Unknown names pass through (model can only call offered tools).
    assert restore_tool_name("mystery", m) == "mystery"
    assert restore_tool_name("x", None) == "x"


def test_name_map_reads_openai_shape_definitions():
    tools = [{"type": "function", "function": {"name": "pack.t", "parameters": {}}}]
    assert build_tool_name_map(tools) == {"pack__t": "pack.t"}


def test_name_map_collision_is_loud():
    tools = [
        {"name": "pack.tool", "input_schema": {}},
        {"name": "pack__tool", "input_schema": {}},
    ]
    with pytest.raises(ValueError, match="both sanitize"):
        build_tool_name_map(tools)


# ------------------------------------------------------ classification


class _RateLimitError(Exception):
    status_code = 429


class _AuthenticationError(Exception):
    pass


class _PermissionDeniedError(Exception):
    status_code = 403


class _NotFoundError(Exception):
    status_code = 404


class _InternalServerError(Exception):
    status_code = 500


class _APIConnectionError(Exception):
    pass


def test_classification_table():
    assert classify_provider_exception(_RateLimitError("slow down")) == "llm.rate_limited"
    assert classify_provider_exception(_AuthenticationError("bad key")) == "llm.auth_error"
    assert classify_provider_exception(_PermissionDeniedError("no")) == "llm.auth_error"
    assert classify_provider_exception(_NotFoundError("no model")) == "llm.request_error"
    assert classify_provider_exception(_InternalServerError("oops")) == "llm.network_error"
    assert classify_provider_exception(_APIConnectionError("refused")) == "llm.network_error"
    # Unknown shapes keep the pre-v1.3 transient behavior.
    assert classify_provider_exception(Exception("???")) == "llm.network_error"


# ---------------------------------------------------------- retry set


def _seed_doc():
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})


def _extractor():
    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass


def test_auth_error_is_terminal_not_retried():
    # The provider recovers on the second call — but auth errors must
    # never get a second call (CONTRACT v1.3 #3: retrying identical
    # credentials cannot succeed).
    _seed_doc()
    _extractor()
    provider = _FlakyThenSucceedsProvider(failures=1, reason="llm.auth_error")
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
    assert failed.payload["reason"] == "llm.auth_error"


def test_request_error_is_terminal_not_retried():
    _seed_doc()
    _extractor()
    provider = _FlakyThenSucceedsProvider(failures=1, reason="llm.request_error")
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
    assert failed.payload["reason"] == "llm.request_error"


def test_network_error_stays_transient():
    _seed_doc()
    _extractor()
    provider = _FlakyThenSucceedsProvider(failures=1, reason="llm.network_error")
    g = Graph()
    Runtime(
        g,
        llm_provider=provider,
        llm_retry_max_attempts=3,
        llm_retry_initial_delay_seconds=0,
    ).run_goal("g")
    # Failed once, retried, recovered.
    assert len(provider.call_log) == 2
    assert not [
        e
        for e in g.events
        if e.type == "behavior.failed" and e.payload["behavior"] == "extractor"
    ]
