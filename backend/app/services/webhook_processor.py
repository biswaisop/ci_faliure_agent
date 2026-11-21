"""
Background Webhook Processor Service.

Processes validated GitHub webhook events asynchronously outside the request lifecycle.

TASK QUEUE CHARACTERISTICS AND LIMITATIONS:
- FastAPI's BackgroundTasks runs within the same ASGI process as the web server.
- It is NOT a durable task queue: if the server process restarts, crashes, or is terminated
  while a background task is executing or queued, the task is lost.
- An accepted delivery is therefore NOT guaranteed to finish processing.
- Accepted delivery IDs are intentionally NOT removed automatically upon failure; doing so
  would introduce inconsistent retry behavior and duplicate processing races.
- Automatic retries are not implemented at this stage.
- In production, this background processor function can be dispatched to a durable distributed
  task queue (e.g., Redis Queue, Celery, or Arq) with persistent message broker and dead-letter
  handling, without altering webhook ingestion or validation logic.
"""

import logging
from typing import Any

from app.services.gh_auth import get_installation_id, get_installation_token
from app.services.gh_collector import GitHubCollector
from dependencies import get_github_collector

logger = logging.getLogger(__name__)


async def process_failed_workflow_run(
    delivery_id: str,
    owner: str,
    repo: str,
    run_id: int,
    workflow_id: int | str,
    head_sha: str,
    head_branch: str,
    run_attempt: int,
    conclusion: str,
    installation_id: int | None = None,
    collector: GitHubCollector | None = None,
) -> None:
    """
    Background worker function for failed GitHub Actions workflow runs.

    Coordinates initial inspection and failed job collection without blocking the webhook
    response. Catches and logs all exceptions with full traceback context to ensure the ASGI
    application remains stable and uncompromised.
    """
    if collector is None:
        collector = get_github_collector()

    try:
        # 1. Log that processing has started
        logger.info(
            "Background processing started for delivery_id=%s [run_id=%s, repo=%s/%s]",
            delivery_id,
            run_id,
            owner,
            repo,
        )

        # 2. Log repository, workflow run ID, and delivery ID along with run context
        logger.info(
            "Run details: delivery_id=%s, repo=%s/%s, run_id=%s, workflow_id=%s, head_sha=%s, branch=%s, attempt=%s, conclusion=%s",
            delivery_id,
            owner,
            repo,
            run_id,
            workflow_id,
            head_sha,
            head_branch,
            run_attempt,
            conclusion,
        )

        # 3. Optionally call collector.get_failed_jobs() if token acquisition is supported
        token: str | None = None
        try:
            if installation_id:
                token = await get_installation_token(installation_id)
            else:
                inst_id = await get_installation_id(owner, repo)
                token = await get_installation_token(inst_id)
        except Exception as auth_err:
            logger.warning(
                "Skipping GitHub API collection for delivery_id=%s; unable to acquire installation token for %s/%s: %s",
                delivery_id,
                owner,
                repo,
                auth_err,
            )

        if token:
            failed_jobs = await collector.get_failed_jobs(
                owner=owner,
                repo=repo,
                run_id=run_id,
                token=token,
            )
            # 4. Log the number of failed jobs discovered
            logger.info(
                "Discovered %d failed job(s) for run_id=%s in %s/%s (delivery_id=%s)",
                len(failed_jobs),
                run_id,
                owner,
                repo,
                delivery_id,
            )
        else:
            logger.info(
                "Token unavailable for delivery_id=%s; failed job collection bypassed",
                delivery_id,
            )

        logger.info(
            "Background processing completed successfully for delivery_id=%s [run_id=%s]",
            delivery_id,
            run_id,
        )

    except Exception:
        # 5. Catch and log processing exceptions with traceback information
        # 6. Avoid crashing ASGI application
        logger.exception(
            "Unhandled exception during background processing for delivery_id=%s (repo=%s/%s, run_id=%s)",
            delivery_id,
            owner,
            repo,
            run_id,
        )
