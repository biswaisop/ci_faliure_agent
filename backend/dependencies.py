"""
Dependency Factory for CI Agent.

Centralizes the instantiation and provisioning of application dependencies,
including GitHub API collectors and delivery deduplicators, for FastAPI dependency
injection and background processing.
"""

from typing import Any

from app.services.deduplicator import DeliveryDeduplicator, InMemoryDeliveryDeduplicator
from app.services.gh_collector import GitHubCollector

# Centralized singleton instances initialized in the dependency factory
_delivery_deduplicator: InMemoryDeliveryDeduplicator = InMemoryDeliveryDeduplicator()
_github_collector: GitHubCollector = GitHubCollector()


def get_deduplicator() -> DeliveryDeduplicator:
    """Dependency provider returning the delivery deduplicator."""
    return _delivery_deduplicator


def get_github_collector() -> GitHubCollector:
    """Dependency provider returning the GitHubCollector service instance."""
    return _github_collector


def reset_dependencies() -> None:
    """
    Reset or clear shared state across dependencies.
    Primarily utilized in test suites and environment reinitialization.
    """
    global _delivery_deduplicator, _github_collector
    _delivery_deduplicator = InMemoryDeliveryDeduplicator()
    _github_collector = GitHubCollector()
