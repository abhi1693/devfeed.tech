import base64
import json
import uuid

import pytest
from devfeed_aggregator import tasks
from devfeed_core import services
from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.models import Article, Base, IngestionJob, Topic, utcnow
from devfeed_core.schemas import JobOut, SourceDecision, SourcePatch
from devfeed_core.urls import fingerprint
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def approve_source(identifier):
    with session_factory().begin() as session:
        services.review_source(session, uuid.UUID(identifier), SourceDecision(decision="approved"))


def fetch_source(identifier):
    with session_factory().begin() as session:
        return JobOut.model_validate(
            services.fetch_source(session, uuid.UUID(identifier))
        ).model_dump(mode="json")


def edit_source(identifier, **values):
    with session_factory().begin() as session:
        services.update_source(session, uuid.UUID(identifier), SourcePatch(**values))


def add_source(client):
    response = client.post(
        "/v1/sources",
        json={
            "name": "Example Engineering",
            "feed_url": "https://example.com/rss",
            "source_type": "publisher",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["approval_status"] == "pending"
    approve_source(response.json()["id"])
    return response.json()["id"]


def ingest_fixture(client, rss_bytes, monkeypatch):
    source_id = add_source(client)
    job = fetch_source(source_id)
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    tasks.ingest(job["id"])
    return source_id


def test_empty_feed_and_taxonomy(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert client.get("/v1/feed").json() == {"items": [], "next_cursor": None}
    assert client.get("/v1/sources").json() == []
    assert client.get("/v1/tags").json() == []
    assert client.get("/v1/topics").json() == []


def test_no_account_models_or_authentication_routes(client):
    assert not {"admins", "admin_sessions", "audit_logs"} & Base.metadata.tables.keys()
    assert "hidden" not in Article.__table__.columns
    schema = client.get("/openapi.json").json()
    assert "securitySchemes" not in schema["components"]
    assert not any("admin" in path or "auth" in path for path in schema["paths"])
    assert not {"Login", "TokenOut", "AdminOut", "AuditOut", "ArticlePatch"} & set(
        schema["components"]["schemas"]
    )
    for path in ("/v1/admin/sources", "/v1/admin/audit", "/v1/admin/auth/me"):
        assert client.get(path).status_code == 404
    for path in ("/v1/admin/auth/login", "/v1/admin/auth/logout", "/v1/register"):
        assert client.post(path, json={}).status_code == 404
    # Source registration needs no tokens or account setup.
    assert add_source(client)


def test_source_management_validates_and_coalesces_jobs(client, database):
    source_id = add_source(client)
    first = fetch_source(source_id)
    second = fetch_source(source_id)
    assert first["id"] == second["id"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1
    assert (
        client.post(
            "/v1/sources",
            json={
                "name": "Duplicate",
                "feed_url": "https://example.com/rss",
                "source_type": "publisher",
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/v1/sources",
            json={
                "name": "Private",
                "feed_url": "http://127.0.0.1/",
                "source_type": "publisher",
            },
        ).status_code
        == 422
    )
    assert client.patch(f"/v1/sources/{source_id}", json={"enabled": False}).status_code == 405
    edit_source(source_id, enabled=False)
    assert client.post(f"/v1/sources/{source_id}/fetch").status_code == 404
    assert client.get("/v1/sources", params={"enabled": True}).json() == []
    assert "enabled" not in client.get("/v1/sources").json()[0]
    assert client.get("/v1/sources", params={"enabled": False}).json()[0]["id"] == source_id
    edit_source(source_id, enabled=True)
    assert client.get("/v1/sources", params={"enabled": True}).json()[0]["id"] == source_id


def test_feed_filters_search_and_article_detail(
    client, database, rss_bytes, monkeypatch, configured_tags, publish_for_read_test
):
    # Language is inferred from prose now, not the fixture's channel-wide en hint.
    rss_bytes = rss_bytes.replace(
        b"A practical <b>PostgreSQL</b> tutorial.",
        b"A practical <b>PostgreSQL</b> tutorial. This article explains how developers can build "
        b"reliable applications and store their information in a database. We discuss common "
        b"problems and test the changes before making the application available to readers.",
    )
    with database.begin() as session:
        session.add(
            Topic(
                kind="discipline",
                status="active",
                name="Backend",
                slug="backend",
                keywords=["fastapi"],
            )
        )
    source_id = ingest_fixture(client, rss_bytes, monkeypatch)
    assert client.get("/v1/feed").json()["items"] == []
    publish_for_read_test()
    response = client.get(
        "/v1/feed", params={"tag": "python", "language": "en", "content_type": "tutorial"}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 1
    article = response.json()["items"][0]
    assert len(article["sources"]) == 1
    assert article["sources"][0]["id"] == source_id
    assert "logo_url" in article["sources"][0]
    assert "feed_url" not in json.dumps(article)
    assert len(client.get("/v1/feed", params={"topic": "backend"}).json()["items"]) == 1
    assert len(client.get("/v1/feed", params={"q": "PostgreSQL tutorial"}).json()["items"]) == 1
    assert len(client.get("/v1/feed", params={"exclude_tag": "python"}).json()["items"]) == 1
    assert client.get("/v1/feed", params={"exclude_source": source_id}).json()["items"] == []
    assert len(client.get("/v1/feed", params={"source_id": source_id}).json()["items"]) == 2
    assert client.get(f"/v1/articles/{article['id']}").json() == article
    assert client.get(f"/v1/articles/{uuid.uuid4()}").status_code == 404
    assert client.patch(f"/v1/articles/{article['id']}", json={"hidden": True}).status_code == 405
    assert client.patch(f"/v1/admin/articles/{article['id']}", json={}).status_code == 404


def test_keyset_pagination_has_no_duplicates_at_equal_timestamps(
    client, database, publish_for_read_test
):
    now = utcnow()
    with database.begin() as session:
        for index in range(7):
            url = f"https://example.com/{index}"
            session.add(
                Article(
                    canonical_url=url,
                    url_hash=fingerprint(url),
                    title=f"Item {index}",
                    summary="",
                    published_at=now,
                    feed_at=now,
                )
            )
    publish_for_read_test()
    seen = []
    cursor = None
    for _ in range(4):
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/v1/feed", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
    assert cursor is None
    assert len(seen) == len(set(seen)) == 7


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor",
        "e30=",
        "bnVsbA==",
        base64.urlsafe_b64encode(json.dumps(["2026-01-01", str(uuid.uuid4())]).encode()).decode(),
    ],
)
def test_invalid_cursors_are_client_errors(client, cursor):
    assert client.get("/v1/feed", params={"cursor": cursor}).status_code == 422


def test_topic_edit_and_job_visibility(client, database, admin_client):
    response = admin_client.post(
        "/v1/admin/topics",
        json={"name": "Backend", "slug": "backend", "kind": "discipline", "keywords": ["fastapi"]},
    )
    assert response.status_code == 201
    topic_id = response.json()["id"]
    assert (
        admin_client.put(
            f"/v1/admin/topics/{topic_id}",
            json={
                "name": "Backend",
                "slug": "backend",
                "kind": "discipline",
                "keywords": ["django"],
            },
        ).status_code
        == 200
    )
    source_id = add_source(client)
    job = fetch_source(source_id)
    assert admin_client.get(f"/v1/admin/ingestion/jobs/{job['id']}").json()["status"] == "queued"
    assert admin_client.get("/v1/admin/ingestion/status").json()["jobs"] == {"queued": 1}
    assert admin_client.get("/v1/admin/ingestion/jobs").json() == [job]
    assert admin_client.get("/v1/admin/ingestion/jobs", params={"status": "failed"}).json() == []
    assert (
        admin_client.get("/v1/admin/ingestion/jobs", params={"source_id": str(uuid.uuid4())}).json()
        == []
    )
    assert (
        admin_client.get("/v1/admin/ingestion/jobs", params={"status": "invalid"}).status_code
        == 422
    )
    assert admin_client.get(f"/v1/admin/ingestion/jobs/{uuid.uuid4()}").status_code == 404


def test_unknown_sources_are_not_created_by_fetch_or_patch(client):
    source_id = uuid.uuid4()
    assert client.post(f"/v1/sources/{source_id}/fetch").status_code == 404
    assert client.patch(f"/v1/sources/{source_id}", json={"name": "Missing"}).status_code == 405
    assert client.get("/v1/sources").json() == []


def test_sources_can_be_filtered_by_type_and_type_is_immutable(client):
    publisher_id = add_source(client)
    response = client.post(
        "/v1/sources",
        json={
            "name": "Community",
            "feed_url": "https://community.example/rss",
            "source_type": "aggregator",
        },
    )
    assert response.status_code == 201, response.text
    aggregator_id = response.json()["id"]
    approve_source(aggregator_id)
    for kind, identifier in [("publisher", publisher_id), ("aggregator", aggregator_id)]:
        sources = client.get("/v1/sources", params={"source_type": kind}).json()
        assert [source["id"] for source in sources] == [identifier]
        assert sources[0]["source_type"] == kind
    assert client.get("/v1/sources", params={"source_type": "unknown"}).status_code == 422
    assert (
        client.patch(f"/v1/sources/{publisher_id}", json={"source_type": "aggregator"}).status_code
        == 405
    )
