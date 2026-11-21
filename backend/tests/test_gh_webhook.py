"""
Comprehensive tests for GitHub Webhook Ingestion, Deduplication, and Background Processing.

Covers all 10 required test scenarios:
1. Valid signed failed workflow_run event schedules exactly one background task.
2. Duplicate X-GitHub-Delivery returns HTTP 200 without scheduling another task.
3. Invalid signature is rejected.
4. Missing delivery ID is rejected.
5. Non-workflow_run events are ignored.
6. Non-failed workflow runs are ignored.
7. A workflow_run event that is not completed is ignored.
8. Malformed eligible payloads are handled gracefully.
9. Exceptions in the background processor are logged without breaking the application.
10. Concurrent requests with the same delivery ID do not schedule duplicate processing.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services.gh_collector import GitHubCollector
from app.services.webhook_processor import process_failed_workflow_run
from config import settings
from dependencies import get_deduplicator, get_github_collector

TEST_SECRET = "test_webhook_secret_key"


def compute_signature(payload: bytes, secret: str = TEST_SECRET) -> str:
    """Helper to generate HMAC-SHA256 signature for test payloads."""
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def build_workflow_run_payload(
    action: str = "completed",
    conclusion: str = "failure",
    run_id: int = 12345678,
    workflow_id: int | str = 998877,
    head_sha: str = "0123456789abcdef0123456789abcdef01234567",
    head_branch: str = "main",
    owner: str = "test-org",
    repo: str = "test-repo",
    installation_id: int = 112233,
) -> dict[str, Any]:
    """Helper to build a GitHub workflow_run webhook payload."""
    return {
        "action": action,
        "workflow_run": {
            "id": run_id,
            "workflow_id": workflow_id,
            "head_sha": head_sha,
            "head_branch": head_branch,
            "run_attempt": 1,
            "status": "completed" if action == "completed" else "in_progress",
            "conclusion": conclusion,
        },
        "repository": {
            "name": repo,
            "full_name": f"{owner}/{repo}",
            "owner": {"login": owner},
        },
        "installation": {
            "id": installation_id,
        },
    }


@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch: pytest.MonkeyPatch):
    """Ensure test webhook secret is set and clear deduplicator between tests."""
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", TEST_SECRET)
    deduplicator = get_deduplicator()
    if hasattr(deduplicator, "_seen"):
        deduplicator._seen.clear()


# ============================================================================
# Scenario 1: Valid signed failed workflow_run schedules exactly one task
# ============================================================================
def test_valid_signed_failed_workflow_run_schedules_task():
    payload_dict = build_workflow_run_payload()
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    delivery_id = "delivery-uuid-001"

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": compute_signature(payload_bytes),
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        client = TestClient(app)
        response = client.post("/github/webhook", content=payload_bytes, headers=headers)

        assert response.status_code == 200
        assert response.json() == {
            "status": "accepted",
            "delivery_id": delivery_id,
        }
        mock_processor.assert_called_once()
        call_kwargs = mock_processor.call_args.kwargs
        assert call_kwargs["delivery_id"] == delivery_id
        assert call_kwargs["owner"] == "test-org"
        assert call_kwargs["repo"] == "test-repo"
        assert call_kwargs["run_id"] == 12345678
        assert call_kwargs["conclusion"] == "failure"


# ============================================================================
# Scenario 2: Duplicate delivery ID returns HTTP 200 without scheduling again
# ============================================================================
def test_duplicate_delivery_id_returns_200_without_scheduling():
    payload_dict = build_workflow_run_payload()
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    delivery_id = "delivery-uuid-002"

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": compute_signature(payload_bytes),
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        client = TestClient(app)

        # First delivery: accepted
        resp1 = client.post("/github/webhook", content=payload_bytes, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json() == {"status": "accepted", "delivery_id": delivery_id}
        assert mock_processor.call_count == 1

        # Second delivery (duplicate): returned duplicate status, no new task
        resp2 = client.post("/github/webhook", content=payload_bytes, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json() == {"status": "duplicate", "delivery_id": delivery_id}
        assert mock_processor.call_count == 1


# ============================================================================
# Scenario 3: Invalid signature is rejected with HTTP 401
# ============================================================================
def test_invalid_signature_rejected():
    payload_dict = build_workflow_run_payload()
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    delivery_id = "delivery-uuid-003"

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": "sha256=badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadb",
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        client = TestClient(app)
        response = client.post("/github/webhook", content=payload_bytes, headers=headers)

        assert response.status_code == 401
        assert "Invalid webhook signature" in response.json()["detail"]
        mock_processor.assert_not_called()
        # Ensure invalid signature delivery is NOT stored in deduplicator
        deduplicator = get_deduplicator()
        assert delivery_id not in deduplicator._seen


# ============================================================================
# Scenario 4: Missing delivery ID is rejected with HTTP 400
# ============================================================================
def test_missing_delivery_id_rejected():
    payload_dict = build_workflow_run_payload()
    payload_bytes = json.dumps(payload_dict).encode("utf-8")

    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": compute_signature(payload_bytes),
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        client = TestClient(app)
        response = client.post("/github/webhook", content=payload_bytes, headers=headers)

        assert response.status_code == 400
        assert "Missing X-GitHub-Delivery header" in response.json()["detail"]
        mock_processor.assert_not_called()


# ============================================================================
# Scenario 5: Non-workflow_run events are ignored (return 200)
# ============================================================================
def test_non_workflow_run_events_ignored():
    payload_dict = {"ref": "refs/heads/main", "commits": []}
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    delivery_id = "delivery-uuid-005"

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "push",
        "X-Hub-Signature-256": compute_signature(payload_bytes),
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        client = TestClient(app)
        response = client.post("/github/webhook", content=payload_bytes, headers=headers)

        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        mock_processor.assert_not_called()
        # Ineligible events must not consume deduplication entries
        deduplicator = get_deduplicator()
        assert delivery_id not in deduplicator._seen


# ============================================================================
# Scenario 6: Non-failed workflow runs are ignored (return 200)
# ============================================================================
def test_successful_workflow_run_ignored():
    payload_dict = build_workflow_run_payload(action="completed", conclusion="success")
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    delivery_id = "delivery-uuid-006"

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": compute_signature(payload_bytes),
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        client = TestClient(app)
        response = client.post("/github/webhook", content=payload_bytes, headers=headers)

        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        mock_processor.assert_not_called()
        deduplicator = get_deduplicator()
        assert delivery_id not in deduplicator._seen


# ============================================================================
# Scenario 7: A workflow_run event that is not completed is ignored
# ============================================================================
def test_in_progress_workflow_run_ignored():
    payload_dict = build_workflow_run_payload(action="in_progress", conclusion="failure")
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    delivery_id = "delivery-uuid-007"

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": compute_signature(payload_bytes),
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        client = TestClient(app)
        response = client.post("/github/webhook", content=payload_bytes, headers=headers)

        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        mock_processor.assert_not_called()
        deduplicator = get_deduplicator()
        assert delivery_id not in deduplicator._seen


# ============================================================================
# Scenario 8: Malformed eligible payloads are handled gracefully
# ============================================================================
def test_malformed_eligible_payload_handled_gracefully():
    malformed_payload = {
        "action": "completed",
        "workflow_run": {
            "conclusion": "failure",
        },
    }
    payload_bytes = json.dumps(malformed_payload).encode("utf-8")
    delivery_id = "delivery-uuid-008"

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": compute_signature(payload_bytes),
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        client = TestClient(app)
        response = client.post("/github/webhook", content=payload_bytes, headers=headers)

        assert response.status_code == 400
        assert "Malformed payload" in response.json()["detail"]
        mock_processor.assert_not_called()
        deduplicator = get_deduplicator()
        assert delivery_id not in deduplicator._seen


def test_malformed_unparseable_json_rejected():
    invalid_json_bytes = b"This is clearly not { json"
    delivery_id = "delivery-uuid-008b"

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": compute_signature(invalid_json_bytes),
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        client = TestClient(app)
        response = client.post("/github/webhook", content=invalid_json_bytes, headers=headers)

        assert response.status_code == 400
        assert "Malformed JSON payload" in response.json()["detail"]
        mock_processor.assert_not_called()


# ============================================================================
# Scenario 9: Exceptions in background processor logged without breaking app
# ============================================================================
@pytest.mark.anyio
async def test_background_processor_catches_and_logs_exceptions(caplog: pytest.LogCaptureFixture):
    with patch(
        "app.services.webhook_processor.get_installation_id",
        side_effect=RuntimeError("GitHub API token acquisition failed!"),
    ), patch.object(
        GitHubCollector,
        "get_failed_jobs",
        side_effect=Exception("Unexpected API connection error"),
    ), caplog.at_level(logging.WARNING):
        # Must execute without raising unhandled exception out of the function
        await process_failed_workflow_run(
            delivery_id="delivery-uuid-009",
            owner="test-org",
            repo="test-repo",
            run_id=9999,
            workflow_id="ci.yml",
            head_sha="deadbeef",
            head_branch="main",
            run_attempt=1,
            conclusion="failure",
        )

        # Verify warning/error logs were generated
        assert any(
            "Could not acquire GitHub installation token" in record.message
            or "Skipping GitHub API collection" in record.message
            for record in caplog.records
        )


@pytest.mark.anyio
async def test_background_processor_top_level_exception_logged(caplog: pytest.LogCaptureFixture):
    with patch("app.services.webhook_processor.logger.info", side_effect=ValueError("Simulated logging failure")), caplog.at_level(logging.ERROR):
        # Should not crash the runner
        await process_failed_workflow_run(
            delivery_id="delivery-uuid-009-err",
            owner="test-org",
            repo="test-repo",
            run_id=9999,
            workflow_id="ci.yml",
            head_sha="deadbeef",
            head_branch="main",
            run_attempt=1,
            conclusion="failure",
        )

        assert any("Unhandled exception during background processing" in record.message for record in caplog.records)


# ============================================================================
# Scenario 10: Concurrent requests with same delivery ID do not duplicate
# ============================================================================
@pytest.mark.anyio
async def test_concurrent_requests_same_delivery_id_no_duplicate_processing():
    payload_dict = build_workflow_run_payload()
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    delivery_id = "delivery-uuid-concurrent-010"

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": compute_signature(payload_bytes),
        "Content-Type": "application/json",
    }

    with patch("app.routes.gh_webhook.process_failed_workflow_run", new_callable=AsyncMock) as mock_processor:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Issue 5 concurrent requests with identical delivery_id
            tasks = [
                client.post("/github/webhook", content=payload_bytes, headers=headers)
                for _ in range(5)
            ]
            responses = await asyncio.gather(*tasks)

        # All requests must return HTTP 200
        assert all(resp.status_code == 200 for resp in responses)

        statuses = [resp.json()["status"] for resp in responses]

        # Exactly 1 request accepted, and 4 marked as duplicate
        assert statuses.count("accepted") == 1
        assert statuses.count("duplicate") == 4

        # Background task scheduled exactly once
        assert mock_processor.call_count == 1
