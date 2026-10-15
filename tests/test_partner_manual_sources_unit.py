"""Direct account inventory follows the same delivery, policy and review safeguards."""

import uuid
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_admin_api import partner_tools as admin
from devfeed_core import partner_placement_destinations
from devfeed_core import partner_placements as placements
from devfeed_core.models import PartnerProduct, utcnow
from devfeed_core.partner_tools import ProductOut, manual_product_views, product_view
from devfeed_core.partner_tracking import BusyPartnerAsset
from sqlalchemy.dialects.postgresql import dialect


def sql(statement, *, literal=True):
    return str(statement.compile(dialect=dialect(), compile_kwargs={"literal_binds": literal}))


def product(**changes):
    return PartnerProduct(
        **{
            "id": uuid.uuid4(),
            "name": "Kubernetes monitoring",
            "product_url": "https://monitor.example/",
            "tagline": "Monitor Kubernetes metrics and diagnose cluster workloads.",
            "pricing": "paid",
            "technologies": ["Kubernetes"],
            "evidence": [],
            "revision": 1,
            "status": "approved",
            "verified_at": utcnow(),
            "updated_at": utcnow(),
            "reviews": [],
            "assessment": {},
            "excluded": False,
            "metadata_listing_id": None,
            **changes,
        }
    )


def test_direct_delivery_locks_active_ownership_and_retains_product_policy():
    session = Mock()
    account_id, product_id = uuid.uuid4(), uuid.uuid4()
    session.scalar.side_effect = [None, product_id, False]
    assert placements.account_product_available(
        session, account_id, product_id, busy_error=True, ad_format="feed", surface="article"
    )
    source = session.scalar.call_args_list[1].args[0]
    lock = source._for_update_arg
    assert lock.read and lock.skip_locked
    assert "partner_manual_products.active IS true" in sql(source)
    assert str(account_id) in sql(source) and str(product_id) in sql(source)
    policy = sql(session.scalar.call_args_list[2].args[0], literal=False)
    assert "ad_formats" in policy and "promotion_surfaces" in policy


@pytest.mark.parametrize("busy", [False, True])
def test_removed_direct_ownership_stops_delivery_while_busy_rows_are_retryable(busy):
    session = Mock()
    session.scalar.side_effect = [None, None, busy]
    if busy:
        with pytest.raises(BusyPartnerAsset):
            placements.account_product_available(
                session, uuid.uuid4(), uuid.uuid4(), busy_error=True
            )
    else:
        assert not placements.account_product_available(
            session, uuid.uuid4(), uuid.uuid4(), busy_error=True
        )
    assert session.scalar.call_count == 3


def test_direct_ownership_cannot_bypass_a_product_exclusion():
    session = Mock()
    session.scalar.side_effect = [None, uuid.uuid4(), True]
    assert not placements.account_product_available(
        session, uuid.uuid4(), uuid.uuid4(), busy_error=True
    )


def test_imported_products_keep_the_existing_lock_and_query_budget():
    session = Mock()
    session.scalar.side_effect = [uuid.uuid4(), False]
    assert placements.account_product_available(session, uuid.uuid4(), uuid.uuid4())
    assert session.scalar.call_count == 2
    assert all(
        "partner_manual_products" not in sql(call.args[0], literal=False)
        for call in session.scalar.call_args_list
    )


def test_listing_destination_uses_direct_website_without_fabricating_platform_provenance():
    session = Mock()
    account_id, product_id = uuid.uuid4(), uuid.uuid4()
    session.execute.side_effect = [
        [],
        Mock(
            first=lambda: NS(
                tagline="Approved manual copy",
                product_url="https://monitor.example/start?ref=docs#install",
            )
        ),
    ]
    creative = partner_placement_destinations.account_product_creative(
        session, NS(id=account_id), NS(id=product_id), lock=True
    )
    assert (
        creative.listing_url == "https://monitor.example/start?ref=docs&utm_source=devfeed#install"
    )
    source = session.execute.call_args.args[0]
    assert "partner_manual_products.active IS true" in sql(source)
    assert str(account_id) in sql(source)
    assert source._for_update_arg.read and source._for_update_arg.skip_locked


@pytest.mark.parametrize(
    "website", [None, "http://monitor.example/", "https://localhost/", "file:///secret"]
)
def test_direct_destination_cannot_resurrect_an_archived_or_unsafe_product(website):
    session = Mock()
    session.execute.side_effect = [
        [],
        Mock(first=lambda: NS(tagline="Approved manual copy", product_url=website))
        if website is not None
        else Mock(first=lambda: None),
    ]
    creative = partner_placement_destinations.account_product_creative(
        session, NS(id=uuid.uuid4()), NS(id=uuid.uuid4())
    )
    assert creative is None or creative.listing_url is None


def test_real_owned_listing_remains_preferred_over_the_direct_website():
    session = Mock()
    session.execute.return_value = [
        NS(tagline="Owned source copy", listing_url="https://launch.example/products/monitor")
    ]
    creative = partner_placement_destinations.account_product_creative(
        session, NS(id=uuid.uuid4()), NS(id=uuid.uuid4())
    )
    assert creative.listing_url == "https://launch.example/products/monitor?utm_source=devfeed"
    assert creative.tagline == "Owned source copy"
    session.execute.assert_called_once()


@pytest.mark.parametrize(
    "active,account_status,excluded,expected",
    [
        (True, "active", False, True),
        (False, "active", False, False),
        (True, "paused", False, False),
        (True, "active", True, False),
    ],
)
def test_manual_source_approval_is_batched_and_obeys_account_and_product_policy(
    active, account_status, excluded, expected
):
    item, other = product(), product()
    account = NS(
        id=uuid.uuid4(), name="Direct partner", status=account_status, approval_mode="trusted"
    )
    source = NS(active=active, updated_at=utcnow())
    policy = NS(excluded=excluded, commercial_approved_at=utcnow(), approval_source="trusted")
    session = Mock()
    session.execute.return_value = [(source, account, item, policy)]
    views = manual_product_views(session, [item.id, other.id])
    assert views[other.id] == []
    assert views[item.id][0]["commercial_approved"] is expected
    assert views[item.id][0]["account_id"] == account.id
    session.execute.assert_called_once()


def test_direct_product_admin_view_preserves_manual_provenance_and_approval():
    item = product()
    source = {
        "account_id": uuid.uuid4(),
        "account_name": "Direct partner",
        "active": True,
        "commercial_approved": True,
        "approval_source": "qualified",
        "updated_at": utcnow(),
    }
    view = ProductOut.model_validate(product_view(item, [], [source]))
    assert view.partnership_type == "direct"
    assert view.listings == [] and len(view.manual_sources) == 1
    assert view.eligible and view.commercial_approved
    assert view.approval_sources == ["qualified"]
    item.verified_at = utcnow() - timedelta(days=91)
    assert not product_view(item, [], [source])["eligible"]
    source["active"] = False
    assert not product_view(product(), [], [source])["eligible"]


def test_admin_lists_direct_inventory_and_batches_its_sources(monkeypatch):
    item, session = product(), Mock()
    page = {"items": [item], "total": 1, "limit": 25, "offset": 0}
    paginate = Mock(return_value=page)
    monkeypatch.setattr(admin, "paginate", paginate)
    monkeypatch.setattr(admin, "listing_views", lambda *_: {item.id: []})
    sources = Mock(return_value={item.id: []})
    monkeypatch.setattr(admin, "manual_product_views", sources)
    result = admin.listing(session, NS(q="", sort=None, limit=25, offset=0))
    assert result["items"][0]["id"] == item.id
    statement = sql(paginate.call_args.args[1])
    assert "partner_manual_products" in statement and "partner_listings" in statement
    assert " OR " in statement
    sources.assert_called_once_with(session, [item.id])
