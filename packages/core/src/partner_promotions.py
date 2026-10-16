"""Account inheritance, campaign targeting and immutable automatic attribution."""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from itertools import batched

from sqlalchemy import and_, delete, exists, func, or_, select, union, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .cache_events import mark_dirty
from .models import (
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerManualProduct,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerPromotionEnrollment,
)
from .partner_ad_formats import AUTOMATIC_AD_FORMATS, ad_format_allowed
from .partner_surfaces import default_campaign_surfaces
from .partner_tiers import effective_promotion_kind, promotion_kind_allowed
from .partner_tools import eligible

PROMOTION_BATCH_SIZE = 200
PROMOTION_INSERT_BATCH_SIZE = 500


def account_product_source(account_id, product_id):
    """Import ownership and explicit manual inventory share the same serving policy."""
    imported = exists(
        select(PartnerListing.id)
        .join(PartnerConnection)
        .where(
            PartnerConnection.account_id == account_id,
            PartnerListing.product_id == product_id,
            PartnerConnection.enabled.is_(True),
            PartnerConnection.archived_at.is_(None),
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
        )
    ).correlate(PartnerAccount, PartnerProduct)
    manual = exists(
        select(PartnerManualProduct.product_id).where(
            PartnerManualProduct.account_id == account_id,
            PartnerManualProduct.product_id == product_id,
            PartnerManualProduct.active.is_(True),
        )
    ).correlate(PartnerAccount, PartnerProduct)
    return or_(imported, manual)


def associated_products_query(account_id: uuid.UUID | None = None, *, include_archived=False):
    """Current explicit or imported inventory, independently of canonical identity."""
    imported = (
        select(PartnerConnection.account_id, PartnerListing.product_id)
        .join(PartnerListing, PartnerListing.provider == PartnerConnection.provider)
        .where(
            PartnerConnection.enabled.is_(True),
            PartnerConnection.archived_at.is_(None),
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
        )
    )
    manual = select(PartnerManualProduct.account_id, PartnerManualProduct.product_id)
    if not include_archived:
        manual = manual.where(PartnerManualProduct.active.is_(True))
    if account_id is not None:
        imported = imported.where(PartnerConnection.account_id == account_id)
        manual = manual.where(PartnerManualProduct.account_id == account_id)
    sources = union(imported, manual).subquery()
    query = (
        select(PartnerAccount, PartnerProduct)
        .join(sources, sources.c.account_id == PartnerAccount.id)
        .join(PartnerProduct, PartnerProduct.id == sources.c.product_id)
        .where(
            PartnerProduct.merged_into_id.is_(None),
        )
    )
    if account_id is not None:
        query = query.where(PartnerAccount.id == account_id)
    return query


def campaign_is_live(campaign: PartnerCampaign, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return campaign.status == "active" and campaign.starts_at <= now < campaign.ends_at


def campaign_matches_product(
    campaign: PartnerCampaign,
    product: PartnerProduct,
    topic_ids: Iterable[uuid.UUID | str] | None = None,
) -> bool:
    """OR within each filter, AND between technologies and prepared product topics.

    Attribution preparation omits topic_ids; serving must provide the prepared
    product topics to enforce topic targeting alongside current contextual relevance.
    """
    if campaign.targeting == "all":
        return True
    if campaign.targeting == "selected":
        return str(product.id) in {str(value) for value in campaign.product_ids}
    if campaign.targeting != "filters":
        return False
    if campaign.technologies:
        wanted = {value.casefold().strip() for value in campaign.technologies}
        available = {value.casefold().strip() for value in product.technologies}
        if not wanted.intersection(available):
            return False
    if (
        campaign.topic_ids
        and topic_ids is not None
        and not {str(value) for value in campaign.topic_ids}.intersection(
            str(value) for value in topic_ids
        )
    ):
        return False
    return bool(campaign.technologies or campaign.topic_ids)


def trusted_product_exists():
    """Correlated account-owned acceptance, usable independently of factual qualification."""
    return exists(
        select(PartnerProductPromotion.product_id)
        .join(PartnerAccount, PartnerAccount.id == PartnerProductPromotion.account_id)
        .where(
            PartnerProductPromotion.product_id == PartnerProduct.id,
            PartnerProductPromotion.commercial_approved_at.is_not(None),
            PartnerProductPromotion.approval_source == "trusted",
            PartnerProductPromotion.excluded.is_(False),
            PartnerAccount.status == "active",
            PartnerAccount.approval_mode == "trusted",
            account_product_source(PartnerAccount.id, PartnerProduct.id),
        )
    )


def commercially_eligible(session, account, product, now, *, busy_error=False):
    if product.merged_into_id or product.excluded:
        return False
    if eligible(product, now):
        return True
    if account.approval_mode != "trusted":
        return False
    policy = select(PartnerProductPromotion).where(
        PartnerProductPromotion.account_id == account.id,
        PartnerProductPromotion.product_id == product.id,
    )
    approval = session.scalar(policy.with_for_update(read=True, skip_locked=True))
    if approval is None and busy_error and session.scalar(policy) is not None:
        from .partner_tracking import BusyPartnerAsset

        raise BusyPartnerAsset("Partner commercial acceptance is being updated")
    return bool(
        approval
        and not approval.excluded
        and approval.commercial_approved_at
        and approval.approval_source == "trusted"
    )


def ensure_managed_campaign(session, account, now, ad_format="feed"):
    if (
        effective_promotion_kind(account) != "ad"
        or not promotion_kind_allowed(account, "ad")
        or ad_format not in getattr(account, "ad_formats", ["feed"])
        or ad_format not in AUTOMATIC_AD_FORMATS
        or not ad_format_allowed(account, ad_format)
    ):
        return None
    campaign_id = session.scalar(
        insert(PartnerCampaign)
        .values(
            id=uuid.uuid4(),
            account_id=account.id,
            name="Inherited sponsored placement"
            if ad_format == "feed"
            else f"Inherited {ad_format.replace('_', ' ')}",
            ad_format=ad_format,
            managed=True,
            status="active",
            targeting="all",
            product_ids=[],
            topic_ids=[],
            technologies=[],
            surfaces=default_campaign_surfaces(ad_format),
            starts_at=now,
            ends_at=datetime(9999, 12, 31, tzinfo=UTC),
            priority=1,
            created_at=now,
        )
        .on_conflict_do_nothing()
        .returning(PartnerCampaign.id)
    )
    return campaign_id or session.scalar(
        select(PartnerCampaign.id).where(
            PartnerCampaign.account_id == account.id,
            PartnerCampaign.managed.is_(True),
            PartnerCampaign.ad_format == ad_format,
        )
    )


def ensure_automatic_assets(
    session: Session,
    account_id: uuid.UUID | None = None,
    now: datetime | None = None,
    product_ids: Iterable[uuid.UUID] | None = None,
) -> int:
    """Enroll current owned inventory; preserve immutable historical attribution.

    Trusted commercial acceptance never changes global AI qualification or evidence.
    Tier-inherited sponsorship uses its own stable ledger alongside dated campaigns.
    """
    now = now or datetime.now(UTC)
    query = automatic_products_query(account_id)
    if product_ids is not None:
        query = query.where(PartnerProduct.id.in_(list(product_ids)))
    rows = session.execute(query).all()
    if not rows:
        return 0
    return _enroll_automatic_rows(session, rows, now)


def automatic_products_query(account_id=None):
    return associated_products_query(account_id).where(
        PartnerAccount.status == "active", PartnerProduct.excluded.is_(False)
    )


def _enroll_automatic_rows(session, rows, now):
    if not rows:
        return 0
    rows = sorted(rows, key=lambda row: (row[0].id, row[1].id))
    accounts = {account.id: account for account, _ in rows}
    for account in accounts.values():
        for ad_format in getattr(account, "ad_formats", ["feed"]):
            ensure_managed_campaign(session, account, now, ad_format)
    trusted = (
        dict(
            account_id=account.id,
            product_id=product.id,
            commercial_approved_at=now,
            approval_source="trusted",
            excluded=False,
        )
        for account, product in rows
        if account.approval_mode == "trusted"
    )
    accepted: set[uuid.UUID] = set()
    for batch in batched(trusted, PROMOTION_INSERT_BATCH_SIZE):
        accepted.update(
            session.execute(
                insert(PartnerProductPromotion)
                .values(list(batch))
                .on_conflict_do_update(
                    index_elements=[
                        PartnerProductPromotion.account_id,
                        PartnerProductPromotion.product_id,
                    ],
                    set_={"commercial_approved_at": now, "approval_source": "trusted"},
                    where=PartnerProductPromotion.commercial_approved_at.is_(None),
                )
                .returning(PartnerProductPromotion.product_id)
            )
            .scalars()
            .all()
        )
    # Queue only newly accepted products; failed/imported image jobs keep their own
    # retry and idempotency contracts, independently of the commercial ledger.
    if accepted:
        from .logos import request_logo
        from .partner_product_images import request_product_image

        for product_id in sorted(accepted):
            request_logo(session, PartnerProduct, product_id, automatic=True)
            request_product_image(session, product_id, automatic=True)
    campaigns: dict[uuid.UUID, list[PartnerCampaign]] = {}
    for campaign in session.scalars(
        select(PartnerCampaign).where(
            PartnerCampaign.account_id.in_(accounts),
            PartnerCampaign.status == "active",
            PartnerCampaign.starts_at <= now,
            PartnerCampaign.ends_at > now,
        )
    ):
        campaigns.setdefault(campaign.account_id, []).append(campaign)
    excluded = set(
        session.execute(
            select(PartnerProductPromotion.account_id, PartnerProductPromotion.product_id).where(
                PartnerProductPromotion.account_id.in_(accounts),
                PartnerProductPromotion.excluded.is_(True),
            )
        ).all()
    )
    created = 0
    for batch in batched(
        _automatic_asset_values(rows, campaigns, excluded), PROMOTION_INSERT_BATCH_SIZE
    ):
        sponsored_ids = {row["id"] for row in batch if row["ad_format"] == "sponsored_content"}
        result = session.execute(
            insert(PartnerAsset)
            .values(list(batch))
            .on_conflict_do_nothing()
            .returning(PartnerAsset.id)
        )
        inserted = result.all()
        if sponsored_ids and any(row[0] in sponsored_ids for row in inserted):
            mark_dirty(session, "partner_assets")
        created += len(inserted)
    return created


def _automatic_asset_values(rows, campaigns, excluded):
    for account, product in rows:
        if (account.id, product.id) in excluded or not (
            account.approval_mode == "trusted" or product.status == "approved"
        ):
            continue
        if (
            account.organic_enabled
            and account.auto_include_products
            and effective_promotion_kind(account) == "product"
        ):
            yield _asset_values(account.id, product, None)
        for campaign in campaigns.get(account.id, []):
            if not promotion_kind_allowed(account, "ad"):
                continue
            if campaign.managed and effective_promotion_kind(account) != "ad":
                continue
            if not account.auto_include_products and campaign.targeting != "selected":
                continue
            if campaign_matches_product(campaign, product):
                if not ad_format_allowed(account, campaign.ad_format):
                    continue
                yield _asset_values(
                    account.id, product, campaign.id, campaign.ad_format, campaign.article_id
                )


def _next_campaign_start(session, account_id, after, now):
    return session.scalar(
        select(func.min(PartnerCampaign.starts_at))
        .join(PartnerAccount)
        .where(
            PartnerCampaign.account_id == account_id,
            PartnerAccount.status == "active",
            PartnerCampaign.status == "active",
            PartnerCampaign.starts_at > after,
            PartnerCampaign.ends_at > now,
        )
    )


def request_promotion_enrollment(session, account_id, now=None):
    """Keep small saves immediate; checkpoint large catalogs in the same transaction."""
    now = now or datetime.now(UTC)
    # Account -> enrollment is shared with scheduler slices and policy editors.
    if (
        session.scalar(
            select(PartnerAccount.id).where(PartnerAccount.id == account_id).with_for_update()
        )
        is None
    ):
        return 0
    rows = session.execute(
        automatic_products_query(account_id)
        .order_by(PartnerProduct.id)
        .limit(PROMOTION_BATCH_SIZE + 1)
    ).all()
    created = 0
    due = now
    if len(rows) <= PROMOTION_BATCH_SIZE:
        created = _enroll_automatic_rows(session, rows, now)
        due = _next_campaign_start(session, account_id, now, now)
        if due is None:
            session.execute(
                delete(PartnerPromotionEnrollment).where(
                    PartnerPromotionEnrollment.account_id == account_id
                )
            )
            return created
    values = dict(
        account_id=account_id,
        product_cursor=None,
        requested_at=now,
        updated_at=now,
        available_at=due,
        sweep_started_at=None,
    )
    session.execute(
        insert(PartnerPromotionEnrollment)
        .values(**values)
        .on_conflict_do_update(index_elements=[PartnerPromotionEnrollment.account_id], set_=values)
    )
    return created


def process_promotion_enrollment(factory, *, limit=PROMOTION_BATCH_SIZE, now=None):
    """Commit one account slice and its cursor together, without waiting for editors."""
    if limit <= 0:
        return 0
    now = now or datetime.now(UTC)
    account_id = requested_at = None
    try:
        with factory.begin() as session:
            account = session.scalar(
                select(PartnerAccount)
                .join(PartnerPromotionEnrollment)
                .where(PartnerPromotionEnrollment.available_at <= now)
                .order_by(PartnerPromotionEnrollment.updated_at, PartnerAccount.id)
                .limit(1)
                .with_for_update(of=PartnerAccount, skip_locked=True)
            )
            if account is None:
                return 0
            job = session.scalar(
                select(PartnerPromotionEnrollment)
                .where(PartnerPromotionEnrollment.account_id == account.id)
                .with_for_update()
            )
            if job is None or job.available_at > now:
                return 0
            account_id, requested_at = account.id, job.requested_at
            job.sweep_started_at = job.sweep_started_at or now
            query = automatic_products_query(account.id)
            if job.product_cursor is not None:
                query = query.where(PartnerProduct.id > job.product_cursor)
            rows = session.execute(query.order_by(PartnerProduct.id).limit(limit + 1)).all()
            created = _enroll_automatic_rows(session, rows[:limit], now)
            job.updated_at = now
            if len(rows) > limit:
                job.product_cursor = rows[limit - 1][1].id
            else:
                # A campaign may start during a long sweep. Revisit earlier rows
                # as well as scheduling agreements that have not started yet.
                due = _next_campaign_start(session, account.id, job.sweep_started_at, now)
                if due is None:
                    session.delete(job)
                else:
                    job.product_cursor = job.sweep_started_at = None
                    job.available_at = max(now, due)
            return created
    except Exception:
        if account_id is not None:
            # The failed slice rolled back. Cool down only its request generation;
            # a newly saved policy keeps its own fresh checkpoint and due time.
            with factory.begin() as session:
                locked = session.scalar(
                    select(PartnerAccount.id)
                    .where(PartnerAccount.id == account_id)
                    .with_for_update(skip_locked=True)
                )
                if locked is not None:
                    session.execute(
                        update(PartnerPromotionEnrollment)
                        .where(
                            PartnerPromotionEnrollment.account_id == account_id,
                            PartnerPromotionEnrollment.requested_at == requested_at,
                        )
                        .values(
                            updated_at=now,
                            available_at=now + timedelta(seconds=60),
                            sweep_started_at=func.coalesce(
                                PartnerPromotionEnrollment.sweep_started_at, now
                            ),
                        )
                    )
        raise


def sync_partner_promotions(factory, *, limit=PROMOTION_BATCH_SIZE, now=None):
    """Bounded repair lane for imported inventory and newly inherited account policy."""
    now = now or datetime.now(UTC)
    created = process_promotion_enrollment(factory, limit=limit, now=now)
    with factory.begin() as session:
        if not session.scalar(select(func.pg_try_advisory_xact_lock(832604283))):
            return created
        paid_mode = or_(
            PartnerAccount.promotion_kind == "ad",
            and_(
                PartnerAccount.promotion_kind.is_(None),
                PartnerAccount.tier.in_(["silver", "gold", "platinum", "diamond"]),
            ),
        )
        has_organic = exists(
            select(PartnerAsset.id).where(
                PartnerAsset.account_id == PartnerAccount.id,
                PartnerAsset.product_id == PartnerProduct.id,
                PartnerAsset.automatic.is_(True),
                PartnerAsset.campaign_id.is_(None),
            )
        )
        missing_formats = or_(
            *[
                and_(
                    PartnerAccount.ad_formats.contains([kind]),
                    ~exists(
                        select(PartnerAsset.id)
                        .join(PartnerCampaign)
                        .where(
                            PartnerAsset.account_id == PartnerAccount.id,
                            PartnerAsset.product_id == PartnerProduct.id,
                            PartnerAsset.automatic.is_(True),
                            PartnerAsset.ad_format == kind,
                            PartnerCampaign.managed.is_(True),
                        )
                    ),
                )
                for kind in AUTOMATIC_AD_FORMATS
            ]
        )
        has_asset = or_(and_(paid_mode, ~missing_formats), and_(~paid_mode, has_organic))
        excluded = exists(
            select(PartnerProductPromotion.product_id).where(
                PartnerProductPromotion.account_id == PartnerAccount.id,
                PartnerProductPromotion.product_id == PartnerProduct.id,
                PartnerProductPromotion.excluded.is_(True),
            )
        )
        accepted = exists(
            select(PartnerProductPromotion.product_id).where(
                PartnerProductPromotion.account_id == PartnerAccount.id,
                PartnerProductPromotion.product_id == PartnerProduct.id,
                PartnerProductPromotion.commercial_approved_at.is_not(None),
            )
        )
        rows = session.execute(
            associated_products_query()
            .where(
                PartnerAccount.status == "active",
                PartnerProduct.excluded.is_(False),
                ~excluded,
                PartnerAccount.auto_include_products.is_(True),
                or_(paid_mode, PartnerAccount.organic_enabled.is_(True)),
                or_(PartnerProduct.status == "approved", PartnerAccount.approval_mode == "trusted"),
                or_(~has_asset, and_(PartnerAccount.approval_mode == "trusted", ~accepted)),
                ~exists(
                    select(PartnerPromotionEnrollment.account_id).where(
                        PartnerPromotionEnrollment.account_id == PartnerAccount.id,
                        or_(
                            PartnerPromotionEnrollment.available_at <= now,
                            PartnerPromotionEnrollment.sweep_started_at.is_not(None),
                        ),
                    )
                ),
            )
            .order_by(PartnerProduct.id, PartnerAccount.id)
            .limit(limit)
        ).all()
        return created + _enroll_automatic_rows(session, rows, now)


def _asset_values(
    account_id: uuid.UUID,
    product: PartnerProduct,
    campaign_id: uuid.UUID | None,
    ad_format="feed",
    article_id=None,
):
    return {
        "id": uuid.uuid4(),
        "account_id": account_id,
        "product_id": product.id,
        "campaign_id": campaign_id,
        "ad_format": ad_format,
        "article_id": article_id,
        "automatic": True,
        "name": product.name,
        "kind": "ad" if campaign_id else "product",
        "status": "active",
        "created_at": datetime.now(UTC),
    }
