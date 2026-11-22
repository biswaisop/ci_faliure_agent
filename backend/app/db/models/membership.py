from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.github_installation import GitHubId

ResourceType = Literal["installation", "repository"]
MembershipStatus = Literal["active", "suspended", "revoked"]


class VerifiedMembershipGrant(BaseModel):
    """Input for trusted server-side GitHub verification or authorized administration."""

    model_config = ConfigDict(extra="forbid")

    clerk_user_id: str = Field(min_length=1)
    resource_type: ResourceType
    resource_id: GitHubId
    # Bind a repository grant to the installation under which it was verified.
    installation_id: GitHubId
    role: Literal["viewer", "admin"] = "viewer"
    verification_source: Literal["github", "admin"]
    verified_at: datetime


class UserMembership(VerifiedMembershipGrant):
    model_config = ConfigDict(extra="ignore")

    status: MembershipStatus
    created_at: datetime
    updated_at: datetime
