"""Diligence pack settings. CONTRACT v0.9 #15.

Every field has a default so `runtime.load_pack(diligence_pack)`
without `settings=` works for the quickstart. The killer demo
overrides explicitly to demonstrate the typed-injection API.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DiligenceSettings(BaseModel):  # type: ignore[misc]  # BaseModel is Any under follow_imports=skip
    """Configuration for the Diligence pack.

    Accessed by behaviors in three forms (CONTRACT v0.9 #7):
      1. Typed parameter injection (primary):
         `def claim_extractor(event, graph, ctx, out, *,
                              settings: DiligenceSettings): ...`
      2. `ctx.settings.confidence_threshold_for_review`
      3. `ctx.pack_settings("diligence")` for cross-pack lookups.
    """

    llm_model: str = Field(
        default="claude-sonnet-4-5",
        description="The Claude model used by all diligence LLM behaviors.",
    )
    max_documents_per_company: int = Field(
        default=5, ge=1,
        description="Cap on documents fetched per company. Bounds tool cost.",
    )
    max_claims_per_document: int = Field(
        default=20, ge=1,
        description="Cap on claims extracted per document. Bounds LLM cost and "
                    "downstream evidence-linking work.",
    )
    confidence_threshold_for_review: float = Field(
        default=0.7, ge=0.0, le=1.0,
        description="Claims with confidence ≥ this threshold participate in "
                    "contradiction detection.",
    )
    min_questions: int = Field(
        default=8, ge=1,
        description="Minimum number of thesis questions the generator produces.",
    )
    max_questions: int = Field(
        default=15, ge=1,
        description="Maximum number of thesis questions. Generator is one-shot "
                    "in v0.9 (CONTRACT v0.9 #16); adaptive in v1.0.",
    )
    auto_approve_memos: bool = Field(
        default=True,
        description="When False, memo writes go through the memo_approval "
                    "policy (CONTRACT v0.9 #15 / #9). True for quickstart.",
    )
    auto_approve_risks: bool = Field(
        default=True,
        description="When False, risk writes go through the risk_approval "
                    "policy. True for quickstart.",
    )
