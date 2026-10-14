"""Private explicit product inventory; imports and qualification remain independent."""

import hashlib
from typing import Literal

from devfeed_core.job_lifecycle import fail_or_retry
from devfeed_core.models import (
    PartnerEvaluation,
    PartnerManualProduct,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerProductURL,
    utcnow,
)
from devfeed_core.partner_catalog import lock_catalog, refresh_availability
from devfeed_core.partner_connections import request_assessment
from devfeed_core.partner_promotions import (
    account_product_source,
    associated_products_query,
    request_promotion_enrollment,
)
from devfeed_core.rate_limits import RateLimitBudget, consume_rate_limits
from devfeed_core.schemas import InputModel
from devfeed_core.urls import canonicalize_url, fingerprint
from fastapi import HTTPException
from pydantic import Field, field_validator, model_validator
from redis.exceptions import RedisError
from sqlalchemy import select

from devfeed_http.partner_portal import WorkspaceInventoryProductOut, inventory_product_view

Pricing = Literal["free", "freemium", "paid", "unknown"]


def validate_manual_product_url(value):
    value = canonicalize_url(value)
    if not value.startswith("https://"):
        raise ValueError("Products require a public HTTPS website")
    return value


class ManualProductInput(InputModel):
    name: str = Field(min_length=1, max_length=200)
    product_url: str = Field(max_length=2048)
    description: str = Field(min_length=10, max_length=5000)
    pricing: Pricing = "unknown"

    _url = field_validator("product_url")(validate_manual_product_url)

    @field_validator("name", "description", mode="before")
    @classmethod
    def clean_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if "\x00" in value:
                raise ValueError("Text contains an invalid control character")
        return value


class ManualProductUpdate(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    product_url: str | None = Field(default=None, max_length=2048)
    description: str | None = Field(default=None, min_length=10, max_length=5000)
    pricing: Pricing | None = None
    active: bool | None = None
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("product_url")
    @classmethod
    def clean_url(cls, value):
        return validate_manual_product_url(value) if value is not None else value

    @field_validator("name", "description", mode="before")
    @classmethod
    def clean_text(cls, value):
        return ManualProductInput.clean_text(value)

    @model_validator(mode="after")
    def complete_update(self):
        fields = self.model_fields_set - {"expected_revision"}
        if not fields or any(getattr(self, field) is None for field in fields):
            raise ValueError("Supply at least one non-null product setting")
        if fields - {"active"} and self.expected_revision is None:
            raise ValueError("The current product revision is required to edit product facts")
        return self


class ProductDetailOut(WorkspaceInventoryProductOut):
    pass


def limit_product_write(redis, actor, account_id):
    caller = hashlib.sha256(f"{actor.issuer}\0{actor.subject}".encode()).hexdigest()
    try:
        retry = consume_rate_limits(
            redis,
            [
                RateLimitBudget(key=f"partner-product-caller:{caller}", limit=30, window=60),
                RateLimitBudget(
                    key=f"partner-product-account:{account_id}", limit=100, window=3600
                ),
            ],
        )
    except RedisError:
        raise HTTPException(503, "Product changes are temporarily unavailable") from None
    if retry:
        raise HTTPException(
            429, "Too many product changes; try again shortly", headers={"Retry-After": str(retry)}
        )


def lock_manual_identity(session, account_id, *, product_id=None, url=None):
    """Catalog -> product -> account matches import, merge and ad-image lock order."""
    lock_catalog(session)
    if product_id is None:
        alias = session.get(PartnerProductURL, fingerprint(url))
        product_id = alias.product_id if alias else None
    elif session.get(PartnerManualProduct, (account_id, product_id)) is None:
        raise HTTPException(404, "Manual product not found")
    if product_id is not None:
        product = session.scalar(
            select(PartnerProduct)
            .where(PartnerProduct.id == product_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if product is None or product.merged_into_id:
            raise HTTPException(404, "Product not found")
        return product
    return None


def product_detail(session, account, product_id):
    source = session.get(PartnerManualProduct, (account.id, product_id))
    product = session.get(PartnerProduct, product_id)
    if (
        product is None
        or product.merged_into_id
        or (
            source is None
            and session.execute(
                associated_products_query(account.id).where(PartnerProduct.id == product_id)
            ).first()
            is None
        )
    ):
        raise HTTPException(404, "Product not found")
    policy = session.get(PartnerProductPromotion, (account.id, product_id))
    # Inventory rows already come from active source pairs. Direct details also
    # admit archived manual registrations, whose imported source may remain live.
    active = (
        source is None
        or source.active
        or bool(session.scalar(select(account_product_source(account.id, product.id))))
    )
    return inventory_product_view(account, product, policy, utcnow(), source, active=active)


def create_manual_product(session, account, payload, existing=None):
    if existing is None:
        existing = PartnerProduct(
            **payload.model_dump(),
            technologies=[],
            evidence=[],
            reviews=[],
            metadata_account_id=account.id,
            revision=1,
            status="pending",
            assessment={"state": "waiting", "reason": "Waiting for automatic product checks."},
        )
        session.add(existing)
        session.flush()
        session.add(
            PartnerProductURL(
                url_hash=fingerprint(existing.product_url),
                url=existing.product_url,
                product_id=existing.id,
            )
        )
    source = session.get(PartnerManualProduct, (account.id, existing.id))
    if source is None:
        session.add(PartnerManualProduct(account_id=account.id, product_id=existing.id))
        session.flush()
        refresh_availability(session, existing)
        if existing.assessment_revision != existing.revision:
            request_assessment(session, existing)
        request_promotion_enrollment(session, account.id)
    # Repeating create never undoes a deliberate archive or product exclusion.
    return product_detail(session, account, existing.id)


def update_manual_product(session, account, product, payload):
    source = session.get(PartnerManualProduct, (account.id, product.id))
    if source is None:
        raise HTTPException(404, "Manual product not found")
    if payload.expected_revision is not None and payload.expected_revision != product.revision:
        raise HTTPException(409, "Product changed. Refresh the page before saving.")
    changes = payload.model_dump(exclude_unset=True, exclude={"active", "expected_revision"})
    if changes and product.metadata_account_id != account.id:
        raise HTTPException(409, "This catalog product is managed by its original source")
    url = changes.get("product_url")
    if url:
        alias = session.get(PartnerProductURL, fingerprint(url))
        if alias and alias.product_id != product.id:
            raise HTTPException(409, "This website already belongs to another catalog product")
        if alias is None:
            session.add(
                PartnerProductURL(url_hash=fingerprint(url), url=url, product_id=product.id)
            )
    changed = any(getattr(product, key) != value for key, value in changes.items())
    if changed:
        for key, value in changes.items():
            setattr(product, key, value)
        product.revision += 1
        product.updated_at = utcnow()
        product.verified_at = None
        product.technologies, product.evidence = [], []
        product.assessment_revision = 0
        product.assessment = {
            "state": "waiting",
            "reason": "Product details changed; checking again.",
        }
        product.status = "paused" if product.excluded else "pending"
        for model in (PartnerPipelineJob, PartnerEvaluation):
            for job in session.scalars(
                select(model).where(
                    model.product_id == product.id, model.status.in_(["queued", "running"])
                )
            ):
                fail_or_retry(job, "Product facts changed", utcnow(), retryable=False)
    if "active" in payload.model_fields_set:
        source.active = payload.active
        source.updated_at = utcnow()
    session.flush()
    refresh_availability(session, product)
    if changed or payload.active is True:
        if product.assessment_revision != product.revision:
            request_assessment(session, product)
        request_promotion_enrollment(session, account.id)
    return product_detail(session, account, product.id)


def archive_manual_product(session, account, product):
    update_manual_product(session, account, product, ManualProductUpdate(active=False))
