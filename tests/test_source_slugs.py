import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_core.models import Source
from devfeed_core.search_records import documents, hit, public_records
from sqlalchemy import insert, text, update
from sqlalchemy.exc import IntegrityError
from test_public_search_integration import seed

pytestmark = pytest.mark.integration


def values(name):
    return dict(
        name=name, source_type="publisher", feed_url=f"https://example.test/{uuid.uuid4()}/rss"
    )


def test_slug_allocation_is_concurrent_unique_and_stable(database):
    def create(name):
        with database.begin() as session:
            source = Source(**values(name))
            session.add(source)
            session.flush()
            return source.id, source.slug

    with ThreadPoolExecutor(max_workers=8) as workers:
        rows = list(workers.map(create, ["Example", "Example-2"] * 12))
    slugs = [slug for _, slug in rows]
    assert len(set(slugs)) == 24
    assert all(re.fullmatch(r"example(-\d+)*", slug) for slug in slugs)
    with database.begin() as session:
        source = session.get(Source, rows[0][0])
        source.name = "Corrected publisher name"
        session.flush()
        session.refresh(source)
        assert source.slug == rows[0][1]
        inserted = session.scalars(
            insert(Source).returning(Source.slug),
            [
                values(name)
                for name in ["Café & Déjà vu", "🚀 中文", "x" * 200, "Suggest", str(uuid.uuid4())]
            ],
        ).all()
        assert inserted[0] == "cafe-deja-vu"
        assert inserted[1] == "source"
        assert len(inserted[2]) <= 200
        assert inserted[3] == "source-suggest"
        assert inserted[4].startswith("source-")
    with (
        pytest.raises(IntegrityError, match="Source slug is immutable"),
        database.begin() as session,
    ):
        session.execute(update(Source).where(Source.id == rows[0][0]).values(slug="changed"))


def test_source_slug_lookup_feed_search_and_sitemap_agree(client, database):
    identifier, *_ = seed(database)
    with database() as session:
        source = session.get(Source, identifier)
        slug = source.slug
        records = public_records(session, "sources", [identifier])
        assert hit("sources", records[identifier])["href"] == f"/sources/{slug}"
        docs, _ = documents(session, "sources", [identifier])
        assert slug in docs[0]["terms"]
    by_slug = client.get(f"/v1/sources/{slug}")
    by_id = client.get(f"/v1/sources/{identifier}")
    assert by_slug.status_code == by_id.status_code == 200
    assert by_slug.json() == by_id.json()
    assert by_slug.json()["slug"] == slug
    assert client.get("/v1/feed").json()["items"][0]["sources"][0]["slug"] == slug
    sitemap = client.get("/v1/sitemaps/sources/1").json()
    assert sitemap["paths"] == [f"/sources/{slug}"]
    with database.begin() as session:
        session.execute(
            update(Source).where(Source.id == identifier).values(approval_status="pending")
        )
    assert client.get(f"/v1/sources/{slug}").status_code == 404
    assert client.get(f"/v1/sources/{identifier}").status_code == 404
    assert client.get("/v1/sources/unknown-source").status_code == 404


def test_slug_lookup_uses_unique_index_at_catalog_scale(database):
    with database.begin() as session:
        session.execute(insert(Source), [values(f"Publisher {i}") for i in range(3000)])
        session.execute(text("ANALYZE sources"))
        plan = session.scalar(
            text(
                "EXPLAIN (ANALYZE, FORMAT JSON) SELECT * FROM sources WHERE slug = 'publisher-2345'"
            )
        )
        node = plan[0]["Plan"]
        assert node["Node Type"] == "Index Scan"
        assert node["Index Name"] == "uq_sources_slug"
        assert node["Actual Rows"] == 1


def test_hexadecimal_source_name_is_resolved_as_a_slug(client, database):
    with database.begin() as session:
        source = Source(**values("a" * 32), approval_status="approved")
        session.add(source)
        session.flush()
        identifier = str(source.id)
    assert client.get("/v1/sources/" + "a" * 32).json()["id"] == identifier


def test_existing_sources_backfill_preserves_article_links_and_reindexes(database):
    from alembic import command
    from alembic.config import Config
    from devfeed_core.db import get_engine
    from devfeed_core.models import ArticleOrigin, SearchEvent
    from sqlalchemy import func, select

    identifier, *_ = seed(database)
    with database.begin() as session:
        session.add(Source(**values("Kubernetes Journal")))
        before = session.execute(select(Source.id, Source.name)).all()
        origins = session.scalar(select(func.count()).select_from(ArticleOrigin))
        session.query(SearchEvent).delete()
    # Exercise the custom data backfill against populated, pre-slug tables.
    config = Config("alembic.ini")
    assert (get_engine().url.database or "").endswith("_test")
    command.downgrade(config, "0003")
    try:
        with get_engine().connect() as connection:
            assert connection.execute(
                text("SELECT id, name FROM sources ORDER BY id")
            ).all() == sorted(before)
        command.upgrade(config, "head")
        with database() as session:
            rows = session.scalars(select(Source)).all()
            assert {(row.id, row.name) for row in rows} == set(before)
            assert {row.slug for row in rows} == {"kubernetes-journal", "kubernetes-journal-2"}
            assert session.get(Source, identifier).slug == "kubernetes-journal"
            assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == origins
            assert set(
                session.scalars(select(SearchEvent.entity_id).where(SearchEvent.kind == "sources"))
            ) == {row.id for row in rows}
    finally:
        command.upgrade(config, "head")
