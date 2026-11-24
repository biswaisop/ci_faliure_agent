from typing import Annotated, Literal

from pydantic import BaseModel, Field

FixStatus = Literal["suggested", "insufficient_evidence"]
ShortExplanation = Annotated[str, Field(min_length=1, max_length=500)]
EvidenceId = Annotated[str, Field(min_length=1, max_length=240)]


class SuggestedFileChange(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    original_snippet: str = Field(min_length=1, max_length=3000)
    replacement_snippet: str = Field(min_length=1, max_length=3000)
    explanation: ShortExplanation


class FixSuggestion(BaseModel):
    status: FixStatus
    summary: ShortExplanation
    changes: list[SuggestedFileChange] = Field(default_factory=list, max_length=3)
    rationale: ShortExplanation
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=16)
    risks: list[ShortExplanation] = Field(default_factory=list, max_length=4)
    tests_to_run: list[ShortExplanation] = Field(default_factory=list, max_length=4)
    verified: Literal[False] = False


class FixSuggestionView(BaseModel):
    status: Literal["pending", "generating", "suggested", "insufficient_evidence", "failed"]
    suggestion: FixSuggestion | None = None
