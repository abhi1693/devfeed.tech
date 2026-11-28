"""Checkpointed, bounded sweeps of duplicate successful-job input payloads."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Row, String, exists, literal_column, or_, select, text, tuple_
from sqlalchemy.orm import Session, load_only, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from devfeed_core.config import Settings, get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleRefresh,
    JobPayloadRetention,
    ResearchVerificationJob,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
    utcnow,
)

logger = logging.getLogger(__name__)
# This fixed predicate must remain visible to PostgreSQL even for a generic
# prepared plan, otherwise the partial retention/success indexes cannot apply.
_SUCCEEDED: ColumnElement[str] = literal_column("'succeeded'")
_ACTIVE_REFRESH_STATES: tuple[ColumnElement[str], ...] = (
    literal_column("'active'"),
    literal_column("'review'"),
)
_TERMINAL_REFRESH_STATES: tuple[ColumnElement[str], ...] = tuple(
    literal_column(f"'{state}'") for state in ("promoted", "superseded", "cancelled")
)


def _eligible_conditions[T: ArticleAnalysisJob | TopicAnalysisJob](
    model: type[T],
) -> list[ColumnElement[bool]]:
    newer = model.__table__.alias("newer")
    subjects = (
        (newer.c.article_id == ArticleAnalysisJob.article_id,)
        if model is ArticleAnalysisJob
        else (
            newer.c.proposal_id == TopicAnalysisJob.proposal_id,
            newer.c.topic_id == TopicAnalysisJob.topic_id,
        )
    )
    successor_conditions: list[ColumnElement[bool]] = []
    if model is ArticleAnalysisJob:
        # Private successes never replace ordinary/public evidence. A promoted
        # owner contributes only its exact selected analysis artifact.
        successor_conditions.append(
            or_(
                newer.c.refresh_id.is_(None),
                select(ArticleRefresh.id)
                .where(
                    ArticleRefresh.id == newer.c.refresh_id,
                    ArticleRefresh.article_id == newer.c.article_id,
                    ArticleRefresh.status == "promoted",
                    ArticleRefresh.analysis_job_id == newer.c.id,
                )
                .correlate(newer)
                .exists(),
            )
        )
    # A failed or still-active successor cannot replace the last retained success.
    # Partial subject/range indexes also bound probes through long failed histories.
    newer_exists = or_(
        *(
            select(1)
            .select_from(newer)
            .where(
                subject,
                newer.c.status == _SUCCEEDED,
                newer.c.inputs_pruned_at.is_(None),
                *successor_conditions,
                tuple_(newer.c.created_at, newer.c.id) > tuple_(model.created_at, model.id),
            )
            .order_by(newer.c.created_at.desc(), newer.c.id.desc())
            .limit(1)
            .correlate(model)
            .scalar_subquery()
            .is_not(None)
            for subject in subjects
        )
    )
    conditions = [
        model.status == _SUCCEEDED,
        model.inputs_pruned_at.is_(None),
        model.lease_token.is_(None),
        model.lease_until.is_(None),
        newer_exists,
    ]
    if model is ArticleAnalysisJob:
        # A maintenance owner may select an ordinary or previously promoted job
        # without rebinding it. Protect the selected artifact, not only its owner.
        conditions.append(
            ~exists().where(
                ArticleRefresh.analysis_job_id == model.id,
                ArticleRefresh.status.in_(_ACTIVE_REFRESH_STATES),
            )
        )
        conditions.append(
            exists().where(
                Article.id == ArticleAnalysisJob.article_id,
                Article.review_status != "pending",
                Article.classification_provenance["analysis_id"].astext.is_distinct_from(
                    model.id.cast(String)
                ),
                Article.classification_provenance["ai_generation"][
                    "analysis_id"
                ].astext.is_distinct_from(model.id.cast(String)),
            )
        )
    else:
        conditions.extend(
            [
                ~exists().where(
                    TopicProposal.id == TopicAnalysisJob.proposal_id,
                    TopicProposal.status == "pending",
                ),
                ~exists().where(
                    ResearchVerificationJob.id == model.id,
                    ResearchVerificationJob.status.in_(["queued", "running"]),
                ),
                ~exists().where(
                    TopicRelationProposal.job_id == model.id,
                    TopicRelationProposal.status == "pending",
                ),
            ]
        )
    return conditions


def _prune_payload(job: ArticleAnalysisJob | TopicAnalysisJob, now: datetime) -> None:
    job.input_snapshot = {}
    if isinstance(job, ArticleAnalysisJob):
        job.catalog_snapshot = {}
        job.steps = {
            name: {field: value for field, value in step.items() if field != "output"}
            for name, step in job.steps.items()
            if isinstance(step, dict)
        }
    job.inputs_pruned_at = now
    job.usage = {**job.usage, "input_payload_pruned_at": now.isoformat()}


def _prune_page[T: ArticleAnalysisJob | TopicAnalysisJob](
    session: Session,
    model: type[T],
    state: JobPayloadRetention,
    now: datetime,
    settings: Settings,
) -> int:
    current_cutoff = now - timedelta(days=settings.job_payload_retention_days)
    # Freeze the upper age boundary so arrivals cannot extend a sweep. A restart
    # with a longer retention policy must immediately protect younger evidence.
    state.cutoff = current_cutoff if state.cutoff is None else min(state.cutoff, current_cutoff)
    subject_column = (
        ArticleAnalysisJob.article_id
        if model is ArticleAnalysisJob
        else TopicAnalysisJob.proposal_id
    )
    page_query = (
        select(model.id, model.finished_at, subject_column)
        .where(
            model.status == _SUCCEEDED,
            model.inputs_pruned_at.is_(None),
            model.finished_at < state.cutoff,
        )
        .order_by(model.finished_at, model.id)
        .limit(settings.job_payload_scan_batch_size)
    )
    if state.cursor_finished_at is not None:
        page_query = page_query.where(
            tuple_(model.finished_at, model.id) > tuple_(state.cursor_finished_at, state.cursor_id)
        )
    # This LIMIT applies BEFORE any history/provenance/verification probes. In
    # particular a permanently protected prefix cannot make selection unbounded.
    page = session.execute(page_query).all()
    candidates: Sequence[Row[uuid.UUID, datetime | None, uuid.UUID | None]] = []
    jobs: Sequence[T] = []
    if page:
        candidates = session.execute(
            select(model.id, model.finished_at, subject_column)
            .where(
                model.id.in_([row.id for row in page]),
                model.finished_at < state.cutoff,
                # The ID list bounds returned rows, but an ordered retention
                # index can still traverse the entire earlier history. Carry
                # both leading range keys so deep pages remain bounded too.
                tuple_(model.finished_at, model.id) >= tuple_(page[0].finished_at, page[0].id),
                tuple_(model.finished_at, model.id) <= tuple_(page[-1].finished_at, page[-1].id),
                *_eligible_conditions(model),
            )
            .order_by(model.finished_at, model.id)
            .limit(settings.job_payload_prune_batch_size)
        ).all()
    if candidates:
        parent = Article if model is ArticleAnalysisJob else TopicProposal
        parent_ids = {row[2] for row in candidates if row[2] is not None}
        # Lock mutable review/provenance first, then jobs; every acquisition skips
        # locks so a worker can always finish. Only potential redactions take row
        # locks: a page of latest/protected successes doesn't dirty parent heaps.
        locked_parents = session.scalars(
            select(parent.id)
            .where(parent.id.in_(parent_ids))
            .order_by(parent.id)
            # Review/provenance writes must wait; foreign-key inserts for
            # ingestion may continue because parent identity is unchanged.
            .with_for_update(key_share=True, skip_locked=True)
        ).all()
        unlocked_subject = (
            ArticleAnalysisJob.article_id.in_(locked_parents)
            if model is ArticleAnalysisJob
            else or_(
                TopicAnalysisJob.proposal_id.is_(None),
                TopicAnalysisJob.proposal_id.in_(locked_parents),
            )
        )
        locked_jobs = session.scalars(
            select(model.id)
            .where(model.id.in_([row.id for row in candidates]), unlocked_subject)
            .order_by(model.finished_at, model.id)
            .with_for_update(skip_locked=True)
        ).all()
        # PostgreSQL's row-lock recheck can still use the statement's old snapshot
        # for other tables. A separate statement AFTER locking jobs must see any
        # verification admitted just before we acquired those locks. Admission
        # takes the same job lock, so it cannot race the fresh protection check.
        jobs = session.scalars(
            select(model)
            .options(load_only(model.id, model.finished_at, model.usage))
            .where(
                model.id.in_(locked_jobs),
                model.finished_at < state.cutoff,
                *_eligible_conditions(model),
            )
            .order_by(model.finished_at, model.id)
        ).all()
        for job in jobs:
            _prune_payload(job, now)

    # If the prune budget was filled, don't advance past eligible rows that the
    # LIMIT didn't return. Locked rows behind the cursor are retried next sweep.
    exhausted = (
        len(page) < settings.job_payload_scan_batch_size
        and len(candidates) < settings.job_payload_prune_batch_size
    )
    if not page or exhausted:
        state.cutoff = state.cursor_finished_at = state.cursor_id = None
        delay = settings.job_payload_sweep_interval_seconds
    else:
        if candidates and len(candidates) == settings.job_payload_prune_batch_size:
            last = candidates[-1]
        else:
            last = page[-1]
        state.cursor_finished_at, state.cursor_id = last.finished_at, last.id
        delay = settings.job_payload_page_interval_seconds
    state.next_run_at = now + timedelta(seconds=delay)
    session.info["job_payload_retention_page"] = {
        "kind": state.kind,
        "scanned": len(page),
        "pruned": len(jobs),
        "sweep_completed": exhausted,
        "next_run_at": state.next_run_at.isoformat(),
    }
    return len(jobs)


def _prune_refresh_payloads(
    session: Session, state: JobPayloadRetention, now: datetime, settings: Settings
) -> int:
    """Redact one sparse page, retaining ownership, receipts and public state."""
    limit = min(settings.job_payload_scan_batch_size, settings.job_payload_prune_batch_size)
    owners = session.scalars(
        select(ArticleRefresh)
        .options(load_only(ArticleRefresh.id))
        .where(
            ArticleRefresh.payload_pruned_at.is_(None),
            ArticleRefresh.completed_at.is_not(None),
            ArticleRefresh.status.in_(_TERMINAL_REFRESH_STATES),
            ArticleRefresh.completed_at < now - timedelta(days=settings.job_payload_retention_days),
        )
        .order_by(ArticleRefresh.completed_at, ArticleRefresh.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for owner in owners:
        owner.payload = {}
        owner.payload_pruned_at = now
    if len(owners) == limit:
        # Reuse the existing article cadence for an owner backlog; no additional
        # checkpoint, queue or scheduler is needed. Pruned rows leave the index.
        state.next_run_at = min(
            state.next_run_at, now + timedelta(seconds=settings.job_payload_page_interval_seconds)
        )
    if owners:
        session.info["article_refresh_payload_retention_page"] = {
            "redacted": len(owners),
            "next_run_at": state.next_run_at.isoformat(),
        }
    return len(owners)


def prune_job_payloads(factory: sessionmaker[Session]) -> int:
    """Run at most one due page per history; ownership/checkpoint commit together.

    Ordinary ticks only probe two primary-key maintenance rows. A process crash
    rolls back the payload rewrites and cursor, without leaving a timed lease.
    Due article pages also redact old private-owner payloads; the return value
    counts job inputs only, with owner redactions reported separately.
    """
    settings = get_settings()
    pruned = 0
    histories: tuple[tuple[str, type[ArticleAnalysisJob] | type[TopicAnalysisJob]], ...] = (
        ("article", ArticleAnalysisJob),
        ("topic", TopicAnalysisJob),
    )
    for kind, model in histories:
        with factory.begin() as session:
            now = utcnow()
            state = session.scalar(
                select(JobPayloadRetention)
                .where(JobPayloadRetention.kind == kind, JobPayloadRetention.next_run_at <= now)
                .with_for_update(skip_locked=True)
            )
            if state is not None:
                session.execute(text("SET LOCAL statement_timeout = '2s'"))
                session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                session.execute(text("SET LOCAL transaction_timeout = '5s'"))
                pruned += _prune_page(session, model, state, now, settings)
                if model is ArticleAnalysisJob:
                    _prune_refresh_payloads(session, state, now, settings)
                    session.info["job_payload_retention_page"]["next_run_at"] = (
                        state.next_run_at.isoformat()
                    )
        if state is not None:
            # A timeout during flush/commit must not report successful progress.
            logger.info(
                "job_payload_retention_page", extra=session.info.pop("job_payload_retention_page")
            )
            if progress := session.info.pop("article_refresh_payload_retention_page", None):
                logger.info("article_refresh_payload_retention_page", extra=progress)
    return pruned
