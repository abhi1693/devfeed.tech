"""Serve bounded prepared matches, then apply live commercial policy and attribution."""

import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from devfeed_core.models import (
    Article,
    ArticleTopic,
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
from devfeed_core.partner_ad_formats import ad_format_allowed, placement_format_allowed
from devfeed_core.partner_audiences import (
    audience_entitled,
    audience_matches,
    audience_topics_current,
    effective_audience,
    reader_audience,
)
from devfeed_core.partner_matching import MATCH_TTL, article_fingerprint
from devfeed_core.partner_product_images import product_image_url, product_image_variants
from devfeed_core.partner_product_logos import product_logo_url
from devfeed_core.partner_promotions import (
    campaign_is_live,
    campaign_matches_product,
    commercially_eligible,
)
from devfeed_core.partner_tiers import effective_promotion_kind, promotion_kind_allowed
from devfeed_core.partner_tracking import (
    RECEIPT_TTL,
    BusyPartnerAsset,
    PartnerTrackingTokens,
)
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut
from devfeed_core.urls import validate_public_url


def account_product_available(
    session, account_id, product_id, *, busy_error=False, ad_format=None, surface=None
):
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
    restrictions: list[ColumnElement[bool]] = [PartnerProductPromotion.excluded == True]  # noqa: E712
    if ad_format is not None:
        restrictions.append(
            and_(
                PartnerProductPromotion.ad_formats.is_not(None),
                ~PartnerProductPromotion.ad_formats.contains([ad_format]),
            )
        )
    if surface is not None:
        restrictions.append(
            and_(
                PartnerProductPromotion.promotion_surfaces.is_not(None),
                ~PartnerProductPromotion.promotion_surfaces.contains([surface]),
            )
        )
    else:
        restrictions.append(PartnerProductPromotion.promotion_surfaces == [])
    exclusion = select(or_(*restrictions)).where(
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
        or not commercially_eligible(session, account, product, now, busy_error=busy_error)
        or not account_product_available(
            session,
            account.id,
            product.id,
            busy_error=busy_error,
            ad_format=getattr(asset, "ad_format", "feed"),
            surface=surface,
        )
        or (surface is not None and surface not in account.promotion_surfaces)
    ):
        return False
    if asset.campaign_id is None:
        return (
            audience_entitled(account)
            and account.organic_enabled
            and account.auto_include_products
            and effective_promotion_kind(account) == "product"
        )
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
    if campaign and campaign.managed:
        # An explicit live agreement owns its selected inventory until its end.
        # A standing tier agreement must not bypass that agreement's daily cap.
        explicit = session.scalars(
            select(PartnerCampaign).where(
                PartnerCampaign.account_id == account.id,
                PartnerCampaign.managed.is_(False),
                PartnerCampaign.ad_format == getattr(asset, "ad_format", "feed"),
                PartnerCampaign.status == "active",
                PartnerCampaign.starts_at <= now,
                PartnerCampaign.ends_at > now,
            )
        )
        topics = prepared_product_topics(session, product, now)
        if any(campaign_matches_product(other, product, topics) for other in explicit):
            return False
    if campaign and getattr(asset, "article_id", None) is not None:
        article_query = select(Article).where(Article.id == asset.article_id, visible_article())
        article = session.scalar(article_query.with_for_update(read=True, skip_locked=True))
        if article is None:
            if busy_error and session.scalar(article_query) is not None:
                raise BusyPartnerAsset("Sponsored article is being updated")
            return False
    return bool(
        campaign
        and getattr(campaign, "ad_format", "feed") == getattr(asset, "ad_format", "feed")
        and getattr(campaign, "article_id", None) == getattr(asset, "article_id", None)
        and (
            ad_format_allowed(account, getattr(asset, "ad_format", "feed"))
            or (campaign.grandfathered and getattr(asset, "ad_format", "feed") == "feed")
        )
        and (
            not campaign.managed
            or getattr(asset, "ad_format", "feed") in getattr(account, "ad_formats", ["feed"])
        )
        and campaign_is_live(campaign, now)
        and audience_entitled(account, campaign)
        and (promotion_kind_allowed(account, "ad") or campaign.grandfathered)
        and (not campaign.managed or effective_promotion_kind(account) == "ad")
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


def feed_topic_weights(session, article_ids):
    """Resolve displayed content into approved context without accepting reader topic claims."""
    if not article_ids:
        return {}
    links = session.execute(
        select(ArticleTopic.topic_id, ArticleTopic.role, ArticleTopic.relevance)
        .join(Article, Article.id == ArticleTopic.article_id)
        .join(Topic, Topic.id == ArticleTopic.topic_id)
        .where(
            Article.id.in_(set(article_ids)),
            visible_article(),
            Topic.status == "active",
            ArticleTopic.role.in_(["primary", "supporting"]),
        )
        .order_by(ArticleTopic.relevance.desc(), ArticleTopic.topic_id, ArticleTopic.article_id)
        .limit(500)
        .with_for_update(read=True, of=[Article, ArticleTopic, Topic], skip_locked=True)
    ).all()
    weights: dict[uuid.UUID, int] = {}
    for topic_id, role, relevance in links:
        weight = round(relevance * (100 if role == "primary" else 80))
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
    elif query.article_ids:
        weights = feed_topic_weights(session, query.article_ids)
        if user_id and weights:
            interests = personal_topic_weights(session, user_id, now)
            # Search/category context remains authoritative. Personalization can
            # strengthen existing topics but cannot introduce unrelated inventory.
            weights = {
                topic_id: min(100, weight + interests.get(topic_id, 0) // 10)
                for topic_id, weight in weights.items()
            }
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


def placement_destination(session, account, product):
    """Choose the first safe current listing owned by this account, never another provider."""
    if account.outbound_destination != "listing_url":
        return devfeed_outbound_url(product.product_url)
    return account_listing_destination(session, account.id, product.id, lock=True)


def account_listing_destination(session, account_id, product_id, *, lock=False):
    """Return an owned listing URL; only delivery locks its attribution source rows."""
    query = (
        select(PartnerListing)
        .join(PartnerConnection)
        .where(
            PartnerListing.product_id == product_id,
            PartnerConnection.account_id == account_id,
            PartnerConnection.enabled.is_(True),
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
        )
        .order_by(PartnerListing.provider, PartnerListing.external_id, PartnerListing.id)
        .limit(20)
    )
    if lock:
        query = query.with_for_update(
            read=True, of=[PartnerListing, PartnerConnection], skip_locked=True
        )
    listings = session.scalars(query)
    return next(
        (url for listing in listings if (url := devfeed_outbound_url(listing.listing_url))), None
    )


def devfeed_outbound_url(destination):
    """HTTPS destination with DevFeed source, preserving other parameters and fragments."""
    try:
        fragment = urlsplit(destination).fragment
        destination = validate_public_url(destination)
    except (ValueError, TypeError, AttributeError):
        return None
    parts = urlsplit(destination)
    if parts.scheme != "https":
        return None
    params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "utm_source"
    ]
    params.append(("utm_source", "devfeed"))
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), fragment))
    return url if len(url) <= 2048 else None


def sponsored_fallback_rows(session, excluded, now, formats=("feed", "homepage_sponsorship")):
    ownership = exists(
        select(PartnerListing.id)
        .join(PartnerConnection)
        .where(
            PartnerListing.product_id == PartnerProduct.id,
            PartnerConnection.account_id == PartnerAccount.id,
            PartnerConnection.enabled.is_(True),
            PartnerListing.active.is_(True),
            PartnerListing.identity_status == "resolved",
        )
    ).correlate(PartnerAccount, PartnerProduct)
    # A single bounded sponsored slot, using only an inherited paid account policy.
    # No unverified category, personal-interest or article relevance claim is made.
    return session.execute(
        select(PartnerAsset, PartnerAccount, PartnerProduct)
        .join(PartnerAccount, PartnerAccount.id == PartnerAsset.account_id)
        .join(PartnerProduct, PartnerProduct.id == PartnerAsset.product_id)
        .join(PartnerCampaign, PartnerCampaign.id == PartnerAsset.campaign_id)
        .outerjoin(
            PartnerProductPromotion,
            and_(
                PartnerProductPromotion.account_id == PartnerAccount.id,
                PartnerProductPromotion.product_id == PartnerProduct.id,
            ),
        )
        .where(
            ownership,
            or_(
                PartnerProductPromotion.excluded.is_(None),
                PartnerProductPromotion.excluded.is_(False),
            ),
            or_(
                and_(
                    PartnerProduct.status == "approved",
                    PartnerProduct.verified_at >= now - timedelta(days=90),
                ),
                and_(
                    PartnerAccount.approval_mode == "trusted",
                    PartnerProductPromotion.commercial_approved_at.is_not(None),
                    PartnerProductPromotion.approval_source == "trusted",
                ),
            ),
            PartnerAsset.automatic.is_(True),
            PartnerAsset.ad_format.in_(formats),
            PartnerAsset.status == "active",
            PartnerAccount.status == "active",
            PartnerCampaign.managed.is_(True),
            PartnerCampaign.status == "active",
            PartnerCampaign.starts_at <= now,
            PartnerCampaign.ends_at > now,
            PartnerAccount.tier.in_(["silver", "gold", "platinum", "diamond"]),
            or_(PartnerAccount.promotion_kind.is_(None), PartnerAccount.promotion_kind == "ad"),
            PartnerAccount.auto_include_products.is_(True),
            PartnerAccount.promotion_surfaces.contains(["feed"]),
            PartnerProduct.id.not_in(excluded),
            PartnerProduct.excluded.is_(False),
            PartnerProduct.merged_into_id.is_(None),
        )
        .order_by(PartnerCampaign.priority.desc(), PartnerProduct.id, PartnerAsset.id)
        .limit(100)
    ).all()


def sponsored_story_matches(session, query, article_ids, now, user_id=None):
    """A product match cannot lend relevance to an unrelated promoted story."""
    if not article_ids:
        return set()
    weights = (
        feed_topic_weights(session, query.article_ids)
        if query.article_ids
        else personal_topic_weights(session, user_id, now)
        if user_id
        else {}
    )
    context = [topic_id for topic_id, weight in weights.items() if weight >= 40]
    if not context:
        return set()
    return set(
        session.scalars(
            select(ArticleTopic.article_id)
            .join(Topic)
            .where(
                ArticleTopic.article_id.in_(article_ids),
                ArticleTopic.topic_id.in_(context),
                ArticleTopic.role.in_(["primary", "supporting"]),
                Topic.status == "active",
            )
            .order_by(ArticleTopic.article_id, ArticleTopic.topic_id)
            .limit(2000)
            .with_for_update(read=True, of=[ArticleTopic, Topic], skip_locked=True)
        )
    )


def deliver_placements(session, query, key, *, user_id=None, now=None):
    now = now or utcnow()
    if not key:
        return []
    candidates = contextual_candidates(session, query, now, user_id)
    candidates = {
        pid: value for pid, value in candidates.items() if pid not in query.exclude_product_ids
    }
    rows = session.execute(
        select(PartnerAsset, PartnerAccount, PartnerProduct)
        .join(PartnerAccount, PartnerAccount.id == PartnerAsset.account_id)
        .join(PartnerProduct, PartnerProduct.id == PartnerAsset.product_id)
        .where(
            PartnerAsset.automatic.is_(True),
            PartnerAsset.ad_format.in_(query.ad_formats),
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
    selected: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    selected_formats: set[str] = set()
    selected_articles = set(query.article_ids)
    eligible_stories = None
    reader_loaded = False
    reader = None
    audience_cache: dict[tuple, bool] = {}
    # Reserve sponsorship first so a funded product's eligible appearance is attributed
    # to its agreement; fill the independent ordinary slot with the next best product.
    # This flag describes campaign backing only. Reader disclosure is Sponsored for
    # every product placement, including the non-campaign slot.
    for phase in ("sponsored", "fallback", "organic"):
        sponsored = phase != "organic"
        if phase == "fallback":
            if query.surface != "feed" or not query.allow_sponsored_fallback:
                continue
            phase_rows = sponsored_fallback_rows(
                session,
                set(query.exclude_product_ids) | seen,
                now,
                formats=set(query.ad_formats) & {"feed", "homepage_sponsorship"} - selected_formats,
            )
            for asset, _, product in phase_rows:
                # Snapshot before row locking, as with contextual prepared matches.
                candidates.setdefault(
                    product.id,
                    (0, "Sponsored by this partner", product.revision, product.updated_at),
                )
                if asset.campaign_id not in campaigns:
                    campaign = session.get(PartnerCampaign, asset.campaign_id)
                    if campaign:
                        campaigns[campaign.id] = campaign
        else:
            phase_rows = rows
        for asset, account, product in phase_rows:
            if len(selected) >= 2:
                break
            ad_format = asset.ad_format
            if not placement_format_allowed(ad_format, query) or (
                sponsored and ad_format in selected_formats
            ):
                continue
            if asset.article_id is not None and asset.article_id in selected_articles:
                continue
            if ad_format == "sponsored_content":
                if eligible_stories is None:
                    eligible_stories = sponsored_story_matches(
                        session,
                        query,
                        {item.article_id for item, _, _ in rows if item.article_id},
                        now,
                        user_id,
                    )
                if asset.article_id not in eligible_stories:
                    continue
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
            campaign = campaigns.get(asset.campaign_id)
            audience = effective_audience(account, campaign)
            if audience is None:
                continue
            if audience.populated:
                if user_id is None:
                    continue
                if not reader_loaded:
                    reader = reader_audience(session, user_id, now)
                    reader_loaded = True
                policy_key = (
                    tuple(audience.experience_levels),
                    tuple(audience.language_topic_ids),
                    tuple(audience.tool_topic_ids),
                )
                if policy_key not in audience_cache:
                    audience_cache[policy_key] = audience_topics_current(
                        session, audience, lock=True
                    ) and audience_matches(audience, reader)
                if not audience_cache[policy_key]:
                    continue
            article = None
            if ad_format == "sponsored_content":
                article = session.scalar(
                    select(Article).where(Article.id == asset.article_id, visible_article())
                )
                if article is None:
                    continue
                url = f"/articles/{article.slug}"
            else:
                url = placement_destination(session, account, product)
            if url is None:
                continue
            if sponsored and not campaign_capacity(session, campaigns[asset.campaign_id], now):
                continue
            delivery_id = uuid.uuid4()
            session.add(
                PartnerPlacementDelivery(
                    delivery_id=delivery_id,
                    asset_id=asset.id,
                    issued_at=now,
                    surface=query.surface,
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
                    logo_url=product_logo_url(product),
                    image_url=product_image_url(product),
                    image_variants=product_image_variants(product),
                    url=url,
                    partner_name=account.name,
                    sponsored=sponsored,
                    ad_format=ad_format,
                    article=ArticleOut.from_article(article) if article else None,
                    reason=(
                        "Sponsored by this partner"
                        if phase == "fallback"
                        else candidates[product.id][1][:1000]
                    ),
                    receipt=signer.issue(asset.id, delivery_id=delivery_id),
                )
            )
            seen.add(product.id)
            if sponsored:
                selected_formats.add(ad_format)
            if article:
                selected_articles.add(article.id)
            if not sponsored:
                break
    return sorted(selected, key=lambda item: item["sponsored"])
