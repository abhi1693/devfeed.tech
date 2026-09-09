"""CRUD behavior against PostgreSQL; never test migration generation."""

import uuid

import pytest
from devfeed_core import services
from devfeed_core.models import Article, ArticleEnrichmentJob, IngestionJob, SourceEnrichmentJob
from devfeed_core.services import ValidatedSource
from devfeed_core.urls import fingerprint
from sqlalchemy import update

pytestmark = pytest.mark.integration


def create(client, resource, body):
    response = client.post(f"/v1/admin/{resource}", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def identity(name):
    suffix = uuid.uuid4().hex[:12]
    return {"name": f"CRUD test {name} {suffix}", "slug": f"crud-{name}-{suffix}"}


def test_admin_taxonomy_crud_and_references(admin_client):
    client = admin_client
    topic_body = {**identity("topic"), "kind": "language", "status": "active", "facts": []}
    first = create(client, "topics", topic_body)
    second = create(client, "topics", {**identity("second"), "kind": "framework"})
    assert client.post("/v1/admin/topics", json=topic_body).status_code == 409
    tag_body = {**identity("tag"), "topic_id": first["id"]}
    tag = create(client, "tags", tag_body)
    for resource, obj in (("topics", first), ("tags", tag)):
        detail = client.get(f"/v1/admin/{resource}/{obj['id']}")
        assert detail.status_code == 200
        assert detail.json()["id"] == obj["id"]
        page = client.get(f"/v1/admin/{resource}", params={"q": obj["name"], "limit": 1}).json()
        assert page["total"] == 1
        assert page["items"][0]["id"] == obj["id"]
        assert client.get(f"/v1/admin/{resource}", params={"sort": "unknown"}).status_code == 422
    assert client.get("/v1/admin/tags", params={"topic_id": first["id"]}).json()["total"] == 1
    updated = client.put(
        f"/v1/admin/tags/{tag['id']}", json={**tag_body, "aliases": ["test alias"]}
    )
    assert updated.status_code == 200
    assert updated.json()["aliases"] == ["test alias"]
    relation = {"topic_id": first["id"], "related_topic_id": second["id"], "relation": "related_to"}
    created = create(client, "topic-relations", relation)
    path = f"/v1/admin/topic-relations/{first['id']}/{second['id']}/related_to"
    assert client.get(path).json() == created
    assert client.post("/v1/admin/topic-relations", json=relation).status_code == 409
    assert (
        client.post(
            "/v1/admin/topic-relations", json={**relation, "related_topic_id": first["id"]}
        ).status_code
        == 409
    )
    assert (
        client.put(
            path, json={**relation, "evidence_url": "https://example.com/evidence"}
        ).status_code
        == 200
    )
    assert (
        client.get("/v1/admin/topic-relations", params={"topic_id": first["id"]}).json()["total"]
        == 1
    )
    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404
    for resource, obj in (
        ("tags", tag),
        ("topics", first),
        ("topics", second),
    ):
        assert client.delete(f"/v1/admin/{resource}/{obj['id']}").status_code == 204
        assert client.get(f"/v1/admin/{resource}/{obj['id']}").status_code == 404


def test_admin_article_crud_classification_and_publication(admin_client, monkeypatch):
    client = admin_client
    monkeypatch.setattr(
        services,
        "validate_source",
        lambda body: ValidatedSource(
            name=body.name,
            feed_url=body.feed_url,
            source_type="publisher",
            enabled=True,
            poll_interval_seconds=1800,
        ),
    )
    source = create(
        client,
        "sources",
        {
            "name": identity("source")["name"],
            "feed_url": f"https://example.com/{uuid.uuid4()}/feed",
            "source_type": "publisher",
        },
    )
    topic = create(client, "topics", {**identity("editorial"), "kind": "language"})
    article = create(
        client,
        "articles",
        {
            "title": "Python transaction verification",
            "canonical_url": f"https://example.com/{uuid.uuid4()}",
            "source_id": source["id"],
            "summary": (
                "Python developers use explicit transactions to ensure that "
                "these changes remain safely isolated."
            ),
            "language": "en",
        },
    )
    path = f"/v1/admin/articles/{article['id']}"
    assert article["review_status"] == "pending"
    assert article["publication_status"] == "unpublished"
    assert article["sources"][0]["id"] == source["id"]
    assert client.get("/v1/admin/articles", params={"source_id": source["id"]}).json()["total"] == 1
    assert (
        client.post(
            path + "/review", json={"action": "publish", "expected_revision": 0}
        ).status_code
        == 409
    )
    classification = {
        "developer_relevance": "relevant",
        "language": "en",
        "content_type": "article",
        "content_format": "article",
        "topics": [
            {"topic_id": topic["id"], "role": "primary", "relevance": 1, "evidence": "Python"}
        ],
        "tags": [],
        "expected_revision": 0,
    }
    bad = client.post(path + "/classify", json={**classification, "actor": "forged-actor"})
    assert bad.status_code == 422
    assert client.post(path + "/classify", json=classification).status_code == 200
    assert client.get("/v1/admin/articles", params={"topic_id": topic["id"]}).json()["total"] == 1
    approved = client.post(path + "/review", json={"action": "approve", "expected_revision": 1})
    assert approved.status_code == 200
    published = client.post(path + "/review", json={"action": "publish", "expected_revision": 2})
    assert published.status_code == 200, published.text
    assert published.json()["publication_status"] == "published"
    assert client.delete(path).status_code == 409
    changes = {
        "title": "Python updated transaction verification",
        "summary": article["summary"],
        "language": "en",
        "expected_revision": 3,
    }
    assert client.put(path, json={**changes, "expected_revision": 0}).status_code == 409
    edited = client.put(path, json=changes)
    assert edited.status_code == 200
    assert edited.json()["publication_status"] == "unpublished"
    assert edited.json()["review_status"] == "pending"
    assert edited.json()["ai_summary"] is None
    history = client.get(path + "/reviews").json()
    assert history["total"] == 5
    assert all(item["actor"] == "integration-admin" for item in history["items"])
    assert client.get(path + "/content").json() is None
    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed"])
def test_article_edit_respects_pending_page_enrichment(admin_client, database, status):
    with database.begin() as session:
        url = f"https://example.com/{uuid.uuid4()}"
        article = Article(
            canonical_url=url,
            url_hash=fingerprint(url),
            title="Original aggregator title",
            summary="Original aggregator summary",
            metadata_source_type="aggregator",
            editorial_revision=3,
        )
        session.add(article)
        session.flush()
        identifier = article.id
        session.add(ArticleEnrichmentJob(article_id=identifier, status=status))
    response = admin_client.put(
        f"/v1/admin/articles/{identifier}",
        json={"title": "Human title", "summary": "Human summary", "expected_revision": 3},
    )
    active = status in {"queued", "running"}
    assert response.status_code == (409 if active else 200), response.text
    if active:
        assert "enrichment is queued or running" in response.json()["detail"]
    with database() as session:
        saved = session.get(Article, identifier)
        assert saved.title == ("Original aggregator title" if active else "Human title")
        assert saved.summary == ("Original aggregator summary" if active else "Human summary")
        assert saved.editorial_revision == (3 if active else 4)


def test_admin_source_validation_review_jobs_and_delete(admin_client, database, monkeypatch):
    client = admin_client
    from devfeed_core.feeds.fetcher import FeedError
    from devfeed_core.feeds.validation import FeedValidationError

    def invalid(body):
        raise FeedValidationError(FeedError("Not a readable feed", reason="invalid_feed"))

    body = {
        "name": identity("rss")["name"],
        "feed_url": f"https://example.com/{uuid.uuid4()}/feed",
        "source_type": "publisher",
    }
    monkeypatch.setattr(services, "validate_source", invalid)
    assert client.post("/v1/admin/sources", json=body).status_code == 422
    assert client.get("/v1/admin/sources", params={"q": body["name"]}).json()["total"] == 0
    monkeypatch.setattr(
        services,
        "validate_source",
        lambda body: ValidatedSource(
            name=body.name,
            feed_url=body.feed_url,
            source_type="publisher",
            enabled=True,
            poll_interval_seconds=1800,
        ),
    )
    disabled = create(client, "sources", {**body, "enabled": False})
    assert disabled["approval_status"] == "approved" and not disabled["enabled"]
    for kind in ("ingestion", "source-enrichment"):
        assert (
            client.get(f"/v1/admin/jobs/{kind}", params={"source_id": disabled["id"]}).json()[
                "total"
            ]
            == 0
        )
    assert client.delete(f"/v1/admin/sources/{disabled['id']}").status_code == 204
    source = create(client, "sources", body)
    path = f"/v1/admin/sources/{source['id']}"
    assert source["approval_status"] == "approved"
    assert source["reviewed_by"] == "integration-admin"
    for endpoint, payload in (
        ("/v1/admin/sources", body),
        (
            "/v1/admin/sources/preview",
            {"feed_url": body["feed_url"], "source_type": body["source_type"]},
        ),
    ):
        duplicate = client.post(endpoint, json=payload)
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"][0]["type"] == "duplicate_feed_url"
    assert client.delete(path).status_code == 409
    assert client.patch(path, json={"description": "A verified feed"}).status_code == 200
    job = client.post(path + "/fetch").json()
    assert client.post(path + "/fetch").json()["id"] == job["id"]
    page = client.get("/v1/admin/jobs/ingestion", params={"source_id": source["id"]}).json()
    assert page["total"] == 1
    assert client.get(f"/v1/admin/jobs/ingestion/{job['id']}").json()["id"] == job["id"]
    assert "lease_token" not in str(page)
    assert client.post(path + "/review", json={"decision": "rejected"}).status_code == 409
    assert (
        client.post(
            path + "/review", json={"decision": "rejected", "note": "No longer relevant"}
        ).status_code
        == 200
    )
    assert client.post(path + "/fetch").status_code == 409
    assert client.get(path + "/reviews").json()["total"] == 2
    with database.begin() as session:
        session.execute(
            update(IngestionJob)
            .where(IngestionJob.source_id == uuid.UUID(source["id"]))
            .values(status="succeeded")
        )
        session.execute(
            update(SourceEnrichmentJob)
            .where(SourceEnrichmentJob.source_id == uuid.UUID(source["id"]))
            .values(status="succeeded")
        )
    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404
