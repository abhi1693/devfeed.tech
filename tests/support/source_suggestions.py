"""Signed-in source setup for domain integration tests; auth has separate coverage."""

from unittest.mock import patch

from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient


def identity():
    return UserIdentity(
        subject="contributor",
        issuer="https://identity.example",
        organization_id="org",
        user_id="00000000-0000-4000-8000-000000000001",
        name="Contributor",
        expires_at=4102444800,
        csrf_token="test",
    )


def suggest_source(*, json):
    app = create_app()
    app.dependency_overrides[require_user] = identity
    with patch("devfeed_user_api.sources.limit_suggestions"), TestClient(app) as client:
        return client.post("/v1/user/sources/suggestions", json=json)
