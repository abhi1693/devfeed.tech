"""Saved reports remain available while bounded publishers fail or restart."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from devfeed_admin_api import overview_projection as projections
from devfeed_core.models import (
    PipelineProjection,
    ReadModelState,
    ReportRefresh,
    ReportSnapshot,
    utcnow,
)
from devfeed_core.read_models import ReadModelSnapshot, pipeline_policy_version
from sqlalchemy import event, func, select

from tests.support.overview import (
    ready_pipeline,
    save_section,
    section_document,
    window_fields,
    window_for,
)

pytestmark = pytest.mark.integration


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
            response = admin_client.get(
                "/v1/admin/overview/charts/publication-rate?from=now-16d/d&to=now"
            )
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "report_refreshing"
        assert admin_client.get("/health/live").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not any("article_analysis_jobs" in statement for statement in statements)
    assert not any("article_contents" in statement for statement in statements)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportRefresh)) == 1
        assert (
            session.get(ReportRefresh, ("publishing", window_for("publishing", 17, utcnow()).key))
            is not None
        )
        assert session.scalar(select(func.count()).select_from(ReportSnapshot)) == 0


def test_last_complete_survives_redis_outage_and_process_restart(
    database, admin_client, monkeypatch
):
    from devfeed_admin_api.main import create_app
    from devfeed_core import cache
    from devfeed_core.config import get_settings
    from fastapi.testclient import TestClient

    generated = utcnow() - timedelta(days=2)
    save_section(database, generated_at=generated)
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    monkeypatch.setattr(cache, "get_cache", lambda: pytest.fail("report serving contacted Redis"))
    first = admin_client.get("/v1/admin/overview/charts/publication-rate?from=now-6d/d&to=now")
    assert first.status_code == 200
    assert first.json()["data"]["summary"]["published"] == 23
    assert first.json()["snapshot"]["status"] == "refreshing"
    assert first.headers["x-overview-stale"] == "true"
    app = create_app()
    app.dependency_overrides.update(admin_client.app.dependency_overrides)
    with TestClient(app) as restarted:
        second = restarted.get("/v1/admin/overview/charts/publication-rate?from=now-6d/d&to=now")
    assert second.status_code == 200
    assert second.json()["generated_at"] == first.json()["generated_at"]
    assert second.json()["data"]["summary"]["published"] == 23
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportRefresh)) == 1


def test_worker_failure_preserves_snapshot_and_persistent_cooldown(
    database, admin_client, monkeypatch
):
    now = utcnow()
    monkeypatch.setattr(projections, "utcnow", lambda: now)
    save_section(database, generated_at=now - timedelta(minutes=5))
    with database.begin() as session:
        projections.request_refresh(session, "publishing", window_for("publishing", 7, now), now)
    calls = []

    def fail(*args):
        calls.append(1)
        raise ValueError("unavailable dependency")

    monkeypatch.setattr(
        projections, "calculate_report_async", AsyncMock(side_effect=lambda *args: fail(*args[:4]))
    )
    assert projections.refresh_overview_projections(database, limit=1, client=object()) == 0
    for _ in range(8):
        response = admin_client.get(
            "/v1/admin/overview/charts/publication-rate?from=now-6d/d&to=now"
        )
        assert response.status_code == 200
        assert response.json()["snapshot"]["status"] == "stale"
    assert projections.refresh_overview_projections(database, limit=1, client=object()) == 0
    with database() as session:
        queued = session.get(
            ReportRefresh, ("publishing", window_for("publishing", 7, utcnow()).key)
        )
        assert queued.available_at == now + timedelta(seconds=15)
        assert queued.last_error == "ValueError"
        assert (
            session.get(
                ReportSnapshot, ("publishing", window_for("publishing", 7, utcnow()).key)
            ).generated_at
            < now
        )
    assert calls == [1]
    now += timedelta(seconds=16)
    monkeypatch.setattr(
        projections,
        "calculate_report_async",
        AsyncMock(
            side_effect=lambda factory, group, window, stamp, client: section_document(
                generated_at=stamp, window=window
            )
        ),
    )
    assert projections.refresh_overview_projections(database, limit=1, client=object()) == 1
    response = admin_client.get("/v1/admin/overview/charts/publication-rate?from=now-6d/d&to=now")
    assert response.status_code == 200 and response.json()["snapshot"]["status"] == "fresh"
    with database() as session:
        assert (
            session.get(ReportRefresh, ("publishing", window_for("publishing", 7, utcnow()).key))
            is None
        )


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
    save_section(database, "operations", 1, generated_at=now - timedelta(hours=2))
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
        projections.request_refresh(session, "operations", window_for("operations", 1, now), now)
        session.flush()
        session.get(
            ReportRefresh, ("operations", window_for("operations", 1, utcnow()).key)
        ).attempts = 30
    assert projections.refresh_overview_projections(database, limit=1, client=object()) == 0
    with database() as session:
        demand = session.get(
            ReportRefresh, ("operations", window_for("operations", 1, utcnow()).key)
        )
        assert demand.available_at == now + timedelta(seconds=5)
        assert demand.attempts == 0 and demand.last_error == "ProjectionNotReady"
        assert demand.lease_token is None and demand.lease_until is None
    restarted = create_app()
    restarted.dependency_overrides.update(admin_client.app.dependency_overrides)
    with TestClient(restarted) as client:
        response = client.get("/v1/admin/overview/charts/attention?from=now-29d/d&to=now")
        assert response.status_code == 200
        assert response.json()["data"]["reviews"][0]["count"] == 23
        assert response.json()["snapshot"]["status"] == "stale"
    assert projections.refresh_overview_projections(database, limit=1, client=object()) == 0
    now += timedelta(seconds=6)
    with database.begin() as session:
        state = session.get(ReadModelState, "pipeline")
        state.payload = {
            **state.payload,
            "catalog_refresh": {**state.payload["catalog_refresh"], "progress_at": now.isoformat()},
        }
        state.updated_at = now
    assert projections.refresh_overview_projections(database, limit=1, client=object()) == 1
    response = admin_client.get("/v1/admin/overview/charts/attention?from=now-29d/d&to=now")
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == "catching_up"
    with database() as session:
        assert (
            session.get(ReportRefresh, ("operations", window_for("operations", 1, utcnow()).key))
            is None
        )
        assert (
            session.get(
                ReportSnapshot, ("operations", window_for("operations", 1, utcnow()).key)
            ).generated_at
            == now
        )


@pytest.mark.parametrize("operational_lag,status", [(40, "catching_up"), (59, "stale")])
def test_saved_dependency_ages_with_report_without_scanning_projection_rows(
    database, admin_client, monkeypatch, operational_lag, status
):
    now = utcnow()
    monkeypatch.setattr(projections, "utcnow", lambda: now)
    policy = pipeline_policy_version()
    save_section(database, "operations", 1, generated_at=now - timedelta(seconds=10))
    with database.begin() as session:
        saved = session.get(
            ReportSnapshot, ("operations", window_for("operations", 1, utcnow()).key)
        )
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
        response = admin_client.get("/v1/admin/overview/charts/attention?from=now-29d/d&to=now")
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
        projections.request_refresh(session, "publishing", window_for("publishing", 17, now), now)
    old = projections.claim_refresh(database, now)
    assert old is not None
    with database.begin() as session:
        session.get(
            ReportRefresh, ("publishing", window_for("publishing", 17, utcnow()).key)
        ).lease_until = now - timedelta(seconds=1)
    current = projections.claim_refresh(database, now)
    assert current is not None and current[2] != old[2]
    result = section_document(generated_at=now, days=17)
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
        projections.request_refresh(session, "operations", window_for("operations", 1, now), now)
    current = projections.claim_refresh(database, now)
    result = section_document(
        group="operations",
        generated_at=now,
        days=1,
        count=11,
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
        projections.request_refresh(session, "operations", window_for("operations", 1, now), now)
    later = projections.claim_refresh(database, now)
    outdated = result.model_copy(deep=True)
    outdated.data.reviews[0].count = 99
    if regression == "generation":
        outdated.generated_at = now + timedelta(seconds=1)
        outdated.snapshot.article_generation = 8
        outdated.snapshot.generation = f"{policy}:8"
    else:
        outdated.generated_at = now - timedelta(seconds=1)
    assert not projections.publish_report(database, later, outdated, now)
    with database() as session:
        saved = session.get(
            ReportSnapshot, ("operations", window_for("operations", 1, utcnow()).key)
        )
        assert saved.generated_at == now
        assert saved.payload["data"]["reviews"][0]["count"] == 11
        assert saved.payload["_projection_snapshot"]["article_generation"] == 9
        demand = session.get(
            ReportRefresh, ("operations", window_for("operations", 1, utcnow()).key)
        )
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
        projections.request_refresh(session, "operations", window_for("operations", 1, now), now)
    claim = projections.claim_refresh(database, now)
    result = projections.calculate_report(
        database, "operations", window_for("operations", 1, now), now
    )
    assert result.snapshot.policy_version == policy
    assert result.snapshot.article_generation == 7
    monkeypatch.setattr(projections, "pipeline_policy_version", lambda: "replacement-policy")
    assert not projections.publish_report(database, claim, result, now)
    with database() as session:
        assert (
            session.get(ReportSnapshot, ("operations", window_for("operations", 1, utcnow()).key))
            is None
        )
        demand = session.get(
            ReportRefresh, ("operations", window_for("operations", 1, utcnow()).key)
        )
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
        projections.request_refresh(session, "operations", window_for("operations", 1, now), now)
    claim = projections.claim_refresh(database, now)
    result = projections.calculate_report(
        database, "operations", window_for("operations", 1, now), now
    )
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
                    group="operations",
                    **window_fields("operations", 1, now=utcnow()),
                    payload=section_document(
                        group="operations", generated_at=now, days=1, count=77
                    ).model_dump(mode="json"),
                    generated_at=now,
                    policy_version="replacement-report-policy",
                )
            )
    assert projections.pipeline_policy_version() == policy
    assert projections.publish_report(database, claim, result, now) is (not policy_cutover)
    with database() as session:
        saved = session.get(
            ReportSnapshot, ("operations", window_for("operations", 1, utcnow()).key)
        )
        if policy_cutover:
            assert saved.policy_version == "replacement-report-policy"
            assert saved.payload["data"]["reviews"][0]["count"] == 77
            assert (
                session.get(
                    ReportRefresh, ("operations", window_for("operations", 1, utcnow()).key)
                ).lease_token
                is None
            )
        else:
            assert saved.payload["_projection_snapshot"]["article_generation"] == 7
            assert (
                session.get(
                    ReportRefresh, ("operations", window_for("operations", 1, utcnow()).key)
                )
                is None
            )


def test_report_worker_rejects_policy_changed_since_calculation_started(database, monkeypatch):
    now = utcnow()
    old_policy = projections.report_policy_version()
    with database.begin() as session:
        projections.request_refresh(session, "publishing", window_for("publishing", 7, now), now)
    claim = projections.claim_refresh(database, now)
    result = section_document(generated_at=now, days=7)
    monkeypatch.setattr(projections, "report_policy_version", lambda: "replacement-report-policy")
    assert not projections.publish_report(database, claim, result, now, policy_version=old_policy)
    with database() as session:
        assert (
            session.get(ReportSnapshot, ("publishing", window_for("publishing", 7, utcnow()).key))
            is None
        )
        demand = session.get(
            ReportRefresh, ("publishing", window_for("publishing", 7, utcnow()).key)
        )
        assert demand is not None and demand.lease_token is None


def test_range_independent_sections_share_one_durable_snapshot(database, admin_client):
    ready_pipeline(database)
    save_section(database, "operations", 1)
    for days in (1, 17, 90):
        response = admin_client.get(
            f"/v1/admin/overview/charts/attention?from=now-{days - 1}d/d&to=now"
        )
        assert response.status_code == 200 and response.json()["days"] == 1
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(ReportRefresh)) == 0


def test_policy_rebuild_serves_last_complete_and_retains_demand(database, admin_client):
    save_section(database, policy="old-report-policy")
    response = admin_client.get("/v1/admin/overview/charts/publication-rate?from=now-6d/d&to=now")
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == "rebuilding"
    with database() as session:
        assert (
            session.get(ReportRefresh, ("publishing", window_for("publishing", 7, utcnow()).key))
            is not None
        )


def test_missing_pipeline_keeps_saved_blockers_rebuilding_and_records_refresh_demand(
    database, admin_client
):
    save_section(database, "operations", 1)
    response = admin_client.get("/v1/admin/overview/charts/attention?from=now-29d/d&to=now")
    assert response.status_code == 200
    assert response.json()["data"]["reviews"][0]["count"] == 23
    assert response.json()["snapshot"]["status"] == "rebuilding"
    with database() as session:
        assert (
            session.get(ReportRefresh, ("operations", window_for("operations", 1, utcnow()).key))
            is not None
        )


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
                group="publishing",
                **window_fields("publishing", 7, now=utcnow()),
                payload={"invalid": True},
                generated_at=utcnow(),
                policy_version=projections.report_policy_version(),
            )
        )
    response = admin_client.get("/v1/admin/overview/charts/publication-rate?from=now-6d/d&to=now")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "report_rebuilding"
    with database() as session:
        assert (
            session.get(ReportRefresh, ("publishing", window_for("publishing", 7, utcnow()).key))
            is not None
        )


def test_background_worker_publishes_requested_panel_groups(database, admin_client, monkeypatch):
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
    groups_to_request = projections.REPORT_GROUPS
    with database.begin() as session:
        for group in groups_to_request:
            projections.request_refresh(
                session,
                group,
                window_for(group, 1 if group in projections.INDEPENDENT_GROUPS else 7, utcnow()),
                utcnow(),
            )

    async def calculate(factory, group, window, now, client):
        return section_document(group=group, generated_at=now, window=window)

    monkeypatch.setattr(projections, "calculate_report_async", calculate)
    published = projections.refresh_overview_projections(database, limit=100, client=object())
    assert published == 6
    with database() as session:
        groups = set(session.scalars(select(ReportSnapshot.group)))
        pending = list(session.scalars(select(ReportRefresh)))
    assert groups == groups_to_request
    assert not pending
    assert (
        admin_client.get(
            "/v1/admin/overview/charts/publication-rate?from=now-16d/d&to=now"
        ).status_code
        == 503
    )
    assert projections.refresh_overview_projections(database, limit=1, client=object()) == 1
    response = admin_client.get("/v1/admin/overview/charts/publication-rate?from=now-16d/d&to=now")
    assert response.status_code == 200
    assert response.json()["chart"] == "publication-rate"
    assert response.json()["days"] == 17


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
    save_section(database, "operations", 1, generated_at=now - timedelta(minutes=5))
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
        projections.request_refresh(session, "operations", window_for("operations", 1, now), now)
    assert projections.refresh_overview_projections(database, limit=1, client=object()) == 0
    response = admin_client.get("/v1/admin/overview/charts/attention?from=now-29d/d&to=now")
    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == (
        "rebuilding" if reason == "rebuild" else "stale"
    )
    assert response.json()["data"]["reviews"][0]["count"] == 23
    with database() as session:
        assert (
            session.get(
                ReportRefresh, ("operations", window_for("operations", 1, utcnow()).key)
            ).last_error
            == "ProjectionNotReady"
        )
        assert session.get(
            ReportSnapshot, ("operations", window_for("operations", 1, utcnow()).key)
        ).generated_at == now - timedelta(minutes=5)
