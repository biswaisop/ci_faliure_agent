import os
from typing import Annotated

from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

load_dotenv()

bearer = HTTPBearer(auto_error=False)

def current_clerk_user_id(
    request: Request,
    _credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer)
    ],
) -> str:
    state = authenticate_request(
        request,
        AuthenticateRequestOptions(
            secret_key=os.getenv("CLERK_SECRET_KEY"),
            jwt_key=os.getenv("CLERK_JWT_KEY") or None,
            authorized_parties=[
                os.getenv("FRONTEND_URL")
            ],
            accepts_token=["session_token"]
        )
    )

    if (
        not state.is_signed_in
        or not state.payload
        or not state.payload.get("sub")
    ):
        raise HTTPException(
            status_code = 401,
            detail = "unauthorized"
        )

    return str(state.payload["sub"])
