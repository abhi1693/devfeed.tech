"""Latest result lookup must stop at one index entry, regardless of history size."""

import json
import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleEnrichmentJob, utcnow
from sqlalchemy import insert, select, text
from sqlalchemy.dialects import postgresql


@pytest.mark.integration
@pytest.mark.parametrize("model", [ArticleAnalysisJob, ArticleEnrichmentJob])
def test_latest_result_is_index_ordered_and_preserves_tie_break(database, model):
    now = utcnow()
    article_id = uuid.uuid4()
    identifiers = [uuid.uuid4() for _ in range(1000)]
    with database.begin() as session:
        session.add(
            Article(
                id=article_id,
                title="History",
                canonical_url="https://example.test/history",
                url_hash="a" * 64,
            )
        )
        session.flush()
        session.execute(
            insert(model),
            [
                dict(
                    id=identifier,
                    article_id=article_id,
                    status="succeeded",
                    created_at=now - timedelta(days=index // 2),
                    result={"position": index},
                )
                for index, identifier in enumerate(identifiers)
            ],
        )
        session.execute(text(f"ANALYZE {model.__tablename__}"))
        query = (
            select(model.id)
            .where(model.article_id == article_id)
            .order_by(model.created_at.desc(), model.id.desc())
            .limit(1)
        )
        assert session.scalar(query) == max(identifiers[:2])
        sql = str(
            query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        plan = session.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)).scalar()
        encoded = json.dumps(plan)
        assert "_latest" in encoded and '"Node Type": "Sort"' not in encoded


@pytest.mark.integration
def test_latest_indexes_round_trip_preserves_history(database):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    migration = importlib.import_module("migrations.versions.0027_article_job_latest_indexes")
    with database() as session, session.get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                for table in ("article_analysis_jobs", "article_enrichment_jobs"):
                    assert not any(
                        index["name"].endswith("_latest")
                        for index in inspect(connection).get_indexes(table)
                    )
                migration.upgrade()
                for table in ("article_analysis_jobs", "article_enrichment_jobs"):
                    assert any(
                        index["name"].endswith("_latest")
                        for index in inspect(connection).get_indexes(table)
                    )
        finally:
            transaction.rollback()
