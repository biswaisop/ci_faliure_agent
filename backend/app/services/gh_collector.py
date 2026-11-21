"""
GitHub API Collector Service.

Encapsulates all GitHub REST API interactions for collecting CI run details,
failed job steps, logs, commit diffs, and workflow execution history.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT = 30.0


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
    ) -> list[dict[str, Any]]:
        """
        Fetch all failed jobs and their failed steps for a specific workflow run.
        Handles pagination across all pages.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        headers = self._get_headers(token)
        failed_jobs: list[dict[str, Any]] = []

        async with self._get_client() as client:
            params: dict[str, Any] | None = {"per_page": 100}
            next_url: str | None = url

            while next_url:
                response = await client.get(next_url, headers=headers, params=params)
                response.raise_for_status()

                data = response.json()
                jobs = data.get("jobs", [])

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
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        headers = self._get_headers(token)

        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code in (404, 410):
                    return None

                response.raise_for_status()

                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        del buffer[:-max_bytes]

                return buffer.decode("utf-8", errors="replace")

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
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            return {
                "files": [
                    {
                        "filename": f.get("filename"),
                        "previous_filename": f.get("previous_filename"),
                        "status": f.get("status"),
                        "additions": f.get("additions", 0),
                        "deletions": f.get("deletions", 0),
                        "patch": f.get("patch", ""),
                        "truncated": "patch" not in f,
                    }
                    for f in data.get("files", [])
                ],
                "commits": data.get("total_commits", 0),
            }

    async def get_run_history(
        self,
        owner: str,
        repo: str,
        workflow_id: int | str,
        token: str,
        branch: str | None = None,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch completed workflow runs with pagination."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        headers = self._get_headers(token)
        params: dict[str, Any] | None = {
            "per_page": per_page,
            "status": "completed",
        }
        if branch:
            params["branch"] = branch

        runs: list[dict[str, Any]] = []

        async with self._get_client() as client:
            next_url: str | None = url

            while next_url:
                response = await client.get(next_url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()

                for run in data.get("workflow_runs", []):
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
    ) -> dict[str, Any]:
        """Fetch metadata for a specific GitHub Actions workflow run."""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
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
        """
        Find the most recent successful workflow run before a failing run.
        Returns its metadata, including head_sha for commit comparison.
        """
        failing_run = await self.get_run(
            owner=owner,
            repo=repo,
            run_id=before_run,
            token=token,
        )

        history = await self.get_run_history(
            owner=owner,
            repo=repo,
            workflow_id=workflow_id,
            branch=branch,
            token=token,
            per_page=100,
        )

        failing_created_at = failing_run.get("created_at", "")
        successful_runs = [
            run
            for run in history
            if run.get("conclusion") == "success"
            and str(run.get("id")) != str(before_run)
            and run.get("created_at", "") < failing_created_at
        ]
        if not successful_runs:
            return None

        last_passing_run = max(
            successful_runs,
            key=lambda run: run.get("created_at", ""),
        )

        return last_passing_run


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
