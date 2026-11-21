
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from app.core.clerk_auth import (
    current_clerk_user_id,
)

from app.db.repositories import (
    UserRepository
)

from app.schema import UserProfile


router = APIRouter(
    prefix="/api/users",
    tags=["users"],
)


@router.get(
    "/me",
    response_model=UserProfile,
)
async def get_me(
    clerk_user_id: Annotated[
        str,
        Depends(current_clerk_user_id),
    ],
):

    user = await UserRepository().get_by_clerk_id(
        clerk_user_id
    )

    if not user or user.get("status") != "active":
        raise HTTPException(
            status_code=404,
            detail="User profile not synchronized yet",
        )

    return user
