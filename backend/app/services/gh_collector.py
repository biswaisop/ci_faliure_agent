import httpx

BASE_URL = "https://api.github.com"

async def get_failed_jobs(
    owner: str,
    repo: str,
    run_id: int,
    token: str
) -> list:
    """Fetch all failed jobs for a specific workflow run in a repository."""
    url = (
        f"{BASE_URL}/repos/{owner}/{repo}"
        f"/actions/runs/{run_id}/jobs"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url, 
            headers=headers,
            params={"per_page": 100}
        )

        response.raise_for_status()
        data = response.json()

        failed_jobs = [
            job for job in data["jobs"]
            if job["conclusion"] == "failure"
        ]

        return failed_jobs

async def get_job_log(
    owner: str,
    repo: str,
    job_id: str,
    token: str
) -> str:
    """Download the complete log text for a specific GitHub Actions job."""
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
        response = await client.get(url, headers=headers)
        response.raise_for_status()

        return response.text

async def get_commit_diff(
    owner: str,
    repo: str,
    base: str,
    head: str,
    token: str
) -> dict:
    """Compare two commits and return the file diffs and commit summary."""
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

        data=response.json()

        return {
            "files": [
                {
                    "filename": f["filename"],
                    "status": f["status"],
                    "additions": f["additions"],
                    "deletions": f["deletions"],
                    "patch": f.get("patch","")
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
    per_page: int = 10
) -> list:
    url = (
        f"https://api.github.com/repos/{owner}/{repo}"
        f"/actions/workflows/{workflow_id}/runs"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    params = {
        "per_page": per_page
    }
    if branch:
        params["branch"] = branch
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            url, 
            headers=headers,
            params=params
        )

        response.raise_for_status()
        data = response.json()

        return [
            {
                "id": run["id"],
                "run_number": run["run_number"],
                "head_sha": run["head_sha"],
                "head_branch": run["head_branch"],
                "status": run["status"],
                "conclusion": run["conclusion"],
                "created_at": run["created_at"],
                "updated_at": run["updated_at"],
                "event": run["event"]
            }
            for run in data.get("workflow_runs", [])
        ]

