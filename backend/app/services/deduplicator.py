"""Atomic receipt recording for GitHub webhook deliveries."""

import asyncio
from typing import Protocol

from app.db.repositories.webhook_delivery_repository import WebhookDeliveryRepository
from app.schema.webhook_delivery import DeliveryMetadata


class DeliveryDeduplicator(Protocol):
    async def acquire_or_record(self, delivery_id: str) -> bool:
        """Return true for a new delivery or false for a duplicate."""
        ...


class MongoDeliveryDeduplicator:
    def __init__(self, metadata: DeliveryMetadata, repository: WebhookDeliveryRepository | None = None) -> None:
        self.metadata = metadata
        self.repository = repository

    async def acquire_or_record(self, delivery_id: str) -> bool:
        """Record a delivery through the MongoDB unique index."""
        repository = self.repository if self.repository is not None else WebhookDeliveryRepository()
        return await repository.insert_delivery(delivery_id, self.metadata)


class InMemoryDeliveryDeduplicator:
    """Task-safe in-memory implementation for tests."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = asyncio.Lock()

    async def acquire_or_record(self, delivery_id: str) -> bool:
        """Return true exactly once per ID in this process."""
        async with self._lock:
            if delivery_id in self._seen:
                return False
            self._seen.add(delivery_id)
            return True

    async def is_duplicate(self, delivery_id: str) -> bool:
        """Return whether this process has already accepted the delivery."""
        async with self._lock:
            return delivery_id in self._seen

    async def clear(self) -> None:
        """Reset recorded deliveries for tests."""
        async with self._lock:
            self._seen.clear()
