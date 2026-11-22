import httpx

from app.services.gh_auth import generate_app_jwt
from config import settings

GITHUB_API = "https://api.github.com"


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def installation_summary(installation: dict) -> dict:
    return {
        "id": installation["id"],
        "account": installation["account"]["login"],
        "target_type": installation["target_type"],
        "repository_selection": installation["repository_selection"],
        "status": "suspended" if installation.get("suspended_at") else "active",
    }


async def verify_user_installations(
    code: str, verifier: str, callback_url: str, installation_id: int | None,
) -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": settings.GITHUB_APP_CLIENT_ID,
                "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": callback_url,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValueError("GitHub did not accept the authorization code.")

        page = 1
        installations = []
        while True:
            response = await client.get(
                f"{GITHUB_API}/user/installations",
                params={"per_page": 100, "page": page},
                headers=github_headers(token),
            )
            response.raise_for_status()
            installations.extend(
                installation_summary(item)
                for item in response.json()["installations"]
                if str(item["app_id"]) == settings.GITHUB_APP_ID
                and not item.get("suspended_at")
                and (installation_id is None or item["id"] == installation_id)
            )
            if "next" not in response.links or (installation_id is not None and installations):
                return installations
            page += 1


async def read_installation(
    client: httpx.AsyncClient, installation_id: int, headers: dict[str, str],
) -> dict | None:
    """Fetch the installation metadata as the GitHub App, if visible."""
    response = await client.get(f"{GITHUB_API}/app/installations/{installation_id}", headers=headers)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def read_installation_repositories(token: str) -> list[dict]:
    """Read every repository accessible to the installation token."""
    headers = github_headers(token)
    repositories = []
    page = 1
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            response = await client.get(
                f"{GITHUB_API}/installation/repositories",
                params={"per_page": 100, "page": page},
                headers=headers,
            )
            response.raise_for_status()
            page_repositories = response.json()["repositories"]
            if not isinstance(page_repositories, list):
                raise TypeError("Invalid GitHub installation repository list")
            repositories.extend(page_repositories)
            if "next" not in response.links:
                return repositories
            page += 1


async def read_installations(installation_ids: list[int]) -> list[dict]:
    if not installation_ids:
        return []

    headers = github_headers(generate_app_jwt())
    installations = []
    # shortcut: one GitHub request per linked account, batch if workspaces link many accounts.
    async with httpx.AsyncClient(timeout=10) as client:
        for installation_id in installation_ids:
            installation = await read_installation(client, installation_id, headers)
            if installation is not None:
                installations.append(installation_summary(installation))
    return installations
