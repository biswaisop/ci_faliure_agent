
from typing import Literal

from pydantic import BaseModel, Field


class DiagnosisFields(BaseModel):

    confidence: Literal[
        "high", "medium", "low", "insufficient_evidence"
    ]

    evidence_ids: list[str] = Field(default_factory=list)

    suggested_actions: list[str] = Field(
        default_factory=list
    )
    regression_attribution: Literal["supported", "possible", "insufficient_evidence"] = "insufficient_evidence"
    limitations: list[str] = Field(default_factory=list)
    possible_cause: str | None = None


class DiagnosisDraft(DiagnosisFields):
    """Structured LLM output before evidence validation."""

    observed_error: str | None = None


class DiagnosisResult(DiagnosisFields):
    """Validated output of the investigation graph."""

    summary: str
    root_cause: str
    unsupported_evidence_ids: list[str] = Field(
        default_factory=list
    )

    evidence_validated: bool = False
