"""Bounded signup cohorts, acquisition coverage and first-open outcomes in UTC."""

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Date, and_, cast, func, or_, select
from sqlalchemy.orm import Session, aliased

from devfeed_core.models import (
    ReaderMilestoneDelivery,
    ReaderSignup,
    UserAccount,
    UserReadingEvent,
    utcnow,
)

STATUSES = ("unavailable", "pending", "attempted", "http_accepted", "failed", "unknown")
MAX_DAYS = 93
MAX_GROUPS = 2000


def _count(condition):
    return func.count().filter(condition)


def _delivery_counts(delivery, milestone, prefix):
    return [
        _count(
            and_(
                milestone,
                or_(delivery.status == status, delivery.id.is_(None))
                if status == "unavailable"
                else delivery.status == status,
            )
        ).label(f"{prefix}_{status}")
        for status in STATUSES
    ] + [_count(and_(milestone, delivery.id.is_(None))).label(f"{prefix}_missing")]


def _delivery_result(row, prefix):
    statuses = {status: int(row[f"{prefix}_{status}"]) for status in STATUSES}
    return {
        "eligible": sum(value for key, value in statuses.items() if key != "unavailable"),
        "status_counts": statuses,
        "missing_measurement_rows": int(row[f"{prefix}_missing"]),
    }


def reader_acquisition_report(
    session: Session, *, start: date, end: date, observed_at: datetime | None = None
) -> dict:
    """Select signup dates [start, end), grouping recorded acquisition date/source.

    Acquisition date can precede the selected signup window. A missing acquisition
    date remains null; it is never relabeled as the account's signup date. Counts
    come from verified account markers and retained authenticated reading events,
    so unavailable attribution or an optional measurement-write failure cannot
    turn a committed signup or original-article open into an analytics zero.
    """
    observed = observed_at or utcnow()
    if observed.tzinfo is None:
        raise ValueError("Observation time must include its UTC offset")
    observed = observed.astimezone(UTC)
    if not 0 < (end - start).days <= MAX_DAYS or start > observed.date():
        raise ValueError("Select between 1 and 93 UTC signup dates ending after the start")
    if end > observed.date() + timedelta(days=1):
        raise ValueError("The signup window cannot include future dates")
    beginning = datetime.combine(start, time.min, UTC)
    ending = datetime.combine(end, time.min, UTC)
    accounts = (
        select(UserAccount.id.label("user_id"), UserAccount.reader_signup_at.label("signup_at"))
        .where(
            UserAccount.reader_signup_at >= beginning,
            UserAccount.reader_signup_at < ending,
            UserAccount.reader_signup_at <= observed,
        )
        .cte("verified_reader_cohort")
    )
    first_open = (
        select(
            UserReadingEvent.user_id,
            func.min(UserReadingEvent.occurred_at).label("activated_at"),
        )
        .join(accounts, accounts.c.user_id == UserReadingEvent.user_id)
        .where(
            UserReadingEvent.source == "article_open",
            UserReadingEvent.occurred_at >= accounts.c.signup_at,
            UserReadingEvent.occurred_at < accounts.c.signup_at + timedelta(days=7),
            UserReadingEvent.occurred_at <= observed,
        )
        .group_by(UserReadingEvent.user_id)
        .cte("first_original_open")
    )
    signup_delivery = aliased(ReaderMilestoneDelivery)
    activation_delivery = aliased(ReaderMilestoneDelivery)
    acquisition_date = cast(func.timezone("UTC", ReaderSignup.acquisition_at), Date)
    attribution = func.coalesce(ReaderSignup.acquisition_status, "unavailable")
    activated = first_open.c.activated_at.is_not(None)
    mature = accounts.c.signup_at + timedelta(days=7) <= observed
    counters = [
        func.count().label("verified_signups"),
        _count(activated).label("activated"),
        _count(and_(~activated, ~mature)).label("pending_window"),
        _count(and_(~activated, mature)).label("matured_inactive"),
        _count(ReaderSignup.user_id.is_(None)).label("missing_signup_rows"),
        _count(and_(activated, ReaderSignup.activated_at.is_(None))).label(
            "missing_activation_rows"
        ),
        *_delivery_counts(signup_delivery, True, "signup"),
        *_delivery_counts(activation_delivery, activated, "activation"),
    ]
    statement = (
        select(
            acquisition_date.label("acquisition_date"),
            attribution.label("attribution_state"),
            ReaderSignup.source,
            *counters,
        )
        .select_from(accounts)
        .outerjoin(ReaderSignup, ReaderSignup.user_id == accounts.c.user_id)
        .outerjoin(first_open, first_open.c.user_id == accounts.c.user_id)
        .outerjoin(
            signup_delivery,
            and_(
                signup_delivery.user_id == accounts.c.user_id,
                signup_delivery.event_name == "sign_up",
            ),
        )
        .outerjoin(
            activation_delivery,
            and_(
                activation_delivery.user_id == accounts.c.user_id,
                activation_delivery.event_name == "reader_activation",
            ),
        )
        .group_by(acquisition_date, attribution, ReaderSignup.source)
        .order_by(acquisition_date.asc().nulls_last(), attribution, ReaderSignup.source)
        .limit(MAX_GROUPS + 1)
    )
    rows = list(session.execute(statement).mappings())
    if len(rows) > MAX_GROUPS:
        raise ValueError("Too many acquisition groups; select a smaller signup window")
    count_names = [column.name for column in counters]
    totals = {name: sum(int(row[name]) for row in rows) for name in count_names}
    outcomes = ("verified_signups", "activated", "pending_window", "matured_inactive")
    groups = [
        {
            "acquisition_date": row["acquisition_date"].isoformat()
            if row["acquisition_date"]
            else None,
            "attribution_state": row["attribution_state"],
            "source": row["source"],
            **{name: int(row[name]) for name in outcomes},
            "ga_sign_up": _delivery_result(row, "signup"),
            "ga_reader_activation": _delivery_result(row, "activation"),
        }
        for row in rows
    ]
    return {
        "version": 1,
        "observed_at": observed.isoformat(),
        "signup_window_utc": {
            "start_inclusive": beginning.isoformat(),
            "end_exclusive": ending.isoformat(),
        },
        "definitions": {
            "signup": "Committed new account through the verified reader OIDC callback.",
            "activation": "First retained authenticated original-article open in "
            "[signup_at, signup_at + 7 days); an outbound click, not reading completion.",
            "acquisition_date": "UTC date of bounded browser first observation; client-supplied "
            "capture linked through the verified flow, not a verified visit timestamp. "
            "Null means unavailable and can never be assigned to a known visit date.",
            "pending_window": "Not activated; seven-day observation window still open.",
            "matured_inactive": "No recorded activation in a completed seven-day window.",
            "ga_http_accepted": "HTTP acknowledgment only; GA ingestion remains unverified.",
            "ga_attempted": "Claimed once; unresolved or interrupted delivery, never auto-retried.",
            "ga_eligible": "Delivery rows eligible for an attempt, excluding unavailable rows. "
            "This is not a count of all measurable visitors.",
            "ga_status_observation": "Current delivery bookkeeping at query time, not historical "
            "provider ingestion. The observation cutoff filters first-party outcome timestamps.",
            "scope": "Signup cohort outcomes, not visitor conversion rates or retention. "
            "Accounts without a verified reader signup marker are excluded.",
        },
        "totals": {name: totals[name] for name in outcomes},
        "attribution_coverage": {
            state: sum(
                group["verified_signups"] for group in groups if group["attribution_state"] == state
            )
            for state in ("attributed", "direct", "unknown", "unavailable")
        },
        "first_party_gaps": {
            "missing_signup_rows": totals["missing_signup_rows"],
            "missing_activation_rows": totals["missing_activation_rows"],
        },
        "ga_sign_up": _delivery_result(totals, "signup"),
        "ga_reader_activation": _delivery_result(totals, "activation"),
        "groups": groups,
    }
