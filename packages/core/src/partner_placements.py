"""Serve bounded prepared matches, then apply live commercial policy and attribution."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select

from devfeed_core.models import (
    Article,
    PartnerAccount,
    PartnerArticleMatch,
    PartnerAsset,
    PartnerCampaign,
    PartnerConnection,
    PartnerDeliveryEvent,
    PartnerListing,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerProductTopic,
    Topic,
    UserInterest,
    UserRecommendationState,
    UserTopic,
    utcnow,
)
from devfeed_core.partner_matching import MATCH_TTL, article_fingerprint
from devfeed_core.partner_promotions import campaign_is_live, campaign_matches_product
from devfeed_core.partner_tools import eligible
from devfeed_core.partner_tracking import (
    RECEIPT_TTL,
    BusyPartnerAsset,
    PartnerTrackingTokens,
)
from devfeed_core.publication import visible_article
from devfeed_core.urls import validate_public_url


def account_product_available(session, account_id, product_id, *, busy_error=False):
    ownership = (
        select(PartnerListing.id)
        .join(PartnerConnection)
        .where(
            PartnerListing.product_id == product_id,
            PartnerConnection.account_id == account_id,
            PartnerConnection.enabled.is_(True),
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
        )
        .limit(1)
    )
    owned = session.scalar(
        ownership.with_for_update(
            read=True, of=[PartnerListing, PartnerConnection], skip_locked=True
        )
    )
    if owned is None:
        if busy_error and session.scalar(ownership) is not None:
            raise BusyPartnerAsset("Partner catalog ownership is being updated")
        return False
    exclusion = select(PartnerProductPromotion.excluded).where(
        PartnerProductPromotion.account_id == account_id,
        PartnerProductPromotion.product_id == product_id,
    )
    excluded = session.scalar(exclusion.with_for_update(read=True, skip_locked=True))
    if excluded is None and session.scalar(exclusion) is not None:
        if busy_error:
            raise BusyPartnerAsset("Partner product policy is being updated")
        return False
    # Inserts of a previously absent exclusion serialize through the owning account
    # lock shared by issuance, tracking and the administrator's promotion endpoint.
    return not excluded


def prepared_product_topics(session, product, now):
    return set(
        session.scalars(
            select(PartnerProductTopic.topic_id)
            .join(Topic)
            .where(
                PartnerProductTopic.product_id == product.id,
                PartnerProductTopic.product_revision == product.revision,
                PartnerProductTopic.product_updated_at == product.updated_at,
                PartnerProductTopic.updated_at >= now - MATCH_TTL,
                PartnerProductTopic.topic_updated_at == Topic.updated_at,
                Topic.status == "active",
            )
        )
    )


def automatic_asset_available(
    session, asset, account, product, now, *, surface=None, busy_error=False
):
    """Shared serving/tracking fence; legacy reporting assets keep their original contract."""
    if account.status != "active" or asset.status != "active":
        return False
    if not asset.automatic:
        return True
    if (
        product is None
        or not eligible(product, now)
        or not account_product_available(session, account.id, product.id, busy_error=busy_error)
        or (surface is not None and surface not in account.promotion_surfaces)
    ):
        return False
    if asset.campaign_id is None:
        return account.organic_enabled and account.auto_include_products
    campaign = session.scalar(
        select(PartnerCampaign)
        .where(
            PartnerCampaign.id == asset.campaign_id,
            PartnerCampaign.account_id == account.id,
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if campaign is None and busy_error:
        # The composite FK guarantees the campaign exists. A skipped row is busy;
        # the browser can retry this valid receipt after the editing transaction.
        raise BusyPartnerAsset("Partner campaign is being updated")
    return bool(
        campaign
        and campaign_is_live(campaign, now)
        and (surface is None or surface in campaign.surfaces)
        and (account.auto_include_products or campaign.targeting == "selected")
        and campaign_matches_product(
            campaign, product, prepared_product_topics(session, product, now)
        )
    )


def personal_topic_weights(session, user_id, now):
    weights = {
        topic_id: 100
        for topic_id in session.scalars(
            select(UserTopic.topic_id)
            .join(Topic)
            .where(
                UserTopic.user_id == user_id,
                Topic.status == "active",
            )
            .limit(100)
        )
    }
    # Inferred interests are prepared by the existing recommendation worker. Suppress
    # them immediately when preferences/likes invalidate that generation.
    inferred = session.execute(
        select(UserInterest.topic_id, UserInterest.weight)
        .join(UserRecommendationState, UserRecommendationState.user_id == UserInterest.user_id)
        .join(Topic, Topic.id == UserInterest.topic_id)
        .where(
            UserInterest.user_id == user_id,
            Topic.status == "active",
            UserRecommendationState.invalidated.is_(False),
            UserRecommendationState.computed_at >= now - timedelta(hours=6),
        )
        .order_by(UserInterest.weight.desc(), UserInterest.topic_id)
        .limit(200)
    ).all()
    for topic_id, weight in inferred:
        weights[topic_id] = max(weights.get(topic_id, 0), weight)
    return weights


def contextual_candidates(session, query, now, user_id=None):
    if query.surface == "article":
        article = session.scalar(
            select(Article)
            .where(
                Article.id == query.article_id,
                visible_article(),
                Article.content_type == "tutorial",
            )
            .with_for_update(read=True, of=Article, skip_locked=True)
        )
        if article is None:
            return {}
        matches = session.execute(
            select(
                PartnerArticleMatch.product_id,
                PartnerArticleMatch.score,
                PartnerArticleMatch.reason,
                PartnerArticleMatch.product_revision,
                PartnerArticleMatch.product_updated_at,
            )
            .join(PartnerProduct)
            .where(
                PartnerArticleMatch.article_id == article.id,
                PartnerArticleMatch.relevant.is_(True),
                PartnerArticleMatch.product_revision == PartnerProduct.revision,
                PartnerArticleMatch.product_updated_at == PartnerProduct.updated_at,
                PartnerArticleMatch.editorial_revision == article.editorial_revision,
                PartnerArticleMatch.article_fingerprint == article_fingerprint(article),
                PartnerArticleMatch.updated_at >= now - MATCH_TTL,
                PartnerArticleMatch.score >= 75,
            )
            .order_by(PartnerArticleMatch.score.desc(), PartnerArticleMatch.product_id)
            .limit(100)
        )
        return {
            product_id: (score, reason, revision, updated_at)
            for product_id, score, reason, revision, updated_at in matches
        }
    if query.surface == "topic":
        topic = session.scalar(
            select(Topic)
            .where(
                Topic.id == query.topic_id,
                Topic.status == "active",
            )
            .with_for_update(read=True, skip_locked=True)
        )
        weights = {topic.id: 100} if topic else {}
    else:
        weights = personal_topic_weights(session, user_id, now) if user_id else {}
    if not weights:
        return {}
    matches = session.execute(
        select(PartnerProductTopic)
        .join(Topic)
        .join(PartnerProduct)
        .where(
            PartnerProductTopic.topic_id.in_(weights),
            Topic.status == "active",
            PartnerProductTopic.topic_updated_at == Topic.updated_at,
            PartnerProductTopic.product_revision == PartnerProduct.revision,
            PartnerProductTopic.product_updated_at == PartnerProduct.updated_at,
            PartnerProductTopic.updated_at >= now - MATCH_TTL,
        )
        .order_by(PartnerProductTopic.score.desc(), PartnerProductTopic.product_id)
        .limit(1000)
    ).scalars()
    candidates: dict[uuid.UUID, tuple[int, str, int, datetime]] = {}
    for match in matches:
        score = match.score * weights[match.topic_id] // 100
        if score < 40 or score <= candidates.get(match.product_id, (0, ""))[0]:
            continue
        candidates[match.product_id] = (
            score,
            match.reason,
            match.product_revision,
            match.product_updated_at,
        )
    return dict(sorted(candidates.items(), key=lambda item: (-item[1][0], str(item[0])))[:100])


def campaign_capacity(session, campaign, now):
    if campaign.daily_impression_limit is None:
        return True
    day = now.date()
    # A campaign lock serializes issuance across all its products. Count actual
    # impressions plus still-valid reservations, once per delivery, across UTC days.
    delivered = (
        select(PartnerDeliveryEvent.delivery_id)
        .where(
            PartnerDeliveryEvent.asset_id == PartnerPlacementDelivery.asset_id,
            PartnerDeliveryEvent.delivery_id == PartnerPlacementDelivery.delivery_id,
            PartnerDeliveryEvent.kind == "impression",
            PartnerDeliveryEvent.day == day,
        )
        .exists()
    )
    used = session.scalar(
        select(func.count())
        .select_from(PartnerPlacementDelivery)
        .join(PartnerAsset)
        .where(
            PartnerAsset.campaign_id == campaign.id,
            PartnerPlacementDelivery.issued_at
            >= now.replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(seconds=RECEIPT_TTL),
            or_(
                delivered,
                PartnerPlacementDelivery.issued_at > now - timedelta(seconds=RECEIPT_TTL),
            ),
        )
    )
    return used < campaign.daily_impression_limit


def lock_attribution_row(session, model, identifier, *, busy_error=False):
    row = session.scalar(
        select(model)
        .where(model.id == identifier)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if row is None and busy_error:
        raise BusyPartnerAsset("Partner attribution is being updated")
    return row


def deliver_placements(session, query, key, *, user_id=None, now=None):
    now = now or utcnow()
    if not key:
        return []
    candidates = contextual_candidates(session, query, now, user_id)
    candidates = {
        pid: value for pid, value in candidates.items() if pid not in query.exclude_product_ids
    }
    if not candidates:
        return []
    rows = session.execute(
        select(PartnerAsset, PartnerAccount, PartnerProduct)
        .join(PartnerAccount, PartnerAccount.id == PartnerAsset.account_id)
        .join(PartnerProduct, PartnerProduct.id == PartnerAsset.product_id)
        .where(
            PartnerAsset.automatic.is_(True),
            PartnerAsset.status == "active",
            PartnerAccount.status == "active",
            PartnerAsset.product_id.in_(candidates),
        )
        .order_by(PartnerAsset.id)
        .limit(1000)
    ).all()
    campaigns = {
        campaign.id: campaign
        for campaign in session.scalars(
            select(PartnerCampaign).where(
                PartnerCampaign.id.in_(
                    [asset.campaign_id for asset, _, _ in rows if asset.campaign_id]
                ),
            )
        )
    }
    rows.sort(
        key=lambda row: (
            -candidates[row[2].id][0],
            -campaigns[row[0].campaign_id].priority if row[0].campaign_id in campaigns else 0,
            str(row[0].id),
        )
    )
    signer = PartnerTrackingTokens(key, clock=lambda: now.timestamp())
    selected = []
    seen = set()
    # Reserve sponsorship first so a funded product's eligible appearance is attributed
    # to its agreement; fill the independent ordinary slot with the next best product.
    for sponsored in (True, False):
        for asset, account, product in rows:
            if bool(asset.campaign_id) != sponsored or product.id in seen:
                continue
            # Readers never wait in an editor's lock order, or on another page's
            # differently ranked candidates. Skip busy rows and retain the same
            # account -> asset -> product -> campaign order as event recording.
            if not lock_attribution_row(session, PartnerAccount, account.id):
                continue
            if not lock_attribution_row(session, PartnerAsset, asset.id):
                continue
            if not lock_attribution_row(session, PartnerProduct, product.id):
                continue
            if (product.revision, product.updated_at) != candidates[product.id][2:]:
                continue
            if not automatic_asset_available(
                session, asset, account, product, now, surface=query.surface
            ):
                continue
            try:
                url = validate_public_url(product.product_url)
            except ValueError:
                continue
            if not url.startswith("https://"):
                continue
            if sponsored and not campaign_capacity(session, campaigns[asset.campaign_id], now):
                continue
            delivery_id = uuid.uuid4()
            session.add(
                PartnerPlacementDelivery(
                    delivery_id=delivery_id,
                    asset_id=asset.id,
                    issued_at=now,
                )
            )
            session.flush()
            selected.append(
                dict(
                    product_id=product.id,
                    asset_id=asset.id,
                    campaign_id=asset.campaign_id,
                    name=product.name,
                    description=product.description[:2000],
                    url=url,
                    partner_name=account.name,
                    sponsored=sponsored,
                    reason=candidates[product.id][1][:1000],
                    receipt=signer.issue(asset.id, delivery_id=delivery_id),
                    frequency_cap=account.frequency_cap,
                )
            )
            seen.add(product.id)
            break
    return sorted(selected, key=lambda item: item["sponsored"])
