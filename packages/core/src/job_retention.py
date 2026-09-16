"""Bound duplicate successful-job input payloads, retaining durable audit identities."""

from datetime import timedelta

from sqlalchemy import String, exists, or_, select, tuple_
from sqlalchemy.orm import load_only

from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ResearchVerificationJob,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
    utcnow,
)


def prune_job_payloads(factory) -> int:
    settings = get_settings()
    now = utcnow()
    cutoff = now - timedelta(days=settings.job_payload_retention_days)
    pruned = 0
    for model in (ArticleAnalysisJob, TopicAnalysisJob):
        newer = model.__table__.alias("newer")
        subject = (
            newer.c.article_id == ArticleAnalysisJob.article_id
            if model is ArticleAnalysisJob
            else or_(
                newer.c.proposal_id == TopicAnalysisJob.proposal_id,
                newer.c.topic_id == TopicAnalysisJob.topic_id,
            )
        )
        newer_exists = exists().where(
            subject, tuple_(newer.c.created_at, newer.c.id) > tuple_(model.created_at, model.id)
        )
        conditions = [
            model.status == "succeeded",
            model.finished_at < cutoff,
            model.inputs_pruned_at.is_(None),
            newer_exists,
        ]
        if model is ArticleAnalysisJob:
            conditions.append(
                exists().where(
                    Article.id == model.article_id,
                    Article.review_status != "pending",
                    Article.classification_provenance["analysis_id"].astext.is_distinct_from(
                        model.id.cast(String)
                    ),
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
        with factory.begin() as session:
            jobs = session.scalars(
                select(model)
                .options(load_only(model.id, model.usage))
                .where(*conditions)
                .order_by(model.finished_at, model.id)
                .limit(settings.job_payload_prune_batch_size)
                .with_for_update(skip_locked=True)
            ).all()
            for job in jobs:
                job.input_snapshot = {}
                if isinstance(job, ArticleAnalysisJob):
                    job.catalog_snapshot = {}
                job.inputs_pruned_at = now
                job.usage = {**job.usage, "input_payload_pruned_at": now.isoformat()}
            pruned += len(jobs)
    return pruned
