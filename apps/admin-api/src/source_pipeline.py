"""ClickHouse source boards and transactional lifecycle detail/actions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from devfeed_core.analytics_card_types import (
    SourcePipelineAdvance as SourcePipelineAdvance,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineBoard as SourcePipelineBoard,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineCard as SourcePipelineCard,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineCheck as SourcePipelineCheck,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineColumn as SourcePipelineColumn,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineDetail as SourcePipelineDetail,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineKind as SourcePipelineKind,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineProfile as SourcePipelineProfile,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineRetry as SourcePipelineRetry,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineRetryTarget as SourcePipelineRetryTarget,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineReview as SourcePipelineReview,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineStage as SourcePipelineStage,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineState as SourcePipelineState,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineTarget as SourcePipelineTarget,
)
from devfeed_core.analytics_card_types import (
    SourcePipelineTask as SourcePipelineTask,
)
from devfeed_core.analytics_source_cards import _activity as _activity
from devfeed_core.analytics_source_cards import _card as _card
from devfeed_core.models import Source, SourceCandidate, SourceReview, utcnow
from devfeed_core.schemas import DatabaseText
from devfeed_core.source_lifecycle import (
    SourceLifecycleFacts,
    _job_items,
    advance_source_processing,
    retry_source_processing,
    source_readiness_batch,
)
from devfeed_core.source_types import SourceType
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(
    prefix="/v1/admin/source-pipeline",
    tags=["admin-sources"],
    dependencies=[Depends(require_admin)],
)
MAX_CURSOR = 2**31 - 1


class SourcePipelineQuery:
    def __init__(
        self,
        q: DatabaseText = Query("", max_length=200),
        source_type: SourceType | None = None,
        needs_attention: bool = False,
        terminal_since: datetime | None = None,
        limit: int = Query(20, ge=1, le=50),
    ) -> None:
        now = utcnow()
        since = terminal_since or now - timedelta(days=7)
        if since.tzinfo is None:
            raise HTTPException(422, "terminal_since must include a timezone")
        since = since.astimezone(UTC)
        if since < now - timedelta(days=90) or since > now:
            raise HTTPException(422, "terminal_since must be within the last 90 days")
        self.q, self.source_type = q.strip(), source_type
        self.needs_attention, self.terminal_since, self.limit = needs_attention, since, limit


PipelineQuery = Annotated[SourcePipelineQuery, Depends()]


def _detail(session: Session, facts: SourceLifecycleFacts) -> SourcePipelineDetail:
    reviews = (
        list(
            session.scalars(
                select(SourceReview)
                .where(SourceReview.source_id == facts.source.id)
                .order_by(SourceReview.created_at.desc(), SourceReview.id)
                .limit(50)
            )
        )
        if facts.source is not None
        else []
    )
    return SourcePipelineDetail(
        item=_card(facts),
        checks=[
            SourcePipelineCheck(key=key, **jsonable_encoder(check))
            for key, check in facts.readiness.checks.items()
        ],
        blockers=facts.readiness.blockers,
        tasks=[
            SourcePipelineTask(
                stage=stage,
                job_id=job.id,
                **{
                    field: getattr(job, field)
                    for field in (
                        "status",
                        "attempts",
                        "created_at",
                        "available_at",
                        "finished_at",
                        "error",
                    )
                },
            )
            for stage, job in _job_items(facts.jobs)
            if job is not None
        ],
        profile=SourcePipelineProfile.model_validate(facts.source)
        if facts.source is not None
        else SourcePipelineProfile(),
        assessment=jsonable_encoder(facts.source.relevance_assessment)
        if facts.source is not None and facts.source.relevance_assessment
        else None,
        reviews=[SourcePipelineReview.model_validate(review) for review in reviews],
        candidate_ids=[candidate.id for candidate in facts.candidates],
    )


@router.get("", response_model=SourcePipelineBoard, operation_id="admin_source_pipeline")
async def board(query: PipelineQuery) -> SourcePipelineBoard:
    raise HTTPException(410, "This analytics report has been retired.")


@router.get(
    "/columns/{stage}",
    response_model=SourcePipelineColumn,
    operation_id="admin_source_pipeline_column",
)
async def column(
    stage: SourcePipelineStage,
    query: PipelineQuery,
    cursor: int = Query(0, ge=0, le=MAX_CURSOR),
) -> SourcePipelineColumn:
    raise HTTPException(410, "This analytics report has been retired.")


@router.get(
    "/items/{kind}/{identifier}",
    response_model=SourcePipelineDetail,
    operation_id="admin_source_pipeline_detail",
)
def detail(kind: SourcePipelineKind, identifier: uuid.UUID, session: DB) -> SourcePipelineDetail:
    from devfeed_core.services import RecordNotFound

    if kind == "candidate":
        candidate = session.get(SourceCandidate, identifier)
        if candidate is None:
            raise RecordNotFound("Source pipeline item not found; reload the board")
        if candidate.source_id is None:
            facts = source_readiness_batch(session, [], [candidate])
            result = _detail(session, facts[f"candidate:{identifier}"])
            session.close()
            return result
        identifier = candidate.source_id
    source = session.get(Source, identifier)
    if source is None:
        raise RecordNotFound("Source pipeline item not found; reload the board")
    facts = source_readiness_batch(session, [source])
    result = _detail(session, facts[f"source:{identifier}"])
    session.close()
    return result


@router.post(
    "/items/{kind}/{identifier}/advance",
    response_model=SourcePipelineDetail,
    operation_id="admin_source_pipeline_advance",
)
def advance(
    kind: SourcePipelineKind,
    identifier: uuid.UUID,
    body: SourcePipelineAdvance,
    session: DB,
    admin: Admin,
) -> SourcePipelineDetail:
    facts = advance_source_processing(
        session, kind, identifier, body.expected_snapshot, actor=admin.subject
    )
    result = _detail(session, facts)
    session.commit()
    session.close()
    return result


@router.post(
    "/items/{kind}/{identifier}/retry",
    response_model=SourcePipelineDetail,
    operation_id="admin_source_pipeline_retry",
)
def retry(
    kind: SourcePipelineKind,
    identifier: uuid.UUID,
    body: SourcePipelineRetry,
    session: DB,
    admin: Admin,
) -> SourcePipelineDetail:
    facts = retry_source_processing(
        session,
        kind,
        identifier,
        body.expected_snapshot,
        body.target,
        body.job_id,
        actor=admin.subject,
    )
    result = _detail(session, facts)
    session.commit()
    session.close()
    return result
