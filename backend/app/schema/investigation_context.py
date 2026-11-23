
from typing import Literal

from pydantic import BaseModel, Field

from app.schema.failure_packet import FailurePacket

ComparisonStatus = Literal[
    "available", "partial", "no_previous_success", "same_commit", "baseline_not_ancestor",
    "comparison_failed", "not_attempted",
]
ComparisonReason = Literal[
    "branch_unavailable", "failure_time_unavailable", "history_limit", "github_rate_limited",
    "github_access_denied", "commit_unavailable", "github_unavailable", "invalid_response",
]


class ChangedFile(BaseModel):
    filename: str
    previous_filename: str | None = None
    status: str

    additions: int = 0
    deletions: int = 0

    patch: str = ""
    truncated: bool = False
    patch_status: Literal["available", "omitted", "truncated"] | None = None


class FileCorrelation(BaseModel):
    failing_file: str
    changed_file: str
    match_type: str


class InvestigationContext(BaseModel):
    github_repo_id: int
    run_id: int
    run_attempt: int
    workflow_id: int

    head_sha: str
    base_sha: str | None = None
    head_branch: str | None = None

    failure_packets: list[FailurePacket] = Field(
        default_factory=list
    )

    changed_files: list[ChangedFile] = Field(
        default_factory=list
    )

    correlations: list[FileCorrelation] = Field(
        default_factory=list
    )

    diff_available: bool = False
    diff_may_be_incomplete: bool = False
    comparison_status: ComparisonStatus = "not_attempted"
    comparison_reason: ComparisonReason | None = None
    files_limit_reached: bool = False
