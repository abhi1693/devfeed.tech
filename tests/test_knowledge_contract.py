from devfeed_admin_api.main import create_app
from devfeed_api.main import create_app as public_app
from fastapi.testclient import TestClient


def test_graph_is_admin_only_and_read_only(monkeypatch):
    from devfeed_admin_api import auth
    from devfeed_admin_api.config import Settings

    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            admin_base_url="https://admin.example",
            oidc_issuer_url="https://identity.example",
            oidc_client_id="test",
            oidc_organization_id="org",
        ),
    )
    app = create_app()
    with TestClient(app) as client:
        for path in ["graph", "search", "path"]:
            assert client.get(f"/v1/admin/knowledge/{path}").status_code == 401
    routes = app.openapi()["paths"]
    for path, operations in routes.items():
        if path.startswith("/v1/admin/knowledge/"):
            assert set(operations) == {"get"}
    assert not any("knowledge" in path for path in public_app().openapi()["paths"])
