import hashlib
import hmac
import time
from pathlib import Path

import httpx
import jwt

from config import settings


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify that the incoming GitHub webhook payload matches the HMAC-SHA256 signature."""
    if not signature or not settings.GITHUB_WEBHOOK_SECRET:
        return False

    whs = settings.GITHUB_WEBHOOK_SECRET

    expected = "sha256="+hmac.new(
        whs.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)

def generate_app_jwt() -> str:
    """Generate a short-lived RS256 JWT for authenticating as a GitHub App."""
    private_key = Path(settings.GITHUB_PRIVATE_KEY_PATH).read_text()
    
    now = int(time.time())

    payload = {
        "iat": now-60,
        "exp": now+540,
        "iss": settings.GITHUB_APP_ID
    }

    return jwt.encode(payload, private_key, algorithm="RS256")

async def get_installation_id(owner: str, repo: str) -> int:
    """Fetch the GitHub App installation ID for a given repository."""
    app_jwt = generate_app_jwt()

    url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repo}/installation"
    )

    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()["id"]

async def get_installation_token(installation_id: int) -> str:
    """Create an installation access token for authenticating API requests on behalf of the app."""
    app_jwt = generate_app_jwt()
    url = (
        f"https://api.github.com/app/installations/"
        f"{installation_id}/access_tokens"
    )

    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        return response.json().get("token") or ""


