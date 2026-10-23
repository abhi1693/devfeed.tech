"""Catalog churn preserves complete coverage and honest saved-report freshness."""

import uuid
from datetime import timedelta

import pytest
from devfeed_admin_api import automation, overview_projection, pipeline_projection, pipeline_reads
from devfeed_admin_api.pipeline_projection import drain_pipeline, project_batch
from devfeed_core.models import (
    PipelineProjection,
    ReadModelEvent,
    ReadModelState,
    ReportSnapshot,
    Tag,
    utcnow,
)
from devfeed_core.read_models import pipeline_policy_version
from sqlalchemy import event, select, text, update

from tests.admin.articles.test_article_pipeline import make_article
from tests.admin.reporting.projections.test_pipeline_projection_safety import add_article
from tests.admin.sources.test_source_pipeline import candidate_for, seed_source

pytestmark = pytest.mark.integration


def edit_catalog(database, number):
    with database.begin() as session:
        session.execute(text("UPDATE tags SET name=:name"), {"name": f"Catalog revision {number}"})


def publish_blockers(database):
    with database.begin() as session:
        overview_projection.request_refresh(session, "blockers", 1, utcnow())
    assert overview_projection.refresh_overview_projections(database, limit=1, seed=False) == 1


def test_truncate_after_article_backfill_replays_before_marking_coverage_complete(database):
    with database.begin() as session:
        articles = {add_article(session).id for _ in range(3)}
        sources = {seed_source(session).id for _ in range(6)}
    policy = pipeline_policy_version()
    for _ in range(50):
        project_batch(database, batch=2)
        with database() as session:
            state = session.get(ReadModelState, "pipeline").payload
            if "article" not in state["pending_backfills"] and state["pending_backfills"]:
                projected = set(
                    session.scalars(
                        select(PipelineProjection.entity_id).where(
                            PipelineProjection.policy_version == policy,
                            PipelineProjection.kind == "article",
                        )
                    )
                )
                assert projected == articles
                break
    else:
        pytest.fail("Article coverage did not finish before the remaining source batches")
    with database.begin() as session:
        session.execute(text("TRUNCATE articles CASCADE"))
    drain_pipeline(database, batch=2)
    with database() as session:
        projected = set(
            session.execute(
                select(PipelineProjection.kind, PipelineProjection.entity_id).where(
                    PipelineProjection.policy_version == policy
                )
            )
        )
        assert projected == {("source", identifier) for identifier in sources}
        assert session.get(ReadModelState, "pipeline").payload["backfill_complete"]
        assert session.scalar(select(ReadModelEvent.id).limit(1)) is None


def test_initial_policy_event_does_not_repeat_article_coverage(database, monkeypatch):
    with database.begin() as session:
        identifier = add_article(session).id
        # The migration requests policy coverage before the worker initializes.
        session.add(ReadModelEvent(kind="policy"))
        # Remove row events so only the policy-triggered coverage evaluates it.
        session.execute(text("DELETE FROM read_model_events WHERE kind = 'article'"))
    original = pipeline_projection.article_rows
    evaluated = []

    def record(factory, identifiers, policy, now):
        evaluated.extend(identifiers)
        return original(factory, identifiers, policy, now)

    monkeypatch.setattr(pipeline_projection, "article_rows", record)
    drain_pipeline(database, batch=2)
    assert evaluated == [identifier]


@pytest.mark.parametrize("previous_policy", [False, True])
def test_catalog_churn_publishes_full_coverage_after_bounded_orphan_cleanup(
    database, admin_client, monkeypatch, previous_policy
):
    with database.begin() as session:
        articles = {add_article(session).id for _ in range(3)}
        source = seed_source(session)
        candidate = candidate_for(session)
        expected = {("article", identifier) for identifier in articles} | {
            ("source", source.id),
            ("candidate", candidate.id),
        }
        session.add(Tag(name="Original", slug="original"))
    old_policy = pipeline_policy_version()
    if previous_policy:
        drain_pipeline(database, batch=2)
        target = "replacement-policy"
        monkeypatch.setattr(pipeline_projection, "pipeline_policy_version", lambda: target)
        monkeypatch.setattr(pipeline_reads, "pipeline_policy_version", lambda: target)
    else:
        target = old_policy
    now = utcnow()
    orphans = {uuid.uuid4() for _ in range(3)}
    with database.begin() as session:
        session.add_all(
            PipelineProjection(
                policy_version=target,
                kind="article",
                entity_id=identifier,
                payload={"title": "Deleted before the coverage pass"},
                stage="ingested",
                state="ready",
                created_at=now - timedelta(days=1),
                activity_at=now - timedelta(days=1),
                evaluated_at=now - timedelta(days=1),
            )
            for identifier in orphans
        )
    deletions = []

    def deleted(connection, cursor, statement, parameters, context, many):
        if statement.startswith("DELETE FROM pipeline_projections"):
            deletions.append(cursor.rowcount)

    engine = database.kw["bind"]
    event.listen(engine, "after_cursor_execute", deleted)
    try:
        for cycle in range(100):
            edit_catalog(database, cycle)
            project_batch(database, batch=2)
            with database() as session:
                state = session.get(ReadModelState, "pipeline").payload
                keys = set(
                    session.execute(
                        select(PipelineProjection.kind, PipelineProjection.entity_id).where(
                            PipelineProjection.policy_version == target
                        )
                    )
                )
                if state.get("serving_policy") == target:
                    assert keys == expected
                    assert state["backfill_complete"]
                    assert not state["pending_backfills"]
                    assert state["serving_generated_at"] >= now.isoformat()
                    break
                if previous_policy:
                    assert state["serving_policy"] == old_policy
                    old_keys = set(
                        session.execute(
                            select(PipelineProjection.kind, PipelineProjection.entity_id).where(
                                PipelineProjection.policy_version == old_policy
                            )
                        )
                    )
                    assert old_keys == expected
                else:
                    assert admin_client.get("/v1/admin/article-pipeline").status_code == 503
        else:
            pytest.fail("Continuous catalog edits prevented publication of complete coverage")
        for cycle in range(100, 104):
            edit_catalog(database, cycle)
            project_batch(database, batch=2)
            response = admin_client.get("/v1/admin/article-pipeline")
            assert response.status_code == 200
            # Catalog freshness must not invalidate completed population coverage.
            assert response.json()["snapshot"]["status"] in {"fresh", "refreshing"}
            assert sum(response.json()["counts"].values()) == len(articles)
            with database() as session:
                state = session.get(ReadModelState, "pipeline").payload
                assert state["backfill_complete"] and not state["pending_backfills"]
    finally:
        event.remove(engine, "after_cursor_execute", deleted)
    assert deletions and max(deletions) <= 2
    drain_pipeline(database, batch=2)
    response = admin_client.get("/v1/admin/article-pipeline")
    assert response.json()["snapshot"]["status"] == "fresh"


def test_saved_blockers_are_available_during_churn_and_become_fresh_after_catchup(
    database, admin_client, monkeypatch
):
    with database.begin() as session:
        identifiers = [add_article(session).id for _ in range(3)]
        for _ in range(3):
            make_article(session, state="ingested")
        session.add(Tag(name="Original", slug="original"))
    drain_pipeline(database, batch=2)
    edit_catalog(database, 1)
    project_batch(database, batch=1)
    with database() as session:
        state = session.get(ReadModelState, "pipeline").payload
        assert state["backfill_complete"] and state["catalog_refresh"]
    publish_blockers(database)
    with database() as session:
        saved = session.get(ReportSnapshot, ("blockers", 1))
        assert saved.payload["_projection_snapshot"]["status"] == "refreshing"
        expected = saved.payload["automation"]["blockers"]
        assert any(item["count"] > 0 for item in expected)
    statements = []

    def record(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lower())

    with monkeypatch.context() as patch:
        patch.setattr(
            overview_projection,
            "calculate_report",
            lambda *_: pytest.fail("A GET calculated the blocker report"),
        )
        event.listen(database.kw["bind"], "before_cursor_execute", record)
        try:
            response = admin_client.get("/v1/admin/overview/panels/blockers?days=30")
        finally:
            event.remove(database.kw["bind"], "before_cursor_execute", record)
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == "refreshing"
    assert response.json()["automation"]["blockers"] == expected
    assert not any("pipeline_projections" in statement for statement in statements)
    assert not any("read_model_events" in statement for statement in statements)
    drain_pipeline(database, batch=2)
    publish_blockers(database)
    response = admin_client.get("/v1/admin/overview/panels/blockers?days=30")
    assert response.json()["snapshot"]["status"] == "fresh"
    assert response.json()["automation"]["blockers"] == expected
    with database.begin() as session:
        session.add(ReadModelEvent(kind="article", entity_id=identifiers[0]))
    publish_blockers(database)
    response = admin_client.get("/v1/admin/overview/panels/blockers?days=30")
    assert response.json()["snapshot"]["status"] == "refreshing"
    drain_pipeline(database, batch=2)
    publish_blockers(database)
    assert (
        admin_client.get("/v1/admin/overview/panels/blockers?days=30").json()["snapshot"]["status"]
        == "fresh"
    )


def test_blocker_calculation_keeps_one_snapshot_across_policy_cutover_and_pruning(
    database, monkeypatch
):
    with database.begin() as session:
        add_article(session)
    drain_pipeline(database)
    with database() as session:
        expected = [
            item.model_dump(mode="json") for item in overview_projection.projected_blockers(session)
        ]
    assert any(item["count"] > 0 for item in expected)
    old_policy = pipeline_policy_version()
    original = automation.automation_blockers
    isolation = []

    def cutover(session, *args, **kwargs):
        isolation.append(session.scalar(text("SHOW transaction_isolation")))
        assert session.scalar(text("SHOW transaction_read_only")) == "on"
        with database.begin() as publisher:
            publisher.execute(
                update(PipelineProjection)
                .where(PipelineProjection.policy_version == old_policy)
                .values(policy_version="replacement-policy")
            )
            state = publisher.get(ReadModelState, "pipeline")
            state.payload = {**state.payload, "serving_policy": "replacement-policy"}
        return original(session, *args, **kwargs)

    monkeypatch.setattr(automation, "automation_blockers", cutover)
    result = overview_projection.calculate_report(database, "blockers", 1, utcnow())
    assert isolation == ["repeatable read"]
    assert [item.model_dump(mode="json") for item in result.automation.blockers] == expected
    assert result.snapshot.status == "fresh"


def test_catalog_edits_and_ingestion_do_not_rewind_active_coverage(database, monkeypatch):
    with database.begin() as session:
        sensitive = {make_article(session).id for _ in range(7)}
        session.add(Tag(name="Original", slug="original"))
    drain_pipeline(database, batch=2)
    original = pipeline_projection.article_rows
    batches = []

    def record(factory, identifiers, policy, now):
        batches.append(list(identifiers))
        return original(factory, identifiers, policy, now)

    monkeypatch.setattr(pipeline_projection, "article_rows", record)
    edit_catalog(database, 1)
    project_batch(database, batch=2)
    with database() as session:
        initial = session.get(ReadModelState, "pipeline").payload["catalog_refresh"]
        first_cursor = session.scalar(
            select(ReadModelEvent.after_id).where(ReadModelEvent.kind == "catalog")
        )
        assert first_cursor is not None
    with database.begin() as session:
        ingested = add_article(session).id
    edit_catalog(database, 2)
    project_batch(database, batch=2)
    with database() as session:
        state = session.get(ReadModelState, "pipeline").payload
        assert state["catalog_refresh"] == initial
        cursors = list(
            session.scalars(
                select(ReadModelEvent.after_id).where(
                    ReadModelEvent.kind == "catalog", ReadModelEvent.after_id.is_not(None)
                )
            )
        )
        assert cursors and all(cursor > first_cursor for cursor in cursors)
        assert state["backfill_complete"] and not state["pending_backfills"]
    for _ in range(30):
        project_batch(database, batch=2)
        with database() as session:
            state = session.get(ReadModelState, "pipeline").payload
            assert state["backfill_complete"] and not state["pending_backfills"]
            assert (
                session.scalar(
                    select(ReadModelEvent.id).where(ReadModelEvent.kind.like("backfill_%")).limit(1)
                )
                is None
            )
            if session.scalar(select(ReadModelEvent.id).limit(1)) is None:
                assert "catalog_refresh" not in state
                break
    else:
        pytest.fail("Coalesced catalog refresh never caught up")
    evaluated = [identifier for batch in batches for identifier in batch]
    # One pass for each catalog revision, and only one targeted ingestion event.
    assert all(evaluated.count(identifier) == 2 for identifier in sensitive)
    assert evaluated.count(ingested) == 1
    assert len(evaluated) == len(sensitive) * 2 + 1
    assert max(map(len, batches)) <= 2
