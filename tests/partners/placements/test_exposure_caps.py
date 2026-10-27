"""Product repetition is unrestricted while commercial budgets and ledgers stay intact."""

import pytest
from devfeed_core.models import PartnerCampaign, PartnerPlacementDelivery
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_tracking import PartnerTrackingTokens, record_delivery_event
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import func, select

from tests.support.partner_placements import KEY
from tests.support.partner_placements import catalog as catalog_fixture

catalog = catalog_fixture


@pytest.mark.integration
def test_same_reader_can_receive_product_past_twenty_appearances_until_campaign_budget(
    database, catalog
):
    with database.begin() as session:
        session.get(PartnerCampaign, catalog["campaign"]).daily_impression_limit = 25
    query = PlacementQuery(surface="topic", topic_id=catalog["topic"])
    for _ in range(25):
        with database.begin() as session:
            items = deliver_placements(session, query, KEY, user_id=catalog["user"])
            assert len(items) == 2 and len({item["product_id"] for item in items}) == 2
            assert all("frequency_cap" not in item for item in items)
            sponsored = next(item for item in items if item["sponsored"])
            assert sponsored["product_id"] == catalog["paid_product"]
            assert record_delivery_event(
                session, PartnerTrackingTokens(KEY).verify(sponsored["receipt"]), "impression"
            )
    with database.begin() as session:
        items = deliver_placements(session, query, KEY, user_id=catalog["user"])
        assert items and all(not item["sponsored"] for item in items)
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerPlacementDelivery)
                .where(PartnerPlacementDelivery.impression_recorded_at.is_not(None))
            )
            == 25
        )
