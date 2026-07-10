"""Content-keyed replay cache for runtime-owned embedding calls.

CONTRACT v1.8 #6 mirrors the LLM and tool cache posture: requests are
keyed by canonical input content, successful recorded returns hydrate a
fresh runtime, and cache reads return defensive copies so callers cannot
mutate the replay authority.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Iterable, Optional

from activegraph.core.event import Event


def hash_embedding_request(*, texts: list[str], model: str) -> str:
    """Return the stable content key for one embedding request."""

    canonical = json.dumps(
        {"model": model, "texts": texts},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedEmbedding:
    """One successful recorded embedding return."""

    vectors: tuple[tuple[float, ...], ...]
    requested_event_id: Optional[str] = None


class EmbeddingCache:
    """Replay cache keyed by embedding model and input text content."""

    def __init__(self) -> None:
        self._by_hash: dict[str, CachedEmbedding] = {}

    def get(self, inputs_hash: str) -> Optional[list[list[float]]]:
        """Return defensive vector copies for ``inputs_hash`` if present."""

        entry = self._by_hash.get(inputs_hash)
        if entry is None:
            return None
        return [list(vector) for vector in entry.vectors]

    def has(self, inputs_hash: str) -> bool:
        """Whether a successful response is cached for ``inputs_hash``."""

        return inputs_hash in self._by_hash

    def __len__(self) -> int:
        return len(self._by_hash)

    def record(
        self,
        inputs_hash: str,
        vectors: list[list[float]],
        *,
        requesting_event_id: Optional[str] = None,
    ) -> None:
        """Store an immutable copy of a validated embedding response."""

        self._by_hash[inputs_hash] = CachedEmbedding(
            vectors=tuple(tuple(value for value in vector) for vector in vectors),
            requested_event_id=requesting_event_id,
        )

    @classmethod
    def from_events(cls, events: Iterable[Event]) -> "EmbeddingCache":
        """Harvest successful request/response pairs from an event log."""

        cache = cls()
        events_list = list(events)
        by_id = {event.id: event for event in events_list}
        for event in events_list:
            if event.type != "embedding.responded" or event.payload.get("error"):
                continue
            request_id = event.caused_by
            if request_id is None:
                continue
            request = by_id.get(request_id)
            if request is None or request.type != "embedding.requested":
                continue
            inputs_hash = request.payload.get("inputs_hash")
            vectors = event.payload.get("vectors")
            if not isinstance(inputs_hash, str) or not isinstance(vectors, list):
                continue
            try:
                hydrated: list[list[float]] = []
                dimensions: Optional[int] = None
                for vector in vectors:
                    if not isinstance(vector, list):
                        raise TypeError("non-list vector")
                    if dimensions is None:
                        dimensions = len(vector)
                    elif len(vector) != dimensions:
                        raise ValueError("mixed dimensions")
                    hydrated_vector: list[float] = []
                    for value in vector:
                        if isinstance(value, bool) or not isinstance(
                            value, (int, float)
                        ):
                            raise TypeError("non-numeric component")
                        number = float(value)
                        if not math.isfinite(number):
                            raise ValueError("non-finite component")
                        hydrated_vector.append(number)
                    hydrated.append(hydrated_vector)
            except (TypeError, ValueError):
                continue
            input_count = request.payload.get("input_count")
            if isinstance(input_count, int) and len(hydrated) != input_count:
                continue
            cache.record(
                inputs_hash,
                hydrated,
                requesting_event_id=request_id,
            )
        return cache


__all__ = ["EmbeddingCache"]
