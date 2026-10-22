"""Account read adapters preserve authoritative scope and bounded result documents.

Selected repository rows and domain-service responses are explicit boundaries;
these cases do not evaluate SQL membership, filtering, ordering or locks.
"""

import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import partner_accounts as accounts
from devfeed_http.partner_portal import (
    AccountsOut,
    AudienceTopicsOut,
    CreativePreviewOut,
    Dashboard,
    DestinationPreviewOut,
    SponsorArticlesOut,
)
from devfeed_partner_api import portal
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_admin_editorial_policy_unit import operator_api
from test_partner_api_policy_boundary_unit import identity
from test_partner_matching_workflow_unit import NOW
from test_partner_placement_workflow_unit import account


@pytest.mark.parametrize("catalog", ["audience", "articles"])
def test_global_operator_catalog_reads_forward_only_the_explicit_picker_scope(monkeypatch, catalog):
    identifier = uuid.uuid4()
    if catalog == "audience":
        name, path = "audience_topic_options", "audience-topics"
        output = AudienceTopicsOut(items=[], total=0)
        params = {"dimension": "tools", "topic_id": str(identifier)}
    else:
        name, path = "sponsor_articles", "sponsor-articles"
        output = SponsorArticlesOut(items=[], total=0)
        params = {"article_id": str(identifier)}
    read = Mock(return_value=output)
    monkeypatch.setattr(accounts, name, read)
    with operator_api(accounts) as api:
        response = api.client.get(
            f"/v1/admin/partner-accounts/{path}",
            params={**params, "q": "Angular", "offset": 0, "limit": 12},
        )
        assert response.status_code == 200 and response.json() == {"items": [], "total": 0}
        expected = {"q": "Angular", "offset": 0, "limit": 12}
        if catalog == "audience":
            read.assert_called_once_with(api.session, "tools", topic_id=identifier, **expected)
        else:
            read.assert_called_once_with(api.session, article_id=identifier, **expected)
        api.session.scalar.assert_not_called()
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("read", ["articles", "audience", "creative", "destination", "dashboard"])
def test_operator_account_reads_resolve_current_account_before_using_shared_services(
    monkeypatch, missing, read
):
    owner, identifier = account(), uuid.uuid4()
    path, name, params, args, kwargs, output = {
        "articles": (
            "sponsor-articles",
            "sponsor_articles",
            {"q": "Angular", "article_id": str(identifier), "offset": 0, "limit": 12},
            (),
            {"q": "Angular", "article_id": identifier, "offset": 0, "limit": 12},
            SponsorArticlesOut(items=[], total=0),
        ),
        "audience": (
            "audience-topics",
            "audience_topic_options",
            {
                "dimension": "tools",
                "q": "Angular",
                "topic_id": str(identifier),
                "offset": 0,
                "limit": 12,
            },
            ("tools",),
            {"account": owner, "q": "Angular", "topic_id": identifier, "offset": 0, "limit": 12},
            AudienceTopicsOut(items=[], total=0),
        ),
        "creative": (
            "creative-preview",
            "creative_preview",
            {"product_id": str(identifier)},
            (owner,),
            {"product_id": identifier},
            CreativePreviewOut(product=None),
        ),
        "destination": (
            "destination-preview",
            "destination_preview",
            {},
            (owner,),
            {},
            DestinationPreviewOut(product=None),
        ),
        "dashboard": (
            "dashboard",
            "reporting_dashboard",
            {"days": 7, "limit": 12, "offset": 0, "ad_format": "feed"},
            (owner, 7, 12, 0),
            {"ad_format": "feed"},
            Dashboard(
                account=owner,
                start=(NOW - timedelta(days=6)).date(),
                end=NOW.date(),
                totals={"impressions": 0, "clicks": 0, "ctr": None, "measured_days": 0},
                trend=[],
                assets=[],
                asset_total=0,
                last_updated_at=None,
            ),
        ),
    }[read]
    service = Mock(return_value=output)
    monkeypatch.setattr(accounts, name, service)
    with operator_api(accounts) as api:
        api.session.scalar.return_value = None if missing else owner
        response = api.client.get(f"/v1/admin/partner-accounts/{owner.id}/{path}", params=params)
        if missing:
            assert response.status_code == 404
            assert response.json()["detail"] == "Partner account not found"
            service.assert_not_called()
        else:
            assert response.status_code == 200, response.text
            assert response.json() == output.model_dump(mode="json")
            prefix = (owner.id, api.session) if read == "dashboard" else (api.session,)
            service.assert_called_once_with(*prefix, *args, **kwargs)
        api.session.scalar.assert_called_once()
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_operator_account_detail_uses_live_record_and_never_commits(missing):
    owner = account()
    with operator_api(accounts) as api:
        api.session.scalar.return_value = None if missing else owner
        response = api.client.get(f"/v1/admin/partner-accounts/{owner.id}")
        assert response.status_code == (404 if missing else 200)
        if not missing:
            assert response.json()["id"] == str(owner.id)
            assert response.json()["benefits"] == list(owner.benefits)
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("query", ["", "Launch"])
def test_member_account_page_retains_scoped_records_and_global_count(monkeypatch, empty, query):
    owner, session, member = account(), Mock(spec=Session), identity()
    access = Mock()
    access.query.return_value = select(type(owner)).where(type(owner).id == owner.id)
    access.require.return_value = owner
    construct = Mock(return_value=access)
    monkeypatch.setattr(portal, "PartnerAccountAccess", construct)
    session.scalars.return_value.all.return_value = [] if empty else [owner]
    session.scalar.return_value = 1
    output = AccountsOut.model_validate(
        portal.accounts(session, member, limit=1, offset=int(empty), q=query)
    )
    assert output.total == 1
    assert [value.id for value in output.items] == ([] if empty else [owner.id])
    construct.assert_called_once_with(session, member)
    access.query.assert_called_once_with()
    assert portal.get_account(owner.id, session, member) is owner
    access.require.assert_called_once_with(owner.id)
    session.commit.assert_not_called()
