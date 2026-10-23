"""Durable report serving and bounded background publication.

HTTP readers only load the last complete document and record coalesced demand.
Neither Redis availability nor browser retries can start report calculations.
"""

import logging
import time as clock
import uuid
from datetime import datetime, timedelta
from typing import get_args

from devfeed_core.models import (
    PipelineProjection,
    ReadModelState,
    ReportRefresh,
    ReportSnapshot,
    utcnow,
)
from devfeed_core.read_models import ReadModelSnapshot, pipeline_policy_version
from fastapi import HTTPException
from sqlalchemy import cast, func, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import JSONB, insert

from devfeed_admin_api.overview_panel_data import GROUPS, PanelName, panel_data

logger = logging.getLogger(__name__)
FRESH_SECONDS = 60
LEASE_SECONDS = 45
POLICY_VERSION = "overview-v3"
INDEPENDENT_GROUPS = frozenset(
    {"adoption", "personalization", "attention", "blockers", "throughput"}
)
REPORT_GROUPS = frozenset(GROUPS.get(panel, panel) for panel in get_args(PanelName)) | {"overview"}


class ProjectionNotReady(RuntimeError):
    pass


def projected_blockers(session):
    """Aggregate compact exact classifications; never revisit article bodies."""
    from devfeed_admin_api.automation import AutomationBlocker, RecoveryTarget, automation_blockers
    from devfeed_admin_api.pipeline_reads import serving_projection

    try:
        serving, dependency = serving_projection(session, kind="article")
    except HTTPException as error:
        raise ProjectionNotReady("Article pipeline projection is still rebuilding") from error
    if serving != pipeline_policy_version():
        raise ProjectionNotReady("Article pipeline projection is still rebuilding")
    if dependency.lag_seconds is not None and dependency.lag_seconds > FRESH_SECONDS:
        raise ProjectionNotReady("Article pipeline projection is catching up")
    session.info["report_projection_snapshot"] = dependency
    blockers = automation_blockers(session, article_ids=[])
    indexed = {blocker.code: blocker for blocker in blockers}
    elements = (
        func.jsonb_array_elements(PipelineProjection.payload["_automation_blockers"])
        .table_valued("value")
        .lateral("blocker")
    )
    value = cast(elements.c.value, JSONB)
    code = value["code"].astext
    matches = (
        select(
            PipelineProjection.entity_id.label("id"),
            func.coalesce(
                PipelineProjection.payload["_search"]["title"].astext,
                PipelineProjection.payload["title"].astext,
            ).label("title"),
            PipelineProjection.payload["editorial_revision"].as_integer().label("revision"),
            PipelineProjection.created_at,
            code.label("code"),
        )
        .join(elements, true())
        .where(PipelineProjection.kind == "article", PipelineProjection.policy_version == serving)
        .subquery()
    )
    ranked = select(
        matches,
        func.count().over(partition_by=matches.c.code).label("total"),
        func.row_number()
        .over(partition_by=matches.c.code, order_by=(matches.c.created_at, matches.c.id))
        .label("position"),
    ).subquery()
    for row in session.execute(
        select(ranked).where(ranked.c.position <= 5).order_by(ranked.c.code, ranked.c.position)
    ):
        blocker = indexed.get(row.code)
        if blocker is None:
            raise ProjectionNotReady("Stored blocker policy is incompatible")
        blocker.count = row.total
        blocker.targets.append(
            RecoveryTarget(id=row.id, title=row.title, kind="article", revision=row.revision or 0)
        )
    return [AutomationBlocker.model_validate(blocker) for blocker in blockers]


def report_policy_version():
    return f"{POLICY_VERSION}:{pipeline_policy_version()[:64]}"


def report_key(group: str, days: int) -> tuple[str, int]:
    if group not in REPORT_GROUPS or not 1 <= days <= 90:
        raise ValueError("Unknown report group or date range")
    return group, 1 if group in INDEPENDENT_GROUPS else days


def request_refresh(session, group, days, now):
    """The finite group/range key space bounds demand even under repeated GETs.

    Conflict handling deliberately leaves failure cooldowns and active leases
    untouched. A process restart cannot turn polling into a refresh storm.
    """
    group, days = report_key(group, days)
    session.execute(
        insert(ReportRefresh)
        .values(group=group, days=days, requested_at=now, available_at=now, attempts=0)
        .on_conflict_do_nothing(index_elements=[ReportRefresh.group, ReportRefresh.days])
    )


def read_report(factory, group, days, model, *, now=None):
    now = now or utcnow()
    key = report_key(group, days)
    version = report_policy_version()
    with factory.begin() as session:
        session.connection().info["report_deadline"] = clock.monotonic() + 2
        session.execute(text("SET LOCAL statement_timeout = '2s'"))
        session.execute(text("SET LOCAL lock_timeout = '500ms'"))
        catalog_refresh = None
        if group in {"blockers", "overview"}:
            saved = session.execute(
                select(ReportSnapshot, ReadModelState.payload["catalog_refresh"])
                .outerjoin(ReadModelState, ReadModelState.name == "pipeline")
                .where(ReportSnapshot.group == key[0], ReportSnapshot.days == key[1])
            ).one_or_none()
            stored, catalog_refresh = saved if saved is not None else (None, None)
        else:
            stored = session.get(ReportSnapshot, key)
        age = max(0, (now - stored.generated_at).total_seconds()) if stored else float("inf")
        rebuilding = stored is not None and stored.policy_version != version
        dependency = None
        result = None
        if stored is not None:
            try:
                result = model.model_validate(stored.payload).model_copy(update={"days": days})
                if stored.payload.get("_projection_snapshot") is not None:
                    dependency = ReadModelSnapshot.model_validate(
                        stored.payload["_projection_snapshot"]
                    )
            except ValueError:
                rebuilding = True
        catalog_started = None
        if result is not None and catalog_refresh:
            catalog_started = datetime.fromisoformat(catalog_refresh["started_at"])
        if (
            stored is None
            or rebuilding
            or age >= FRESH_SECONDS
            or (
                catalog_started is not None and (dependency is None or dependency.status == "fresh")
            )
        ):
            request_refresh(session, key[0], key[1], now)
        refresh = session.get(ReportRefresh, key)
        if result is None or stored is None:
            raise_after_commit = True
        else:
            raise_after_commit = False
            result.snapshot = ReadModelSnapshot(
                status="rebuilding"
                if rebuilding or (dependency is not None and dependency.status == "rebuilding")
                else dependency.status
                if dependency is not None and dependency.status != "fresh"
                else "fresh"
                if age < FRESH_SECONDS
                else "refreshing"
                if refresh is not None and refresh.last_error is None
                else "stale",
                generated_at=stored.generated_at,
                generation=stored.policy_version,
                lag_seconds=age + (dependency.lag_seconds or 0) if dependency is not None else age,
            )
            if catalog_started is not None:
                if result.snapshot.status == "fresh":
                    result.snapshot.status = "refreshing"
                result.snapshot.lag_seconds = max(
                    result.snapshot.lag_seconds or 0,
                    max(0.0, (now - catalog_started).total_seconds()),
                )
    # Commit demand before returning a cold response; HTTP exceptions roll back
    # ordinary request-scoped sessions and would otherwise lose that demand.
    if raise_after_commit:
        raise HTTPException(
            503,
            detail={
                "code": "report_rebuilding" if rebuilding else "report_refreshing",
                "message": "This saved report is being prepared. Try again shortly.",
            },
            headers={"Retry-After": "5"},
        )
    return result


def seed_refreshes(factory, now):
    """Continuously refresh known ranges, including after the last viewer leaves."""
    with factory.begin() as session:
        for group in sorted(REPORT_GROUPS):
            for days in (1,) if group in INDEPENDENT_GROUPS else (7, 30):
                stored = session.get(ReportSnapshot, (group, days))
                if (
                    stored is None
                    or stored.policy_version != report_policy_version()
                    or stored.generated_at <= now - timedelta(seconds=FRESH_SECONDS)
                ):
                    request_refresh(session, group, days, now)
        for group, days in session.execute(
            select(ReportSnapshot.group, ReportSnapshot.days)
            .where(
                ReportSnapshot.group.in_(REPORT_GROUPS),
                or_(
                    ReportSnapshot.generated_at <= now - timedelta(seconds=FRESH_SECONDS),
                    ReportSnapshot.policy_version != report_policy_version(),
                ),
            )
            .limit(len(REPORT_GROUPS) * 90)
        ):
            request_refresh(session, group, days, now)


def claim_refresh(factory, now):
    with factory.begin() as session:
        refresh = session.scalar(
            select(ReportRefresh)
            .where(
                ReportRefresh.group.in_(REPORT_GROUPS),
                ReportRefresh.available_at <= now,
                or_(ReportRefresh.lease_until.is_(None), ReportRefresh.lease_until <= now),
            )
            .order_by(ReportRefresh.available_at, ReportRefresh.requested_at, ReportRefresh.group)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if refresh is None:
            return None
        refresh.lease_token = uuid.uuid4()
        refresh.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        refresh.attempts += 1
        return refresh.group, refresh.days, refresh.lease_token, refresh.attempts


def calculate_report(factory, group, days, now):
    """Worker-only entry point. Each calculation owns a short-lived connection."""
    from devfeed_admin_api.overview import overview_metrics
    from devfeed_admin_api.overview_processing import processing_facts
    from devfeed_admin_api.reporting import report_budget

    with factory() as session, report_budget(session):
        session.connection().info["report_deadline"] = clock.monotonic() + 30
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '10s'"))
        session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '10s'"))
        if group in {"processing", "overview"}:
            from datetime import datetime, time

            from devfeed_admin_api.overview_processing import STATE_NAME

            start = datetime.combine(
                now.date() - timedelta(days=days - 1), time.min, tzinfo=now.tzinfo
            )
            facts = session.get(ReadModelState, STATE_NAME)
            if (
                facts is None
                or facts.updated_at < now - timedelta(seconds=FRESH_SECONDS)
                or processing_facts(session, start, now) is None
            ):
                raise ProjectionNotReady("Processing rollups are still rebuilding")
        if group == "overview":
            result = overview_metrics(session, days, projected=True)
            assert facts is not None
            result.generated_at = min(result.generated_at, facts.updated_at)
            result.snapshot = session.info.get("report_projection_snapshot")
            return result
        panel = panel_data(session, group, days, now)
        if group == "processing":
            assert facts is not None
            panel.generated_at = min(panel.generated_at, facts.updated_at)
        panel.snapshot = session.info.get("report_projection_snapshot")
        return panel


def publish_report(factory, claim, result, now):
    group, days, token, _attempts = claim
    with factory.begin() as session:
        owned = session.scalar(
            select(ReportRefresh)
            .where(
                ReportRefresh.group == group,
                ReportRefresh.days == days,
                ReportRefresh.lease_token == token,
                ReportRefresh.lease_until > now,
            )
            .with_for_update()
        )
        if owned is None:
            return False
        payload = result.model_dump(mode="json", exclude={"snapshot"})
        if result.snapshot is not None:
            payload["_projection_snapshot"] = result.snapshot.model_dump(mode="json")
        statement = insert(ReportSnapshot).values(
            group=group,
            days=days,
            payload=payload,
            generated_at=result.generated_at,
            policy_version=report_policy_version(),
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[ReportSnapshot.group, ReportSnapshot.days],
                set_={
                    "payload": statement.excluded.payload,
                    "generated_at": statement.excluded.generated_at,
                    "policy_version": statement.excluded.policy_version,
                },
            )
        )
        session.delete(owned)
        return True


def fail_refresh(factory, claim, now, error):
    group, days, token, attempts = claim
    with factory.begin() as session:
        session.execute(
            update(ReportRefresh)
            .where(
                ReportRefresh.group == group,
                ReportRefresh.days == days,
                ReportRefresh.lease_token == token,
            )
            .values(
                available_at=now + timedelta(seconds=min(900, 15 * 2 ** min(attempts - 1, 6))),
                lease_until=None,
                lease_token=None,
                last_error=type(error).__name__[:100],
            )
        )


def refresh_overview_projections(factory=None, *, limit=4, now=None, seed=True):
    """Publish at most ``limit`` reports; failure never removes the last complete one."""
    from devfeed_admin_api.overview_processing import refresh_processing_facts
    from devfeed_admin_api.reporting import reporting_sessions

    if not 1 <= limit <= 100:
        raise ValueError("Report refresh limit must be between 1 and 100")
    factory = factory or reporting_sessions()
    now = now or utcnow()
    try:
        refresh_processing_facts(factory, now)
    except Exception as error:
        # Other panels remain independently publishable while one rollup fails.
        logger.warning(
            "overview_processing_refresh_failed", extra={"error_type": type(error).__name__}
        )
    if seed:
        seed_refreshes(factory, now)
    published = 0
    for _ in range(limit):
        claim_now = max(now, utcnow())
        claim = claim_refresh(factory, claim_now)
        if claim is None:
            break
        try:
            result = calculate_report(factory, claim[0], claim[1], claim_now)
            published += publish_report(factory, claim, result, utcnow())
        except Exception as error:
            fail_refresh(factory, claim, utcnow(), error)
            logger.warning(
                "overview_projection_failed",
                extra={"group": claim[0], "days": claim[1], "error_type": type(error).__name__},
            )
    return published
