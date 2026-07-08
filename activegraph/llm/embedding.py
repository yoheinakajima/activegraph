"""The ``EmbeddingProvider`` Protocol. v1.3.

The runtime's second provider seam, next to :class:`LLMProvider`.
The runtime itself never calls it — retrieval, ranking, and score
blending are pack concerns (the memory capability lives downstream) —
but the seam lives here so every pack finds embeddings the same way
(``runtime.embedding_provider``) instead of each pack growing its own
configuration channel. Locked by the July 2026 agent-readiness
review: keyword-overlap-only memory retrieval missed obvious queries,
and the blocking gap was that nothing standard could hold an embedder.

Deliberately minimal: one method plus a default-model declaration.
Cost accounting, batching hints, and dimension negotiation can widen
the Protocol additively later, the way ``LLMProvider`` grew
``recognizes_model`` (v1.0.2) and
``supports_native_structured_output`` (v1.3) behind ``getattr``
guards.

Shipped implementations: only :class:`HashEmbeddingProvider`, a
deterministic, dependency-free test double. Real providers (OpenAI
embeddings, Voyage, local sentence-transformers) are pack/application
territory — the runtime stays free of network dependencies and API
keys.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for text-embedding providers.

    Mirrors :class:`LLMProvider`'s conventions: keyword-only
    arguments, explicit ``model``, a ``default_model`` class
    attribute for callers that don't pin one.
    """

    default_model: str

    def embed(self, *, texts: list[str], model: str) -> list[list[float]]:
        """Embed ``texts`` into one vector per input, order-preserving.

        Every returned vector must have the same dimensionality for a
        given ``model``. Implementations should raise on failure
        rather than returning partial results — callers blend these
        scores into retrieval rankings, and a silently short list
        would misalign every downstream score.
        """
        ...


class HashEmbeddingProvider:
    """Deterministic, dependency-free ``EmbeddingProvider`` test double.

    Embeds text by hashing whitespace-separated, lowercased tokens
    into a fixed number of buckets and L2-normalizing the result.
    Identical texts embed identically on every platform and run — no
    network, no keys, no model weights.

    The vectors are **not semantically meaningful**: cosine similarity
    reflects token overlap, nothing more. Use it to test embedding
    plumbing (storage, blending, dimension handling) deterministically;
    wire a real provider for retrieval quality.
    """

    default_model: str = "hash-64"

    def __init__(self, *, dimensions: int = 64) -> None:
        if dimensions < 1:
            raise ValueError(f"dimensions must be >= 1, got {dimensions}")
        self._dimensions = dimensions

    def embed(self, *, texts: list[str], model: str) -> list[list[float]]:
        """One L2-normalized bucket-count vector per input text.

        ``model`` is accepted for Protocol conformance and ignored —
        the double has exactly one behavior.
        """
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimensions
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]
