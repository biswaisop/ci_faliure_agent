from time import time_ns

import httpx

from app.db.repositories.github_installation_repository import (
    GitHubInstallationRepository,
)
from app.db.repositories.github_repository import GitHubRepositoryRepository
from app.schema.github_installation import InstallationMetadata
from app.schema.github_repository import RepositoryMetadata
from app.services.gh_auth import (
    generate_app_jwt,
    get_installation_id,
    get_installation_token,
)
from app.services.gh_installations import (
    github_headers,
    read_installation,
    read_installation_repositories,
)
from app.services.github_webhook_event import GitHubWebhook
from app.services.webhook_errors import WebhookProcessingError


class GitHubSyncService:
    def __init__(
        self, installations: GitHubInstallationRepository, repositories: GitHubRepositoryRepository,
    ) -> None:
        self.installations = installations
        self.repositories = repositories

    async def process_event(self, event: GitHubWebhook) -> None:
        """Apply a signed deletion or reconcile affected installations from current GitHub state."""
        installation_id = event.metadata.installation_id
        if installation_id is None:
            raise WebhookProcessingError("installation_unavailable")
        if event.metadata.event_type == "installation" and event.metadata.action == "deleted":
            version = time_ns()
            await self.installations.set_installation_status(installation_id, "deleted", version=version)
            await self.repositories.deactivate_unsynced(installation_id, version=version)
            return

        await self.reconcile_installation(installation_id)
        if event.metadata.action == "transferred" and event.repository:
            try:
                destination = await get_installation_id(event.repository.owner, event.repository.name)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
            else:
                if destination != installation_id:
                    await self.reconcile_installation(destination)

    async def reconcile_installation(self, installation_id: int) -> None:
        """Refresh installation and repository state, publishing access only after a full snapshot."""
        # shortcut: fences use synchronized host clocks, replace with a MongoDB sequence if hosts can drift.
        version = time_ns()
        if not await self.installations.begin_sync(installation_id, version):
            return

        async with httpx.AsyncClient(timeout=10) as client:
            data = await read_installation(client, installation_id, github_headers(generate_app_jwt()))
        if data is None:
            await self.installations.set_installation_status(installation_id, "unavailable", version=version)
            await self.repositories.deactivate_unsynced(installation_id, version=version)
            return

        metadata = InstallationMetadata.model_validate(data)
        if not await self.installations.upsert_installation(installation_id, metadata, version=version):
            return
        if metadata.suspended_at:
            return

        token = await get_installation_token(installation_id)
        if not isinstance(token, str) or not token.strip():
            raise WebhookProcessingError("missing_installation_token")
        repositories = [
            RepositoryMetadata.model_validate(repository)
            for repository in await read_installation_repositories(token)
        ]
        for repository in repositories:
            await self.repositories.upsert_repository(installation_id, repository, version=version)
        await self.repositories.deactivate_unsynced(installation_id, version=version)
        await self.installations.finish_sync(installation_id, version)
