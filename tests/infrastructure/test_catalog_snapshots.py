"""Catalog cache consistency under commits, rollback, and concurrent writers."""

import pytest
from devfeed_core import analysis, article_automation
from devfeed_core.catalog_cache import snapshot
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.models import Tag
from sqlalchemy import event, text

from tests.workers.test_automation_integration import seed

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


@pytest.mark.parametrize("redis_enabled", [True, False])
def test_tick_snapshot_reuse_rechecks_revisions_and_handles_rollback(
    database, monkeypatch, redis_enabled
):
    from devfeed_core.catalog_cache import reuse_snapshots, snapshot_current

    monkeypatch.setattr(get_settings(), "cache_enabled", redis_enabled)
    with database.begin() as session:
        session.add(Tag(name="Original", slug="original"))
    with reuse_snapshots():
        with database() as session:
            original = analysis.catalog(session)
        with database() as session:
            assert analysis.catalog(session) is original
            assert snapshot_current(session, ("topics", "tags"), original)
        with database() as editor:
            editor.execute(text("UPDATE tags SET name='Uncommitted'"))
            assert analysis.catalog(editor)["tags"][0]["name"] == "Uncommitted"
            with database() as reader:
                assert analysis.catalog(reader)["tags"][0]["name"] == "Original"
            editor.rollback()
        with database.begin() as editor:
            editor.execute(text("UPDATE tags SET name='Committed'"))
        with database() as session:
            assert not snapshot_current(session, ("topics", "tags"), original)
            current = analysis.catalog(session)
            assert current["tags"][0]["name"] == "Committed"
            assert analysis.catalog(session) is current


@pytest.mark.parametrize("column", ["description", "ai_description"])
def test_description_vocabulary_edits_invalidate_cached_catalog(cached, monkeypatch, column):
    from devfeed_core.catalog_cache import reuse_snapshots, snapshot_current
    from devfeed_core.models import Topic

    monkeypatch.setattr(get_settings(), "analysis_fallback_candidates", 0)
    with cached.begin() as session:
        session.add(Topic(name="Career", slug="career", kind="technology", status="active"))
    with reuse_snapshots(), analysis.reuse_candidates():
        with cached() as session:
            before = analysis.catalog(session)
        evidence = {"title": "Interviewing developers"}
        assert analysis.analysis_candidates(before, evidence)["topics"] == []
        with cached.begin() as editor:
            editor.execute(text(f"UPDATE topics SET {column}='Interviewing software developers'"))
        with cached() as session:
            assert not snapshot_current(session, ("topics", "tags"), before)
            after = analysis.catalog(session)
            assert after["topics"][0][column] == "Interviewing software developers"
        assert analysis.analysis_candidates(after, evidence)["topics"][0]["name"] == "Career"


@pytest.mark.parametrize("enabled", [False, True])
def test_analysis_job_reuses_catalog_without_holding_connections_over_inference(
    database, monkeypatch, enabled
):
    import weakref

    from devfeed_aggregator import analysis_tasks
    from devfeed_core import catalog_cache
    from devfeed_core.models import Article, ArticleAnalysisJob
    from sqlalchemy import insert

    from tests.support.article_step_helpers import article_step_client
    from tests.workers.test_automation_integration import enable

    enable(monkeypatch, ANALYSIS_CATALOG_REUSE_ENABLED=enabled)
    with database.begin() as session:
        _, article, topic = seed(session)
        session.execute(
            insert(Tag),
            [{"name": f"Tag {index}", "slug": f"tag-{index}"} for index in range(60000)],
        )
        identifier, topic_id = article.id, topic.id
        job_id = analysis.request_analysis(session, identifier).id
    monkeypatch.setattr(analysis_tasks, "session_factory", lambda: database)
    original = analysis._PreparedCatalog
    references, calls, connections = [], [], set()

    class TrackedCatalog(original):
        def __init__(self, taxonomy):
            super().__init__(taxonomy)
            references.append(weakref.ref(self))

    def checkout(connection, *_):
        connections.add(id(connection))

    def checkin(connection, *_):
        connections.discard(id(connection))

    def complete(*_):
        assert not connections
        calls.append(True)
        return _catalog_job_output(topic_id)

    monkeypatch.setattr(analysis, "_PreparedCatalog", TrackedCatalog)
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    engine = get_engine()
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    try:
        analysis_tasks.analyze_article(str(job_id))
    finally:
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)
    with database() as session:
        assert session.get(ArticleAnalysisJob, job_id).outcome == "applied"
        assert session.get(Article, identifier).ai_summary == "Angular routing for developers."
    assert len(calls) >= 8
    assert len(references) == (1 if enabled else 2)
    assert all(reference() is None for reference in references)
    assert analysis._ranking.get() is None and catalog_cache._snapshots.get() is None


def _catalog_job_output(topic_id):
    return {
        "outcome": "ready",
        "developer_relevance": "relevant",
        "language": "en",
        "content_type": "tutorial",
        "content_format": "article",
        "ai_summary": "Angular routing for developers.",
        "ai_description": "A guide to Angular routing for application developers.",
        "ai_title": None,
        "title_evidence": None,
        "page_kind": "article",
        "topics": [
            {"topic_id": str(topic_id), "role": "primary", "relevance": 0.9, "evidence": "Angular"}
        ],
        "tags": [],
        "reasons": [],
    }


@pytest.mark.parametrize("when", ["inference", "before_locked_apply"])
def test_job_retained_catalog_rebuilds_after_raw_sql_and_refuses_stale_selection(
    database, monkeypatch, when
):
    import weakref

    from devfeed_aggregator import analysis_tasks
    from devfeed_core import catalog_cache
    from devfeed_core.models import Article, ArticleAnalysisJob

    from tests.support.article_step_helpers import article_step_client
    from tests.workers.test_automation_integration import enable

    enable(monkeypatch, ANALYSIS_CATALOG_REUSE_ENABLED=True)
    with database.begin() as session:
        _, article, topic = seed(session)
        identifier, topic_id = article.id, topic.id
        job_id = analysis.request_analysis(session, identifier).id
    monkeypatch.setattr(analysis_tasks, "session_factory", lambda: database)
    original = analysis._PreparedCatalog
    prepare = analysis_tasks._prepare_application
    references, inference_calls, apply_preparations = [], [], []

    class TrackedCatalog(original):
        def __init__(self, taxonomy):
            assert not references or references[-1]() is None
            super().__init__(taxonomy)
            references.append(weakref.ref(self))

    def edit():
        # SQL triggers, rather than ORM hooks, must retire the retained generation.
        with get_engine().begin() as connection:
            connection.execute(
                text("UPDATE topics SET name='Changed identity' WHERE id=:id"), {"id": topic_id}
            )

    def complete(*_):
        inference_calls.append(True)
        if when == "inference" and len(inference_calls) == 1:
            edit()
        return _catalog_job_output(topic_id)

    def prepare_application(*args):
        result = prepare(*args)
        apply_preparations.append(True)
        if when == "before_locked_apply" and len(apply_preparations) == 1:
            edit()
        return result

    monkeypatch.setattr(analysis, "_PreparedCatalog", TrackedCatalog)
    monkeypatch.setattr(analysis_tasks, "_prepare_application", prepare_application)
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    analysis_tasks.analyze_article(str(job_id))
    with database() as session:
        assert session.get(ArticleAnalysisJob, job_id).outcome == "superseded"
        assert session.get(Article, identifier).ai_summary is None
    assert len(references) == 2
    assert len(apply_preparations) == (2 if when == "before_locked_apply" else 1)
    assert all(reference() is None for reference in references)
    assert analysis._ranking.get() is None and catalog_cache._snapshots.get() is None
