from pydantic_core.core_schema import none_schema
import httpx

BASE_URL = "https://api.github.com"


async def get_failed_jobs(
    owner: str,
    repo: str,
    run_id: int,
    token: str
) -> list:
    """Fetch all failed jobs across paginated GitHub API responses."""

    url = (
        f"{BASE_URL}/repos/{owner}/{repo}"
        f"/actions/runs/{run_id}/jobs"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    failed_jobs = []
    page = 1
    per_page = 100

    async with httpx.AsyncClient(timeout=30) as client:
        params = {"per_page": 100}
        next_url = url

        while next_url:
            response = await client.get(
                next_url,
                headers=headers,
                params=params
            )
            response.raise_for_status()

            jobs = response.json().get("jobs", [])

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
    owner: str,
    repo: str,
    job_id: int,
    token: str,
    max_bytes: int = 2 * 1024 * 1024
) -> str | None:
    """
    Fetch a GitHub Actions job log.

    Returns None if logs are unavailable (404/410).
    Keeps only the last max_bytes of the log.
    """

    url = (
        f"{BASE_URL}/repos/{owner}/{repo}"
        f"/actions/jobs/{job_id}/logs"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=60
    ) as client:

        async with client.stream(
            "GET",
            url,
            headers=headers
        ) as response:

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
    owner: str,
    repo: str,
    base: str,
    head: str,
    token: str
) -> dict:
    """Compare two commits and return file diffs and commit summary."""

    url = (
        f"{BASE_URL}/repos/{owner}/{repo}"
        f"/compare/{base}...{head}"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    async with httpx.AsyncClient(timeout=50) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()

        data = response.json()

        return {
            "files": [
                {
                    "filename": f["filename"],
                    "previous_filename": f.get("previous_filename"),
                    "status": f["status"],
                    "additions": f["additions"],
                    "deletions": f["deletions"],
                    "patch": f.get("patch", ""),
                    "truncated": "patch" not in f
                }
                for f in data.get("files", [])
            ],
            "commits": data.get("total_commits")
        }

async def get_run_history(
    owner: str,
    repo: str,
    workflow_id: str,
    token: str,
    branch: str | None = None,
    per_page: int = 100
) -> list:
    """Fetch completed workflow runs with pagination."""

    url = (
        f"{BASE_URL}/repos/{owner}/{repo}"
        f"/actions/workflows/{workflow_id}/runs"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    params = {
        "per_page": per_page,
        "status": "completed"
    }

    if branch:
        params["branch"] = branch

    runs = []

    async with httpx.AsyncClient(timeout=30) as client:
        next_url = url

        while next_url:
            response = await client.get(
                next_url,
                headers=headers,
                params=params
            )
            response.raise_for_status()

            data = response.json()

            for run in data.get("workflow_runs", []):
                runs.append({
                    "id": run["id"],
                    "workflow_id": run["workflow_id"],
                    "run_number": run["run_number"],
                    "run_attempt": run["run_attempt"],
                    "head_sha": run["head_sha"],
                    "head_branch": run["head_branch"],
                    "status": run["status"],
                    "conclusion": run["conclusion"],
                    "created_at": run["created_at"],
                    "updated_at": run["updated_at"],
                    "event": run["event"]
                })

            next_url = response.links.get("next", {}).get("url")
            params = None

    return runs

async def get_run(
    owner: str,
    repo: str,
    run_id: str,
    token: str
) -> dict:
    """Fetch metadata for a specific GitHub Actions workflow run."""

    url = (
        f"{BASE_URL}/repos/{owner}/{repo}"
        f"/actions/runs/{run_id}"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()

        run = response.json()

        return {
            "id": run["id"],
            "workflow_id": run["workflow_id"],
            "head_sha": run["head_sha"],
            "head_branch": run["head_branch"],
            "event": run["event"],
            "run_attempt": run["run_attempt"],
            "status": run["status"],
            "conclusion": run["conclusion"],
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
            "html_url": run["html_url"]
        }

async def get_last_passing_run(
    owner: str,
    repo: str,
    workflow_id: str,
    branch: str,
    before_run: int,
    token: str,
) -> dict | None:
    """
    Find the most recent successful workflow run before a failing run.
    Returns its metadata, including head_sha for commit comparison.
    """

    failing_run = await get_run(
        owner=owner,
        repo=repo,
        run_id=before_run,
        token=token
    )

    history = await get_run_history(
        owner = owner,
        repo = repo,
        workflow_id=str(workflow_id),
        branch = branch,
        token = token,
        per_page = 100
    )

    successful_runs = [
        run for run in history
        if run["conclusion"] == "success"
        and run["id"] != before_run
        and run["created_at"] < failing_run["created_at"]
    ]
    if not successful_runs:
        return None

    last_passing_runs = max(
        successful_runs,
        key = lambda run: run["created_at"]
    )

    return last_passing_runs