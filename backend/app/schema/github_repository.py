from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.schema.github_installation import GitHubAccount, GitHubId


class RepositoryMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    github_repo_id: GitHubId = Field(validation_alias=AliasChoices("github_repo_id", "id"))
    owner: GitHubAccount
    name: str = Field(min_length=1)
    full_name: str = Field(min_length=3)
    default_branch: str = Field(min_length=1)
    private: bool
    html_url: str = Field(min_length=1)


class GitHubRepository(RepositoryMetadata):
    installation_id: GitHubId
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_synced_at: datetime
    sync_version: int
