from pymongo.errors import DuplicateKeyError

from app.db.models.github_installation import (
    GitHubInstallation,
    InstallationMetadata,
    InstallationStatus,
)
from app.db.mongodb import MongoDb
from app.db.repositories.user_repositories import now_utc


class GitHubInstallationRepository:
    def __init__(self, collection=None):
        self.collection = collection if collection is not None else MongoDb.github_installations()

    async def begin_sync(self, installation_id: int, version: int) -> bool:
        """Fence older snapshots and close access until the new snapshot completes."""
        selector = {
            "installation_id": installation_id,
            "status": {"$ne": "deleted"},
            "$or": [{"sync_version": {"$lt": version}}, {"sync_version": {"$exists": False}}],
        }
        update = {
            "$set": {"sync_version": version, "updated_at": now_utc()},
            "$setOnInsert": {"status": "unavailable"},
        }
        try:
            result = await self.collection.update_one(selector, update, upsert=True)
        except DuplicateKeyError:
            result = await self.collection.update_one(selector, update)
        return bool(result.matched_count or result.upserted_id)

    @staticmethod
    def _sync_selector(installation_id: int, version: int) -> dict:
        return {
            "installation_id": installation_id,
            "sync_version": version,
            "status": {"$ne": "deleted"},
        }

    async def upsert_installation(
        self, installation_id: int, metadata: InstallationMetadata, *, version: int,
    ) -> bool:
        """Persist installation metadata under the fence reserved by begin_sync."""
        result = await self.collection.update_one(
            self._sync_selector(installation_id, version),
            {"$set": {
                **metadata.model_dump(),
                "status": "suspended" if metadata.suspended_at else "active",
                "updated_at": now_utc(),
            }},
        )
        return bool(result.matched_count)

    async def finish_sync(self, installation_id: int, version: int) -> None:
        """Publish a completed repository snapshot if its installation fence still matches."""
        await self.collection.update_one(
            self._sync_selector(installation_id, version),
            {"$set": {"repository_sync_version": version, "updated_at": now_utc()}},
        )

    async def get_by_installation_id(self, installation_id: int) -> GitHubInstallation | None:
        """Return a synchronized installation or tombstone, including its current status."""
        document = await self.collection.find_one({"installation_id": installation_id, "status": {"$exists": True}})
        return GitHubInstallation.model_validate(document) if document else None

    async def get_by_account_id(self, github_account_id: int) -> list[GitHubInstallation]:
        """Find installations for an immutable GitHub account ID."""
        cursor = self.collection.find({"account.github_account_id": github_account_id})
        return [GitHubInstallation.model_validate(document) async for document in cursor]

    async def update_installation_permissions(
        self, installation_id: int, permissions: dict[str, str], *, version: int,
    ) -> None:
        """Update permissions for a current installation snapshot."""
        await self.collection.update_one(
            self._sync_selector(installation_id, version),
            {"$set": {"permissions": dict(permissions), "updated_at": now_utc()}},
        )

    async def set_installation_status(
        self, installation_id: int, status: InstallationStatus, *, version: int,
    ) -> None:
        """Update fenced status or permanently tombstone a deleted installation ID."""
        now = now_utc()
        if status == "deleted":
            selector = {"installation_id": installation_id}
            update = [
                {"$set": {
                    "status": "deleted",
                    "deleted_at": {"$ifNull": ["$deleted_at", now]},
                    "updated_at": {"$cond": [{"$eq": ["$status", "deleted"]}, "$updated_at", now]},
                }},
            ]
            try:
                await self.collection.update_one(selector, update, upsert=True)
            except DuplicateKeyError:
                await self.collection.update_one(selector, update)
            return

        await self.collection.update_one(
            self._sync_selector(installation_id, version),
            {"$set": {"status": status, "suspended_at": now if status == "suspended" else None, "updated_at": now}},
        )
