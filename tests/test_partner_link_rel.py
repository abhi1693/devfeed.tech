"""Advertising relationships stay account scoped and obey current tier entitlements."""

from unittest.mock import Mock

import pytest
from devfeed_core.models import (
    Article,
    PartnerAccount,
    PartnerProductPromotion,
)
from devfeed_core.partner_link_rel import (
    LINK_REL_OPTIONS,
    effective_link_rel,
    sponsored_article_link_rel,
    validate_link_rel,
)
from devfeed_core.partner_tiers import tier_policy
from devfeed_core.schemas import ArticleOut
from devfeed_http.partner_placements import PlacementQuery
from devfeed_http.partner_portal import AccountInput, PartnerPreferencesInput, ProductPromotionInput
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from test_partner_feed_placements import feed_catalog as feed_catalog
from test_partner_native_formats import (
    campaign_payload,
    feed_request,
    native_account,
    sponsored_article,
)
from test_partner_placements_integration import catalog as catalog
from test_partner_placements_integration import deliver
from test_partner_portal import admin_portal_client as admin_portal_client
from test_partner_portal import portal_client as portal_client
from test_partner_product_formats import owned_catalog


@pytest.mark.parametrize("tier", ["bronze", "silver", "gold", "platinum", "diamond"])
def test_link_relationship_entitlements_and_defaults(tier):
    configured = tier in {"platinum", "diamond"}
    account = PartnerAccount(tier=tier, link_rel=[])
    assert tier_policy(tier)["configurable_link_rel"] is configured
    assert account.configurable_link_rel is configured
    assert account.link_rel_options == list(LINK_REL_OPTIONS)
    assert AccountInput(name="Partner", tier=tier).link_rel == ["nofollow"]
    assert effective_link_rel(account) == ([] if configured else ["nofollow"])
    if configured:
        validate_link_rel(tier, [])
    else:
        with pytest.raises(ValueError, match="Platinum or Diamond"):
            validate_link_rel(tier, [])
    validate_link_rel(tier, ["nofollow"])
    validate_link_rel(tier, None)


@pytest.mark.parametrize(
    "values",
    [
        ["nofollow", "nofollow"],
        ["opener"],
        ["dofollow"],
        ["stylesheet"],
        "nofollow",
        [None],
        ["external"] * 5,
    ],
)
@pytest.mark.parametrize("model", [PartnerPreferencesInput, ProductPromotionInput])
def test_relationship_inputs_reject_duplicates_unknowns_and_nonselections(model, values):
    with pytest.raises(ValidationError):
        model(link_rel=values)


def test_empty_product_selection_and_inheritance_are_distinct():
    assert ProductPromotionInput(link_rel=None).model_dump(exclude_unset=True) == {"link_rel": None}
    assert ProductPromotionInput(link_rel=[]).model_dump(exclude_unset=True) == {"link_rel": []}
    assert PartnerPreferencesInput(link_rel=[]).link_rel == []
    with pytest.raises(ValidationError):
        PartnerPreferencesInput(link_rel=None)
    assert ProductPromotionInput(link_rel=list(LINK_REL_OPTIONS)).link_rel == list(LINK_REL_OPTIONS)


def test_account_and_product_resolution_rechecks_tier_without_changing_saved_choices():
    account = PartnerAccount(tier="platinum", link_rel=["sponsored", "external"])
    policy = PartnerProductPromotion(link_rel=[])
    assert effective_link_rel(account, policy) == []
    policy.link_rel = None
    assert effective_link_rel(account, policy) == ["sponsored", "external"]
    account.tier = "gold"
    assert effective_link_rel(account, policy) == ["nofollow"]
    assert account.link_rel == ["sponsored", "external"]
    account.tier = "diamond"
    assert effective_link_rel(account, policy) == ["sponsored", "external"]


def test_empty_article_policy_batch_never_queries():
    session = Mock()
    assert sponsored_article_link_rel(session, []) == {}
    session.execute.assert_not_called()


def test_lower_tier_admin_account_creation_cannot_forge_relationship_entitlement():
    from devfeed_admin_api.partner_accounts import create_account
    from fastapi import HTTPException

    session = Mock()
    with pytest.raises(HTTPException) as error:
        create_account(AccountInput(name="Forged", tier="gold", link_rel=[]), Mock(), session)
    assert error.value.status_code == 422
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.integration
def test_account_and_product_api_preferences_resolve_inheritance(
    database, portal_client, admin_portal_client
):
    account, product = owned_catalog(database, tier="platinum")
    portal, _ = portal_client
    admin, _ = admin_portal_client
    prefix = f"/v1/partner/accounts/{account}"
    path = f"{prefix}/products/{product}/promotion"
    initial = portal.get(path)
    assert initial.status_code == 200, initial.text
    assert initial.json()["link_rel"] is None
    assert initial.json()["effective_link_rel"] == ["nofollow"]
    assert initial.json()["account_link_rel"] == ["nofollow"]
    assert initial.json()["configurable_link_rel"] is True
    assert initial.json()["link_rel_options"] == list(LINK_REL_OPTIONS)
    response = portal.put(prefix + "/preferences", json={"link_rel": ["sponsored", "external"]})
    assert response.status_code == 200, response.text
    assert response.json()["link_rel"] == ["sponsored", "external"]
    assert portal.get(path).json()["effective_link_rel"] == ["sponsored", "external"]
    custom = portal.put(path, json={"link_rel": []})
    assert custom.status_code == 200, custom.text
    assert custom.json()["effective_link_rel"] == []
    preview = portal.get(prefix + "/creative-preview", params={"product_id": str(product)})
    assert preview.status_code == 200, preview.text
    assert preview.json()["product"]["link_rel"] == []
    inherited = admin.put(
        f"/v1/admin/partner-accounts/{account}/products/{product}/promotion",
        json={"link_rel": None},
    )
    assert inherited.status_code == 200, inherited.text
    assert inherited.json()["effective_link_rel"] == ["sponsored", "external"]
    inventory = portal.get(prefix + "/inventory").json()["items"][0]
    assert inventory["link_rel"] is None
    assert inventory["effective_link_rel"] == ["sponsored", "external"]


@pytest.mark.integration
@pytest.mark.parametrize("tier", ["bronze", "silver", "gold"])
def test_lower_tier_apis_reject_custom_relationships(
    database, portal_client, admin_portal_client, tier
):
    account, product = owned_catalog(database, tier=tier)
    portal, _ = portal_client
    admin, _ = admin_portal_client
    prefix = f"/v1/partner/accounts/{account}"
    for values in ([], ["sponsored"], ["ugc", "external"]):
        assert portal.put(prefix + "/preferences", json={"link_rel": values}).status_code == 422
        for client, policy_prefix in (
            (portal, prefix),
            (admin, f"/v1/admin/partner-accounts/{account}"),
        ):
            assert (
                client.put(
                    f"{policy_prefix}/products/{product}/promotion", json={"link_rel": values}
                ).status_code
                == 422
            )
    assert portal.put(prefix + "/preferences", json={"link_rel": ["nofollow"]}).status_code == 200
    reset = portal.put(f"{prefix}/products/{product}/promotion", json={"link_rel": None})
    assert reset.status_code == 200, reset.text
    assert reset.json()["effective_link_rel"] == ["nofollow"]


@pytest.mark.integration
def test_admin_downgrade_preserves_configuration_but_reapplies_nofollow(
    database, portal_client, admin_portal_client
):
    account, product = owned_catalog(database, tier="diamond")
    portal, _ = portal_client
    admin, _ = admin_portal_client
    prefix = f"/v1/partner/accounts/{account}"
    path = f"{prefix}/products/{product}/promotion"
    assert portal.put(prefix + "/preferences", json={"link_rel": ["sponsored"]}).status_code == 200
    assert portal.put(path, json={"link_rel": []}).status_code == 200
    admin_path = f"/v1/admin/partner-accounts/{account}"
    for tier, expected in (("gold", ["nofollow"]), ("platinum", [])):
        response = admin.put(admin_path, json={"name": "Downgraded partner", "tier": tier})
        assert response.status_code == 200, response.text
        assert response.json()["link_rel"] == ["sponsored"]
        assert response.json()["configurable_link_rel"] is (tier == "platinum")
        policy = portal.get(path).json()
        assert policy["link_rel"] == []
        assert policy["effective_link_rel"] == expected
        preview = portal.get(prefix + "/creative-preview", params={"product_id": str(product)})
        assert preview.json()["product"]["link_rel"] == expected


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["feed", "header_sponsorship", "engagement"])
def test_every_automatic_format_uses_effective_relationships(database, feed_catalog, kind):
    native_account(database, feed_catalog, [kind])
    with database.begin() as session:
        account = session.get(PartnerAccount, feed_catalog["paid"])
        account.tier = "platinum"
        account.link_rel = ["sponsored"]
        session.add(
            PartnerProductPromotion(
                account_id=account.id, product_id=feed_catalog["paid_product"], link_rel=[]
            )
        )
    query = (
        PlacementQuery(surface="header", ad_formats=[kind], article_ids=[feed_catalog["article"]])
        if kind == "header_sponsorship"
        else feed_request(feed_catalog, [kind])
    )
    sponsored = [row for row in deliver(database, query) if row["sponsored"]]
    assert sponsored and sponsored[0]["link_rel"] == []
    with database.begin() as session:
        session.get(PartnerAccount, feed_catalog["paid"]).tier = "gold"
    sponsored = [row for row in deliver(database, query) if row["sponsored"]]
    assert sponsored and sponsored[0]["link_rel"] == ["nofollow"]


@pytest.mark.integration
def test_sponsored_articles_keep_live_policy_after_campaign_retargets(
    database, feed_catalog, admin_portal_client, client
):
    native_account(database, feed_catalog, [])
    with database.begin() as session:
        account = session.get(PartnerAccount, feed_catalog["paid"])
        account.tier = "platinum"
        account.link_rel = ["sponsored"]
        session.add(
            PartnerProductPromotion(
                account_id=account.id, product_id=feed_catalog["paid_product"], link_rel=[]
            )
        )
    article_id = sponsored_article(database, feed_catalog)
    admin, _ = admin_portal_client
    prefix = f"/v1/admin/partner-accounts/{feed_catalog['paid']}/campaigns"
    selection = {"targeting": "selected", "product_ids": [str(feed_catalog["paid_product"])]}
    created = admin.post(prefix, json={**campaign_payload(article_id), **selection})
    assert created.status_code == 201, created.text
    row = deliver(database, feed_request(feed_catalog, ["sponsored_content"]))[0]
    assert row["link_rel"] == []
    assert row["article"].sponsor_link_rel == []
    assert client.get(f"/v1/articles/{article_id}").json()["sponsor_link_rel"] == []
    changed = admin.put(
        prefix + "/" + created.json()["id"],
        json={**campaign_payload(feed_catalog["article"]), **selection},
    )
    assert changed.status_code == 200, changed.text
    with database() as session:
        assert sponsored_article_link_rel(session, [article_id])[article_id] == []
    with database.begin() as session:
        session.get(PartnerAccount, feed_catalog["paid"]).tier = "gold"
    old = client.get(f"/v1/articles/{article_id}")
    assert old.status_code == 200, old.text
    assert old.json()["sponsor_link_rel"] == ["nofollow"]
    assert old.json()["sponsor_names"] == ["Paid partner"]


@pytest.mark.integration
def test_sponsored_article_policy_batch_adds_one_query_for_any_page_size(database, feed_catalog):
    with database.begin() as session:
        article = session.get(Article, feed_catalog["article"])
        article.sponsor_names = ["Historical sponsor"]
    with database() as session:
        article = session.get(Article, feed_catalog["article"])
        # Load public relationships before measuring the policy batch itself.
        ArticleOut.from_article(article)
        statements = []
        engine = database.kw["bind"]

        def count(*args):
            statements.append(args[2])

        event.listen(engine, "before_cursor_execute", count)
        try:
            rows = ArticleOut.from_articles([article] * 24)
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert len(statements) == 1
        assert all(row.sponsor_link_rel == ["nofollow"] for row in rows)


@pytest.mark.integration
@pytest.mark.parametrize("value", ['["opener"]', '["nofollow","nofollow"]', "{}", "[true]"])
@pytest.mark.parametrize("table", ["partner_accounts", "partner_product_promotions"])
def test_database_rejects_invalid_relationship_arrays(database, table, value):
    account, product = owned_catalog(database, tier="platinum")
    with database() as session:
        session.add(PartnerProductPromotion(account_id=account, product_id=product))
        session.flush()
        key = "id" if table == "partner_accounts" else "account_id"
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                text(f"UPDATE {table} SET link_rel=CAST(:value AS JSONB) WHERE {key}=:account"),
                {"value": value, "account": account},
            )


@pytest.mark.integration
@pytest.mark.parametrize(
    "canonical,alias,expected",
    [
        (None, [], ["nofollow"]),
        ([], None, ["nofollow"]),
        (["sponsored"], ["nofollow"], ["nofollow", "sponsored"]),
        ([], [], []),
        (None, None, None),
    ],
)
def test_product_identity_merge_preserves_inherited_and_explicit_relationships(
    database, catalog, canonical, alias, expected
):
    from devfeed_core.models import PartnerProduct
    from devfeed_core.partner_catalog import merge_products

    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        account.link_rel = ["nofollow"]
        recipient = session.get(PartnerProduct, catalog["paid_product"])
        donor = session.get(PartnerProduct, catalog["unrelated"])
        session.add_all(
            [
                PartnerProductPromotion(
                    account_id=account.id, product_id=recipient.id, link_rel=canonical
                ),
                PartnerProductPromotion(account_id=account.id, product_id=donor.id, link_rel=alias),
            ]
        )
        session.flush()
        merge_products(session, donor, recipient)
        policy = session.get(PartnerProductPromotion, (account.id, recipient.id))
        assert policy.link_rel == expected


@pytest.mark.integration
@pytest.mark.parametrize(
    "canonical,alias,expected", [([], ["ugc"], ["ugc"]), (None, [], ["external"])]
)
def test_downgraded_product_merge_preserves_saved_choices_for_a_later_upgrade(
    database, catalog, canonical, alias, expected
):
    from devfeed_core.models import PartnerProduct
    from devfeed_core.partner_catalog import merge_products

    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        account.tier = "gold"
        account.link_rel = ["external"]
        recipient = session.get(PartnerProduct, catalog["paid_product"])
        donor = session.get(PartnerProduct, catalog["unrelated"])
        session.add_all(
            [
                PartnerProductPromotion(
                    account_id=account.id, product_id=recipient.id, link_rel=canonical
                ),
                PartnerProductPromotion(account_id=account.id, product_id=donor.id, link_rel=alias),
            ]
        )
        session.flush()
        merge_products(session, donor, recipient)
        policy = session.get(PartnerProductPromotion, (account.id, recipient.id))
        assert policy.link_rel == expected
        assert effective_link_rel(account, policy) == ["nofollow"]
        account.tier = "platinum"
        assert effective_link_rel(account, policy) == expected


@pytest.mark.integration
def test_article_policy_changes_invalidate_public_cache_after_commit(
    database, feed_catalog, admin_portal_client, client, monkeypatch
):
    from devfeed_core import cache
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    try:
        native_account(database, feed_catalog, [])
        with database.begin() as session:
            account = session.get(PartnerAccount, feed_catalog["paid"])
            account.tier = "platinum"
            account.link_rel = ["external"]
        article_id = sponsored_article(database, feed_catalog)
        admin, _ = admin_portal_client
        created = admin.post(
            f"/v1/admin/partner-accounts/{feed_catalog['paid']}/campaigns",
            json={
                **campaign_payload(article_id),
                "targeting": "selected",
                "product_ids": [str(feed_catalog["paid_product"])],
            },
        )
        assert created.status_code == 201, created.text
        deliver(database, feed_request(feed_catalog, ["sponsored_content"]))
        path = f"/v1/articles/{article_id}"
        assert client.get(path).json()["sponsor_link_rel"] == ["external"]
        assert client.get(path).headers["x-cache"] == "HIT"
        account_path = f"/v1/admin/partner-accounts/{feed_catalog['paid']}"
        product_path = f"{account_path}/products/{feed_catalog['paid_product']}/promotion"
        saved = admin.put(product_path, json={"link_rel": []})
        assert saved.status_code == 200, saved.text
        changed = client.get(path)
        assert changed.headers["x-cache"] == "MISS"
        assert changed.json()["sponsor_link_rel"] == []
        assert client.get(path).headers["x-cache"] == "HIT"
        downgraded = admin.put(account_path, json={"name": "Paid partner", "tier": "gold"})
        assert downgraded.status_code == 200, downgraded.text
        changed = client.get(path)
        assert changed.headers["x-cache"] == "MISS"
        assert changed.json()["sponsor_link_rel"] == ["nofollow"]
    finally:
        cache.close_cache()


@pytest.mark.integration
def test_delayed_sponsored_asset_enrollment_invalidates_cached_account_fallback(
    database, feed_catalog, admin_portal_client, client, monkeypatch
):
    import uuid

    from devfeed_core import cache
    from devfeed_core.config import get_settings
    from devfeed_core.models import PartnerAsset
    from devfeed_core.partner_promotions import ensure_automatic_assets
    from sqlalchemy import select

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    try:
        native_account(database, feed_catalog, [])
        with database.begin() as session:
            account = session.get(PartnerAccount, feed_catalog["paid"])
            account.tier = "platinum"
            session.add(
                PartnerProductPromotion(
                    account_id=account.id, product_id=feed_catalog["paid_product"], link_rel=[]
                )
            )
        article_id = sponsored_article(database, feed_catalog)
        admin, _ = admin_portal_client
        created = admin.post(
            f"/v1/admin/partner-accounts/{feed_catalog['paid']}/campaigns",
            json={
                **campaign_payload(article_id),
                "targeting": "selected",
                "product_ids": [str(feed_catalog["paid_product"])],
            },
        )
        assert created.status_code == 201, created.text
        with database.begin() as session:
            for asset in session.scalars(
                select(PartnerAsset).where(
                    PartnerAsset.campaign_id == uuid.UUID(created.json()["id"])
                )
            ):
                session.delete(asset)
        path = f"/v1/articles/{article_id}"
        assert client.get(path).json()["sponsor_link_rel"] == ["nofollow"]
        assert client.get(path).headers["x-cache"] == "HIT"
        with database.begin() as session:
            assert ensure_automatic_assets(session, feed_catalog["paid"]) > 0
        changed = client.get(path)
        assert changed.headers["x-cache"] == "MISS"
        assert changed.json()["sponsor_link_rel"] == []
        # The idempotent enrollment path creates no association and keeps cache hits.
        with database.begin() as session:
            assert ensure_automatic_assets(session, feed_catalog["paid"]) == 0
        assert client.get(path).headers["x-cache"] == "HIT"
    finally:
        cache.close_cache()


@pytest.mark.integration
def test_multiple_historical_sponsors_union_current_relationships(database, feed_catalog):
    from devfeed_core.models import PartnerAsset

    article_id = feed_catalog["article"]
    with database.begin() as session:
        session.get(Article, article_id).sponsor_names = ["Paid partner", "Free partner"]
        session.get(PartnerAccount, feed_catalog["paid"]).link_rel = ["external"]
        session.get(PartnerAccount, feed_catalog["free"]).link_rel = ["sponsored"]
        for account, product in (("paid", "paid_product"), ("free", "free_product")):
            session.add(
                PartnerAsset(
                    account_id=feed_catalog[account],
                    product_id=feed_catalog[product],
                    article_id=article_id,
                    ad_format="sponsored_content",
                    kind="ad",
                    name="Historical sponsorship",
                    status="ended",
                )
            )
    with database() as session:
        assert sponsored_article_link_rel(session, [article_id])[article_id] == [
            "nofollow",
            "external",
        ]


@pytest.mark.integration
@pytest.mark.parametrize("change", ["pause", "retarget", "format"])
def test_waiting_campaign_changes_invalidate_cached_article_policy(
    database, feed_catalog, admin_portal_client, client, monkeypatch, change
):
    from devfeed_core import cache
    from devfeed_core.config import get_settings
    from devfeed_http import partner_campaigns

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    # A resumable large-catalog enrollment can leave an activated campaign
    # without its immutable asset associations until a later worker slice.
    monkeypatch.setattr(partner_campaigns, "request_promotion_enrollment", Mock())
    cache.close_cache()
    try:
        native_account(database, feed_catalog, [])
        with database.begin() as session:
            account = session.get(PartnerAccount, feed_catalog["paid"])
            account.tier = "platinum"
            account.link_rel = ["external"]
            # Retargeting to an already-disclosed article must not rely on a
            # changed sponsor_names value to trigger cache invalidation.
            session.get(Article, feed_catalog["article"]).sponsor_names = ["Paid partner"]
        article_id = sponsored_article(database, feed_catalog)
        admin, _ = admin_portal_client
        prefix = f"/v1/admin/partner-accounts/{feed_catalog['paid']}/campaigns"
        payload = campaign_payload(article_id)
        created = admin.post(prefix, json=payload)
        assert created.status_code == 201, created.text
        path = f"/v1/articles/{article_id}"
        other_path = f"/v1/articles/{feed_catalog['article']}"
        assert client.get(path).json()["sponsor_link_rel"] == ["external"]
        assert client.get(path).headers["x-cache"] == "HIT"
        assert client.get(other_path).json()["sponsor_link_rel"] == ["nofollow"]
        assert client.get(other_path).headers["x-cache"] == "HIT"
        if change == "pause":
            payload["status"] = "paused"
        elif change == "retarget":
            payload["article_id"] = str(feed_catalog["article"])
        else:
            payload.update(ad_format="feed", article_id=None)
        updated = admin.put(prefix + "/" + created.json()["id"], json=payload)
        assert updated.status_code == 200, updated.text
        changed = client.get(path)
        assert changed.headers["x-cache"] == "MISS"
        assert changed.json()["sponsor_link_rel"] == ["nofollow"]
        other = client.get(other_path)
        assert other.json()["sponsor_link_rel"] == (
            ["external"] if change == "retarget" else ["nofollow"]
        )
    finally:
        cache.close_cache()
