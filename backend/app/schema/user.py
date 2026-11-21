from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserPreferences(BaseModel):
    email_notifications: bool = True
    slack_notifications: bool = True

class UserProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clerk_user_id: str
    email: str | None = None
    name: str | None = None
    avatar_url: str | None = None

    role: Literal["user", "admin"] = "user"
    status: Literal["active", "deleted"] = "active"

    preferences: UserPreferences = Field(
        default_factory=UserPreferences
    )

    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
