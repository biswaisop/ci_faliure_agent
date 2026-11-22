from datetime import UTC, datetime

import pytest
from bson import BSON, ObjectId
from pydantic import ValidationError

from app.schema.github_installation import GitHubInstallation
from app.schema.github_repository import GitHubRepository
from app.schema.membership import UserMembership
from app.schema.webhook_delivery import WebhookDelivery
from app.schema.workflow_run import EvidenceUpdate, FailedJob, WorkflowRun

NOW = datetime(2026, 11, 23, tzinfo=UTC)
OWNER = {"github_account_id": 20, "login": "example", "type": "Organization"}


@pytest.mark.parametrize("schema, document, required, invalid", [
    (GitHubInstallation, {
        "installation_id": 10, "status": "active", "updated_at": NOW, "account": OWNER,
        "installed_at": NOW, "repository_selection": "selected",
    }, "installation_id", {"installation_id": True, "status": "broken"}),
    (GitHubRepository, {
        "github_repo_id": 30, "owner": OWNER, "name": "demo", "full_name": "example/demo",
        "default_branch": "main", "private": True, "html_url": "https://github.com/example/demo",
        "installation_id": 10, "is_active": True, "created_at": NOW, "updated_at": NOW,
        "last_synced_at": NOW, "sync_version": 1,
    }, "owner", {"github_repo_id": -1, "installation_id": "10"}),
    (UserMembership, {
        "clerk_user_id": "user_example", "resource_type": "installation", "resource_id": 10,
        "installation_id": 10, "status": "active", "verification_source": "github",
        "verified_at": NOW, "created_at": NOW, "updated_at": NOW,
    }, "verified_at", {"status": "unknown", "role": "owner", "resource_type": "user", "clerk_user_id": ""}),
    (WorkflowRun, {
        "github_repo_id": 30, "run_id": 40, "workflow_id": 50, "head_sha": "abc123",
        "created_at": NOW, "updated_at": NOW,
    }, "head_sha", {"run_attempt": 0, "conclusion": "success", "workflow_id": "ci.yml"}),
    (WebhookDelivery, {
        "delivery_id": "example-delivery", "event_type": "workflow_run", "action": "completed",
        "received_at": NOW,
    }, "received_at", {"delivery_id": "", "status": "retrying", "last_error": "raw error with secret"}),
])
def test_collection_schema_validates_and_serializes(schema, document, required, invalid):
    model = schema.model_validate({"_id": ObjectId(), **document})
    stored = BSON(BSON.encode(model.model_dump())).decode()
    assert "_id" not in stored
    assert schema.model_validate(stored)
    assert isinstance(stored.get("updated_at", stored.get("received_at")), datetime)

    with pytest.raises(ValidationError):
        schema.model_validate({key: value for key, value in document.items() if key != required})
    for field, bad_value in invalid.items():
        with pytest.raises(ValidationError):
            schema.model_validate({**document, field: bad_value})


def test_job_log_and_evidence_constraints():
    job = FailedJob(job_id=1, name="test", log_status="expired")
    assert job.log_reference is None
    with pytest.raises(ValidationError):
        FailedJob(job_id=1, name="test", log_status="stored_raw")
    with pytest.raises(ValidationError):
        EvidenceUpdate(failure_packet="x" * 24_001)
    with pytest.raises(ValidationError):
        EvidenceUpdate(raw_log="unbounded raw log")
