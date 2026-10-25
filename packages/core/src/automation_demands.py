"""Transactional dependency demand and bounded recovery for article automation.

Jobs remain execution authority. These cursors only wake the existing scheduler;
they never decide publication, skip current evidence, or create replacement jobs.
"""

import logging
import time
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

from sqlalchemy import case, delete, func, literal_column, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm.attributes import flag_modified

from devfeed_core.config import get_settings
from devfeed_core.models import (
    ARTICLE_AUTOMATION_LANE_SQL,
    Article,
    ArticleOrigin,
    ArticleTag,
    AutomationInvalidation,
    utcnow,
)
from devfeed_core.read_models import get_state, pipeline_policy_version, set_state
from devfeed_core.telemetry import current

LOCK = 0x444641  # DFA: independent of source/topic/publication locks.
POLICY_STATE = "article-automation-demand"
BACKLOG_SAMPLE_SECONDS = 30
_backlog_sample_at = None
logger = logging.getLogger(__name__)
MAINTENANCE_WAIT_REASON = "maintenance"
MAINTENANCE_WAIT_REASONS = (
    MAINTENANCE_WAIT_REASON,
    "dependencies",
    "source_admission",
    "content_date_deferred",
)
GLOBAL_DEMAND_KEYS = ("all", "catalog:topics", "catalog:tags", "proposals")
DIRECT_DEMAND_PREFIX = "article:"


def maintenance_lane():
    """Literal index expression keeps prepared and custom plans on the same lane index."""
    return literal_column(ARTICLE_AUTOMATION_LANE_SQL)


def policy_version(settings):
    from devfeed_core.analysis import VALIDATION_VERSION, snapshot_hash
    from devfeed_core.article_jobs import EXTRACTION_VERSION
    from devfeed_core.publication_policy import POLICY_VERSION as PUBLICATION_POLICY

    return snapshot_hash(
        {
            "version": "automation-demand-v1",
            "lifecycle": pipeline_policy_version(settings),
            "publication": PUBLICATION_POLICY,
            "extraction": EXTRACTION_VERSION,
            "validation": VALIDATION_VERSION,
            "dependency_wakes": settings.automation_dependency_wakes_enabled,
            "reconcile_seconds": settings.automation_reconcile_seconds,
            "article_topic_proposals": settings.article_topic_proposals_enabled,
        }
    )


def _enqueue_policy(factory, settings):
    policy = policy_version(settings)
    with factory.begin() as session:
        if not session.scalar(select(func.pg_try_advisory_xact_lock(LOCK))):
            return
        state = get_state(session, POLICY_STATE)
        if state is not None and state.payload.get("policy") == policy:
            return
        statement = insert(AutomationInvalidation).values(key="all")
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[AutomationInvalidation.key],
                set_={"revision": AutomationInvalidation.revision + 1},
            )
        )
        set_state(session, POLICY_STATE, {"policy": policy})


def _expand_automation_demands(factory, now):
    """Wake one keyset page; new revisions never rewind an in-progress cursor.

    Queue rows are not locked before articles: an article writer can enqueue a
    catalog event in its transaction. NOWAIT avoids waiting behind ingestion's
    differently ordered multi-article locks. A conflict rolls back the whole page.
    """
    settings = get_settings()
    # A gate-off transition also wakes saved sleepers before legacy polling takes
    # over. Separate transactions keep queue locks out of article acquisition.
    _enqueue_policy(factory, settings)
    try:
        with factory.begin() as session:
            if not session.scalar(select(func.pg_try_advisory_xact_lock(LOCK))):
                return 0
            demand = session.scalar(
                select(AutomationInvalidation)
                .order_by(
                    case(
                        (
                            or_(
                                AutomationInvalidation.key.startswith(DIRECT_DEMAND_PREFIX),
                                AutomationInvalidation.source_id.is_not(None),
                                AutomationInvalidation.tag_id.is_not(None),
                            ),
                            0,
                        ),
                        else_=1,
                    ),
                    func.coalesce(
                        AutomationInvalidation.progressed_at, AutomationInvalidation.requested_at
                    ),
                    AutomationInvalidation.key,
                )
                .limit(1)
            )
            if demand is None:
                return 0
            demands = [demand]
            direct = demand.key.startswith(DIRECT_DEMAND_PREFIX)
            if direct:
                demands = list(
                    session.scalars(
                        select(AutomationInvalidation)
                        .where(AutomationInvalidation.key.startswith(DIRECT_DEMAND_PREFIX))
                        .order_by(
                            func.coalesce(
                                AutomationInvalidation.progressed_at,
                                AutomationInvalidation.requested_at,
                            ),
                            AutomationInvalidation.key,
                        )
                        .limit(settings.automation_invalidation_batch_size)
                    )
                )
                page = [UUID(item.key.removeprefix(DIRECT_DEMAND_PREFIX)) for item in demands]
            elif demand.source_id is None and demand.tag_id is None:
                # Independent writer families share work only when their durable
                # cursors cover the exact same prefix. A new request behind an
                # active pass keeps its own complete coverage. Do not lock queue
                # rows before acquiring the article page's NOWAIT locks.
                demands.extend(
                    session.scalars(
                        select(AutomationInvalidation)
                        .where(
                            AutomationInvalidation.key.in_(GLOBAL_DEMAND_KEYS),
                            AutomationInvalidation.key != demand.key,
                            AutomationInvalidation.source_id.is_(None),
                            AutomationInvalidation.tag_id.is_(None),
                            AutomationInvalidation.after_article_id.is_not_distinct_from(
                                demand.after_article_id
                            ),
                        )
                        .order_by(AutomationInvalidation.key)
                    )
                )
                # The partial identity index bounds the eligible population;
                # historical published/rejected articles are never enumerated.
                identifiers = select(Article.id).where(
                    Article.review_status.in_(["pending", "approved"]),
                    Article.publication_status == "unpublished",
                )
                identity = Article.id
            elif demand.source_id is not None:
                # Limit examined source-origin keys before testing eligibility.
                # A source with mostly terminal history still advances each page.
                identifiers = (
                    select(ArticleOrigin.article_id)
                    .where(ArticleOrigin.source_id == demand.source_id)
                    .distinct()
                )
                identity = ArticleOrigin.article_id
            else:
                # Assignment/linking policy can affect only already linked
                # articles. Catalog identity/IDF changes still use a global pass.
                identifiers = (
                    select(ArticleTag.article_id)
                    .where(ArticleTag.tag_id == demand.tag_id)
                    .distinct()
                )
                identity = ArticleTag.article_id
            if not direct:
                if demand.after_article_id is not None:
                    identifiers = identifiers.where(identity > demand.after_article_id)
                page = list(
                    session.scalars(
                        identifiers.order_by(identity).limit(
                            settings.automation_invalidation_batch_size
                        )
                    )
                )
            locked = []
            if page:
                locked = list(
                    session.scalars(
                        select(Article.id)
                        .where(Article.id.in_(page))
                        .order_by(Article.id)
                        .with_for_update(of=Article, nowait=True)
                    )
                )
            # A writer can hold one family row before changing another. Never
            # wait while ACKing shared pages in a different statement order.
            list(
                session.scalars(
                    select(AutomationInvalidation.key)
                    .where(AutomationInvalidation.key.in_([item.key for item in demands]))
                    .order_by(AutomationInvalidation.key)
                    .with_for_update(nowait=True)
                )
            )
            if locked:
                global_demand = not direct and demand.source_id is None and demand.tag_id is None
                wake = true() if direct else Article.automation_next_check_at > now
                if not global_demand and not direct:
                    wake = or_(wake, maintenance_lane() == 1)
                session.execute(
                    update(Article)
                    .where(
                        Article.id.in_(locked),
                        Article.review_status.in_(["pending", "approved"]),
                        Article.publication_status == "unpublished",
                        wake,
                    )
                    .values(
                        automation_input_revision=Article.automation_input_revision + 1,
                        automation_next_check_at=func.least(Article.automation_next_check_at, now),
                        automation_wait_reason=MAINTENANCE_WAIT_REASON if global_demand else None,
                    )
                )
            for demand in demands:
                revision = demand.scan_revision or demand.revision
                if not direct and len(page) == settings.automation_invalidation_batch_size:
                    # Only the publisher writes cursor fields. A concurrent writer
                    # increments revision without losing this page's progress.
                    session.execute(
                        update(AutomationInvalidation)
                        .where(AutomationInvalidation.key == demand.key)
                        .values(
                            after_article_id=page[-1], scan_revision=revision, progressed_at=now
                        )
                    )
                else:
                    removed = session.execute(
                        delete(AutomationInvalidation).where(
                            AutomationInvalidation.key == demand.key,
                            AutomationInvalidation.revision == revision,
                        )
                    ).rowcount
                    if not removed:
                        # Each family independently retains edits after its pass
                        # started, including entities already covered by this page.
                        session.execute(
                            update(AutomationInvalidation)
                            .where(AutomationInvalidation.key == demand.key)
                            .values(after_article_id=None, scan_revision=None, progressed_at=now)
                        )
            return len(page)
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            return 0
        raise


def wait_for_dependency(article, now, *, reason="dependencies", deadline=None):
    settings = get_settings()
    if not settings.automation_dependency_wakes_enabled:
        return
    recovery = now + timedelta(seconds=settings.automation_reconcile_seconds)
    article.automation_next_check_at = min(recovery, deadline) if deadline else recovery
    article.automation_wait_reason = reason


def wait_for_job(article, job, now):
    if job.status == "running":
        deadline = job.lease_until
        if deadline is not None and deadline <= now:
            # The scheduler's normal recovery runs later in this same tick.
            deadline = now + timedelta(seconds=15)
    else:
        # A prerequisite created during this check naturally has a timestamp a
        # few milliseconds after its observation. Only a real future dispatch
        # slot needs a timer; ordinary execution completion supplies its own wake.
        deadline = job.available_at if job.available_at > now + timedelta(seconds=15) else None
    wait_for_dependency(article, now, reason=f"{job.status}_job", deadline=deadline)


@contextmanager
def observe_automation_demand(session, article, now):
    """Consume only the completed locked observation, including our own requests.

    Semantic writes flushed by this evaluation can advance the database revision.
    Refresh that scalar before acknowledging our own writes. A concurrent
    dependency writer that cannot lock this article records a durable exact wake;
    its later replay advances the revision and due clock after this observation.
    Changed-input retries intentionally remain due and are never acknowledged.
    """
    enabled = get_settings().automation_dependency_wakes_enabled
    yield
    if not enabled or article.automation_next_check_at <= now:
        return
    next_check, reason = article.automation_next_check_at, article.automation_wait_reason
    session.flush()
    session.refresh(article, attribute_names=["automation_input_revision"])
    article.automation_evaluated_revision = article.automation_input_revision
    # Our own newly queued prerequisites have been observed. Their completion
    # will issue a new wake; do not retain the insertion trigger's due clock.
    article.automation_next_check_at = next_check
    article.automation_wait_reason = reason
    flag_modified(article, "automation_next_check_at")
    flag_modified(article, "automation_wait_reason")


def record_automation_backlog(factory):
    """Sample demand age and stalled progress independently of page throughput."""
    global _backlog_sample_at
    runtime = current()
    if runtime is None or runtime.meter_provider is None:
        return
    sampled_at = time.monotonic()
    if _backlog_sample_at is not None and sampled_at - _backlog_sample_at < BACKLOG_SAMPLE_SECONDS:
        return
    # Throttle attempts too: an unavailable telemetry query must not amplify load
    # or turn an already committed scheduler page into a failed business cycle.
    _backlog_sample_at = sampled_at
    now = utcnow()
    try:
        with factory.begin() as session:
            count, requested, progressed, direct_count, direct_oldest = session.execute(
                select(
                    func.count(),
                    func.min(AutomationInvalidation.requested_at),
                    func.min(
                        func.coalesce(
                            AutomationInvalidation.progressed_at,
                            AutomationInvalidation.requested_at,
                        )
                    ),
                    func.count().filter(
                        AutomationInvalidation.key.startswith(DIRECT_DEMAND_PREFIX)
                    ),
                    func.min(AutomationInvalidation.requested_at).filter(
                        AutomationInvalidation.key.startswith(DIRECT_DEMAND_PREFIX)
                    ),
                ).select_from(AutomationInvalidation)
            ).one()
            foreground, foreground_oldest, maintenance, maintenance_oldest = session.execute(
                select(
                    func.count().filter(maintenance_lane() == 0),
                    func.min(Article.automation_next_check_at).filter(maintenance_lane() == 0),
                    func.count().filter(maintenance_lane() == 1),
                    func.min(Article.automation_next_check_at).filter(maintenance_lane() == 1),
                )
                .select_from(Article)
                .where(
                    Article.review_status.in_(["pending", "approved"]),
                    Article.publication_status == "unpublished",
                    Article.automation_next_check_at <= now,
                )
            ).one()
    except SQLAlchemyError:
        logger.warning("automation_backlog_observation_failed")
        return
    attributes = {"kind": "automation-invalidations"}
    runtime.instruments["read_model_pending"].set(count, attributes)
    runtime.instruments["read_model_oldest"].set(
        max(0.0, (now - requested).total_seconds()) if requested else 0, attributes
    )
    runtime.instruments["read_model_oldest"].set(
        max(0.0, (now - progressed).total_seconds()) if progressed else 0,
        {"kind": "automation-invalidation-progress"},
    )
    for kind, count, oldest in (
        ("automation-direct-invalidations", direct_count, direct_oldest),
        ("automation-foreground", foreground, foreground_oldest),
        ("automation-maintenance", maintenance, maintenance_oldest),
    ):
        attributes = {"kind": kind}
        runtime.instruments["read_model_pending"].set(count, attributes)
        runtime.instruments["read_model_oldest"].set(
            max(0.0, (now - oldest).total_seconds()) if oldest else 0, attributes
        )


def expand_automation_demands(factory, now):
    processed = _expand_automation_demands(factory, now)
    record_automation_backlog(factory)
    return processed
