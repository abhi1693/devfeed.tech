"""Admin-only catalog. No public serving endpoint or automatic admission."""

import uuid

from devfeed_core.config import get_settings
from devfeed_core.models import Article, PartnerEvaluation, PartnerProduct, utcnow
from devfeed_core.partner_tools import (
    EvaluationInput,
    EvaluationOut,
    MatchReview,
    NickImport,
    ProductImport,
    ProductOut,
    ProductReview,
    article_snapshot,
    eligible,
    product_snapshot,
    product_view,
    snapshot_current,
)
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from devfeed_admin_api.auth import Admin, actor, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, record

router = APIRouter(
    prefix="/v1/admin/partner-tools",
    tags=["admin-partner-tools"],
    dependencies=[Depends(require_admin)],
)


def event(admin, action, note=""):
    return {"actor": actor(admin), "action": action, "note": note, "at": utcnow().isoformat()}


def locked_product(session, identifier):
    value = session.scalar(
        select(PartnerProduct).where(PartnerProduct.id == identifier).with_for_update()
    )
    if value is None:
        raise HTTPException(404, "Product not found")
    return value


@router.get("", response_model=Page[ProductOut], operation_id="admin_partner_tools_list")
def listing(session: DB, query: Listing):
    statement = select(PartnerProduct)
    if query.q:
        statement = statement.where(PartnerProduct.name.ilike(f"%{query.q}%"))
    page = paginate(
        session,
        statement,
        query,
        {"name": PartnerProduct.name, "updated_at": PartnerProduct.updated_at},
        "-updated_at",
    )
    return {**page, "items": [product_view(p) for p in page["items"]]}


@router.post("/import", response_model=list[ProductOut], operation_id="admin_partner_tools_import")
def import_products(body: ProductImport, session: DB, admin: Admin):
    return import_batch(body, session, admin)


def import_batch(body, session, admin, *, preserve_evidence=False):
    identities = [(p.provider, p.external_id) for p in body.products]
    if len(identities) != len(set(identities)):
        raise HTTPException(422, "Duplicate product identities in import")
    products = []
    # Stable lock order prevents two overlapping imports deadlocking.
    for item in sorted(body.products, key=lambda p: (p.provider, p.external_id)):
        values = item.model_dump(mode="json")
        identifier = session.scalar(
            insert(PartnerProduct)
            .values(**values, reviews=[event(admin, "imported")])
            .on_conflict_do_nothing(constraint="uq_partner_identity")
            .returning(PartnerProduct.id)
        )
        product = session.scalar(
            select(PartnerProduct)
            .where(
                PartnerProduct.provider == item.provider,
                PartnerProduct.external_id == item.external_id,
            )
            .with_for_update()
        )
        assert product is not None
        if identifier is None and preserve_evidence:
            values["evidence"] = product.evidence
            values["technologies"] = product.technologies
        if identifier is None and any(
            getattr(product, key) != value for key, value in values.items()
        ):
            for key, value in values.items():
                setattr(product, key, value)
            product.revision += 1
            if product.status not in {"withdrawn", "rejected", "paused"}:
                product.status = "pending"
            product.verified_at = None
            product.updated_at = utcnow()
            product.reviews = [*product.reviews, event(admin, "updated", "Approval invalidated")]
        products.append(product)
    session.commit()
    return [product_view(p) for p in products]


@router.post(
    "/import/nick", response_model=list[ProductOut], operation_id="admin_partner_tools_import_nick"
)
def import_nick(body: NickImport, session: DB, admin: Admin):
    from pydantic import ValidationError

    try:
        products = [entry.product() for entry in body.products]
    except ValidationError as exc:
        raise HTTPException(
            422, "Each product needs a description or tagline of at least 10 characters"
        ) from exc
    # Preserve local evidence under the same product lock used for updates.
    return import_batch(ProductImport(products=products), session, admin, preserve_evidence=True)


@router.post(
    "/{product_id}/review", response_model=ProductOut, operation_id="admin_partner_tools_review"
)
def review_product(product_id: uuid.UUID, body: ProductReview, session: DB, admin: Admin):
    product = locked_product(session, product_id)
    if product.revision != body.expected_revision:
        raise HTTPException(409, "Product changed since you opened it; refresh and review again")
    if body.status == "approved" and (
        not body.evidence_checked
        or not body.display_rights_confirmed
        or not product.evidence
        or not product.technologies
    ):
        raise HTTPException(
            422,
            "Approval requires technologies, capability evidence, "
            "checked evidence and display rights",
        )
    product.status = body.status
    product.verified_at = utcnow() if body.status == "approved" else None
    product.revision += 1
    product.updated_at = utcnow()
    product.reviews = [
        *product.reviews,
        {
            **event(admin, body.status, body.note),
            "evidence_checked": body.evidence_checked,
            "display_rights_confirmed": body.display_rights_confirmed,
        },
    ]
    session.commit()
    return product_view(product)


def evaluation_view(session, job):
    return {
        **{
            key: getattr(job, key)
            for key in (
                "id",
                "product_id",
                "status",
                "created_at",
                "finished_at",
                "error",
                "snapshot",
                "result",
                "reviews",
            )
        },
        "result": job.result or None,
        "current": snapshot_current(session, job),
    }


@router.post(
    "/{product_id}/evaluations",
    response_model=EvaluationOut,
    status_code=202,
    operation_id="admin_partner_tools_evaluate",
)
def evaluate(product_id: uuid.UUID, body: EvaluationInput, session: DB, admin: Admin):
    if not get_settings().ai_enabled:
        raise HTTPException(409, "Enable AI to run a shadow evaluation")
    product = locked_product(session, product_id)
    if not eligible(product):
        raise HTTPException(409, "Approve and verify this product before evaluation")
    active = session.scalar(
        select(PartnerEvaluation).where(
            PartnerEvaluation.product_id == product_id,
            PartnerEvaluation.status.in_(["queued", "running"]),
        )
    )
    if active:
        raise HTTPException(409, "An evaluation is already queued or running")
    statement = select(Article).where(Article.publication_status == "published")
    if body.article_ids:
        statement = statement.where(Article.id.in_(body.article_ids))
    articles = session.scalars(
        statement.order_by(Article.feed_at.desc(), Article.id).limit(20)
    ).all()
    if not articles or (body.article_ids and len(articles) != len(set(body.article_ids))):
        raise HTTPException(422, "Choose existing published articles for evaluation")
    job = PartnerEvaluation(
        product_id=product.id,
        requested_by=actor(admin),
        snapshot={
            "version": "1",
            "product": product_snapshot(product),
            "articles": [article_snapshot(article) for article in articles],
        },
    )
    session.add(job)
    session.commit()
    return evaluation_view(session, job)


@router.get(
    "/{product_id}/evaluations",
    response_model=list[EvaluationOut],
    operation_id="admin_partner_tools_evaluations",
)
def evaluations(product_id: uuid.UUID, session: DB):
    record(session, PartnerProduct, product_id)
    jobs = session.scalars(
        select(PartnerEvaluation)
        .where(PartnerEvaluation.product_id == product_id)
        .order_by(PartnerEvaluation.created_at.desc())
        .limit(10)
    ).all()
    return [evaluation_view(session, job) for job in jobs]


@router.post(
    "/{product_id}/evaluations/{job_id}/review",
    response_model=EvaluationOut,
    operation_id="admin_partner_tools_match_review",
)
def review_match(
    product_id: uuid.UUID, job_id: uuid.UUID, body: MatchReview, session: DB, admin: Admin
):
    locked_product(session, product_id)
    job = session.scalar(
        select(PartnerEvaluation)
        .where(PartnerEvaluation.id == job_id, PartnerEvaluation.product_id == product_id)
        .with_for_update()
    )
    if job is None:
        raise HTTPException(404, "Evaluation not found")
    if job.status != "succeeded" or not snapshot_current(session, job):
        raise HTTPException(409, "Evaluation is incomplete or stale; run it again")
    if not any(d["article_id"] == str(body.article_id) for d in job.result.get("decisions", [])):
        raise HTTPException(422, "Article is not part of this evaluation")
    job.reviews = [
        *job.reviews,
        {**body.model_dump(mode="json"), **event(admin, "match_review", body.note)},
    ]
    session.commit()
    return evaluation_view(session, job)
