import re
from dataclasses import dataclass
from typing import Literal

from app.db.models.workflow_run import WorkflowRunMetadata
from app.services.github_webhook_event import RepositoryLocator

COMMIT_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class PullRequestResolution:
    status: Literal["resolved", "no_pr_found", "ambiguous_pr"]
    number: int | None = None


async def resolve_pull_request(
    run: WorkflowRunMetadata, repo: RepositoryLocator, github,
) -> PullRequestResolution:
    if (
        run.github_repo_id != repo.github_repo_id
        or run.event not in {"pull_request", "pull_request_target", "push"}
        or not COMMIT_SHA.fullmatch(run.head_sha)
    ):
        return PullRequestResolution("no_pr_found")

    if run.event != "push" and run.pull_requests:
        hints = {hint.number: hint for hint in run.pull_requests if hint.base_repo_id == repo.github_repo_id}
        if len(hints) != len(run.pull_requests):
            return PullRequestResolution("no_pr_found")
        numbers = list(hints)
    else:
        if run.event != "push" and run.github_head_repo_id is None:
            return PullRequestResolution("no_pr_found")
        hints = {}
        numbers = await github.associated_pull_requests(run.head_sha)
        if numbers is None:
            return PullRequestResolution("ambiguous_pr")

    if len(numbers) > 1:
        return PullRequestResolution("ambiguous_pr")
    if not numbers:
        return PullRequestResolution("no_pr_found")

    number = numbers[0]
    if type(number) is not int or not 0 < number <= 2**63 - 1:
        return PullRequestResolution("no_pr_found")
    pr = await github.get_pull_request(number)
    if pr is None or pr.get("number") != number or pr.get("state") != "open":
        return PullRequestResolution("no_pr_found")
    base, head = pr.get("base") or {}, pr.get("head") or {}
    if (base.get("repo") or {}).get("id") != repo.github_repo_id:
        return PullRequestResolution("no_pr_found")
    head_repo = (head.get("repo") or {}).get("id")
    if not head_repo or (run.github_head_repo_id is not None and run.github_head_repo_id != head_repo):
        return PullRequestResolution("no_pr_found")
    if head.get("ref") != run.head_branch or not run.head_branch:
        return PullRequestResolution("no_pr_found")

    hint = hints.get(number)
    if hint and (hint.head_repo_id != head_repo or hint.head_sha != head.get("sha")):
        return PullRequestResolution("no_pr_found")
    same_head = run.head_sha == head.get("sha")
    verified_merge = hint is not None and run.head_sha == pr.get("merge_commit_sha")
    if (same_head or verified_merge) and (run.event != "push" or head_repo == repo.github_repo_id):
        return PullRequestResolution("resolved", number)
    return PullRequestResolution("no_pr_found")
