"""Account-scoped campaign operations; callers authorize and own transactions."""

from __future__ import annotations

import uuid

from devfeed_core.models import (
    Article,
    ArticleTopic,
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerDailyMetric,
    PartnerProduct,
    PartnerTrackedDailyMetric,
    Topic,
)
from devfeed_core.partner_ad_formats import ad_format_allowed
from devfeed_core.partner_audiences import validate_audience
from devfeed_core.partner_promotions import (
    associated_products_query,
    request_promotion_enrollment,
)
from devfeed_core.partner_tiers import promotion_kind_allowed
from devfeed_core.publication import visible_article
from fastapi import HTTPException
from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session

from devfeed_http.partner_portal import CampaignInput, CampaignOut


class CampaignService:
    def __init__(self, session: Session, account: PartnerAccount):
        self.session = session
        self.account = account

    def page(self, *, limit: int = 100, offset: int = 0):
        rows = self.session.scalars(
            select(PartnerCampaign)
            .where(PartnerCampaign.account_id == self.account.id)
            .order_by(PartnerCampaign.created_at.desc(), PartnerCampaign.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return {
            "items": self.reports(rows),
            "total": self.session.scalar(
                select(func.count())
                .select_from(PartnerCampaign)
                .where(PartnerCampaign.account_id == self.account.id)
            ),
        }

    def get(self, campaign_id: uuid.UUID) -> CampaignOut:
        return self.reports([self._find(campaign_id)])[0]

    def _find(self, campaign_id: uuid.UUID) -> PartnerCampaign:
        campaign = self.session.scalar(
            select(PartnerCampaign).where(
                PartnerCampaign.id == campaign_id, PartnerCampaign.account_id == self.account.id
            )
        )
        if campaign is None:
            raise HTTPException(404, "Partner campaign not found")
        return campaign

    def save(self, payload: CampaignInput, *, campaign_id: uuid.UUID | None = None):
        """Save after the caller locks the account; keep disclosure and enrollment atomic."""
        campaign = self._find(campaign_id) if campaign_id is not None else None
        if campaign is not None and campaign.managed:
            raise HTTPException(409, "Inherited sponsorship is managed through account defaults")
        if not promotion_kind_allowed(self.account, "ad"):
            raise HTTPException(
                422, "Sponsored campaigns require Silver or a higher partnership tier"
            )
        if not ad_format_allowed(self.account, payload.ad_format):
            raise HTTPException(422, "This ad format requires Silver or a higher partnership tier")
        self.record_sponsorship(payload)
        self._validate_scope(payload)
        if payload.audience is not None:
            try:
                validate_audience(
                    self.session,
                    self.account.tier,
                    payload.audience,
                    previous=campaign.audience if campaign is not None else None,
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None

        values = payload.model_dump()
        values["product_ids"] = [str(value) for value in payload.product_ids]
        values["topic_ids"] = [str(value) for value in payload.topic_ids]
        values["audience"] = (
            payload.audience.model_dump(mode="json") if payload.audience is not None else None
        )
        if campaign is None:
            campaign = PartnerCampaign(account_id=self.account.id, **values)
            self.session.add(campaign)
        else:
            for key, value in values.items():
                setattr(campaign, key, value)
        self.session.flush()
        request_promotion_enrollment(self.session, self.account.id)
        return campaign

    def _validate_scope(self, payload: CampaignInput):
        if payload.product_ids:
            owned = {
                product.id
                for _, product in self.session.execute(
                    associated_products_query(self.account.id).where(
                        PartnerProduct.id.in_(payload.product_ids)
                    )
                )
            }
            if owned != set(payload.product_ids):
                raise HTTPException(
                    422, "Campaign products must belong to this account's active resolved catalog"
                )
        if payload.topic_ids:
            active = set(
                self.session.scalars(
                    select(Topic.id).where(
                        Topic.id.in_(payload.topic_ids), Topic.status == "active"
                    )
                )
            )
            if active != set(payload.topic_ids):
                raise HTTPException(422, "Campaign topics must be active catalog topics")

    def record_sponsorship(self, payload: CampaignInput):
        if payload.article_id is None:
            return
        article = self.session.scalar(
            select(Article)
            .where(Article.id == payload.article_id, visible_article())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if article is None:
            raise HTTPException(
                422, "Sponsored content requires a currently approved visible article"
            )
        if (
            self.session.scalar(
                select(ArticleTopic.article_id)
                .join(Topic)
                .where(
                    ArticleTopic.article_id == article.id,
                    ArticleTopic.role.in_(["primary", "supporting"]),
                    Topic.status == "active",
                )
                .limit(1)
            )
            is None
        ):
            raise HTTPException(422, "Sponsored content requires approved active article topics")
        if payload.status == "active":
            # AppSession tracks assignment to invalidate public/article/search caches.
            # The row lock merges concurrent sponsors without losing disclosure.
            article.sponsor_names = sorted(set(article.sponsor_names or []) | {self.account.name})

    def reports(self, rows) -> list[CampaignOut]:
        if not rows:
            return []
        campaign_ids = [row.id for row in rows]
        metric_rows = union_all(
            *[
                select(
                    PartnerAsset.campaign_id.label("campaign_id"), model.impressions, model.clicks
                )
                .join(PartnerAsset, model.asset_id == PartnerAsset.id)
                .where(PartnerAsset.campaign_id.in_(campaign_ids))
                for model in (PartnerDailyMetric, PartnerTrackedDailyMetric)
            ]
        ).subquery()
        measured = {
            campaign_id: (int(impressions or 0), int(clicks or 0))
            for campaign_id, impressions, clicks in self.session.execute(
                select(
                    metric_rows.c.campaign_id,
                    func.sum(metric_rows.c.impressions),
                    func.sum(metric_rows.c.clicks),
                ).group_by(metric_rows.c.campaign_id)
            )
        }
        result = []
        for row in rows:
            impressions, clicks = measured.get(row.id, (0, 0))
            result.append(
                CampaignOut.model_validate(row).model_copy(
                    update={
                        "impressions": impressions,
                        "clicks": clicks,
                        "ctr": clicks / impressions * 100 if impressions else None,
                    }
                )
            )
        return result
