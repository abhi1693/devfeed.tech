"""Partner report aggregation over ClickHouse observations and authorized catalog data."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from functools import cmp_to_key
from typing import Literal, NotRequired, TypedDict, cast

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
from devfeed_core.models import PartnerAccount, PartnerAsset, PartnerProduct
from sqlalchemy import any_, func, literal, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

type ReportSort = Literal["name", "account", "impressions", "clicks", "ctr"]

type ReportOrder = Literal["asc", "desc"]

type GroupKey = tuple[uuid.UUID, str, uuid.UUID]


class ReportingAsset(TypedDict):
    id: uuid.UUID
    account_id: uuid.UUID
    product_id: uuid.UUID | None
    campaign_id: uuid.UUID | None
    article_id: uuid.UUID | None
    name: str
    kind: str
    status: str
    automatic: bool
    ad_format: str
    account_name: str
    canonical_product_id: uuid.UUID | None
    product_name: str | None


class MetricTotals(TypedDict):
    impressions: int
    clicks: int
    ctr: float | None
    measured_days: int


class MetricStat(TypedDict):
    impressions: int
    clicks: int
    days: set[date | str]


class AssetMetricData(MetricTotals):
    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    product_id: uuid.UUID | None
    name: str
    kind: str
    status: str
    automatic: bool
    aggregated: bool
    ad_format: str
    ad_formats: list[str]
    campaign_id: uuid.UUID | None
    article_id: uuid.UUID | None
    _measured: NotRequired[bool]


class FormatMetricTotals(MetricTotals):
    ad_format: str


class TrendData(TypedDict):
    day: date | str
    impressions: int
    clicks: int


class ReportingMetrics(TypedDict):
    start: date
    end: date
    totals: MetricTotals
    format_totals: list[FormatMetricTotals]
    last_updated_at: datetime | None
    trend: list[TrendData]
    assets: list[AssetMetricData]
    asset_total: int


# Each query returns at most 50 assets x 365 days; no unbounded IN clause.
_ASSET_CHUNK = 50


async def _require_partner_import(client: AnalyticsClient, expected: set[str]) -> None:
    rows = await client.query(
        "SELECT source,complete FROM analytics_backfill_progress FINAL "
        "WHERE import_id={import_id:String} AND source IN {sources:Array(String)}",
        {"import_id": "direct-v1", "sources": sorted(expected)},
    )
    if (
        len(rows) != len(expected)
        or {row.get("source") for row in rows if row.get("complete") == 1} != expected
    ):
        raise AnalyticsUnavailable("Partner history import is still running")


async def require_partner_claims(client: AnalyticsClient) -> None:
    """Accept direct receipts only after legacy operational claims are imported."""
    await _require_partner_import(client, {"partner_delivery_events"})


async def require_partner_history(client: AnalyticsClient) -> None:
    """Do not serve incomplete one-time partner baseline imports."""
    await _require_partner_import(
        client,
        {"partner_daily_metrics", "partner_tracked_daily_metrics", "partner_delivery_events"},
    )


def reporting_assets(
    session: Session, accounts_query: Select[PartnerAccount], ad_format: str | None = None
) -> list[ReportingAsset]:
    accounts = (
        accounts_query.with_only_columns(
            PartnerAccount.id.label("account_id"), PartnerAccount.name.label("account_name")
        )
        .order_by(None)
        .cte("report_accounts")
    )
    scoped = (
        select(PartnerAsset, accounts.c.account_name)
        .join(accounts, accounts.c.account_id == PartnerAsset.account_id)
        .where(*([PartnerAsset.ad_format == ad_format] if ad_format is not None else []))
        .cte("report_assets")
    )
    identities = (
        select(
            PartnerProduct.id.label("original_id"),
            PartnerProduct.id.label("canonical_id"),
            PartnerProduct.merged_into_id,
            array([PartnerProduct.id]).label("path"),
            literal(0).label("depth"),
        )
        .where(PartnerProduct.id.in_(select(scoped.c.product_id)))
        .cte("report_identity", recursive=True)
    )
    parent = PartnerProduct.__table__.alias("report_parent")
    identities = identities.union_all(
        select(
            identities.c.original_id,
            parent.c.id,
            parent.c.merged_into_id,
            identities.c.path + array([parent.c.id]),
            identities.c.depth + 1,
        )
        .join(parent, parent.c.id == identities.c.merged_into_id)
        .where(identities.c.depth < 32, parent.c.id != any_(identities.c.path))
    )
    resolved = (
        select(identities.c.original_id, identities.c.canonical_id)
        .distinct(identities.c.original_id)
        .order_by(identities.c.original_id, identities.c.depth.desc())
        .subquery()
    )
    product = PartnerProduct.__table__.alias("report_product")
    canonical = func.coalesce(resolved.c.canonical_id, scoped.c.product_id)
    query = (
        select(
            scoped, canonical.label("canonical_product_id"), product.c.name.label("product_name")
        )
        .select_from(scoped)
        .outerjoin(resolved, resolved.c.original_id == scoped.c.product_id)
        .outerjoin(product, product.c.id == canonical)
    )
    return [cast(ReportingAsset, dict(row)) for row in session.execute(query).mappings().all()]


def _totals(impressions: int, clicks: int, days: Collection[date | str]) -> MetricTotals:
    return {
        "impressions": impressions,
        "clicks": clicks,
        "ctr": clicks / impressions * 100 if impressions else None,
        "measured_days": len(days),
    }


def _timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _group(
    rows: Sequence[ReportingAsset],
) -> tuple[dict[GroupKey, list[ReportingAsset]], dict[str, GroupKey]]:
    groups: dict[GroupKey, list[ReportingAsset]] = {}
    by_asset: dict[str, GroupKey] = {}
    for row in rows:
        product = row["canonical_product_id"]
        key = (row["account_id"], "product" if product else "asset", product or row["id"])
        group = groups.setdefault(key, [])
        group.append(row)
        by_asset[str(row["id"])] = key
    return groups, by_asset


def _asset(
    groups: Mapping[GroupKey, list[ReportingAsset]], stats: Mapping[GroupKey, MetricStat]
) -> list[AssetMetricData]:
    items: list[AssetMetricData] = []
    status_order = {"active": 0, "paused": 1, "draft": 2, "ended": 3}
    for key, rows in groups.items():
        stat = stats[key]
        first = min(rows, key=lambda row: str(row["id"]))
        products = [name for row in rows if (name := row["product_name"]) is not None]
        items.append(
            {
                "id": first["id"],
                "account_id": first["account_id"],
                "account_name": first["account_name"],
                "product_id": first["canonical_product_id"],
                "name": min(products) if products else min(row["name"] for row in rows),
                "kind": "ad" if any(row["kind"] == "ad" for row in rows) else "product",
                "status": min(
                    (row["status"] for row in rows), key=lambda s: status_order.get(s, 3)
                ),
                "automatic": all(row["automatic"] for row in rows),
                "aggregated": len(rows) > 1,
                "ad_format": min(row["ad_format"] for row in rows),
                "ad_formats": sorted({row["ad_format"] for row in rows}),
                "campaign_id": first["campaign_id"] if len(rows) == 1 else None,
                "article_id": first["article_id"] if len(rows) == 1 else None,
                **_totals(stat["impressions"], stat["clicks"], stat["days"]),
                "_measured": bool(stat["days"]),
            }
        )
    return items


def _order(
    items: list[AssetMetricData], sort: ReportSort, order: ReportOrder
) -> list[AssetMetricData]:
    def compare(left: AssetMetricData, right: AssetMetricData) -> int:
        def value(row: AssetMetricData) -> str | int | float | None:
            if sort == "name":
                return row["name"].lower()
            if sort == "account":
                return row["account_name"].lower()
            if sort in {"impressions", "clicks"} and not row["_measured"]:
                return None
            if sort == "impressions":
                return row["impressions"]
            if sort == "clicks":
                return row["clicks"]
            return row["ctr"]

        a, b = value(left), value(right)
        if a is None or b is None:
            result = (a is None) - (b is None)
        else:
            if isinstance(a, str):
                assert isinstance(b, str)
                result = (a > b) - (a < b)
            else:
                assert isinstance(b, (int, float))
                result = (a > b) - (a < b)
            if order == "desc":
                result = -result
        if result:
            return result
        secondary = []
        if sort != "name":
            secondary.append((left["name"].lower(), right["name"].lower()))
        if sort != "account":
            secondary.append((left["account_name"].lower(), right["account_name"].lower()))
        secondary.append((str(left["id"]), str(right["id"])))
        return next(((x > y) - (x < y) for x, y in secondary if x != y), 0)

    return sorted(items, key=cmp_to_key(compare))


async def reporting_metrics(
    assets: Sequence[ReportingAsset],
    analytics: DirectEngagementAnalytics,
    days: int = 30,
    limit: int = 100,
    offset: int = 0,
    *,
    sort: ReportSort = "name",
    order: ReportOrder = "asc",
) -> ReportingMetrics:
    """Read bounded CH batches and sort the complete authorized account scope."""
    if sort not in {"name", "account", "impressions", "clicks", "ctr"} or order not in {
        "asc",
        "desc",
    }:
        raise ValueError("Invalid partner report ordering")
    if not 1 <= days <= 365 or not 1 <= limit <= 200 or offset < 0:
        raise ValueError("Invalid partner report range")
    groups, by_asset = _group(assets)
    asset_format = {str(row["id"]): row["ad_format"] for row in assets}
    ids = list(by_asset)
    stats: dict[GroupKey, MetricStat] = defaultdict(
        lambda: {"impressions": 0, "clicks": 0, "days": set()}
    )
    formats: dict[str, MetricStat] = defaultdict(
        lambda: {"impressions": 0, "clicks": 0, "days": set()}
    )
    trend: dict[date | str, dict[str, int]] = defaultdict(lambda: {"impressions": 0, "clicks": 0})
    total: MetricStat = {"impressions": 0, "clicks": 0, "days": set()}
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days - 1)
    last_updated_at = None
    for position in range(0, len(ids), _ASSET_CHUNK):
        rows = await analytics.partner_daily(
            ids[position : position + _ASSET_CHUNK], start=start, end=end
        )
        for row in rows:
            identity, day = row["owner"], row["day"]
            if identity not in by_asset:
                raise ValueError("Unauthorized partner metrics identity")
            impressions, clicks = int(row["impressions"]), int(row["clicks"])
            for stat in (stats[by_asset[identity]], formats[asset_format[identity]], total):
                stat["impressions"] += impressions
                stat["clicks"] += clicks
                stat["days"].add(day)
            trend[day]["impressions"] += impressions
            trend[day]["clicks"] += clicks
            stamp = _timestamp(row["updated_at"])
            if last_updated_at is None or stamp > last_updated_at:
                last_updated_at = stamp
    items = _order(_asset(groups, stats), sort, order)
    result: list[AssetMetricData] = []
    for item in items[offset : offset + limit]:
        result.append(
            cast(AssetMetricData, {key: value for key, value in item.items() if key != "_measured"})
        )
    return {
        "start": start,
        "end": end,
        "totals": _totals(total["impressions"], total["clicks"], total["days"]),
        "format_totals": [
            {"ad_format": key, **_totals(value["impressions"], value["clicks"], value["days"])}
            for key, value in sorted(formats.items())
        ],
        "last_updated_at": last_updated_at,
        "trend": [
            {"day": key, "impressions": value["impressions"], "clicks": value["clicks"]}
            for key, value in sorted(trend.items())
        ],
        "assets": result,
        "asset_total": len(items),
    }
