from asyncio import gather
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import UTC
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.responses import StreamingResponse
from jwt import InvalidKeyError
from pydantic import BaseModel
from pymongo.errors import PyMongoError

from app.core.clerk_auth import current_clerk_user_id
from app.db.models.github_repository import GitHubRepository
from app.db.models.workflow_run import WorkflowRun, WorkflowRunKey
from app.db.mongodb import MongoDb
from app.db.repositories.fix_suggestion_repository import FixSuggestionRepository
from app.db.repositories.github_installation_repository import (
    GitHubInstallationRepository,
)
from app.db.repositories.github_repository import GitHubRepositoryRepository
from app.db.repositories.investigation_repository import InvestigationRepository
from app.db.repositories.membership_repository import MembershipRepository
from app.db.repositories.pr_comment_repository import PRCommentRepository
from app.db.repositories.user_repositories import UserRepository
from app.db.repositories.workflow_run_repository import WorkflowRunRepository
from app.schema.diagnosis import DiagnosisResult
from app.schema.fix_suggestion import FixSuggestionView
from app.services.fix_suggestion_service import suggestion_version
from app.services.gh_auth import get_installation_token
from app.services.github_authorization_service import GitHubAuthorizationService
from app.services.post_investigation import followup_collection, followups_enabled
from config import settings
from dependencies import get_github_collector


def prevent_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    prefix="/api/github/repositories", tags=["workflow-investigations"], dependencies=[Depends(prevent_caching)],
)
ClerkUser = Annotated[str, Depends(current_clerk_user_id)]
GitHubPathId = Annotated[int, Path(gt=0, le=2**63 - 1)]


def get_authorization() -> GitHubAuthorizationService:
    return GitHubAuthorizationService(
        UserRepository(), MembershipRepository(), GitHubInstallationRepository(), GitHubRepositoryRepository(),
    )


def get_investigations() -> InvestigationRepository:
    return InvestigationRepository(MongoDb.investigations())


def get_runs() -> WorkflowRunRepository:
    return WorkflowRunRepository()


Authorization = Annotated[GitHubAuthorizationService, Depends(get_authorization)]
Runs = Annotated[WorkflowRunRepository, Depends(get_runs)]
Investigations = Annotated[InvestigationRepository, Depends(get_investigations)]


def repository_summary(repo: GitHubRepository) -> dict:
    return {"id": str(repo.github_repo_id), "name": repo.name, "owner": repo.owner.login, "connection_status": "connected"}


def run_summary(run: WorkflowRun, investigation_status: str | None) -> dict:
    failed_at = run.github_updated_at or run.github_created_at or run.created_at
    return {
        "run_id": str(run.run_id), "run_attempt": run.run_attempt, "workflow_id": str(run.workflow_id),
        "run_number": run.run_number, "workflow_name": run.workflow_name, "branch": run.head_branch,
        "commit_sha": run.head_sha, "conclusion": run.conclusion, "html_url": run.html_url,
        "failed_at": failed_at.replace(tzinfo=failed_at.tzinfo or UTC),
        "collection_status": run.evidence.collection_status,
        "investigation_status": investigation_status or "unavailable",
        "classification": run.evidence.classification,
    }


async def recent_runs(
    repo: GitHubRepository, runs: WorkflowRunRepository, investigations: InvestigationRepository, limit: int,
) -> list[dict]:
    attempts = await runs.list_run_summaries(repo.github_repo_id, repo.installation_id, limit)
    statuses = await investigations.list_statuses(repo.github_repo_id, attempts)
    return [run_summary(run, statuses.get((run.run_id, run.run_attempt))) for run in attempts]


@router.get("")
async def list_repositories(
    user_id: ClerkUser, authorization: Authorization, runs: Runs, investigations: Investigations,
    offset: Annotated[int, Query(ge=0)] = 0,
    installation_id: Annotated[int | None, Query(gt=0, le=2**63 - 1)] = None,
):
    repositories = await authorization.list_authorized_repositories(user_id)
    if installation_id is not None:
        repositories = [repo for repo in repositories if repo.installation_id == installation_id]
    repositories = sorted(repositories, key=lambda repo: repo.github_repo_id)
    page_size = 20
    page = repositories[offset:offset + page_size]
    histories = await gather(*(recent_runs(repo, runs, investigations, 3) for repo in page))
    return {
        "repositories": [{**repository_summary(repo), "recent_runs": history} for repo, history in zip(page, histories)],
        "next_offset": offset + page_size if offset + page_size < len(repositories) else None,
    }


@router.get("/{repo_id}/runs")
async def list_runs(
    repo_id: GitHubPathId, user_id: ClerkUser, authorization: Authorization, runs: Runs, investigations: Investigations,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
):
    repo = await authorization.get_authorized_repository(user_id, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository unavailable.")
    return {"repository": repository_summary(repo), "runs": await recent_runs(repo, runs, investigations, limit)}


async def get_authorized_run(
    repo_id: GitHubPathId, run_id: GitHubPathId, attempt: GitHubPathId,
    user_id: ClerkUser, authorization: Authorization, runs: Runs,
) -> tuple[GitHubRepository, WorkflowRun]:
    repo = await authorization.get_authorized_repository(user_id, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Run unavailable.")
    run = await runs.get_run(
        WorkflowRunKey(github_repo_id=repo_id, run_id=run_id, run_attempt=attempt),
        installation_id=repo.installation_id,
    )
    if run is None or run.installation_id != repo.installation_id:
        raise HTTPException(status_code=404, detail="Run unavailable.")
    return repo, run


AuthorizedRun = Annotated[tuple[GitHubRepository, WorkflowRun], Depends(get_authorized_run)]


class PRCommentView(BaseModel):
    status: Literal["pending", "publishing", "published", "updated", "no_pr_found", "ambiguous_pr", "uncertain", "failed"]
    comment_url: str | None = None


class AttemptDetail(BaseModel):
    repository: dict
    run: dict
    diagnosis: DiagnosisResult | None
    evidence: dict
    fix_suggestion: FixSuggestionView | None = None
    pr_comment: PRCommentView | None = None


async def read_attempt_followups(
    repo: GitHubRepository, run: WorkflowRun, diagnosis: DiagnosisResult | None,
) -> dict:
    if not followups_enabled() or diagnosis is None:
        return {}
    stages = {
        row["stage"]: row["status"]
        async for row in followup_collection().find(
            PRCommentRepository.key(run), {"stage": 1, "status": 1},
        )
    }
    fix, comment = None, None
    context, classification = run.evidence.investigation_context, run.evidence.classification
    if settings.AUTO_PCI_FIX_SUGGESTIONS and context and classification:
        record = await FixSuggestionRepository().get(run, suggestion_version(context, classification, diagnosis))
        if record:
            fix = FixSuggestionView.model_validate(record)
        elif "fix" in stages:
            fix = FixSuggestionView(status="failed" if stages["fix"] == "failed" else "pending")
        if fix and stages.get("fix") in {"pending", "retrying", "processing"} and fix.status == "failed":
            fix = FixSuggestionView(status="pending")
        if fix and fix.status == "generating" and stages.get("fix") == "failed":
            fix = FixSuggestionView(status="failed")

    if settings.AUTO_PCI_PR_COMMENTS:
        row = await PRCommentRepository().get(run)
        queued = any(stages.get(stage) in {"pending", "retrying", "processing"} for stage in (
            "diagnosis_comment", "fix_comment",
        ))
        if row or "diagnosis_comment" in stages:
            comment_url = None
            if row:
                number, comment_id = row.get("pr_number"), row.get("comment_id")
                if type(number) is int and number > 0 and type(comment_id) is int and comment_id > 0:
                    owner, name = quote(repo.owner.login, safe=""), quote(repo.name, safe="")
                    comment_url = f"https://github.com/{owner}/{name}/pull/{number}#issuecomment-{comment_id}"
            status = row["status"] if row else "failed"
            if status == "publishing" and not queued:
                status = "uncertain" if row.get("create_uncertain") else "failed"
            comment = PRCommentView(status="pending" if queued else status, comment_url=comment_url)
    return {"fix_suggestion": fix, "pr_comment": comment}


@router.get("/{repo_id}/runs/{run_id}/attempts/{attempt}", response_model=AttemptDetail)
async def read_run(authorized_run: AuthorizedRun, investigations: Investigations):
    repo, run = authorized_run

    investigation = await investigations.get(run)
    status = investigation.get("status") if investigation else None
    saved = investigation.get("diagnosis") if status == "completed" else None
    diagnosis = DiagnosisResult.model_validate(saved) if saved else None
    evidence = run.evidence
    context = evidence.investigation_context
    packets = (context.failure_packets if context else None) or evidence.failure_packets or run.failure_packets or []
    try:
        followups = await read_attempt_followups(repo, run, diagnosis)
    except PyMongoError:
        followups = {}
    return {
        "repository": repository_summary(repo), "run": run_summary(run, status),
        "diagnosis": diagnosis,
        **followups,
        "evidence": {
            "failed_jobs": [
                {**job.model_dump(exclude={"log_reference"}), "job_id": str(job.job_id)} for job in run.failed_jobs
            ],
            "failure_packets": [{**packet.model_dump(), "job_id": str(packet.job_id)} for packet in packets],
            "changed_files": context.changed_files if context else [],
            "correlations": context.correlations if context else [],
            "base_sha": (context.base_sha if context else None) or evidence.base_sha,
            "diff_available": context.diff_available if context else False,
            "diff_may_be_incomplete": context.diff_may_be_incomplete if context else False,
            "comparison_status": context.comparison_status if context else "not_attempted",
            "comparison_reason": context.comparison_reason if context else None,
            "files_limit_reached": context.files_limit_reached if context else False,
        },
    }


async def open_failed_job_log(job_id: GitHubPathId, authorized_run: AuthorizedRun) -> AsyncIterator[httpx.Response]:
    repo, run = authorized_run
    if not any(job.job_id == job_id for job in run.failed_jobs):
        raise HTTPException(status_code=404, detail="Failed job unavailable.")

    async with AsyncExitStack() as stack:
        try:
            token = await get_installation_token(repo.installation_id)
            if not token:
                raise HTTPException(status_code=502, detail="Could not retrieve the log from GitHub.")
            log = await stack.enter_async_context(
                get_github_collector().open_job_log(repo.owner.login, repo.name, job_id, token),
            )
        except (httpx.HTTPError, OSError, ValueError, InvalidKeyError):
            raise HTTPException(status_code=502, detail="Could not retrieve the log from GitHub.") from None
        if log is None:
            raise HTTPException(status_code=404, detail="GitHub no longer has this job log.")
        yield log


@router.get("/{repo_id}/runs/{run_id}/attempts/{attempt}/jobs/{job_id}/log")
async def read_failed_job_log(log: Annotated[httpx.Response, Depends(open_failed_job_log, scope="request")]):
    return StreamingResponse(
        log.aiter_bytes(), media_type="text/plain",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
