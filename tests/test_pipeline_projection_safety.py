"""Publisher retention and lease failures preserve complete serving generations."""

import uuid
from datetime import timedelta

import pytest
from devfeed_admin_api import article_pipeline, pipeline_projection, pipeline_reads
from devfeed_admin_api.pipeline_projection import drain_pipeline, project_batch
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    PipelineProjection,
    ReadModelEvent,
    ReadModelState,
    Source,
    utcnow,
)
from devfeed_core.read_models import pipeline_policy_version
from sqlalchemy import event, func, select, update

pytestmark = pytest.mark.integration


def add_article(session, *, published_at=None, published=False, source=None):
    identity = uuid.uuid4()
    article = Article(
        id=identity,
        canonical_url=f"https://publisher.example/{identity}",
        url_hash=identity.hex,
        title=f"Article {identity}",
        summary="Evidence about Python coroutine execution.",
        review_status="approved" if published else "pending",
        publication_status="published" if published else "unpublished",
        published_to_feed_at=published_at,
    )
    session.add(article)
    if source is not None:
        article.origins = [
            ArticleOrigin(source=source, entry_key=identity.hex, original_url=article.canonical_url)
        ]
    session.flush()
    return article


def test_terminal_publications_without_a_timestamp_preserve_legacy_omission(database, admin_client):
    with database.begin() as session:
        missing = add_article(session, published=True)
        current = add_article(session, published=True, published_at=utcnow())
        identifiers = missing.id, current.id
    drain_pipeline(database)
    with database() as session:
        expected = article_pipeline._board(
            session, q=None, source_id=None, needs_attention=False, limit=20
        )
        policy = pipeline_policy_version()
        assert session.get(PipelineProjection, (policy, "article", identifiers[0])) is None
    response = admin_client.get("/v1/admin/article-pipeline")
    assert response.status_code == 200
    assert response.json()["counts"] == expected.counts
    published = next(
        column for column in response.json()["columns"] if column["stage"] == "published"
    )
    assert [item["id"] for item in published["items"]] == [str(identifiers[1])]


def test_rebuild_retains_serving_timestamp_and_prunes_previous_policy_in_batches(
    database, monkeypatch
):
    now = utcnow()
    monkeypatch.setattr(pipeline_projection, "utcnow", lambda: now)
    with database.begin() as session:
        for _ in range(6):
            add_article(session)
    drain_pipeline(database, batch=2)
    old_policy = pipeline_policy_version()
    with database() as session:
        previous = session.get(ReadModelState, "pipeline").payload["serving_generated_at"]
    now += timedelta(minutes=2)
    monkeypatch.setattr(
        pipeline_projection, "pipeline_policy_version", lambda: "replacement-policy"
    )
    monkeypatch.setattr(pipeline_reads, "pipeline_policy_version", lambda: "replacement-policy")
    project_batch(database, batch=2)
    with database() as session:
        state = session.get(ReadModelState, "pipeline").payload
        assert state["serving_policy"] == old_policy
        assert state["serving_generated_at"] == previous
        _, snapshot = pipeline_reads.serving_projection(session)
        assert snapshot.generated_at.isoformat() == previous
        assert snapshot.status == "rebuilding"
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.policy_version == old_policy)
            )
            == 6
        )
    deletions = []

    def deleted(connection, cursor, statement, parameters, context, many):
        if statement.startswith("DELETE FROM pipeline_projections"):
            deletions.append(cursor.rowcount)

    engine = database.kw["bind"]
    event.listen(engine, "after_cursor_execute", deleted)
    try:
        drain_pipeline(database, batch=2)
    finally:
        event.remove(engine, "after_cursor_execute", deleted)
    assert deletions and max(deletions) <= 2
    with database() as session:
        state = session.get(ReadModelState, "pipeline").payload
        assert state["serving_policy"] == "replacement-policy"
        assert state["serving_generated_at"] == now.isoformat()
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.policy_version == old_policy)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.policy_version == "replacement-policy")
            )
            == 6
        )


def test_terminal_retention_expires_without_new_writes_and_keeps_active_work(database):
    now = utcnow()
    with database.begin() as session:
        for _ in range(3):
            add_article(session, published=True, published_at=now - timedelta(days=6))
        active = add_article(session)
        active.discovered_at = now - timedelta(days=365)
        active_id = active.id
    drain_pipeline(database, batch=2)
    policy = pipeline_policy_version()
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.kind == "article")
            )
            == 4
        )
    project_batch(database, batch=2, now=now + timedelta(days=2))
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.kind == "article")
            )
            == 2
        )
    project_batch(database, batch=2, now=now + timedelta(days=2))
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.kind == "article")
            )
            == 1
        )
        assert session.get(PipelineProjection, (policy, "article", active_id)) is not None


def test_expired_publisher_cannot_ack_or_overwrite_replacement_and_drain_reports_busy(
    database, monkeypatch
):
    with database.begin() as session:
        identity = add_article(session).id
    drain_pipeline(database)
    policy = pipeline_policy_version()
    with database.begin() as session:
        session.execute(
            update(Article).where(Article.id == identity).values(title="New authoritative title")
        )
    original = pipeline_projection.article_rows

    def replaced(factory, identifiers, version, now):
        rows = original(factory, identifiers, version, now)
        with factory.begin() as session:
            lease = session.get(ReadModelState, "pipeline-worker")
            lease.payload = {
                "token": "replacement-worker",
                "expires_at": (utcnow() + timedelta(minutes=5)).isoformat(),
            }
            stored = session.get(PipelineProjection, (policy, "article", identity))
            stored.payload = {**stored.payload, "title": "Replacement publisher result"}
        return rows

    monkeypatch.setattr(pipeline_projection, "article_rows", replaced)
    assert project_batch(database) == 0
    with database() as session:
        assert (
            session.get(PipelineProjection, (policy, "article", identity)).payload["title"]
            == "Replacement publisher result"
        )
        assert session.scalar(select(ReadModelEvent.id).limit(1)) is not None
    with pytest.raises(RuntimeError, match="busy|lease expired"):
        drain_pipeline(database)
    with database.begin() as session:
        session.get(ReadModelState, "pipeline-worker").payload = {}
    monkeypatch.setattr(pipeline_projection, "article_rows", original)
    drain_pipeline(database)
    with database() as session:
        assert (
            session.get(PipelineProjection, (policy, "article", identity)).payload["title"]
            == "New authoritative title"
        )
        assert session.scalar(select(ReadModelEvent.id).limit(1)) is None


def test_larger_configured_batch_keeps_article_evaluation_below_two_hundred(database, admin_client):
    with database.begin() as session:
        for _ in range(201):
            add_article(session)
    drain_pipeline(database, batch=500)
    response = admin_client.get("/v1/admin/article-pipeline")
    assert response.status_code == 200
    assert sum(response.json()["counts"].values()) == 201
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.kind == "article")
            )
            == 201
        )
        assert session.scalar(select(ReadModelEvent.id).limit(1)) is None


def test_source_invalidation_fanout_is_bounded_and_reaches_every_article(database):
    with database.begin() as session:
        source = Source(
            name="Publisher",
            source_type="publisher",
            feed_url="https://publisher.example/feed",
            approval_status="approved",
            enabled=True,
        )
        session.add(source)
        session.flush()
        identifiers = [add_article(session, source=source).id for _ in range(7)]
        source_id = source.id
    drain_pipeline(database, batch=2)
    with database.begin() as session:
        session.execute(update(Source).where(Source.id == source_id).values(enabled=False))
    policy = pipeline_policy_version()
    previous = 0
    for _ in range(10):
        project_batch(database, batch=2)
        with database() as session:
            rows = list(
                session.scalars(
                    select(PipelineProjection).where(
                        PipelineProjection.kind == "article",
                        PipelineProjection.entity_id.in_(identifiers),
                        PipelineProjection.policy_version == policy,
                    )
                )
            )
            changed = sum(
                any(
                    item["code"] == "source_ineligible"
                    for item in row.payload["_automation_blockers"]
                )
                for row in rows
            )
            assert changed - previous <= 2
            previous = changed
            pending = session.scalar(select(ReadModelEvent.id).limit(1))
        if pending is None:
            break
    assert previous == 7 and pending is None


def test_catalog_rebuild_keeps_complete_cards_visible_with_rebuilding_status(
    database, admin_client
):
    from devfeed_core.models import Topic

    with database.begin() as session:
        for _ in range(3):
            add_article(session)
    drain_pipeline(database, batch=2)
    before = admin_client.get("/v1/admin/article-pipeline")
    assert before.status_code == 200 and before.json()["snapshot"]["status"] == "fresh"
    with database.begin() as session:
        session.add(
            Topic(name="Python runtime", slug="python-runtime", kind="language", status="active")
        )
    project_batch(database, batch=1)
    after = admin_client.get("/v1/admin/article-pipeline")
    assert after.status_code == 200
    assert after.json()["snapshot"]["status"] == "rebuilding"
    assert after.json()["snapshot"]["generation"] == before.json()["snapshot"]["generation"]
    assert after.json()["snapshot"]["generated_at"] == before.json()["snapshot"]["generated_at"]
    assert after.json()["counts"] == before.json()["counts"]
    assert after.json()["columns"] == before.json()["columns"]
