"""ClickHouse source boards and transactional lifecycle detail/actions."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from devfeed_core.analytics import AnalyticsUnavailable
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
    advance_source_processing,
    retry_source_processing,
    source_readiness_batch,
)
from devfeed_core.source_types import SourceType
from devfeed_core.text_search import text_search
from devfeed_http.dependencies import run_database
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from devfeed_admin_api.analytics_current import preparing
from devfeed_admin_api.analytics_runtime import AnalyticsRuntime, get_analytics
from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB, DBFactory

router = APIRouter(
    prefix="/v1/admin/source-pipeline",
    tags=["admin-sources"],
    dependencies=[Depends(require_admin)],
)
MAX_CURSOR = 2**31 - 1
Analytics = Annotated[AnalyticsRuntime, Depends(get_analytics)]


class SourcePipelineQuery:
    def __init__(
        self,
        q: DatabaseText = Query("", max_length=200),
        source_type: SourceType | None = None,
        needs_attention: bool = False,
        terminal_since: datetime | None = None,
        limit: int = Query(20, ge=1, le=50),
    ):
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


def _detail(session, facts: SourceLifecycleFacts) -> SourcePipelineDetail:
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
            for stage, job in facts.jobs.items()
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


def _matching_sources(session, query):
    """Search private identifiers without exporting credentials to ClickHouse.

    Only identity UUIDs cross this boundary. Lifecycle filters/counts and paging
    still use the frozen analytical generation; no evidence or jobs are loaded.
    """
    if not query.q:
        return None
    session.execute(select(func.set_config("statement_timeout", "2000", True)))
    return {
        "source": list(
            session.scalars(
                select(Source.id).where(
                    text_search(
                        query.q,
                        Source.name,
                        Source.website_url,
                        Source.feed_url,
                    )
                )
            )
        ),
        "candidate": list(
            session.scalars(
                select(SourceCandidate.id).where(
                    SourceCandidate.source_id.is_(None),
                    text_search(query.q, SourceCandidate.name, SourceCandidate.identity_url),
                )
            )
        ),
    }


def _hydrate_uris(session, result):
    items = [item for column in result.columns for item in column.items]
    source_ids = [item.id for item in items if item.kind == "source"]
    candidate_ids = [item.id for item in items if item.kind == "candidate"]
    sources = (
        {
            row.id: row
            for row in session.execute(
                select(
                    Source.id,
                    Source.website_url,
                    Source.feed_url,
                ).where(Source.id.in_(source_ids))
            )
        }
        if source_ids
        else {}
    )
    candidates = (
        dict(
            session.execute(
                select(SourceCandidate.id, SourceCandidate.identity_url).where(
                    SourceCandidate.id.in_(candidate_ids)
                )
            ).all()
        )
        if candidate_ids
        else {}
    )
    for item in items:
        if item.kind == "source" and (row := sources.get(item.id)) is not None:
            item.website_url, item.feed_url = row.website_url, row.feed_url
        elif item.kind == "candidate" and item.id in candidates:
            item.website_url = candidates[item.id]
    return result


async def _read_board(factory, analytics, query, **parameters):
    identifiers = await run_database(factory, _matching_sources, query)
    try:
        result = await analytics.source_board(query, matching_ids=identifiers, **parameters)
    except AnalyticsUnavailable as error:
        raise preparing() from error
    return await run_database(factory, _hydrate_uris, result)


@router.get("", response_model=SourcePipelineBoard, operation_id="admin_source_pipeline")
async def board(factory: DBFactory, query: PipelineQuery, analytics: Analytics):
    return await _read_board(factory, analytics, query)


@router.get(
    "/columns/{stage}",
    response_model=SourcePipelineColumn,
    operation_id="admin_source_pipeline_column",
)
async def column(
    stage: SourcePipelineStage,
    factory: DBFactory,
    query: PipelineQuery,
    analytics: Analytics,
    cursor: int = Query(0, ge=0, le=MAX_CURSOR),
):
    return (await _read_board(factory, analytics, query, stage=stage, cursor=cursor)).columns[0]


@router.get(
    "/items/{kind}/{identifier}",
    response_model=SourcePipelineDetail,
    operation_id="admin_source_pipeline_detail",
)
def detail(kind: SourcePipelineKind, identifier: uuid.UUID, session: DB):
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
):
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
):
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
