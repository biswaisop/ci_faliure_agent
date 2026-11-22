from pymongo.collation import Collation
from pymongo.errors import DuplicateKeyError

from app.db.models.github_repository import GitHubRepository, RepositoryMetadata
from app.db.mongodb import MongoDb
from app.db.repositories.user_repositories import now_utc

FULL_NAME_COLLATION = Collation(locale="en", strength=2)


class GitHubRepositoryRepository:
    def __init__(self, collection=None):
        self.collection = collection if collection is not None else MongoDb.github_repositories()

    async def upsert_repository(
        self, installation_id: int, metadata: RepositoryMetadata, *, version: int,
    ) -> None:
        """Upsert by numeric ID and reject older observations, including before a transfer."""
        now = now_utc()
        selector = {
            "github_repo_id": metadata.github_repo_id,
            "$and": [
                {"$or": [{"sync_version": {"$lt": version}}, {"sync_version": {"$exists": False}}]},
                {"$or": [
                    {"installation_id": {"$ne": installation_id}},
                    {"deactivated_version": {"$not": {"$gte": version}}},
                ]},
            ],
        }
        update = {
            "$set": {
                **metadata.model_dump(),
                "installation_id": installation_id,
                "is_active": True,
                "sync_version": version,
                "updated_at": now,
                "last_synced_at": now,
            },
            "$setOnInsert": {"created_at": now},
            "$unset": {"deactivated_version": ""},
        }
        try:
            await self.collection.update_one(selector, update, upsert=True)
        except DuplicateKeyError:
            await self.collection.update_one(selector, update)

    async def get_by_repo_id(self, github_repo_id: int) -> GitHubRepository | None:
        """Look up a repository by its immutable GitHub ID."""
        document = await self.collection.find_one({"github_repo_id": github_repo_id})
        return GitHubRepository.model_validate(document) if document else None

    async def get_by_full_name(self, full_name: str) -> GitHubRepository | None:
        """Find the active repository currently using a case-insensitive display name."""
        document = await self.collection.find_one(
            {"full_name": full_name, "is_active": True},
            sort=[("last_synced_at", -1)],
            collation=FULL_NAME_COLLATION,
        )
        return GitHubRepository.model_validate(document) if document else None

    async def list_by_installation_id(self, installation_id: int) -> list[GitHubRepository]:
        """List active and inactive repositories associated with an installation."""
        return await self._list({"installation_id": installation_id})

    async def mark_repository_inactive(self, github_repo_id: int, installation_id: int, *, version: int) -> None:
        """Soft-revoke a repository only under its observed installation and version."""
        await self.collection.update_one(
            {**self._older_repositories(installation_id, version), "github_repo_id": github_repo_id},
            self._deactivation(version),
        )

    async def deactivate_unsynced(self, installation_id: int, *, version: int) -> None:
        """Soft-revoke associations absent from a complete newer snapshot."""
        await self.collection.update_many(
            self._older_repositories(installation_id, version), self._deactivation(version),
        )

    @staticmethod
    def _older_repositories(installation_id: int, version: int) -> dict:
        return {
            "installation_id": installation_id,
            "sync_version": {"$not": {"$gte": version}},
            "deactivated_version": {"$not": {"$gte": version}},
        }

    @staticmethod
    def _deactivation(version: int) -> dict:
        now = now_utc()
        return {"$set": {
            "is_active": False, "deactivated_version": version, "updated_at": now, "last_synced_at": now,
        }}

    async def list_active_repositories(
        self, *, installation_ids: tuple[int, ...] = (), repository_ids: tuple[int, ...] = (),
    ) -> list[GitHubRepository]:
        """List active repositories in the supplied scopes, or all active repositories."""
        query = {"is_active": True}
        if installation_ids or repository_ids:
            query["$or"] = [
                {"installation_id": {"$in": list(installation_ids)}},
                {"github_repo_id": {"$in": list(repository_ids)}},
            ]
        return await self._list(query)

    async def _list(self, query: dict) -> list[GitHubRepository]:
        return [GitHubRepository.model_validate(document) async for document in self.collection.find(query)]
