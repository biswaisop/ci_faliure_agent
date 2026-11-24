from datetime import timedelta
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern

from app.db.models.workflow_run import WorkflowRunMetadata
from app.db.mongodb import MongoDb
from app.db.repositories.user_repositories import now_utc

PUBLICATIONS = "pr_comment_publications"


class PRCommentRepository:
    def __init__(self, collection=None):
        self.collection = collection if collection is not None else MongoDb.get_collection(
            PUBLICATIONS,
        ).with_options(write_concern=WriteConcern(w="majority"))

    @staticmethod
    def key(run: WorkflowRunMetadata) -> dict:
        return {
            "github_repo_id": run.github_repo_id, "run_id": run.run_id, "run_attempt": run.run_attempt,
            "installation_id": run.installation_id,
        }

    async def get(self, run: WorkflowRunMetadata) -> dict | None:
        return await self.collection.find_one(self.key(run))

    async def claim(self, run: WorkflowRunMetadata) -> dict | None:
        now = now_utc()
        try:
            return await self.collection.find_one_and_update(
                {**self.key(run), "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}]},
                {
                    "$set": {
                        "status": "publishing", "lease_id": uuid4().hex, "updated_at": now,
                        "lease_until": now + timedelta(minutes=3),
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True, return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return None

    async def write(self, run: WorkflowRunMetadata, lease_id: str, **fields) -> None:
        now = now_utc()
        result = await self.collection.update_one(
            {**self.key(run), "lease_id": lease_id, "lease_until": {"$gt": now}},
            {"$set": {**fields, "updated_at": now}},
        )
        if result.matched_count != 1:
            raise RuntimeError("Publication lease lost")

    async def finish(self, run: WorkflowRunMetadata, lease_id: str, status: str, **fields) -> None:
        await self.write(run, lease_id, status=status, lease_until=None, **fields)
