import json
import uuid

import pytest
from devfeed_core.topic_proposals import TopicImport, TopicReview, parse_import
from pydantic import ValidationError


@pytest.mark.parametrize("content", ["{}", "[]", "[1]", '[{"name":"A","name":"B"}]', "["])
def test_json_import_rejects_ambiguous_or_invalid_documents(content):
    with pytest.raises(ValueError):
        parse_import(TopicImport(format="json", content=content, source_name="test.json"))


@pytest.mark.parametrize(
    "content",
    ["name,name\nA,B", "name,slug,approved\nA,a,true", "name,slug\nA,a,extra", "name,slug\nA"],
)
def test_csv_import_rejects_duplicate_unknown_or_misaligned_columns(content):
    with pytest.raises(ValueError):
        parse_import(TopicImport(format="csv", content=content, source_name="test.csv"))


def test_csv_import_handles_bom_quoted_fields_and_explicit_clearing():
    rows = parse_import(
        TopicImport(
            format="csv",
            source_name="test.csv",
            content=(
                "\ufeffname,slug,description,keywords,website_url\n"
                '"Data, storage",data,,sql| postgres,\n'
            ),
        )
    )
    assert rows == [
        {
            "name": "Data, storage",
            "slug": "data",
            "description": None,
            "keywords": ["sql", "postgres"],
            "website_url": None,
        }
    ]


def test_import_bounds_are_enforced_in_bytes_and_rows():
    for content in (
        json.dumps([{"name": "A", "slug": "a"}] * 101),
        json.dumps([{"name": "界" * 90_000}], ensure_ascii=False),
    ):
        with pytest.raises(ValueError):
            parse_import(TopicImport(format="json", content=content, source_name="test.json"))


def test_review_requires_explicit_fields_and_cannot_forge_actor():
    for body in (
        {"decision": "approved"},
        {"decision": "rejected", "actor": "someone-else"},
        {"decision": "rejected", "topic": {"name": "A", "slug": "a"}},
    ):
        with pytest.raises(ValidationError):
            TopicReview.model_validate(body)


def test_proposal_routes_are_private():
    from devfeed_admin_api.auth import get_settings
    from devfeed_admin_api.dependencies import get_session
    from devfeed_admin_api.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    app.dependency_overrides[get_session] = lambda: pytest.fail("Unauthenticated DB access")
    with TestClient(app) as client:
        for method, path in (
            ("get", "/topic-proposals"),
            ("get", "/topic-proposals/filters"),
            ("get", f"/topic-proposals/{uuid.uuid4()}"),
            ("delete", f"/topic-proposals/{uuid.uuid4()}"),
            ("post", "/topic-imports/preview"),
            ("post", "/topic-discovery/github"),
            ("post", "/topic-imports"),
            ("post", f"/topic-proposals/{uuid.uuid4()}/review"),
            ("post", f"/topics/{uuid.uuid4()}/enrichment/preview"),
            ("post", f"/topics/{uuid.uuid4()}/enrichment"),
        ):
            # Configured auth returns 401; an installation without OIDC fails closed with 503.
            assert getattr(client, method)("/v1/admin" + path).status_code in {401, 503}
    get_settings.cache_clear()


def test_categories_are_absent_from_reader_admin_and_analysis_contracts():
    from devfeed_admin_api.main import create_app as admin_app
    from devfeed_api.main import create_app as reader_app
    from devfeed_core.analysis import AnalysisResult

    for app in (admin_app(), reader_app()):
        schema = app.openapi()
        assert all("categor" not in path for path in schema["paths"])
        for name, definition in schema["components"]["schemas"].items():
            if "Notification" not in name:
                assert "categories" not in definition.get("properties", {})
    assert "categories" not in AnalysisResult.model_fields


@pytest.mark.parametrize(
    "profile", [{}, {"name": " "}, {"name": " Alex Morgan ", "email": "alex@example.com"}]
)
def test_proposal_actor_records_profile_without_session_secrets(profile):
    from devfeed_admin_api.auth import AdminIdentity
    from devfeed_admin_api.topic_proposals import actor

    identity = {
        "subject": "admin-1",
        "issuer": "https://identity.example",
        "organization_id": "org",
    }
    admin = AdminIdentity(
        **identity,
        **profile,
        roles=["superuser"],
        csrf_token="private-session-csrf",
        expires_at=4102444800,
    )
    result = actor(admin)
    assert {field: result[field] for field in identity} == identity
    assert "csrf_token" not in result and "roles" not in result and "expires_at" not in result
    assert result.get("name") == (profile.get("name", "").strip() or None)
    assert result.get("email") == profile.get("email")
