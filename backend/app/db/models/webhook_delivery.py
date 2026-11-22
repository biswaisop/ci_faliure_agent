from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.github_installation import GitHubId

DeliveryStatus = Literal["pending", "processing", "completed", "failed"]
ProcessingError = Literal[
    "github_timeout", "github_rate_limit", "github_access_unavailable", "github_api_error",
    "missing_installation_token", "installation_unavailable", "repository_unavailable",
    "invalid_github_data", "database_error", "processing_error",
]


class DeliveryMetadata(BaseModel):
    event_type: Literal["workflow_run", "installation", "installation_repositories", "repository"]
    action: str = Field(min_length=1, max_length=64)
    installation_id: GitHubId | None = None
    github_repo_id: GitHubId | None = None
    run_id: GitHubId | None = None
    run_attempt: GitHubId | None = None


class WebhookDelivery(DeliveryMetadata):
    model_config = ConfigDict(extra="ignore")

    delivery_id: str = Field(min_length=1, max_length=128)
    status: DeliveryStatus = "pending"
    attempt_count: int = Field(default=0, ge=0)
    received_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: ProcessingError | None = None
