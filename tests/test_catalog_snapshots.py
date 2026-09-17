"""Catalog cache consistency under commits, rollback, and concurrent writers."""

import pytest
from devfeed_core import analysis, article_automation
from devfeed_core.catalog_cache import snapshot
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.models import Tag
from sqlalchemy import event, text
from test_automation_integration import seed

pytestmark = pytest.mark.integration


@pytest.fixture
def cached(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    return database


def test_catalog_reuse_raw_sql_edits_and_rollback(cached, monkeypatch):
    with cached.begin() as session:
        tag = Tag(name="Original", slug="original")
        session.add(tag)
        session.flush()
        identifier = tag.id
    with cached() as session:
        original = analysis.catalog(session)
    loader = analysis._read_catalog
    monkeypatch.setattr(analysis, "_read_catalog", lambda _: pytest.fail("cache miss"))
    with cached() as session:
        assert analysis.catalog(session) == original
    monkeypatch.setattr(analysis, "_read_catalog", loader)
    with cached() as editor:
        editor.execute(text("UPDATE tags SET name='Uncommitted' WHERE id=:id"), {"id": identifier})
        assert analysis.catalog(editor)["tags"][0]["name"] == "Uncommitted"
        # Uncommitted snapshots cannot leak to another transaction.
        with cached() as reader:
            assert analysis.catalog(reader) == original
        editor.rollback()
    with cached() as session:
        assert analysis.catalog(session) == original
    with cached.begin() as editor:
        editor.execute(text("UPDATE tags SET name='Committed' WHERE id=:id"), {"id": identifier})
    with cached() as session:
        assert analysis.catalog(session)["tags"][0]["name"] == "Committed"


def test_concurrent_change_during_load_does_not_poison_snapshot(cached):
    with cached.begin() as session:
        session.add(Tag(name="Old", slug="old"))

    def racing_load():
        with cached.begin() as writer:
            writer.execute(text("UPDATE tags SET name='New'"))
        return {"stale": True}

    with cached() as session:
        assert snapshot(session, ("tags",), racing_load) == {"stale": True}
    with cached() as session:
        assert snapshot(session, ("tags",), lambda: {"fresh": True}) == {"fresh": True}


def test_cache_failure_falls_back_to_current_database(cached, monkeypatch):
    from devfeed_core.cache import CacheUnavailable, get_cache

    monkeypatch.setattr(get_cache(), "_run", lambda *_: (_ for _ in ()).throw(CacheUnavailable()))
    with cached.begin() as session:
        session.add(Tag(name="Current", slug="current"))
        assert analysis.catalog(session)["tags"][0]["name"] == "Current"


def test_no_source_candidates_does_not_acquire_proposal_lock(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    get_settings.cache_clear()
    with database.begin() as session:
        _, article, _ = seed(session)
        monkeypatch.setattr(
            article_automation, "lock_article_catalog", lambda *_: pytest.fail("unneeded lock")
        )
        assert article_automation.propose_source_topics(session, article) == 0


def test_catalog_hit_transfers_no_taxonomy_rows(cached):
    with cached.begin() as session:
        session.add_all(Tag(name=f"Tag {i}", slug=f"tag-{i}") for i in range(1000))
    with cached() as session:
        assert len(analysis.catalog(session)["tags"]) == 1000
    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        with cached() as session:
            assert len(analysis.catalog(session)["tags"]) == 1000
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
    assert len(queries) == 1
    assert "catalog_revisions" in queries[0]


def test_noop_writes_and_unrelated_metadata_preserve_revision(cached):
    with cached.begin() as session:
        session.add(Tag(name="Original", slug="original"))
    with cached() as session:
        original = session.scalar(text("SELECT revision FROM catalog_revisions WHERE name='tags'"))
    with cached.begin() as session:
        session.execute(text("UPDATE tags SET name=name"))
        session.execute(text("UPDATE tags SET topic_match_status='unmatched'"))
        session.execute(text("DELETE FROM tags WHERE false"))
        session.execute(
            text(
                """INSERT INTO tags(id,name,slug,aliases,auto_link_topic,
                   topic_match_revision,topic_match_status)
                   SELECT id,name,slug,aliases,auto_link_topic,topic_match_revision,
                   topic_match_status FROM tags ON CONFLICT DO NOTHING"""
            )
        )
        assert (
            session.scalar(text("SELECT revision FROM catalog_revisions WHERE name='tags'"))
            == original
        )


def test_topic_status_keywords_and_pending_proposal_changes_invalidate(cached):
    import uuid

    from devfeed_core.models import Topic, TopicProposal

    with cached.begin() as session:
        session.add(Topic(name="Python", slug="python", kind="language", status="active"))
        session.add(
            TopicProposal(
                batch_id=uuid.uuid4(),
                slug="pending",
                action="create",
                origin="import",
                source_name="Test",
                proposed={"name": "Pending", "slug": "pending"},
                created_by={},
                status="pending",
            )
        )
    with cached() as session:
        assert analysis.catalog(session)["topics"][0]["keywords"] == []
        assert article_automation.pending_topic_matches(session, {"title": "Pending"})
    with cached.begin() as session:
        session.execute(text("UPDATE topics SET keywords=ARRAY['code']"))
        session.execute(
            text(
                "UPDATE topic_proposals SET status='rejected', reviewed_at=now(), "
                "reviewed_by='{}'::jsonb"
            )
        )
    with cached() as session:
        assert analysis.catalog(session)["topics"][0]["keywords"] == ["code"]
        assert not article_automation.pending_topic_matches(session, {"title": "Pending"})
    with cached.begin() as session:
        session.execute(text("UPDATE topics SET status='rejected'"))
    with cached() as session:
        assert analysis.catalog(session)["topics"] == []


def test_concurrent_writers_commit_in_order_and_cache_stays_current(cached):
    # Separate table revisions must both participate in the combined key.
    from devfeed_core.models import Topic

    with cached.begin() as session:
        session.add(Tag(name="Tag", slug="tag"))
        session.add(Topic(name="Topic", slug="topic", kind="technology", status="active"))
    with cached() as first, cached() as second:
        first.execute(text("UPDATE tags SET name='New tag'"))
        second.execute(text("UPDATE topics SET name='New topic'"))
        second.commit()
        with cached() as reader:
            intermediate = analysis.catalog(reader)
            assert intermediate["tags"][0]["name"] == "Tag"
            assert intermediate["topics"][0]["name"] == "New topic"
        first.commit()
    with cached() as reader:
        assert analysis.catalog(reader)["tags"][0]["name"] == "New tag"


def test_rapid_edits_keep_one_bounded_cache_slot(cached):
    from devfeed_core.cache import get_cache

    with cached.begin() as session:
        session.add(Tag(name="Original", slug="original"))
    for i in range(12):
        with cached.begin() as session:
            session.execute(text("UPDATE tags SET name=:name"), {"name": f"Edit {i}"})
        with cached() as session:
            assert analysis.catalog(session)["tags"][0]["name"] == f"Edit {i}"
    cache = get_cache()
    assert len(list(cache.redis.scan_iter(f"{cache.namespace}:catalog:*"))) == 1
