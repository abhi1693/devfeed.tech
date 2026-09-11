import uuid

import pytest
from devfeed_aggregator import tasks
from devfeed_core import services
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.models import Article, Tag, Topic
from devfeed_core.schemas import SourceDecision
from source_suggestions import suggest_source
from sqlalchemy import select

pytestmark = pytest.mark.integration


def create_topic(admin_client, name, slug, keywords=None):
    response = admin_client.post(
        "/v1/admin/topics",
        json={
            "name": name,
            "slug": slug,
            "kind": "discipline",
            "keywords": keywords or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_tag(admin_client, name, slug, aliases=None, topic_id=None):
    response = admin_client.post(
        "/v1/admin/tags",
        json={
            "name": name,
            "slug": slug,
            "aliases": aliases or [],
            "topic_id": topic_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_runtime_tags_aliases_topic_grouping_and_rename(
    client, database, rss_bytes, monkeypatch, admin_client, publish_for_read_test
):
    child = create_topic(admin_client, "Orchestration", "orchestration")
    tag = create_tag(admin_client, "Cluster platform", "cluster-platform", ["k8s"], child["id"])
    source = suggest_source(
        json={"name": "Example", "feed_url": "https://example.com/rss", "source_type": "publisher"},
    ).json()
    with database.begin() as session:
        source_id = uuid.UUID(source["id"])
        services.review_source(session, source_id, SourceDecision(decision="approved"))
        job = {"id": str(services.fetch_source(session, source_id).id)}
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    tasks.ingest(job["id"])
    publish_for_read_test()
    page = client.get("/v1/feed", params={"tag": "cluster-platform"}).json()
    assert len(page["items"]) == 1
    article = page["items"][0]
    assert article["tags"] == ["cluster-platform"]
    # A tag link does not imply topic relevance.
    assert all(t["slug"] != "orchestration" for t in article["topics"])
    assert client.get("/v1/feed", params={"topic": "orchestration"}).json()["items"] == []
    assert client.get("/v1/tags", params={"topic_id": child["id"]}).json()[0]["id"] == tag["id"]
    # Explicit feed labels are imported; other words in article text are not new tags.
    assert sorted(item["slug"] for item in client.get("/v1/tags").json()) == [
        "cluster-platform",
        "python",
    ]
    changed = admin_client.patch(
        f"/v1/admin/tags/{tag['id']}",
        json={"name": "Cluster runtime", "slug": "cluster-runtime"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["aliases"] == ["k8s"]
    assert client.get("/v1/feed", params={"tag": "cluster-platform"}).json()["items"] == []
    assert (
        client.get("/v1/feed", params={"tag": "cluster-runtime"}).json()["items"][0]["id"]
        == article["id"]
    )
    assert (
        len(client.get("/v1/feed", params={"exclude_tag": "cluster-runtime"}).json()["items"]) == 1
    )
    admin_client.patch(f"/v1/admin/tags/{tag['id']}", json={"topic_id": None}).raise_for_status()
    assert client.get("/v1/feed", params={"topic": "orchestration"}).json()["items"] == []


def test_taxonomy_write_validation(client, admin_client):
    body = {"name": "New platform", "slug": "new-platform", "aliases": ["np-engine"]}
    assert admin_client.post("/v1/admin/tags", json=body).status_code == 201
    assert admin_client.post("/v1/admin/tags", json=body).status_code == 409
    body["slug"] = "orphan"
    body["topic_id"] = str(uuid.uuid4())
    assert admin_client.post("/v1/admin/tags", json=body).status_code == 404
    topic = create_topic(admin_client, "Custom", "custom")
    tag = create_tag(admin_client, "Custom engine", "custom-engine", topic_id=topic["id"])
    assert (
        admin_client.patch(f"/v1/admin/tags/{tag['id']}", json={"aliases": None}).status_code == 422
    )
    assert (
        admin_client.put(f"/v1/admin/topics/{topic['id']}", json={"name": None}).status_code == 422
    )
    assert client.get("/v1/tags").json()[0]["aliases"] == []


def test_feed_tags_are_imported_without_creating_topics(
    client, database, rss_bytes, monkeypatch, admin_client, publish_for_read_test
):
    source = suggest_source(
        json={"name": "Example", "feed_url": "https://example.com/rss", "source_type": "publisher"},
    ).json()
    with database.begin() as session:
        source_id = uuid.UUID(source["id"])
        services.review_source(session, source_id, SourceDecision(decision="approved"))
        job = {"id": str(services.fetch_source(session, source_id).id)}
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    tasks.ingest(job["id"])
    with database() as session:
        assert session.scalars(select(Topic)).all() == []
        assert set(session.scalars(select(Tag.slug)).all()) == {"python", "k8s"}
    with database() as session:
        items = session.scalars(select(Article)).all()
        assert len(items) == 2
        assert all(item.tags and not item.topic_links for item in items)
