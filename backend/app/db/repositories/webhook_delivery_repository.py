from datetime import datetime

from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern

from app.db.mongodb import MongoDb
from app.db.repositories.user_repositories import now_utc
from app.schema.webhook_delivery import (
    DeliveryMetadata,
    ProcessingError,
    WebhookDelivery,
)


class WebhookDeliveryRepository:
    def __init__(self, collection=None):
        self.collection = (
            collection if collection is not None
            else MongoDb.webhook_deliveries().with_options(write_concern=WriteConcern(w="majority"))
        )

    async def insert_delivery(self, delivery_id: str, metadata: DeliveryMetadata) -> bool:
        """Atomically record receipt, returning false only for a duplicate delivery."""
        delivery = WebhookDelivery(
            delivery_id=delivery_id, **metadata.model_dump(), received_at=now_utc(),
        )
        try:
            await self.collection.insert_one(delivery.model_dump())
        except DuplicateKeyError:
            return False
        return True

    async def get_delivery(self, delivery_id: str) -> WebhookDelivery | None:
        """Read the persisted processing status for a delivery."""
        document = await self.collection.find_one({"delivery_id": delivery_id})
        return WebhookDelivery.model_validate(document) if document else None

    async def start_processing(self, delivery_id: str) -> bool:
        """Atomically claim a pending delivery once."""
        result = await self.collection.update_one(
            {"delivery_id": delivery_id, "status": "pending"},
            {"$set": {"status": "processing", "started_at": now_utc()}, "$inc": {"attempt_count": 1}},
        )
        return bool(result.modified_count)

    async def finish_processing(self, delivery_id: str, *, error: ProcessingError | None = None) -> None:
        """Finish a claimed delivery with a sanitized reason on failure."""
        await self.collection.update_one(
            {"delivery_id": delivery_id, "status": "processing"},
            {"$set": {
                "status": "failed" if error else "completed",
                "completed_at": now_utc(),
                "last_error": error,
            }},
        )

    async def list_stale(self, before: datetime, *, limit: int = 100) -> list[WebhookDelivery]:
        """Locate stale work without claiming or retrying it."""
        if limit < 1:
            raise ValueError("limit must be positive")
        cursor = self.collection.find({
            "received_at": {"$lt": before},
            "$or": [{"status": "pending"}, {"status": "processing", "started_at": {"$lt": before}}],
        }).sort("received_at", 1).limit(limit)
        return [WebhookDelivery.model_validate(document) async for document in cursor]
