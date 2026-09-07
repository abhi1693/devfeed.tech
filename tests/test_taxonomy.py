import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_aggregator import tasks
from devfeed_core import services
from devfeed_core.categories import MAX_CATEGORY_DEPTH
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.models import Article, ArticleCategory, Category, Tag, utcnow
from devfeed_core.schemas import SourceDecision
from devfeed_core.urls import fingerprint
from sqlalchemy import select

pytestmark = pytest.mark.integration


def create_category(admin_client, name, slug, parent_id=None, keywords=None):
    response = admin_client.post(
        "/v1/admin/categories",
        json={
            "name": name,
            "slug": slug,
            "parent_id": parent_id,
            "keywords": keywords or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_tag(admin_client, name, slug, aliases=None, category_id=None):
    response = admin_client.post(
        "/v1/admin/tags",
        json={
            "name": name,
            "slug": slug,
            "aliases": aliases or [],
            "category_id": category_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_nested_category_tree_and_reparenting(client, admin_client):
    root = create_category(admin_client, "Engineering", "engineering")
    child = create_category(admin_client, "Languages", "languages", root["id"])
    leaf = create_category(admin_client, "Python", "python", child["id"])
    tree = client.get("/v1/categories/tree").json()
    assert tree[0]["id"] == root["id"]
    assert tree[0]["children"][0]["children"][0]["id"] == leaf["id"]
    assert tree[0]["children"][0]["parent_id"] == root["id"]
    assert [
        row["id"] for row in client.get("/v1/categories", params={"parent_id": child["id"]}).json()
    ] == [leaf["id"]]
    assert (
        admin_client.patch(
            f"/v1/admin/categories/{child['id']}", json={"parent_id": None}
        ).status_code
        == 200
    )
    tree = client.get("/v1/categories/tree").json()
    assert len(tree) == 2
    assert next(row for row in tree if row["id"] == child["id"])["children"][0]["id"] == leaf["id"]


def test_unknown_parent_self_parent_and_descendant_cycles_are_rejected(client, admin_client):
    root = create_category(admin_client, "Root", "root")
    child = create_category(admin_client, "Child", "child", root["id"])
    for parent in (root["id"], child["id"]):
        response = admin_client.patch(
            f"/v1/admin/categories/{root['id']}", json={"parent_id": parent}
        )
        assert response.status_code == 409
    assert (
        admin_client.patch(
            f"/v1/admin/categories/{child['id']}",
            json={"parent_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    assert (
        admin_client.post(
            "/v1/admin/categories",
            json={"name": "Orphan", "slug": "orphan", "parent_id": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    tree = client.get("/v1/categories/tree").json()
    assert tree[0]["parent_id"] is None
    assert tree[0]["children"][0]["id"] == child["id"]


def test_concurrent_reparenting_cannot_create_cycle(client, admin_client):
    first = create_category(admin_client, "First", "first")
    second = create_category(admin_client, "Second", "second")

    def move(pair):
        target, parent = pair
        return admin_client.patch(
            f"/v1/admin/categories/{target['id']}",
            json={"parent_id": parent["id"]},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(move, [(first, second), (second, first)]))
    assert sorted(results) == [200, 409]
    tree = client.get("/v1/categories/tree").json()
    assert len(tree) == 1 and len(tree[0]["children"]) == 1


def test_reparenting_checks_depth_of_entire_subtree(client, database, admin_client):
    parent_id = None
    with database.begin() as session:
        # The moved branch itself would fit; its child would exceed the limit.
        for index in range(MAX_CATEGORY_DEPTH - 1):
            category = Category(name=f"Level {index}", slug=f"level-{index}", parent_id=parent_id)
            session.add(category)
            session.flush()
            parent_id = category.id
    branch = create_category(admin_client, "Branch", "branch")
    create_category(admin_client, "Leaf", "leaf", branch["id"])
    response = admin_client.patch(
        f"/v1/admin/categories/{branch['id']}",
        json={"parent_id": str(parent_id)},
    )
    assert response.status_code == 409
    assert "levels" in response.json()["detail"]


def test_parent_feed_includes_descendants_and_reparenting_is_immediate(
    client, database, admin_client, publish_for_read_test
):
    root = create_category(admin_client, "Engineering", "engineering")
    child = create_category(admin_client, "Languages", "languages", root["id"])
    leaf = create_category(admin_client, "Python", "python", child["id"])
    other = create_category(admin_client, "Other", "other")
    with database.begin() as session:
        article = Article(
            title="A nested article",
            summary="",
            canonical_url="https://example.com/nested",
            url_hash=fingerprint("nested"),
            published_at=utcnow(),
        )
        session.add(article)
        session.flush()
        session.add(ArticleCategory(article_id=article.id, category_id=uuid.UUID(leaf["id"])))
        article_id = str(article.id)
    publish_for_read_test()
    for slug in ("engineering", "languages", "python"):
        page = client.get("/v1/feed", params={"category": slug}).json()
        assert [item["id"] for item in page["items"]] == [article_id]
    assert (
        client.get(
            "/v1/feed", params={"category": "engineering", "include_descendants": False}
        ).json()["items"]
        == []
    )
    assert client.get("/v1/feed", params={"category": "missing"}).json()["items"] == []
    admin_client.patch(
        f"/v1/admin/categories/{child['id']}",
        json={"parent_id": other["id"]},
    ).raise_for_status()
    assert client.get("/v1/feed", params={"category": "engineering"}).json()["items"] == []
    assert len(client.get("/v1/feed", params={"category": "other"}).json()["items"]) == 1


def test_runtime_tags_aliases_category_grouping_and_rename(
    client, database, rss_bytes, monkeypatch, admin_client, publish_for_read_test
):
    root = create_category(admin_client, "Infrastructure", "infrastructure")
    child = create_category(admin_client, "Orchestration", "orchestration", root["id"])
    tag = create_tag(admin_client, "Cluster platform", "cluster-platform", ["k8s"], child["id"])
    source = client.post(
        "/v1/sources",
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
    assert article["categories"][0]["id"] == child["id"]
    assert len(client.get("/v1/feed", params={"category": "infrastructure"}).json()["items"]) == 1
    assert (
        client.get("/v1/tags", params={"category": "infrastructure"}).json()[0]["id"] == tag["id"]
    )
    # No fixed Python tag should have appeared just because the feed mentions Python.
    assert [item["slug"] for item in client.get("/v1/tags").json()] == ["cluster-platform"]
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
    admin_client.patch(f"/v1/admin/tags/{tag['id']}", json={"category_id": None}).raise_for_status()
    assert client.get("/v1/feed", params={"category": "infrastructure"}).json()["items"] == []


def test_taxonomy_write_validation(client, admin_client):
    body = {"name": "New platform", "slug": "new-platform", "aliases": ["np-engine"]}
    assert admin_client.post("/v1/admin/tags", json=body).status_code == 201
    assert admin_client.post("/v1/admin/tags", json=body).status_code == 409
    body["slug"] = "orphan"
    body["category_id"] = str(uuid.uuid4())
    assert admin_client.post("/v1/admin/tags", json=body).status_code == 404
    category = create_category(admin_client, "Custom", "custom")
    tag = create_tag(admin_client, "Custom engine", "custom-engine", category_id=category["id"])
    assert (
        admin_client.patch(f"/v1/admin/tags/{tag['id']}", json={"aliases": None}).status_code == 422
    )
    assert (
        admin_client.patch(
            f"/v1/admin/categories/{category['id']}", json={"name": None}
        ).status_code
        == 422
    )
    assert client.get("/v1/tags").json()[0]["aliases"] == []


def test_empty_taxonomy_stays_empty_after_ingestion(
    client, database, rss_bytes, monkeypatch, admin_client, publish_for_read_test
):
    source = client.post(
        "/v1/sources",
        json={"name": "Example", "feed_url": "https://example.com/rss", "source_type": "publisher"},
    ).json()
    with database.begin() as session:
        source_id = uuid.UUID(source["id"])
        services.review_source(session, source_id, SourceDecision(decision="approved"))
        job = {"id": str(services.fetch_source(session, source_id).id)}
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    tasks.ingest(job["id"])
    publish_for_read_test()
    with database() as session:
        assert session.scalars(select(Category)).all() == []
        assert session.scalars(select(Tag)).all() == []
    items = client.get("/v1/feed").json()["items"]
    assert len(items) == 2
    assert all(item["tags"] == [] and item["categories"] == [] for item in items)
