from pymongo.errors import DuplicateKeyError

from app.db.models.github_repository import GitHubRepository
from app.db.models.membership import (
    MembershipStatus,
    ResourceType,
    UserMembership,
    VerifiedMembershipGrant,
)
from app.db.mongodb import MongoDb
from app.db.repositories.github_repository import GitHubRepositoryRepository
from app.db.repositories.user_repositories import now_utc


class MembershipRepository:
    def __init__(self, collection=None):
        self.collection = collection if collection is not None else MongoDb.get_collection("user_memberships")

    @staticmethod
    def _key(clerk_user_id: str, resource_type: ResourceType, resource_id: int) -> dict:
        return {"clerk_user_id": clerk_user_id, "resource_type": resource_type, "resource_id": resource_id}

    async def grant_membership(self, grant: VerifiedMembershipGrant) -> None:
        """Persist a grant already verified by the authorization service."""
        selector = self._key(grant.clerk_user_id, grant.resource_type, grant.resource_id)
        now = now_utc()
        update = {
            "$set": {**grant.model_dump(), "status": "active", "updated_at": now},
            "$setOnInsert": {"created_at": now},
        }
        try:
            await self.collection.update_one(selector, update, upsert=True)
        except DuplicateKeyError:
            await self.collection.update_one(selector, update)

    async def get_user_memberships(
        self, clerk_user_id: str, *, status: MembershipStatus | None = None,
    ) -> list[UserMembership]:
        """List a Clerk user's memberships, optionally constrained by status."""
        query = {"clerk_user_id": clerk_user_id}
        if status is not None:
            query["status"] = status
        return [UserMembership.model_validate(document) async for document in self.collection.find(query)]

    async def get_membership(
        self, clerk_user_id: str, resource_type: ResourceType, resource_id: int,
    ) -> UserMembership | None:
        """Look up a membership for a specific resource."""
        document = await self.collection.find_one(self._key(clerk_user_id, resource_type, resource_id))
        return UserMembership.model_validate(document) if document else None

    async def revoke_membership(self, clerk_user_id: str, resource_type: ResourceType, resource_id: int) -> None:
        """Revoke an existing grant while retaining its audit metadata."""
        await self.collection.update_one(
            self._key(clerk_user_id, resource_type, resource_id),
            {"$set": {"status": "revoked", "updated_at": now_utc()}},
        )

    async def list_repository_candidates(
        self, memberships: list[UserMembership], repositories: GitHubRepositoryRepository,
    ) -> list[GitHubRepository]:
        """Select repository candidates for already verified active membership scopes."""
        installation_ids = [
            grant.resource_id for grant in memberships
            if grant.status == "active" and grant.resource_type == "installation"
        ]
        repository_ids = [
            grant.resource_id for grant in memberships
            if grant.status == "active" and grant.resource_type == "repository"
        ]
        if not installation_ids and not repository_ids:
            return []
        return await repositories.list_active_repositories(
            installation_ids=tuple(installation_ids), repository_ids=tuple(repository_ids),
        )
