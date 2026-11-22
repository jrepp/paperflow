"""Typed contracts for model work, evidence, memory, and evaluation."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pathlib import Path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenPrices(StrictModel):
    model: str
    as_of: str = "2026-11-17"
    input_per_million: float = Field(ge=0)
    cached_input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    off_peak_multiplier: float = Field(default=1, gt=0, le=1)
    holiday_dates_utc: list[str] = Field(default_factory=list)


class AssistantSettings(StrictModel):
    enabled: bool = False
    path: Path = Path("artifacts/curation.db")
    reasoning_model: str = "deepseek-flash"
    reasoning_base_url: str = "https://api.deepseek.com"
    reasoning_key: SecretStr | None = Field(default=None, exclude=True)
    decision_model: str = "jev-1.14.0"
    decision_base_url: str = "https://api.typesafe.ai"
    decision_key: SecretStr | None = Field(default=None, exclude=True)
    story_search_weeks: int = Field(default=4, ge=1, le=520)
    interval_seconds: int = Field(default=60, ge=1)
    items_per_cycle: int = Field(default=1, ge=1, le=20)
    reasoning_prices: TokenPrices = Field(default_factory=lambda: TokenPrices(
        model='deepseek-flash', input_per_million=0.3, cached_input_per_million=0.006,
        output_per_million=1.2, off_peak_multiplier=0.5))
    decision_prices: TokenPrices = Field(default_factory=lambda: TokenPrices(
        model='jev-1.14.0', input_per_million=0.042, cached_input_per_million=0.042,
        output_per_million=0))
    timeout_seconds: int = Field(default=240, ge=1, le=1800)
    request_limit: int = Field(default=12, ge=1, le=100)
    tool_limit: int = Field(default=40, ge=1, le=200)
    token_limit: int = Field(default=200000, ge=1000, le=500000)
    max_output_tokens: int = Field(default=32000, ge=1000, le=64000)
    decision_limit: int = Field(default=12, ge=1, le=100)
    decision_depth: int = Field(default=3, ge=1, le=10)
    max_attempts: int = Field(default=3, ge=1, le=10)
    daily_attempt_limit: int | None = Field(default=None, ge=1, le=10000, deprecated=True)
    rolling_token_limit: int = Field(default=20000000, ge=1)
    retry_seconds: int = Field(default=120, ge=1)
    evaluation_sample_rate: float = Field(default=0.2, ge=0, le=1)
    evaluation_token_limit: int = Field(default=32000, ge=1000, le=100000)
    source_character_limit: int = Field(default=120000, ge=1000, le=200000)
    evidence_selection_enabled: bool = True
    categories: list[str] = Field(default_factory=lambda: [
        "artificial-intelligence", "computing", "science", "engineering", "society", "other",
    ], min_length=1, max_length=50)


class Citation(StrictModel):
    revision_id: UUID
    quote: str = Field(min_length=1, max_length=2000)
    start: int = Field(default=0, ge=0, description="Unicode offset if known; the harness resolves unique exact quotes when the offset does not match.")


class Finding(StrictModel):
    statement: str = Field(min_length=1, max_length=3000)
    kind: Literal["reported_result", "author_claim", "inference", "counterfactual"]
    citations: list[Citation] = Field(min_length=1, max_length=12)
    uncertainty: str = Field(min_length=1, max_length=2000)


class Effect(StrictModel):
    order: Literal[2, 3]
    hypothesis: str = Field(min_length=1, max_length=2000)
    mechanism: list[str] = Field(min_length=2, max_length=6)
    assumptions: list[str] = Field(min_length=1, max_length=8)
    time_horizon: str = Field(min_length=1, max_length=300)
    falsifier: str = Field(min_length=1, max_length=1000)
    uncertainty: str = Field(min_length=1, max_length=1000)
    citations: list[Citation] = Field(min_length=1, max_length=12)


class Analysis(StrictModel):
    summary: str = Field(min_length=1, max_length=4000)
    category: str
    evidence_basis: Literal["full_text", "abstract", "excerpt"]
    findings: list[Finding] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(min_length=1, max_length=12)
    biases: list[Finding] = Field(max_length=12)
    effects: list[Effect] = Field(max_length=10)
    research_gaps: list[str] = Field(max_length=12)


class MemoryInput(StrictModel):
    kind: Literal["follow_up", "insight"]
    text: str = Field(min_length=1, max_length=4000)
    scope: str = Field(min_length=1, max_length=1000)
    basis: Literal["observation", "hypothesis", "task"]
    citations: list[Citation] = Field(max_length=12)
    uncertainty: str = Field(min_length=1, max_length=1000)
    counterevidence: str = Field(max_length=2000)
    revisit_when: str = Field(min_length=1, max_length=1000)
    priority: Literal["low", "normal", "high"] = "normal"


class DecisionRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    criteria: str = Field(min_length=1, max_length=3000)
    citations: list[Citation] = Field(min_length=1, max_length=12)
    parent_ids: list[str] = Field(default_factory=list, max_length=12)


class Judgment(StrictModel):
    """Answer the supplied question using its criteria and the cited evidence."""
    supported: bool = Field(description="The evidence satisfies the supplied question's criteria.")
    evidence_sufficient: bool = Field(description="The evidence is sufficient to make this judgment.")


class QualityJudgment(StrictModel):
    """Audit the supplied analysis against source text, independently of the curator."""
    factual_support: bool = Field(description="Every factual claim in the summary, findings, and bias assessments is supported by the supplied source. Return false for any invented number, motive, source layout, claim placement, or omission that the source does not establish.")
    evidence_limits: bool = Field(description="The analysis preserves the limits of excerpt, abstract, or full-text evidence.")
    uncertainty: bool = Field(description="The analysis distinguishes findings, claims, inferences, and counterfactuals.")
    causal_reasoning: bool = Field(description="Effects have defensible causal steps and assumptions, or missing effects have explicit evidence gaps.")
    neutrality: bool = Field(description="The prose is direct and neutral, with no editorializing, rhetorical questions, tool activity, bucket status, or internal collection state in the public summary.")
    coverage: bool = Field(description="The analysis captures the source's important ideas without material omissions.")
    category_fit: bool = Field(description="The category matches the content and the supplied taxonomy.")
