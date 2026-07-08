"""LLM behaviors subpackage. CONTRACT v0.6; OpenAIProvider added v1.0.1 #5.

Public surface:

  LLMProvider            — Protocol every provider implements
  LLMMessage             — single role-tagged message
  LLMResponse            — what `complete()` returns
  AnthropicProvider      — reference implementation
  OpenAIProvider         — second concrete provider, surface parity
                           with AnthropicProvider (v1.0.1 #5)
  EmbeddingProvider      — Protocol for text-embedding providers
                           (v1.3); the runtime holds one for packs,
                           it never calls it itself
  HashEmbeddingProvider  — deterministic, dependency-free test double
                           for embedding plumbing
  RecordedLLMProvider    — fixture-backed provider for tests
  RecordingLLMProvider   — wraps another provider, persists responses
                           as fixtures (for first-time test seed)
  LLMCache               — content-keyed replay cache
  AssembledPrompt        — the deterministic prompt + params blob
                           returned by `LLMBehavior.build_prompt`
  MissingProviderError   — raised when an @llm_behavior is registered
                           without a runtime provider
  LLMBehaviorError       — structured failure carrier from @llm_behavior
                           wrappers; runtime turns it into
                           `behavior.failed` with a `reason`
  parse_structured_response — JSON-extraction-then-Pydantic-validate
                           helper shared by every provider that uses
                           the framework's instruction-based path
"""

from activegraph.llm.anthropic import AnthropicProvider
from activegraph.llm.cache import LLMCache
from activegraph.llm.embedding import EmbeddingProvider, HashEmbeddingProvider
from activegraph.llm.errors import LLMBehaviorError, MissingProviderError
from activegraph.llm.openai import OpenAIProvider
from activegraph.llm.parsing import parse_structured_response
from activegraph.llm.prompt import (
    AssembledPrompt,
    assemble_prompt,
    schema_to_json,
    serialize_view,
)
from activegraph.llm.provider import LLMProvider
from activegraph.llm.recorded import RecordedLLMProvider, RecordingLLMProvider
from activegraph.llm.types import LLMMessage, LLMResponse, ToolCall


__all__ = [
    "AnthropicProvider",
    "AssembledPrompt",
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "LLMBehaviorError",
    "LLMCache",
    "LLMMessage",
    "LLMProvider",
    "LLMResponse",
    "MissingProviderError",
    "OpenAIProvider",
    "RecordedLLMProvider",
    "RecordingLLMProvider",
    "ToolCall",
    "assemble_prompt",
    "parse_structured_response",
    "schema_to_json",
    "serialize_view",
]
