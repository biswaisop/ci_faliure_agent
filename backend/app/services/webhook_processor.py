"""
Background work for accepted GitHub deliveries and their recorded processing status.
"""

import logging
from collections.abc import Awaitable, Callable

from app.db.models.workflow_run import (
    EvidenceUpdate,
    FailedJob,
    WorkflowRunMetadata,
)
from app.db.repositories.github_installation_repository import (
    GitHubInstallationRepository,
)
from app.db.repositories.github_repository import GitHubRepositoryRepository
from app.db.repositories.webhook_delivery_repository import WebhookDeliveryRepository
from app.db.repositories.workflow_run_repository import (
    WorkflowRunInstallationConflict,
    WorkflowRunRepository,
)
from app.services.gh_auth import get_installation_id, get_installation_token
from app.services.gh_collector import GitHubCollector
from app.services.github_sync_service import GitHubSyncService
from app.services.github_webhook_event import GitHubWebhook
from app.services.investigation_context_builder import build_investigation_context
from app.services.log_reducer import reduce_job_log
from app.services.webhook_errors import WebhookProcessingError, processing_error
from dependencies import get_github_collector
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)


async def process_failed_workflow_run(
    delivery_id: str,
    event: GitHubWebhook,
    *,
    deliveries: WebhookDeliveryRepository | None = None,
    runs: WorkflowRunRepository | None = None,
    installations: GitHubInstallationRepository | None = None,
    repositories: GitHubRepositoryRepository | None = None,
    collector: GitHubCollector | None = None,
) -> None:
    """
    Persist a failed workflow attempt and collect its failing job and step metadata.

    Wraps the collection logic in the delivery claiming lifecycle so that
    delivery status and processing outcomes are atomically recorded.
    """
    async def collect() -> None:
        await collect_workflow_run(
            event,
            runs=runs if runs is not None else WorkflowRunRepository(),
            installations=installations if installations is not None else GitHubInstallationRepository(),
            repositories=repositories if repositories is not None else GitHubRepositoryRepository(),
            collector=collector if collector is not None else get_github_collector(),
        )

    await _process_delivery(delivery_id, event, collect, deliveries)


async def collect_workflow_run(
    event: GitHubWebhook,
    runs: WorkflowRunRepository,
    installations: GitHubInstallationRepository,
    repositories: GitHubRepositoryRepository,
    collector: GitHubCollector,
) -> None:
    """
    Collect failed workflow evidence, reduce logs, and persist failure packets.

    Pipeline:
    1. Persist initial workflow run metadata.
    2. Resolve and validate installation and repository access.
    3. Mint a scoped GitHub installation access token.
    4. Fetch failed jobs and steps for the run attempt.
    5. Download and deterministically reduce logs into FailurePackets.
    6. Find the previous passing baseline run and fetch the commit diff.
    7. Build the unified InvestigationContext with file correlations.
    8. Persist failure packets and investigation context to MongoDB.
    9. Mark evidence collection status as completed.
    """
    run, repo = event.run, event.repository
    if run is None or repo is None:
        raise WebhookProcessingError("invalid_github_data")

    try:
        # 1. Persist workflow metadata and mark collection in-progress
        await runs.upsert_workflow_run(run)
        await runs.update_collection_status(run, "collecting")

        # 2. Resolve installation ID if not present in webhook payload
        installation_id = run.installation_id
        if installation_id is None:
            installation_id = await get_installation_id(repo.owner, repo.name)
            run = WorkflowRunMetadata.model_validate({
                **run.model_dump(),
                "installation_id": installation_id,
            })
            await runs.upsert_workflow_run(run)

        # 3. Validate installation access and synchronization state
        installation = await installations.get_by_installation_id(installation_id)
        if installation is not None and (
            installation.status != "active"
            or installation.sync_version != installation.repository_sync_version
        ):
            raise WebhookProcessingError("installation_unavailable")

        # 4. Validate repository access and active status
        record = await repositories.get_by_repo_id(repo.github_repo_id)
        if record is not None and (
            not record.is_active
            or record.installation_id != installation_id
            or (
                installation is not None
                and record.sync_version != installation.repository_sync_version
            )
        ):
            raise WebhookProcessingError("repository_unavailable")

        # 5. Acquire scoped GitHub App installation access token
        token = await get_installation_token(installation_id)
        if not isinstance(token, str) or not token.strip():
            raise WebhookProcessingError("missing_installation_token")

        owner = record.owner.login if record is not None else repo.owner
        repo_name = record.name if record is not None else repo.name

        # 6. Fetch failed jobs and steps for this specific run attempt
        jobs = await collector.get_failed_jobs(
            owner=owner,
            repo=repo_name,
            run_id=run.run_id,
            token=token,
            run_attempt=run.run_attempt,
        )
        logger.info(
            "Fetched %d failed jobs: run_id=%s attempt=%s",
            len(jobs),
            run.run_id,
            run.run_attempt,
        )

        # 7. Persist failed job metadata
        await runs.add_failed_jobs(
            run,
            [FailedJob.model_validate(job) for job in jobs],
        )

        # 8. Fetch raw logs and reduce each job to a compact failure packet
        failure_packets = []
        for job in jobs:
            job_id = job["id"]
            job_name = job["name"]

            try:
                raw_log = await collector.get_job_log(
                    owner=owner,
                    repo=repo_name,
                    job_id=job_id,
                    token=token,
                )
            except Exception:
                logger.exception(
                    "Failed to fetch job log: run_id=%s attempt=%s job_id=%s",
                    run.run_id,
                    run.run_attempt,
                    job_id,
                )
                # Preserve job in evidence even if log download failed
                raw_log = None

            # Identify failed steps within the job
            failed_steps = job.get("failed_steps")
            if failed_steps is None:
                failed_steps = [
                    step
                    for step in job.get("steps", [])
                    if step.get("conclusion") == "failure"
                ]

            failed_step_name = failed_steps[0].get("name") if failed_steps else None

            # Extract deterministic failure packet
            packet = reduce_job_log(
                raw_log=raw_log,
                job_id=job_id,
                job_name=job_name,
                failed_step=failed_step_name,
            )
            failure_packets.append(packet)

            logger.info(
                "Failure packet extracted: run_id=%s job_id=%s error_type=%s status=%s",
                run.run_id,
                job_id,
                packet.error_type,
                packet.extraction_status,
            )

        # 9. Retrieve baseline passing run to identify commit diff range
        last_success = await collector.get_last_passing_run(
            owner=owner,
            repo=repo_name,
            workflow_id=run.workflow_id,
            branch=run.head_branch,
            before_run=run.run_id,
            token=token,
        )
        base_sha = last_success["head_sha"] if last_success else None

        # Fetch commit diff if a baseline commit exists and differs from head
        diff = None
        if base_sha and base_sha != run.head_sha:
            diff = await collector.get_commit_diff(
                owner=owner,
                repo=repo_name,
                base=base_sha,
                head=run.head_sha,
                token=token,
            )

        # 10. Assemble failure packets, diff changes, and file correlations
        context = build_investigation_context(
            github_repo_id=repo.github_repo_id,
            run_id=run.run_id,
            run_attempt=run.run_attempt,
            workflow_id=run.workflow_id,
            head_sha=run.head_sha,
            head_branch=run.head_branch,
            failure_packets=failure_packets,
            base_sha=base_sha,
            diff=diff,
        )
        logger.info(
            "Investigation context built: run_id=%s changed_files=%d correlations=%d",
            run.run_id,
            len(context.changed_files),
            len(context.correlations),
        )

        # 11. Persist failure packets and investigation context in MongoDB
        await runs.update_evidence(
            run,
            EvidenceUpdate(
                failure_packets=failure_packets,
                investigation_context=context,
            ),
        )

        # 12. Mark evidence collection as completed
        await runs.update_collection_status(run, "completed")
        logger.info(
            "Workflow evidence collection completed: run_id=%s attempt=%s packets=%d",
            run.run_id,
            run.run_attempt,
            len(failure_packets),
        )

    except WorkflowRunInstallationConflict as exc:
        # Installation mismatch should not be silently ignored
        try:
            await runs.update_collection_status(run, "failed")
        except PyMongoError:
            logger.exception(
                "Could not persist failed collection status: run_id=%s",
                run.run_id,
            )
        raise WebhookProcessingError("repository_unavailable") from exc

    except Exception:
        # Preserve consistent failure state in MongoDB
        try:
            await runs.update_collection_status(run, "failed")
        except PyMongoError:
            logger.exception(
                "Could not record workflow collection failure: run_id=%s",
                run.run_id,
            )
        logger.exception(
            "Workflow collection failed: run_id=%s attempt=%s",
            run.run_id,
            run.run_attempt,
        )
        raise


async def process_installation_event(
    delivery_id: str,
    event: GitHubWebhook,
    *,
    deliveries: WebhookDeliveryRepository | None = None,
    sync: GitHubSyncService | None = None,
) -> None:
    """
    Synchronize the installation affected by a signed lifecycle or repository event.

    Reconciles GitHubApp installation states, repository additions, transfers,
    and deletions with MongoDB.
    """
    async def reconcile() -> None:
        service = sync if sync is not None else GitHubSyncService(
            GitHubInstallationRepository(),
            GitHubRepositoryRepository(),
        )
        await service.process_event(event)

    await _process_delivery(delivery_id, event, reconcile, deliveries)


async def _process_delivery(
    delivery_id: str,
    event: GitHubWebhook,
    work: Callable[[], Awaitable[None]],
    deliveries: WebhookDeliveryRepository | None = None,
) -> None:
    """
    Atomically claims, executes, and finalizes a GitHub delivery.

    Guarantees delivery status transitions (pending -> processing -> completed/failed)
    and sanitizes error codes on background failures.
    """
    context = {"delivery_id": delivery_id, **event.metadata.model_dump()}

    # Attempt to claim the delivery atomically in MongoDB
    try:
        deliveries = deliveries if deliveries is not None else WebhookDeliveryRepository()
        if not await deliveries.start_processing(delivery_id):
            return
    except (PyMongoError, RuntimeError):
        logger.error("Could not claim GitHub delivery", extra=context)
        return

    # Execute the background task
    error = None
    try:
        await work()
    except Exception as exc:  # noqa: BLE001 - persist unexpected background failures as well
        error = processing_error(exc)
        logger.error("GitHub delivery processing failed", extra={**context, "error_code": error})

    # Finalize delivery record in MongoDB
    try:
        await deliveries.finish_processing(delivery_id, error=error)
    except PyMongoError:
        logger.error("Could not persist GitHub delivery outcome", extra=context)
