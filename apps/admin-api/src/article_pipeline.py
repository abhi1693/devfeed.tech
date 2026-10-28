"""ClickHouse pipeline boards and authoritative transactional detail/actions."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from devfeed_core.analysis import catalog
from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_article_cards import (
    _actions as _actions,
)
from devfeed_core.analytics_article_cards import (
    _analysis_steps as _analysis_steps,
)
from devfeed_core.analytics_article_cards import (
    _article_facts,
)
from devfeed_core.analytics_article_cards import (
    _batch_facts as _batch_facts,
)
from devfeed_core.analytics_article_cards import (
    _batch_readiness as _batch_readiness,
)
from devfeed_core.analytics_article_cards import (
    _card as _card,
)
from devfeed_core.analytics_article_cards import (
    _current_analysis_step as _current_analysis_step,
)
from devfeed_core.analytics_article_cards import (
    _display_source_id as _display_source_id,
)
from devfeed_core.analytics_article_cards import (
    _job as _job,
)
from devfeed_core.analytics_article_cards import (
    _jobs as _jobs,
)
from devfeed_core.analytics_article_cards import (
    _latest as _latest,
)
from devfeed_core.analytics_article_cards import (
    _readiness_key as _readiness_key,
)
from devfeed_core.analytics_article_cards import (
    _statement as _statement,
)
from devfeed_core.analytics_card_types import (
    STAGES as STAGES,
)
from devfeed_core.analytics_card_types import (
    AdvancePipeline as AdvancePipeline,
)
from devfeed_core.analytics_card_types import (
    AnalysisStepOut as AnalysisStepOut,
)
from devfeed_core.analytics_card_types import (
    PipelineBoard as PipelineBoard,
)
from devfeed_core.analytics_card_types import (
    PipelineCard as PipelineCard,
)
from devfeed_core.analytics_card_types import (
    PipelineCheck as PipelineCheck,
)
from devfeed_core.analytics_card_types import (
    PipelineColumn as PipelineColumn,
)
from devfeed_core.analytics_card_types import (
    PipelineDetail as PipelineDetail,
)
from devfeed_core.analytics_card_types import (
    PipelineJob as PipelineJob,
)
from devfeed_core.analytics_card_types import (
    PipelineJobs as PipelineJobs,
)
from devfeed_core.analytics_card_types import (
    PipelineSourceGroup as PipelineSourceGroup,
)
from devfeed_core.analytics_card_types import (
    RetryPipeline as RetryPipeline,
)
from devfeed_core.analytics_card_types import (
    Stage as Stage,
)
from devfeed_core.article_jobs import approved_sources, request_article_enrichment
from devfeed_core.article_lifecycle import (
    ProcessingInputs,
    derive_publication_readiness,
    publication_readiness,
    reconcile_processing,
)
from devfeed_core.article_processing_retry import (
    processing_retry_targets,
    request_processing_retry,
    retry_readiness,
)
from devfeed_core.models import Article
from devfeed_core.schemas import DatabaseText
from devfeed_core.services import OperationConflict
from devfeed_core.text_search import normalized_text
from devfeed_http.dependencies import run_database
from devfeed_http.pagination import record
from fastapi import APIRouter, Depends, Query
from sqlalchemy import literal, select
from sqlalchemy.orm import Session

from devfeed_admin_api.analytics_current import preparing
from devfeed_admin_api.analytics_runtime import AnalyticsRuntime, get_analytics
from devfeed_admin_api.auth import Admin, actor, require_admin
from devfeed_admin_api.dependencies import DB, DBFactory

router = APIRouter(tags=["admin-articles"], dependencies=[Depends(require_admin)])
Analytics = Annotated[AnalyticsRuntime, Depends(get_analytics)]


def _normalized_search(session: Session, query: str | None) -> str | None:
    """Retain PostgreSQL Unicode/locale semantics using one literal, table-free SELECT."""
    if not query:
        return None
    return session.scalar(select(normalized_text(literal(query))))


def _hydrate_clocks(session: Session, result: PipelineBoard) -> PipelineBoard:
    items = [item for column in result.columns for item in column.items]
    clocks = (
        dict(
            session.execute(
                select(Article.id, Article.automation_next_check_at).where(
                    Article.id.in_([item.id for item in items])
                )
            ).all()
        )
        if items
        else {}
    )
    for item in items:
        if item.state != "complete" and item.id in clocks:
            item.next_check_at = clocks[item.id]
    return result


@router.get(
    "/v1/admin/article-pipeline",
    response_model=PipelineBoard,
    operation_id="admin_article_pipeline",
)
async def board(
    factory: DBFactory,
    analytics: Analytics,
    q: Annotated[DatabaseText | None, Query(max_length=200)] = None,
    source_id: uuid.UUID | None = None,
    needs_attention: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    group_by: Literal["source"] | None = None,
) -> PipelineBoard:
    needle = await run_database(factory, _normalized_search, q)
    try:
        result = await analytics.article_board(
            q=q,
            normalized_q=needle,
            source_id=source_id,
            needs_attention=needs_attention,
            limit=limit,
            group_by=group_by,
        )
    except AnalyticsUnavailable as error:
        raise preparing() from error
    return await run_database(factory, _hydrate_clocks, result)


@router.get(
    "/v1/admin/article-pipeline/{stage}",
    response_model=PipelineColumn,
    operation_id="admin_article_pipeline_column",
)
async def column(
    stage: Stage,
    factory: DBFactory,
    analytics: Analytics,
    q: Annotated[DatabaseText | None, Query(max_length=200)] = None,
    source_id: uuid.UUID | None = None,
    needs_attention: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
    group_by: Literal["source"] | None = None,
    source_group: uuid.UUID | Literal["unknown"] | None = None,
) -> PipelineColumn:
    needle = await run_database(factory, _normalized_search, q)
    try:
        result = await analytics.article_board(
            q=q,
            normalized_q=needle,
            source_id=source_id,
            needs_attention=needs_attention,
            limit=limit,
            stage=stage,
            cursor=cursor,
            group_by=group_by,
            source_group=source_group,
        )
    except AnalyticsUnavailable as error:
        raise preparing() from error
    return (await run_database(factory, _hydrate_clocks, result)).columns[0]


def _detail(session: Session, article: Article) -> PipelineDetail:
    batch = _batch_facts(session, [article])
    facts = _article_facts(batch, article.id)
    taxonomy = catalog(session)
    inputs: ProcessingInputs = {**facts, "taxonomy": taxonomy}
    readiness = derive_publication_readiness(article, **inputs)
    processing = retry_readiness(article, readiness, facts, taxonomy=taxonomy)
    checks = [
        PipelineCheck(
            code=code,
            label={
                "enrichment": "Source evidence",
                "analysis": "Current AI analysis",
                "images": "Article images",
            }[code],
            status="passed"
            if check.status == "complete" or (code == "images" and check.status == "unavailable")
            else "blocked"
            if check.status in {"failed", "stale", "unavailable", "disabled"}
            else "pending",
            detail=(
                "No article image found; the reader fallback is available"
                if code == "images" and check.status == "unavailable"
                else check.error or check.outcome or check.status.replace("_", " ")
            ),
        )
        for code, check in readiness.checks.items()
    ]
    if readiness.stage not in {"published", "rejected"}:
        requirements = (
            ("identifying_title", "Identifying title", {"missing_title", "unidentifiable_title"}),
            ("overview", "Article overview", {"missing_ai_summary"}),
            ("description", "Search description", {"missing_ai_description"}),
            ("language", "Known language", {"unknown_language"}),
            ("primary_topic", "Active primary topic", {"missing_active_primary_topic"}),
            (
                "relevance",
                "Relevant article",
                {"developer_relevance_unresolved", "not_substantive_article"},
            ),
            ("source", "Approved source", {"no_approved_source"}),
            (
                "metadata",
                "Valid URL and content format",
                {"invalid_canonical_url", "unknown_content_type", "unknown_content_format"},
            ),
            ("approval", "Editorial approval", {"not_approved"}),
        )
        for code, label, reasons in requirements:
            missing = reasons.intersection(readiness.blockers)
            checks.append(
                PipelineCheck(
                    code=code,
                    label=label,
                    status="pending"
                    if missing == {"not_approved"}
                    else "blocked"
                    if missing
                    else "passed",
                    detail="; ".join(reason.replace("_", " ") for reason in sorted(missing))
                    if missing
                    else "Complete",
                )
            )
        extra = {
            "content_date_deferred": (
                "Content date",
                "The source publication date is outside the configured AI content window",
            ),
            "insufficient_source_text": (
                "Source text",
                "No readable source text is available. "
                "Fetch the source again or inspect its evidence.",
            ),
            "insufficient_analysis_evidence": (
                "Analysis evidence",
                "The completed analysis could not establish the required evidence. "
                "Inspect the source and classification.",
            ),
            "ai_not_configured": (
                "AI configuration",
                "Configure and enable AI to produce the required article content",
            ),
            "paywalled_content": (
                "Source access",
                "The publisher gates this article behind a paywall",
            ),
        }
        for code, (label, description) in extra.items():
            if code in readiness.blockers:
                checks.append(
                    PipelineCheck(code=code, label=label, status="blocked", detail=description)
                )
    actions = _actions(readiness)
    labels = {
        "ingested": "Start enrichment",
        "enrichment": "Run enrichment",
        "analysis": "Run current analysis",
        "images": "Process images",
    }
    if readiness.stage == "enrichment" and "insufficient_source_text" in readiness.blockers:
        labels["enrichment"] = "Fetch source again"
    return PipelineDetail(
        article_id=article.id,
        editorial_revision=article.editorial_revision,
        stage=readiness.stage,
        state=readiness.state,
        checks=checks,
        blockers=readiness.blockers,
        can_publish=readiness.stage == "ready" and not readiness.blockers,
        jobs=_jobs(
            facts,
            allow_retry=readiness.stage not in {"published", "rejected"},
            readiness=processing,
        ),
        allowed_actions=actions,
        advance_label=labels.get(readiness.stage) if actions else None,
        analysis_steps=_analysis_steps(facts["analysis"]),
        retry_targets=processing_retry_targets(article, processing, facts),
    )


@router.get(
    "/v1/admin/articles/{article_id}/pipeline",
    response_model=PipelineDetail,
    operation_id="admin_article_pipeline_detail",
)
def detail(article_id: uuid.UUID, session: DB) -> PipelineDetail:
    result = _detail(session, record(session, Article, article_id))
    session.close()
    return result


@router.post(
    "/v1/admin/articles/{article_id}/pipeline/advance",
    response_model=PipelineDetail,
    operation_id="admin_article_pipeline_advance",
)
def advance(
    article_id: uuid.UUID, body: AdvancePipeline, session: DB, admin: Admin
) -> PipelineDetail:
    approved_sources(session, article_id, lock=True)
    article = record(session, Article, article_id, lock=True)
    if article.editorial_revision != body.expected_revision:
        raise OperationConflict("Article changed; reload before resuming processing")
    readiness = publication_readiness(session, article)
    if "advance" not in _actions(readiness):
        raise OperationConflict("No processing step can be resumed; refresh the readiness checks")
    if readiness.stage == "enrichment" and "insufficient_source_text" in readiness.blockers:
        request_article_enrichment(session, article_id)
    else:
        reconcile_processing(session, article)
    session.commit()
    result = _detail(session, article)
    session.close()
    return result


@router.post(
    "/v1/admin/articles/{article_id}/pipeline/retry",
    response_model=PipelineDetail,
    operation_id="admin_article_pipeline_retry",
)
def retry(article_id: uuid.UUID, body: RetryPipeline, session: DB, admin: Admin) -> PipelineDetail:
    article = request_processing_retry(
        session,
        article_id,
        target=body.target,
        expected_revision=body.expected_revision,
        expected_job_id=body.expected_job_id,
        actor=actor(admin),
    )
    session.commit()
    result = _detail(session, article)
    session.close()
    return result
