
import asyncio

from app.services.gh_auth import get_installation_token
from app.services.gh_collector import get_failed_jobs, get_job_log

OWNER = "cachette-cloud"
REPO = "demo-repository"
INSTALLATION_ID = 183189785

# Replace with an actual failed run ID from demo-repository
RUN_ID = 44241368868


async def main():
    token = await get_installation_token(INSTALLATION_ID)

    jobs = await get_failed_jobs(
        OWNER, REPO, RUN_ID, token
    )

    for job in jobs:
        logs = await get_job_log(
            OWNER,
            REPO,
            job["id"],
            token
        )

    print(f"\n--- LOGS: {job['name']} ---")
    print(logs[:5000])

    


if __name__ == "__main__":
    asyncio.run(main())
    
