from datetime import datetime
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

GitHubId = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
InstallationStatus = Literal["active", "suspended", "deleted", "unavailable"]
RepositorySelection = Literal["all", "selected"]


class GitHubAccount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    github_account_id: GitHubId = Field(validation_alias=AliasChoices("github_account_id", "id"))
    login: str = Field(min_length=1)
    type: Literal["User", "Organization"]
    avatar_url: str | None = None


class InstallationMetadata(BaseModel):
    account: GitHubAccount
    repository_selection: RepositorySelection
    permissions: dict[str, str] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=list)
    installed_at: datetime = Field(validation_alias=AliasChoices("installed_at", "created_at"))
    suspended_at: datetime | None = None


class GitHubInstallation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    installation_id: GitHubId
    status: InstallationStatus
    updated_at: datetime
    # A deletion or unavailable installation can be recorded before metadata is known.
    account: GitHubAccount | None = None
    repository_selection: RepositorySelection | None = None
    permissions: dict[str, str] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=list)
    installed_at: datetime | None = None
    suspended_at: datetime | None = None
    deleted_at: datetime | None = None
    sync_version: int | None = None
    repository_sync_version: int | None = None
