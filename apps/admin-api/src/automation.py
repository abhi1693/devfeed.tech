"""Explain current pipeline blockers and provide revision-checked, targeted recovery."""

import uuid
from datetime import datetime, timedelta
from typing import Literal

from devfeed_core.analysis import request_analysis
from devfeed_core.article_jobs import approved_sources, request_article_enrichment
from devfeed_core.config import get_settings
from devfeed_core.json_types import JsonValue
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticlePublicationDecision,
    ArticleReview,
    ArticleTopic,
    ResearchVerificationJob,
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
from sqlalchemy import (
    BigInteger,
    String,
    case,
    cast,
    false,
    func,
    literal,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.dialects.postgresql import JSONPATH

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, record, require_record
from devfeed_admin_api.search import text_search

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


class AnalysisTokenDay(BaseModel):
    date: str
    article_analysis: int = 0
    topic_analysis: int = 0
    research_verification: int = 0
    reported_runs: int = 0
    unreported_runs: int = 0


class AutomationOverview(BaseModel):
    full_automation: bool = Field(default_factory=lambda: get_settings().full_automation)
    blockers: list[AutomationBlocker]
    published_in_window: int
    published_without_intervention: int
    automatic_publication_percent: float | None
    median_ingestion_to_publication_seconds: float | None
    analysis_tokens: int
    analysis_duration_ms: int
    usage_reported_runs: int
    usage_unreported_runs: int = 0
    token_activity: list[AnalysisTokenDay] = Field(default_factory=list)


def analysis_token_activity(session, start: datetime, now: datetime):
    """Aggregate stored cumulative job usage once, on the UTC completion date."""
    statements = []
    for kind, model in (
        ("article_analysis", ArticleAnalysisJob),
        ("topic_analysis", TopicAnalysisJob),
        ("research_verification", ResearchVerificationJob),
    ):
        day = func.date(func.timezone("UTC", model.finished_at))
        raw = model.usage["totalTokens"].astext
        tokens = case((raw.op("~")(r"^[0-9]{1,18}$"), cast(raw, BigInteger)), else_=None)
        statements.append(
            select(
                day.label("date"),
                literal(kind).label("kind"),
                func.coalesce(func.sum(tokens), 0).label("tokens"),
                func.count(tokens).label("reported"),
                func.count().filter(tokens.is_(None)).label("unreported"),
                func.coalesce(func.sum(model.duration_ms), 0).label("duration"),
            )
            .where(
                model.finished_at >= start,
                model.finished_at <= now,
                model.status.in_(("succeeded", "failed")),
            )
            .group_by(day)
        )
    days = {
        (start.date() + timedelta(days=index)).isoformat(): AnalysisTokenDay(
            date=(start.date() + timedelta(days=index)).isoformat()
        )
        for index in range((now.date() - start.date()).days + 1)
    }
    duration = 0
    for row in session.execute(union_all(*statements)).mappings():
        point = days[row["date"].isoformat()]
        setattr(point, row["kind"], int(row["tokens"]))
        point.reported_runs += row["reported"]
        point.unreported_runs += row["unreported"]
        duration += row["duration"]
    return list(days.values()), duration


def automation_metrics(session, start: datetime, now: datetime) -> AutomationOverview:
    full = get_settings().full_automation
    pending = (Article.review_status == "pending", Article.publication_status == "unpublished")
    primary_topic = (
        select(ArticleTopic.article_id)
        .join(Topic, ArticleTopic.topic_id == Topic.id)
        .where(
            ArticleTopic.article_id == Article.id,
            ArticleTopic.role == "primary",
            Topic.status == "active",
        )
        .exists()
    )
    content = func.coalesce(ArticleContent.text, Article.summary)
    # A boolean match avoids allocating the entire letter-only copy of long text.
    # Keep the same PostgreSQL alpha class and exact 40-letter threshold.
    readable_text = content.op("~")("([[:alpha:]][^[:alpha:]]*){40}")
    # Keep JSON extraction outside the ordered LIMIT. Extracting the policy here
    # detoasts every historical result before sorting, even though only the
    # newest run contributes to the dashboard.
    latest = (
        select(
            ArticleAnalysisJob.status,
            ArticleAnalysisJob.result,
        )
        .where(ArticleAnalysisJob.article_id == Article.id)
        .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id.desc())
        .limit(1)
        .correlate(Article)
        .lateral()
    )
    # Materialize the expensive text/evidence checks once per pending article.
    # Each blocker can overlap another; rank within each group before limiting
    # recovery targets so counts stay exact and the response stays bounded.
    flags = (
        select(
            Article.id,
            Article.title,
            Article.editorial_revision,
            Article.discovered_at,
            readable_text.label("readable"),
            primary_topic.label("primary"),
            func.coalesce(latest.c.status == "failed", false()).label("failed"),
            func.coalesce(
                latest.c.result["publication_policy"]["status"].astext == "would_publish", false()
            ).label("preview"),
        )
        .outerjoin(ArticleContent, ArticleContent.article_id == Article.id)
        .outerjoin(latest, true())
        .where(*pending)
        .cte("pending_article_flags")
        .prefix_with("MATERIALIZED")
    )
    readable, primary, failed, preview = (
        flags.c.readable,
        flags.c.primary,
        flags.c.failed,
        flags.c.preview,
    )
    definitions = (
        ("insufficient_text", "Insufficient article text", ~readable, "enrich"),
        ("missing_primary_topic", "Missing primary topic", ~primary & readable, "analyze"),
        ("analysis_failed", "Failed article analysis", failed, "analyze"),
        ("publication_preview", "Ready in publication preview", preview, "evaluate"),
        (
            "editorial_review",
            "Articles awaiting automatic decision"
            if full
            else "Classified articles awaiting review",
            primary & readable & ~preview & ~failed,
            "evaluate",
        ),
    )
    matches = union_all(
        *(
            select(
                flags.c.id,
                flags.c.title,
                flags.c.editorial_revision,
                flags.c.discovered_at,
                literal(code).label("code"),
            ).where(condition)
            for code, _, condition, _ in definitions
        )
    ).subquery()
    ranked = select(
        matches,
        func.count().over(partition_by=matches.c.code).label("total"),
        func.row_number()
        .over(partition_by=matches.c.code, order_by=(matches.c.discovered_at, matches.c.id))
        .label("position"),
    ).subquery()
    targets: dict[str, list] = {}
    for row in session.execute(
        select(ranked).where(ranked.c.position <= 5).order_by(ranked.c.code, ranked.c.position)
    ):
        targets.setdefault(row.code, []).append(row)
    blockers = []
    for code, label, _, action in definitions:
        rows = targets.get(code, [])
        blockers.append(
            AutomationBlocker(
                code=code,
                label=label,
                count=rows[0].total if rows else 0,
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
            or_(
                func.jsonb_path_exists(
                    TopicAnalysisJob.result,
                    cast('$.evidence_verification.checks.* ? (@.status == "unverified")', JSONPATH),
                ),
                TopicAnalysisJob.result["topic_verification"]["check"]["verdict"].astext.in_(
                    ["unsupported", "uncertain"]
                ),
                func.jsonb_path_exists(
                    TopicAnalysisJob.result,
                    cast("$.topic_verification.check.fields[*] ? (@.supported == false)", JSONPATH),
                ),
                func.jsonb_path_exists(
                    TopicAnalysisJob.result,
                    cast(
                        "$.topic_verification.check.aliases[*] ? (@.same_identity == false)",
                        JSONPATH,
                    ),
                ),
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
            label="Topic research in progress"
            if full
            else "Research evidence or identity needs review",
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
                func.jsonb_path_exists(
                    TopicAnalysisJob.result,
                    cast(
                        "$.relationship_verification.checks.* ? (@.proposal_id == $id "
                        '&& (@.verdict != "supported" || @.scope_matches == false))',
                        JSONPATH,
                    ),
                    func.jsonb_build_object("id", cast(TopicRelationProposal.id, String)),
                ),
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
            label="Relationship verification in progress"
            if full
            else "Relationship evidence or meaning needs review",
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
    published, autonomous, median = session.execute(
        select(
            func.count(),
            func.count().filter(automatic, ~manual),
            func.percentile_cont(0.5)
            .within_group(
                func.extract("epoch", Article.published_to_feed_at - Article.discovered_at)
            )
            .filter(Article.published_to_feed_at >= Article.discovered_at),
        )
        .select_from(Article)
        .where(*publication_window)
    ).one()
    token_activity, duration = analysis_token_activity(session, start, now)
    return AutomationOverview(
        blockers=blockers,
        published_in_window=published,
        published_without_intervention=autonomous,
        automatic_publication_percent=round(100 * autonomous / published, 1) if published else None,
        median_ingestion_to_publication_seconds=median,
        analysis_tokens=sum(
            day.article_analysis + day.topic_analysis + day.research_verification
            for day in token_activity
        ),
        analysis_duration_ms=duration,
        usage_reported_runs=sum(day.reported_runs for day in token_activity),
        usage_unreported_runs=sum(day.unreported_runs for day in token_activity),
        token_activity=token_activity,
    )


class RecoveryRequest(InputModel):
    expected_revision: int = Field(ge=0)


class PublicationDecisionOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    decision: dict[str, JsonValue]


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
    require_record(session, Article, article_id)
    statement = select(ArticlePublicationDecision).where(
        ArticlePublicationDecision.article_id == article_id
    )
    if query.q:
        statement = statement.where(
            text_search(query.q, cast(ArticlePublicationDecision.decision, String))
        )
    return paginate(
        session,
        statement,
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
    require_record(session, Source, source_id)
    statement = select(SourcePublicationPolicyReview).where(
        SourcePublicationPolicyReview.source_id == source_id
    )
    if query.q:
        statement = statement.where(
            text_search(
                query.q, SourcePublicationPolicyReview.mode, SourcePublicationPolicyReview.actor
            )
        )
    return paginate(
        session,
        statement,
        query,
        {"created_at": SourcePublicationPolicyReview.created_at},
        "-created_at",
    )


class RecoveryResult(BaseModel):
    status: str
    job_id: uuid.UUID | None = None
    decision: dict[str, JsonValue] | None = None


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
