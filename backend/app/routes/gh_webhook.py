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
    Handle incoming GitHub webhook deliveries.

    Lifecycle:
    1. Extract and validate delivery ID from header (HTTP 400 if missing).
    2. Read raw payload bytes and verify HMAC-SHA256 signature (HTTP 401 if invalid).
    3. Filter by event type: handle 'ping', ignore non-'workflow_run' events (HTTP 200).
    4. Parse JSON payload and check eligibility (action == 'completed' and conclusion == 'failure').
    5. Validate presence of required repository and workflow run identifiers (HTTP 400 if malformed).
    6. Deduplicate delivery ID via injected deduplicator under concurrency lock; return HTTP 200 if duplicate.
    7. Schedule background processing via FastAPI BackgroundTasks and acknowledge with HTTP 200.
    """
    # 1. Extract delivery ID
    delivery_id = request.headers.get("X-GitHub-Delivery")
    if not delivery_id:
        logger.warning("Rejected webhook request: missing X-GitHub-Delivery header")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Delivery header",
        )

    # 2. Read raw body and verify HMAC-SHA256 signature
    payload = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not signature or not verify_signature(payload, signature):
        logger.warning(
            "Rejected webhook request delivery_id=%s: invalid or missing signature",
            delivery_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # 3. Check GitHub event header
    event = request.headers.get("X-GitHub-Event")
    if not event:
        logger.warning(
            "Rejected webhook request delivery_id=%s: missing X-GitHub-Event header",
            delivery_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Event header",
        )

    if event == "ping":
        logger.info("Received valid ping event for delivery_id=%s", delivery_id)
        return {"status": "pong"}

    if event != "workflow_run":
        logger.debug(
            "Ignored non-workflow_run event '%s' for delivery_id=%s",
            event,
            delivery_id,
        )
        return {"status": "ignored"}

    # 4. Parse JSON payload
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning(
            "Rejected webhook request delivery_id=%s: unparseable JSON payload",
            delivery_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON payload",
        )

    if not isinstance(data, dict):
        logger.warning(
            "Rejected webhook request delivery_id=%s: payload root is not an object",
            delivery_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON payload",
        )

    action = data.get("action")
    workflow_run = data.get("workflow_run")

    # Filter: process only workflow_run where action == 'completed' and conclusion == 'failure'
    if (
        action != "completed"
        or not isinstance(workflow_run, dict)
        or workflow_run.get("conclusion") != "failure"
    ):
        logger.debug(
            "Ignored workflow_run event for delivery_id=%s: action=%s, conclusion=%s",
            delivery_id,
            action,
            workflow_run.get("conclusion") if isinstance(workflow_run, dict) else None,
        )
        return {"status": "ignored"}

    # 5. Validate required identifiers in repository and workflow_run
    repository = data.get("repository")
    if not isinstance(repository, dict):
        logger.warning(
            "Rejected eligible webhook delivery_id=%s: missing or invalid repository object",
            delivery_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed payload: missing repository object",
        )

    repo_name = repository.get("name")
    owner_info = repository.get("owner")
    owner = owner_info.get("login") if isinstance(owner_info, dict) else None

    # Fallback to repository.full_name if owner login or name not extracted directly
    if (not owner or not repo_name) and "full_name" in repository:
        full_name = repository.get("full_name", "")
        if "/" in full_name:
            owner, repo_name = full_name.split("/", 1)

    run_id = workflow_run.get("id")
    workflow_id = workflow_run.get("workflow_id")
    head_sha = workflow_run.get("head_sha")
    head_branch = workflow_run.get("head_branch")
    run_attempt = workflow_run.get("run_attempt", 1)
    conclusion = workflow_run.get("conclusion")

    if not owner or not repo_name or not run_id or not workflow_id or not head_sha:
        logger.warning(
            "Rejected eligible webhook delivery_id=%s: missing required fields (owner=%s, repo=%s, run_id=%s, workflow_id=%s, head_sha=%s)",
            delivery_id,
            owner,
            repo_name,
            run_id,
            workflow_id,
            head_sha,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed payload: missing required workflow_run or repository identifiers",
        )

    installation = data.get("installation")
    installation_id = installation.get("id") if isinstance(installation, dict) else None

    # 6. Deduplication check-and-insert (only for validated eligible events)
    is_new = await deduplicator.acquire_or_record(delivery_id)
    if not is_new:
        logger.info(
            "Duplicate delivery ID detected and ignored: delivery_id=%s",
            delivery_id,
        )
        return {
            "status": "duplicate",
            "delivery_id": delivery_id,
        }

    # 7. Schedule background processing and return prompt acknowledgment
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

    logger.info(
        "Accepted eligible failed workflow run for delivery_id=%s (repo=%s/%s, run_id=%s)",
        delivery_id,
        owner,
        repo_name,
        run_id,
    )

    return {
        "status": "accepted",
        "delivery_id": delivery_id,
    }
