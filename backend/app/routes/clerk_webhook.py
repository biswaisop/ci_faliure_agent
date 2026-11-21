
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.db.repositories import UserRepository

router = APIRouter(
    prefix="/webhooks",
    tags=["clerk-webhooks"],
)

logger = logging.getLogger(__name__)


def profile_from_clerk(
    data: dict[str, Any],
) -> dict[str, Any]:

    addresses = data.get("email_addresses") or []
    primary = data.get("primary_email_address_id")

    email = next(
        (
            item.get("email_address")
            for item in addresses
            if (
                isinstance(item, dict)
                and item.get("id") == primary
            )
        ),
        None,
    )

    first = data.get("first_name") or ""
    last = data.get("last_name") or ""

    return {
        "email": email,
        "name": " ".join(
            s for s in (first, last) if s
        ).strip() or None,
        "avatar_url": data.get("image_url"),
    }


@router.post("/clerk")
async def clerk_webhook(
    request: Request,
) -> dict[str, str]:

    secret = os.environ[
        "CLERK_WEBHOOK_SIGNING_SECRET"
    ]

    body = await request.body()

    # 1. Verify Clerk webhook signature
    try:
        Webhook(secret).verify(
            body,
            dict(request.headers),
        )

    except (
        WebhookVerificationError,
        ValueError,
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid Clerk webhook signature",
        )

    try:
        event = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Malformed Clerk event") from exc

    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Malformed Clerk event")

    # 2. Identify event
    event_type = event.get("type")

    if event_type not in {
        "user.created",
        "user.updated",
        "user.deleted",
    }:
        return {"status": "ignored"}

    data = event.get("data")
    timestamp = event.get("timestamp")

    # 3. Validate event data
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("id"), str)
        or not data["id"]
        or not isinstance(timestamp, int)
        or isinstance(timestamp, bool)
        or timestamp <= 0
    ):
        raise HTTPException(
            status_code=400,
            detail="Malformed Clerk event",
        )

    # 4. Synchronize with MongoDB
    repository = UserRepository()

    applied = await repository.apply_clerk_event(
        clerk_user_id=data["id"],
        event_type=event_type,
        event_timestamp_ms=timestamp,
        profile=(
            profile_from_clerk(data)
            if event_type != "user.deleted"
            else None
        ),
    )

    logger.info(
        "Clerk event %s for user %s: applied=%s",
        event_type,
        data["id"],
        applied,
    )

    return {
        "status": (
            "processed"
            if applied
            else "stale_or_duplicate"
        )
    }
