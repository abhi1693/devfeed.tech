"""Source processing upgrades retain decisions, origin history and article indexes."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from devfeed_core.models import Article, ArticleOrigin, Source, SourceEnrichmentJob, SourceReview
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_source_processing_upgrade_retains_history_and_parent_indexes(database):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    source_id, article_id, job_id, review_id, origin_id = (uuid.uuid4() for _ in range(5))
    with database.begin() as session:
        # Core inserts retain ordinary historical rows without scheduling new work.
        session.execute(
            Source.__table__.insert().values(
                id=source_id,
                name="Existing publisher",
                feed_url="https://publisher.example/migration/feed",
                source_type="publisher",
                approval_status="approved",
                enabled=True,
            )
        )
        session.execute(
            SourceReview.__table__.insert().values(
                id=review_id,
                source_id=source_id,
                decision="approved",
                actor="Original editor",
                note="Retain this review",
            )
        )
        session.execute(
            SourceEnrichmentJob.__table__.insert().values(
                id=job_id, source_id=source_id, status="succeeded", operation="legacy"
            )
        )
        session.execute(
            Article.__table__.insert().values(
                id=article_id,
                canonical_url="https://publisher.example/migration/article",
                url_hash="source-lifecycle-migration",
                title="Existing published guide",
                publication_status="published",
                review_status="approved",
            )
        )
        session.execute(
            ArticleOrigin.__table__.insert().values(
                id=origin_id,
                article_id=article_id,
                source_id=source_id,
                entry_key="migration-guide",
                original_url="https://publisher.example/migration/article",
            )
        )
        image_index = session.scalar(text("SELECT 'ix_article_image_history'::regclass::oid"))
    try:
        command.downgrade(config, "0032")
        with database() as session:
            assert session.scalar(text("SELECT 'ix_article_image_history'::regclass::oid")) == (
                image_index
            )
        command.upgrade(config, "head")
        with database() as session:
            source = session.get(Source, source_id)
            assert source.approval_status == "approved" and source.enabled
            assert source.processing_request == {}
            job = session.get(SourceEnrichmentJob, job_id)
            assert job.status == "succeeded" and job.operation == "legacy" and job.result == {}
            review = session.get(SourceReview, review_id)
            assert review.source_id == source_id and review.note == "Retain this review"
            assert session.get(Article, article_id).publication_status == "published"
            assert session.get(ArticleOrigin, origin_id).entry_key == "migration-guide"
            assert session.scalar(text("SELECT 'ix_article_image_history'::regclass::oid")) == (
                image_index
            )
    finally:
        command.upgrade(config, "head")
