"""Catalog availability and verified redirect policy without external services."""

import uuid
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core import partner_catalog as catalog
from devfeed_core.models import PartnerProductURL


def product(**changes):
    return NS(
        **{
            "id": uuid.uuid4(),
            "revision": 1,
            "merged_into_id": None,
            "status": "approved",
            "verified_at": object(),
            "excluded": False,
            "assessment_revision": 1,
            "assessment": {},
            "reviews": [],
            "product_url": "https://checker.example/tool?a=1",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "available,status,excluded",
    [
        (False, "approved", False),
        (False, "withdrawn", False),
        (True, "withdrawn", False),
        (True, "withdrawn", True),
        (True, "approved", False),
    ],
)
def test_availability_restoration_does_not_undo_exclusion(available, status, excluded):
    item = product(status=status, excluded=excluded)
    session = Mock()
    session.scalar.return_value = uuid.uuid4() if available else None
    catalog.refresh_availability(session, item)
    expected = (
        ("paused" if excluded else "pending")
        if available and status == "withdrawn"
        else "withdrawn"
        if not available
        else status
    )
    assert item.status == expected
    if available and status == "withdrawn":
        assert item.assessment_revision == 0 and item.revision == 2
    if not available and status != "withdrawn":
        assert item.verified_at is None and item.assessment["state"] == "withdrawn"
    merged = product(merged_into_id=uuid.uuid4())
    session.reset_mock()
    catalog.refresh_availability(session, merged)
    session.scalar.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/tool?a=1",
        "https://checker.example/else?a=1",
        "https://checker.example/tool?a=2",
        "http://checker.example/tool?a=1",
    ],
)
def test_unrelated_redirects_never_create_an_identity_alias(url):
    item, session = product(), Mock()
    assert catalog.record_verified_redirect(session, item, url) is item
    session.get.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("existing,collision", [(False, False), (True, False), (True, True)])
def test_verified_www_redirect_resolves_only_matching_listings(monkeypatch, existing, collision):
    item, session = product(), Mock()
    recipient = product()
    alias = PartnerProductURL(product_id=recipient.id if collision else item.id)
    session.get.side_effect = lambda model, _: (
        (alias if existing else None) if model is PartnerProductURL else recipient
    )
    matching = NS(
        product_url="https://www.checker.example/tool?a=1",
        identity_status="unresolved",
        identity_reason="changed",
    )
    other = NS(product_url="https://checker.example/else", identity_status="unresolved")
    session.scalars.return_value = [matching, other]
    merge = Mock(return_value=recipient)
    monkeypatch.setattr(catalog, "merge_products", merge)
    result = catalog.record_verified_redirect(session, item, matching.product_url)
    if collision:
        assert result is recipient
        merge.assert_called_once_with(session, item, recipient)
    else:
        assert result is item and matching.identity_status == "resolved"
        assert matching.identity_reason is None and other.identity_status == "unresolved"
        assert session.add.call_count == int(not existing)


@pytest.mark.parametrize("excluded", [False, True])
def test_merge_preserves_exclusions_and_cancels_stale_work(monkeypatch, excluded):
    from devfeed_core import partner_connections

    donor, recipient, session = product(excluded=excluded), product(), Mock()
    donor.reviews, recipient.reviews = [{"decision": "exclude"}], [{"decision": "review"}]
    session.scalars.return_value = []
    monkeypatch.setattr(catalog, "refresh_availability", Mock())
    monkeypatch.setattr(catalog, "eligible", lambda _: False)
    request = Mock()
    monkeypatch.setattr(partner_connections, "request_assessment", request)
    assert catalog.merge_products(session, donor, recipient) is recipient
    assert donor.merged_into_id == recipient.id and donor.status == "withdrawn"
    assert recipient.excluded is excluded and len(recipient.reviews) == 2
    assert request.call_count == int(not excluded)
    if excluded:
        assert recipient.status == "paused" and recipient.verified_at is None
    session.execute.assert_called()
    session.reset_mock()
    assert catalog.merge_products(session, recipient, recipient) is recipient
    session.execute.assert_not_called()
