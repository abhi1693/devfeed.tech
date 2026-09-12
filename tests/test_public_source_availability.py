"""Source discovery advertises only feeds with publicly visible articles."""

import uuid

import pytest
from devfeed_core.db import get_engine
from devfeed_core.models import Article, ArticleOrigin, Source
from sqlalchemy import event, insert, update

pytestmark = pytest.mark.integration


def test_available_sources_match_feeds_and_filter_before_pagination(client, database):
    scenarios = [
        ("a-empty", "approved", True, None, None),
        ("b-pending-article", "approved", True, "unpublished", "pending"),
        ("c-rejected-article", "approved", True, "unpublished", "rejected"),
        ("d-unpublished", "approved", True, "unpublished", "approved"),
        ("e-pending-source", "pending", True, "published", "approved"),
        ("f-rejected-source", "rejected", True, "published", "approved"),
        ("g-disabled", "approved", False, "published", "approved"),
        ("p-published", "approved", True, "published", "approved"),
        ("s-published", "approved", True, "published", "approved"),
    ]
    source_ids, article_ids = {}, {}
    with database.begin() as session:
        for name, approval, enabled, publication, review in scenarios:
            source_id = source_ids[name] = uuid.uuid4()
            session.execute(
                insert(Source.__table__).values(
                    id=source_id,
                    name=name,
                    feed_url=f"https://example.test/{name}/feed",
                    source_type="publisher",
                    approval_status=approval,
                    enabled=enabled,
                )
            )
            if publication is None:
                continue
            # Multiple articles must neither duplicate sources nor consume page slots.
            for index in range(2 if name == "p-published" else 1):
                article_id = uuid.uuid4()
                article_ids.setdefault(name, []).append(article_id)
                url = f"https://example.test/{name}/{index}"
                session.execute(
                    insert(Article.__table__).values(
                        id=article_id,
                        title=name,
                        canonical_url=url,
                        url_hash=article_id.hex,
                        publication_status=publication,
                        review_status=review,
                    )
                )
                session.execute(
                    insert(ArticleOrigin.__table__).values(
                        article_id=article_id,
                        source_id=source_id,
                        entry_key=str(index),
                        original_url=url,
                    )
                )

    assert "a-empty" in {row["name"] for row in client.get("/v1/sources").json()}
    query = "/v1/sources?has_articles=true&enabled=true"
    statements = []

    def count(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(get_engine(), "before_cursor_execute", count)
    try:
        response = client.get(query)
    finally:
        event.remove(get_engine(), "before_cursor_execute", count)
    assert response.status_code == 200
    assert len(statements) == 1
    assert [row["name"] for row in response.json()] == ["p-published", "s-published"]
    for offset, expected in [(0, ["p-published"]), (1, ["s-published"]), (2, [])]:
        page = client.get(f"{query}&limit=1&offset={offset}")
        assert [row["name"] for row in page.json()] == expected
    for name, approval, enabled, *_ in scenarios:
        if approval == "approved" and enabled:
            page = client.get(f"/v1/feed?source_id={source_ids[name]}")
            assert bool(page.json()["items"]) == (name in {"p-published", "s-published"})
    assert [row["name"] for row in client.get("/v1/sources?has_articles=true").json()] == [
        "g-disabled",
        "p-published",
        "s-published",
    ]
    # A source disappears from discovery when its final article is unpublished.
    with database.begin() as session:
        session.execute(
            update(Article)
            .where(Article.id.in_(article_ids["p-published"]))
            .values(publication_status="unpublished")
        )
    assert [row["name"] for row in client.get(query).json()] == ["s-published"]
