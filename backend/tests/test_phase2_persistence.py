import asyncio
import json
import os
import re
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure

from app.db.indexes import setup_indexes
from app.db.models.github_installation import InstallationMetadata
from app.db.models.github_repository import RepositoryMetadata
from app.db.models.membership import VerifiedMembershipGrant
from app.db.models.webhook_delivery import DeliveryMetadata
from app.db.models.workflow_run import (
    EvidenceUpdate,
    FailedJob,
    WorkflowRunKey,
    WorkflowRunMetadata,
)
from app.db.mongodb import MongoDb
from app.db.repositories import UserRepository
from app.db.repositories.github_installation_repository import (
    GitHubInstallationRepository,
)
from app.db.repositories.github_repository import GitHubRepositoryRepository
from app.db.repositories.investigation_repository import InvestigationRepository
from app.db.repositories.membership_repository import MembershipRepository
from app.db.repositories.pr_comment_repository import PRCommentRepository
from app.db.repositories.webhook_delivery_repository import WebhookDeliveryRepository
from app.db.repositories.workflow_run_repository import (
    WorkflowRunInstallationConflict,
    WorkflowRunRepository,
)
from app.main import app
from app.schema.failure_packet import FailurePacket
from app.services import (
    gh_installations,
    github_sync_service,
    investigation_service,
    webhook_processor,
)
from app.services.deduplicator import MongoDeliveryDeduplicator
from app.services.gh_collector import CommitComparison, GitHubCollector
from app.services.github_authorization_service import GitHubAuthorizationService
from app.services.github_webhook_event import parse_webhook
from app.services.log_reducer import reduce_job_log
from config import settings
from tests.test_gh_webhook import (
    TEST_SECRET,
    build_workflow_run_payload,
    compute_signature,
)
from tests.test_investigation_agent import PAGES_ERROR, PAGES_LOG, FakeLLM, finding

pytestmark = pytest.mark.anyio


@pytest.fixture
async def mongo_db(monkeypatch):
    uri = os.getenv("TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set TEST_MONGO_URI to a local MongoDB server to run persistence tests")
    if not re.fullmatch(r"mongodb://127\.0\.0\.1:\d{1,5}/?", uri):
        pytest.fail("TEST_MONGO_URI must use a direct 127.0.0.1 MongoDB connection")
    database_name = f"autopci_test_{uuid4().hex}"
    client = AsyncIOMotorClient(uri, directConnection=True, serverSelectionTimeoutMS=2000)
    monkeypatch.setattr(MongoDb, "_client", client)
    monkeypatch.setattr(MongoDb, "DB_NAME", database_name)
    try:
        await setup_indexes()
        yield
    finally:
        try:
            await client.drop_database(database_name)
        finally:
            client.close()


@pytest.fixture
async def webhook_client(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", TEST_SECRET)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def send(client, delivery_id, event="workflow_run", payload=None):
    body = json.dumps(build_workflow_run_payload() if payload is None else payload).encode()
    return await client.post("/github/webhook", content=body, headers={
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": compute_signature(body),
    })


def installation_payload(account_id=11, *, account_type="Organization", **overrides):
    return {
        "account": {"id": account_id, "login": "example", "type": account_type},
        "repository_selection": "selected",
        "permissions": {"actions": "read"},
        "events": ["workflow_run"],
        "created_at": "2026-11-20T10:00:00Z",
        **overrides,
    }


def repository_payload(repo_id=33, *, name="demo", owner_id=11, login="example"):
    return {
        "id": repo_id, "name": name, "full_name": f"{login}/{name}",
        "owner": {"id": owner_id, "login": login, "type": "Organization"},
        "default_branch": "main", "private": True, "html_url": f"https://github.com/{login}/{name}",
    }


async def test_installation_repository_snapshot_follows_all_pages(monkeypatch):
    pages = []

    def github_api(request):
        assert request.url.path == "/installation/repositories"
        assert request.headers["Authorization"] == "Bearer mock-installation-token"
        page = int(request.url.params["page"])
        pages.append(page)
        headers = {"Link": '<https://api.github.com/installation/repositories?page=2>; rel="next"'} if page == 1 else {}
        return httpx.Response(200, headers=headers, json={"repositories": [repository_payload(page)]})

    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(github_api)
    monkeypatch.setattr(gh_installations.httpx, "AsyncClient", lambda **kwargs: async_client(transport=transport, **kwargs))
    repositories = await gh_installations.read_installation_repositories("mock-installation-token")
    assert pages == [1, 2]
    assert [repo["id"] for repo in repositories] == [1, 2]


async def test_mongo_indexes_and_atomic_receipt(mongo_db):
    indexes = await MongoDb.webhook_deliveries().index_information()
    assert indexes["delivery_id_1"]["unique"]
    assert indexes["status_1_received_at_1"]["key"] == [("status", 1), ("received_at", 1)]
    run_indexes = await MongoDb.workflow_runs().index_information()
    assert run_indexes["github_repo_id_1_run_id_1_run_attempt_1"]["unique"]


    assert run_indexes["github_repo_id_1_created_at_-1"]["key"][-1] == ("created_at", -1)

    metadata = DeliveryMetadata(event_type="workflow_run", action="completed", run_id=1, run_attempt=2)
    contenders = [MongoDeliveryDeduplicator(metadata) for _ in range(10)]
    assert sum(await asyncio.gather(*(store.acquire_or_record("same") for store in contenders))) == 1
    assert not await MongoDeliveryDeduplicator(metadata).acquire_or_record("same")

    repo = WebhookDeliveryRepository()
    assert (await repo.get_delivery("same")).status == "pending"
    assert sum(await asyncio.gather(*(repo.start_processing("same") for _ in range(4)))) == 1
    cutoff = datetime.now(UTC) + timedelta(seconds=1)
    assert [(item.delivery_id, item.status) for item in await repo.list_stale(cutoff)] == [("same", "processing")]
    await repo.insert_delivery("pending", metadata)
    assert {item.delivery_id for item in await repo.list_stale(cutoff)} == {"same", "pending"}

    await repo.finish_processing("same")
    await repo.finish_processing("same", error="processing_error")
    record = await repo.get_delivery("same")
    assert record.status == "completed" and record.attempt_count == 1
    assert record.started_at and record.completed_at and record.last_error is None
    assert [item.delivery_id for item in await repo.list_stale(cutoff)] == ["pending"]


async def test_mongo_publication_lease_recovers_uncertain_create(mongo_db):
    run = WorkflowRunMetadata(
        github_repo_id=123, installation_id=7, run_id=8, run_attempt=1, workflow_id=9, head_sha="a" * 40,
    )
    publications = PRCommentRepository()
    claims = await asyncio.gather(*(publications.claim(run) for _ in range(8)))
    claimed = [row for row in claims if row]
    assert len(claimed) == 1
    lease = claimed[0]["lease_id"]
    await publications.write(run, lease, pr_number=12, create_uncertain=True)
    await publications.collection.update_one(
        publications.key(run), {"$set": {"lease_until": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    recovered = await publications.claim(run)
    assert recovered["lease_id"] != lease and recovered["create_uncertain"]
    with pytest.raises(RuntimeError, match="lease lost"):
        await publications.finish(run, lease, "published")
    await publications.finish(run, recovered["lease_id"], "published", comment_id=100, create_uncertain=False)
    assert (await publications.get(run))["comment_id"] == 100


async def test_database_failure_rejects_delivery(webhook_client, monkeypatch):
    collection = Mock()
    collection.with_options.return_value.insert_one = AsyncMock(
        side_effect=ConnectionFailure("sensitive DB details"),
    )
    monkeypatch.setattr(MongoDb, "webhook_deliveries", lambda: collection)
    processor = AsyncMock()
    monkeypatch.setattr("app.routes.gh_webhook.process_failed_workflow_run", processor)

    response = await send(webhook_client, "db-error")
    assert response.status_code == 503
    assert "sensitive" not in response.text
    processor.assert_not_awaited()


@pytest.mark.parametrize("model_available", [True, False])
async def test_pages_investigation_does_not_change_evidence_collection_status(monkeypatch, model_available):
    payload = build_workflow_run_payload()
    payload["workflow_run"].update(
        name="Deploy Next.js site to Pages", path=".github/workflows/pages.yml",
        head_repository={"id": payload["repository"]["id"]},
    )
    event = parse_webhook("workflow_run", payload)
    collector = Mock(
        get_failed_jobs=AsyncMock(return_value=[{
            "id": 77, "name": "deploy", "failed_steps": [{"number": 2, "name": "Setup Pages"}],
        }]),
        get_job_log=AsyncMock(return_value=PAGES_LOG),
        resolve_commit_comparison=AsyncMock(return_value=CommitComparison("not_attempted")),
    )
    collection = Mock(update_one=AsyncMock(return_value=Mock(matched_count=1)))
    runs = Mock(upsert_workflow_run=AsyncMock(), update_collection_status=AsyncMock(),
                add_failed_jobs=AsyncMock(), update_evidence=AsyncMock())
    installations = Mock(get_by_installation_id=AsyncMock(return_value=None))
    repositories = Mock(get_by_repo_id=AsyncMock(return_value=None))
    token = AsyncMock(return_value="mock-token")
    monkeypatch.setattr(settings, "AUTO_PCI_FIX_SUGGESTIONS", False)
    monkeypatch.setattr(settings, "AUTO_PCI_PR_COMMENTS", False)
    monkeypatch.setattr(MongoDb, "investigations", lambda: collection)
    monkeypatch.setattr(webhook_processor, "get_installation_token", token)
    monkeypatch.setattr(webhook_processor, "get_installation_id", AsyncMock(return_value=123))
    get_llm = Mock(return_value=FakeLLM(finding("github_actions", reduce_job_log(PAGES_LOG, 77, "deploy", "Setup Pages"))))
    if not model_available:
        get_llm.side_effect = RuntimeError("NIM configuration unavailable")
    monkeypatch.setattr(investigation_service, "get_llm", get_llm)

    await webhook_processor.collect_workflow_run(event, runs, installations, repositories, collector)

    assert [call.args[1] for call in runs.update_collection_status.await_args_list] == ["collecting", "completed"]
    evidence = runs.update_evidence.await_args.args[1]
    assert evidence.failure_packets[0].action == "actions/configure-pages@v5"
    assert evidence.investigation_context.workflow_path == ".github/workflows/pages.yml"
    assert token.await_args.kwargs == {"repository_id": event.repository.github_repo_id}
    saved = collection.update_one.await_args.args[1]["$set"]
    if model_available:
        assert saved["investigation_metadata"]["selected_specialist"] == "github_actions"
        assert saved["diagnosis"]["summary"] == PAGES_ERROR
    else:
        assert saved["status"] == "failed" and saved["error_code"] == "investigation_failed"


async def test_signed_failed_run_records_correct_attempt_and_jobs(mongo_db, webhook_client, monkeypatch):
    monkeypatch.setattr(webhook_processor, "get_installation_token", AsyncMock(return_value="mock-token"))
    log = "##[group]Run pytest\nAssertionError: mismatch"
    packet = reduce_job_log(log, 77, "tests", "test")
    model = FakeLLM(finding("python", packet))
    monkeypatch.setattr(investigation_service, "get_llm", lambda: model)
    investigations = InvestigationRepository(MongoDb.investigations())
    processing_statuses = []
    mark_pending = investigations.mark_pending

    async def track_pending(self, run):
        await mark_pending(run)
        processing_statuses.append((await self.get(run))["status"])

    invoke = model.ainvoke

    async def diagnose(messages):
        context = WorkflowRunKey(run_id=12345678, github_repo_id=445566, run_attempt=2)
        processing_statuses.append((await investigations.get(context))["status"])
        return await invoke(messages)

    monkeypatch.setattr(InvestigationRepository, "mark_pending", track_pending)
    monkeypatch.setattr(model, "ainvoke", diagnose)
    calls = []

    def github_api(request):
        calls.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer mock-token"
        if request.url.path.endswith("/workflows/998877/runs"):
            assert request.url.params["branch"] == "main"
            return httpx.Response(200, json={"workflow_runs": [{
                "id": 17, "workflow_id": 998877, "head_branch": "main", "head_sha": "base",
                "conclusion": "success", "status": "completed", "run_attempt": 1,
                "created_at": "2026-11-22T10:00:00Z", "updated_at": "2026-11-22T10:01:00Z",
            }]})
        if request.url.path.endswith("/attempts/1"):
            return httpx.Response(404)
        if "/compare/" in request.url.path:
            return httpx.Response(200, json={
                "status": "ahead", "ahead_by": 1, "behind_by": 0, "merge_base_commit": {"sha": "base"},
                "files": [{"filename": "app.py", "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new"}],
            })
        return httpx.Response(200, json={"jobs": [
            {"id": 77, "name": "tests", "conclusion": "failure", "steps": [
                {"number": 1, "name": "setup", "conclusion": "success"},
                {"number": 2, "name": "test", "conclusion": "failure"},
            ]},
            {"id": 88, "name": "lint", "conclusion": "success"},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(github_api)) as client:
        collector = GitHubCollector(client=client)
        monkeypatch.setattr(collector, "get_job_log", AsyncMock(return_value=log))
        monkeypatch.setattr(webhook_processor, "get_github_collector", lambda: collector)
        payload = build_workflow_run_payload()
        payload["workflow_run"].update(
            run_attempt=2, run_number=42, event="push", name="Python tests", updated_at="2026-11-23T10:00:00Z",
        )
        responses = await asyncio.gather(
            *(send(webhook_client, "run-2", payload=payload) for _ in range(5)),
        )

    assert [reply.status_code for reply in responses] == [200] * 5
    assert [reply.json()["status"] for reply in responses].count("accepted") == 1
    assert [reply.json()["status"] for reply in responses].count("duplicate") == 4
    assert calls == [
        "/repos/test-org/test-repo/actions/runs/12345678/attempts/2/jobs",
        "/repos/test-org/test-repo/actions/workflows/998877/runs",
        "/repos/test-org/test-repo/actions/runs/12345678/attempts/1",
        "/repos/test-org/test-repo/compare/base...0123456789abcdef0123456789abcdef01234567",
    ]

    delivery = await WebhookDeliveryRepository().get_delivery("run-2")
    assert delivery.status == "completed" and delivery.attempt_count == 1 and delivery.run_attempt == 2
    assert delivery.github_repo_id == 445566
    runs = await WorkflowRunRepository().get_recent_failures(445566, workflow_id=998877, head_branch="main")
    assert len(runs) == 1
    run = runs[0]
    assert (run.run_attempt, run.run_number, run.event) == (2, 42, "push")
    assert run.workflow_name == "Python tests"
    assert run.evidence.collection_status == "completed"
    assert run.evidence.investigation_context.comparison_status == "available"
    assert run.evidence.investigation_context.changed_files[0].filename == "app.py"
    assert processing_statuses == ["pending", "running"]
    investigation = await investigations.get(run)
    assert investigation["status"] == "completed"
    assert investigation["diagnosis"]["summary"] == "AssertionError: mismatch"
    assert [(job.job_id, job.log_status) for job in run.failed_jobs] == [(77, "not_collected")]
    assert [(step.number, step.name) for step in run.failed_jobs[0].failed_steps] == [(2, "test")]


async def test_relink_updates_only_github_repository_grants(mongo_db):
    memberships = MembershipRepository()
    await memberships.grant_membership(VerifiedMembershipGrant(
        clerk_user_id="user_1", resource_type="repository", resource_id=3, installation_id=14,
        verification_source="admin", role="admin", verified_at=datetime.now(UTC),
    ))
    await memberships.sync_github_repository_grants("user_1", 14, [1, 2, 3])
    await memberships.sync_github_repository_grants("user_1", 14, [2])
    assert (await memberships.get_membership("user_1", "repository", 1)).status == "revoked"
    assert (await memberships.get_membership("user_1", "repository", 2)).status == "active"
    admin = await memberships.get_membership("user_1", "repository", 3)
    assert (admin.status, admin.role, admin.verification_source) == ("active", "admin", "admin")


@pytest.mark.parametrize("reason", ["timeout", "rate_limit", "missing_token", "unexpected"])
async def test_background_failure_is_sanitized(mongo_db, webhook_client, monkeypatch, caplog, reason):
    request = httpx.Request("GET", "https://api.github.com/")
    error_cases = {
        "timeout": (httpx.ReadTimeout("secret timeout info"), "github_timeout"),
        "rate_limit": (
            httpx.HTTPStatusError(
                "secret API info", request=request,
                response=httpx.Response(403, headers={"Retry-After": "10"}, request=request),
            ),
            "github_rate_limit",
        ),
        "missing_token": (None, "missing_installation_token"),
        "unexpected": (RuntimeError("secret traceback info"), "processing_error"),
    }
    failure, code = error_cases[reason]
    monkeypatch.setattr(
        webhook_processor, "get_installation_token",
        AsyncMock(side_effect=failure, return_value=""),
    )

    response = await send(webhook_client, "failed")
    assert response.json()["status"] == "accepted"
    assert (await send(webhook_client, "failed")).json()["status"] == "duplicate"

    delivery = await WebhookDeliveryRepository().get_delivery("failed")
    assert delivery.status == "failed" and delivery.last_error == code and delivery.attempt_count == 1
    run, = await WorkflowRunRepository().list_runs_by_repository(445566)
    assert run.evidence.collection_status == "failed"
    assert "secret" not in caplog.text


async def test_workflow_attempts_merge_jobs_and_keep_evidence(mongo_db):
    repository = WorkflowRunRepository()
    run = WorkflowRunMetadata(
        github_repo_id=1, installation_id=7, run_id=2, workflow_id=3, head_sha="abc", head_branch="main",
    )
    rerun = run.model_copy(update={"run_attempt": 2, "head_branch": "staging"})

    await asyncio.gather(repository.upsert_workflow_run(run), repository.upsert_workflow_run(rerun))
    packet = FailurePacket(job_id=4, job_name="test", relevant_log="compact failure")
    await repository.update_evidence(run, EvidenceUpdate(base_sha="base", failure_packets=[packet]))
    await repository.update_evidence(run, EvidenceUpdate(diff_summary="source change", base_sha=None))
    await repository.update_collection_status(run, "completed")

    jobs = [
        FailedJob(job_id=4, name="test", log_status="expired", failed_steps=[{"number": 1, "name": "first"}]),
        FailedJob(job_id=5, name="build", log_status="missing"),
    ]
    await asyncio.gather(*(repository.add_failed_jobs(run, [job]) for job in jobs))
    await repository.add_failed_jobs(run, [FailedJob(job_id=4, name="test", failed_steps=[{"number": 2, "name": "second"}])])
    await repository.upsert_workflow_run(run.model_copy(update={"run_number": 50, "head_branch": None}))
    await repository.update_collection_status(run, "failed")
    with pytest.raises(WorkflowRunInstallationConflict):
        await repository.upsert_workflow_run(run.model_copy(update={"installation_id": 8}))

    attempts = await repository.list_runs_by_repository(1)
    assert {attempt.run_attempt for attempt in attempts} == {1, 2}
    assert await repository.list_runs_by_repository(1, installation_id=8) == []
    assert (await repository.get_run(rerun)).failed_jobs == []
    updated, = await repository.get_recent_failures(1, workflow_id=3, head_branch="main")
    assert updated.run_number == 50 and updated.evidence.collection_status == "completed"
    assert updated.evidence.base_sha == "base" and updated.evidence.failure_packets == [packet]
    assert updated.evidence.diff_summary == "source change"
    assert {job.job_id: job.log_status for job in updated.failed_jobs} == {4: "expired", 5: "missing"}
    job = next(job for job in updated.failed_jobs if job.job_id == 4)
    assert [step.number for step in job.failed_steps] == [1, 2]
    assert await repository.get_run(run, installation_id=8) is None
    summaries = await repository.list_run_summaries(1, 7, 2)
    assert {summary.run_attempt for summary in summaries} == {1, 2}
    assert all(not summary.failed_jobs and summary.evidence.failure_packets is None for summary in summaries)


async def test_workflow_job_budget_preserves_existing_data(mongo_db, monkeypatch):
    repository = WorkflowRunRepository()
    run = WorkflowRunMetadata(github_repo_id=1, run_id=2, workflow_id=3, head_sha="abc")
    await repository.upsert_workflow_run(run)
    await repository.add_failed_jobs(run, [FailedJob(job_id=1, name="original")])
    monkeypatch.setattr("app.db.repositories.workflow_run_repository.MAX_FAILED_JOBS_BYTES", 400)
    with pytest.raises(ValueError, match="storage budget"):
        await repository.add_failed_jobs(run, [FailedJob(job_id=2, name="x" * 1000)])
    assert [job.job_id for job in (await repository.get_run(run)).failed_jobs] == [1]


async def test_installation_deletion_requires_signed_installation_shape(webhook_client):
    response = await send(webhook_client, "wrong-event", "installation", {
        "action": "deleted", "installation": {"id": 22}, "repository": repository_payload(),
    })
    assert response.status_code == 400


async def test_installation_lifecycle_and_repository_access(mongo_db, webhook_client, monkeypatch):
    installations = GitHubInstallationRepository()
    repositories = GitHubRepositoryRepository()
    account = installation_payload()
    accessible = [repository_payload()]
    get_installation = AsyncMock(return_value=account)
    monkeypatch.setattr(github_sync_service, "generate_app_jwt", lambda: "fake-app-jwt")
    monkeypatch.setattr(github_sync_service, "read_installation", get_installation)
    monkeypatch.setattr(github_sync_service, "get_installation_token", AsyncMock(return_value="mock-token"))
    get_repositories = AsyncMock(return_value=accessible)
    monkeypatch.setattr(github_sync_service, "read_installation_repositories", get_repositories)

    async def event(action, event_type="installation", delivery_id=None, installation_id=22, **fields):
        if event_type == "installation_repositories":
            fields.update(repositories_added=[], repositories_removed=[])
        reply = await send(webhook_client, delivery_id or action, event_type, {
            "action": action, "installation": {"id": installation_id, **account}, **fields,
        })
        assert reply.status_code == 200
        return reply.json()["status"]

    assert await event("created") == "accepted"
    assert await event("created") == "duplicate"
    get_installation.assert_awaited_once()
    record, = await installations.get_by_account_id(11)
    assert record.installation_id == 22 and record.status == "active"
    assert record.events == ["workflow_run"]
    repo = await repositories.get_by_full_name("EXAMPLE/Demo")
    assert repo.github_repo_id == 33 and repo.installation_id == 22

    account["permissions"] = {"actions": "read", "contents": "read"}
    assert await event("new_permissions_accepted") == "accepted"
    assert (await installations.get_by_installation_id(22)).permissions == account["permissions"]

    accessible[0] = repository_payload(name="renamed")
    assert await event("renamed", "repository", repository=accessible[0]) == "accepted"
    renamed = await repositories.get_by_repo_id(33)
    assert renamed.name == "renamed" and renamed.created_at == repo.created_at
    assert await repositories.get_by_full_name("example/demo") is None

    account["suspended_at"] = "2026-11-21T10:00:00Z"
    assert await event("suspend") == "accepted"
    assert (await installations.get_by_installation_id(22)).status == "suspended"

    account["suspended_at"] = None
    assert await event("unsuspend") == "accepted"
    assert await event("suspend", delivery_id="late-suspend") == "accepted"
    assert (await installations.get_by_installation_id(22)).status == "active"

    get_repositories.side_effect = httpx.ReadTimeout("GitHub timed out")
    assert await event("added", "installation_repositories", "incomplete") == "accepted"
    snapshot = await installations.get_by_installation_id(22)
    assert snapshot.sync_version != snapshot.repository_sync_version
    assert (await WebhookDeliveryRepository().get_delivery("incomplete")).last_error == "github_timeout"
    assert (await repositories.get_by_repo_id(33)).is_active
    get_repositories.side_effect = None

    accessible.clear()
    assert await event("removed", "installation_repositories") == "accepted"
    assert not (await repositories.get_by_repo_id(33)).is_active
    accessible.append(repository_payload(33, name="renamed"))
    assert await event("added", "installation_repositories") == "accepted"
    assert (await repositories.get_by_repo_id(33)).is_active

    account.update(installation_payload(44, account_type="User"))
    accessible[0] = repository_payload(33, name="renamed", owner_id=44, login="octocat")
    assert await event("created", delivery_id="personal", installation_id=55) == "accepted"
    assert (await installations.get_by_installation_id(55)).account.type == "User"
    assert (await repositories.get_by_repo_id(33)).installation_id == 55

    assert await event("deleted") == "accepted"
    removed = await installations.get_by_installation_id(22)
    assert removed.status == "deleted" and removed.deleted_at is not None
    assert await event("unsuspend", delivery_id="late-unsuspend") == "accepted"
    assert (await installations.get_by_installation_id(22)).status == "deleted"
    assert (await repositories.get_by_repo_id(33)).is_active
    assert await MongoDb.get_collection("user_memberships").count_documents({}) == 0

    get_installation.return_value = None
    assert await event("removed", "installation_repositories", "not-visible", installation_id=55) == "accepted"
    assert (await installations.get_by_installation_id(55)).status == "unavailable"
    assert not (await repositories.get_by_repo_id(33)).is_active
    get_installation.return_value = account
    assert await event("added", "installation_repositories", "visible-again", installation_id=55) == "accepted"
    assert (await repositories.get_by_repo_id(33)).is_active


async def test_snapshot_fences_reject_stale_writes_and_preserve_transfer(mongo_db):
    installations = GitHubInstallationRepository()
    repositories = GitHubRepositoryRepository()
    company = InstallationMetadata.model_validate(installation_payload())
    repo = RepositoryMetadata.model_validate(repository_payload())

    assert await installations.begin_sync(22, 10)
    assert await installations.begin_sync(22, 20)
    assert not await installations.upsert_installation(22, company, version=10)
    await installations.finish_sync(22, 10)
    assert (await installations.get_by_installation_id(22)).repository_sync_version is None
    assert await installations.upsert_installation(22, company, version=20)

    await repositories.upsert_repository(22, repo, version=10)
    await repositories.deactivate_unsynced(22, version=20)
    await repositories.upsert_repository(22, repo, version=19)
    assert not (await repositories.get_by_repo_id(33)).is_active

    await repositories.upsert_repository(44, repo, version=15)
    await repositories.mark_repository_inactive(33, 22, version=30)
    await repositories.upsert_repository(22, repo, version=12)
    assert (await repositories.get_by_repo_id(33)).installation_id == 44
    assert (await repositories.get_by_repo_id(33)).is_active

    await installations.set_installation_status(22, "deleted", version=30)
    await installations.finish_sync(22, 20)
    assert not await installations.begin_sync(22, 40)
    assert (await installations.get_by_installation_id(22)).status == "deleted"


async def test_membership_requires_trusted_scope_and_current_access(mongo_db):
    users = UserRepository()
    memberships = MembershipRepository()
    installations = GitHubInstallationRepository()
    repositories = GitHubRepositoryRepository()
    service = GitHubAuthorizationService(users, memberships, installations, repositories)

    for user_id in ("user_org", "user_repo", "user_unverified"):
        await users.apply_clerk_event(user_id, "user.created", 1)
    company = InstallationMetadata.model_validate(installation_payload())
    await installations.begin_sync(22, 10)
    await installations.upsert_installation(22, company, version=10)
    await repositories.upsert_repository(22, RepositoryMetadata.model_validate(repository_payload()), version=10)
    await repositories.upsert_repository(22, RepositoryMetadata.model_validate(repository_payload(34)), version=10)
    await installations.finish_sync(22, 10)

    async def grant(user_id, resource_type, resource_id, *, verified=True):
        evidence = VerifiedMembershipGrant(
            clerk_user_id=user_id, resource_type=resource_type, resource_id=resource_id, installation_id=22,
            verification_source="github", verified_at=datetime.now(UTC),
        )
        verifier = AsyncMock()
        verifier.verify_membership.return_value = evidence if verified else None
        return await service.grant_membership(user_id, resource_type, resource_id, verifier=verifier)

    assert not await grant("user_missing", "installation", 22)
    assert not await grant("user_unverified", "installation", 22, verified=False)
    assert not await service.can_access_repository("user_unverified", 33)
    assert await grant("user_org", "installation", 22)
    assert await grant("user_repo", "repository", 33)
    assert await service.can_access_repository("user_repo", 33)
    assert not await service.can_access_repository("user_repo", 34)
    assert {repo.github_repo_id for repo in await service.list_authorized_repositories("user_org")} == {33, 34}
    assert [repo.github_repo_id for repo in await service.list_authorized_repositories("user_repo")] == [33]
    historical_run = WorkflowRunMetadata(
        github_repo_id=33, installation_id=44, run_id=1, workflow_id=1, head_sha="abc",
    )
    assert not await service.can_access_workflow_run("user_org", historical_run)
    assert await service.can_access_workflow_run("user_org", historical_run.model_copy(update={"installation_id": 22}))

    await memberships.revoke_membership("user_repo", "repository", 33)
    assert (await memberships.get_membership("user_repo", "repository", 33)).status == "revoked"
    assert not await service.can_access_repository("user_repo", 33)
    await repositories.mark_repository_inactive(33, 22, version=11)
    assert not await service.can_access_repository("user_org", 33)
    await installations.set_installation_status(22, "suspended", version=10)
    assert not await service.can_access_repository("user_org", 34)
    assert await service.list_authorized_repositories("user_org") == []
