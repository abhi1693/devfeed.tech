"""Private aggregate browser-push outcomes, grouped by UTC publication cohort."""

from datetime import UTC, date, datetime, time, timedelta

from devfeed_core.models import WebPushDelivery, WebPushEvent, WebPushSubscription, utcnow
from devfeed_core.web_push import get_web_push_settings
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(prefix="/v1/admin", tags=["admin-push-analytics"])


class PushAnalyticsMetrics(BaseModel):
    published_events: int = Field(default=0, ge=0)
    recipient_accounts: int = Field(default=0, ge=0)
    browser_deliveries: int = Field(default=0, ge=0)
    accepted: int = Field(default=0, ge=0)
    displayed: int = Field(default=0, ge=0)
    clicked: int = Field(default=0, ge=0)
    opened: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    queued: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    click_rate: float = Field(default=0, ge=0, le=100)


class PushAnalyticsDay(PushAnalyticsMetrics):
    date: date


class PushAnalyticsKind(PushAnalyticsMetrics):
    kind: str


class PushAnalytics(BaseModel):
    generated_at: datetime
    enabled: bool
    days: int
    enabled_subscriptions: int
    enabled_accounts: int
    totals: PushAnalyticsMetrics
    daily: list[PushAnalyticsDay]
    by_kind: list[PushAnalyticsKind]


def _delivery_counts():
    delivery = WebPushDelivery
    return (
        func.count(func.distinct(delivery.user_id)).label("recipient_accounts"),
        func.count(delivery.id).label("browser_deliveries"),
        func.count().filter(delivery.accepted_at.is_not(None)).label("accepted"),
        func.count().filter(delivery.displayed_at.is_not(None)).label("displayed"),
        func.count().filter(delivery.clicked_at.is_not(None)).label("clicked"),
        func.count().filter(delivery.opened_at.is_not(None)).label("opened"),
        func.count().filter(delivery.status == "failed").label("failed"),
        func.count()
        .filter(delivery.status == "succeeded", delivery.accepted_at.is_(None))
        .label("skipped"),
        func.count().filter(delivery.status == "queued").label("queued"),
        func.count().filter(delivery.status == "running").label("running"),
        func.coalesce(func.sum(func.greatest(delivery.attempts - 1, 0)), 0).label("retries"),
        func.count()
        .filter(delivery.displayed_at.is_not(None), delivery.clicked_at.is_not(None))
        .label("clicked_from_displayed"),
    )


def _metrics(row, published_events=0):
    counts = dict(row) if row is not None else {}
    clicked_from_displayed = counts.pop("clicked_from_displayed", 0)
    displayed = counts.get("displayed", 0)
    click_rate = 0.0
    if displayed > 0:
        click_rate = round(clicked_from_displayed * 100 / displayed, 2)
    return PushAnalyticsMetrics(
        published_events=published_events,
        click_rate=click_rate,
        **counts,
    )


def push_analytics_metrics(session: Session, days: int, *, now=None) -> PushAnalytics:
    """Reports outcomes for events published in the range, including later receipts.

    Enabled registrations use their stored absolute authorization deadline; this
    inventory does not claim to check live Redis sessions. Browser receipts are
    independently reported evidence, distinct from relay acceptance or reading.
    """
    if not 1 <= days <= 90:
        raise ValueError("Push analytics days must be between 1 and 90")
    now = (now or utcnow()).astimezone(UTC)
    start = datetime.combine(now.date() - timedelta(days=days - 1), time.min, UTC)
    cohort = (WebPushEvent.created_at >= start, WebPushEvent.created_at <= now)
    publication_day = func.date(func.timezone("UTC", WebPushEvent.created_at))
    events = list(
        session.execute(
            select(publication_day, WebPushEvent.kind, func.count())
            .where(*cohort)
            .group_by(publication_day, WebPushEvent.kind)
        )
    )
    published_daily: dict[date, int] = {}
    published_kinds: dict[str, int] = {}
    for day, kind, count in events:
        published_daily[day] = published_daily.get(day, 0) + count
        published_kinds[kind] = published_kinds.get(kind, 0) + count

    deliveries = (
        select(*_delivery_counts())
        .select_from(WebPushDelivery)
        .join(WebPushEvent, WebPushEvent.id == WebPushDelivery.event_id)
        .where(*cohort)
    )
    totals = session.execute(deliveries).mappings().one()
    daily_counts = {
        row["date"]: {key: value for key, value in row.items() if key != "date"}
        for row in session.execute(
            deliveries.add_columns(publication_day.label("date")).group_by(publication_day)
        ).mappings()
    }
    kind_counts = {
        row["kind"]: {key: value for key, value in row.items() if key != "kind"}
        for row in session.execute(
            deliveries.add_columns(WebPushEvent.kind).group_by(WebPushEvent.kind)
        ).mappings()
    }
    enabled_subscriptions, enabled_accounts = session.execute(
        select(
            func.count(WebPushSubscription.id),
            func.count(func.distinct(WebPushSubscription.user_id)),
        ).where(
            WebPushSubscription.enabled.is_(True),
            WebPushSubscription.authorization_expires_at > now,
        )
    ).one()
    return PushAnalytics(
        generated_at=now,
        enabled=get_web_push_settings().web_push_enabled,
        days=days,
        enabled_subscriptions=enabled_subscriptions,
        enabled_accounts=enabled_accounts,
        totals=_metrics(totals, sum(published_daily.values())),
        daily=[
            PushAnalyticsDay(
                date=day,
                **_metrics(daily_counts.get(day), published_daily.get(day, 0)).model_dump(),
            )
            for day in (start.date() + timedelta(days=offset) for offset in range(days))
        ],
        by_kind=[
            PushAnalyticsKind(
                kind=kind,
                **_metrics(kind_counts.get(kind), published_kinds[kind]).model_dump(),
            )
            for kind in sorted(published_kinds)
        ],
    )


@router.get("/push-analytics", response_model=PushAnalytics, operation_id="admin_push_analytics")
def push_analytics(admin: Admin, session: DB, days: int = Query(default=30, ge=1, le=90)):
    return push_analytics_metrics(session, days)
