"""Saved reader selections resolve stable identities without changing public slug routes."""

import uuid

import pytest
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, Source, Tag, Topic

pytestmark = pytest.mark.integration


def seed_catalog(database, slug="catalog-item", *, status="active", visible=True):
    with database.begin() as session:
        topic = Topic(name=slug, slug=slug, kind="technology", status=status)
        tag = Tag(name=slug, slug=slug)
        source = Source(
            name=slug,
            feed_url=f"https://example.test/{slug}/rss",
            source_type="publisher",
            approval_status="approved",
        )
        article = Article(
            title=slug,
            canonical_url=f"https://example.test/{slug}/article",
            url_hash=uuid.uuid4().hex,
            publication_status="published" if visible else "unpublished",
            review_status="approved",
        )
        session.add_all([topic, tag, source, article])
        session.flush()
        session.add_all(
            [
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    entry_key=str(article.id),
                    original_url=article.canonical_url,
                ),
                ArticleTag(article_id=article.id, tag_id=tag.id, origin="source"),
            ]
        )
        return {
            "topics": topic.id,
            "tags": tag.id,
            "article": article.id,
            "source": source.id,
        }


@pytest.mark.parametrize("kind, model", [("topics", Topic), ("tags", Tag)])
def test_saved_identity_returns_current_name_and_slug_after_rename(client, database, kind, model):
    identity = seed_catalog(database)[kind]
    by_slug = client.get(f"/v1/{kind}/catalog-item")
    by_id = client.get(f"/v1/{kind}/{identity}", params={"by_id": True})
    assert by_slug.status_code == by_id.status_code == 200
    assert by_slug.json() == by_id.json()
    # UUID lookup is explicit: introducing it cannot repurpose an existing slug route.
    assert client.get(f"/v1/{kind}/{identity}").status_code == 404
    with database.begin() as session:
        row = session.get(model, identity)
        row.name = "Renamed selection"
        row.slug = "renamed-selection"
    renamed = client.get(f"/v1/{kind}/{identity}", params={"by_id": True})
    assert renamed.status_code == 200
    assert renamed.json()["id"] == str(identity)
    assert renamed.json()["name"] == "Renamed selection"
    assert renamed.json()["slug"] == "renamed-selection"
    assert client.get(f"/v1/{kind}/renamed-selection").json() == renamed.json()
    assert client.get(f"/v1/{kind}/catalog-item").status_code == 404


@pytest.mark.parametrize("kind", ["topics", "tags"])
def test_explicit_identity_does_not_collide_with_uuid_shaped_canonical_slug(client, database, kind):
    identity = seed_catalog(database)[kind]
    other = seed_catalog(database, str(identity))[kind]
    assert client.get(f"/v1/{kind}/{identity}").json()["id"] == str(other)
    assert client.get(f"/v1/{kind}/{identity}?by_id=false").json()["id"] == str(other)
    assert client.get(f"/v1/{kind}/{identity}?by_id=true").json()["id"] == str(identity)


@pytest.mark.parametrize("status", ["proposed", "rejected"])
def test_topic_identity_does_not_disclose_nonpublic_topics(client, database, status):
    identity = seed_catalog(database, status=status)["topics"]
    for path in ("catalog-item", f"{identity}?by_id=true"):
        response = client.get(f"/v1/topics/{path}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Topic not found"}


@pytest.mark.parametrize("scenario", ["empty", "unpublished", "unapproved-source", "orphan"])
def test_tag_identity_requires_publicly_visible_articles(client, database, scenario):
    identities = seed_catalog(database, visible=scenario != "unpublished")
    with database.begin() as session:
        if scenario == "empty":
            session.query(ArticleTag).filter(ArticleTag.tag_id == identities["tags"]).delete()
        elif scenario == "unapproved-source":
            session.get(Source, identities["source"]).approval_status = "pending"
        elif scenario == "orphan":
            session.query(ArticleOrigin).filter(
                ArticleOrigin.article_id == identities["article"]
            ).delete()
    for path in ("catalog-item", f"{identities['tags']}?by_id=true"):
        response = client.get(f"/v1/tags/{path}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Tag not found"}


@pytest.mark.parametrize("kind", ["topics", "tags"])
def test_unknown_and_invalid_identity_requests_do_not_fall_back_to_slug(client, database, kind):
    seed_catalog(database)
    for identity in (str(uuid.uuid4()), "catalog-item", "not-a-uuid"):
        assert client.get(f"/v1/{kind}/{identity}?by_id=true").status_code == 404
    assert client.get(f"/v1/{kind}/not-a-uuid").status_code == 404
