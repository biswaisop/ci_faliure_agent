import asyncio
import logging
import os
from datetime import timedelta
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.write_concern import WriteConcern

from app.db.models.workflow_run import WorkflowRunKey
from app.db.mongodb import MongoDb
from app.db.repositories.fix_suggestion_repository import FixSuggestionRepository
from app.db.repositories.github_installation_repository import (
    GitHubInstallationRepository,
)
from app.db.repositories.github_repository import GitHubRepositoryRepository
from app.db.repositories.investigation_repository import InvestigationRepository
from app.db.repositories.pr_comment_repository import PRCommentRepository
from app.db.repositories.user_repositories import now_utc
from app.db.repositories.workflow_run_repository import WorkflowRunRepository
from app.schema.diagnosis import DiagnosisResult
from app.schema.fix_suggestion import FixSuggestion
from app.services.fix_suggestion_service import FixSuggestionService, suggestion_version
from app.services.gh_auth import get_installation_token
from app.services.github_pr_client import GitHubPullRequests
from app.services.github_source_reader import GitHubSourceReader
from app.services.github_webhook_event import RepositoryLocator
from app.services.pr_comment_publisher import PRCommentPublisher, github_failure
from config import settings
from dependencies import get_github_collector

logger = logging.getLogger(__name__)
FOLLOWUPS = "workflow_followups"
MAX_ATTEMPTS = 4


def followups_enabled() -> bool:
    return settings.AUTO_PCI_PR_COMMENTS or settings.AUTO_PCI_FIX_SUGGESTIONS


def initial_stages() -> list[str]:
    return [
        stage for stage, enabled in (
            ("diagnosis_comment", settings.AUTO_PCI_PR_COMMENTS), ("fix", settings.AUTO_PCI_FIX_SUGGESTIONS),
        ) if enabled
    ]


def followup_collection():
    return MongoDb.get_collection(FOLLOWUPS).with_options(write_concern=WriteConcern(w="majority"))


async def queue_followup(run, *, stages: list[str] | None = None) -> None:
    collection = followup_collection()
    for stage in initial_stages() if stages is None else stages:
        key = {**PRCommentRepository.key(run), "stage": stage}
        update = {"$setOnInsert": {"created_at": now_utc()}, "$set": {
            "due_at": now_utc() + timedelta(minutes=5), "attempts": 0, "status": "pending",
            "request_id": uuid4().hex, "error_code": None,
        }}
        try:
            await collection.update_one(key, update, upsert=True)
        except DuplicateKeyError:
            if not (await collection.update_one(key, update)).matched_count:
                raise


async def post_investigation(key: WorkflowRunKey) -> None:
    for stage in [*initial_stages(), *(["fix_comment"] if settings.AUTO_PCI_PR_COMMENTS else [])]:
        try:
            await run_followup(key, stage, ready=True)
        except PyMongoError:
            logger.warning("Follow-up checkpoint unavailable run_id=%s stage=%s", key.run_id, stage)


async def run_followup(key: WorkflowRunKey, stage: str, *, ready: bool = False) -> None:
    if not (settings.AUTO_PCI_FIX_SUGGESTIONS if stage == "fix" else settings.AUTO_PCI_PR_COMMENTS):
        return
    collection = followup_collection()
    now = now_utc()
    lease = uuid4().hex
    row = await collection.find_one_and_update(
        {
            **key.model_dump(include=set(WorkflowRunKey.model_fields)),
            "stage": stage,
            "attempts": {"$lt": MAX_ATTEMPTS},
            "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}],
            "due_at": {"$ne": None, **({} if ready else {"$lte": now})},
        },
        {"$set": {
            "lease_id": lease, "lease_until": now + timedelta(minutes=4), "status": "processing",
            "updated_at": now,
        }, "$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if row is None:
        return
    delay = None
    code = None
    tries = row["attempts"]
    try:
        async with asyncio.timeout(150):
            delay, code = await process_stage(key, row["installation_id"], stage)
    except Exception as exc:  # noqa: BLE001 - background work cannot change investigation success.
        code, delay = github_failure(exc)
        logger.warning("Investigation follow-up failed run_id=%s reason=%s", key.run_id, code)
    retry = delay is not None and tries < MAX_ATTEMPTS
    identity = {"_id": row["_id"], "lease_id": lease}
    result = await collection.update_one(
        {**identity, "request_id": row["request_id"]},
        {"$set": {
            "status": "retrying" if retry else "failed" if code or delay else "completed",
            "error_code": code,
            "due_at": now_utc() + timedelta(seconds=max(delay, tries * 30)) if retry else None,
            "lease_until": None, "updated_at": now_utc(),
        }},
    )
    if not result.matched_count:
        await collection.update_one(identity, {"$set": {"lease_until": None, "due_at": now_utc()}})


async def process_stage(key: WorkflowRunKey, installation_id: int, stage: str) -> tuple[int | None, str | None]:
    run = await WorkflowRunRepository().get_run(key, installation_id=installation_id)
    investigation = await InvestigationRepository(MongoDb.investigations()).get(key, include_fix_sources=stage == "fix")
    if not run or not investigation or investigation["status"] in {"pending", "running"}:
        return 60, "investigation_pending"
    if investigation["status"] != "completed":
        return None, "investigation_unavailable"
    context, classification = run.evidence.investigation_context, run.evidence.classification
    if context is None or classification is None:
        return None, "evidence_unavailable"
    if (
        context.github_repo_id != run.github_repo_id or context.run_id != run.run_id
        or context.run_attempt != run.run_attempt or context.head_sha != run.head_sha
    ):
        return None, "evidence_mismatch"
    diagnosis = DiagnosisResult.model_validate(investigation["diagnosis"])
    if not diagnosis.evidence_validated:
        return None, "diagnosis_unvalidated"
    installation = await GitHubInstallationRepository().get_by_installation_id(installation_id)
    repository = await GitHubRepositoryRepository().get_by_repo_id(run.github_repo_id)
    if (
        installation is None or installation.status != "active"
        or installation.sync_version != installation.repository_sync_version
        or repository is None or not repository.is_active or repository.installation_id != installation_id
        or repository.sync_version != installation.repository_sync_version
    ):
        return None, "installation_unavailable"
    token = await get_installation_token(installation_id, repository_id=run.github_repo_id)
    if not token:
        return 60, "unauthorized_installation"
    locator = RepositoryLocator(owner=repository.owner.login, name=repository.name, github_repo_id=run.github_repo_id)
    collector = get_github_collector()
    if stage == "fix":
        if run.github_head_repo_id not in {None, run.github_repo_id}:
            return None, "source_revision_unavailable"
        cache = investigation.get("fix_sources") or {}
        cached_sources = (
            {item["path"]: item["text"] for item in cache["files"]}
            if cache.get("sha") == context.head_sha else None
        )
        suggestion, retry = await FixSuggestionService(FixSuggestionRepository()).generate(
            run, context, classification, diagnosis, GitHubSourceReader(collector, locator, token),
            cached_sources=cached_sources,
            relevant_diffs=(investigation.get("investigation_metadata") or {}).get("relevant_diffs"),
        )
        if suggestion and suggestion.status == "suggested" and settings.AUTO_PCI_PR_COMMENTS:
            await queue_followup(run, stages=["fix_comment"])
        return retry, None
    fix = None
    if settings.AUTO_PCI_FIX_SUGGESTIONS:
        version = suggestion_version(context, classification, diagnosis)
        record = await FixSuggestionRepository().get(run, version)
        if record and record.get("status") == "suggested":
            fix = FixSuggestion.model_validate(record["suggestion"])
    github = GitHubPullRequests(collector, locator, token)
    publisher = PRCommentPublisher(
        PRCommentRepository(), settings.GITHUB_APP_SLUG, settings.GITHUB_APP_ID, os.getenv("FRONTEND_URL", ""),
    )
    return await publisher.publish(run, locator, github, diagnosis, classification, context, fix), None


async def resume_followups() -> None:
    """Resume persisted follow-ups while an API process is running."""
    while True:
        try:
            now = now_utc()
            collection = followup_collection()
            await collection.update_many(
                {"lease_until": {"$lte": now}, "attempts": {"$gte": MAX_ATTEMPTS}, "status": "processing"},
                {"$set": {"lease_until": None, "due_at": None, "status": "failed", "error_code": "interrupted"}},
            )
            stages = [*initial_stages(), *(["fix_comment"] if settings.AUTO_PCI_PR_COMMENTS else [])]
            cursor = collection.find({
                "due_at": {"$lte": now}, "attempts": {"$lt": MAX_ATTEMPTS}, "stage": {"$in": stages},
                "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}],
            }).sort("due_at", 1).limit(5)
            async for row in cursor:
                await run_followup(WorkflowRunKey.model_validate(row), row["stage"])
        except Exception as exc:  # noqa: BLE001 - a transient DB outage must not stop future sweeps.
            logger.warning("Investigation follow-up sweep failed type=%s", type(exc).__name__)
        await asyncio.sleep(30)
