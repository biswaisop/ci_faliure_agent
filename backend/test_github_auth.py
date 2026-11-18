
import asyncio
from app.services.gh_auth import (
    generate_app_jwt,
    get_installation_id,
    get_installation_token
)
from config import settings
import jwt
import httpx

OWNER = "cachette-cloud"
REPO = "demo-repository"


async def main():
    print("Generating JWT...")
    token = generate_app_jwt()
    print("JWT generated!")

    print("App ID:", settings.GITHUB_APP_ID)
    print("JWT generated:", bool(token))
    print("JWT claims:", jwt.decode(
    token,
    options={"verify_signature": False}
))

    print("Fetching installation ID...")
    installation_id = await get_installation_id(OWNER, REPO)
    print("Installation ID:", installation_id)

    print("Generating installation token...")
    installation_token = await get_installation_token(installation_id)
    print("Authentication successful!")

    print("Token received:", bool(installation_token))


async def test_app_auth():
    app_jwt = generate_app_jwt()

    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/app",
            headers=headers
        )

        print("Status:", response.status_code)
        print("Response:", response.text)


async def get_all_installations():
    app_jwt = generate_app_jwt()

    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/app/installations",
            headers=headers
        )

        response.raise_for_status()

        for installation in response.json():
            print(
                "Installation ID:",
                installation["id"],
                "Account:",
                installation["account"]["login"]
            )

if __name__ == "__main__":
    # installation_id = asyncio.run(get_installation_id(
    #     OWNER, REPO
    # ))
    # print("Installation ID:", installation_id)

    installation_id = 183189785

    token = asyncio.run(get_installation_token(installation_id))

    print("Token generated:", bool(token))
    asyncio.run(get_all_installations())
