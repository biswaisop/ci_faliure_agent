import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from svix.webhooks import Webhook

from app.main import app


def test_signed_user_created_webhook_upserts_profile(monkeypatch):
    signing_secret = "whsec_d2ViaG9vay10ZXN0LWtleQ=="
    monkeypatch.setenv("CLERK_WEBHOOK_SIGNING_SECRET", signing_secret)
    now = datetime.now(UTC)
    event_timestamp = int(now.timestamp() * 1000)
    body = json.dumps({
        "type": "user.created",
        "timestamp": event_timestamp,
        "data": {
            "id": "user_example",
            "primary_email_address_id": "email_example",
            "email_addresses": [{"id": "email_example", "email_address": "developer@example.test"}],
            "first_name": "Example",
            "last_name": "Developer",
        },
    })
    headers = {
        "svix-id": "msg_example",
        "svix-timestamp": str(int(now.timestamp())),
        "svix-signature": Webhook(signing_secret).sign("msg_example", now, body),
    }
    client = TestClient(app)

    with patch("app.db.repositories.user_repositories.MongoDb.users") as users:
        write = users.return_value.update_one = AsyncMock(
            return_value=SimpleNamespace(matched_count=0, upserted_id="profile_id"),
        )
        assert client.post("/webhooks/clerk", content=body + " ", headers=headers).status_code == 400
        write.assert_not_awaited()

        response = client.post("/webhooks/clerk", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"status": "processed"}
        write.assert_awaited_once()
        selector, update = write.await_args.args
        assert selector["clerk_user_id"] == "user_example"
        assert update["$set"]["email"] == "developer@example.test"
        assert update["$set"]["name"] == "Example Developer"
        assert update["$set"]["clerk_event_ts"] == event_timestamp
        assert write.await_args.kwargs == {"upsert": True}
