"""Saved reports remain available while bounded publishers fail or restart."""

import uuid
from datetime import timedelta

import pytest
from devfeed_admin_api import overview_projection as projections
from devfeed_admin_api.overview_panel_data import OverviewPanel
from devfeed_admin_api.overview_processing import processing_facts, refresh_processing_facts
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    PipelineProjection,
    ReadModelState,
    ReportRefresh,
    ReportSnapshot,
    utcnow,
)
from devfeed_core.read_models import ReadModelSnapshot, pipeline_policy_version
from sqlalchemy import event, func, select

pytestmark = pytest.mark.integration


def save_panel(database, group="daily", days=7, *, generated_at=None, policy=None):
    now = generated_at or utcnow()
    panel = OverviewPanel(generated_at=now, days=days, articles_pending_review=23)
    with database.begin() as session:
        session.merge(
            ReportSnapshot(
                group=group,
                days=days,
                payload=panel.model_dump(mode="json"),
                generated_at=now,
                policy_version=policy or projections.report_policy_version(),
            )
        )
    return panel


def save_overview(database, days=30, *, generated_at=None):
    from devfeed_admin_api.overview import overview_metrics

    with database() as session:
        report = overview_metrics(session, days)
    if generated_at is not None:
        report.generated_at = generated_at
    with database.begin() as session:
        session.merge(
            ReportSnapshot(
                group="overview",
                days=days,
                payload=report.model_dump(mode="json"),
                generated_at=report.generated_at,
                policy_version=projections.report_policy_version(),
            )
        )
    return report


def test_cold_reads_only_coalesce_demand_and_never_calculate(database, admin_client, monkeypatch):
    monkeypatch.setattr(
        projections, "calculate_report", lambda *_: pytest.fail("GET started report calculation")
    )
    statements = []

    def record(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lower())

    engine = database.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        for _ in range(5):
            response = admin_client.get("/v1/admin/overview/panels/readers?days=17")
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "report_refreshing"
        assert admin_client.get("/health/live").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not any("article_analysis_jobs" in statement for statement in statements)
    assert not any("article_contents" in statement for statement in statements)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportRefresh)) == 1
        assert session.get(ReportRefresh, ("daily", 17)) is not None
        assert session.scalar(select(func.count()).select_from(ReportSnapshot)) == 0


def test_last_complete_survives_redis_outage_and_process_restart(
    database, admin_client, monkeypatch
):
    from devfeed_admin_api.main import create_app
    from devfeed_core import cache
    from devfeed_core.config import get_settings
    from fastapi.testclient import TestClient

    generated = utcnow() - timedelta(days=2)
    save_panel(database, generated_at=generated)
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    monkeypatch.setattr(cache, "get_cache", lambda: pytest.fail("report serving contacted Redis"))
    first = admin_client.get("/v1/admin/overview/panels/readers?days=7")
    assert first.status_code == 200
    assert first.json()["articles_pending_review"] == 23
    assert first.json()["snapshot"]["status"] == "refreshing"
    assert first.headers["x-overview-stale"] == "true"
    app = create_app()
    app.dependency_overrides.update(admin_client.app.dependency_overrides)
    with TestClient(app) as restarted:
        second = restarted.get("/v1/admin/overview/panels/readers?days=7")
    assert second.status_code == 200
    assert second.json()["generated_at"] == first.json()["generated_at"]
    assert second.json()["articles_pending_review"] == 23
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportRefresh)) == 1


def test_worker_failure_preserves_snapshot_and_persistent_cooldown(
    database, admin_client, monkeypatch
):
    from devfeed_admin_api import overview_processing

    now = utcnow()
    monkeypatch.setattr(projections, "utcnow", lambda: now)
    monkeypatch.setattr(overview_processing, "refresh_processing_facts", lambda *_: False)
    save_panel(database, generated_at=now - timedelta(minutes=5))
    with database.begin() as session:
        projections.request_refresh(session, "daily", 7, now)
    calls = []

    def fail(*args):
        calls.append(1)
        raise ValueError("unavailable dependency")

    monkeypatch.setattr(projections, "calculate_report", fail)
    assert projections.refresh_overview_projections(database, limit=1, seed=False) == 0
    for _ in range(8):
        response = admin_client.get("/v1/admin/overview/panels/readers?days=7")
        assert response.status_code == 200
        assert response.json()["snapshot"]["status"] == "stale"
    assert projections.refresh_overview_projections(database, limit=1, seed=False) == 0
    with database() as session:
        queued = session.get(ReportRefresh, ("daily", 7))
        assert queued.available_at == now + timedelta(seconds=15)
        assert queued.last_error == "ValueError"
        assert session.get(ReportSnapshot, ("daily", 7)).generated_at < now
    assert calls == [1]
    now += timedelta(seconds=16)
    monkeypatch.setattr(
        projections,
        "calculate_report",
        lambda factory, group, days, stamp: OverviewPanel(generated_at=stamp, days=days),
    )
    assert projections.refresh_overview_projections(database, limit=1, seed=False) == 1
    response = admin_client.get("/v1/admin/overview/panels/readers?days=7")
    assert response.status_code == 200 and response.json()["snapshot"]["status"] == "fresh"
    with database() as session:
        assert session.get(ReportRefresh, ("daily", 7)) is None


def test_dependency_catchup_resets_old_failure_backoff_and_recovers_after_restart(
    database, admin_client, monkeypatch
):
    from devfeed_admin_api import pipeline_reads
    from devfeed_admin_api.main import create_app
    from fastapi.testclient import TestClient

    now = utcnow()
    for module in (projections, pipeline_reads):
        monkeypatch.setattr(module, "utcnow", lambda: now)
    policy = pipeline_policy_version()
    save_panel(database, "blockers", 1, generated_at=now - timedelta(hours=2))
    with database.begin() as session:
        session.add(
            ReadModelState(
                name="pipeline",
                payload={
                    "serving_policy": policy,
                    "target_policy": policy,
                    "backfill_complete": True,
                    "catalog_refresh": {
                        "token": str(uuid.uuid4()),
                        "revision": [],
                        "started_at": (now - timedelta(hours=1)).isoformat(),
                        "progress_at": (now - timedelta(seconds=61)).isoformat(),
                    },
                },
                updated_at=now,
            )
        )
        projections.request_refresh(session, "blockers", 1, now)
        session.flush()
        session.get(ReportRefresh, ("blockers", 1)).attempts = 30
    assert projections.refresh_overview_projections(database, limit=1, seed=False) == 0
    with database() as session:
        demand = session.get(ReportRefresh, ("blockers", 1))
        assert demand.available_at == now + timedelta(seconds=5)
        assert demand.attempts == 0 and demand.last_error == "ProjectionNotReady"
        assert demand.lease_token is None and demand.lease_until is None
    restarted = create_app()
    restarted.dependency_overrides.update(admin_client.app.dependency_overrides)
    with TestClient(restarted) as client:
        response = client.get("/v1/admin/overview/panels/blockers?days=30")
        assert response.status_code == 200
        assert response.json()["articles_pending_review"] == 23
        assert response.json()["snapshot"]["status"] == "stale"
    assert projections.refresh_overview_projections(database, limit=1, seed=False) == 0
    now += timedelta(seconds=6)
    with database.begin() as session:
        state = session.get(ReadModelState, "pipeline")
        state.payload = {
            **state.payload,
            "catalog_refresh": {**state.payload["catalog_refresh"], "progress_at": now.isoformat()},
        }
        state.updated_at = now
    assert projections.refresh_overview_projections(database, limit=1, seed=False) == 1
    response = admin_client.get("/v1/admin/overview/panels/blockers?days=30")
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == "catching_up"
    with database() as session:
        assert session.get(ReportRefresh, ("blockers", 1)) is None
        assert session.get(ReportSnapshot, ("blockers", 1)).generated_at == now


@pytest.mark.parametrize("operational_lag,status", [(40, "catching_up"), (59, "stale")])
def test_saved_dependency_ages_with_report_without_scanning_projection_rows(
    database, admin_client, monkeypatch, operational_lag, status
):
    now = utcnow()
    monkeypatch.setattr(projections, "utcnow", lambda: now)
    policy = pipeline_policy_version()
    save_panel(database, "blockers", 1, generated_at=now - timedelta(seconds=10))
    with database.begin() as session:
        saved = session.get(ReportSnapshot, ("blockers", 1))
        saved.payload = {
            **saved.payload,
            "_projection_snapshot": ReadModelSnapshot(
                status="catching_up",
                policy_version=policy,
                generation=f"{policy}:7",
                article_generation=7,
                catalog_generation=2,
                lag_seconds=operational_lag,
                catalog_lag_seconds=3600,
            ).model_dump(mode="json"),
        }
        session.add(
            ReadModelState(
                name="pipeline",
                payload={
                    "serving_policy": policy,
                    "target_policy": policy,
                    "backfill_complete": True,
                    "catalog_refresh": {
                        "token": str(uuid.uuid4()),
                        "revision": [],
                        "started_at": (now - timedelta(hours=1)).isoformat(),
                        "progress_at": now.isoformat(),
                    },
                },
                updated_at=now,
            )
        )
    statements = []

    def record(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lower())

    event.listen(database.kw["bind"], "before_cursor_execute", record)
    try:
        response = admin_client.get("/v1/admin/overview/panels/blockers?days=30")
    finally:
        event.remove(database.kw["bind"], "before_cursor_execute", record)
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == status
    assert response.json()["snapshot"]["lag_seconds"] == operational_lag + 10
    assert response.json()["snapshot"]["article_generation"] == 7
    assert not any("pipeline_projections" in statement for statement in statements)
    assert not any("read_model_events" in statement for statement in statements)


def test_expired_worker_cannot_replace_new_lease_snapshot(database):
    now = utcnow()
    with database.begin() as session:
        projections.request_refresh(session, "daily", 17, now)
    old = projections.claim_refresh(database, now)
    assert old is not None
    with database.begin() as session:
        session.get(ReportRefresh, ("daily", 17)).lease_until = now - timedelta(seconds=1)
    current = projections.claim_refresh(database, now)
    assert current is not None and current[2] != old[2]
    result = OverviewPanel(generated_at=now, days=17)
    assert not projections.publish_report(database, old, result, now)
    assert projections.publish_report(database, current, result, now)


@pytest.mark.parametrize("regression", ["generation", "timestamp"])
def test_report_publication_cannot_replace_newer_observed_generation_or_document(
    database, regression
):
    now = utcnow()
    policy = pipeline_policy_version()
    with database.begin() as session:
        session.add(
            ReadModelState(
                name="pipeline",
                payload={
                    "serving_policy": policy,
                    "target_policy": policy,
                    "backfill_complete": True,
                    "article_generation": 9,
                },
                updated_at=now,
            )
        )
        projections.request_refresh(session, "blockers", 1, now)
    current = projections.claim_refresh(database, now)
    result = OverviewPanel(
        generated_at=now,
        days=1,
        articles_pending_review=11,
        snapshot=ReadModelSnapshot(
            status="catching_up",
            policy_version=policy,
            generation=f"{policy}:9",
            article_generation=9,
            catalog_generation=3,
            lag_seconds=0,
            catalog_lag_seconds=600,
        ),
    )
    assert projections.publish_report(database, current, result, now)
    with database.begin() as session:
        projections.request_refresh(session, "blockers", 1, now)
    later = projections.claim_refresh(database, now)
    outdated = result.model_copy(deep=True)
    outdated.articles_pending_review = 99
    if regression == "generation":
        outdated.generated_at = now + timedelta(seconds=1)
        outdated.snapshot.article_generation = 8
        outdated.snapshot.generation = f"{policy}:8"
    else:
        outdated.generated_at = now - timedelta(seconds=1)
    assert not projections.publish_report(database, later, outdated, now)
    with database() as session:
        saved = session.get(ReportSnapshot, ("blockers", 1))
        assert saved.generated_at == now
        assert saved.payload["articles_pending_review"] == 11
        assert saved.payload["_projection_snapshot"]["article_generation"] == 9
        demand = session.get(ReportRefresh, ("blockers", 1))
        assert demand is not None and demand.lease_token is None


def test_calculated_dependency_cannot_publish_after_policy_changes(database, monkeypatch):
    now = utcnow()
    policy = pipeline_policy_version()
    with database.begin() as session:
        session.add(
            ReadModelState(
                name="pipeline",
                payload={
                    "serving_policy": policy,
                    "target_policy": policy,
                    "backfill_complete": True,
                    "article_generation": 7,
                },
                updated_at=now,
            )
        )
        projections.request_refresh(session, "blockers", 1, now)
    claim = projections.claim_refresh(database, now)
    result = projections.calculate_report(database, "blockers", 1, now)
    assert result.snapshot.policy_version == policy
    assert result.snapshot.article_generation == 7
    monkeypatch.setattr(projections, "pipeline_policy_version", lambda: "replacement-policy")
    assert not projections.publish_report(database, claim, result, now)
    with database() as session:
        assert session.get(ReportSnapshot, ("blockers", 1)) is None
        demand = session.get(ReportRefresh, ("blockers", 1))
        assert demand is not None and demand.lease_token is None


@pytest.mark.parametrize("policy_cutover", [False, True])
def test_report_publication_checks_live_policy_without_requiring_globally_quiet_generation(
    database, policy_cutover
):
    now = utcnow()
    policy = pipeline_policy_version()
    with database.begin() as session:
        session.add(
            ReadModelState(
                name="pipeline",
                payload={
                    "serving_policy": policy,
                    "target_policy": policy,
                    "backfill_complete": True,
                    "article_generation": 7,
                },
                updated_at=now,
            )
        )
        projections.request_refresh(session, "blockers", 1, now)
    claim = projections.claim_refresh(database, now)
    result = projections.calculate_report(database, "blockers", 1, now)
    assert result.snapshot.article_generation == 7
    with database.begin() as session:
        state = session.get(ReadModelState, "pipeline")
        state.payload = {
            **state.payload,
            "article_generation": 8,
            **(
                {"serving_policy": "replacement-policy", "target_policy": "replacement-policy"}
                if policy_cutover
                else {}
            ),
        }
        if policy_cutover:
            # A newer worker published under its own policy while this process
            # still has the old configuration hash loaded.
            session.add(
                ReportSnapshot(
                    group="blockers",
                    days=1,
                    payload=OverviewPanel(
                        generated_at=now, days=1, articles_pending_review=77
                    ).model_dump(mode="json"),
                    generated_at=now,
                    policy_version="replacement-report-policy",
                )
            )
    assert projections.pipeline_policy_version() == policy
    assert projections.publish_report(database, claim, result, now) is (not policy_cutover)
    with database() as session:
        saved = session.get(ReportSnapshot, ("blockers", 1))
        if policy_cutover:
            assert saved.policy_version == "replacement-report-policy"
            assert saved.payload["articles_pending_review"] == 77
            assert session.get(ReportRefresh, ("blockers", 1)).lease_token is None
        else:
            assert saved.payload["_projection_snapshot"]["article_generation"] == 7
            assert session.get(ReportRefresh, ("blockers", 1)) is None


def test_report_worker_rejects_policy_changed_since_calculation_started(database, monkeypatch):
    now = utcnow()
    old_policy = projections.report_policy_version()
    with database.begin() as session:
        projections.request_refresh(session, "daily", 7, now)
    claim = projections.claim_refresh(database, now)
    result = OverviewPanel(generated_at=now, days=7)
    monkeypatch.setattr(projections, "report_policy_version", lambda: "replacement-report-policy")
    assert not projections.publish_report(database, claim, result, now, policy_version=old_policy)
    with database() as session:
        assert session.get(ReportSnapshot, ("daily", 7)) is None
        demand = session.get(ReportRefresh, ("daily", 7))
        assert demand is not None and demand.lease_token is None


def test_background_publisher_refreshes_known_ranges_without_page_views(database, monkeypatch):
    from devfeed_admin_api import overview_processing

    now = utcnow()
    monkeypatch.setattr(projections, "utcnow", lambda: now)
    monkeypatch.setattr(overview_processing, "refresh_processing_facts", lambda *_: False)
    save_panel(database, days=17, generated_at=now - timedelta(minutes=10))
    loaded = []

    def calculate(factory, group, days, stamp):
        loaded.append((group, days))
        return OverviewPanel(generated_at=stamp, days=days)

    monkeypatch.setattr(projections, "calculate_report", calculate)
    assert projections.refresh_overview_projections(database, limit=100) > 0
    assert ("daily", 17) in loaded
    loaded.clear()
    now += timedelta(seconds=61)
    assert projections.refresh_overview_projections(database, limit=100) > 0
    assert ("daily", 17) in loaded


def test_range_independent_panels_share_one_durable_snapshot(database, admin_client):
    save_panel(database, "attention", 1)
    for days in (1, 17, 90):
        response = admin_client.get(f"/v1/admin/overview/panels/attention?days={days}")
        assert response.status_code == 200 and response.json()["days"] == days
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(ReportRefresh)) == 0


def test_policy_rebuild_serves_last_complete_and_retains_demand(database, admin_client):
    save_panel(database, policy="old-report-policy")
    response = admin_client.get("/v1/admin/overview/panels/readers?days=7")
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == "rebuilding"
    with database() as session:
        assert session.get(ReportRefresh, ("daily", 7)) is not None


def test_missing_pipeline_keeps_saved_blockers_rebuilding_and_records_refresh_demand(
    database, admin_client
):
    save_panel(database, "blockers", 1)
    response = admin_client.get("/v1/admin/overview/panels/blockers?days=30")
    assert response.status_code == 200
    assert response.json()["articles_pending_review"] == 23
    assert response.json()["snapshot"]["status"] == "rebuilding"
    with database() as session:
        assert session.get(ReportRefresh, ("blockers", 1)) is not None


def test_processing_rollups_cover_arbitrary_ranges_and_old_active_work(database):
    now = utcnow()
    identity = uuid.uuid4()
    with database.begin() as session:
        session.add(
            Article(
                id=identity,
                canonical_url="https://example.test/jobs",
                url_hash="a" * 64,
                title="Jobs",
            )
        )
        session.flush()
        session.add_all(
            [
                ArticleAnalysisJob(
                    article_id=identity, status="queued", created_at=now - timedelta(days=120)
                ),
                ArticleAnalysisJob(
                    article_id=identity,
                    status="succeeded",
                    finished_at=now - timedelta(days=16),
                    duration_ms=100,
                ),
                ArticleAnalysisJob(
                    article_id=identity,
                    status="failed",
                    finished_at=now - timedelta(days=2),
                    duration_ms=200,
                ),
            ]
        )
    assert refresh_processing_facts(database, now)
    with database() as session:
        short = next(
            row
            for row in processing_facts(session, now - timedelta(days=6), now)
            if row["kind"] == "analysis"
        )
        long = next(
            row
            for row in processing_facts(session, now - timedelta(days=16), now)
            if row["kind"] == "analysis"
        )
    assert short["completed"] == 0 and short["failed"] == 1
    assert long["completed"] == long["failed"] == 1
    assert short["queued"] == long["queued"] == 1
    assert long["duration_ms"] == 300 and long["duration_runs"] == 2
    assert not refresh_processing_facts(database, now)


@pytest.mark.parametrize("title_padding", ["", "x" * 450])
def test_projected_blockers_count_all_matches_and_preserve_bounded_targets(database, title_padding):
    now = utcnow()
    policy = pipeline_policy_version()
    with database.begin() as session:
        session.add(
            ReadModelState(
                name="pipeline",
                payload={
                    "serving_policy": policy,
                    "target_policy": policy,
                    "backfill_complete": True,
                },
                updated_at=now,
            )
        )
        for index in range(8):
            session.add(
                PipelineProjection(
                    policy_version=policy,
                    kind="article",
                    entity_id=uuid.UUID(int=8 - index),
                    payload={
                        "title": f"Generated display title {index}",
                        "_search": {"title": f"Pending {index}{title_padding}"},
                        "editorial_revision": index,
                        "_automation_blockers": [
                            {
                                "code": "analysis_failed",
                                "label": "Failed article analysis",
                                "action": "analyze",
                            },
                            {"code": "insufficient_text"},
                        ],
                    },
                    stage="analysis",
                    state="failed",
                    created_at=now + timedelta(seconds=index // 2),
                    activity_at=now,
                    evaluated_at=now,
                )
            )
        for decoy_policy, kind in (("obsolete-policy", "article"), (policy, "source")):
            session.add(
                PipelineProjection(
                    policy_version=decoy_policy,
                    kind=kind,
                    entity_id=uuid.UUID(int=8),
                    payload={"title": "Wrong generation or kind", "editorial_revision": 99},
                    stage="analysis",
                    state="complete",
                    created_at=now,
                    activity_at=now,
                    evaluated_at=now,
                )
            )
    with database() as session:
        blockers = projections.projected_blockers(session)
    for code in ("analysis_failed", "insufficient_text"):
        blocked = next(row for row in blockers if row.code == code)
        assert blocked.count == 8 and len(blocked.targets) == 5
        expected = [1, 0, 3, 2, 5]
        assert [row.title for row in blocked.targets] == [
            f"Pending {index}{title_padding}" for index in expected
        ]
        assert [row.revision for row in blocked.targets] == expected


def test_corrupt_saved_document_queues_rebuild_without_calculation(database, admin_client):
    with database.begin() as session:
        session.add(
            ReportSnapshot(
                group="daily",
                days=7,
                payload={"invalid": True},
                generated_at=utcnow(),
                policy_version=projections.report_policy_version(),
            )
        )
    response = admin_client.get("/v1/admin/overview/panels/readers?days=7")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "report_rebuilding"
    with database() as session:
        assert session.get(ReportRefresh, ("daily", 7)) is not None


def test_background_worker_publishes_every_report_group(database, admin_client):
    policy = pipeline_policy_version()
    with database.begin() as session:
        session.add(
            ReadModelState(
                name="pipeline",
                payload={
                    "serving_policy": policy,
                    "target_policy": policy,
                    "backfill_complete": True,
                },
                updated_at=utcnow(),
            )
        )
    published = projections.refresh_overview_projections(database, limit=100)
    assert published > 0
    with database() as session:
        groups = set(session.scalars(select(ReportSnapshot.group)))
        pending = list(session.scalars(select(ReportRefresh)))
    assert groups == projections.REPORT_GROUPS
    assert not pending
    assert admin_client.get("/v1/admin/overview/panels/readers?days=17").status_code == 503
    assert projections.refresh_overview_projections(database, limit=1, seed=False) == 1
    response = admin_client.get("/v1/admin/overview/panels/readers?days=17")
    assert response.status_code == 200
    assert len(response.json()["insights"]["reader_activity"]) == 17


def test_report_budget_cancels_sql_with_a_supplied_factory(database):
    from devfeed_admin_api.reporting import report_budget
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with (
        database() as session,
        pytest.raises(DBAPIError, match="statement timeout"),
        report_budget(session, 0.05),
    ):
        session.execute(text("SELECT pg_sleep(1)"))


@pytest.mark.parametrize("reason", ["lag", "rebuild", "publisher", "progress"])
def test_unready_projection_cannot_publish_fresh_looking_blocker_counts(
    database, admin_client, reason
):
    from devfeed_core.models import ReadModelEvent

    now = utcnow()
    save_panel(database, "blockers", 1, generated_at=now - timedelta(minutes=5))
    with database.begin() as session:
        session.add(
            ReadModelState(
                name="pipeline",
                payload={
                    "serving_policy": pipeline_policy_version()
                    if reason != "rebuild"
                    else "previous-policy",
                    "target_policy": pipeline_policy_version(),
                    "backfill_complete": reason != "rebuild",
                    **(
                        {
                            "catalog_refresh": {
                                "token": str(uuid.uuid4()),
                                "revision": [],
                                "started_at": (now - timedelta(hours=1)).isoformat(),
                                "progress_at": (now - timedelta(seconds=61)).isoformat(),
                            }
                        }
                        if reason == "progress"
                        else {}
                    ),
                },
                updated_at=now - timedelta(seconds=61) if reason == "publisher" else now,
            )
        )
        if reason == "lag":
            session.add(
                ReadModelEvent(
                    kind="article", entity_id=uuid.uuid4(), created_at=now - timedelta(seconds=61)
                )
            )
        projections.request_refresh(session, "blockers", 1, now)
    assert projections.refresh_overview_projections(database, limit=1, seed=False) == 0
    response = admin_client.get("/v1/admin/overview/panels/blockers?days=30")
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == (
        "rebuilding" if reason == "rebuild" else "stale"
    )
    assert response.json()["articles_pending_review"] == 23
    with database() as session:
        assert session.get(ReportRefresh, ("blockers", 1)).last_error == "ProjectionNotReady"
        assert session.get(ReportSnapshot, ("blockers", 1)).generated_at == now - timedelta(
            minutes=5
        )
