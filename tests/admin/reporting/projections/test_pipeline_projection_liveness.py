"""Catalog churn preserves complete coverage and honest saved-report freshness."""

import uuid
from collections import Counter
from datetime import timedelta

import pytest
from devfeed_admin_api import (
    article_pipeline,
    automation,
    overview_projection,
    pipeline_projection,
    pipeline_reads,
)
from devfeed_admin_api.pipeline_projection import drain_pipeline, project_batch
from devfeed_core import read_models
from devfeed_core.models import (
    Article,
    PipelineProjection,
    ReadModelEvent,
    ReadModelState,
    ReportSnapshot,
    Source,
    Tag,
    utcnow,
)
from devfeed_core.read_models import pipeline_policy_version
from sqlalchemy import delete, event, select, text, update

from tests.admin.articles.test_article_pipeline import make_article
from tests.admin.reporting.projections.test_pipeline_projection_safety import add_article
from tests.admin.sources.test_source_pipeline import candidate_for, seed_source

pytestmark = pytest.mark.integration


def edit_catalog(database, number):
    with database.begin() as session:
        session.execute(text("UPDATE tags SET name=:name"), {"name": f"Catalog revision {number}"})


def publish_blockers(database, *, now=None):
    with database.begin() as session:
        overview_projection.request_refresh(session, "blockers", 1, now or utcnow())
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
            assert response.json()["snapshot"]["status"] in {"fresh", "catching_up"}
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
        assert saved.payload["_projection_snapshot"]["status"] == "catching_up"
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
    assert response.json()["snapshot"]["status"] == "catching_up"
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
        assert {key: state["catalog_refresh"][key] for key in initial if key != "progress_at"} == {
            key: value for key, value in initial.items() if key != "progress_at"
        }
        assert state["catalog_refresh"]["progress_at"] >= initial["progress_at"]
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


def test_long_catalog_churn_publishes_observed_generations_with_live_ingestion_and_deletions(
    database, admin_client, monkeypatch
):
    now = utcnow()
    for module in (pipeline_projection, pipeline_reads, overview_projection, read_models):
        monkeypatch.setattr(module, "utcnow", lambda: now)
    with database.begin() as session:
        sensitive = [make_article(session).id for _ in range(15)]
        source = Source(
            name="Recovery publisher",
            source_type="publisher",
            feed_url="https://recovery.example/feed",
            approval_status="approved",
            enabled=True,
        )
        session.add(source)
        session.flush()
        article = add_article(session, source=source)
        article.summary = ""
        recoverable = article.id
        source_id = source.id
        session.add(Tag(name="Original", slug="original"))
    drain_pipeline(database, batch=2)
    publish_blockers(database, now=now)
    with database() as session:
        blockers = session.get(ReportSnapshot, ("blockers", 1)).payload["automation"]["blockers"]
        saved_target = next(
            target
            for blocker in blockers
            if blocker["action"] == "enrich"
            for target in blocker["targets"]
            if target["id"] == str(recoverable)
        )
    original = pipeline_projection.article_rows
    batches, inserted = [], []
    changed_during_evaluation = False
    interrupt_requested = False

    def evaluate(factory, identifiers, policy, stamp):
        nonlocal changed_during_evaluation, interrupt_requested
        batches.append(list(identifiers))
        rows = original(factory, identifiers, policy, stamp)
        if interrupt_requested:
            interrupt_requested = False
            raise RuntimeError("Interrupted publisher during continuous catalog catchup")
        if not changed_during_evaluation:
            changed_during_evaluation = True
            # Commit authoritative changes while the publisher holds computed
            # cards but has not committed its generation. Their independent
            # events must survive acknowledgement of the captured batch.
            with database.begin() as session:
                source = session.get(Source, source_id)
                inserted.append(add_article(session, source=source).id)
                session.execute(
                    update(Article)
                    .where(Article.id == recoverable)
                    .values(editorial_revision=1, title="Changed after the saved target")
                )
                session.execute(delete(Article).where(Article.id == sensitive[-1]))
                session.execute(
                    update(ReadModelEvent)
                    .where(ReadModelEvent.kind != "catalog")
                    .values(created_at=stamp)
                )
        return rows

    monkeypatch.setattr(pipeline_projection, "article_rows", evaluate)
    edit_catalog(database, 1)
    project_batch(database, batch=2)
    assert changed_during_evaluation
    # An observed projection can legitimately retain revision zero while its
    # authoritative article has already changed. Recovery still uses live CAS.
    response = admin_client.post(
        f"/v1/admin/automation/articles/{recoverable}/enrich",
        json={"expected_revision": saved_target["revision"]},
    )
    assert response.status_code == 409 and "Article changed" in response.json()["detail"]
    generations, completed, catalog_ages, report_times = [], [], [], []
    interruptions = 0
    for cycle in range(2, 47):
        now += timedelta(seconds=15)
        edit_catalog(database, cycle)
        if cycle == 12:
            interrupt_requested = True
        with database() as session:
            before = dict(session.get(ReadModelState, "pipeline").payload)
        try:
            project_batch(database, batch=2, token=f"publisher-process-{cycle}")
        except RuntimeError as error:
            assert "Interrupted publisher" in str(error)
            interruptions += 1
            with database() as session:
                assert session.get(ReadModelState, "pipeline").payload == before
                assert session.scalar(select(ReadModelEvent.id).limit(1)) is not None
            # A report can publish the preceding committed generation while the
            # next publisher process resumes the unacknowledged page next tick.
        with database() as session:
            dependency = pipeline_reads.serving_projection(session, kind="article")[1]
            assert dependency.lag_seconds <= 60
        publish_blockers(database, now=now)
        with database() as session:
            state = session.get(ReadModelState, "pipeline").payload
            saved = session.get(ReportSnapshot, ("blockers", 1))
            dependency = saved.payload["_projection_snapshot"]
            assert state["backfill_complete"] and not state["pending_backfills"]
            assert dependency["policy_version"] == state["serving_policy"]
            assert dependency["article_generation"] == state["article_generation"]
            assert dependency["generation"] == (
                f"{state['serving_policy']}:{state['article_generation']}"
            )
            assert dependency["status"] == "catching_up"
            assert dependency["lag_seconds"] <= 60
            catalog_ages.append(dependency["catalog_lag_seconds"])
            generations.append(dependency["article_generation"])
            completed.append(dependency["catalog_generation"])
            report_times.append(saved.generated_at)
            cards = list(
                session.scalars(
                    select(PipelineProjection).where(
                        PipelineProjection.policy_version == state["serving_policy"],
                        PipelineProjection.kind == "article",
                    )
                )
            )
            by_id = {str(card.entity_id): card.payload for card in cards}
            expected = Counter(
                blocker["code"]
                for card in cards
                for blocker in card.payload["_automation_blockers"]
            )
            for blocker in saved.payload["automation"]["blockers"]:
                assert blocker["count"] == expected[blocker["code"]]
                assert len(blocker["targets"]) <= 5
                for target in blocker["targets"]:
                    card = by_id[target["id"]]
                    assert target["revision"] == card["editorial_revision"]
                    assert target["title"] == card["_search"]["title"]
        response = admin_client.get("/v1/admin/overview/panels/blockers?days=30")
        assert response.status_code == 200
        assert response.json()["snapshot"]["status"] == "catching_up"
    assert max(catalog_ages) > 60
    assert interruptions == 1
    assert generations == sorted(generations) and len(set(generations)) > 2
    assert completed[-1] - completed[0] >= 2
    assert report_times == sorted(report_times) and len(set(report_times)) == len(report_times)
    assert max(map(len, batches)) == 2
    monkeypatch.setattr(pipeline_projection, "article_rows", original)
    drain_pipeline(database, batch=2)
    publish_blockers(database, now=now)
    response = admin_client.get("/v1/admin/overview/panels/blockers?days=30")
    assert response.json()["snapshot"]["status"] == "fresh"
    with database() as session:
        state = session.get(ReadModelState, "pipeline").payload
        assert "catalog_refresh" not in state
        assert session.scalar(select(ReadModelEvent.id).limit(1)) is None
        assert (
            session.get(PipelineProjection, (state["serving_policy"], "article", sensitive[-1]))
            is None
        )
        assert (
            session.get(PipelineProjection, (state["serving_policy"], "article", inserted[0]))
            is not None
        )
        target = session.get(PipelineProjection, (state["serving_policy"], "article", recoverable))
        assert target.payload["editorial_revision"] == 1


def test_catalog_generation_and_cursor_survive_failed_batch_and_restarted_publisher(
    database, monkeypatch
):
    with database.begin() as session:
        for _ in range(5):
            make_article(session)
        session.add(Tag(name="Original", slug="original"))
    drain_pipeline(database, batch=2)
    edit_catalog(database, 1)
    project_batch(database, batch=1, token="first-process")
    with database() as session:
        before = dict(session.get(ReadModelState, "pipeline").payload)
        cursor = session.execute(
            select(ReadModelEvent.id, ReadModelEvent.after_id).where(
                ReadModelEvent.kind == "catalog"
            )
        ).one()
    original = pipeline_projection.article_rows

    def interrupted(*_args):
        raise RuntimeError("Publisher process interrupted before committing computed cards")

    monkeypatch.setattr(pipeline_projection, "article_rows", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        project_batch(database, batch=1, token="interrupted-process")
    with database() as session:
        assert session.get(ReadModelState, "pipeline").payload == before
        assert (
            session.execute(
                select(ReadModelEvent.id, ReadModelEvent.after_id).where(
                    ReadModelEvent.kind == "catalog"
                )
            ).one()
            == cursor
        )
    monkeypatch.setattr(pipeline_projection, "article_rows", original)
    assert project_batch(database, batch=1, token="restarted-process") > 0
    with database() as session:
        resumed = session.get(ReadModelState, "pipeline").payload
        assert resumed["catalog_refresh"]["token"] == before["catalog_refresh"]["token"]
        assert resumed["article_generation"] > before["article_generation"]
        assert (
            session.scalar(select(ReadModelEvent.after_id).where(ReadModelEvent.kind == "catalog"))
            > cursor.after_id
        )
    drain_pipeline(database, batch=1)
    with database() as session:
        state = session.get(ReadModelState, "pipeline").payload
        assert "catalog_refresh" not in state
        assert state["catalog_generation"] > before["catalog_generation"]


def test_article_publication_reads_revision_and_blocker_facts_from_one_snapshot(
    database, monkeypatch
):
    with database.begin() as session:
        source = seed_source(session, approval="approved", enabled=True)
        article = add_article(session, source=source)
        article.summary = ""
        identity = article.id
    drain_pipeline(database)
    with database.begin() as session:
        session.execute(
            update(Article).where(Article.id == identity).values(title="Observed before ingestion")
        )
    original = article_pipeline._batch_facts
    isolation = []

    def ingest_between_card_and_blocker_reads(session, articles, **kwargs):
        facts = original(session, articles, **kwargs)
        isolation.append(session.scalar(text("SHOW transaction_isolation")))
        assert session.scalar(text("SHOW transaction_read_only")) == "on"
        with database.begin() as writer:
            writer.execute(
                update(Article)
                .where(Article.id == identity)
                .values(
                    editorial_revision=1,
                    summary="Newly ingested text about Python coroutine execution.",
                )
            )
        return facts

    monkeypatch.setattr(article_pipeline, "_batch_facts", ingest_between_card_and_blocker_reads)
    assert project_batch(database) > 0
    assert isolation == ["repeatable read"]
    policy = pipeline_policy_version()
    with database() as session:
        card = session.get(PipelineProjection, (policy, "article", identity)).payload
        assert card["editorial_revision"] == 0
        assert "insufficient_text" in {item["code"] for item in card["_automation_blockers"]}
        assert (
            session.scalar(
                select(ReadModelEvent.id).where(
                    ReadModelEvent.kind == "article", ReadModelEvent.entity_id == identity
                )
            )
            is not None
        )
    monkeypatch.setattr(article_pipeline, "_batch_facts", original)
    drain_pipeline(database)
    with database() as session:
        card = session.get(PipelineProjection, (policy, "article", identity)).payload
        assert card["editorial_revision"] == 1
        assert "insufficient_text" not in {item["code"] for item in card["_automation_blockers"]}
        assert session.scalar(select(ReadModelEvent.id).limit(1)) is None
