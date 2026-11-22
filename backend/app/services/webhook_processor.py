"""Background work for accepted GitHub deliveries and their recorded processing status."""

import logging
from collections.abc import Awaitable, Callable

from pymongo.errors import PyMongoError

from app.db.models.workflow_run import FailedJob, WorkflowRunMetadata
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
from app.services.webhook_errors import WebhookProcessingError, processing_error
from dependencies import get_github_collector

logger = logging.getLogger(__name__)


async def process_failed_workflow_run(
    delivery_id: str, event: GitHubWebhook, *, deliveries: WebhookDeliveryRepository | None = None,
    runs: WorkflowRunRepository | None = None, installations: GitHubInstallationRepository | None = None,
    repositories: GitHubRepositoryRepository | None = None, collector: GitHubCollector | None = None,
) -> None:
    """Persist a failed workflow attempt and collect its failing job and step metadata."""
    async def collect() -> None:
        await collect_workflow_run(
            event,
            runs if runs is not None else WorkflowRunRepository(),
            installations if installations is not None else GitHubInstallationRepository(),
            repositories if repositories is not None else GitHubRepositoryRepository(),
            collector if collector is not None else get_github_collector(),
        )

    await _process_delivery(delivery_id, event, collect, deliveries)


async def collect_workflow_run(
    event: GitHubWebhook, runs: WorkflowRunRepository, installations: GitHubInstallationRepository,
    repositories: GitHubRepositoryRepository, collector: GitHubCollector,
) -> None:
    """Collect metadata for the signed run attempt while respecting known access revocations."""
    run, repo = event.run, event.repository
    if run is None or repo is None:
        raise WebhookProcessingError("invalid_github_data")

    try:
        await runs.upsert_workflow_run(run)
        await runs.update_collection_status(run, "collecting")
        installation_id = run.installation_id
        if installation_id is None:
            installation_id = await get_installation_id(repo.owner, repo.name)
            run = WorkflowRunMetadata.model_validate({**run.model_dump(), "installation_id": installation_id})
            await runs.upsert_workflow_run(run)

        installation = await installations.get_by_installation_id(installation_id)
        if installation is not None and (
            installation.status != "active" or installation.sync_version != installation.repository_sync_version
        ):
            raise WebhookProcessingError("installation_unavailable")

        record = await repositories.get_by_repo_id(repo.github_repo_id)
        if record is not None and (
            not record.is_active or record.installation_id != installation_id
            or (installation is not None and record.sync_version != installation.repository_sync_version)
        ):
            raise WebhookProcessingError("repository_unavailable")

        token = await get_installation_token(installation_id)
        if not isinstance(token, str) or not token.strip():
            raise WebhookProcessingError("missing_installation_token")

        jobs = await collector.get_failed_jobs(
            owner=record.owner.login if record else repo.owner,
            repo=record.name if record else repo.name,
            run_id=run.run_id, token=token, run_attempt=run.run_attempt,
        )
        await runs.add_failed_jobs(run, [FailedJob.model_validate(job) for job in jobs])
        await runs.update_collection_status(run, "completed")
    except WorkflowRunInstallationConflict as exc:
        raise WebhookProcessingError("repository_unavailable") from exc
    except Exception:
        # This background boundary records incomplete collection before the delivery records the original error.
        try:
            await runs.update_collection_status(run, "failed")
        except PyMongoError:
            logger.error("Could not record workflow collection failure", extra=event.metadata.model_dump())
        raise


async def process_installation_event(
    delivery_id: str, event: GitHubWebhook, *, deliveries: WebhookDeliveryRepository | None = None,
    sync: GitHubSyncService | None = None,
) -> None:
    """Synchronize the installation affected by a signed lifecycle or repository event."""
    async def reconcile() -> None:
        service = sync if sync is not None else GitHubSyncService(
            GitHubInstallationRepository(), GitHubRepositoryRepository(),
        )
        await service.process_event(event)

    await _process_delivery(delivery_id, event, reconcile, deliveries)


async def _process_delivery(
    delivery_id: str, event: GitHubWebhook, work: Callable[[], Awaitable[None]],
    deliveries: WebhookDeliveryRepository | None,
) -> None:
    context = {"delivery_id": delivery_id, **event.metadata.model_dump()}
    try:
        deliveries = deliveries if deliveries is not None else WebhookDeliveryRepository()
        if not await deliveries.start_processing(delivery_id):
            return
    except (PyMongoError, RuntimeError):
        logger.error("Could not claim GitHub delivery", extra=context)
        return

    error = None
    try:
        await work()
    except Exception as exc:  # noqa: BLE001 - persist unexpected background failures as well
        # BackgroundTasks has no caller to handle failure; preserve a sanitized terminal state.
        error = processing_error(exc)
        logger.error("GitHub delivery processing failed", extra={**context, "error_code": error})

    try:
        await deliveries.finish_processing(delivery_id, error=error)
    except PyMongoError:
        logger.error("Could not persist GitHub delivery outcome", extra=context)
