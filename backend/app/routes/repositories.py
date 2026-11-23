from asyncio import gather
from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from app.core.clerk_auth import current_clerk_user_id
from app.db.models.github_repository import GitHubRepository
from app.db.models.workflow_run import WorkflowRun, WorkflowRunKey
from app.db.mongodb import MongoDb
from app.db.repositories.github_installation_repository import (
    GitHubInstallationRepository,
)
from app.db.repositories.github_repository import GitHubRepositoryRepository
from app.db.repositories.investigation_repository import InvestigationRepository
from app.db.repositories.membership_repository import MembershipRepository
from app.db.repositories.user_repositories import UserRepository
from app.db.repositories.workflow_run_repository import WorkflowRunRepository
from app.schema.diagnosis import DiagnosisResult
from app.services.github_authorization_service import GitHubAuthorizationService


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


@router.get("/{repo_id}/runs/{run_id}/attempts/{attempt}")
async def read_run(
    repo_id: GitHubPathId, run_id: GitHubPathId, attempt: GitHubPathId,
    user_id: ClerkUser, authorization: Authorization, runs: Runs, investigations: Investigations,
):
    repo = await authorization.get_authorized_repository(user_id, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Run unavailable.")
    run = await runs.get_run(
        WorkflowRunKey(github_repo_id=repo_id, run_id=run_id, run_attempt=attempt),
        installation_id=repo.installation_id,
    )
    if run is None or run.installation_id != repo.installation_id:
        raise HTTPException(status_code=404, detail="Run unavailable.")

    investigation = await investigations.get(run)
    status = investigation.get("status") if investigation else None
    diagnosis = investigation.get("diagnosis") if status == "completed" else None
    evidence = run.evidence
    context = evidence.investigation_context
    packets = (context.failure_packets if context else None) or evidence.failure_packets or run.failure_packets or []
    return {
        "repository": repository_summary(repo), "run": run_summary(run, status),
        "diagnosis": DiagnosisResult.model_validate(diagnosis) if diagnosis else None,
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
