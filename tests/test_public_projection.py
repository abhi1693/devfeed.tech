"""Public discovery excludes unpublished taxonomy and unapproved source evidence."""

import pytest
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, Source, Tag
from sqlalchemy import select
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_public_articles_omit_unapproved_sources_and_origins(database, client, user_data, status):
    user_client, _, _, _, _ = user_data
    with database.begin() as session:
        article = session.scalar(select(Article).where(Article.title == "Article 109"))
        hidden = session.scalar(select(Source).where(Source.approval_status == "rejected"))
        hidden.approval_status = status
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=hidden.id,
                entry_key="private-origin",
                original_url="https://private.example/original",
                source_metadata={"title": "Unapproved evidence"},
            )
        )
        article_id, hidden_id = str(article.id), str(hidden.id)
    # Seed genuine activity so the same shared projection is exercised in trending.
    user_client.put(f"/v1/user/articles/{article_id}/like", json={"liked": True})
    assert client.get(f"/v1/sources/{hidden_id}").status_code == 404
    for http, path in [
        (client, f"/v1/articles/{article_id}"),
        (client, "/v1/feed?limit=100"),
        (user_client, "/v1/user/trending"),
    ]:
        response = http.get(path)
        assert response.status_code == 200, response.text
        body = response.json()
        row = (
            next(item for item in body["items"] if item["id"] == article_id)
            if "items" in body
            else body
        )
        assert len(row["sources"]) == len(row["origins"]) == 1
        assert hidden_id not in {source["id"] for source in row["sources"]}
        assert hidden_id not in {origin["source_id"] for origin in row["origins"]}
        assert "Unapproved evidence" not in response.text
    # Operator inspection retains all evidence; only reader projections are restricted.
    from devfeed_cli.editorial import article_view

    with database() as session:
        article = session.scalar(select(Article).where(Article.title == "Article 109"))
        assert len(article_view(article)["origins"]) == 2


def test_public_tags_only_include_visible_article_tags_and_public_fields(
    database, client, user_data
):
    with database.begin() as session:
        visible = session.scalar(select(Article).where(Article.title == "Article 109"))
        private = session.scalar(select(Article).where(Article.title == "Article 110"))
        tags = [
            Tag(name=name, slug=name, aliases=[name + "-alias"])
            for name in ["visible", "private", "unused"]
        ]
        session.add_all(tags)
        session.flush()
        session.add_all(
            [
                ArticleTag(article_id=visible.id, tag_id=tags[0].id),
                ArticleTag(article_id=private.id, tag_id=tags[1].id),
            ]
        )
    response = client.get("/v1/tags")
    assert response.status_code == 200
    assert len(response.json()) == 1
    row = response.json()[0]
    assert row["slug"] == "visible"
    assert set(row) == {"id", "name", "slug", "aliases"}
