"""Portal input and authorization failures must not mutate partner data."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import partner_accounts as portal
from devfeed_core.models import PartnerAccount, PartnerAsset
from devfeed_http import partner_portal
from devfeed_partner_api.auth import PartnerIdentity
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.fixture
def identity():
    return PartnerIdentity(
        subject="alice",
        issuer="https://identity.example",
        organization_id="org",
        roles=["partner"],
        expires_at=2**31,
        csrf_token="test",
    )


def test_management_requires_superuser(identity):
    with pytest.raises(HTTPException) as error:
        portal.require_superuser(identity)
    assert error.value.status_code == 403
    identity.roles = ["superuser"]
    assert portal.require_superuser(identity) is identity


@pytest.mark.parametrize("kind", ["account", "asset", "product"])
def test_missing_resource_does_not_commit(identity, kind):
    session = Mock()
    session.scalar.return_value = session.get.return_value = None
    with pytest.raises(HTTPException) as error:
        if kind == "account":
            portal.account_access(session, identity, uuid.uuid4())
        elif kind == "asset":
            portal.asset_access(session, uuid.uuid4(), uuid.uuid4())
        else:
            portal.validate_product(
                portal.AssetInput(name="Product", kind="product", product_id=uuid.uuid4()), session
            )
    assert error.value.status_code == (422 if kind == "product" else 404)
    session.commit.assert_not_called()


def test_asset_and_measurement_validation():
    with pytest.raises(ValidationError):
        portal.AssetInput(name="Product", kind="product")
    with pytest.raises(ValidationError):
        partner_portal.MetricInput(
            day=datetime.now(UTC).date() + timedelta(days=1), impressions=1, clicks=0
        )
    with pytest.raises(ValidationError):
        partner_portal.MetricInput(day=datetime.now(UTC).date(), impressions=-1, clicks=0)
    assert partner_portal.totals(None, None, None) == {
        "impressions": 0,
        "clicks": 0,
        "ctr": None,
        "measured_days": 0,
    }
    assert partner_portal.totals(20, 1, 2)["ctr"] == 5


def test_account_and_asset_edits_preserve_ownership(identity):
    session = Mock()
    account_id, asset_id = uuid.uuid4(), uuid.uuid4()
    account = PartnerAccount(id=account_id, name="Old", tier="bronze", status="active")
    asset = PartnerAsset(
        id=asset_id, account_id=account_id, name="Old ad", kind="ad", status="draft"
    )
    payload = portal.AccountInput(name=" New ", tier="gold")
    session.scalar.return_value = account
    assert portal.update_account(account_id, payload, identity, session).name == "New"
    session.scalar.side_effect = [account, asset]
    updated = portal.update_asset(
        account_id,
        asset_id,
        portal.AssetInput(name="Ad", kind="ad", status="active"),
        identity,
        session,
    )
    assert (
        updated.account_id == account_id and updated.id == asset_id and updated.status == "active"
    )
    assert session.commit.call_count == 2


@pytest.mark.parametrize("exists", [False, True])
def test_membership_removal_is_idempotent_and_issuer_scoped(identity, exists):
    session = Mock()
    account_id = uuid.uuid4()
    member = object() if exists else None
    session.get.return_value = member
    assert portal.remove_member(account_id, "bob", identity, session).status_code == 204
    session.get.assert_called_once_with(
        portal.PartnerMembership, (account_id, identity.issuer, "bob")
    )
    assert session.delete.call_count == session.commit.call_count == int(exists)


@pytest.mark.parametrize("tier", ["bronze", "silver", "gold", "platinum", "diamond"])
def test_predefined_tiers_derive_benefits_from_code(tier):
    from devfeed_core.partner_tiers import TIER_BENEFITS

    payload = portal.AccountInput(name="Partner", tier=tier)
    account = PartnerAccount(id=uuid.uuid4(), **payload.model_dump())
    output = portal.AccountOut.model_validate(account)
    assert output.benefits == list(TIER_BENEFITS[tier])
    assert "benefits" not in PartnerAccount.__table__.columns
    assert "benefits" not in payload.model_dump()


@pytest.mark.parametrize("tier", ["Custom", "", "Gold", "enterprise"])
def test_rejects_unrecognized_tiers(tier):
    with pytest.raises(ValidationError):
        portal.AccountInput(name="Partner", tier=tier)


def test_account_cannot_override_code_defined_benefits():
    with pytest.raises(ValidationError):
        portal.AccountInput(name="Partner", tier="bronze", benefits=["Forged benefit"])
