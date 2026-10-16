"""Bounded verified-reader cohorts and first original-open outcomes."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.models import (
    ReaderMilestoneDelivery,
    ReaderSignup,
    UserAccount,
    UserReadingEvent,
    utcnow,
)

ACQUISITION_SOURCES = frozenset(
    {
        "google",
        "bing",
        "duckduckgo",
        "yahoo",
        "baidu",
        "yandex",
        "github",
        "x",
        "reddit",
        "hacker_news",
        "linkedin",
        "mastodon",
        "bluesky",
        "nick_launches",
        "newsletter",
        "other",
    }
)
ACQUISITION_MEDIA = frozenset(
    {"organic", "referral", "social", "email", "cpc", "paid_social", "none", "unknown"}
)
LANDING_ROUTES = frozenset(
    {
        "home",
        "latest",
        "article",
        "topics",
        "topic",
        "sources",
        "source",
        "search",
        "trending",
        "profile",
        "other",
        "unavailable",
    }
)
CLIENT_PLATFORMS = frozenset({"web", "chrome_extension", "edge_extension"})
CAMPAIGN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ACTIVATION_WINDOW = timedelta(days=7)


class AcquisitionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    status: Literal["attributed", "direct", "unknown", "unavailable"]
    source: str | None = Field(default=None, max_length=64)
    medium: str | None = Field(default=None, max_length=64)
    campaign: str | None = Field(default=None, max_length=64)
    landing_route: str
    captured_at: int
    client_platform: Literal["web", "chrome_extension", "edge_extension"]
    measurement: Literal["eligible", "unavailable", "disabled"]
    client_id: str | None = Field(default=None, pattern=r"^[0-9]{1,10}\.[0-9]{1,10}$")
    session_id: int | None = Field(default=None, gt=0, le=9_007_199_254_740_991)

    @model_validator(mode="after")
    def bounded_context(self):
        if self.landing_route not in LANDING_ROUTES:
            raise ValueError("Landing route must be coarse")
        if self.source is not None and self.source not in ACQUISITION_SOURCES | {"direct"}:
            raise ValueError("Unknown acquisition source")
        if self.medium is not None and self.medium not in ACQUISITION_MEDIA:
            raise ValueError("Unknown acquisition medium")
        if self.campaign is not None and not CAMPAIGN.fullmatch(self.campaign):
            raise ValueError("Campaign must be a configured label")
        if self.status == "attributed" and (
            self.source not in ACQUISITION_SOURCES or self.medium in {None, "none", "unknown"}
        ):
            raise ValueError("Attribution requires a known source and medium")
        if self.status == "direct" and (self.source, self.medium, self.campaign) != (
            "direct",
            "none",
            None,
        ):
            raise ValueError("Direct attribution has no campaign")
        if self.status == "unknown" and (self.source, self.medium, self.campaign) != (
            None,
            "unknown",
            None,
        ):
            raise ValueError("Unknown attribution must not retain arbitrary values")
        if self.status == "unavailable" and (
            any((self.source, self.medium, self.campaign)) or self.landing_route != "unavailable"
        ):
            raise ValueError("Unavailable attribution must not retain context")
        if self.measurement == "eligible":
            if self.client_id is None or self.session_id is None:
                raise ValueError("Measurement requires existing client and session identifiers")
        elif self.client_id is not None or self.session_id is not None:
            raise ValueError("Unavailable measurement cannot retain identifiers")
        if self.measurement == "disabled" and self.status != "unavailable":
            raise ValueError("Disabled measurement cannot retain acquisition")
        return self


def unavailable_context(*, now=None, disabled=False) -> dict:
    return {
        "version": 1,
        "status": "unavailable",
        "source": None,
        "medium": None,
        "campaign": None,
        "landing_route": "unavailable",
        "captured_at": int((now or utcnow()).timestamp()),
        "client_platform": "web",
        "measurement": "disabled" if disabled else "unavailable",
    }


def sanitize_context(value, *, campaigns=(), now=None) -> dict:
    """Malformed, stale or personal capture is unavailable; never fail authentication."""
    moment = now or utcnow()
    try:
        body = AcquisitionContext.model_validate(value)
        captured = datetime.fromtimestamp(body.captured_at, UTC)
        if not moment - timedelta(days=30) <= captured <= moment + timedelta(minutes=5):
            raise ValueError("Acquisition capture is stale")
        if body.campaign not in campaigns:
            body.campaign = None
        return body.model_dump(exclude_none=True)
    except (ValueError, TypeError, OverflowError, OSError):
        return unavailable_context(now=moment)


def _delivery(
    session,
    signup,
    event_name,
    occurred_at,
    context,
    *,
    bound_client_id=None,
    bound_platform=None,
    historical=False,
):
    permitted = context.get("measurement") == "eligible"
    reason = "ready" if permitted else "measurement_unavailable"
    if context.get("measurement") == "disabled":
        reason = "analytics_disabled"
    if (
        event_name == "reader_activation"
        and permitted
        and (
            not bound_client_id
            or context.get("client_id") != bound_client_id
            or context.get("client_platform") != bound_platform
        )
    ):
        permitted, reason = False, "unbound_client"
    if historical:
        permitted, reason = False, "historical_activation"
    payload = {}
    if permitted:
        payload = {
            "client_id": context["client_id"],
            "session_id": context["session_id"],
            "client_platform": context["client_platform"],
            "acquisition_status": signup.acquisition_status,
            "source": signup.source,
            "medium": signup.medium,
            "campaign": signup.campaign,
            "landing_route": signup.landing_route,
        }
    session.execute(
        insert(ReaderMilestoneDelivery)
        .values(
            user_id=signup.user_id,
            event_name=event_name,
            id=uuid.uuid4(),
            occurred_at=occurred_at,
            status="pending" if permitted else "unavailable",
            reason=reason,
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "event_name"])
    )


def record_verified_signup(session, user_id, signup_at, context) -> bool:
    """The callback-owned account marker is authoritative; caller owns commit."""
    marker = session.scalar(select(UserAccount.reader_signup_at).where(UserAccount.id == user_id))
    if marker is None or marker != signup_at:
        raise ValueError("Reader signup requires its committed-account marker")
    recorded = session.scalar(
        insert(ReaderSignup)
        .values(
            user_id=user_id,
            signup_at=signup_at,
            acquisition_at=datetime.fromtimestamp(context["captured_at"], UTC)
            if context["status"] != "unavailable"
            else None,
            acquisition_status=context["status"],
            source=context.get("source"),
            medium=context.get("medium"),
            campaign=context.get("campaign"),
            landing_route=context["landing_route"],
            client_platform=context["client_platform"],
        )
        .on_conflict_do_nothing(index_elements=["user_id"])
        .returning(ReaderSignup.user_id)
    )
    if recorded is None:
        return False
    signup = session.get(ReaderSignup, user_id)
    _delivery(session, signup, "sign_up", signup_at, context)
    return True


def record_reader_activation(
    session,
    user_id,
    context,
    *,
    bound_client_id=None,
    bound_platform=None,
    current_open_at=None,
    current_article_id=None,
) -> bool:
    """First recorded original open in [signup, signup + 7 days), exactly once."""
    marker = session.scalar(
        select(UserAccount.reader_signup_at).where(UserAccount.id == user_id).with_for_update()
    )
    if marker is None:
        return False
    signup = session.get(ReaderSignup, user_id)
    if signup is None:
        record_verified_signup(session, user_id, marker, unavailable_context())
        signup = session.get(ReaderSignup, user_id)
    if signup.activated_at is not None:
        return False
    reading = session.scalar(
        select(UserReadingEvent)
        .where(
            UserReadingEvent.user_id == user_id,
            UserReadingEvent.source == "article_open",
            UserReadingEvent.occurred_at >= signup.signup_at,
            UserReadingEvent.occurred_at < signup.signup_at + ACTIVATION_WINDOW,
        )
        .order_by(UserReadingEvent.occurred_at, UserReadingEvent.article_id)
        .limit(1)
    )
    if reading is None:
        return False
    signup.activated_at, signup.activation_article_id = reading.occurred_at, reading.article_id
    _delivery(
        session,
        signup,
        "reader_activation",
        reading.occurred_at,
        context,
        bound_client_id=bound_client_id,
        bound_platform=bound_platform,
        historical=reading.occurred_at != current_open_at
        or reading.article_id != current_article_id,
    )
    session.flush()
    return True
