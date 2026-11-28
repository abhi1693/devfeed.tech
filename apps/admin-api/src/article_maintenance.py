"""Bounded private replacement preview and explicit editorial consent."""

from __future__ import annotations

import uuid
from typing import Literal, Self, cast

from devfeed_core.analytics_article_cards import _jobs, _PipelineMarkers
from devfeed_core.analytics_card_types import MaintenanceDetail
from devfeed_core.article_refresh import ACTIVE_REFRESH_STATES, review_refresh
from devfeed_core.article_refresh_processing import candidate_view, refresh_facts
from devfeed_core.article_refresh_types import RefreshDraft
from devfeed_core.job_retries import retry_candidate
from devfeed_core.models import Article, ArticleRefresh
from devfeed_core.schemas import InputModel, ReviewNote
from devfeed_http.pagination import record
from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from devfeed_admin_api.auth import Admin, actor, require_admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(tags=["admin-articles"], dependencies=[Depends(require_admin)])


class ReviewMaintenance(InputModel):
    expected_version: int = Field(ge=1)
    action: Literal["approve", "reject"]
    note: ReviewNote | None = None

    @model_validator(mode="after")
    def discard_reason(self) -> Self:
        if self.action == "reject" and self.note is None:
            raise ValueError("Discarding a replacement requires a reason")
        return self


def maintenance_detail(
    session: Session, article: Article, owner: ArticleRefresh | None = None
) -> MaintenanceDetail | None:
    if (
        owner is None
        and article.published_to_feed_at is None
        and article.publication_status != "published"
    ):
        return None
    if owner is None:
        owner = session.scalar(
            select(ArticleRefresh).where(
                ArticleRefresh.article_id == article.id,
                ArticleRefresh.status.in_(ACTIVE_REFRESH_STATES),
            )
        )
    if owner is None:
        return None
    draft = RefreshDraft.model_validate(owner.payload)
    facts = refresh_facts(session, owner, draft, lightweight=True)
    view = candidate_view(session, article, owner, draft, facts)
    for lane in ("enrichment", "analysis", "discovery", "storage"):
        job = facts[lane]
        if job is not None and job.status == "failed":
            cast(_PipelineMarkers, job)._pipeline_retryable = bool(
                session.scalar(
                    select(type(job).id).where(type(job).id == job.id, retry_candidate(type(job)))
                )
            )
    jobs = _jobs(facts)
    selected = {"enrichment": jobs.enrichment, "analysis": jobs.analysis, "images": jobs.images}
    scope = {
        "enrichment": ("enrichment", "analysis", "images"),
        "analysis": ("analysis", "images"),
        "images": ("images",),
    }[owner.target]
    retry_targets = [
        name
        for name in scope
        if (task := selected[name]) is not None
        and (
            task.retryable
            or task.status == "succeeded"
            and task.outcome in {"insufficient_evidence", "superseded", "published_preserved"}
        )
    ]
    labels = {
        "enrichment": "Publisher enrichment",
        "analysis": "Analysis",
        "images": "Image processing",
    }
    reasons = [
        f"{labels[name]} {task.status}."
        for name, task in selected.items()
        if task is not None and task.status in {"queued", "running", "failed"}
    ]
    enrichment = facts["enrichment"]
    if enrichment is not None and (enrichment.result or {}).get("paywalled") is True:
        reasons.append(
            "Publisher access is restricted. Inspect the source or discard the replacement."
        )
    analysis = facts["analysis"]
    if analysis is not None and analysis.outcome == "insufficient_evidence":
        reasons.append(
            "Analysis needs more source evidence. Correct the input and retry, "
            "or discard the replacement."
        )
    current = (
        article.publication_status == "published"
        and article.review_status == "approved"
        and article.editorial_revision == owner.base_revision
        and any(origin.source.approval_status == "approved" for origin in article.origins)
    )
    if not current:
        reasons.append(
            "The approved publication or source authority changed. Discard this replacement."
        )
    ready = current and owner.status == "review" and not reasons
    actions = ["reject"] if owner.status in ACTIVE_REFRESH_STATES else []
    if ready:
        actions.insert(0, "approve")
        reasons.append("The replacement is ready for an editorial decision.")
    elif not reasons and owner.status == "active":
        reasons.append("The replacement is waiting for final publication checks.")
    return MaintenanceDetail.model_validate(
        {
            "id": owner.id,
            "article_id": article.id,
            "target": owner.target,
            "status": owner.status,
            "version": owner.version,
            "created_at": owner.created_at,
            "completed_at": owner.completed_at,
            "ready": ready,
            "reasons": reasons,
            "allowed_actions": actions,
            "jobs": jobs,
            "retry_targets": retry_targets,
            "replacement": {
                "title": view.ai_title or view.title,
                "summary": (view.ai_summary or view.summary)[:6000],
                "description": view.ai_description,
                "image_url": view.image_url,
                "language": view.language,
                "content_type": view.content_type,
                "content_format": view.content_format,
                "topics": [
                    {"id": link.topic_id, "name": link.topic.name, "role": link.role}
                    for link in view.topic_links
                ],
                "tags": [{"id": tag.id, "name": tag.name} for tag in view.tags],
            },
        }
    )


@router.get(
    "/v1/admin/articles/{article_id}/maintenance",
    response_model=MaintenanceDetail | None,
    operation_id="adminArticleMaintenanceDetail",
)
def detail(article_id: uuid.UUID, session: DB) -> MaintenanceDetail | None:
    result = maintenance_detail(session, record(session, Article, article_id))
    session.close()
    return result


@router.post(
    "/v1/admin/articles/{article_id}/maintenance/{refresh_id}/review",
    response_model=MaintenanceDetail,
    operation_id="adminArticleMaintenanceReview",
)
def review(
    article_id: uuid.UUID,
    refresh_id: uuid.UUID,
    body: ReviewMaintenance,
    session: DB,
    admin: Admin,
) -> MaintenanceDetail:
    owner = review_refresh(
        session,
        article_id,
        refresh_id,
        body.expected_version,
        action=body.action,
        actor=actor(admin),
        note=body.note,
    )
    session.commit()
    result = maintenance_detail(session, record(session, Article, article_id), owner)
    assert result is not None
    session.close()
    return result
