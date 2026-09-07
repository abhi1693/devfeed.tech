"""Authenticated source management; RSS validation and review remain core policy."""

import logging
import uuid
from dataclasses import asdict
from typing import Literal

from devfeed_core import services
from devfeed_core.feeds.validation import FeedValidationError
from devfeed_core.logging import log_identifier
from devfeed_core.models import (
    ArticleOrigin,
    IngestionJob,
    Source,
    SourceEnrichmentJob,
    SourceReview,
)
from devfeed_core.schemas import (
    InputModel,
    JobOut,
    Name,
    ReviewNote,
    SourceCreate,
    SourceDecision,
    SourceOut,
    SourcePatch,
    SourceProfileInput,
    SourceReviewOut,
)
from devfeed_core.source_preview import preview_source
from devfeed_core.source_types import SourceType
from devfeed_core.urls import validate_public_url
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field, field_validator
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, prohibit_references, record

router = APIRouter(
    prefix="/v1/admin/sources", tags=["admin-sources"], dependencies=[Depends(require_admin)]
)
logger = logging.getLogger(__name__)


class ReviewSource(InputModel):
    decision: Literal["approved", "rejected"]
    note: ReviewNote | None = None


class SourcePreviewRequest(InputModel):
    feed_url: str = Field(max_length=2048)
    source_type: SourceType

    _validate_url = field_validator("feed_url")(validate_public_url)


class SourcePreviewOut(SourceProfileInput):
    name: Name
    entries_seen: int
    entries_skipped: int
    warnings: list[str]


def reject_duplicate_feed(session, feed_url: str) -> None:
    if session.scalar(select(Source.id).where(Source.feed_url == feed_url)) is not None:
        raise HTTPException(
            409,
            detail=[
                {
                    "loc": ["body", "feed_url"],
                    "msg": (
                        "A source with this RSS / Atom URL already exists. "
                        "Edit the existing source instead."
                    ),
                    "type": "duplicate_feed_url",
                }
            ],
        )


@router.post("/preview", response_model=SourcePreviewOut, operation_id="admin_source_preview")
def preview(body: SourcePreviewRequest, session: DB):
    # End the read transaction before network I/O. No records/jobs are created.
    with session.begin():
        reject_duplicate_feed(session, body.feed_url)
    try:
        result = preview_source(body.feed_url, body.source_type)
    except FeedValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    return SourcePreviewOut(
        name=result.name,
        **asdict(result.profile),
        entries_seen=result.entries_seen,
        entries_skipped=result.entries_skipped,
        warnings=list(result.warnings),
    )


@router.get("", response_model=Page[SourceOut], operation_id="admin_sources_list")
def sources(
    session: DB,
    query: Listing,
    source_type: SourceType | None = None,
    approval_status: Literal["pending", "approved", "rejected"] | None = None,
    enabled: bool | None = None,
):
    statement = select(Source)
    if query.q:
        statement = statement.where(
            or_(
                Source.name.icontains(query.q, autoescape=True),
                Source.feed_url.icontains(query.q, autoescape=True),
            )
        )
    if source_type:
        statement = statement.where(Source.source_type == source_type)
    if approval_status:
        statement = statement.where(Source.approval_status == approval_status)
    if enabled is not None:
        statement = statement.where(Source.enabled == enabled)
    return paginate(
        session,
        statement,
        query,
        {
            "name": Source.name,
            "created_at": Source.created_at,
            "approval_status": Source.approval_status,
        },
    )


@router.get("/{source_id}", response_model=SourceOut, operation_id="admin_source_get")
def detail(source_id: uuid.UUID, session: DB):
    return record(session, Source, source_id)


@router.post("", response_model=SourceOut, status_code=201, operation_id="admin_source_create")
def create(body: SourceCreate, session: DB, admin: Admin):
    with session.begin():
        reject_duplicate_feed(session, body.feed_url)
    try:
        validated = services.validate_source(body)
    except FeedValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Preserve the existing channel contract: UI submissions use the API channel.
    # The authenticated administrator then makes an attributed approval decision.
    try:
        source = services.create_source(session, validated)
        services.review_source(
            session,
            source.id,
            SourceDecision(
                decision="approved", actor=admin.subject, note="Created by administrator"
            ),
            enable_on_approval=body.enabled,
        )
        session.commit()
    except IntegrityError:
        # The unique constraint also covers another submission winning after
        # preflight. Translate that race into the same field-level error.
        session.rollback()
        reject_duplicate_feed(session, body.feed_url)
        raise
    logger.info("source_created", extra={"source_id": source.id})
    return source


@router.patch("/{source_id}", response_model=SourceOut, operation_id="admin_source_update")
def update(source_id: uuid.UUID, body: SourcePatch, session: DB):
    value = services.update_source(session, source_id, body)
    session.commit()
    logger.info(
        "source_updated",
        extra={
            "source_id": log_identifier(source_id),
            "changed_fields": sorted(body.model_fields_set),
        },
    )
    return value


@router.post("/{source_id}/review", response_model=SourceOut, operation_id="admin_source_review")
def review(source_id: uuid.UUID, body: ReviewSource, session: DB, admin: Admin):
    value = services.review_source(
        session, source_id, SourceDecision(**body.model_dump(), actor=admin.subject)
    )
    session.commit()
    logger.info(
        "source_reviewed", extra={"source_id": log_identifier(source_id), "action": body.decision}
    )
    return value


@router.post(
    "/{source_id}/fetch", response_model=JobOut, status_code=202, operation_id="admin_source_fetch"
)
def fetch(source_id: uuid.UUID, session: DB):
    job = services.fetch_source(session, source_id)
    session.commit()
    logger.info(
        "source_fetch_requested", extra={"source_id": log_identifier(source_id), "job_id": job.id}
    )
    return job


@router.get(
    "/{source_id}/reviews",
    response_model=Page[SourceReviewOut],
    operation_id="admin_source_reviews",
)
def reviews(source_id: uuid.UUID, session: DB, query: Listing):
    record(session, Source, source_id)
    return paginate(
        session,
        select(SourceReview).where(SourceReview.source_id == source_id),
        query,
        {"created_at": SourceReview.created_at},
        "-created_at",
    )


@router.delete("/{source_id}", status_code=204, operation_id="admin_source_delete")
def remove(source_id: uuid.UUID, session: DB):
    record(session, Source, source_id, lock=True)
    prohibit_references(
        session,
        [
            ("articles", select(ArticleOrigin).where(ArticleOrigin.source_id == source_id)),
            (
                "active ingestion runs",
                select(IngestionJob).where(
                    IngestionJob.source_id == source_id,
                    IngestionJob.status.in_(["queued", "running"]),
                ),
            ),
            (
                "active profile jobs",
                select(SourceEnrichmentJob).where(
                    SourceEnrichmentJob.source_id == source_id,
                    SourceEnrichmentJob.status.in_(["queued", "running"]),
                ),
            ),
        ],
    )
    session.execute(delete(IngestionJob).where(IngestionJob.source_id == source_id))
    session.execute(delete(Source).where(Source.id == source_id))
    session.commit()
    logger.info("source_deleted", extra={"source_id": log_identifier(source_id)})
    return Response(status_code=204)
