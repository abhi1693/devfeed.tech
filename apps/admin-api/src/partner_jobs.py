"""Read-only administration pages for partner pipeline and evaluation runs."""

import uuid
from datetime import datetime
from typing import Literal

from devfeed_core.models import (
    PartnerEvaluation,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
)
from devfeed_core.partner_connections import PartnerJobOut
from devfeed_core.partner_tools import EvaluationOut
from devfeed_core.schemas import ORMModel
from fastapi import APIRouter, Depends
from sqlalchemy import String, cast, exists, or_, select
from sqlalchemy.orm import load_only

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, record
from devfeed_admin_api.partner_tools import evaluation_view

router = APIRouter(
    prefix="/v1/admin/partner-tools",
    tags=["admin-partner-tools"],
    dependencies=[Depends(require_admin)],
)
JobStatus = Literal["queued", "running", "succeeded", "failed"]


class PartnerPipelineOut(PartnerJobOut):
    available_at: datetime
    product_name: str | None


class PartnerEvaluationSummary(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    status: str
    attempts: int
    created_at: datetime
    available_at: datetime
    finished_at: datetime | None
    error: str | None
    matches: int
    articles: int


class PartnerEvaluationDetail(EvaluationOut):
    attempts: int
    available_at: datetime


def pipeline_views(session, jobs):
    names = (
        dict(
            session.execute(
                select(PartnerProduct.id, PartnerProduct.name).where(
                    PartnerProduct.id.in_([job.product_id for job in jobs if job.product_id])
                )
            ).all()
        )
        if jobs
        else {}
    )
    return [
        {
            **PartnerJobOut.model_validate(job).model_dump(),
            "available_at": job.available_at,
            "product_name": names.get(job.product_id),
        }
        for job in jobs
    ]


@router.get(
    "/pipeline", response_model=Page[PartnerPipelineOut], operation_id="admin_partner_pipeline_list"
)
def pipeline(
    session: DB,
    query: Listing,
    status: JobStatus | None = None,
    operation: Literal["sync", "sync_product", "assess"] | None = None,
    provider: str | None = None,
    product_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
):
    statement = (
        select(PartnerPipelineJob)
        .options(
            load_only(
                *(getattr(PartnerPipelineJob, key) for key in PartnerJobOut.model_fields),
                PartnerPipelineJob.available_at,
                raiseload=True,
            )
        )
        .outerjoin(PartnerProduct, PartnerProduct.id == PartnerPipelineJob.product_id)
    )
    for column, value in (
        (PartnerPipelineJob.status, status),
        (PartnerPipelineJob.operation, operation),
        (PartnerPipelineJob.product_id, product_id),
        (PartnerPipelineJob.parent_id, parent_id),
    ):
        if value is not None:
            statement = statement.where(column == value)
    if provider is not None:
        statement = statement.where(
            or_(
                PartnerPipelineJob.provider == provider,
                (PartnerPipelineJob.operation == "assess")
                & exists(
                    select(PartnerListing.id).where(
                        PartnerListing.provider == provider,
                        PartnerListing.product_id == PartnerPipelineJob.product_id,
                    )
                ),
            )
        )
    if query.q:
        statement = statement.where(
            or_(
                *[
                    column.icontains(query.q, autoescape=True)
                    for column in (
                        cast(PartnerPipelineJob.id, String),
                        PartnerPipelineJob.external_id,
                        PartnerPipelineJob.provider,
                        PartnerProduct.name,
                    )
                ]
            )
        )
    page = paginate(
        session,
        statement,
        query,
        {
            "created_at": PartnerPipelineJob.created_at,
            "status": PartnerPipelineJob.status,
            "attempts": PartnerPipelineJob.attempts,
        },
        "-created_at",
    )
    return {**page, "items": pipeline_views(session, page["items"])}


@router.get(
    "/pipeline/{job_id}",
    response_model=PartnerPipelineOut,
    operation_id="admin_partner_pipeline_get",
)
def pipeline_detail(job_id: uuid.UUID, session: DB):
    return pipeline_views(session, [record(session, PartnerPipelineJob, job_id)])[0]


@router.get(
    "/evaluations",
    response_model=Page[PartnerEvaluationSummary],
    operation_id="admin_partner_evaluations_list",
)
def evaluations(
    session: DB,
    query: Listing,
    status: JobStatus | None = None,
    product_id: uuid.UUID | None = None,
    provider: str | None = None,
):
    # Lists omit article snapshots; the detail endpoint supplies the full evidence.
    fields = (
        PartnerEvaluation.id,
        PartnerEvaluation.product_id,
        PartnerEvaluation.status,
        PartnerEvaluation.attempts,
        PartnerEvaluation.created_at,
        PartnerEvaluation.available_at,
        PartnerEvaluation.finished_at,
        PartnerEvaluation.error,
        PartnerEvaluation.result,
    )
    name = PartnerEvaluation.snapshot["product"]["name"].astext
    statement = select(PartnerEvaluation).options(load_only(*fields, raiseload=True))
    if status is not None:
        statement = statement.where(PartnerEvaluation.status == status)
    if product_id is not None:
        statement = statement.where(PartnerEvaluation.product_id == product_id)
    if provider is not None:
        statement = statement.where(
            exists(
                select(PartnerListing.id).where(
                    PartnerListing.product_id == PartnerEvaluation.product_id,
                    PartnerListing.provider == provider,
                )
            )
        )
    if query.q:
        statement = statement.where(
            or_(
                cast(PartnerEvaluation.id, String).icontains(query.q, autoescape=True),
                name.icontains(query.q, autoescape=True),
            )
        )
    page = paginate(
        session,
        statement,
        query,
        {
            "created_at": PartnerEvaluation.created_at,
            "status": PartnerEvaluation.status,
            "attempts": PartnerEvaluation.attempts,
        },
        "-created_at",
    )
    names: dict[uuid.UUID, str | None] = (
        {
            identifier: product_name
            for identifier, product_name in session.execute(
                select(PartnerEvaluation.id, name).where(
                    PartnerEvaluation.id.in_([job.id for job in page["items"]])
                )
            ).all()
        }
        if page["items"]
        else {}
    )
    return {
        **page,
        "items": [
            {
                **{field.key: getattr(job, field.key) for field in fields if field.key != "result"},
                "product_name": names.get(job.id) or "Product",
                "matches": sum(
                    bool(d.get("relevant")) for d in (job.result or {}).get("decisions", [])
                ),
                "articles": len((job.result or {}).get("decisions", [])),
            }
            for job in page["items"]
        ],
    }


@router.get(
    "/evaluations/{job_id}",
    response_model=PartnerEvaluationDetail,
    operation_id="admin_partner_evaluation_get",
)
def evaluation_detail(job_id: uuid.UUID, session: DB):
    job = record(session, PartnerEvaluation, job_id)
    return {
        **evaluation_view(session, job),
        "attempts": job.attempts,
        "available_at": job.available_at,
    }
