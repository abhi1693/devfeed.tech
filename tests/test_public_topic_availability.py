"""Topic discovery must not advertise feeds containing only nonpublic articles."""

import uuid

import pytest
from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Source, Topic, TopicRelation
from sqlalchemy import insert

pytestmark = pytest.mark.integration


def test_available_topics_match_public_feeds_and_filter_before_pagination(client, database):
    scenarios = [
        ("a-empty", "active", None, None, None, None),
        ("b-pending", "active", "unpublished", "pending", "approved", "primary"),
        ("c-rejected", "active", "unpublished", "rejected", "approved", "primary"),
        ("d-unpublished", "active", "unpublished", "approved", "approved", "primary"),
        ("e-rejected-source", "active", "published", "approved", "rejected", "primary"),
        ("f-pending-source", "active", "published", "approved", "pending", "primary"),
        ("g-orphan", "active", "published", "approved", None, "primary"),
        ("h-incidental", "active", "published", "approved", "approved", "incidental"),
        ("i-comparison", "active", "published", "approved", "approved", "comparison"),
        ("j-proposed", "proposed", "published", "approved", "approved", "primary"),
        ("p-primary", "active", "published", "approved", "approved", "primary"),
        ("s-supporting", "active", "published", "approved", "approved", "supporting"),
    ]
    topic_ids = {}
    with database.begin() as session:
        for slug, status, publication, review, approval, role in scenarios:
            topic_id = topic_ids[slug] = uuid.uuid4()
            session.execute(
                insert(Topic.__table__).values(
                    id=topic_id,
                    slug=slug,
                    name=slug,
                    kind="technology",
                    status=status,
                )
            )
            if publication is None:
                continue
            source_id = uuid.uuid4()
            if approval:
                session.execute(
                    insert(Source.__table__).values(
                        id=source_id,
                        name=slug,
                        feed_url=f"https://example.test/{slug}/rss",
                        source_type="publisher",
                        approval_status=approval,
                    )
                )
            # Multiple matching articles must not duplicate a topic or consume page slots.
            for index in range(2 if slug == "p-primary" else 1):
                article_id = uuid.uuid4()
                url = f"https://example.test/{slug}/{index}"
                session.execute(
                    insert(Article.__table__).values(
                        id=article_id,
                        canonical_url=url,
                        url_hash=article_id.hex,
                        title=slug,
                        publication_status=publication,
                        review_status=review,
                    )
                )
                if approval:
                    session.execute(
                        insert(ArticleOrigin.__table__).values(
                            article_id=article_id,
                            source_id=source_id,
                            entry_key=str(index),
                            original_url=url,
                        )
                    )
                session.execute(
                    insert(ArticleTopic.__table__).values(
                        article_id=article_id,
                        topic_id=topic_id,
                        role=role,
                        relevance=1,
                        evidence="Test topic assignment",
                    )
                )
        session.execute(
            insert(TopicRelation.__table__).values(
                topic_id=topic_ids["a-empty"],
                related_topic_id=topic_ids["p-primary"],
                relation="related_to",
            )
        )

    ordinary = client.get("/v1/topics").json()
    assert "a-empty" in {item["slug"] for item in ordinary}
    response = client.get("/v1/topics?has_articles=true")
    assert response.status_code == 200
    assert [item["slug"] for item in response.json()] == ["p-primary", "s-supporting"]
    for offset, expected in [(0, ["p-primary"]), (1, ["s-supporting"]), (2, [])]:
        page = client.get(f"/v1/topics?has_articles=true&limit=1&offset={offset}")
        assert [item["slug"] for item in page.json()] == expected
    for slug, status, *_ in scenarios:
        if status == "active":
            visible = client.get(f"/v1/feed?topic={slug}").json()["items"]
            assert bool(visible) == (slug in {"p-primary", "s-supporting"})
