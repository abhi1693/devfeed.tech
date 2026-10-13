"""Serve bounded prepared matches, then apply live commercial policy and attribution."""

import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import and_, case, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import aliased
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
from devfeed_core.partner_ad_formats import placement_format_allowed
from devfeed_core.partner_audiences import (
    audience_matches,
    audience_topics_current,
    effective_audience,
)
from devfeed_core.partner_matching import article_fingerprint
from devfeed_core.partner_placement_preflight import (
    PlacementPreflight,
    automatic_configuration_available,
)
from devfeed_core.partner_product_images import product_image_url, product_image_variants
from devfeed_core.partner_product_logos import product_logo_url
from devfeed_core.partner_promotions import (
    campaign_matches_product,
    commercially_eligible,
)
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
        return automatic_configuration_available(asset, account, None, now, surface=surface)
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
        automatic_configuration_available(asset, account, campaign, now, surface=surface)
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


def product_delivery_history():
    """Read a candidate product's latest delivery across all of its assets.

    Product/asset and asset/issuance indexes bound the two lookups. History spans
    accounts, formats and asset statuses so switching assets cannot reset rotation.
    """
    asset = aliased(PartnerAsset)
    latest = (
        select(PartnerPlacementDelivery.issued_at)
        .where(PartnerPlacementDelivery.asset_id == asset.id)
        .order_by(PartnerPlacementDelivery.issued_at.desc())
        .limit(1)
        .correlate(asset)
        .scalar_subquery()
    )
    return (
        select(func.max(latest))
        .where(asset.product_id == PartnerProduct.id)
        .correlate(PartnerProduct)
        .scalar_subquery()
    )


def prepared_article_candidates(*conditions):
    """Current grounded article edges, including authoritative evaluation decisions."""
    return (
        select(
            PartnerArticleMatch.product_id,
            PartnerArticleMatch.score,
            PartnerArticleMatch.reason,
            PartnerArticleMatch.product_revision,
            PartnerArticleMatch.product_updated_at,
        )
        .join(PartnerProduct)
        .join(Article, Article.id == PartnerArticleMatch.article_id)
        .where(
            visible_article(),
            Article.content_type == "tutorial",
            PartnerArticleMatch.relevant.is_(True),
            PartnerArticleMatch.product_revision == PartnerProduct.revision,
            PartnerArticleMatch.product_updated_at == PartnerProduct.updated_at,
            PartnerArticleMatch.score >= 75,
            *conditions,
        )
    )


def contextual_candidates(session, query, now, user_id=None, *, as_query=False):
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
            return None if as_query else {}
        statement = prepared_article_candidates(
            PartnerArticleMatch.article_id == article.id,
            PartnerArticleMatch.editorial_revision == article.editorial_revision,
            PartnerArticleMatch.article_fingerprint == article_fingerprint(article),
        )
        if as_query:
            return statement
        return {
            product_id: (score, reason, revision, updated_at)
            for product_id, score, reason, revision, updated_at in session.execute(
                statement.order_by(
                    PartnerArticleMatch.score.desc(), PartnerArticleMatch.product_id
                ).limit(100)
            )
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
        return None if as_query else {}
    weighted_score = (
        PartnerProductTopic.score
        * case(
            weights,
            value=PartnerProductTopic.topic_id,
            else_=0,
        )
    ).op("/")(100)
    conditions = (
        PartnerProductTopic.topic_id.in_(weights),
        Topic.status == "active",
        PartnerProductTopic.topic_updated_at == Topic.updated_at,
        PartnerProductTopic.product_revision == PartnerProduct.revision,
        PartnerProductTopic.product_updated_at == PartnerProduct.updated_at,
        weighted_score >= 40,
    )
    if as_query:
        # Reduce multiple approved context edges to the strongest edge per product
        # before joining assets. There is no pre-policy terminal candidate cutoff.
        ranked = (
            select(
                PartnerProductTopic.product_id,
                weighted_score.label("score"),
                PartnerProductTopic.reason,
                PartnerProductTopic.product_revision,
                PartnerProductTopic.product_updated_at,
                func.row_number()
                .over(
                    partition_by=PartnerProductTopic.product_id,
                    order_by=(weighted_score.desc(), PartnerProductTopic.topic_id),
                )
                .label("rank"),
            )
            .join(Topic)
            .join(PartnerProduct)
            .where(*conditions)
            .subquery()
        )
        return select(
            ranked.c.product_id,
            ranked.c.score,
            ranked.c.reason,
            ranked.c.product_revision,
            ranked.c.product_updated_at,
        ).where(ranked.c.rank == 1)
    matches = session.execute(
        select(PartnerProductTopic)
        .join(Topic)
        .join(PartnerProduct)
        .where(*conditions)
        .order_by(weighted_score.desc(), PartnerProductTopic.product_id)
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
    return dict(sorted(candidates.items(), key=lambda item: -item[1][0])[:100])


def engagement_feed_candidates(session, article_ids):
    """Resolve at most 24 visible tutorials once; topic overlap cannot imply a task."""
    if not article_ids:
        return None
    articles = session.execute(
        select(
            Article.id,
            Article.title,
            Article.summary,
            Article.content_type,
            Article.publication_status,
            Article.editorial_revision,
        )
        .where(
            Article.id.in_(set(article_ids)), visible_article(), Article.content_type == "tutorial"
        )
        .with_for_update(read=True, of=Article, skip_locked=True)
    ).all()
    if not articles:
        return None
    statement = prepared_article_candidates(
        or_(
            *(
                and_(
                    PartnerArticleMatch.article_id == article.id,
                    PartnerArticleMatch.editorial_revision == article.editorial_revision,
                    PartnerArticleMatch.article_fingerprint == article_fingerprint(article),
                )
                for article in articles
            )
        )
    )
    ranked = statement.add_columns(
        func.row_number()
        .over(
            partition_by=PartnerArticleMatch.product_id,
            order_by=(PartnerArticleMatch.score.desc(), PartnerArticleMatch.article_id),
        )
        .label("rank")
    ).subquery()
    return select(
        ranked.c.product_id,
        ranked.c.score,
        ranked.c.reason,
        ranked.c.product_revision,
        ranked.c.product_updated_at,
    ).where(ranked.c.rank == 1)


def placement_format_candidates(session, query, matches):
    """Keep each creative's score and reason attached to its own relevance contract."""
    groups = []
    if matches is not None and any(value != "engagement" for value in query.ad_formats):
        groups.append(
            matches.add_columns(
                literal(False).label("engagement"),
                literal(2 if query.surface == "article" else 1).label("intent"),
            )
        )
    if "engagement" in query.ad_formats:
        if query.surface == "feed":
            engagement = engagement_feed_candidates(session, query.article_ids)
        elif query.surface == "topic" and matches is not None:
            direct = matches.subquery()
            engagement = select(*direct.c).where(direct.c.score == 100)
        else:
            engagement = matches
        if engagement is not None:
            groups.append(
                engagement.add_columns(
                    literal(True).label("engagement"),
                    literal(1 if query.surface == "topic" else 2).label("intent"),
                )
            )
    if not groups:
        return None
    return union_all(*groups) if len(groups) > 1 else groups[0]


def fallback_formats(query, *, feed_context):
    """Feed standing sponsorship is contextless; Homepage is an explicit brand placement."""
    if query.surface != "feed" or not query.allow_sponsored_fallback:
        return set()
    formats = set(query.ad_formats) & {"feed", "homepage_sponsorship"}
    if feed_context or query.article_ids:
        formats.discard("feed")
    return formats


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


def placement_rows_query(query, now):
    """Cheap current eligibility before ranking; locked policy rechecks remain final."""
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
    formats = [value for value in query.ad_formats if placement_format_allowed(value, query)]
    return (
        select(PartnerAsset, PartnerAccount, PartnerProduct, PartnerCampaign)
        .join(PartnerAccount, PartnerAccount.id == PartnerAsset.account_id)
        .join(PartnerProduct, PartnerProduct.id == PartnerAsset.product_id)
        .outerjoin(PartnerCampaign, PartnerCampaign.id == PartnerAsset.campaign_id)
        .outerjoin(
            PartnerProductPromotion,
            and_(
                PartnerProductPromotion.account_id == PartnerAccount.id,
                PartnerProductPromotion.product_id == PartnerProduct.id,
            ),
        )
        .where(
            ownership,
            PartnerAsset.automatic.is_(True),
            PartnerAsset.ad_format.in_(formats),
            PartnerAsset.status == "active",
            PartnerAccount.status == "active",
            PartnerAccount.promotion_surfaces.contains([query.surface]),
            PartnerProduct.id.not_in(query.exclude_product_ids),
            PartnerProduct.excluded.is_(False),
            PartnerProduct.merged_into_id.is_(None),
            or_(
                PartnerProductPromotion.excluded.is_(None),
                PartnerProductPromotion.excluded.is_(False),
            ),
            or_(
                PartnerProductPromotion.ad_formats.is_(None),
                PartnerProductPromotion.ad_formats.bool_op("@>")(
                    func.jsonb_build_array(PartnerAsset.ad_format)
                ),
            ),
            or_(
                PartnerProductPromotion.promotion_surfaces.is_(None),
                PartnerProductPromotion.promotion_surfaces.contains([query.surface]),
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
        )
    )


def ranked_placement_rows(
    session,
    query,
    now,
    matches,
    *,
    phase,
    seen,
    selected_formats=(),
    user_id=None,
    preflight=None,
    feed_context=False,
):
    """Stream ranked assets in bounded batches past any rejected live policies.

    A fixed candidate cutoff before audience/destination/budget checks can leave
    later eligible products permanently unreachable. Fetching batches instead of
    imposing a terminal cutoff keeps those checks authoritative without starvation.
    """
    if phase == "fallback":
        formats = fallback_formats(query, feed_context=feed_context)
        if not formats:
            return
        statement = placement_rows_query(query, now).where(
            PartnerCampaign.managed.is_(True),
            PartnerCampaign.status == "active",
            PartnerCampaign.starts_at <= now,
            PartnerCampaign.ends_at > now,
            PartnerAccount.tier.in_(["silver", "gold", "platinum", "diamond"]),
            or_(PartnerAccount.promotion_kind.is_(None), PartnerAccount.promotion_kind == "ad"),
            PartnerAccount.auto_include_products.is_(True),
            PartnerAccount.ad_formats.bool_op("@>")(func.jsonb_build_array(PartnerAsset.ad_format)),
            PartnerCampaign.surfaces.contains(["feed"]),
            PartnerAsset.ad_format.in_(formats),
            PartnerProduct.id.not_in(seen),
        )
        score = literal(0).label("score")
        intent = literal(0)
        reason = literal("Sponsored by this partner").label("reason")
        revision, updated_at = PartnerProduct.revision, PartnerProduct.updated_at
    else:
        if matches is None:
            return
        matches = matches.subquery()
        statement = placement_rows_query(query, now).join(
            matches,
            and_(
                matches.c.product_id == PartnerProduct.id,
                matches.c.engagement == (PartnerAsset.ad_format == "engagement"),
            ),
        )
        statement = statement.where(
            PartnerAsset.campaign_id.is_not(None)
            if phase == "sponsored"
            else PartnerAsset.campaign_id.is_(None)
        )
        if phase == "sponsored":
            statement = statement.where(
                PartnerCampaign.status == "active",
                PartnerCampaign.starts_at <= now,
                PartnerCampaign.ends_at > now,
                PartnerCampaign.surfaces.contains([query.surface]),
            )
        score, reason = matches.c.score, matches.c.reason
        intent = matches.c.intent
        revision, updated_at = matches.c.product_revision, matches.c.product_updated_at
    if phase != "organic" and selected_formats:
        statement = statement.where(PartnerAsset.ad_format.not_in(selected_formats))
    statement = statement.add_columns(score, reason, revision, updated_at).order_by(
        intent.desc(),
        score.desc(),
        PartnerCampaign.priority.desc().nulls_last(),
        product_delivery_history().asc().nulls_first(),
        PartnerProduct.id,
        PartnerAsset.id,
    )
    rows = session.execute(statement.execution_options(yield_per=100))
    try:
        for batch in rows.partitions(100):
            if preflight is not None:
                batch = preflight.eligible_rows(batch)
            stories = sponsored_story_matches(
                session,
                query,
                {asset.article_id for asset, *_ in batch if asset.article_id},
                now,
                user_id,
            )
            for row in batch:
                yield (*row, row[0].article_id is None or row[0].article_id in stories)
    finally:
        rows.close()


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
    """Issue up to two placements after relevance and locked live-policy checks.

    Article placements and feed Engagement require current grounded tutorial tasks;
    task intent ranks ahead of broader prepared topic support. Topic Engagement
    requires direct support. Generic Feed sponsorship is limited to contextless
    requests, while explicit Homepage sponsorship may serve alongside page context.
    """
    now = now or utcnow()
    if not key:
        return []
    matches = contextual_candidates(session, query, now, user_id, as_query=True)
    feed_context = bool(query.article_ids) or matches is not None
    matches = placement_format_candidates(session, query, matches)
    signer = PartnerTrackingTokens(key, clock=lambda: now.timestamp())
    selected: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    selected_formats: set[str] = set()
    selected_articles = set(query.article_ids)
    preflight = PlacementPreflight(session, query, now, user_id)
    # Reserve sponsorship first so a funded product's eligible appearance is attributed
    # to its agreement; fill the independent ordinary slot with the next best product.
    # This flag describes campaign backing only. Reader disclosure is Sponsored for
    # every product placement, including the non-campaign slot.
    for phase in ("sponsored", "fallback", "organic"):
        sponsored = phase != "organic"
        phase_formats = {
            value for value in query.ad_formats if placement_format_allowed(value, query)
        }
        if phase == "fallback":
            phase_formats = fallback_formats(query, feed_context=feed_context)
        if sponsored and phase_formats <= selected_formats:
            continue
        phase_rows = ranked_placement_rows(
            session,
            query,
            now,
            matches,
            phase=phase,
            seen=seen,
            selected_formats=selected_formats,
            user_id=user_id,
            preflight=preflight,
            feed_context=feed_context,
        )
        for (
            asset,
            account,
            product,
            campaign,
            _score,
            reason,
            revision,
            updated_at,
            story_matches,
        ) in phase_rows:
            if len(selected) >= 2:
                break
            ad_format = asset.ad_format
            if not placement_format_allowed(ad_format, query) or (
                sponsored and ad_format in selected_formats
            ):
                continue
            if asset.article_id is not None and asset.article_id in selected_articles:
                continue
            if ad_format == "sponsored_content" and not story_matches:
                continue
            if bool(asset.campaign_id) != sponsored or product.id in seen:
                continue
            reader = preflight.reader() if user_id is not None else None
            # A failed final check releases this candidate's locks. Accepted locks
            # survive savepoint commit until the outer transaction commits, keeping
            # concurrent daily-cap reservations serialized.
            candidate = session.begin_nested()
            accepted = False
            try:
                # Readers never wait in an editor's lock order, or on another page's
                # differently ranked candidates. Skip busy rows and retain the same
                # account -> asset -> product -> campaign order as event recording.
                if not lock_attribution_row(session, PartnerAccount, account.id):
                    continue
                if not lock_attribution_row(session, PartnerAsset, asset.id):
                    continue
                if not lock_attribution_row(session, PartnerProduct, product.id):
                    continue
                if asset.campaign_id != (campaign.id if campaign else None):
                    continue
                if (product.revision, product.updated_at) != (revision, updated_at):
                    continue
                if not automatic_asset_available(
                    session, asset, account, product, now, surface=query.surface
                ):
                    continue
                if query.surface == "article" or (
                    ad_format == "engagement" and query.surface == "feed"
                ):
                    # A validated negative may commit after candidate ranking but
                    # before this product lock. Recheck the task edge now; the
                    # lock then serializes later rule/evaluation publications.
                    current = matches.subquery()
                    reason = session.scalar(
                        select(current.c.reason)
                        .where(
                            current.c.product_id == product.id,
                            current.c.engagement == (ad_format == "engagement"),
                            current.c.intent == 2,
                        )
                        .limit(1)
                    )
                    if reason is None:
                        continue
                audience = effective_audience(account, campaign)
                if audience is None:
                    continue
                if audience.populated and (
                    not audience_matches(audience, reader)
                    or not audience_topics_current(session, audience, lock=True)
                ):
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
                if sponsored and not campaign_capacity(session, campaign, now):
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
                            "Sponsored by this partner" if phase == "fallback" else reason[:1000]
                        ),
                        receipt=signer.issue(asset.id, delivery_id=delivery_id),
                    )
                )
                candidate.commit()
                accepted = True
            finally:
                if not accepted:
                    candidate.rollback()
            seen.add(product.id)
            if sponsored:
                selected_formats.add(ad_format)
            if article:
                selected_articles.add(article.id)
            if not sponsored or phase_formats <= selected_formats:
                break
        phase_rows.close()
        if len(selected) >= 2:
            break
    return sorted(selected, key=lambda item: item["sponsored"])
