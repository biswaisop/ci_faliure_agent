from datetime import datetime
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.db.models.github_installation import GitHubId
from app.schema.failure_packet import FailurePacket


CollectionStatus = Literal["pending", "collecting", "completed", "failed"]
LogStatus = Literal["not_collected", "available", "missing", "expired", "failed"]
ShortText = Annotated[str, Field(min_length=1, max_length=1024)]


class WorkflowRunKey(BaseModel):
    github_repo_id: GitHubId
    run_id: GitHubId
    run_attempt: GitHubId = 1


class WorkflowRunMetadata(WorkflowRunKey):
    installation_id: GitHubId | None = None
    workflow_id: GitHubId
    run_number: GitHubId | None = None
    head_sha: ShortText
    head_branch: ShortText | None = None
    event: ShortText | None = None
    status: Literal["completed"] = "completed"
    conclusion: Literal["failure"] = "failure"
    html_url: ShortText | None = None
    github_created_at: datetime | None = None
    github_updated_at: datetime | None = None


class FailedStep(BaseModel):
    number: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    name: ShortText
    conclusion: Literal["failure"] = "failure"


class FailedJob(BaseModel):
    job_id: GitHubId = Field(validation_alias=AliasChoices("job_id", "id"))
    name: ShortText
    conclusion: Literal["failure"] = "failure"
    failed_steps: list[FailedStep] = Field(default_factory=list, max_length=1000)
    log_status: LogStatus = "not_collected"
    log_reference: ShortText | None = None


class EvidenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_packets: list[FailurePacket] | None = None
    base_sha: ShortText | None = None
    diff_summary: str | None = Field(default=None, max_length=8_000)


class RunEvidence(EvidenceUpdate):
    collection_status: CollectionStatus = "pending"


class WorkflowRun(WorkflowRunMetadata):
    model_config = ConfigDict(extra="ignore")
    failure_packets: list[FailurePacket] | None = None
    failed_jobs: list[FailedJob] = Field(default_factory=list)
    evidence: RunEvidence = Field(default_factory=RunEvidence)
    created_at: datetime
    updated_at: datetime
