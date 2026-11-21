"""
Manual verification test script for GitHub collector: workflow run history.
"""

import asyncio

from app.services.gh_auth import get_installation_token
from dependencies import get_github_collector

OWNER = "cachette-cloud"
REPO = "demo-repository"
INSTALLATION_ID = 183189785

# Replace with your actual workflow ID
WORKFLOW_ID = "auto-assign.yml"


async def main():
    print("Generating installation token...")
    token = await get_installation_token(INSTALLATION_ID)
    collector = get_github_collector()

    print("Fetching workflow history...")
    history = await collector.get_run_history(
        owner=OWNER,
        repo=REPO,
        workflow_id=WORKFLOW_ID,
        token=token,
        per_page=10,
    )

    print("\n========== WORKFLOW HISTORY ==========")
    print("Total runs retrieved:", len(history))

    for run in history:
        print("\nRun ID:", run["id"])
        print("Run number:", run["run_number"])
        print("Branch:", run["head_branch"])
        print("Status:", run["status"])
        print("Conclusion:", run["conclusion"])
        print("Commit:", run["head_sha"])
        print("Event:", run["event"])
        print("Created:", run["created_at"])

    print("\n========== TEST COMPLETE ==========")


if __name__ == "__main__":
    asyncio.run(main())
