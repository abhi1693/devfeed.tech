import importlib.util
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core.db import get_engine
from devfeed_core.models import Article, ArticleOrigin, Source
from sqlalchemy import insert, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

pytestmark = pytest.mark.integration


def values(title="Optimize Docker Images: Faster Builds!"):
    return dict(
        title=title, canonical_url=f"https://example.test/{uuid.uuid4()}", url_hash=uuid.uuid4().hex
    )


def test_slugs_unique_for_concurrent_orm_and_bulk_inserts_and_stable_on_edit(database):
    def create(_):
        with database.begin() as session:
            article = Article(**values())
            session.add(article)
            session.flush()
            return article.id, article.slug

    with ThreadPoolExecutor(max_workers=8) as workers:
        rows = list(workers.map(create, range(24)))
    slugs = [slug for _, slug in rows]
    assert len(set(slugs)) == 24
    assert all(re.fullmatch(r"optimize-docker-images-faster-builds-\d+", s) for s in slugs)
    with database.begin() as session:
        article = session.get(Article, rows[0][0])
        article.title = "A corrected title"
        session.flush()
        session.refresh(article)
        assert article.slug == rows[0][1]
        inserted = (
            session.execute(
                insert(Article).returning(Article.slug),
                [
                    values("Café & Déjà vu"),
                    values("🚀 中文"),
                    values("x" * 500),
                ],
            )
            .scalars()
            .all()
        )
        assert inserted[0].startswith("cafe-deja-vu-")
        assert inserted[1].startswith("article-")
        assert len(inserted[2]) <= 200
        duplicate = values()
        first = session.scalar(pg_insert(Article).values(**duplicate).returning(Article.slug))
        session.execute(
            pg_insert(Article)
            .values(**duplicate)
            .on_conflict_do_nothing(index_elements=[Article.url_hash])
        )
        assert first not in slugs


def test_public_feed_and_detail_expose_same_slug_and_preserve_visibility(client, database):
    with database.begin() as session:
        source = Source(
            name="Publisher",
            source_type="publisher",
            approval_status="approved",
            feed_url="https://example.test/rss",
        )
        article = Article(**values(), publication_status="published", review_status="approved")
        hidden = Article(**values())
        session.add_all([source, article, hidden])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                original_url=article.canonical_url,
                entry_key="entry",
            )
        )
        identifier, slug, hidden_slug = str(article.id), article.slug, hidden.slug
    feed = client.get("/v1/feed").json()
    assert feed["items"][0]["slug"] == slug
    by_id = client.get(f"/v1/articles/{identifier}")
    by_slug = client.get(f"/v1/articles/{slug}")
    assert by_id.status_code == by_slug.status_code == 200
    assert by_id.json() == by_slug.json()
    assert client.get(f"/v1/articles/{hidden_slug}").status_code == 404
    assert client.get("/v1/articles/missing-123").status_code == 404


def test_migration_backfills_existing_titles_and_preserves_article_ids(database):
    path = Path(__file__).parents[1] / "migrations/versions/0024_article_slugs.py"
    spec = importlib.util.spec_from_file_location("article_slug_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Exercise the actual upgrade against a pre-slug table in a disposable schema.
    with get_engine().connect() as connection, connection.begin() as transaction:
        schema = "slug_test_" + uuid.uuid4().hex
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        connection.execute(
            text("CREATE TABLE articles (id integer PRIMARY KEY, title text NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO articles VALUES (1, 'Duplicate'), (2, 'Duplicate'), (3, '')")
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            rows = connection.execute(text("SELECT id, slug FROM articles ORDER BY id")).all()
            assert [row.id for row in rows] == [1, 2, 3]
            assert len({row.slug for row in rows}) == 3
            connection.execute(text("INSERT INTO articles (id, title) VALUES (4, 'Duplicate')"))
            assert connection.scalar(text("SELECT count(DISTINCT slug) FROM articles")) == 4
            module.downgrade()
            assert connection.scalar(text("SELECT count(*) FROM articles")) == 4
        transaction.rollback()
