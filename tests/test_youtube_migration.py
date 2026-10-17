"""Upgrade populated pre-video schemas and safely remove provider-only state."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from devfeed_core.models import Article, Source, YouTubeMembership
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from test_youtube import PLAYLIST, VIDEO
from test_youtube_ingestion import import_video, source

pytestmark = pytest.mark.integration


def test_video_migration_preserves_rss_and_polling_and_downgrades_provider_content(database):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    rss_id = uuid.uuid4()
    origin_id = source(database)
    _, video_id = import_video(database, origin_id)
    with database.begin() as session:
        session.add(
            Source(
                id=rss_id,
                name="Existing RSS",
                feed_url="https://example.com/feed",
                source_type="publisher",
                approval_status="approved",
                polling_mode="adaptive",
                poll_interval_seconds=43200,
                polling_state={"observed_at": "2026-10-17T00:00:00+00:00"},
            )
        )
        record = session.get(Article, video_id)
        record.review_status, record.publication_status = "approved", "published"
    try:
        command.downgrade(config, "0025")
        with database() as session:
            assert "provider" not in {
                c["name"] for c in inspect(session.get_bind()).get_columns("sources")
            }
            assert "youtube_memberships" not in inspect(session.get_bind()).get_table_names()
            assert session.execute(
                text("SELECT approval_status,enabled FROM sources WHERE id=:id"), {"id": origin_id}
            ).one() == ("pending", False)
            assert (
                session.scalar(
                    text("SELECT publication_status FROM articles WHERE id=:id"), {"id": video_id}
                )
                == "unpublished"
            )
            assert session.execute(
                text("SELECT polling_mode,poll_interval_seconds FROM sources WHERE id=:id"),
                {"id": rss_id},
            ).one() == ("adaptive", 43200)
    finally:
        command.upgrade(config, "head")
    with database() as session:
        rss = session.get(Source, rss_id)
        assert (rss.provider, rss.resource_kind, rss.recurring) == ("rss", "feed", True)
        assert rss.polling_state == {"observed_at": "2026-10-17T00:00:00+00:00"}
        assert session.get(Article, video_id).publication_status == "unpublished"
        assert session.scalars(select(YouTubeMembership)).all() == []


@pytest.mark.parametrize("bad", ["missing-id", "rss-identity", "video-recurring"])
def test_provider_database_constraints_reject_ambiguous_resource_identity(database, bad):
    values = dict(
        name="Invalid provider",
        provider="youtube",
        resource_kind="playlist",
        provider_resource_id=PLAYLIST,
        provider_collection_id=PLAYLIST,
        feed_url=None,
        recurring=True,
        source_type="publisher",
    )
    if bad == "missing-id":
        values["provider_resource_id"] = None
    elif bad == "rss-identity":
        values.update(provider="rss", resource_kind="feed", feed_url="https://example.com/rss")
    else:
        values.update(
            resource_kind="video", provider_resource_id=VIDEO, provider_collection_id=None
        )
    with pytest.raises(IntegrityError), database.begin() as session:
        session.add(Source(**values))
        session.flush()
