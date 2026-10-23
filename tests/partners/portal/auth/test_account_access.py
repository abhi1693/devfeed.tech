"""Live account access stays scoped and serialized with completed membership revocations."""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from queue import Queue
from threading import Event
from urllib.parse import quote

import pytest
from devfeed_admin_api.dependencies import get_session as admin_session
from devfeed_core.models import PartnerAccount, PartnerMembership, PartnerPromotionEnrollment
from devfeed_partner_api import portal
from devfeed_partner_api.access import PartnerAccountAccess
from devfeed_partner_api.dependencies import get_session as partner_session
from fastapi import HTTPException
from sqlalchemy import select, text

from tests.support.partner_portal import admin_portal_client as admin_portal_client
from tests.support.partner_portal import portal_client as portal_client

pytestmark = pytest.mark.integration


@pytest.fixture
def accounts(database, portal_client):
    _, identity = portal_client
    with database.begin() as session:
        active = PartnerAccount(name="Current membership", tier="gold")
        paused = PartnerAccount(name="Paused membership", tier="gold", status="paused")
        foreign = PartnerAccount(name="Another member", tier="gold")
        issuer = PartnerAccount(name="Another identity provider", tier="gold")
        session.add_all([active, paused, foreign, issuer])
        session.flush()
        session.add_all(
            [
                PartnerMembership(
                    account_id=account.id, issuer=identity.issuer, subject=identity.subject
                )
                for account in (active, paused)
            ]
            + [
                PartnerMembership(
                    account_id=foreign.id, issuer=identity.issuer, subject="another-member"
                ),
                PartnerMembership(
                    account_id=issuer.id,
                    issuer="https://another-identity.example",
                    subject=identity.subject,
                ),
            ]
        )
        return {
            "active": active.id,
            "paused": paused.id,
            "foreign": foreign.id,
            "issuer": issuer.id,
        }


@contextmanager
def request_sessions(client, database, dependency):
    """Observe actual request backends and bound accidental blocking with a watchdog."""
    pids = Queue()
    original = client.app.dependency_overrides[dependency]

    def session():
        with database() as current:
            current.execute(text("SET LOCAL statement_timeout = '10s'"))
            pids.put(current.scalar(text("SELECT pg_backend_pid()")))
            yield current

    client.app.dependency_overrides[dependency] = session
    try:
        yield pids
    finally:
        client.app.dependency_overrides[dependency] = original


def wait_for_block(database, waiting_pid, blocking_pid, request):
    """Require a real PostgreSQL lock wait; elapsed time never establishes ordering."""
    deadline = time.monotonic() + 5
    with database() as observer:
        observer.execute(text("SET LOCAL statement_timeout = '2s'"))
        while time.monotonic() < deadline:
            if request.done():
                response = request.result()
                pytest.fail(
                    f"Request completed before its conflicting lock: {response.status_code}"
                )
            blockers = observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiting_pid})
            if blocking_pid in blockers:
                return
    pytest.fail("Request never reached the expected PostgreSQL lock")


@pytest.mark.parametrize(
    "change,visible",
    [(None, True), ("issuer", False), ("subject", False), ("superuser", True)],
)
def test_account_queries_require_current_issuer_membership_and_active_account(
    database, portal_client, accounts, change, visible
):
    _, identity = portal_client
    if change == "issuer":
        identity.issuer = "https://unrelated-identity.example"
    elif change == "subject":
        identity.subject = "nonmember"
    elif change == "superuser":
        identity.roles = ["superuser"]
    with database() as session:
        access = PartnerAccountAccess(session, identity)
        actual = {account.id for account in session.scalars(access.query())}
        assert actual == ({accounts["active"]} if visible else set())
        if visible:
            assert access.require(accounts["active"]).id == accounts["active"]
            assert session.scalar(access.query(accounts["active"])).id == accounts["active"]
        else:
            with pytest.raises(HTTPException) as failure:
                access.require(accounts["active"])
            assert failure.value.status_code == 404
        for account_id in [accounts[key] for key in ("paused", "foreign", "issuer")]:
            assert session.scalar(access.query().where(PartnerAccount.id == account_id)) is None
            with pytest.raises(HTTPException) as failure:
                access.query(account_id)
            assert failure.value.status_code == 404
            assert failure.value.detail == "Partner account not found"
            with pytest.raises(HTTPException) as failure:
                access.require(account_id)
            assert failure.value.status_code == 404
            assert failure.value.detail == "Partner account not found"
        with pytest.raises(HTTPException) as failure:
            access.require(uuid.uuid4())
        assert failure.value.status_code == 404
        assert failure.value.detail == "Partner account not found"


def test_nowait_contention_rolls_back_and_preserves_retry_diagnostics(
    database, portal_client, accounts
):
    _, identity = portal_client
    with database.begin() as editor:
        editor.scalar(
            select(PartnerAccount).where(PartnerAccount.id == accounts["active"]).with_for_update()
        )
        with database() as requester:
            with pytest.raises(HTTPException) as failure:
                PartnerAccountAccess(requester, identity).lock(accounts["active"], nowait=True)
            assert failure.value.status_code == 503
            assert failure.value.headers == {"Retry-After": "1"}
            assert not requester.in_transaction()


def test_preference_commit_holds_membership_until_admin_revocation_can_complete(
    database, portal_client, admin_portal_client, accounts, monkeypatch
):
    partner, identity = portal_client
    admin, _ = admin_portal_client
    account_id = accounts["active"]
    paused, release = Event(), Event()
    enrollments = []
    enrollment = portal.request_promotion_enrollment

    def hold_write(session, identifier):
        enrollments.append(identifier)
        paused.set()
        assert release.wait(10), "Preference transaction was never released"
        return enrollment(session, identifier)

    monkeypatch.setattr(portal, "request_promotion_enrollment", hold_write)
    preferences = f"/v1/partner/accounts/{account_id}/preferences"
    revocation = (
        f"/v1/admin/partner-accounts/{account_id}/members/{quote(identity.subject, safe='')}"
    )
    with (
        request_sessions(partner, database, partner_session) as partner_pids,
        request_sessions(admin, database, admin_session) as admin_pids,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        save = pool.submit(partner.put, preferences, json={"outbound_destination": "listing_url"})
        try:
            assert paused.wait(5), "Preference save never reached its guarded transaction"
            writer_pid = partner_pids.get(timeout=5)
            revoke = pool.submit(admin.delete, revocation)
            revoker_pid = admin_pids.get(timeout=5)
            wait_for_block(database, revoker_pid, writer_pid, revoke)
            assert not save.done()
        finally:
            release.set()
        saved, revoked = save.result(timeout=15), revoke.result(timeout=15)
    assert saved.status_code == 200, saved.text
    assert revoked.status_code == 204, revoked.text
    with database() as session:
        assert session.get(PartnerAccount, account_id).outbound_destination == "listing_url"
        assert (
            session.get(PartnerMembership, (account_id, identity.issuer, identity.subject)) is None
        )
    rejected = partner.put(preferences, json={"outbound_destination": "product_url"})
    assert rejected.status_code == 404 and rejected.json() == {
        "detail": "Partner account not found"
    }
    assert enrollments == [account_id]
    with database() as session:
        assert session.get(PartnerAccount, account_id).outbound_destination == "listing_url"


def test_completed_revocation_rejects_preferences_waiting_on_an_old_account_snapshot(
    database, portal_client, admin_portal_client, accounts
):
    partner, identity = portal_client
    admin, _ = admin_portal_client
    account_id = accounts["active"]
    preferences = f"/v1/partner/accounts/{account_id}/preferences"
    revocation = (
        f"/v1/admin/partner-accounts/{account_id}/members/{quote(identity.subject, safe='')}"
    )
    with (
        request_sessions(partner, database, partner_session) as partner_pids,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        with database.begin() as editor:
            editor.scalar(
                select(PartnerAccount).where(PartnerAccount.id == account_id).with_for_update()
            )
            editor_pid = editor.scalar(text("SELECT pg_backend_pid()"))
            save = pool.submit(
                partner.put, preferences, json={"outbound_destination": "listing_url"}
            )
            writer_pid = partner_pids.get(timeout=5)
            wait_for_block(database, writer_pid, editor_pid, save)
            revoked = admin.delete(revocation)
            assert revoked.status_code == 204, revoked.text
            assert not save.done()
            with database() as observer:
                assert (
                    observer.get(PartnerMembership, (account_id, identity.issuer, identity.subject))
                    is None
                )
        rejected = save.result(timeout=15)
    assert rejected.status_code == 404
    assert rejected.json() == {"detail": "Partner account not found"}
    with database() as session:
        assert session.get(PartnerAccount, account_id).outbound_destination == "product_url"
        assert session.get(PartnerPromotionEnrollment, account_id) is None
