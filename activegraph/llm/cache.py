"""Replay LLM cache.

CONTRACT v0.6 #8. The cache is keyed by prompt hash (content-match),
not by event id — that's what lets a fork's regenerated prompts hit
the same recorded responses. The originating `llm.requested` event id
is stored alongside the cached response for trace lineage but is not
the lookup key.

Population paths:

  * `LLMCache.from_events(events)` — used at `Runtime.load(...,
    replay_llm_cache=True)` and `runtime.fork(...,
    replay_llm_cache=True)`. Walks the recorded log and harvests every
    successful `llm.responded` event whose preceding `llm.requested`
    carries a `prompt_hash`. Failed retry attempts carry
    `payload["error"]` and are deliberately skipped.

  * `cache.record(prompt_hash, response, requesting_event_id)` —
    called inline from the LLM-behavior invocation path so that
    same-run repeat prompts hit the cache too (cheap insurance).

`replay_strict=True` semantics layer on top: if a recorded
`llm.responded` event references a `prompt_hash` that the live
re-assembled prompt does NOT produce, the runtime raises
`ReplayDivergenceError(event_id=<llm.requested id>, expected=<recorded
hash>, actual=<rebuilt hash>)` — same divergence-pinning pattern as
the existing event-stream comparison. See decision-2 adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from activegraph.core.event import Event
from activegraph.llm.types import LLMResponse


@dataclass
class CachedEntry:
    response: LLMResponse
    requested_event_id: Optional[str]


class LLMCache:
    def __init__(self) -> None:
        self._by_hash: dict[str, CachedEntry] = {}

    # ---- read ----

    def get(self, prompt_hash: str) -> Optional[LLMResponse]:
        entry = self._by_hash.get(prompt_hash)
        if entry is None:
            return None
        # Mark cache_hit on a copy so we don't mutate the stored
        # response (a second hit on the same hash should also report
        # cache_hit=True).
        r = entry.response
        return LLMResponse(
            raw_text=r.raw_text,
            parsed=r.parsed,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cost_usd=r.cost_usd,
            latency_seconds=r.latency_seconds,
            model=r.model,
            finish_reason=r.finish_reason,
            seed=r.seed,
            cache_hit=True,
            provider_meta=dict(r.provider_meta),
            # v0.7: tool_calls must round-trip so the turn loop sees
            # the same shape live vs cached.
            tool_calls=list(r.tool_calls) if r.tool_calls else None,
        )

    def has(self, prompt_hash: str) -> bool:
        return prompt_hash in self._by_hash

    def __len__(self) -> int:
        return len(self._by_hash)

    # ---- write ----

    def record(
        self,
        prompt_hash: str,
        response: LLMResponse,
        *,
        requesting_event_id: Optional[str] = None,
    ) -> None:
        # Store an un-flagged copy so subsequent `get()` calls
        # consistently set cache_hit=True.
        # v0.7: tolerate test-provider responses without tool_calls.
        tool_calls = getattr(response, "tool_calls", None)
        clean = LLMResponse(
            raw_text=response.raw_text,
            parsed=response.parsed,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
            latency_seconds=response.latency_seconds,
            model=response.model,
            finish_reason=response.finish_reason,
            seed=response.seed,
            cache_hit=False,
            provider_meta=dict(response.provider_meta),
            tool_calls=list(tool_calls) if tool_calls else None,
        )
        self._by_hash[prompt_hash] = CachedEntry(
            response=clean, requested_event_id=requesting_event_id
        )

    # ---- bulk-load from a recorded event log ----

    @classmethod
    def from_events(cls, events: Iterable[Event]) -> "LLMCache":
        """Walk the log and harvest every successful `llm.responded`
        whose preceding `llm.requested` carries a `prompt_hash`.

        The pairing rule: an `llm.responded.caused_by` points at its
        `llm.requested.id`. The `prompt_hash` is on the `llm.requested`
        payload. Error-shaped `llm.responded` events are failed attempts,
        not reusable model output.
        """

        cache = cls()
        events_list = list(events)
        by_id: dict[str, Event] = {e.id: e for e in events_list}
        for e in events_list:
            if e.type != "llm.responded":
                continue
            if e.payload.get("error"):
                continue
            request_id = e.caused_by
            if request_id is None:
                continue
            request = by_id.get(request_id)
            if request is None or request.type != "llm.requested":
                continue
            prompt_hash = request.payload.get("prompt_hash")
            if not prompt_hash:
                continue
            response = _response_from_event_payload(e.payload)
            cache.record(
                prompt_hash, response, requesting_event_id=request_id
            )
        return cache


def _response_from_event_payload(payload: dict) -> LLMResponse:
    from activegraph.llm.types import ToolCall

    cost = payload.get("cost_usd", "0")
    if not isinstance(cost, Decimal):
        cost = Decimal(str(cost))
    # v0.7: tool_calls round-trip through the event payload as plain
    # dicts; hydrate back into ToolCall instances so the runtime's
    # turn loop sees a uniform shape.
    tc_payload = payload.get("tool_calls")
    tool_calls = None
    if tc_payload:
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("name", ""),
                args=dict(tc.get("args", {})),
            )
            for tc in tc_payload
        ]
    return LLMResponse(
        raw_text=payload.get("raw_text", ""),
        parsed=payload.get("parsed"),
        input_tokens=int(payload.get("input_tokens", 0) or 0),
        output_tokens=int(payload.get("output_tokens", 0) or 0),
        cost_usd=cost,
        latency_seconds=float(payload.get("latency_seconds", 0.0) or 0.0),
        model=payload.get("model", "?"),
        finish_reason=payload.get("finish_reason", "?"),
        seed=payload.get("seed"),
        cache_hit=False,
        provider_meta=dict(payload.get("provider_meta", {}) or {}),
        tool_calls=tool_calls,
    )
