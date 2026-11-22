"""Validate signed GitHub deliveries and schedule eligible background work."""

import json
import logging
from dataclasses import dataclass
from typing import Annotated, Literal, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pymongo.errors import PyMongoError

from app.services.deduplicator import DeliveryDeduplicator, MongoDeliveryDeduplicator
from app.services.gh_auth import verify_signature
from app.services.github_webhook_event import (
    EVENT_ACTIONS,
    GitHubEvent,
    GitHubWebhook,
    parse_webhook,
)
from app.services.webhook_processor import (
    process_failed_workflow_run,
    process_installation_event,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github", tags=["webhook"])


@dataclass(frozen=True)
class VerifiedDelivery:
    delivery_id: str
    response: Literal["pong", "ignored", "accepted"]
    event: GitHubWebhook | None = None


async def read_github_webhook(request: Request) -> VerifiedDelivery:
    """Verify the raw payload and validate event-specific data before any database write."""
    delivery_id = request.headers.get("X-GitHub-Delivery")
    if not delivery_id:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery header")
    if len(delivery_id) > 128 or not all(char.isascii() and (char.isalnum() or char in "-_") for char in delivery_id):
        raise HTTPException(status_code=400, detail="Invalid X-GitHub-Delivery header")

    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256", "")):
        logger.warning("Invalid GitHub webhook signature", extra={"delivery_id": delivery_id})
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event_type = request.headers.get("X-GitHub-Event")
    if not event_type:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")
    if event_type == "ping":
        return VerifiedDelivery(delivery_id, "pong")
    if event_type not in EVENT_ACTIONS:
        return VerifiedDelivery(delivery_id, "ignored")

    try:
        payload = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="Malformed JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object")

    try:
        event = parse_webhook(cast(GitHubEvent, event_type), payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed payload: invalid GitHub event metadata") from exc
    if event is None:
        return VerifiedDelivery(delivery_id, "ignored")
    return VerifiedDelivery(delivery_id, "accepted", event)


VerifiedWebhook = Annotated[VerifiedDelivery, Depends(read_github_webhook)]


async def get_deduplicator(webhook: VerifiedWebhook) -> DeliveryDeduplicator | None:
    """Bind validated metadata to the recorder while preserving the single-argument interface."""
    return MongoDeliveryDeduplicator(webhook.event.metadata) if webhook.event else None


@router.post("/webhook")
async def github_webhooks(
    background_tasks: BackgroundTasks,
    webhook: VerifiedWebhook,
    deduplicator: Annotated[DeliveryDeduplicator | None, Depends(get_deduplicator)],
) -> dict[str, str]:
    """Record receipt atomically before scheduling any background task."""
    event = webhook.event
    if event is None:
        return {"status": webhook.response}
    delivery_id = webhook.delivery_id
    if deduplicator is None:
        raise HTTPException(status_code=503, detail="Could not record GitHub delivery")

    try:
        accepted = await deduplicator.acquire_or_record(delivery_id)
    except (PyMongoError, RuntimeError) as exc:
        logger.error(
            "Could not record GitHub delivery",
            extra={"delivery_id": delivery_id, **event.metadata.model_dump()},
        )
        raise HTTPException(status_code=503, detail="Could not record GitHub delivery") from exc

    if not accepted:
        return {"status": "duplicate", "delivery_id": delivery_id}

    if event.run is not None:
        background_tasks.add_task(process_failed_workflow_run, delivery_id, event)
    else:
        background_tasks.add_task(process_installation_event, delivery_id, event)
    return {"status": "accepted", "delivery_id": delivery_id}
