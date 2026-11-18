import httpx

BASE_URL = "https://api.github.com"

async def get_failed_jobs(
    owner: str,
    repo: str,
    run_id: int,
    token: str
) -> list:

    url = (
        f"{BASE_URL}/repos/{owner}/{repo}"
        f"/actions/runs/{run_id}/jobs"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-Github_Api-Version": "2022-11-28"
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
    url = (
        f"https://api.github.com/repos/{owner}/{repo}"
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
