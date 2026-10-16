"""Advisory batch eligibility before the authoritative locked placement checks."""

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from devfeed_core.models import PartnerCampaign, PartnerProductTopic, Topic
from devfeed_core.partner_ad_formats import ad_format_allowed
from devfeed_core.partner_audiences import (
    audience_entitled,
    audience_matches,
    audience_topic_kinds_match,
    effective_audience,
    reader_audience,
)
from devfeed_core.partner_placement_capacity import campaign_delivery_usage
from devfeed_core.partner_placement_destinations import PlacementDestinations
from devfeed_core.partner_promotions import campaign_is_live, campaign_matches_product
from devfeed_core.partner_tiers import effective_promotion_kind, promotion_kind_allowed


def automatic_configuration_available(asset, account, campaign, now, *, surface=None):
    """Policy that needs no database reads; also rechecked after attribution locks."""
    if account.status != "active" or asset.status != "active":
        return False
    if surface is not None and surface not in account.promotion_surfaces:
        return False
    if asset.campaign_id is None:
        if surface == "header":
            return False
        return (
            audience_entitled(account)
            and account.organic_enabled
            and account.auto_include_products
            and effective_promotion_kind(account) == "product"
        )
    ad_format = getattr(asset, "ad_format", "feed")
    return bool(
        campaign
        and getattr(campaign, "ad_format", "feed") == ad_format
        and getattr(campaign, "article_id", None) == getattr(asset, "article_id", None)
        and ad_format_allowed(account, ad_format)
        and (not campaign.managed or ad_format in getattr(account, "ad_formats", ["feed"]))
        and campaign_is_live(campaign, now)
        and audience_entitled(account, campaign)
        and promotion_kind_allowed(account, "ad")
        and (not campaign.managed or effective_promotion_kind(account) == "ad")
        and (surface is None or surface in campaign.surfaces)
        and (account.auto_include_products or campaign.targeting == "selected")
    )


class PlacementPreflight:
    def __init__(self, session, query, now, user_id):
        self.session = session
        self.query = query
        self.now = now
        self.user_id = user_id
        self._reader_loaded = False
        self._reader = None
        self._exhausted_campaigns = set()
        self.destinations = PlacementDestinations(session)

    def campaign_exhausted(self, campaign):
        return campaign is not None and campaign.id in self._exhausted_campaigns

    def reject_campaign(self, campaign):
        self._exhausted_campaigns.add(campaign.id)

    def _rows_with_capacity(self, rows):
        campaigns = {
            campaign.id: campaign
            for _, _, _, campaign, *_ in rows
            if campaign is not None
            and campaign.daily_impression_limit is not None
            and not self.campaign_exhausted(campaign)
        }
        if campaigns:
            used = dict(self.session.execute(campaign_delivery_usage(campaigns, self.now)).all())
            self._exhausted_campaigns.update(
                identifier
                for identifier, campaign in campaigns.items()
                if used.get(identifier, 0) >= campaign.daily_impression_limit
            )
        return [row for row in rows if not self.campaign_exhausted(row[3])]

    def reader(self):
        # Load outside candidate savepoints so rejection cannot release locks
        # protecting the reader snapshot reused by the final audience checks.
        if not self._reader_loaded:
            self._reader = reader_audience(self.session, self.user_id, self.now)
            self._reader_loaded = True
        return self._reader

    def eligible_rows(self, rows):
        candidates = []
        for row in rows:
            asset, account, _product, campaign = row[:4]
            if self.campaign_exhausted(campaign):
                continue
            if not automatic_configuration_available(
                asset, account, campaign, self.now, surface=self.query.surface
            ):
                continue
            audience = effective_audience(account, campaign)
            if audience is None or (
                audience.populated
                and (self.user_id is None or not audience_matches(audience, self.reader()))
            ):
                continue
            candidates.append((row, audience))

        wanted = {
            identifier
            for _, audience in candidates
            for identifier in (*audience.language_topic_ids, *audience.tool_topic_ids)
        }
        kinds = (
            dict(
                self.session.execute(
                    select(Topic.id, Topic.kind).where(
                        Topic.id.in_(wanted), Topic.status == "active"
                    )
                ).all()
            )
            if wanted
            else {}
        )
        rows = [row for row, audience in candidates if audience_topic_kinds_match(audience, kinds)]
        rows = self._rows_with_capacity(rows)
        rows = self.destinations.eligible_rows(rows)
        if not rows:
            return []

        managed_accounts = {
            account.id for _, account, _, campaign, *_ in rows if campaign and campaign.managed
        }
        explicit: dict[Any, list[Any]] = defaultdict(list)
        if managed_accounts:
            # Scalar columns avoid caching an unlocked campaign ORM instance that
            # a later authoritative read could mistake for current policy.
            agreements = self.session.execute(
                select(
                    PartnerCampaign.account_id,
                    PartnerCampaign.ad_format,
                    PartnerCampaign.targeting,
                    PartnerCampaign.product_ids,
                    PartnerCampaign.technologies,
                    PartnerCampaign.topic_ids,
                ).where(
                    PartnerCampaign.account_id.in_(managed_accounts),
                    PartnerCampaign.managed.is_(False),
                    PartnerCampaign.status == "active",
                    PartnerCampaign.starts_at <= self.now,
                    PartnerCampaign.ends_at > self.now,
                )
            )
            for campaign in agreements:
                explicit[(campaign.account_id, campaign.ad_format)].append(campaign)

        products = {
            product.id: product
            for asset, account, product, campaign, *_ in rows
            if campaign
            and (
                (campaign.targeting == "filters" and campaign.topic_ids)
                or any(
                    other.targeting == "filters" and other.topic_ids
                    for other in explicit[(account.id, asset.ad_format)]
                )
            )
        }
        topics: dict[Any, set[Any]] = defaultdict(set)
        if products:
            prepared = self.session.execute(
                select(
                    PartnerProductTopic.product_id,
                    PartnerProductTopic.topic_id,
                    PartnerProductTopic.product_revision,
                    PartnerProductTopic.product_updated_at,
                )
                .join(Topic)
                .where(
                    PartnerProductTopic.product_id.in_(products),
                    PartnerProductTopic.topic_updated_at == Topic.updated_at,
                    Topic.status == "active",
                )
            )
            for product_id, topic_id, revision, updated_at in prepared:
                product = products[product_id]
                if (revision, updated_at) == (product.revision, product.updated_at):
                    topics[product_id].add(topic_id)

        eligible = []
        for row in rows:
            asset, account, product, campaign = row[:4]
            if campaign is not None:
                if not campaign_matches_product(campaign, product, topics[product.id]):
                    continue
                # Explicit live inventory stays reserved even if its audience or
                # remaining capacity cannot serve this particular reader.
                if campaign.managed and any(
                    campaign_matches_product(other, product, topics[product.id])
                    for other in explicit[(account.id, asset.ad_format)]
                ):
                    continue
            eligible.append(row)
        return eligible
