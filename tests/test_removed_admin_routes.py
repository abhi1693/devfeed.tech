"""Removed explorer routes must not remain callable or in the admin contract."""

from devfeed_admin_api.main import create_app
from fastapi.testclient import TestClient


def test_knowledge_explorer_endpoints_are_removed():
    app = create_app()
    assert not any(path.startswith("/v1/admin/knowledge/") for path in app.openapi()["paths"])
    with TestClient(app) as client:
        for path in ("graph", "search", "path"):
            assert client.get(f"/v1/admin/knowledge/{path}").status_code == 404
