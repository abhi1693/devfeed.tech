"""Admin imports enter discovery, never the trusted source-create approval path."""

import hashlib
import uuid
from datetime import datetime
from typing import Literal

from devfeed_core import discovery
from devfeed_core.config import get_settings
from devfeed_core.discovery_crawler import CrawlSession
from devfeed_core.discovery_import import parse_import
from devfeed_core.discovery_quality import Quality
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import Source, SourceCandidate
from devfeed_core.schemas import InputModel, ORMModel, ReviewNote
from devfeed_core.urls import validate_public_url
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(
    prefix="/v1/admin/source-imports", tags=["admin-sources"], dependencies=[Depends(require_admin)]
)


class SourceImportRequest(InputModel):
    format: Literal["opml", "urls", "json", "markdown"]
    url: str | None = Field(default=None, max_length=2048)
    content: str | None = Field(default=None, max_length=1_000_000)
    name: str = Field(default="Admin import", min_length=1, max_length=200)

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        return validate_public_url(value) if value is not None else None

    @model_validator(mode="after")
    def one_input(self):
        if bool(self.url) == bool(self.content):
            raise ValueError("Provide either a collection URL or file/pasted content")
        return self


class SourceImportResult(ORMModel):
    created: int
    existing: int
    total: int


class ImportCandidateOut(ORMModel):
    id: uuid.UUID
    name: str
    identity_url: str
    status: str
    approval_status: str
    selected_feed_id: uuid.UUID | None
    source_id: uuid.UUID | None
    updated_at: datetime


class ImportCandidatePage(ORMModel):
    items: list[ImportCandidateOut]
    total: int
    offset: int
    limit: int


class ImportSample(ORMModel):
    title: str
    summary: str
    url: str
    published_at: str | None = None


class ImportFeedEvidence(ORMModel):
    title: str | None = None
    usable_entries: int = 0
    publisher_type: str = "unknown"
    publisher_ratio: float | None = None
    recent: bool = False
    sample: list[ImportSample] = Field(default_factory=list)


class ImportFeedOut(ORMModel):
    id: uuid.UUID
    url: str
    evidence: ImportFeedEvidence
    checked_at: datetime


class ImportAssessmentEvidence(ORMModel):
    score: float | None = None
    recommendation: str | None = None
    components: dict[str, float | None] = Field(default_factory=dict)
    model_output: Quality | None = None
    sample: list[ImportSample] = Field(default_factory=list)
    decision: str | None = None
    actor: str | None = None
    reason: str | None = None


class ImportAssessmentOut(ORMModel):
    id: uuid.UUID
    version: str
    feed_id: uuid.UUID | None
    created_at: datetime
    evidence: ImportAssessmentEvidence


class ImportJobOut(ORMModel):
    id: uuid.UUID
    stage: str
    status: str
    attempts: int
    error: str | None
    available_at: datetime
    lease_until: datetime | None


class ImportProvenanceOut(ORMModel):
    origin: str
    created_at: datetime


class ImportCandidateDetail(ImportCandidateOut):
    feeds: list[ImportFeedOut]
    assessments: list[ImportAssessmentOut]
    jobs: list[ImportJobOut]
    provenance: list[ImportProvenanceOut]


class ImportAction(InputModel):
    action: Literal["discover", "assess", "select", "approve", "reject"]
    feed_id: uuid.UUID | None = None
    note: ReviewNote | None = None


def require_ai():
    if not get_settings().ai_enabled:
        raise HTTPException(409, "AI is not enabled. Choose manual review or enable AI first.")


@router.post(
    "",
    response_model=SourceImportResult,
    status_code=201,
    operation_id="admin_source_import_submit",
)
def submit(body: SourceImportRequest, admin: Admin):
    try:
        if body.url:
            content = CrawlSession().get(body.url).body
        else:
            content = (body.content or "").encode("utf-8")
        hints = parse_import(content, body.format)
    except FeedError as exc:
        raise HTTPException(422, f"Collection could not be fetched ({exc.reason}).") from exc
    except ValueError as exc:
        raise HTTPException(
            422, "Invalid collection. Check the format, public URLs, and 1 MB / 1,000-entry limits."
        ) from exc
    if not hints:
        raise HTTPException(422, "No publisher entries found in the collection.")
    result = discovery.import_publishers(
        hints,
        f"admin:{admin.subject}:{body.url or body.name}"[:2048],
        checksum=hashlib.sha256(content).hexdigest(),
        background=True,
        assess_after=get_settings().full_automation,
    )
    total = len(result["candidates"])
    return SourceImportResult(
        created=result["created"], existing=total - result["created"], total=total
    )


@router.get("", response_model=ImportCandidatePage, operation_id="admin_source_import_candidates")
def candidates(
    session: DB,
    limit: int = 25,
    offset: int = 0,
    pending_only: bool = False,
    q: str | None = None,
    status: Literal[
        "pending", "ready", "unresolved", "retry_wait", "rejected", "admitted", "linked"
    ]
    | None = None,
):
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(422, "Invalid pagination")
    approval = func.coalesce(Source.approval_status, SourceCandidate.approval_status)
    query = select(SourceCandidate, approval.label("approval_status")).outerjoin(
        Source, Source.id == SourceCandidate.source_id
    )
    if pending_only:
        query = query.where(approval == "pending")
    if q:
        query = query.where(
            SourceCandidate.name.icontains(q, autoescape=True)
            | SourceCandidate.identity_url.icontains(q, autoescape=True)
        )
    if status:
        query = query.where(SourceCandidate.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(
        session.execute(
            query.order_by(SourceCandidate.created_at.desc(), SourceCandidate.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return ImportCandidatePage(
        items=[
            ImportCandidateOut.model_validate(row).model_copy(update={"approval_status": approval})
            for row, approval in items
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{candidate_id}",
    response_model=ImportCandidateDetail,
    operation_id="admin_source_import_candidate",
)
def detail(candidate_id: uuid.UUID):
    return discovery.show(candidate_id)


@router.post(
    "/{candidate_id}/review",
    response_model=ImportCandidateDetail,
    operation_id="admin_source_import_review",
)
def review(candidate_id: uuid.UUID, body: ImportAction, admin: Admin):
    try:
        if body.action == "select":
            if not body.feed_id:
                raise HTTPException(422, "Select a feed")
            discovery.select_feed(candidate_id, body.feed_id)
        elif body.action in {"approve", "reject"}:
            if not body.note:
                raise HTTPException(422, "Add your review notes before deciding")
            if body.action == "approve":
                discovery.approve(candidate_id, admin.subject, body.note)
            else:
                discovery.reject(candidate_id, admin.subject, body.note)
        else:
            if body.action == "assess":
                require_ai()
            discovery.request(candidate_id, body.action, background=True)
    except FeedError as exc:
        raise HTTPException(422, f"Feed could not be validated ({exc.reason}).") from exc
    return discovery.show(candidate_id)


class DeleteImportedPublishers(InputModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class DeletedImportedPublishers(ORMModel):
    deleted: int


@router.delete(
    "/{candidate_id}",
    response_model=DeletedImportedPublishers,
    operation_id="admin_source_import_delete",
)
def delete_import(candidate_id: uuid.UUID):
    return discovery.delete_candidates([candidate_id])


@router.post(
    "/bulk-delete",
    response_model=DeletedImportedPublishers,
    operation_id="admin_source_import_bulk_delete",
)
def bulk_delete_imports(body: DeleteImportedPublishers):
    return discovery.delete_candidates(body.ids)
