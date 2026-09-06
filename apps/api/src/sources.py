import logging
import uuid

from devfeed_core import services
from devfeed_core.models import Source
from devfeed_core.schemas import (
    SourceCreate,
    SourcePublicOut,
    SourceSubmission,
    SourceSubmissionOut,
)
from devfeed_core.source_types import SourceType
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1/sources", tags=["sources"], route_class=CachedReadRoute)
logger = logging.getLogger(__name__)


@router.get("", response_model=list[SourcePublicOut])
def sources(
    session: DB,
    enabled: bool | None = None,
    source_type: SourceType | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    statement = select(Source).where(Source.approval_status == "approved")
    if source_type is not None:
        statement = statement.where(Source.source_type == source_type)
    if enabled is not None:
        statement = statement.where(Source.enabled == enabled)
    return session.scalars(
        statement.order_by(Source.name, Source.id).offset(offset).limit(limit)
    ).all()


@router.post("", response_model=SourceSubmissionOut, status_code=201)
def create_source(body: SourceSubmission, session: DB):
    validated = services.validate_source(SourceCreate.model_validate(body.model_dump()))
    source = services.create_source(session, validated)
    session.commit()
    logger.info(
        "source_submitted",
        extra={
            "source_id": source.id,
            "source_type": source.source_type,
            "enabled": source.enabled,
            "approval_status": source.approval_status,
        },
    )
    return source


@router.get("/{source_id}", response_model=SourcePublicOut)
def source_detail(source_id: uuid.UUID, session: DB):
    source = session.get(Source, source_id)
    if source is None or source.approval_status != "approved":
        raise HTTPException(404, "Source not found")
    return source
