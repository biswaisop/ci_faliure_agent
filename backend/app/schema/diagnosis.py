
from typing import Literal

from pydantic import BaseModel, Field


class DiagnosisDraft(BaseModel):
    """Structured LLM output before evidence validation."""

    summary: str
    root_cause: str

    confidence: Literal[
        "high", "medium", "low", "insufficient_evidence"
    ]

    evidence_ids: list[str] = Field(default_factory=list)

    suggested_actions: list[str] = Field(
        default_factory=list
    )


class DiagnosisResult(DiagnosisDraft):
    """Validated output of the investigation graph."""

    unsupported_evidence_ids: list[str] = Field(
        default_factory=list
    )

    evidence_validated: bool = False
