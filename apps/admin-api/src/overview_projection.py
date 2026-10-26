"""Durable report serving and bounded background publication.

HTTP readers only load the last complete document and record coalesced demand.
Neither Redis availability nor browser retries can start report calculations.
"""

import asyncio
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

from devfeed_admin_api.overview_operations import operations_report
from devfeed_admin_api.overview_sections import SectionName
from devfeed_admin_api.overview_windows import ReportWindow, current_window

logger = logging.getLogger(__name__)
FRESH_SECONDS = 60
LEASE_SECONDS = 45
POLICY_VERSION = "overview-v6"
DEPENDENCY_RETRY_SECONDS = 5
INDEPENDENT_GROUPS = frozenset({"operations"})
REPORT_GROUPS = frozenset(get_args(SectionName))


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
    if serving != pipeline_policy_version() or dependency.status == "rebuilding":
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
    # Rank only compact keys. Carrying titles or the projection JSON through
    # both window sorts spills wide histories to disk before LIMIT can help.
    targets = (
        select(ranked)
        .where(ranked.c.position <= 5)
        .cte("blocker_targets")
        .prefix_with("MATERIALIZED")
    )
    card = (
        select(
            func.coalesce(
                PipelineProjection.payload["_search"]["title"].astext,
                PipelineProjection.payload["title"].astext,
            ).label("title"),
            PipelineProjection.payload["editorial_revision"].as_integer().label("revision"),
        )
        .where(
            PipelineProjection.policy_version == serving,
            PipelineProjection.kind == "article",
            PipelineProjection.entity_id == targets.c.id,
        )
        # A single-row PK probe also prevents a misestimated window cardinality
        # from hashing every wide card just to resolve a handful of targets.
        .limit(1)
        .correlate(targets)
        .lateral("target_card")
    )
    cards = (
        select(
            targets.c.id,
            card.c.title,
            card.c.revision,
            targets.c.code,
            targets.c.total,
            targets.c.position,
        )
        .select_from(targets)
        .join(card, true())
        .order_by(targets.c.code, targets.c.position)
    )
    for row in session.execute(cards):
        blocker = indexed.get(row.code)
        if blocker is None:
            raise ProjectionNotReady("Stored blocker policy is incompatible")
        blocker.count = row.total
        blocker.targets.append(
            RecoveryTarget(id=row.id, title=row.title, kind="article", revision=row.revision or 0)
        )
    return [AutomationBlocker.model_validate(blocker) for blocker in blockers]


def report_policy_version():
    return f"{POLICY_VERSION}:clickhouse-v2:{pipeline_policy_version()[:64]}"


def report_key(group: str, window: ReportWindow) -> tuple[str, str]:
    if group not in REPORT_GROUPS:
        raise ValueError("Unknown report group")
    if group not in INDEPENDENT_GROUPS and window.start is None:
        raise ValueError("Analytical reports require explicit dates")
    return group, "current" if group in INDEPENDENT_GROUPS else window.key


def request_refresh(session, group, window, now):
    """Coalesce an exact calendar window without disturbing leases or cooldowns."""
    group, key = report_key(group, window)
    if group in INDEPENDENT_GROUPS:
        window = current_window()
    statement = insert(ReportRefresh).values(
        group=group,
        window_key=key,
        window_start=window.start,
        window_end=window.end,
        days=window.days,
        requested_at=now,
        last_requested_at=now,
        available_at=now,
        attempts=0,
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[ReportRefresh.group, ReportRefresh.window_key],
            set_={"last_requested_at": now},
            where=ReportRefresh.last_requested_at <= now - timedelta(hours=1),
        )
    )


def read_report(factory, group, window, model, *, now=None):
    now = now or utcnow()
    key = report_key(group, window)
    if group in INDEPENDENT_GROUPS:
        window = current_window()
    version = report_policy_version()
    with factory.begin() as session:
        session.connection().info["report_deadline"] = clock.monotonic() + 2
        session.execute(text("SET LOCAL statement_timeout = '2s'"))
        session.execute(text("SET LOCAL lock_timeout = '500ms'"))
        pipeline = None
        if group in {"operations"}:
            saved = session.execute(
                select(ReportSnapshot, ReadModelState.payload)
                .outerjoin(ReadModelState, ReadModelState.name == "pipeline")
                .where(ReportSnapshot.group == key[0], ReportSnapshot.window_key == key[1])
            ).one_or_none()
            stored, pipeline = saved if saved is not None else (None, None)
        else:
            stored = session.get(ReportSnapshot, key)
        age = max(0, (now - stored.generated_at).total_seconds()) if stored else float("inf")
        rebuilding = stored is not None and stored.policy_version != version
        dependency = None
        result = None
        if stored is not None:
            try:
                result = model.model_validate(stored.payload).model_copy(
                    update={"days": window.days, "start": window.start, "end": window.inclusive_end}
                )
                if stored.last_requested_at <= now - timedelta(hours=1):
                    stored.last_requested_at = now
                if stored.payload.get("_projection_snapshot") is not None:
                    dependency = ReadModelSnapshot.model_validate(
                        stored.payload["_projection_snapshot"]
                    )
            except ValueError:
                rebuilding = True
        # Freshness includes the source age captured when this report was built.
        # Waiting for document age alone leaves an already-expired source
        # observation without refresh demand until the next full report interval.
        lag = age + (dependency.lag_seconds or 0) if dependency else age
        catalog_refresh = pipeline.get("catalog_refresh") if pipeline else None
        dependency_rebuilding = group in {"operations"} and (
            pipeline is None
            or (
                pipeline.get("serving_policy") != pipeline_policy_version()
                or pipeline.get("target_policy") != pipeline.get("serving_policy")
                or pipeline.get("backfill_complete") is False
            )
        )
        if (
            stored is None
            or rebuilding
            or dependency_rebuilding
            or lag >= FRESH_SECONDS
            or (
                catalog_refresh is not None and (dependency is None or dependency.status == "fresh")
            )
        ):
            request_refresh(session, key[0], window, now)
        refresh = (
            session.get(ReportRefresh, key) if result is not None and stored is not None else None
        )
        if result is None or stored is None:
            raise_after_commit = True
        else:
            raise_after_commit = False
            # Preserve the exact observed projection generation. Reading a newer
            # pipeline pointer here must never relabel an older saved document.
            if (
                rebuilding
                or dependency_rebuilding
                or (dependency is not None and dependency.status == "rebuilding")
            ):
                status = "rebuilding"
            elif (refresh is not None and refresh.last_error is not None) or (
                dependency is not None and (dependency.status == "stale" or lag > FRESH_SECONDS)
            ):
                status = "stale"
            elif lag >= FRESH_SECONDS:
                status = "refreshing" if refresh is not None else "stale"
            elif catalog_refresh is not None:
                status = "catching_up"
            else:
                status = dependency.status if dependency is not None else "fresh"
            result.snapshot = (dependency or ReadModelSnapshot(status="fresh")).model_copy(
                update={
                    "status": status,
                    "generated_at": stored.generated_at,
                    "generation": (dependency.generation if dependency else None)
                    or stored.policy_version,
                    "lag_seconds": lag,
                }
            )
            if dependency is not None and dependency.catalog_lag_seconds is not None:
                result.snapshot.catalog_lag_seconds = age + dependency.catalog_lag_seconds
            if catalog_refresh is not None:
                result.snapshot.catalog_lag_seconds = max(
                    result.snapshot.catalog_lag_seconds or 0,
                    max(
                        0.0,
                        (
                            now - datetime.fromisoformat(catalog_refresh["started_at"])
                        ).total_seconds(),
                    ),
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
        return (
            refresh.group,
            ReportWindow(refresh.window_start, refresh.window_end),
            refresh.lease_token,
            refresh.attempts,
        )


def calculate_report(factory, group, window, now):
    """Calculate current review and recovery controls in a bounded transaction."""
    from devfeed_admin_api.reporting import report_budget

    if group != "operations":
        raise ValueError("Historical reports require ClickHouse")
    with factory() as session, report_budget(session):
        session.connection().info["report_deadline"] = clock.monotonic() + 30
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '10s'"))
        session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '10s'"))
        return operations_report(session, 1, now)


async def calculate_report_async(factory, group, window, now, client):
    """Historical reports read compact ClickHouse rollups outside PG transactions."""
    from devfeed_admin_api.analytics_reports import ANALYTICAL_GROUPS, Reports

    if group == "operations":
        return await asyncio.to_thread(calculate_report, factory, group, window, now)
    if group not in ANALYTICAL_GROUPS:
        raise ValueError("Unknown analytical section")
    async with asyncio.timeout(30):
        status = await client.status()
        if not status.get("ready"):
            raise ProjectionNotReady("ClickHouse analytical history is still backfilling")
        capture_started_at = datetime.fromisoformat(status["capture_started_at"])
        observed_at = datetime.fromisoformat(status["updated_at"])
        reports = Reports(client, window, now, capture_started_at=capture_started_at)
        result = await reports.section(group)
    lag = max(0.0, (now - observed_at).total_seconds(), float(status.get("lag_seconds", 0)))
    result.snapshot = ReadModelSnapshot(
        status="stale" if lag > FRESH_SECONDS else "fresh",
        generation=f"analytics:clickhouse:{observed_at.isoformat()}",
        generated_at=observed_at,
        lag_seconds=lag,
    )
    return result


def _defer_refresh(owned, now):
    owned.available_at = now + timedelta(seconds=DEPENDENCY_RETRY_SECONDS)
    owned.lease_until = None
    owned.lease_token = None
    owned.attempts = 0
    owned.last_error = "ProjectionNotReady"


def publish_report(factory, claim, result, now, *, policy_version=None):
    group, window, token, _attempts = claim
    key = report_key(group, window)[1]
    version = policy_version or report_policy_version()
    with factory.begin() as session:
        owned = session.scalar(
            select(ReportRefresh)
            .where(
                ReportRefresh.group == group,
                ReportRefresh.window_key == key,
                ReportRefresh.lease_token == token,
                ReportRefresh.lease_until > now,
            )
            .with_for_update()
        )
        if owned is None:
            return False
        dependency = result.snapshot
        if version != report_policy_version() or (
            dependency is not None
            and dependency.policy_version is not None
            and dependency.policy_version != pipeline_policy_version()
        ):
            _defer_refresh(owned, now)
            return False
        if dependency is not None and dependency.policy_version is not None:
            # Fence mixed-configuration publishers against the database pointer,
            # even while an old process still considers its local policy current.
            # Hold this row only for the short saved-document write, never during
            # calculation. A moving article generation is deliberately allowed.
            pipeline = session.get(ReadModelState, "pipeline", with_for_update=True)
            if pipeline is None or (
                pipeline.payload.get("serving_policy") != dependency.policy_version
                or pipeline.payload.get("target_policy") != dependency.policy_version
                or pipeline.payload.get("backfill_complete") is False
            ):
                _defer_refresh(owned, now)
                return False
        stored = session.get(ReportSnapshot, (group, key), with_for_update=True)
        if stored is not None and stored.policy_version == version:
            previous = stored.payload.get("_projection_snapshot") or {}
            previous_generation = previous.get("article_generation")
            if result.generated_at < stored.generated_at or (
                dependency is not None
                and dependency.policy_version == previous.get("policy_version")
                and dependency.article_generation is not None
                and previous_generation is not None
                and dependency.article_generation < previous_generation
            ):
                _defer_refresh(owned, now)
                return False
        payload = result.model_dump(mode="json", exclude={"snapshot"})
        if result.snapshot is not None:
            payload["_projection_snapshot"] = result.snapshot.model_dump(mode="json")
        statement = insert(ReportSnapshot).values(
            group=group,
            window_key=key,
            window_start=window.start,
            window_end=window.end,
            days=window.days,
            last_requested_at=owned.last_requested_at,
            payload=payload,
            generated_at=result.generated_at,
            policy_version=version,
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[ReportSnapshot.group, ReportSnapshot.window_key],
                set_={
                    "payload": statement.excluded.payload,
                    "generated_at": statement.excluded.generated_at,
                    "policy_version": statement.excluded.policy_version,
                    "last_requested_at": func.greatest(
                        ReportSnapshot.last_requested_at, statement.excluded.last_requested_at
                    ),
                },
            )
        )
        session.delete(owned)
        return True


def fail_refresh(factory, claim, now, error):
    group, window, token, attempts = claim
    key = report_key(group, window)[1]
    with factory.begin() as session:
        session.execute(
            update(ReportRefresh)
            .where(
                ReportRefresh.group == group,
                ReportRefresh.window_key == key,
                ReportRefresh.lease_token == token,
            )
            .values(
                available_at=now
                + timedelta(
                    seconds=DEPENDENCY_RETRY_SECONDS
                    if isinstance(error, ProjectionNotReady)
                    else min(900, 15 * 2 ** min(attempts - 1, 6))
                ),
                lease_until=None,
                lease_token=None,
                attempts=0 if isinstance(error, ProjectionNotReady) else attempts,
                last_error=type(error).__name__[:100],
            )
        )


def refresh_overview_projections(factory=None, *, limit=4, now=None, client=None):
    """Run the required asynchronous ClickHouse publisher from an operator command."""
    if not 1 <= limit <= 100:
        raise ValueError("Report refresh limit must be between 1 and 100")
    return asyncio.run(
        refresh_overview_projections_async(factory, limit=limit, now=now, client=client)
    )


async def refresh_overview_projections_async(factory=None, *, limit=4, now=None, client=None):
    """Publish coalesced demand only; idle cycles perform no historical queries."""
    from devfeed_core.analytics import AnalyticsClient

    from devfeed_admin_api.reporting import reporting_sessions

    if not 1 <= limit <= 100:
        raise ValueError("Report refresh limit must be between 1 and 100")
    factory = factory or reporting_sessions()
    now = now or utcnow()
    if client is None:
        async with AnalyticsClient() as owned:
            return await refresh_overview_projections_async(
                factory, limit=limit, now=now, client=owned
            )
    published = 0
    for _ in range(limit):
        claim = await asyncio.to_thread(claim_refresh, factory, max(now, utcnow()))
        if claim is None:
            break
        try:
            version = report_policy_version()
            result = await calculate_report_async(factory, claim[0], claim[1], utcnow(), client)
            published += await asyncio.to_thread(
                publish_report, factory, claim, result, utcnow(), policy_version=version
            )
        except Exception as error:
            await asyncio.to_thread(fail_refresh, factory, claim, utcnow(), error)
            logger.warning(
                "overview_projection_failed",
                extra={
                    "group": claim[0],
                    "window": claim[1].key,
                    "error_type": type(error).__name__,
                },
            )
    return published
