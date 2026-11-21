"""
GitHub Webhook Ingestion Router.

Receives GitHub webhook events, verifies HMAC-SHA256 signatures, deduplicates
deliveries using the injected deduplicator dependency, filters for failed CI workflow
runs, and dispatches background processing.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.services.deduplicator import DeliveryDeduplicator
from app.services.gh_auth import verify_signature
from app.services.webhook_processor import process_failed_workflow_run
from dependencies import get_deduplicator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["webhook"])

@router.post("/webhook")
async def github_webhooks(
    request: Request,
    background_tasks: BackgroundTasks,
    deduplicator: DeliveryDeduplicator = Depends(get_deduplicator),
) -> dict[str, Any]:
    """
    Handle GitHub webhook deliveries.

    1. Validate delivery ID.
    2. Verify webhook signature.
    3. Validate event type.
    4. Parse JSON payload.
    5. Filter failed workflow runs.
    6. Deduplicate delivery ID.
    7. Schedule background processing.
    8. Return HTTP 200.
    """

    # 1. Extract delivery ID
    delivery_id = request.headers.get("X-GitHub-Delivery")

    if not delivery_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Delivery header",
        )

    # 2. Read raw request body
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256")

    if not signature or not verify_signature(body, signature):
        logger.warning(
            "Invalid webhook signature: delivery_id=%s",
            delivery_id,
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # 3. Validate event type
    event = request.headers.get("X-GitHub-Event")

    if not event:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Event header",
        )

    if event == "ping":
        logger.info(
            "GitHub ping received: delivery_id=%s",
            delivery_id,
        )
        return {"status": "pong"}

    if event != "workflow_run":
        logger.info(
            "Ignoring GitHub event=%s delivery_id=%s",
            event,
            delivery_id,
        )
        return {"status": "ignored"}

    # 4. Parse JSON only AFTER signature verification
    try:
        payload = json.loads(body)

    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning(
            "Malformed JSON payload: delivery_id=%s",
            delivery_id,
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON payload",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload must be a JSON object",
        )

    # 5. Extract workflow run information
    action = payload.get("action")
    workflow_run = payload.get("workflow_run")

    if not isinstance(workflow_run, dict):
        workflow_run = {}

    conclusion = workflow_run.get("conclusion")

    logger.info(
        "GitHub event=%s action=%s conclusion=%s delivery_id=%s",
        event,
        action,
        conclusion,
        delivery_id,
    )

    # Only process completed, failed workflow runs
    if action != "completed" or conclusion != "failure":
        logger.info(
            "Ignoring workflow run: delivery_id=%s",
            delivery_id,
        )

        return {"status": "ignored"}

    # 6. Validate repository information
    repository = payload.get("repository")

    if not isinstance(repository, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing repository information",
        )

    repo_name = repository.get("name")
    owner_info = repository.get("owner")

    owner = (
        owner_info.get("login")
        if isinstance(owner_info, dict)
        else None
    )

    # Fallback to repository.full_name
    if not owner or not repo_name:
        full_name = repository.get("full_name")

        if isinstance(full_name, str) and "/" in full_name:
            owner, repo_name = full_name.split("/", 1)

    # 7. Extract run metadata
    run_id = workflow_run.get("id")
    workflow_id = workflow_run.get("workflow_id")
    head_sha = workflow_run.get("head_sha")
    head_branch = workflow_run.get("head_branch")
    run_attempt = workflow_run.get("run_attempt", 1)

    if (
        not owner
        or not repo_name
        or not run_id
        or not workflow_id
        or not head_sha
        or not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or not isinstance(workflow_id, int)
        or isinstance(workflow_id, bool)
        or not isinstance(head_sha, str)
        or not isinstance(run_attempt, int)
        or isinstance(run_attempt, bool)
        or run_attempt < 1
    ):
        logger.warning(
            "Invalid workflow metadata: delivery_id=%s",
            delivery_id,
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing or invalid workflow run identifiers",
        )

    # Installation ID from webhook payload
    installation = payload.get("installation")

    installation_id = (
        installation.get("id")
        if isinstance(installation, dict)
        else None
    )

    # 8. Deduplicate after signature and payload validation
    is_new = await deduplicator.acquire_or_record(delivery_id)

    if not is_new:
        logger.info(
            "Duplicate webhook ignored: delivery_id=%s",
            delivery_id,
        )

        return {
            "status": "duplicate",
            "delivery_id": delivery_id,
        }

    # 9. Schedule background processing
    background_tasks.add_task(
        process_failed_workflow_run,
        delivery_id=delivery_id,
        owner=owner,
        repo=repo_name,
        run_id=run_id,
        workflow_id=workflow_id,
        head_sha=head_sha,
        head_branch=head_branch,
        run_attempt=run_attempt,
        conclusion=conclusion,
        installation_id=installation_id,
    )

    logger.warning(
        "Background task scheduled: repo=%s/%s run_id=%s delivery_id=%s",
        owner,
        repo_name,
        run_id,
        delivery_id,
    )

    # 10. Acknowledge immediately
    return {
        "status": "accepted",
        "delivery_id": delivery_id,
    }
