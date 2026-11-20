
import asyncio
from dotenv import load_dotenv

from app.services.gh_auth import get_installation_token
from app.services.gh_collector import get_commit_diff

load_dotenv()

OWNER = "cachette-cloud"
REPO = "demo-repository"
INSTALLATION_ID = 183189785

# Replace these with actual commit SHAs from your repository
BASE_SHA = "d0a1c8852e0c542aea093eea1651a757c5fd2ee0"
HEAD_SHA = "ea5319f98565e005c7e393297eaad1e179430f0f"


async def main():
    print("Generating installation token...")

    token = await get_installation_token(INSTALLATION_ID)

    print("Fetching commit diff...")

    result = await get_commit_diff(
        OWNER,
        REPO,
        BASE_SHA,
        HEAD_SHA,
        token
    )

    print("\n========== COMMIT DIFF ==========")
    print("Total commits:", result["commits"])
    print("Changed files:", len(result["files"]))

    for file in result["files"]:
        print("\nFile:", file["filename"])
        print("Status:", file["status"])
        print("Additions:", file["additions"])
        print("Deletions:", file["deletions"])
        print("Patch:")
        print(file["patch"] or "Patch unavailable")

    print("\n========== TEST COMPLETE ==========")


if __name__ == "__main__":
    asyncio.run(main())
