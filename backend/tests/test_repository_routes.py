from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.clerk_auth import current_clerk_user_id
from app.db.models.workflow_run import WorkflowRun
from app.main import app
from app.routes import repositories


def test_workflow_reads_use_repository_scope_and_keep_attempts_separate(monkeypatch):
    job_id = 2**53 + 1
    run = WorkflowRun.model_validate({
        "github_repo_id": 7, "installation_id": 8, "run_id": 9, "run_attempt": 2,
        "workflow_id": 10, "workflow_name": "Tests", "head_sha": "abc123", "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 1),
        "failed_jobs": [{"job_id": job_id, "name": "pytest", "log_reference": "private-reference"}],
        "evidence": {
            "collection_status": "completed",
            "failure_packets": [{"job_id": job_id, "job_name": "pytest", "relevant_log": "line one\nline two"}],
        },
    })
    repo = SimpleNamespace(github_repo_id=7, installation_id=8, name="ci", owner=SimpleNamespace(login="team"))
    authorization = SimpleNamespace(
        list_authorized_repositories=AsyncMock(return_value=[repo]),
        get_authorized_repository=AsyncMock(return_value=repo),
    )
    runs = SimpleNamespace(list_run_summaries=AsyncMock(return_value=[run]), get_run=AsyncMock(return_value=run))
    investigations = SimpleNamespace(list_statuses=AsyncMock(return_value={(9, 2): "pending"}), get=AsyncMock())
    monkeypatch.setitem(app.dependency_overrides, repositories.get_authorization, lambda: authorization)
    monkeypatch.setitem(app.dependency_overrides, repositories.get_runs, lambda: runs)
    monkeypatch.setitem(app.dependency_overrides, repositories.get_investigations, lambda: investigations)

    client = TestClient(app)
    base = "/api/github/repositories"
    detail = f"{base}/7/runs/9/attempts/2"
    assert client.get(detail).status_code == 401
    monkeypatch.setitem(app.dependency_overrides, current_clerk_user_id, lambda: "user_1")

    dashboard = client.get(base)
    assert dashboard.status_code == 200
    assert dashboard.headers["Cache-Control"] == "private, no-store"
    summary = dashboard.json()["repositories"][0]["recent_runs"][0]
    assert (summary["workflow_name"], summary["run_attempt"], summary["investigation_status"]) == ("Tests", 2, "pending")
    assert "failed_jobs" not in summary
    runs.list_run_summaries.assert_awaited_once_with(7, 8, 3)
    assert client.get(f"{base}/7/runs?limit=51").status_code == 422
    assert client.get(f"{base}/7/runs").status_code == 200
    runs.list_run_summaries.assert_awaited_with(7, 8, 50)

    authorization.get_authorized_repository.return_value = None
    assert client.get(detail).status_code == 404
    runs.get_run.assert_not_awaited()
    authorization.get_authorized_repository.return_value = repo
    runs.get_run.return_value = run.model_copy(update={"installation_id": 99})
    assert client.get(detail).status_code == 404
    investigations.get.assert_not_awaited()
    runs.get_run.return_value = run

    investigations.get.return_value = None
    result = client.get(detail).json()
    assert result["run"]["collection_status"] == "completed"
    assert result["run"]["investigation_status"] == "unavailable"
    assert result["diagnosis"] is None
    assert runs.get_run.await_args.args[0].run_attempt == 2
    assert runs.get_run.await_args.kwargs["installation_id"] == 8
    assert result["evidence"]["failure_packets"][0]["job_id"] == str(job_id)
    assert result["evidence"]["failure_packets"][0]["relevant_log"] == "line one\nline two"
    assert "log_reference" not in result["evidence"]["failed_jobs"][0]

    investigations.get.return_value = {
        "status": "completed", "error_code": "internal-only", "diagnosis": {
            "summary": "Summary", "root_cause": "Hypothesis", "confidence": "low",
            "evidence_ids": [f"log:{job_id}"], "evidence_validated": True,
        },
    }
    response = client.get(detail)
    assert response.json()["diagnosis"]["evidence_ids"] == [f"log:{job_id}"]
    assert "internal-only" not in response.text
