"""Read-model changes survive SQL writers, rollback, cascades, and commit reordering."""

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleReview,
    IngestionJob,
    PipelineProjection,
    ReadModelEvent,
    Source,
    SourceCandidate,
    SourceDiscoveryJob,
    SourceEnrichmentJob,
    SourceReview,
    Tag,
    Topic,
    utcnow,
)
from devfeed_core.read_models import (
    acknowledge_events,
    enqueue_event,
    event_batch,
    get_state,
    pipeline_policy_version,
    set_state,
)
from sqlalchemy import delete, select, text, update


def article(session):
    identifier = uuid.uuid4()
    row = Article(
        id=identifier,
        canonical_url=f"https://publisher.example/{identifier}",
        url_hash=identifier.hex,
        title="Inspect Python coroutine state",
        summary="Source evidence",
    )
    session.add(row)
    session.flush()
    return row


def source(session):
    identifier = uuid.uuid4()
    row = Source(
        id=identifier,
        name="Publisher " + identifier.hex,
        feed_url=f"https://publisher.example/{identifier}/feed",
        source_type="publisher",
    )
    session.add(row)
    session.flush()
    return row


def candidate(session, owner=None):
    row = SourceCandidate(
        name="Discovery candidate",
        identity_url=f"https://{uuid.uuid4().hex}.example/",
        source_id=owner.id if owner else None,
    )
    session.add(row)
    session.flush()
    return row


def clear_events(session):
    session.flush()
    session.execute(delete(ReadModelEvent))


def changes(session):
    session.flush()
    return set(session.execute(select(ReadModelEvent.kind, ReadModelEvent.entity_id)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ai_enabled", True),
        ("full_automation", True),
        ("image_storage_enabled", True),
        ("image_public_url", "https://images.example"),
        ("analysis_max_candidates", 81),
        ("analysis_fallback_candidates", 9),
        ("ai_content_not_before", "2026-01-01"),
    ],
)
def test_policy_identity_includes_every_readiness_setting(field, value):
    settings = get_settings()
    assert pipeline_policy_version(settings) != pipeline_policy_version(
        settings.model_copy(update={field: value})
    )


def test_policy_identity_excludes_connection_credentials_and_batch_capacity():
    settings = get_settings()
    changed = settings.model_copy(
        update={
            "database_url": "postgresql+psycopg://other:private@elsewhere.invalid/unit_test",
            "redis_url": "redis://elsewhere.invalid/15",
            "database_pool_size": 20,
        }
    )
    assert pipeline_policy_version(settings) == pipeline_policy_version(changed)


def test_policy_identity_tracks_logo_contract_changes(monkeypatch):
    from devfeed_core import logos

    before = pipeline_policy_version()
    monkeypatch.setattr(logos, "LOGO_VERSION", "profile-next-logo-version")
    assert pipeline_policy_version() != before


@pytest.mark.integration
def test_raw_bulk_article_updates_and_rollback_are_transactional(database):
    with database.begin() as session:
        identifiers = [article(session).id for _ in range(3)]
        clear_events(session)
    with database.begin() as session:
        session.execute(text("UPDATE articles SET ai_title = 'Current title'"))
        assert changes(session) == {("article", identifier) for identifier in identifiers}
    with database.begin() as session:
        clear_events(session)
    with database() as session:
        session.execute(text("UPDATE articles SET ai_summary = 'Rolled back'"))
        assert changes(session)
        session.rollback()
    with database() as session:
        assert changes(session) == set()
        assert session.scalar(select(Article.ai_summary).limit(1)) is None


@pytest.mark.integration
def test_content_and_review_writes_capture_their_article_owner(database):
    with database.begin() as session:
        row = article(session)
        clear_events(session)
        content = ArticleContent(
            article_id=row.id,
            url=row.canonical_url,
            text="Private evidence",
            content_hash="v1",
            method="page",
        )
        session.add(content)
        assert changes(session) == {("article", row.id)}
        clear_events(session)
        session.execute(update(ArticleContent).values(text="Changed evidence", content_hash="v2"))
        assert changes(session) == {("article", row.id)}
        clear_events(session)
        session.delete(content)
        assert changes(session) == {("article", row.id)}
        clear_events(session)
        review = ArticleReview(article_id=row.id, action="reject", revision=1)
        session.add(review)
        assert changes(session) == {("article", row.id)}
        clear_events(session)
        session.delete(review)
        assert changes(session) == {("article", row.id)}


@pytest.mark.integration
def test_origin_reassignment_and_candidate_linking_capture_old_and_new_owners(database):
    with database.begin() as session:
        first, second = article(session), article(session)
        old_source, new_source = source(session), source(session)
        origin = ArticleOrigin(
            article_id=first.id,
            source_id=old_source.id,
            entry_key="entry",
            original_url=first.canonical_url,
        )
        session.add(origin)
        standalone = candidate(session, old_source)
        clear_events(session)
        session.execute(update(ArticleOrigin).values(article_id=second.id))
        assert changes(session) == {("article", first.id), ("article", second.id)}
        clear_events(session)
        session.execute(update(SourceCandidate).values(source_id=new_source.id))
        assert changes(session) == {
            ("candidate", standalone.id),
            ("source", old_source.id),
            ("source", new_source.id),
        }
        clear_events(session)
        session.execute(update(SourceCandidate).values(source_id=None))
        assert changes(session) == {("candidate", standalone.id), ("source", new_source.id)}


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["enrichment", "analysis", "images"])
def test_article_job_insert_progress_failure_retry_and_deletion_are_captured(database, kind):
    with database.begin() as session:
        row = article(session)
        model = {
            "enrichment": ArticleEnrichmentJob,
            "analysis": ArticleAnalysisJob,
            "images": ArticleImageJob,
        }[kind]
        clear_events(session)
        job = model(article_id=row.id)
        session.add(job)
        assert changes(session) == {("article", row.id)}
        for values in (
            {"status": "running"},
            {"status": "failed", "finished_at": utcnow(), "error": "failed"},
            {"status": "queued", "attempts": 1, "available_at": utcnow() + timedelta(minutes=5)},
        ):
            clear_events(session)
            session.execute(update(model).where(model.id == job.id).values(**values))
            assert changes(session) == {("article", row.id)}
        if kind == "analysis":
            clear_events(session)
            session.execute(
                update(model).where(model.id == job.id).values(usage={"active_step": "language"})
            )
            assert changes(session) == {("article", row.id)}
        clear_events(session)
        session.delete(job)
        assert changes(session) == {("article", row.id)}


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["profile", "ingestion", "branding", "discovery", "review"])
def test_source_jobs_and_reviews_capture_source_and_discovery_candidate(database, kind):
    with database.begin() as session:
        owner = source(session)
        linked = candidate(session, owner)
        clear_events(session)
        if kind == "profile":
            job = SourceEnrichmentJob(source_id=owner.id, operation="profile")
        elif kind == "ingestion":
            job = IngestionJob(source_id=owner.id)
        elif kind == "branding":
            job = ArticleImageJob(source_id=owner.id, operation="source-logo")
        elif kind == "discovery":
            job = SourceDiscoveryJob(candidate_id=linked.id)
        else:
            job = SourceReview(source_id=owner.id, decision="approved")
        expected = {("source", owner.id)}
        if kind == "discovery":
            expected.add(("candidate", linked.id))
        session.add(job)
        assert changes(session) == expected
        clear_events(session)
        session.delete(job)
        assert changes(session) == expected


@pytest.mark.integration
def test_only_article_relevant_source_changes_request_article_fanout(database):
    with database.begin() as session:
        owner = source(session)
        clear_events(session)
        session.execute(
            update(Source).where(Source.id == owner.id).values(last_attempt_at=utcnow())
        )
        assert changes(session) == {("source", owner.id)}
        clear_events(session)
        session.execute(
            update(Source).where(Source.id == owner.id).values(name="Renamed publisher")
        )
        assert changes(session) == {("source", owner.id), ("source_fanout", owner.id)}
        clear_events(session)
        session.execute(update(Source).where(Source.id == owner.id).values(enabled=False))
        assert changes(session) == {("source", owner.id), ("source_fanout", owner.id)}


@pytest.mark.integration
def test_cascading_article_delete_and_source_unlink_leave_durable_tombstones(database):
    with database.begin() as session:
        owner, row = source(session), article(session)
        linked = candidate(session, owner)
        session.add(ArticleEnrichmentJob(article_id=row.id))
        session.add(
            ArticleOrigin(
                article_id=row.id,
                source_id=owner.id,
                entry_key="retained",
                original_url=row.canonical_url,
            )
        )
        clear_events(session)
        session.execute(delete(Article).where(Article.id == row.id))
        assert ("article", row.id) in changes(session)
        clear_events(session)
        session.execute(delete(Source).where(Source.id == owner.id))
        assert changes(session) == {("source", owner.id), ("candidate", linked.id)}
        assert (
            session.scalar(select(SourceCandidate.source_id).where(SourceCandidate.id == linked.id))
            is None
        )


@pytest.mark.integration
def test_catalog_bulk_changes_and_truncation_enqueue_global_rebuild(database):
    with database.begin() as session:
        session.add(Topic(name="Python", slug="python", kind="language", status="active"))
        session.add(Tag(name="Async", slug="async"))
        assert ("catalog", None) in changes(session)
        clear_events(session)
        session.execute(text("UPDATE topics SET ai_description = 'Python runtime internals'"))
        assert changes(session) == {("catalog", None)}
        clear_events(session)
        session.execute(text("UPDATE tags SET aliases = ARRAY['asyncio']::varchar[]"))
        assert changes(session) == {("catalog", None)}
        clear_events(session)
        session.execute(text("TRUNCATE article_contents"))
        assert changes(session) == {("policy", None)}


@pytest.mark.integration
def test_acknowledgement_preserves_an_older_id_committed_during_projection(database):
    with database.begin() as session:
        clear_events(session)
    with database() as delayed:
        late = enqueue_event(delayed, "article", uuid.uuid4())
        delayed.flush()
        with database.begin() as session:
            visible = enqueue_event(session, "article", uuid.uuid4())
            session.flush()
            visible_id = visible.id
        with database() as session:
            captured = [event.id for event in event_batch(session)]
        assert captured == [visible_id] and late.id < visible_id
        delayed.commit()
        with database.begin() as session:
            assert acknowledge_events(session, captured) == 1
        with database() as session:
            assert [event.id for event in event_batch(session)] == [late.id]


@pytest.mark.integration
def test_generation_upsert_refreshes_an_already_loaded_state(database):
    with database.begin() as session:
        set_state(session, "pipeline", {"target_policy": "first"})
        retained = get_state(session, "pipeline")
        assert retained.payload == {"target_policy": "first"}
        set_state(session, "pipeline", {"target_policy": "second"})
        assert retained.payload == {"target_policy": "second"}


@pytest.mark.integration
def test_policy_generations_can_coexist_without_overwriting_complete_cards(database):
    identifier = uuid.uuid4()
    with database.begin() as session:
        for policy in ("complete", "building"):
            session.add(
                PipelineProjection(
                    policy_version=policy,
                    kind="article",
                    entity_id=identifier,
                    payload={"policy": policy},
                    stage="analysis",
                    state="waiting",
                    created_at=utcnow(),
                    activity_at=utcnow(),
                )
            )
    with database() as session:
        assert session.get(PipelineProjection, ("complete", "article", identifier)).payload == {
            "policy": "complete"
        }
        assert session.get(PipelineProjection, ("building", "article", identifier)).payload == {
            "policy": "building"
        }


@pytest.mark.integration
def test_migration_round_trip_preserves_entities_and_requests_bounded_backfill(database):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with database.begin() as session:
        identifier = article(session).id
    try:
        command.downgrade(config, "0033")
        with database() as session:
            assert session.get(Article, identifier).title == "Inspect Python coroutine state"
            assert session.scalar(text("SELECT to_regclass('pipeline_projections')")) is None
        command.upgrade(config, "head")
        with database() as session:
            assert session.get(Article, identifier) is not None
            assert changes(session) == {("policy", None)}
    finally:
        command.upgrade(config, "head")
