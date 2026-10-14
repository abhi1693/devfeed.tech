"""Manual inventory validates facts and preserves canonical authority and policy."""

import uuid
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core.models import PartnerManualProduct, PartnerProduct, PartnerProductURL, utcnow
from devfeed_http import partner_products as products
from fastapi import HTTPException
from pydantic import ValidationError
from redis.exceptions import RedisError


def payload(**values):
    return products.ManualProductInput.model_validate(
        {
            "name": "Developer observer",
            "product_url": "https://observer.example/",
            "description": "Inspect application metrics and logs.",
            "pricing": "paid",
            **values,
        }
    )


def product(account, **values):
    return NS(
        **{
            "id": uuid.uuid4(),
            "name": "Developer observer",
            "product_url": "https://observer.example/",
            "description": "Inspect application metrics and logs.",
            "pricing": "paid",
            "metadata_account_id": account.id,
            "revision": 1,
            "assessment_revision": 1,
            "assessment": {},
            "status": "approved",
            "verified_at": utcnow(),
            "technologies": ["Python"],
            "evidence": [{"quote": "Python runtime metrics"}],
            "excluded": False,
            "merged_into_id": None,
            **values,
        }
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://observer.example/",
        "https://localhost/",
        "https://127.0.0.1/",
        "https://169.254.169.254/",
        "https://private.internal/",
        "file:///etc/passwd",
        "https://2130706433/",
        "https://user:pass@observer.example/",
        "https://observer.example:8443/",
        "https://observer.example/a\nb",
        "",
    ],
)
def test_create_and_update_reject_unsafe_websites(url):
    with pytest.raises(ValidationError):
        payload(product_url=url)
    with pytest.raises(ValidationError):
        products.ManualProductUpdate(product_url=url, expected_revision=1)


@pytest.mark.parametrize(
    "values",
    [
        {"name": " "},
        {"name": "x" * 201},
        {"name": "a\0b"},
        {"description": "too short"},
        {"description": "x" * 5001},
        {"description": "invalid\0description"},
        {"pricing": "enterprise"},
        {"technologies": ["Python"]},
        {"status": "approved"},
        {"evidence": []},
        {"excluded": False},
        {"metadata_account_id": str(uuid.uuid4())},
    ],
)
def test_create_cannot_supply_qualification_or_invalid_facts(values):
    with pytest.raises(ValidationError):
        payload(**values)


def test_urls_and_text_normalize_before_identity_lookup():
    value = payload(name="  Observer  ", product_url="https://OBSERVER.example?utm_source=reader#x")
    assert value.name == "Observer"
    assert value.product_url == "https://observer.example/"
    value = products.ManualProductUpdate(name=" Updated ", expected_revision=2)
    assert value.name == "Updated"


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"expected_revision": 1},
        {"name": "Name"},
        {"name": None, "expected_revision": 1},
        {"active": None},
        {"active": True, "expected_revision": 0},
        {"pricing": "paid"},
        {"status": "approved"},
    ],
)
def test_patch_requires_facts_revision_and_nonempty_nonnull_fields(values):
    with pytest.raises(ValidationError):
        products.ManualProductUpdate(**values)


def test_active_only_update_does_not_require_product_revision():
    assert products.ManualProductUpdate(active=False).active is False


@pytest.mark.parametrize("include_archived", [False, True])
def test_inventory_archive_option_uses_batched_projected_availability(include_archived):
    from devfeed_core.models import PartnerAccount
    from devfeed_core.partner_promotions import associated_products_query
    from devfeed_http.partner_portal import inventory_page
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    account, session = NS(id=uuid.uuid4(), name="Business", approval_mode="qualified"), Mock()
    row = product(account, status="withdrawn" if include_archived else "approved")
    source = NS(active=not include_archived)
    session.scalar.return_value = 1
    session.execute.return_value.all.return_value = [
        (account, row, None, source, not include_archived)
    ]
    result = inventory_page(
        session,
        select(PartnerAccount).where(PartnerAccount.id == account.id),
        include_archived=include_archived,
        limit=10,
        offset=2,
    )
    assert result["total"] == 1 and result["items"][0]["active"] is not include_archived
    assert result["items"][0]["manual_active"] is not include_archived
    assert result["items"][0]["commercial_approved"] is not include_archived
    session.scalar.assert_called_once()
    session.execute.assert_called_once()
    query = str(
        associated_products_query(account.id, include_archived=include_archived).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert ("partner_manual_products.active IS true" in query) is not include_archived
    assert "partner_connections.enabled IS true" in query and "UNION" in query


@pytest.mark.parametrize("status", ["allowed", "limited", "unavailable"])
def test_write_abuse_budget_is_actor_and_account_scoped(monkeypatch, status):
    actor, account_id, redis = NS(issuer="issuer", subject="subject"), uuid.uuid4(), Mock()
    consume = Mock(return_value=12 if status == "limited" else 0)
    if status == "unavailable":
        consume.side_effect = RedisError("private redis address")
    monkeypatch.setattr(products, "consume_rate_limits", consume)
    if status == "allowed":
        products.limit_product_write(redis, actor, account_id)
    else:
        with pytest.raises(HTTPException) as caught:
            products.limit_product_write(redis, actor, account_id)
        assert caught.value.status_code == (429 if status == "limited" else 503)
        assert "private" not in caught.value.detail
    budgets = consume.call_args.args[1]
    assert budgets[0].limit == 30 and "subject" not in budgets[0].key
    assert budgets[1].key.endswith(str(account_id)) and budgets[1].limit == 100


@pytest.mark.parametrize("prefix", ["/v1/partner/accounts", "/v1/admin/partner-accounts"])
def test_body_limit_only_matches_product_mutations(prefix):
    base = f"{prefix}/{uuid.uuid4()}/products"
    assert products.product_body_limit("POST", base) == 65536
    assert products.product_body_limit("PATCH", base + f"/{uuid.uuid4()}") == 65536
    for method, path in [
        ("GET", base),
        ("PATCH", base),
        ("POST", base + "/x"),
        ("POST", base + "/preview"),
        ("POST", "/unrelated"),
    ]:
        assert products.product_body_limit(method, path) is None


@pytest.fixture
def context(monkeypatch):
    account, session = NS(id=uuid.uuid4()), Mock()
    row = product(account)
    source = NS(active=True, updated_at=utcnow())
    session.get.side_effect = lambda model, key: source if model is PartnerManualProduct else None
    session.scalars.return_value = []
    summary = Mock(return_value={"id": row.id})
    monkeypatch.setattr(products, "product_detail", summary)
    refresh, assess, enroll = Mock(), Mock(), Mock()
    monkeypatch.setattr(products, "refresh_availability", refresh)
    monkeypatch.setattr(products, "request_assessment", assess)
    monkeypatch.setattr(products, "request_promotion_enrollment", enroll)
    return account, session, row, source, assess, enroll


@pytest.mark.parametrize("mode", ["missing", "foreign", "stale", "collision"])
def test_edits_reject_missing_foreign_stale_or_colliding_products(context, mode):
    account, session, row, source, assess, enroll = context
    changes = {"name": "Updated observer", "expected_revision": 1}
    if mode == "missing":
        session.get.return_value = None
        session.get.side_effect = None
    elif mode == "foreign":
        row.metadata_account_id = uuid.uuid4()
    elif mode == "stale":
        row.revision = 2
    else:
        changes = {"product_url": "https://other.example/", "expected_revision": 1}
        session.get.side_effect = lambda model, key: (
            source if model is PartnerManualProduct else NS(product_id=uuid.uuid4())
        )
    with pytest.raises(HTTPException) as caught:
        products.update_manual_product(
            session, account, row, products.ManualProductUpdate(**changes)
        )
    assert caught.value.status_code == (404 if mode == "missing" else 409)
    assess.assert_not_called()
    enroll.assert_not_called()
    assert row.name == "Developer observer"


def test_fact_change_invalidates_evidence_and_queues_fenced_qualification(context):
    account, session, row, source, assess, enroll = context
    job = NS(status="running", lease_token=uuid.uuid4(), lease_until=utcnow(), attempts=1)
    session.scalars.side_effect = [[job], []]
    products.update_manual_product(
        session,
        account,
        row,
        products.ManualProductUpdate(
            name="Updated observer",
            product_url="https://new.example/",
            expected_revision=1,
        ),
    )
    assert row.revision == 2 and row.status == "pending" and row.verified_at is None
    assert row.technologies == row.evidence == [] and row.assessment_revision == 0
    assert job.status == "failed" and job.lease_token is None
    assess.assert_called_once_with(session, row)
    enroll.assert_called_once_with(session, account.id)
    assert any(isinstance(call.args[0], PartnerProductURL) for call in session.add.call_args_list)


def test_unchanged_facts_preserve_valid_assessment(context):
    account, session, row, source, assess, enroll = context
    products.update_manual_product(
        session,
        account,
        row,
        products.ManualProductUpdate(
            name=row.name,
            expected_revision=1,
        ),
    )
    assert row.revision == 1 and row.verified_at is not None
    assess.assert_not_called()
    enroll.assert_not_called()


def test_excluded_product_stays_paused_when_facts_change(context):
    account, session, row, source, assess, enroll = context
    row.excluded = True
    products.update_manual_product(
        session,
        account,
        row,
        products.ManualProductUpdate(
            name="Updated observer",
            expected_revision=1,
        ),
    )
    assert row.status == "paused" and row.excluded


def test_archive_and_restore_are_account_scoped_and_keep_current_assessment(context):
    account, session, row, source, assess, enroll = context
    products.archive_manual_product(session, account, row)
    assert not source.active and row.revision == 1
    assess.assert_not_called()
    products.update_manual_product(session, account, row, products.ManualProductUpdate(active=True))
    assert source.active and row.revision == 1
    assess.assert_not_called()
    enroll.assert_called_once_with(session, account.id)


def test_existing_creation_is_idempotent_and_does_not_restore_archived_choice(context):
    account, session, row, source, assess, enroll = context
    source.active = False
    products.create_manual_product(session, account, payload(), row)
    assert not source.active and row.revision == 1
    session.add.assert_not_called()
    assess.assert_not_called()
    enroll.assert_not_called()


def test_associate_existing_product_keeps_facts_and_valid_qualification(context):
    account, session, row, source, assess, enroll = context
    row.metadata_account_id = uuid.uuid4()
    session.get.side_effect = None
    session.get.return_value = None
    products.create_manual_product(session, account, payload(name="Hijacked name"), row)
    assert row.name == "Developer observer" and row.status == "approved"
    assess.assert_not_called()
    enroll.assert_called_once_with(session, account.id)
    assert isinstance(session.add.call_args.args[0], PartnerManualProduct)


def test_create_new_product_has_no_connection_or_self_approved_evidence(context):
    account, session, row, source, assess, enroll = context
    session.get.side_effect = None
    session.get.return_value = None

    def flush():
        for call in session.add.call_args_list:
            item = call.args[0]
            if isinstance(item, PartnerProduct):
                item.id = item.id or uuid.uuid4()
                item.assessment_revision = 0

    session.flush.side_effect = flush
    products.create_manual_product(session, account, payload())
    created = session.add.call_args_list[0].args[0]
    assert created.metadata_account_id == account.id and created.status == "pending"
    assert created.technologies == created.evidence == []
    assert created.metadata_listing_id is None
    assert any(isinstance(call.args[0], PartnerProductURL) for call in session.add.call_args_list)
    assess.assert_called_once_with(session, created)


@pytest.mark.parametrize("mode", ["new", "alias", "manual", "missing", "merged"])
def test_identity_locks_serialize_alias_creation_and_reject_unowned_edits(monkeypatch, mode):
    account, session = NS(id=uuid.uuid4()), Mock()
    row = product(account, merged_into_id=uuid.uuid4() if mode == "merged" else None)
    lock = Mock()
    monkeypatch.setattr(products, "lock_catalog", lock)
    session.get.return_value = None if mode in {"new", "missing"} else NS(product_id=row.id)
    session.scalar.return_value = row
    if mode == "missing":
        with pytest.raises(HTTPException) as caught:
            products.lock_manual_identity(session, account.id, product_id=row.id)
        assert caught.value.status_code == 404
    elif mode == "merged":
        with pytest.raises(HTTPException):
            products.lock_manual_identity(session, account.id, url=row.product_url)
    else:
        result = products.lock_manual_identity(
            session,
            account.id,
            **({"product_id": row.id} if mode == "manual" else {"url": row.product_url}),
        )
        assert result is (None if mode == "new" else row)
    lock.assert_called_once_with(session)


@pytest.mark.parametrize("mode", ["manual", "archived", "imported", "missing", "foreign", "merged"])
def test_private_detail_includes_archived_manual_inventory_but_never_foreign_product(
    monkeypatch, mode
):
    account, session = NS(id=uuid.uuid4()), Mock()
    row = product(account, merged_into_id=uuid.uuid4() if mode == "merged" else None)
    source = NS(active=mode != "archived") if mode in {"manual", "archived", "merged"} else None
    session.get.side_effect = lambda model, key: (
        source
        if model is PartnerManualProduct
        else (None if mode == "missing" else row)
        if model is PartnerProduct
        else None
    )
    session.execute.return_value.first.return_value = (account, row) if mode == "imported" else None
    view = Mock(return_value={"id": row.id})
    monkeypatch.setattr(products, "inventory_product_view", view)
    if mode in {"missing", "foreign", "merged"}:
        with pytest.raises(HTTPException) as caught:
            products.product_detail(session, account, row.id)
        assert caught.value.status_code == 404
        view.assert_not_called()
    else:
        assert products.product_detail(session, account, row.id) == {"id": row.id}
        assert view.call_args.args[-1] is source


@pytest.mark.parametrize("service", ["partner", "admin"])
@pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
def test_private_crud_routes_authorize_scope_apply_write_limits_and_commit(
    monkeypatch, service, method
):
    from devfeed_admin_api import partner_accounts as admin_routes
    from devfeed_admin_api.auth import AdminIdentity, require_admin
    from devfeed_admin_api.dependencies import get_session as admin_session
    from devfeed_partner_api import portal
    from devfeed_partner_api.auth import PartnerIdentity, require_partner
    from devfeed_partner_api.dependencies import get_session as partner_session
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    routes = portal if service == "partner" else admin_routes
    account = NS(id=uuid.uuid4(), name="Business", approval_mode="qualified")
    row = product(account)
    summary = {
        "id": str(row.id),
        "account_id": str(account.id),
        "account_name": account.name,
        "name": row.name,
        "product_url": row.product_url,
        "description": row.description,
        "pricing": row.pricing,
        "technologies": row.technologies,
        "status": row.status,
        "assessment": {},
        "revision": row.revision,
        "source": "manual",
        "editable": True,
        "active": True,
        "manual_active": True,
        "excluded": False,
        "globally_excluded": False,
    }
    session, limiter, redis = Mock(), Mock(), Mock()
    identity_type = PartnerIdentity if service == "partner" else AdminIdentity
    identity = identity_type(
        issuer="https://identity.example",
        subject="business-owner",
        organization_id="business",
        roles=["partner" if service == "partner" else "superuser"],
        expires_at=2**31,
        csrf_token="test",
    )
    if service == "partner":
        access = Mock(return_value=account)
        monkeypatch.setattr(routes.PartnerAccountAccess, "require", access)
        monkeypatch.setattr(
            routes.PartnerAccountAccess, "lock_manual", Mock(return_value=(account, row))
        )
    else:
        access = Mock(return_value=account)
        monkeypatch.setattr(routes, "account_access", access)
        monkeypatch.setattr(routes, "write_manual_account", Mock(return_value=(account, row)))
    monkeypatch.setattr(routes, "limit_product_write", limiter)
    monkeypatch.setattr(routes, "get_redis", lambda: redis)
    for name in (
        "product_detail",
        "create_manual_product",
        "update_manual_product",
        "archive_manual_product",
    ):
        monkeypatch.setattr(routes, name, Mock(return_value=summary))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_partner if service == "partner" else require_admin] = lambda: (
        identity
    )
    app.dependency_overrides[partner_session if service == "partner" else admin_session] = lambda: (
        session
    )
    base = (
        f"/v1/partner/accounts/{account.id}/products"
        if service == "partner"
        else f"/v1/admin/partner-accounts/{account.id}/products"
    )
    with TestClient(app) as client:
        path = base if method == "post" else base + f"/{row.id}"
        response = getattr(client, method)(
            path,
            **(
                {"json": payload().model_dump()}
                if method == "post"
                else {"json": {"name": "Updated", "expected_revision": 1}}
                if method == "patch"
                else {}
            ),
        )
    assert response.status_code == {"post": 201, "delete": 204}.get(method, 200), response.text
    access.assert_called()
    if method == "get":
        limiter.assert_not_called()
        session.commit.assert_not_called()
    else:
        limiter.assert_called_once_with(redis, identity, account.id)
        session.commit.assert_called_once_with()
