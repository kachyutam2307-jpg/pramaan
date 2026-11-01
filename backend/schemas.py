"""Pydantic v2 models — the API contract for /verify and /explain.
Define these BEFORE writing the endpoints (per spec section 11)."""
from __future__ import annotations

from typing import List, Optional, Literal
from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl


# --- Shared sub-models ---

class Citation(BaseModel):
    """One piece of evidence returned to the user. URL must be real."""
    source_name: str
    url: str
    title: str
    fetched_at: datetime
    snippet: str = Field(..., description="Relevant passage from the source, <=400 chars")


class RiskSignal(BaseModel):
    """A single honest signal from the risk module. Never fake/real."""
    signal: str
    severity: Literal["low", "medium", "high"]
    detail: str


class AuditEntry(BaseModel):
    """One step in the agent's audit trail."""
    step: str
    detail: str
    ts: datetime


# --- /verify request / response ---

class VerifyRequest(BaseModel):
    input_type: Literal["text", "image", "audio", "pdf"]
    text: Optional[str] = None
    file_b64: Optional[str] = None
    language: Literal["en", "hi", "ta", "bn"] = "en"
    include_risk: bool = True

    def needs_file(self) -> bool:
        return self.input_type in ("image", "audio", "pdf")


class VerifyResponse(BaseModel):
    request_id: str
    extracted_claim: str
    verdict: Literal["Supported", "Contradicted", "Insufficient Evidence"]
    confidence: Literal["low", "medium", "high"]
    citations: List[Citation]
    reasoning_summary: str
    risk_signals: List[RiskSignal] = Field(default_factory=list)
    audit_trail: List[AuditEntry]


# --- /explain request / response ---

class ExplainRequest(BaseModel):
    input_type: Literal["text", "pdf"]
    text: Optional[str] = None
    file_b64: Optional[str] = None
    language: Literal["en", "hi", "ta", "bn"] = "en"
    generate_audio: bool = False

    def needs_file(self) -> bool:
        return self.input_type == "pdf"


class ExplainResponse(BaseModel):
    request_id: str
    document_title: str
    plain_summary: str
    rights: List[str]
    eligibility: List[str]
    impact: List[str]
    citations: List[Citation]
    audio_b64: Optional[str] = None
    audit_trail: List[AuditEntry]


# --- /health ---

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    sources_up: dict  # e.g. {"pib": true, "factcheck": false}


# --- Internal (LLM structured output) ---

class ExtractedClaim(BaseModel):
    """Output of the claim-extraction LLM call."""
    claim: Optional[str]
    entities: List[str] = Field(default_factory=list)
    language_of_input: str = "en"
    reason: Optional[str] = None  # only set if claim is None


class VerdictResult(BaseModel):
    """Output of the verdict LLM call (before guardrails)."""
    verdict: Literal["Supported", "Contradicted", "Insufficient Evidence"]
    confidence: Literal["low", "medium", "high"]
    citations: List[dict]  # {source_index, snippet}
    reasoning_summary: str


class ExplainResult(BaseModel):
    """Output of the explain LLM call."""
    document_title: str
    issuing_authority: str
    effective_date: Optional[str] = None
    plain_summary: str
    rights: List[str]
    eligibility: List[str]
    impact: List[str]


class ErrorPayload(BaseModel):
    """Returned on 4xx/5xx. Honest error, no fabricated fields."""
    detail: str
    request_id: Optional[str] = None
