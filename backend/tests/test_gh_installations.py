from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from jwt import InvalidKeyError

from app.core.clerk_auth import current_clerk_user_id
from app.main import app
from app.routes import gh_installations
from app.services import gh_installations as github
from config import settings


def github_installation(installation_id, account, account_type):
    return {
        "id": installation_id,
        "app_id": int(settings.GITHUB_APP_ID),
        "target_type": account_type,
        "account": {"login": account},
        "repository_selection": "selected",
        "suspended_at": None,
    }


@pytest.mark.parametrize("error, detail, log_reason", [
    (
        InvalidKeyError("sensitive key diagnostics"),
        "The GitHub App private key is unreadable or invalid.",
        "InvalidKeyError",
    ),
    (
        httpx.HTTPStatusError(
            "sensitive upstream diagnostics",
            request=httpx.Request("GET", "https://api.github.com/app/installations/14"),
            response=httpx.Response(401),
        ),
        "Could not check installations with GitHub.",
        "401",
    ),
])
def test_installation_list_reports_failures_without_exposing_details(monkeypatch, caplog, error, detail, log_reason):
    async def find_links(*args):
        yield {"installation_id": 14}

    monkeypatch.setitem(app.dependency_overrides, current_clerk_user_id, lambda: "user_first")
    monkeypatch.setattr(gh_installations.MongoDb, "github_installations", lambda: SimpleNamespace(find=find_links))
    read = AsyncMock(side_effect=error)
    monkeypatch.setattr(gh_installations, "read_installations", read)

    response = TestClient(app).get("/api/github/installations")

    assert response.status_code == 502
    assert response.json() == {"detail": detail}
    assert f"({log_reason})" in caplog.text
    assert "sensitive" not in caplog.text
    read.assert_awaited_once_with([14])


def test_installation_connection_is_verified_and_scoped_to_the_clerk_user(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setattr(settings, "GITHUB_APP_SLUG", "example-app")
    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_ID", "example-client")
    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_SECRET", "example-secret")

    client = TestClient(app)
    assert client.get("/api/github/installations").status_code == 401
    user_id = "user_first"
    monkeypatch.setitem(app.dependency_overrides, current_clerk_user_id, lambda: user_id)

    states = {}
    links = {}

    async def insert_state(document):
        states[document["_id"]] = document

    async def consume_state(query):
        state = states.get(query["_id"])
        if (
            not state
            or state["clerk_user_id"] != query["clerk_user_id"]
            or state["purpose"] != query["purpose"]
            or state["expires_at"] <= query["expires_at"]["$gt"]
            or ("installation_ids" in query and query["installation_ids"] not in state["installation_ids"])
        ):
            return None
        return states.pop(query["_id"])

    async def save_link(query, update, **kwargs):
        links.setdefault(query["installation_id"], set()).add(update["$addToSet"]["clerk_user_ids"])

    async def find_links(query, projection):
        for installation_id, users in links.items():
            if query["clerk_user_ids"] in users:
                yield {"installation_id": installation_id}

    state_collection = SimpleNamespace(insert_one=insert_state, find_one_and_delete=consume_state)
    collection = SimpleNamespace(update_one=AsyncMock(side_effect=save_link), find=find_links)
    monkeypatch.setattr(gh_installations.MongoDb, "get_collection", lambda name: state_collection)
    monkeypatch.setattr(gh_installations.MongoDb, "github_installations", lambda: collection)
    monkeypatch.setattr(github, "generate_app_jwt", lambda: "test-app-jwt")

    personal = github_installation(14, "octocat", "User")
    organization = github_installation(25, "example-org", "Organization")
    authorized_installations = [personal, organization]
    app_installations = {14: personal, 25: organization}

    def github_api(request):
        if request.url.path == "/login/oauth/access_token":
            token_params = parse_qs(request.content.decode())
            assert token_params["code"] == ["test-code"]
            assert token_params["redirect_uri"] == ["http://localhost:3000/dashboard/github"]
            assert len(token_params["code_verifier"][0]) == 43
            return httpx.Response(200, json={"access_token": "test-user-token"})
        if request.url.path == "/user/installations":
            assert request.headers["Authorization"] == "Bearer test-user-token"
            return httpx.Response(200, json={"installations": authorized_installations})
        assert request.headers["Authorization"] == "Bearer test-app-jwt"
        installation = app_installations.get(int(request.url.path.rsplit("/", 1)[1]))
        return httpx.Response(200, json=installation) if installation else httpx.Response(404)

    transport = httpx.MockTransport(github_api)
    async_client = httpx.AsyncClient
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: async_client(transport=transport, **kwargs))

    base = "/api/github/installations"

    def start(body):
        response = client.post(f"{base}/authorize", json=body)
        assert response.status_code == 200
        url = urlparse(response.json()["url"])
        assert url.hostname == "github.com"
        params = parse_qs(url.query)
        assert params["code_challenge_method"] == ["S256"]
        return {"state": params["state"][0], "code": "test-code"}

    assert client.get(base).json() == {
        "install_url": "https://github.com/apps/example-app/installations/new",
        "installations": [],
    }
    unauthorized = start({"installation_id": 90})
    assert client.post(f"{base}/complete", json=unauthorized).status_code == 403
    collection.update_one.assert_not_awaited()

    returned = start({"installation_id": 14})
    user_id = "user_other"
    assert client.post(f"{base}/complete", json=returned).status_code == 400
    user_id = "user_first"
    assert client.post(f"{base}/complete", json=returned).json() == {"status": "connected"}
    assert client.post(f"{base}/complete", json=returned).status_code == 400

    existing = client.post(f"{base}/complete", json=start({})).json()
    assert existing["status"] == "select"
    assert client.post(f"{base}/link", json={"state": existing["state"], "installation_id": 90}).status_code == 400
    selected = {"state": existing["state"], "installation_id": 25}
    assert client.post(f"{base}/link", json=selected).json() == {"status": "connected"}
    assert client.post(f"{base}/link", json=selected).status_code == 400
    assert links == {14: {"user_first"}, 25: {"user_first"}}

    organization["suspended_at"] = "2026-11-22T12:00:00Z"
    app_installations.pop(14)
    assert client.get(base).json()["installations"] == [github.installation_summary(organization)]
    user_id = "user_other"
    assert client.get(base).json()["installations"] == []
