"""Explain current pipeline blockers and provide revision-checked, targeted recovery."""

import uuid
from datetime import datetime
from typing import Literal

from devfeed_core.analysis import request_analysis
from devfeed_core.article_jobs import approved_sources, request_article_enrichment
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticlePublicationDecision,
    ArticleReview,
    ArticleTopic,
    Source,
    SourcePublicationPolicyReview,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
)
from devfeed_core.publication_policy import apply_publication_policy
from devfeed_core.research_evidence import VERIFICATION_VERSION
from devfeed_core.schemas import InputModel, ORMModel
from devfeed_core.services import OperationConflict
from devfeed_core.topics import lock_topics
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, record

router = APIRouter(
    prefix="/v1/admin/automation", tags=["admin-automation"], dependencies=[Depends(require_admin)]
)


class RecoveryTarget(BaseModel):
    id: uuid.UUID
    title: str
    kind: Literal["article", "topic-proposal", "relationship-proposal"]
    revision: int = 0


class AutomationBlocker(BaseModel):
    code: str
    label: str
    count: int
    action: Literal["enrich", "analyze", "evaluate"] | None = None
    targets: list[RecoveryTarget]


class AutomationOverview(BaseModel):
    blockers: list[AutomationBlocker]
    published_in_window: int
    published_without_intervention: int
    automatic_publication_percent: float | None
    median_ingestion_to_publication_seconds: float | None
    analysis_tokens: int
    analysis_duration_ms: int
    usage_reported_runs: int


def automation_metrics(session, start: datetime, now: datetime) -> AutomationOverview:
    pending = (Article.review_status == "pending", Article.publication_status == "unpublished")
    primary = (
        select(ArticleTopic.article_id)
        .join(Topic, ArticleTopic.topic_id == Topic.id)
        .where(
            ArticleTopic.article_id == Article.id,
            ArticleTopic.role == "primary",
            Topic.status == "active",
        )
        .exists()
    )
    content = func.coalesce(
        select(ArticleContent.text)
        .where(ArticleContent.article_id == Article.id)
        .scalar_subquery(),
        Article.summary,
    )
    readable = func.length(func.regexp_replace(content, "[^[:alpha:]]", "", "g")) >= 40
    latest = (
        select(ArticleAnalysisJob.id)
        .where(ArticleAnalysisJob.article_id == Article.id)
        .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id.desc())
        .limit(1)
        .correlate(Article)
        .scalar_subquery()
    )
    failed = (
        select(ArticleAnalysisJob.id)
        .where(ArticleAnalysisJob.id == latest, ArticleAnalysisJob.status == "failed")
        .correlate(Article)
        .exists()
    )
    preview = (
        select(ArticleAnalysisJob.id)
        .where(
            ArticleAnalysisJob.id == latest,
            ArticleAnalysisJob.result["publication_policy"]["status"].astext == "would_publish",
        )
        .correlate(Article)
        .exists()
    )
    blockers = []
    for code, label, condition, action in (
        ("insufficient_text", "Insufficient article text", ~readable, "enrich"),
        ("missing_primary_topic", "Missing primary topic", ~primary & readable, "analyze"),
        ("analysis_failed", "Failed article analysis", failed, "analyze"),
        ("publication_preview", "Ready in publication preview", preview, "evaluate"),
        (
            "editorial_review",
            "Classified articles awaiting review",
            primary & readable & ~preview & ~failed,
            "evaluate",
        ),
    ):
        count = (
            session.scalar(select(func.count()).select_from(Article).where(*pending, condition))
            or 0
        )
        rows = session.execute(
            select(Article.id, Article.title, Article.editorial_revision)
            .where(*pending, condition)
            .order_by(Article.discovered_at, Article.id)
            .limit(5)
        )
        blockers.append(
            AutomationBlocker(
                code=code,
                label=label,
                count=count,
                action=action,
                targets=[
                    RecoveryTarget(
                        id=row.id, title=row.title, kind="article", revision=row.editorial_revision
                    )
                    for row in rows
                ],
            )
        )
    latest_topic = (
        select(TopicAnalysisJob.id)
        .where(TopicAnalysisJob.proposal_id == TopicProposal.id)
        .order_by(TopicAnalysisJob.created_at.desc(), TopicAnalysisJob.id.desc())
        .limit(1)
        .correlate(TopicProposal)
        .scalar_subquery()
    )
    unverified = (
        select(TopicAnalysisJob.id)
        .where(
            TopicAnalysisJob.id == latest_topic,
            func.jsonb_path_exists(
                TopicAnalysisJob.result,
                cast('$.evidence_verification.checks.* ? (@.status == "unverified")', JSONPATH),
            ),
        )
        .correlate(TopicProposal)
        .exists()
    )
    query = select(TopicProposal).where(TopicProposal.status == "pending", unverified)
    count = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(TopicProposal.created_at, TopicProposal.id).limit(5))
    blockers.append(
        AutomationBlocker(
            code="evidence_unverified",
            label="Research evidence needs review",
            count=count,
            targets=[
                RecoveryTarget(
                    id=row.id,
                    title=row.proposed.get("name", "Unnamed topic"),
                    kind="topic-proposal",
                )
                for row in rows
            ],
        )
    )
    relation_query = (
        select(TopicRelationProposal)
        .join(TopicAnalysisJob, TopicRelationProposal.job_id == TopicAnalysisJob.id)
        .where(
            TopicRelationProposal.status == "pending",
            or_(
                TopicAnalysisJob.result["evidence_verification"]["version"].astext.is_distinct_from(
                    VERIFICATION_VERSION
                ),
                ~func.jsonb_path_exists(
                    TopicAnalysisJob.result,
                    cast(
                        '$.evidence_verification.checks.* ? (@.status == "verified" '
                        "&& @.url == $url && @.quote == $quote)",
                        JSONPATH,
                    ),
                    func.jsonb_build_object(
                        "url",
                        TopicRelationProposal.evidence_url,
                        "quote",
                        TopicRelationProposal.evidence_quote,
                    ),
                ),
            ),
        )
    )
    relation_count = (
        session.scalar(select(func.count()).select_from(relation_query.subquery())) or 0
    )
    relation_rows = session.scalars(
        relation_query.order_by(TopicRelationProposal.created_at, TopicRelationProposal.id).limit(5)
    )
    blockers.append(
        AutomationBlocker(
            code="relationship_evidence_unverified",
            label="Relationship evidence needs review",
            count=relation_count,
            targets=[
                RecoveryTarget(
                    id=row.id,
                    title=(
                        f"{row.topic_snapshot.get('name', 'Unnamed topic')} → "
                        f"{row.related_topic_snapshot.get('name', 'Unnamed topic')}"
                    ),
                    kind="relationship-proposal",
                )
                for row in relation_rows
            ],
        )
    )
    publication_window = (
        Article.published_to_feed_at >= start,
        Article.published_to_feed_at <= now,
    )
    published = (
        session.scalar(select(func.count()).select_from(Article).where(*publication_window)) or 0
    )
    automatic = (
        select(ArticleReview.id)
        .where(
            ArticleReview.article_id == Article.id,
            ArticleReview.action == "publish",
            ArticleReview.automation != {},
            ArticleReview.created_at <= Article.published_to_feed_at,
        )
        .exists()
    )
    manual = (
        select(ArticleReview.id)
        .where(
            ArticleReview.article_id == Article.id,
            ArticleReview.automation == {},
            ArticleReview.created_at <= Article.published_to_feed_at,
        )
        .exists()
    )
    autonomous = (
        session.scalar(
            select(func.count()).select_from(Article).where(*publication_window, automatic, ~manual)
        )
        or 0
    )
    median = session.scalar(
        select(
            func.percentile_cont(0.5).within_group(
                func.extract("epoch", Article.published_to_feed_at - Article.discovered_at)
            )
        ).where(*publication_window, Article.published_to_feed_at >= Article.discovered_at)
    )
    tokens = duration = reported = 0
    for model in (ArticleAnalysisJob, TopicAnalysisJob):
        totals = session.execute(
            select(
                func.coalesce(func.sum(cast(model.usage["totalTokens"].astext, BigInteger)), 0),
                func.coalesce(func.sum(model.duration_ms), 0),
                func.count().filter(model.usage["totalTokens"].astext.is_not(None)),
            ).where(model.finished_at >= start, model.finished_at <= now)
        ).one()
        tokens += totals[0]
        duration += totals[1]
        reported += totals[2]
    return AutomationOverview(
        blockers=blockers,
        published_in_window=published,
        published_without_intervention=autonomous,
        automatic_publication_percent=round(100 * autonomous / published, 1) if published else None,
        median_ingestion_to_publication_seconds=median,
        analysis_tokens=tokens,
        analysis_duration_ms=duration,
        usage_reported_runs=reported,
    )


class RecoveryRequest(InputModel):
    expected_revision: int = Field(ge=0)


class PublicationDecisionOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    decision: dict


class PublicationPolicyReviewOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    mode: str
    revision: int
    actor: str


@router.get(
    "/articles/{article_id}/decisions",
    response_model=Page[PublicationDecisionOut],
    operation_id="admin_publication_decisions",
)
def publication_decisions(article_id: uuid.UUID, session: DB, query: Listing):
    record(session, Article, article_id)
    return paginate(
        session,
        select(ArticlePublicationDecision).where(
            ArticlePublicationDecision.article_id == article_id
        ),
        query,
        {"created_at": ArticlePublicationDecision.created_at},
        "-created_at",
    )


@router.get(
    "/sources/{source_id}/policies",
    response_model=Page[PublicationPolicyReviewOut],
    operation_id="admin_publication_policy_history",
)
def publication_policy_history(source_id: uuid.UUID, session: DB, query: Listing):
    record(session, Source, source_id)
    return paginate(
        session,
        select(SourcePublicationPolicyReview).where(
            SourcePublicationPolicyReview.source_id == source_id
        ),
        query,
        {"created_at": SourcePublicationPolicyReview.created_at},
        "-created_at",
    )


class RecoveryResult(BaseModel):
    status: str
    job_id: uuid.UUID | None = None
    decision: dict | None = None


@router.post(
    "/articles/{article_id}/{action}",
    response_model=RecoveryResult,
    operation_id="admin_automation_recover",
)
def recover(
    article_id: uuid.UUID,
    action: Literal["enrich", "analyze", "evaluate"],
    body: RecoveryRequest,
    session: DB,
    admin: Admin,
):
    # Keep the same source -> article -> taxonomy order as the analysis worker.
    if not approved_sources(session, article_id, lock=True):
        raise OperationConflict("An approved source is required")
    article = record(session, Article, article_id, lock=True)
    if (
        article.editorial_revision != body.expected_revision
        or article.review_status != "pending"
        or article.publication_status != "unpublished"
    ):
        raise OperationConflict("Article changed; refresh the overview before retrying")
    if action == "enrich":
        enrichment = request_article_enrichment(session, article_id)
        assert enrichment is not None
        result = RecoveryResult(status="queued", job_id=enrichment.id)
    elif action == "analyze":
        if not get_settings().ai_enabled:
            raise OperationConflict("Enable AI before requesting analysis")
        job = request_analysis(session, article_id)
        result = RecoveryResult(status="queued", job_id=job.id)
    else:
        if session.scalar(
            select(ArticleAnalysisJob.id)
            .where(
                ArticleAnalysisJob.article_id == article_id,
                ArticleAnalysisJob.status.in_(["queued", "running"]),
            )
            .limit(1)
        ):
            raise OperationConflict("Wait for active analysis before evaluating publication")
        job = session.scalar(
            select(ArticleAnalysisJob)
            .where(ArticleAnalysisJob.article_id == article_id)
            .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id.desc())
            .limit(1)
        )
        if job is None:
            raise OperationConflict("Analyze the article before evaluating publication")
        lock_topics(session)
        decision = apply_publication_policy(session, article, job)
        result = RecoveryResult(status=decision["status"], decision=decision)
    session.commit()
    return result
