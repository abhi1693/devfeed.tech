"""Generic Feed sponsorship cannot bypass known context; Header is separate branding."""

import uuid

import pytest
from devfeed_core.models import Article, PartnerProductPromotion, Source, UserTopic
from devfeed_http.partner_placements import PlacementQuery
from test_partner_feed_placements import feed_catalog as feed_catalog
from test_partner_native_formats import native_account
from test_partner_placements_integration import catalog as catalog
from test_partner_placements_integration import deliver

pytestmark = pytest.mark.integration


@pytest.fixture
def unmatched_sponsor(database, feed_catalog):
    native_account(database, feed_catalog, ["feed", "header_sponsorship"])
    with database.begin() as session:
        session.add(
            PartnerProductPromotion(
                account_id=feed_catalog["paid"],
                product_id=feed_catalog["paid_product"],
                excluded=True,
            )
        )
    return feed_catalog


def test_known_feed_context_does_not_fall_back_to_an_unrelated_feed_sponsor(
    database, unmatched_sponsor
):
    payload = dict(
        surface="feed",
        article_ids=[unmatched_sponsor["article"]],
        allow_sponsored_fallback=True,
    )
    rows = deliver(database, PlacementQuery(**payload, ad_formats=["feed"]))
    assert len(rows) == 1 and not rows[0]["sponsored"]
    assert rows[0]["product_id"] == unmatched_sponsor["free_product"]
    rows = deliver(
        database,
        PlacementQuery(
            surface="header", article_ids=payload["article_ids"], ad_formats=["header_sponsorship"]
        ),
    )
    assert len(rows) == 1
    sponsorship = rows[0]
    assert sponsorship["ad_format"] == "header_sponsorship"
    assert sponsorship["product_id"] == unmatched_sponsor["unrelated"]


@pytest.mark.parametrize("context", ["unknown", "unpublished", "private_source"])
def test_invalid_supplied_context_cannot_enable_generic_feed_fallback(
    database, unmatched_sponsor, context
):
    article_id = unmatched_sponsor["article"]
    if context == "unknown":
        article_id = uuid.uuid4()
    else:
        with database.begin() as session:
            if context == "unpublished":
                session.get(Article, article_id).publication_status = "unpublished"
            else:
                session.get(Source, unmatched_sponsor["source"]).approval_status = "pending"
    payload = dict(surface="feed", article_ids=[article_id], allow_sponsored_fallback=True)
    assert deliver(database, PlacementQuery(**payload, ad_formats=["feed"])) == []
    branding = deliver(
        database,
        PlacementQuery(
            surface="header", article_ids=[article_id], ad_formats=["header_sponsorship"]
        ),
    )
    assert len(branding) == 1 and branding[0]["ad_format"] == "header_sponsorship"
    assert branding[0]["article"] is None


def test_contextless_feed_standing_sponsorship_remains_available(database, unmatched_sponsor):
    payload = PlacementQuery(surface="feed", allow_sponsored_fallback=True)
    rows = deliver(database, payload)
    assert len(rows) == 1 and rows[0]["sponsored"]
    assert rows[0]["product_id"] == unmatched_sponsor["unrelated"]


def test_resolved_personal_context_prevents_generic_feed_fallback(database, unmatched_sponsor):
    with database.begin() as session:
        session.add(
            UserTopic(user_id=unmatched_sponsor["user"], topic_id=unmatched_sponsor["topic"])
        )
    payload = dict(surface="feed", allow_sponsored_fallback=True)
    rows = deliver(
        database,
        PlacementQuery(**payload, ad_formats=["feed"]),
        user_id=unmatched_sponsor["user"],
    )
    assert len(rows) == 1 and not rows[0]["sponsored"]
    branding = deliver(
        database,
        PlacementQuery(surface="header", ad_formats=["header_sponsorship"]),
        user_id=unmatched_sponsor["user"],
    )
    assert len(branding) == 1 and branding[0]["ad_format"] == "header_sponsorship"
