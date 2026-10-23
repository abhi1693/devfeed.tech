"""Scoped partner portal clients and membership setup."""

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerMembership,
)
from devfeed_partner_api.auth import PartnerIdentity, require_partner
from devfeed_partner_api.dependencies import get_session
from devfeed_partner_api.main import create_app
from fastapi.testclient import TestClient

ISSUER = "https://identity.example"


@pytest.fixture
def portal_client(database):
    identity = PartnerIdentity(
        subject="alice",
        issuer=ISSUER,
        organization_id="org-1",
        roles=["partner"],
        expires_at=2**31,
        csrf_token="test",
    )
    app = create_app()
    app.dependency_overrides[require_partner] = lambda: identity

    def request_session():
        with database() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    with TestClient(app) as client:
        yield client, identity


@pytest.fixture
def admin_portal_client(database):
    from devfeed_admin_api.auth import AdminIdentity, require_admin
    from devfeed_admin_api.dependencies import get_session as admin_session
    from devfeed_admin_api.partner_accounts import router
    from fastapi import FastAPI

    identity = AdminIdentity(
        subject="admin",
        issuer=ISSUER,
        organization_id="org-1",
        roles=["superuser"],
        expires_at=2**31,
        csrf_token="test",
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: identity

    def request_session():
        with database() as session:
            yield session

    app.dependency_overrides[admin_session] = request_session
    with TestClient(app) as client:
        yield client, identity


def seed(database):
    with database() as session:
        a = PartnerAccount(name="Alpha", tier="gold")
        b = PartnerAccount(name="Beta", tier="bronze")
        session.add_all([a, b])
        session.flush()
        session.add_all(
            [
                PartnerMembership(account_id=a.id, issuer=ISSUER, subject="alice"),
                PartnerMembership(account_id=a.id, issuer=ISSUER, subject="bob"),
                PartnerMembership(account_id=b.id, issuer=ISSUER, subject="carol"),
            ]
        )
        asset_a = PartnerAsset(account_id=a.id, name="Alpha ad", kind="ad", status="active")
        asset_b = PartnerAsset(account_id=b.id, name="Beta ad", kind="ad", status="active")
        session.add_all([asset_a, asset_b])
        session.commit()
        return a.id, b.id, asset_a.id, asset_b.id
