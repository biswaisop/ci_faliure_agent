"""
GitHub API Collector Service.

Encapsulates all GitHub REST API interactions for collecting CI run details,
failed job steps, logs, commit diffs, and workflow execution history.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.schema.investigation_context import ComparisonReason, ComparisonStatus

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT = 30.0
COMPARE_FILE_LIMIT = 300
PATCH_BYTES_PER_FILE = 64 * 1024
PATCH_BYTES_TOTAL = 1024 * 1024


@dataclass(frozen=True)
class CommitComparison:
    status: ComparisonStatus
    base_sha: str | None = None
    diff: dict | None = None
    reason: ComparisonReason | None = None


def github_timestamp(value: str | datetime | None) -> datetime | None:
    try:
        timestamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return None
    return timestamp.replace(tzinfo=timestamp.tzinfo or UTC) if isinstance(timestamp, datetime) else None


def github_failure_reason(exc: Exception, *, comparing: bool) -> ComparisonReason:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code
        if status == 429 or (status == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers
        )):
            return "github_rate_limited"
        if status in (401, 403):
            return "github_access_denied"
        if comparing and status in (404, 422):
            return "commit_unavailable"
        return "github_unavailable"
    if isinstance(exc, httpx.RequestError):
        return "github_unavailable"
    return "invalid_response"


class GitHubCollector:
    """
    Collector service for querying GitHub REST API for CI postmortem analysis.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.timeout = timeout
        self._client = client

    @asynccontextmanager
    async def _get_client(self, timeout: float | None = None) -> AsyncIterator[httpx.AsyncClient]:
        """Provide an active httpx.AsyncClient instance."""
        if self._client is not None and not self._client.is_closed:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
                yield client

    def _get_headers(self, token: str) -> dict[str, str]:
        """Generate standardized GitHub API headers."""
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
        }

    async def get_failed_jobs(
        self,
        owner: str,
        repo: str,
        run_id: int | str,
        token: str,
        *,
        run_attempt: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch all failed jobs and their failed steps for a specific workflow run.
        Handles pagination across all pages.
        """
        run_url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        if run_attempt is not None:
            run_url = f"{run_url}/attempts/{run_attempt}"
        url = f"{run_url}/jobs"
        headers = self._get_headers(token)
        failed_jobs: list[dict[str, Any]] = []

        async with self._get_client() as client:
            params: dict[str, Any] | None = {"per_page": 100}
            next_url: str | None = url

            while next_url:
                response = await client.get(next_url, headers=headers, params=params)
                response.raise_for_status()

                jobs = response.json()["jobs"]
                if not isinstance(jobs, list):
                    raise TypeError("Invalid GitHub workflow job list")

                for job in jobs:
                    if job.get("conclusion") == "failure":
                        job["failed_steps"] = [
                            step
                            for step in job.get("steps", [])
                            if step.get("conclusion") == "failure"
                        ]
                        failed_jobs.append(job)

                next_url = response.links.get("next", {}).get("url")
                params = None

        return failed_jobs

    async def get_job_log(
        self,
        owner: str,
        repo: str,
        job_id: int | str,
        token: str,
        max_bytes: int = 2 * 1024 * 1024,
    ) -> str | None:
        """
        Fetch a GitHub Actions job log.

        Returns None if logs are unavailable (404/410).
        Keeps only the last max_bytes of the log.
        """
        async with self.open_job_log(owner, repo, job_id, token) as response:
            if response is None:
                return None

            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if len(buffer) > max_bytes:
                    del buffer[:-max_bytes]

            return buffer.decode("utf-8", errors="replace")

    @asynccontextmanager
    async def open_job_log(
        self, owner: str, repo: str, job_id: int | str, token: str,
    ) -> AsyncIterator[httpx.Response | None]:
        """Open the complete GitHub job log for streaming."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        headers = self._get_headers(token)

        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            if response.status_code in (404, 410):
                yield None
            else:
                response.raise_for_status()
                yield response

    async def get_commit_diff(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
        token: str,
    ) -> dict[str, Any]:
        """Compare two commits and return file diffs and commit summary."""
        url = f"{self.base_url}/repos/{owner}/{repo}/compare/{base}...{head}"
        headers = self._get_headers(token)

        async with self._get_client(timeout=50.0) as client:
            response = await client.get(url, headers=headers, params={"per_page": 1, "page": 1})
            response.raise_for_status()
            data = response.json()

        files = data["files"] if data.get("status") == "ahead" else data.get("files", [])
        if not isinstance(files, list) or any(not file.get("filename") or not file.get("status") for file in files):
            raise ValueError("Invalid GitHub comparison files")
        retained = []
        remaining = PATCH_BYTES_TOTAL
        for file in files[:COMPARE_FILE_LIMIT]:
            patch = (file.get("patch") or "").encode("utf-8")
            limit = min(PATCH_BYTES_PER_FILE, remaining)
            truncated = len(patch) > limit
            if truncated:
                patch = patch[:limit].rpartition(b"\n")[0]
            remaining -= len(patch)
            retained.append({
                "filename": file["filename"], "previous_filename": file.get("previous_filename"),
                "status": file["status"], "additions": file.get("additions", 0), "deletions": file.get("deletions", 0),
                "patch": patch.decode("utf-8"), "truncated": truncated,
                "patch_status": "truncated" if truncated else "available" if patch else "omitted",
            })

        return {
            "files": retained, "commits": data.get("total_commits", 0),
            "status": data.get("status"), "ahead_by": data.get("ahead_by"), "behind_by": data.get("behind_by"),
            "merge_base_sha": (data.get("merge_base_commit") or {}).get("sha"),
            "files_limit_reached": len(files) >= COMPARE_FILE_LIMIT,
        }

    async def get_run_history(
        self,
        owner: str,
        repo: str,
        workflow_id: int | str,
        token: str,
        branch: str | None = None,
        per_page: int = 100,
        *,
        include_active: bool = False,
        created_before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch completed workflow runs with pagination."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        headers = self._get_headers(token)
        params: dict[str, Any] | None = {"per_page": per_page}
        if not include_active:
            params["status"] = "completed"
        if created_before is not None:
            params["created"] = f"<={created_before.isoformat()}"
        if branch:
            params["branch"] = branch

        runs: list[dict[str, Any]] = []

        async with self._get_client() as client:
            next_url: str | None = url

            while next_url:
                response = await client.get(next_url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()

                workflow_runs = data["workflow_runs"]
                if not isinstance(workflow_runs, list):
                    raise TypeError("Invalid GitHub workflow history")
                for run in workflow_runs:
                    runs.append({
                        "id": run.get("id"),
                        "workflow_id": run.get("workflow_id"),
                        "run_number": run.get("run_number"),
                        "run_attempt": run.get("run_attempt"),
                        "head_sha": run.get("head_sha"),
                        "head_branch": run.get("head_branch"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "created_at": run.get("created_at"),
                        "updated_at": run.get("updated_at"),
                        "run_started_at": run.get("run_started_at"),
                        "head_repository_id": (run.get("head_repository") or {}).get("id"),
                        "event": run.get("event"),
                    })

                next_url = response.links.get("next", {}).get("url")
                params = None

        return runs

    async def get_run(
        self,
        owner: str,
        repo: str,
        run_id: int | str,
        token: str,
        *,
        run_attempt: int | None = None,
    ) -> dict[str, Any]:
        """Fetch metadata for a specific GitHub Actions workflow run."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        if run_attempt is not None:
            url = f"{url}/attempts/{run_attempt}"
        headers = self._get_headers(token)

        async with self._get_client() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            run = response.json()

            return {
                "id": run.get("id"),
                "workflow_id": run.get("workflow_id"),
                "head_sha": run.get("head_sha"),
                "head_branch": run.get("head_branch"),
                "event": run.get("event"),
                "run_attempt": run.get("run_attempt"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "run_started_at": run.get("run_started_at"),
                "head_repository_id": (run.get("head_repository") or {}).get("id"),
                "html_url": run.get("html_url"),
            }

    async def get_last_passing_run(
        self,
        owner: str,
        repo: str,
        workflow_id: int | str,
        branch: str,
        before_run: int | str,
        token: str,
    ) -> dict[str, Any] | None:
        """Return the latest earlier success for callers that only need run metadata."""
        failing = await self.get_run(owner, repo, before_run, token)
        completed = github_timestamp(failing.get("updated_at"))
        if not branch or failing.get("head_branch") != branch or completed is None:
            return None
        candidates, _ = await self._previous_successes(
            owner, repo, workflow_id, branch, before_run, failing.get("run_attempt") or 1,
            completed, failing.get("head_repository_id"), token,
        )
        return candidates[0] if candidates else None

    async def _previous_successes(
        self, owner: str, repo: str, workflow_id: int | str, branch: str, run_id: int | str, run_attempt: int,
        failed_at: datetime, head_repository_id: int | None, token: str,
    ) -> tuple[list[dict], bool]:
        history = await self.get_run_history(
            owner, repo, workflow_id, token, branch, include_active=True, created_before=failed_at,
        )

        def eligible(candidate: dict, *, same_run: bool) -> bool:
            completed = github_timestamp(candidate.get("updated_at"))
            started = github_timestamp(candidate.get("run_started_at"))
            return bool(
                candidate.get("conclusion") == "success" and candidate.get("status") == "completed"
                and str(candidate.get("workflow_id")) == str(workflow_id)
                and candidate.get("head_branch") == branch and candidate.get("head_sha")
                and (head_repository_id is None or candidate.get("head_repository_id") == head_repository_id)
                and completed and (started is None or started <= completed)
                and (completed < failed_at or (same_run and completed == failed_at))
            )

        async def earlier_attempt(candidate_id: int | str, latest: int, *, same_run: bool) -> dict | None:
            for number in range(latest - 1, 0, -1):
                try:
                    attempt = await self.get_run(owner, repo, candidate_id, token, run_attempt=number)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        continue
                    raise
                if attempt.get("run_attempt") == number and eligible(attempt, same_run=same_run):
                    return attempt
            return None

        candidates = []
        previous_attempt = await earlier_attempt(run_id, run_attempt, same_run=True)
        if previous_attempt:
            candidates.append(previous_attempt)
        for candidate in history:
            candidate_id = candidate.get("id")
            if not candidate_id or str(candidate_id) == str(run_id) or candidate.get("head_branch") != branch:
                continue
            created = github_timestamp(candidate.get("created_at"))
            if created and created > failed_at:
                continue
            if eligible(candidate, same_run=False):
                candidates.append(candidate)
            elif (candidate.get("run_attempt") or 1) > 1:
                previous = await earlier_attempt(candidate_id, candidate["run_attempt"], same_run=False)
                if previous:
                    candidates.append(previous)

        candidates.sort(key=lambda candidate: github_timestamp(candidate["updated_at"]), reverse=True)
        return candidates, len(history) >= 1000

    async def resolve_commit_comparison(
        self, owner: str, repo: str, *, workflow_id: int, branch: str | None, run_id: int, run_attempt: int,
        head_sha: str, failed_at: datetime | None, head_repository_id: int | None, token: str,
    ) -> CommitComparison:
        if not branch:
            return CommitComparison("not_attempted", reason="branch_unavailable")

        try:
            cutoff = github_timestamp(failed_at)
            if cutoff is None:
                failing = await self.get_run(owner, repo, run_id, token, run_attempt=run_attempt)
                if (
                    failing.get("run_attempt") == run_attempt and failing.get("conclusion") == "failure"
                    and failing.get("head_branch") == branch and failing.get("head_sha") == head_sha
                    and failing.get("workflow_id") == workflow_id
                ):
                    cutoff = github_timestamp(failing.get("updated_at"))
                    head_repository_id = head_repository_id or failing.get("head_repository_id")
            if cutoff is None:
                return CommitComparison("not_attempted", reason="failure_time_unavailable")
            candidates, history_limited = await self._previous_successes(
                owner, repo, workflow_id, branch, run_id, run_attempt, cutoff, head_repository_id, token,
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
            return CommitComparison("comparison_failed", reason=github_failure_reason(exc, comparing=False))

        result = CommitComparison("no_previous_success")
        examined = set()
        for candidate in candidates:
            base = candidate["head_sha"]
            if base in examined:
                continue
            examined.add(base)
            if base == head_sha:
                return CommitComparison("same_commit", base)
            try:
                diff = await self.get_commit_diff(owner, repo, base, head_sha, token)
            except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
                reason = github_failure_reason(exc, comparing=True)
                if reason == "commit_unavailable":
                    if result.status in ("no_previous_success", "baseline_not_ancestor"):
                        result = CommitComparison("comparison_failed", base, reason=reason)
                    continue
                return CommitComparison("comparison_failed", base, reason=reason)

            ahead = diff["ahead_by"]
            if diff["status"] == "ahead" and diff["behind_by"] == 0 and isinstance(ahead, int) and ahead > 0:
                if diff["merge_base_sha"] == base:
                    partial = diff["files_limit_reached"] or any(file["patch_status"] != "available" for file in diff["files"])
                    return CommitComparison("partial" if partial else "available", base, diff)
                if not diff["merge_base_sha"]:
                    return CommitComparison("comparison_failed", base, reason="invalid_response")
            elif diff["status"] not in ("behind", "diverged", "identical"):
                return CommitComparison("comparison_failed", base, reason="invalid_response")
            if result.status == "no_previous_success":
                result = CommitComparison("baseline_not_ancestor", base)

        if history_limited and result.status in ("no_previous_success", "baseline_not_ancestor"):
            return CommitComparison("comparison_failed", reason="history_limit")
        return result


# ============================================================================
# Standalone functions delegating to dependency factory for backward compatibility
# ============================================================================

def _get_collector() -> GitHubCollector:
    from dependencies import get_github_collector
    return get_github_collector()


async def get_failed_jobs(owner: str, repo: str, run_id: int | str, token: str) -> list[dict[str, Any]]:
    return await _get_collector().get_failed_jobs(owner=owner, repo=repo, run_id=run_id, token=token)


async def get_job_log(owner: str, repo: str, job_id: int | str, token: str, max_bytes: int = 2 * 1024 * 1024) -> str | None:
    return await _get_collector().get_job_log(owner=owner, repo=repo, job_id=job_id, token=token, max_bytes=max_bytes)


async def get_commit_diff(owner: str, repo: str, base: str, head: str, token: str) -> dict[str, Any]:
    return await _get_collector().get_commit_diff(owner=owner, repo=repo, base=base, head=head, token=token)


async def get_run_history(owner: str, repo: str, workflow_id: int | str, token: str, branch: str | None = None, per_page: int = 100) -> list[dict[str, Any]]:
    return await _get_collector().get_run_history(owner=owner, repo=repo, workflow_id=workflow_id, token=token, branch=branch, per_page=per_page)


async def get_run(owner: str, repo: str, run_id: int | str, token: str) -> dict[str, Any]:
    return await _get_collector().get_run(owner=owner, repo=repo, run_id=run_id, token=token)


async def get_last_passing_run(owner: str, repo: str, workflow_id: int | str, branch: str, before_run: int | str, token: str) -> dict[str, Any] | None:
    return await _get_collector().get_last_passing_run(owner=owner, repo=repo, workflow_id=workflow_id, branch=branch, before_run=before_run, token=token)
