
from typing import Literal

from pydantic import BaseModel, Field


FailureCategory = Literal[
    "code_bug",
    "dependency",
    "infra_config",
    "possible_flaky",
    "unknown",
]


class FailureClassification(BaseModel):
    category: FailureCategory

    # This is a heuristic score, not a calibrated probability.
    confidence: float = Field(ge=0.0, le=1.0)

    reasons: list[str] = Field(default_factory=list)

    requires_investigation: bool = True
