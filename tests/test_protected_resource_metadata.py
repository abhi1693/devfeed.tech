"""Discovery uses the exact resource/issuer used by the bearer-token validator."""

from devfeed_user_api import protected_resource
from devfeed_user_api.config import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_public_resource_metadata_needs_no_credentials_or_connections(monkeypatch):
    settings = Settings(
        _env_file=None,
        base_url="https://reader.example",
        mcp_resource_url="https://mcp.example/custom/mcp",
        mcp_issuer_url="https://authorization.example",
    )
    monkeypatch.setattr(protected_resource, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(protected_resource.router)
    response = TestClient(app).get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "resource": settings.mcp_resource_url,
        "authorization_servers": [settings.mcp_issuer_url],
        "bearer_methods_supported": ["header"],
    }
    assert "set-cookie" not in response.headers


def test_missing_resource_configuration_is_not_advertised(monkeypatch):
    monkeypatch.setattr(protected_resource, "get_settings", lambda: Settings(_env_file=None))
    response = protected_resource.metadata()
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_resource_metadata_is_registered_on_the_real_user_app():
    from devfeed_user_api.main import create_app

    document = create_app().openapi()
    assert "/.well-known/oauth-protected-resource" in document["paths"]
