from typing import Protocol

from app.db.models.github_installation import GitHubInstallation
from app.db.models.github_repository import GitHubRepository
from app.db.models.membership import (
    ResourceType,
    UserMembership,
    VerifiedMembershipGrant,
)
from app.db.models.workflow_run import WorkflowRunMetadata
from app.db.repositories.github_installation_repository import (
    GitHubInstallationRepository,
)
from app.db.repositories.github_repository import GitHubRepositoryRepository
from app.db.repositories.membership_repository import MembershipRepository
from app.db.repositories.user_repositories import UserRepository


class MembershipVerifier(Protocol):
    """Server-side verification boundary for linked GitHub identities or authorized admins."""

    async def verify_membership(
        self, clerk_user_id: str, resource_type: ResourceType, resource_id: int,
    ) -> VerifiedMembershipGrant | None: ...


class GitHubAuthorizationService:
    def __init__(
        self, users: UserRepository, memberships: MembershipRepository,
        installations: GitHubInstallationRepository, repositories: GitHubRepositoryRepository,
    ) -> None:
        self.users = users
        self.memberships = memberships
        self.installations = installations
        self.repositories = repositories

    async def grant_membership(
        self, clerk_user_id: str, resource_type: ResourceType, resource_id: int, *, verifier: MembershipVerifier,
    ) -> bool:
        """Persist an independently verified grant for an active Clerk user and resource."""
        if not await self._active_user(clerk_user_id):
            return False
        grant = await verifier.verify_membership(clerk_user_id, resource_type, resource_id)
        if grant is None or (grant.clerk_user_id, grant.resource_type, grant.resource_id) != (
            clerk_user_id, resource_type, resource_id,
        ):
            return False

        installation = await self.installations.get_by_installation_id(grant.installation_id)
        if not self._active_installation(installation):
            return False
        if resource_type == "installation":
            if resource_id != grant.installation_id:
                return False
        else:
            repo = await self.repositories.get_by_repo_id(resource_id)
            if repo is None or not self._active_repository(repo, installation):
                return False

        await self.memberships.grant_membership(grant)
        return True

    async def can_access_repository(
        self, clerk_user_id: str, github_repo_id: int, *, installation_id: int | None = None,
    ) -> bool:
        """Require an active Clerk user, installation, repository, and scoped grant."""
        if not await self._active_user(clerk_user_id):
            return False
        repo = await self.repositories.get_by_repo_id(github_repo_id)
        if repo is None or not repo.is_active or (installation_id is not None and repo.installation_id != installation_id):
            return False
        grants = await self.memberships.get_user_memberships(clerk_user_id, status="active")
        installation = await self.installations.get_by_installation_id(repo.installation_id)
        return self._active_repository(repo, installation) and self._has_grant(repo, grants)

    async def can_access_workflow_run(self, clerk_user_id: str, run: WorkflowRunMetadata) -> bool:
        """Check a run against its original installation even after a repository transfer."""
        return run.installation_id is not None and await self.can_access_repository(
            clerk_user_id, run.github_repo_id, installation_id=run.installation_id,
        )

    async def list_authorized_repositories(self, clerk_user_id: str) -> list[GitHubRepository]:
        """List repositories that satisfy the same policy as an individual access check."""
        if not await self._active_user(clerk_user_id):
            return []
        grants = await self.memberships.get_user_memberships(clerk_user_id, status="active")
        candidates = await self.memberships.list_repository_candidates(grants, self.repositories)
        installations = {
            installation_id: await self.installations.get_by_installation_id(installation_id)
            for installation_id in {repo.installation_id for repo in candidates}
        }
        return [
            repo for repo in candidates
            if self._active_repository(repo, installations[repo.installation_id]) and self._has_grant(repo, grants)
        ]

    async def _active_user(self, clerk_user_id: str) -> bool:
        user = await self.users.get_by_clerk_id(clerk_user_id)
        return user is not None and user.get("status") == "active"

    @staticmethod
    def _active_installation(installation: GitHubInstallation | None) -> bool:
        return (
            installation is not None and installation.status == "active"
            and installation.sync_version is not None
            and installation.sync_version == installation.repository_sync_version
        )

    @classmethod
    def _active_repository(cls, repo: GitHubRepository, installation: GitHubInstallation | None) -> bool:
        return (
            installation is not None and cls._active_installation(installation)
            and repo.is_active and repo.installation_id == installation.installation_id
            and repo.sync_version == installation.repository_sync_version
        )

    @staticmethod
    def _has_grant(repo: GitHubRepository, grants: list[UserMembership]) -> bool:
        return any(
            grant.status == "active" and grant.installation_id == repo.installation_id
            and (
                (grant.resource_type == "installation" and grant.resource_id == repo.installation_id)
                or (grant.resource_type == "repository" and grant.resource_id == repo.github_repo_id)
            )
            for grant in grants
        )
