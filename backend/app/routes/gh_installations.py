import hashlib
import logging
import os
import re
import secrets
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from jwt import InvalidKeyError
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.core.clerk_auth import current_clerk_user_id
from app.db.mongodb import MongoDb
from app.db.repositories.github_installation_repository import (
    GitHubInstallationRepository,
)
from app.db.repositories.github_repository import GitHubRepositoryRepository
from app.db.repositories.membership_repository import MembershipRepository
from app.services.gh_installations import read_installations, verify_user_installations
from app.services.github_sync_service import GitHubSyncService
from app.services.webhook_errors import WebhookProcessingError
from config import settings

router = APIRouter(prefix="/api/github/installations", tags=["github-installations"])
logger = logging.getLogger(__name__)
ClerkUser = Annotated[str, Depends(current_clerk_user_id)]
InstallationId = Annotated[int, Field(gt=0, le=2**53 - 1)]
State = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]


class AuthorizeInstallation(BaseModel):
    installation_id: InstallationId | None = None


class CompleteInstallation(BaseModel):
    state: State
    code: Annotated[str, Field(min_length=1, max_length=512)]


class LinkInstallation(BaseModel):
    state: State
    installation_id: InstallationId


def callback_url() -> str:
    origin = os.getenv("FRONTEND_URL", "").rstrip("/")
    if not origin or not settings.GITHUB_APP_CLIENT_ID or not settings.GITHUB_APP_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="GitHub App setup is incomplete.")
    return f"{origin}/dashboard/github"


@router.get("")
async def list_installations(user_id: ClerkUser):
    slug = settings.GITHUB_APP_SLUG
    configured = (
        bool(re.fullmatch(r"[A-Za-z0-9-]+", slug))
        and bool(settings.GITHUB_APP_CLIENT_ID and settings.GITHUB_APP_CLIENT_SECRET)
        and bool(os.getenv("FRONTEND_URL"))
    )
    cursor = MongoDb.github_installations().find({"clerk_user_ids": user_id}, {"installation_id": 1})
    installation_ids = [item["installation_id"] async for item in cursor]
    try:
        installations = await read_installations(installation_ids)
    except (OSError, InvalidKeyError) as exc:
        logger.warning("Could not load GitHub App private key (%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="The GitHub App private key is unreadable or invalid.") from exc
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        reason = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        logger.warning("Could not check installations with GitHub (%s)", reason)
        raise HTTPException(status_code=502, detail="Could not check installations with GitHub.") from exc

    return {
        "install_url": f"https://github.com/apps/{slug}/installations/new" if configured else None,
        "installations": installations,
    }


@router.post("/authorize")
async def authorize_installation(body: AuthorizeInstallation, user_id: ClerkUser):
    redirect_uri = callback_url()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(32)
    challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    await MongoDb.get_collection("github_installation_states").insert_one({
        "_id": state,
        "clerk_user_id": user_id,
        "purpose": "authorize",
        "installation_id": body.installation_id,
        "verifier": verifier,
        "expires_at": datetime.now(UTC) + timedelta(minutes=10),
    })
    params = urlencode({
        "client_id": settings.GITHUB_APP_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return {"url": f"https://github.com/login/oauth/authorize?{params}"}


@router.post("/complete")
async def complete_installation(body: CompleteInstallation, user_id: ClerkUser):
    redirect_uri = callback_url()
    states = MongoDb.get_collection("github_installation_states")
    flow = await states.find_one_and_delete({
        "_id": body.state,
        "clerk_user_id": user_id,
        "purpose": "authorize",
        "expires_at": {"$gt": datetime.now(UTC)},
    })
    if flow is None:
        raise HTTPException(status_code=400, detail="GitHub authorization expired or was already used.")

    installation_id = flow["installation_id"]
    try:
        verified = await verify_user_installations(body.code, flow["verifier"], redirect_uri, installation_id)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="Could not verify this installation with GitHub.") from exc
    if installation_id is None:
        if not verified:
            return {"status": "empty"}
        state = secrets.token_urlsafe(32)
        await states.insert_one({
            "_id": state,
            "clerk_user_id": user_id,
            "purpose": "select",
            "installation_ids": [item["id"] for item in verified],
            "repository_scopes": {str(item["id"]): item["repository_ids"] for item in verified},
            "expires_at": datetime.now(UTC) + timedelta(minutes=10),
        })
        return {
            "status": "select", "state": state,
            "installations": [{key: value for key, value in item.items() if key != "repository_ids"} for item in verified],
        }
    if not verified:
        raise HTTPException(status_code=403, detail="This GitHub account cannot access the installation.")

    await save_installation(user_id, installation_id, verified[0]["repository_ids"])
    return {"status": "connected"}


@router.post("/link")
async def link_installation(body: LinkInstallation, user_id: ClerkUser):
    state = await MongoDb.get_collection("github_installation_states").find_one_and_delete({
        "_id": body.state,
        "clerk_user_id": user_id,
        "purpose": "select",
        "installation_ids": body.installation_id,
        "expires_at": {"$gt": datetime.now(UTC)},
    })
    if state is None:
        raise HTTPException(status_code=400, detail="GitHub installation selection expired or was already used.")
    scope = state.get("repository_scopes", {}).get(str(body.installation_id))
    if scope is None:
        raise HTTPException(status_code=400, detail="Restart GitHub authorization to verify repository access.")
    await save_installation(user_id, body.installation_id, scope)
    return {"status": "connected"}


async def save_installation(user_id: str, installation_id: int, repository_ids: list[int]) -> None:
    await ensure_installation_synced(installation_id)
    await MembershipRepository().sync_github_repository_grants(user_id, installation_id, repository_ids)
    installations = MongoDb.github_installations()
    selector = {"installation_id": installation_id}
    update = {"$addToSet": {"clerk_user_ids": user_id}}
    try:
        await installations.update_one(selector, update, upsert=True)
    except DuplicateKeyError:
        await installations.update_one(selector, update)


async def ensure_installation_synced(installation_id: int) -> None:
    installations = GitHubInstallationRepository()
    installation = await installations.get_by_installation_id(installation_id)
    if (
        installation and installation.status == "active" and installation.repository_sync_version is not None
        and installation.repository_sync_version == installation.sync_version
    ):
        return
    try:
        await GitHubSyncService(installations, GitHubRepositoryRepository()).reconcile_installation(installation_id)
    except (httpx.HTTPError, OSError, InvalidKeyError, ValueError, KeyError, WebhookProcessingError) as exc:
        logger.warning("Could not synchronize the GitHub installation (%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Could not synchronize repositories with GitHub.") from exc
