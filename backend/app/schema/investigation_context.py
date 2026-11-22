
from pydantic import BaseModel, Field

from app.schema.failure_packet import FailurePacket


class ChangedFile(BaseModel):
    filename: str
    previous_filename: str | None = None
    status: str

    additions: int = 0
    deletions: int = 0

    patch: str = ""
    truncated: bool = False


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
