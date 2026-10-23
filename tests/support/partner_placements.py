"""Disposable partner placement catalogs and delivery helpers."""

import secrets
import uuid
from datetime import timedelta

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    PartnerAccount,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerMatchJob,
    PartnerProduct,
    Source,
    Topic,
    UserAccount,
    utcnow,
)
from devfeed_core.partner_matching import refresh_partner_matches
from devfeed_core.partner_placements import deliver_placements
from devfeed_http.partner_placements import PlacementQuery
from pydantic import SecretStr
from sqlalchemy import func, select

KEY = secrets.token_urlsafe(32)


@pytest.fixture
def catalog(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    ids = {
        name: uuid.uuid4()
        for name in [
            "free",
            "paid",
            "free_product",
            "paid_product",
            "unrelated",
            "topic",
            "other_topic",
            "source",
            "article",
            "campaign",
            "user",
        ]
    }
    with database.begin() as session:
        session.add_all(
            [
                PartnerAccount(id=ids["free"], name="Free partner", tier="bronze"),
                PartnerAccount(
                    id=ids["paid"], name="Paid partner", tier="diamond", promotion_kind="product"
                ),
                Topic(
                    id=ids["topic"],
                    name="Kubernetes",
                    slug="kubernetes",
                    kind="platform",
                    status="active",
                ),
                Topic(
                    id=ids["other_topic"],
                    name="Figma",
                    slug="figma",
                    kind="product",
                    status="active",
                ),
                Source(
                    id=ids["source"],
                    name="Publisher",
                    feed_url="https://publisher.example/rss",
                    source_type="publisher",
                    approval_status="approved",
                ),
                UserAccount(
                    id=ids["user"],
                    issuer="https://identity.example",
                    subject="reader",
                    organization_id="org",
                ),
            ]
        )
        session.flush()
        for account in ("free", "paid"):
            session.add(
                PartnerConnection(
                    provider=account, name=account, account_id=ids[account], enabled=True
                )
            )
        session.flush()
        for name in ("free_product", "paid_product", "unrelated"):
            technology = "Figma" if name == "unrelated" else "Kubernetes"
            quote = (
                f"Monitor {technology} clusters and inspect metrics "
                "with independently configured alerts."
            )
            session.add(
                PartnerProduct(
                    id=ids[name],
                    name=name,
                    product_url=f"https://tools.example/{name}",
                    tagline=f"A specific {technology} product for practical work.",
                    technologies=[technology],
                    evidence=[
                        {
                            "url": f"https://tools.example/{name}",
                            "quote": quote,
                            "capability": f"Monitor {technology} clusters and inspect metrics",
                        }
                    ],
                    status="approved",
                    verified_at=utcnow(),
                )
            )
        session.flush()
        for name in ("free_product", "paid_product", "unrelated"):
            session.add(
                PartnerListing(
                    product_id=ids[name],
                    provider="free" if name == "free_product" else "paid",
                    external_id=name,
                    name=name,
                    product_url=f"https://tools.example/{name}",
                    listing_url=f"https://catalog.example/{name}",
                    tagline="Verified listing.",
                )
            )
        session.add(
            PartnerCampaign(
                id=ids["campaign"],
                account_id=ids["paid"],
                name="Paid agreement",
                status="active",
                targeting="all",
                starts_at=utcnow() - timedelta(days=1),
                ends_at=utcnow() + timedelta(days=1),
                daily_impression_limit=100,
            )
        )
        item = Article(
            id=ids["article"],
            canonical_url="https://publisher.example/tutorial",
            url_hash=uuid.uuid4().hex,
            title="Monitor Kubernetes clusters",
            summary=(
                "Monitor Kubernetes clusters and inspect metrics "
                "in this practical production tutorial."
            ),
            content_type="tutorial",
            review_status="approved",
            publication_status="published",
        )
        session.add(item)
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=item.id,
                source_id=ids["source"],
                entry_key="tutorial",
                original_url=item.canonical_url,
            )
        )
    refresh_partner_matches(database)
    return ids


def query(catalog, surface="article", **kwargs):
    return PlacementQuery(
        surface=surface,
        **(
            {"article_id": catalog["article"]}
            if surface == "article"
            else {"topic_id": catalog["topic"]}
            if surface == "topic"
            else {}
        ),
        **kwargs,
    )


def deliver(database, payload, **kwargs):
    with database.begin() as session:
        return deliver_placements(session, payload, KEY, **kwargs)


def finish_matching(database, *, product_limit=10, article_limit=100, now=None):
    """Finish bounded preparation jobs, including slices with no matching pairs."""
    processed = 0
    for _ in range(100):
        with database() as session:
            pending = session.scalar(select(func.count()).select_from(PartnerMatchJob))
        if not pending:
            return processed
        processed += refresh_partner_matches(
            database, product_limit=product_limit, article_limit=article_limit, now=now
        )
    pytest.fail("Matching jobs did not finish within the bounded fixture budget")
