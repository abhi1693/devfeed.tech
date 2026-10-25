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
    ReportDemand,
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
HISTORICAL_FRESH_SECONDS = 300
DEMAND_SECONDS = 600
DEMAND_TOUCH_SECONDS = 60
LEASE_SECONDS = 45
POLICY_VERSION = "overview-v5"
DEPENDENCY_RETRY_SECONDS = 5
INDEPENDENT_GROUPS = frozenset(
    {"adoption", "personalization", "attention", "blockers", "throughput"}
)
REPORT_GROUPS = frozenset(GROUPS.get(panel, panel) for panel in get_args(PanelName)) | {"overview"}
OPERATIONAL_GROUPS = frozenset(
    {"attention", "blockers", "throughput", "personalization", "processing"}
)
WARM_REPORT_KEYS = frozenset({("attention", 1), ("throughput", 1)})
FACT_REPORT_GROUPS = frozenset(
    {
        "daily",
        "job-tokens",
        "publication-automation",
        "publication-time",
        "sources",
        "topic-coverage",
        "overview",
    }
)


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
    return f"{POLICY_VERSION}:{pipeline_policy_version()[:64]}"


def report_key(group: str, days: int) -> tuple[str, int]:
    if group not in REPORT_GROUPS or not 1 <= days <= 90:
        raise ValueError("Unknown report group or date range")
    return group, 1 if group in INDEPENDENT_GROUPS else days


def report_fresh_seconds(group):
    return FRESH_SECONDS if group in OPERATIONAL_GROUPS else HISTORICAL_FRESH_SECONDS


def touch_report_demand(session, group, days, now):
    """Persist viewer activity at most once a minute per normalized report key.

    Demand never changes an existing refresh lease or failure cooldown. Its finite
    key space remains bounded even when an old viewer never returns.
    """
    group, days = report_key(group, days)
    statement = insert(ReportDemand).values(
        group=group,
        days=days,
        last_requested_at=now,
        active_until=now + timedelta(seconds=DEMAND_SECONDS),
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[ReportDemand.group, ReportDemand.days],
            set_={
                "last_requested_at": statement.excluded.last_requested_at,
                "active_until": statement.excluded.active_until,
            },
            where=ReportDemand.last_requested_at <= now - timedelta(seconds=DEMAND_TOUCH_SECONDS),
        )
    )


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
    fresh_seconds = report_fresh_seconds(group)
    with factory.begin() as session:
        session.connection().info["report_deadline"] = clock.monotonic() + 2
        session.execute(text("SET LOCAL statement_timeout = '2s'"))
        session.execute(text("SET LOCAL lock_timeout = '500ms'"))
        touch_report_demand(session, key[0], key[1], now)
        pipeline = None
        if group in {"blockers", "overview"}:
            saved = session.execute(
                select(ReportSnapshot, ReadModelState.payload)
                .outerjoin(ReadModelState, ReadModelState.name == "pipeline")
                .where(ReportSnapshot.group == key[0], ReportSnapshot.days == key[1])
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
                result = model.model_validate(stored.payload).model_copy(update={"days": days})
                if stored.payload.get("_projection_snapshot") is not None:
                    dependency = ReadModelSnapshot.model_validate(
                        stored.payload["_projection_snapshot"]
                    )
            except ValueError:
                rebuilding = True
        catalog_refresh = pipeline.get("catalog_refresh") if pipeline else None
        dependency_rebuilding = group in {"blockers", "overview"} and (
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
            or age >= fresh_seconds
            or (
                catalog_refresh is not None and (dependency is None or dependency.status == "fresh")
            )
        ):
            request_refresh(session, key[0], key[1], now)
        refresh = session.get(ReportRefresh, key)
        if result is None or stored is None:
            raise_after_commit = True
        else:
            raise_after_commit = False
            # Preserve the exact observed projection generation. Reading a newer
            # pipeline pointer here must never relabel an older saved document.
            lag = age + (dependency.lag_seconds or 0) if dependency else age
            if (
                rebuilding
                or dependency_rebuilding
                or (dependency is not None and dependency.status == "rebuilding")
            ):
                status = "rebuilding"
            elif (refresh is not None and refresh.last_error is not None) or (
                dependency is not None and (dependency.status == "stale" or lag > fresh_seconds)
            ):
                status = "stale"
            elif age >= fresh_seconds:
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


def seed_refreshes(factory, now):
    """Refresh the small warm set and ranges with an unexpired viewer lease."""
    with factory.begin() as session:
        active = session.execute(
            select(ReportDemand.group, ReportDemand.days)
            .where(
                ReportDemand.group.in_(REPORT_GROUPS),
                ReportDemand.active_until > now,
            )
            .limit(len(REPORT_GROUPS) * 90)
        )
        for group, days in sorted(WARM_REPORT_KEYS | set(active)):
            stored = session.get(ReportSnapshot, (group, days))
            if (
                stored is None
                or stored.policy_version != report_policy_version()
                or stored.generated_at <= now - timedelta(seconds=report_fresh_seconds(group))
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
    from devfeed_core.reporting_facts import ReportingFactsUnavailable, require_reporting_facts

    from devfeed_admin_api.overview import overview_metrics
    from devfeed_admin_api.overview_processing import processing_facts
    from devfeed_admin_api.reporting import report_budget

    with factory() as session, report_budget(session):
        session.connection().info["report_deadline"] = clock.monotonic() + 30
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '10s'"))
        session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '10s'"))
        if group in FACT_REPORT_GROUPS:
            try:
                require_reporting_facts(session)
            except ReportingFactsUnavailable as error:
                raise ProjectionNotReady("Reporting facts are still rebuilding") from error
            session.info["reporting_facts"] = True
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
            return observe_report_facts(session, result)
        panel = panel_data(session, group, days, now)
        if group == "processing":
            assert facts is not None
            panel.generated_at = min(panel.generated_at, facts.updated_at)
        return observe_report_facts(session, panel)


def observe_report_facts(session, result):
    """Carry the actual calculation's dependency observations into publication."""
    from devfeed_core.reporting_facts import reporting_fact_observation

    result.snapshot = session.info.get("report_projection_snapshot")
    if session.info.get("reporting_facts"):
        observation = reporting_fact_observation(session)
        result._reporting_fact_observation = observation
        dependency = result.snapshot or ReadModelSnapshot(
            status="fresh", generation=f"reporting-facts:{observation['generation']}"
        )
        result.snapshot = dependency.model_copy(
            update={"lag_seconds": max(dependency.lag_seconds or 0, observation["lag_seconds"])}
        )
    return result


def _defer_refresh(owned, now):
    owned.available_at = now + timedelta(seconds=DEPENDENCY_RETRY_SECONDS)
    owned.lease_until = None
    owned.lease_token = None
    owned.attempts = 0
    owned.last_error = "ProjectionNotReady"


def publish_report(factory, claim, result, now, *, policy_version=None):
    group, days, token, _attempts = claim
    version = policy_version or report_policy_version()
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
        stored = session.get(ReportSnapshot, (group, days), with_for_update=True)
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
        if result._reporting_fact_observation is not None:
            payload["_reporting_fact_observation"] = result._reporting_fact_observation
        statement = insert(ReportSnapshot).values(
            group=group,
            days=days,
            payload=payload,
            generated_at=result.generated_at,
            policy_version=version,
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


def refresh_overview_projections(factory=None, *, limit=4, now=None, seed=True, fail_fast=False):
    """Publish at most ``limit`` reports; failure never removes the last complete one."""
    from devfeed_core.reporting_facts import ReportingFactsUnavailable, refresh_reporting_facts

    from devfeed_admin_api.overview_processing import refresh_processing_facts
    from devfeed_admin_api.reporting import reporting_sessions

    if not 1 <= limit <= 100:
        raise ValueError("Report refresh limit must be between 1 and 100")
    factory = factory or reporting_sessions()
    now = now or utcnow()
    try:
        refresh_reporting_facts(factory, limit=100)
    except Exception as error:
        logger.warning(
            "overview_reporting_facts_refresh_failed", extra={"error_type": type(error).__name__}
        )
        if fail_fast:
            raise
    try:
        refresh_processing_facts(factory, now)
    except Exception as error:
        # Other panels remain independently publishable while one rollup fails.
        logger.warning(
            "overview_processing_refresh_failed", extra={"error_type": type(error).__name__}
        )
        if fail_fast:
            raise
    if seed:
        seed_refreshes(factory, now)
    published = 0
    for _ in range(limit):
        claim_now = max(now, utcnow())
        claim = claim_refresh(factory, claim_now)
        if claim is None:
            break
        try:
            version = report_policy_version()
            result = calculate_report(factory, claim[0], claim[1], claim_now)
            published += publish_report(factory, claim, result, utcnow(), policy_version=version)
        except Exception as error:
            if isinstance(error, ReportingFactsUnavailable):
                # A reader can cross its source-lag threshold during the same
                # repeatable-read calculation. This remains dependency catch-up,
                # with the same cooldown as an initial readiness failure.
                error = ProjectionNotReady("Reporting facts are catching up")
            fail_refresh(factory, claim, utcnow(), error)
            logger.warning(
                "overview_projection_failed",
                extra={"group": claim[0], "days": claim[1], "error_type": type(error).__name__},
            )
            if fail_fast and not isinstance(error, ProjectionNotReady):
                raise
    return published
