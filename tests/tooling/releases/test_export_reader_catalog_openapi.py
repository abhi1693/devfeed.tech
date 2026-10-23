"""The public discovery contract must track real reader routes, not service internals."""

import json

from scripts.export_reader_catalog_openapi import build_catalog_contract
from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT


def test_reader_catalog_contract_is_current_and_offline(monkeypatch):
    def reject_connection(*args, **kwargs):
        raise AssertionError("Catalog generation attempted a network connection")

    monkeypatch.setattr("socket.socket.connect", reject_connection)
    document = build_catalog_contract()
    assert document == json.loads((ROOT / "apps/web/reader-openapi.json").read_text())
    for path, methods in document["paths"].items():
        assert path.startswith("/api/v1/")
        assert "health" not in path and "admin" not in path
        route = path.removeprefix("/api/")
        for parameter in route.split("/"):
            if parameter.startswith("{"):
                route = route.replace(parameter, "[" + parameter[1:-1] + "]")
        assert (ROOT / "apps/web/src/app/api" / route / "route.ts").is_file()
        for operation in methods.values():
            assert not any(key.startswith("x-devfeed-") for key in operation)


def test_catalog_describes_gateway_wrappers_and_account_security():
    document = build_catalog_contract()
    paths = document["paths"]
    for kind in ("topics", "sources", "tags"):
        schema = paths[f"/api/v1/{kind}"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert set(schema["properties"]) == {"items", "next_cursor"}
    article = paths["/api/v1/articles/{slug}"]["get"]
    schema = article["responses"]["200"]["content"]["application/json"]["schema"]
    assert set(schema["properties"]) == {"article", "topic"}
    assert paths["/api/v1/user/bookmarks"]["get"]["security"]
    assert "security" not in paths["/api/v1/feed"]["get"]
