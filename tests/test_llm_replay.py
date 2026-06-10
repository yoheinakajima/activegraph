"""Replay LLM cache (CONTRACT v0.6 #8, decision-2 adjustment).

What's covered:
- Replay (load) does NOT re-fire behaviors (v0.5 #14 preserved).
- Fork with replay_llm_cache=True hits the cache for matching prompts.
- Fork with replay_llm_cache=True falls through to the provider for
  divergent prompts.
- Cache stores response provenance via the originating llm.requested
  event id.
- decision-4 adjustment: pre-call count_tokens is skipped on cache hit
  even when max_cost_usd is set.
- decision-2 adjustment: replay_strict + LLM behaviors injects the
  cache implicitly; a prompt-hash mismatch raises ReplayDivergenceError.
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pytest

from activegraph import (
    Graph,
    ReplayDivergenceError,
    Runtime,
    behavior,
    clear_registry,
    llm_behavior,
)
from activegraph.llm import LLMBehaviorError, LLMCache, LLMResponse

from tests._llm_helpers import Claim, ClaimList, ScriptedProvider


def _register(extra_extractor=None):
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="x",
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
        deterministic=True,
    )
    def extractor(event, graph, ctx, llm_output):
        for c in llm_output.claims:
            graph.add_object("claim", {"text": c.text, "confidence": c.confidence})


def _scripted(text="Sample claim"):
    return ScriptedProvider(
        respond_fn=lambda m, s: ClaimList(claims=[Claim(text=text, confidence=0.9)])
    )


class _FailsOnceThenSucceeds:
    default_model = "claude-sonnet-4-5"

    def __init__(self):
        self.calls = 0

    def recognizes_model(self, name: str) -> bool:
        return True

    def complete(self, **kw):
        self.calls += 1
        if self.calls == 1:
            raise LLMBehaviorError(
                "llm.network_error",
                "temporary outage",
                payload_extras={"retry_after_seconds": 0},
            )
        parsed = ClaimList(claims=[Claim(text="after retry", confidence=0.9)])
        return LLMResponse(
            raw_text=parsed.model_dump_json(),
            parsed=parsed,
            input_tokens=12,
            output_tokens=6,
            cost_usd=Decimal("0.001"),
            latency_seconds=0.01,
            model=kw["model"],
            finish_reason="end_turn",
        )

    def estimate_cost(self, *, input_tokens, output_tokens, model):
        return Decimal("0.001")

    def count_tokens(self, *, system, messages, model):
        return 12


# ---------- LLMCache.from_events round-trip --------------------------------


def test_cache_from_events_round_trip():
    clear_registry()
    _register()
    provider = _scripted()
    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("test")

    cache = LLMCache.from_events(g.events)
    assert len(cache) == 1

    request = next(e for e in g.events if e.type == "llm.requested")
    cached = cache.get(request.payload["prompt_hash"])
    assert cached is not None
    assert cached.cache_hit is True
    assert cached.raw_text != ""


def test_cache_miss_returns_none():
    cache = LLMCache()
    assert cache.get("nope") is None
    assert cache.has("nope") is False


def test_cache_from_events_skips_failed_retry_attempts():
    clear_registry()
    _register()
    provider = _FailsOnceThenSucceeds()
    g = Graph()
    Runtime(
        g,
        llm_provider=provider,
        llm_retry_max_attempts=1,
        llm_retry_initial_delay_seconds=0,
    ).run_goal("test")

    cache = LLMCache.from_events(g.events)
    assert len(cache) == 0


# ---------- fork with cache: hit + miss paths ------------------------------


def test_fork_with_replay_llm_cache_serves_from_cache():
    clear_registry()
    _register()
    db = tempfile.mktemp(suffix=".db")
    try:
        provider = _scripted()
        g = Graph()
        rt = Runtime(g, llm_provider=provider, persist_to=db)
        rt.run_goal("test")
        original_calls = len(provider.call_log)
        assert original_calls == 1

        # Fork at goal.created — fork-replay re-fires seed + extractor,
        # serving the extractor's LLM call from cache.
        goal_evt = next(e for e in g.events if e.type == "goal.created")
        fork_provider = _scripted()
        fork = rt.fork(
            at_event=goal_evt.id,
            label="cached",
            replay_llm_cache=True,
            llm_provider=fork_provider,
        )
        fork.run_until_idle()

        # Fork provider was NEVER called — all LLM responses came from cache.
        assert fork_provider.call_log == []
        resp = next(e for e in fork.graph.events if e.type == "llm.responded")
        assert resp.payload["cache_hit"] is True
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_fork_without_cache_calls_provider():
    clear_registry()
    _register()
    db = tempfile.mktemp(suffix=".db")
    try:
        provider = _scripted()
        g = Graph()
        rt = Runtime(g, llm_provider=provider, persist_to=db)
        rt.run_goal("test")

        goal_evt = next(e for e in g.events if e.type == "goal.created")
        fork_provider = _scripted()
        fork = rt.fork(
            at_event=goal_evt.id, label="fresh", llm_provider=fork_provider,
            # replay_llm_cache defaults to False
        )
        fork.run_until_idle()
        # Fork calls the provider (no cache).
        assert len(fork_provider.call_log) == 1
        resp = next(e for e in fork.graph.events if e.type == "llm.responded")
        assert resp.payload["cache_hit"] is False
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_fork_with_cache_falls_through_on_prompt_divergence():
    """A fork that injects different content produces a different prompt,
    so the cache misses and the provider is called."""

    clear_registry()
    _register()
    db = tempfile.mktemp(suffix=".db")
    try:
        provider = _scripted()
        g = Graph()
        rt = Runtime(g, llm_provider=provider, persist_to=db)
        rt.run_goal("test")

        goal_evt = next(e for e in g.events if e.type == "goal.created")
        fork_provider = _scripted("Different claim")
        fork = rt.fork(
            at_event=goal_evt.id,
            label="diverged",
            replay_llm_cache=True,
            llm_provider=fork_provider,
        )
        # Inject a *different* document so the prompt for the new
        # document doesn't match anything in the cache.
        fork.graph.add_object(
            "document", {"title": "Different doc", "body": "different content"}
        )
        fork.run_until_idle()
        # The cached doc#1 call hits cache; the new doc#2 misses.
        assert len(fork_provider.call_log) >= 1
    finally:
        if os.path.exists(db):
            os.remove(db)


# ---------- pre-call count_tokens skipped on cache hit ---------------------


def test_count_tokens_skipped_on_cache_hit_even_with_cost_budget():
    """decision-4 adjustment: when a cached response is available, we
    don't pay the count_tokens roundtrip even if max_cost_usd is set."""

    clear_registry()
    _register()
    db = tempfile.mktemp(suffix=".db")
    try:
        provider = _scripted()
        g = Graph()
        rt = Runtime(
            g, llm_provider=provider, persist_to=db, budget={"max_cost_usd": "1.00"}
        )
        rt.run_goal("test")
        # First run made one count_tokens call (no cache).
        assert len(provider.token_count_log) == 1

        goal_evt = next(e for e in g.events if e.type == "goal.created")
        fork_provider = _scripted()
        fork = rt.fork(
            at_event=goal_evt.id,
            label="cached",
            replay_llm_cache=True,
            llm_provider=fork_provider,
        )
        # Re-set the fork's budget so the gate would be active.
        fork.budget = type(fork.budget)({"max_cost_usd": "1.00"})
        fork.run_until_idle()
        # Cache hit → no count_tokens roundtrip.
        assert fork_provider.token_count_log == []
        assert fork_provider.call_log == []
    finally:
        if os.path.exists(db):
            os.remove(db)


# ---------- replay_strict + prompt drift (decision-2 adjustment) -----------


def test_replay_strict_raises_on_prompt_hash_drift():
    """If a recorded llm.responded references a prompt hash that the
    live re-assembly does NOT produce, that's divergence — pinned to
    the new llm.requested event id."""

    clear_registry()
    _register()
    db = tempfile.mktemp(suffix=".db")
    try:
        provider = _scripted()
        g = Graph()
        rt = Runtime(g, llm_provider=provider, persist_to=db)
        rt.run_goal("test")
        # Tamper with the saved prompt hash on the llm.requested event
        # in the live in-memory log. Reload uses the SQLite log though,
        # so we need to update the SQLite row.
        import sqlite3, json
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT id, payload FROM events WHERE type=?", ("llm.requested",))
        row = cur.fetchone()
        eid, payload_json = row
        payload = json.loads(payload_json)
        payload["prompt_hash"] = "ffff" * 16  # 64 chars, won't match
        cur.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), eid))
        conn.commit()
        conn.close()

        clear_registry()
        _register()
        with pytest.raises(ReplayDivergenceError):
            Runtime.load(
                db,
                llm_provider=_scripted(),
                replay_strict=True,
            )
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_replay_strict_ignores_transient_failed_llm_attempts():
    clear_registry()
    _register()
    db = tempfile.mktemp(suffix=".db")
    try:
        provider = _FailsOnceThenSucceeds()
        g = Graph()
        Runtime(
            g,
            llm_provider=provider,
            persist_to=db,
            llm_retry_initial_delay_seconds=0,
        ).run_goal("test")
        assert provider.calls == 2

        clear_registry()
        _register()
        Runtime.load(
            db,
            llm_provider=_scripted("after retry"),
            replay_strict=True,
        )
    finally:
        if os.path.exists(db):
            os.remove(db)
