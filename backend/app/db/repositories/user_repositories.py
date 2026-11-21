
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDb


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class UserRepository:

    def __init__(self, collection=None):
        self.collection = (
            collection
            if collection is not None
            else MongoDb.users()
        )

    async def get_by_clerk_id(
        self,
        clerk_user_id: str,
    ) -> dict[str, Any] | None:

        return await self.collection.find_one({
            "clerk_user_id": clerk_user_id
        })

    async def apply_clerk_event(
        self,
        clerk_user_id: str,
        event_type: str,
        event_timestamp_ms: int,
        profile: dict[str, Any] | None = None,
    ) -> bool:
        """
        Apply a Clerk event only if it is newer
        than the last event stored in MongoDB.

        Deletions leave a tombstone to prevent
        older events from recreating a deleted user.
        """

        timestamp = now_utc()

        selector = {
            "clerk_user_id": clerk_user_id,
            "$or": [
                {
                    "clerk_event_ts": {
                        "$lt": event_timestamp_ms
                    }
                },
                {
                    "clerk_event_ts": {
                        "$exists": False
                    }
                }
            ],
        }

        fields: dict[str, Any] = {
            "clerk_event_ts": event_timestamp_ms,
            "updated_at": timestamp,
        }

        if event_type == "user.deleted":
            fields.update(
                status="deleted",
                deleted_at=timestamp,
            )

        elif event_type in ("user.created", "user.updated"):
            fields.update(profile or {})
            fields.update(
                status="active",
                deleted_at=None,
            )

        else:
            raise ValueError(
                f"Unsupported Clerk event: {event_type}"
            )

        operation = {
            "$set": fields,
            "$setOnInsert": {
                "clerk_user_id": clerk_user_id,
                "created_at": timestamp,
                "role": "user",
                "preferences": {
                    "email_notifications": True,
                    "slack_notifications": True,
                },
            },
        }

        try:
            result = await self.collection.update_one(
                selector,
                operation,
                upsert=True,
            )

            return bool(
                result.matched_count or result.upserted_id
            )

        except DuplicateKeyError:
            # An older event may have arrived after
            # a newer event was already recorded.
            # Retry without an upsert.
            result = await self.collection.update_one(
                selector,
                {"$set": fields},
            )

            return bool(result.matched_count)
