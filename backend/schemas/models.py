from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Verdict = Literal["true", "mostly_true", "misleading", "mostly_false", "false", "unverified"]
ImageVerdict = Literal[
    "Multiple claims reviewed",
    "Context Supported",
    "Possible Context Mismatch",
    "Misleading Caption",
    "Insufficient Evidence",
]
RiskLevel = Literal["Low", "Medium", "High", "Unknown"]


class SearchResult(BaseModel):
    title: str = ""
    url: str
    snippet: str = ""
    published_date: str = ""
    raw_content: str = ""


class EvidenceItem(BaseModel):
    evidence_id: str
    title: str = ""
    url: str
    root_domain: str = ""
    publisher: str = ""
    published_date: str = ""
    retrieved_at: str
    excerpt: str = ""
    source_type: str = "unknown"
    source_quality: float = Field(ge=0.0, le=1.0)


class SourceCredibilityAssessment(BaseModel):
    source_trust_score: int = Field(ge=0, le=100)
    website_risk_level: RiskLevel
    independent_source_count: int = 0
    high_quality_source_count: int = 0
    official_source_count: int = 0
    missing_date_count: int = 0
    duplicate_or_syndication_risk: str = "Unknown"
    strongest_sources: list[str] = Field(default_factory=list)
    trust_signals: list[str] = Field(default_factory=list)
    risk_signals: list[str] = Field(default_factory=list)
    summary: str = ""


class SearchQueries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    general_query: str
    official_source_query: str
    supporting_evidence_query: str
    contradicting_evidence_query: str
    date_context_query: str
    old_news_or_misinformation_query: str

    def as_list(self) -> list[str]:
        return [
            self.general_query,
            self.official_source_query,
            self.supporting_evidence_query,
            self.contradicting_evidence_query,
            self.date_context_query,
            self.old_news_or_misinformation_query,
        ]


class VerifierOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = ""
    verdict: Verdict
    support_score: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    context_mismatch: bool = False
    reasoning_summary: str = ""
    missing_information: list[str] = Field(default_factory=list)

    @field_validator("supporting_evidence", "contradicting_evidence")
    @classmethod
    def no_blank_evidence_ids(cls, value: list[str]) -> list[str]:
        return [item for item in value if item]

    def referenced_evidence_ids(self) -> set[str]:
        return set(self.supporting_evidence) | set(self.contradicting_evidence)


class GonkaTraceRecord(BaseModel):
    step_name: str
    claim_index: int | None = Field(default=None, ge=1)
    claim: str | None = None
    requested_model_id: str
    returned_model_id: str | None = None
    response_body_id: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    timestamp_utc: str
    latency_ms: float
    token_usage: dict[str, Any] | None = None
    success: bool
    error_type: str | None = None
    safe_error_message: str | None = None


class ImageContextAssessment(BaseModel):
    verdict: ImageVerdict
    ocr_text: str = ""
    caption_or_claim: str = ""
    exif_summary: dict[str, str] = Field(default_factory=dict)
    visual_description: str = ""
    reverse_image_note: str = (
        "Reverse-image matching was not performed. "
        "The result evaluates the claim and context, not pixel-level authenticity."
    )
    limitations: list[str] = Field(default_factory=list)


class EvidenceGapPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=2000)
    gaps: list[str] = Field(default_factory=list, max_length=8)
    follow_up_queries: list[str] = Field(default_factory=list, max_length=3)


class DeepReview(BaseModel):
    status: Literal["completed", "partial", "failed"]
    summary: str = ""
    gaps: list[str] = Field(default_factory=list)
    follow_up_queries: list[str] = Field(default_factory=list)
    initial_source_count: int = 0
    additional_source_count: int = 0
    limitations: list[str] = Field(default_factory=list)


class FactCheckReport(BaseModel):
    deep_review: DeepReview | None = None
    extracted_claim: str
    extracted_claims: list[str] = Field(default_factory=list)
    claim_reports: list[FactCheckReport] = Field(default_factory=list)
    unreviewed_claims: list[str] = Field(default_factory=list)
    review_status: Literal["completed", "failed", "partial"] = "completed"
    final_verdict: str
    truth_score: int = Field(ge=0, le=100)
    confidence_score: int = Field(ge=0, le=100)
    concise_explanation: str
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceItem] = Field(default_factory=list)
    all_evidence: list[EvidenceItem] = Field(default_factory=list)
    source_credibility_assessment: SourceCredibilityAssessment | None = None
    verifier_outputs: list[VerifierOutput] = Field(default_factory=list)
    judge_output: VerifierOutput | None = None
    gonka_trace: list[GonkaTraceRecord] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    image_context_assessment: ImageContextAssessment | None = None


class ClaimExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[str] = Field(default_factory=list)
    not_verifiable_reason: str = ""


class ArticleContent(BaseModel):
    url: str
    title: str = ""
    text: str
    publisher: str = ""
    published_date: str = ""
