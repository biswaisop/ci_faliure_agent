from datetime import timedelta
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDb
from app.db.repositories.pr_comment_repository import PRCommentRepository
from app.db.repositories.user_repositories import now_utc

FIX_SUGGESTIONS = "fix_suggestions"


class FixSuggestionRepository:
    def __init__(self, collection=None):
        self.collection = collection if collection is not None else MongoDb.get_collection(FIX_SUGGESTIONS)

    async def get(self, run, version: str | None = None) -> dict | None:
        key = PRCommentRepository.key(run)
        if version is not None:
            key["version"] = version
        return await self.collection.find_one(key, sort=[("created_at", -1), ("_id", -1)])

    async def claim(self, run, version: str, model: str) -> dict | None:
        now = now_utc()
        try:
            return await self.collection.find_one_and_update(
                {
                    **PRCommentRepository.key(run), "version": version,
                    "status": {"$nin": ["suggested", "insufficient_evidence"]},
                    "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}],
                },
                {
                    "$setOnInsert": {"created_at": now, "head_sha": run.head_sha, "model": model},
                    "$set": {
                        "status": "generating", "lease_id": uuid4().hex, "updated_at": now,
                        "lease_until": now + timedelta(minutes=2),
                    },
                },
                upsert=True, return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return None

    async def finish(self, claim: dict, **fields) -> None:
        now = now_utc()
        result = await self.collection.update_one(
            {"_id": claim["_id"], "lease_id": claim["lease_id"], "lease_until": {"$gt": now}},
            {"$set": {"lease_until": None, "updated_at": now, **fields}},
        )
        if result.matched_count != 1:
            raise RuntimeError("Fix suggestion lease lost")
