"""
Manual verification test script for GitHub collector: failed jobs and job logs.
"""

import asyncio

from app.services.gh_auth import get_installation_token
from dependencies import get_github_collector

OWNER = "cachette-cloud"
REPO = "demo-repository"
INSTALLATION_ID = 183189785

# Replace with an actual failed run ID from demo-repository
RUN_ID = 44241368868


async def main():
    token = await get_installation_token(INSTALLATION_ID)
    collector = get_github_collector()

    jobs = await collector.get_failed_jobs(
        owner=OWNER,
        repo=REPO,
        run_id=RUN_ID,
        token=token,
    )

    for job in jobs:
        logs = await collector.get_job_log(
            owner=OWNER,
            repo=REPO,
            job_id=job["id"],
            token=token,
        )
        print(f"\n--- LOGS: {job['name']} ---")
        if logs:
            print(logs[:5000])


if __name__ == "__main__":
    asyncio.run(main())
