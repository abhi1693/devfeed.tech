"""Explain current pipeline blockers and provide revision-checked, targeted recovery."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from devfeed_core.analysis import request_analysis
from devfeed_core.analytics_blockers import (
    AutomationBlocker as AutomationBlocker,
)
from devfeed_core.analytics_blockers import (
    RecoveryTarget as RecoveryTarget,
)
from devfeed_core.analytics_blockers import (
    automation_blocker_rows as automation_blocker_rows,
)
from devfeed_core.analytics_blockers import (
    automation_blockers as automation_blockers,
)
from devfeed_core.article_jobs import approved_sources, request_article_enrichment
from devfeed_core.article_refresh_jobs import ordinary_job_condition
from devfeed_core.config import get_settings
from devfeed_core.json_types import JsonValue
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticlePublicationDecision,
    Source,
    SourcePublicationPolicyReview,
)
from devfeed_core.publication_policy import apply_publication_policy
from devfeed_core.schemas import InputModel, ORMModel
from devfeed_core.services import OperationConflict
from devfeed_core.text_search import text_search
from devfeed_core.topics import lock_topics
from devfeed_http.pagination import Listing, Page, PageData, paginate, record, require_record
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import (
    String,
    cast,
    select,
)

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(
    prefix="/v1/admin/automation", tags=["admin-automation"], dependencies=[Depends(require_admin)]
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
def publication_decisions(
    article_id: uuid.UUID, session: DB, query: Listing
) -> PageData[ArticlePublicationDecision]:
    require_record(session, Article, article_id)
    statement = select(ArticlePublicationDecision).where(
        ArticlePublicationDecision.article_id == article_id
    )
    if query.q:
        decision_text = cast(ArticlePublicationDecision.decision, String)
        statement = statement.where(text_search(query.q, decision_text))
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
def publication_policy_history(
    source_id: uuid.UUID, session: DB, query: Listing
) -> PageData[SourcePublicationPolicyReview]:
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
) -> RecoveryResult:
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
        requested_job = request_analysis(session, article_id)
        result = RecoveryResult(status="queued", job_id=requested_job.id)
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
            .where(
                ArticleAnalysisJob.article_id == article_id,
                ordinary_job_condition(ArticleAnalysisJob),
            )
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
