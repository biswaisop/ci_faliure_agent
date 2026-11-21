from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app.main import app


def test_current_profile_requires_a_session_and_retries_after_sync(monkeypatch):
    origin = "http://localhost:3000"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setenv("CLERK_JWT_KEY", public_key.decode())
    monkeypatch.setenv("FRONTEND_URL", origin)

    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "user_example", "azp": origin, "iat": now, "exp": now + timedelta(minutes=1)},
        key,
        algorithm="RS256",
    )
    profile = {
        "clerk_user_id": "user_example",
        "name": "Example Developer",
        "email": "developer@example.test",
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    headers = {"Authorization": f"Bearer {token}"}
    with (
        patch("app.main.MongoDb.connect") as connect,
        patch("app.main.MongoDb.disconnect"),
        patch("app.main.setup_indexes") as setup_indexes,
        patch("app.main.MongoDb.users") as users,
        TestClient(app) as client,
    ):
        connect.assert_called_once_with()
        setup_indexes.assert_awaited_once_with()
        lookup = users.return_value.find_one = AsyncMock(side_effect=[None, profile])
        assert client.get("/api/users/me").status_code == 401
        lookup.assert_not_awaited()

        assert client.get("/api/users/me", headers=headers).status_code == 404
        response = client.get("/api/users/me", headers=headers)
        assert response.status_code == 200
        assert response.json()["name"] == profile["name"]
        assert response.json()["email"] == profile["email"]
        assert [call.args for call in lookup.await_args_list] == [({"clerk_user_id": "user_example"},)] * 2
