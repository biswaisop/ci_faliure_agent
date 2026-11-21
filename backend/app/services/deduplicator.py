"""
Webhook Delivery Deduplication Service.

Provides deduplication contracts and in-memory store implementation.
Class instantiation is managed centrally by the dependency factory in dependencies.py.

NOTE ON IN-MEMORY IMPLEMENTATION:
- This in-memory deduplication store is strictly per-process and resides in volatile memory.
- It resets whenever the application process restarts.
- It is unsuitable for multi-process or multi-worker deployments (such as running Uvicorn
  with multiple workers or scaling across multiple containers/servers), because worker
  processes do not share memory space.
- The interface is intentionally decoupled with an asynchronous check-and-record method
  (`acquire_or_record`) protected by an asyncio.Lock, making it straightforward to replace
  with SQLite, Redis, or another distributed store in the future without modifying route handlers.
"""

import asyncio
from typing import Protocol


class DeliveryDeduplicator(Protocol):
    """Protocol defining the interface for delivery deduplication stores."""

    async def acquire_or_record(self, delivery_id: str) -> bool:
        """
        Atomically check if delivery_id is present.
        If present, returns False (duplicate).
        If absent, records it and returns True (accepted).
        """
        ...

    async def is_duplicate(self, delivery_id: str) -> bool:
        """Check if delivery_id has already been recorded."""
        ...

    async def clear(self) -> None:
        """Clear all recorded delivery IDs."""
        ...


class InMemoryDeliveryDeduplicator:
    """
    Task-safe in-memory delivery deduplicator using an asyncio.Lock.

    Maintains a set of accepted GitHub webhook delivery IDs for the lifespan
    of the process.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = asyncio.Lock()

    async def acquire_or_record(self, delivery_id: str) -> bool:
        """
        Atomically check and record a delivery ID.

        Returns:
            bool: True if delivery_id was newly recorded (accepted),
                  False if delivery_id was already present (duplicate).
        """
        async with self._lock:
            if delivery_id in self._seen:
                return False
            self._seen.add(delivery_id)
            return True

    async def is_duplicate(self, delivery_id: str) -> bool:
        """Return True if delivery_id has already been accepted."""
        async with self._lock:
            return delivery_id in self._seen

    async def clear(self) -> None:
        """Reset the in-memory set. Useful for testing or maintenance."""
        async with self._lock:
            self._seen.clear()
