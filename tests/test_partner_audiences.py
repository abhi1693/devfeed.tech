"""Private audience selection, inheritance, entitlement and durable attribution fences."""

import importlib.util
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core.models import (
    PartnerAccount,
    PartnerCampaign,
    PartnerPlacementDelivery,
    Topic,
    UserAccount,
    UserInterest,
    UserRecommendationState,
    UserStackAssociation,
    UserTopic,
    utcnow,
)
from devfeed_core.partner_audiences import (
    AudienceInput,
    ReaderAudience,
    audience_entitled,
    audience_matches,
    effective_audience,
    reader_audience,
    validate_audience,
)
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tiers import tier_policy
from devfeed_core.partner_tracking import (
    InactivePartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from devfeed_http.partner_placements import PlacementQuery
from devfeed_http.partner_portal import AccountOut, PartnerPreferencesInput
from pydantic import ValidationError
from sqlalchemy import select, text
from test_partner_placements_integration import KEY
from test_partner_placements_integration import catalog as catalog_fixture
from test_partner_portal import admin_portal_client as admin_client_fixture
from test_partner_portal import portal_client as portal_client_fixture
from test_partner_portal import seed

catalog = catalog_fixture
admin_portal_client = admin_client_fixture
portal_client = portal_client_fixture
LANGUAGE, SECOND_LANGUAGE, TOOL, SECOND_TOOL = (uuid.uuid4() for _ in range(4))


@pytest.mark.parametrize(
    ("criteria", "reader", "matches"),
    [
        ({}, None, True),
        ({"experience_levels": ["senior"]}, None, False),
        ({"experience_levels": ["senior", "mid_level"]}, ReaderAudience("mid_level"), True),
        ({"experience_levels": ["senior"]}, ReaderAudience("entry_level"), False),
        ({"experience_levels": ["senior"]}, ReaderAudience(), False),
        (
            {"language_topic_ids": [LANGUAGE, SECOND_LANGUAGE]},
            ReaderAudience(None, {LANGUAGE}),
            True,
        ),
        ({"tool_topic_ids": [TOOL]}, ReaderAudience("senior", {LANGUAGE}), False),
        (
            {"language_topic_ids": [LANGUAGE], "tool_topic_ids": [TOOL, SECOND_TOOL]},
            ReaderAudience(None, {LANGUAGE, SECOND_TOOL}),
            True,
        ),
        (
            {
                "experience_levels": ["senior"],
                "language_topic_ids": [LANGUAGE],
                "tool_topic_ids": [TOOL],
            },
            ReaderAudience("senior", {LANGUAGE}),
            False,
        ),
        (
            {
                "experience_levels": ["senior"],
                "language_topic_ids": [LANGUAGE],
                "tool_topic_ids": [TOOL],
            },
            ReaderAudience("senior", {LANGUAGE, TOOL}),
            True,
        ),
    ],
)
def test_dimensions_are_or_within_and_across(criteria, reader, matches):
    assert audience_matches(AudienceInput(**criteria), reader) is matches


@pytest.mark.parametrize("tier", ["bronze", "silver", "gold", "platinum", "diamond"])
def test_tiers_enforce_populated_audience_without_rewriting_it(tier):
    targeted = AudienceInput(experience_levels=["senior"])
    account = SimpleNamespace(tier=tier, audience=targeted.model_dump(mode="json"))
    assert tier_policy(tier)["advanced_targeting"] is (tier in {"platinum", "diamond"})
    assert audience_entitled(account) is (tier in {"platinum", "diamond"})
    assert effective_audience(account) == targeted
    # A custom empty campaign means all audience, while null retains account policy.
    assert audience_entitled(account, SimpleNamespace(audience={}))
    assert audience_entitled(account, SimpleNamespace(audience=None)) == audience_entitled(account)


@pytest.mark.parametrize(
    "payload",
    [
        {"experience_levels": ["lead"]},
        {"experience_levels": ["senior", "senior"]},
        {"language_topic_ids": [str(LANGUAGE), str(LANGUAGE)]},
        {"tool_topic_ids": [str(uuid.uuid4()) for _ in range(101)]},
        {"user_id": str(uuid.uuid4())},
    ],
)
def test_audience_rejects_ambiguous_unbounded_or_forged_criteria(payload):
    with pytest.raises(ValidationError):
        AudienceInput.model_validate(payload)
    assert effective_audience(SimpleNamespace(audience=payload)) is None


def test_partial_preferences_and_retained_downgrade_configuration():
    assert PartnerPreferencesInput(audience={}).outbound_destination is None
    assert PartnerPreferencesInput(outbound_destination="listing_url").audience is None
    with pytest.raises(ValidationError):
        PartnerPreferencesInput()
    audience = AudienceInput(experience_levels=["senior"])
    validate_audience(None, "gold", audience, previous=audience.model_dump(mode="json"))
    validate_audience(None, "gold", AudienceInput())
    with pytest.raises(ValueError, match="Platinum or Diamond"):
        validate_audience(None, "gold", audience)


def configure(database, catalog, *, account=None, campaign=None, level="senior", follows=()):
    with database.begin() as session:
        session.get(UserAccount, catalog["user"]).profile = {"experience_level": level}
        if account is not None:
            session.get(PartnerAccount, catalog["paid"]).audience = account
        if campaign is not None:
            session.get(PartnerCampaign, catalog["campaign"]).audience = campaign
        session.add_all(UserTopic(user_id=catalog["user"], topic_id=topic) for topic in follows)


def issue(database, catalog, *, user=True, query=None):
    with database.begin() as session:
        return deliver_placements(
            session,
            query or PlacementQuery(surface="article", article_id=catalog["article"]),
            KEY,
            user_id=catalog["user"] if user else None,
        )


def sponsored(items):
    return [item for item in items if item["sponsored"]]


@pytest.mark.integration
def test_account_inheritance_custom_override_and_guest_privacy(database, catalog):
    configure(database, catalog, account={"experience_levels": ["senior"]})
    assert not sponsored(issue(database, catalog, user=False))
    assert len(sponsored(issue(database, catalog))) == 1
    configure(database, catalog, campaign={"experience_levels": ["entry_level"]})
    assert not sponsored(issue(database, catalog))
    configure(database, catalog, campaign={})
    assert len(sponsored(issue(database, catalog, user=False))) == 1
    with database() as session:
        # Durable tracking remains anonymous; neither private criteria nor a user ID is copied.
        reservation = session.scalars(select(PartnerPlacementDelivery)).first()
        assert set(reservation.__table__.columns.keys()) == {
            "delivery_id",
            "asset_id",
            "issued_at",
            "surface",
        }


@pytest.mark.integration
@pytest.mark.parametrize("change", ["retire", "wrong_kind", "missing_language", "missing_tool"])
def test_current_canonical_topics_and_all_populated_dimensions_gate_delivery(
    database, catalog, change
):
    with database.begin() as session:
        session.add(
            Topic(id=LANGUAGE, name="Python", slug="python", kind="language", status="active")
        )
    configure(
        database,
        catalog,
        campaign={
            "experience_levels": ["senior"],
            "language_topic_ids": [str(LANGUAGE)],
            "tool_topic_ids": [str(catalog["topic"])],
        },
        follows=(LANGUAGE, catalog["topic"]),
    )
    assert len(sponsored(issue(database, catalog))) == 1
    with database.begin() as session:
        if change == "retire":
            session.get(Topic, LANGUAGE).status = "rejected"
        elif change == "wrong_kind":
            session.get(Topic, LANGUAGE).kind = "platform"
        else:
            missing = LANGUAGE if change == "missing_language" else catalog["topic"]
            session.delete(session.get(UserTopic, (catalog["user"], missing)))
    assert not sponsored(issue(database, catalog))


@pytest.mark.integration
def test_downgrade_blocks_targeted_issuance_and_signed_receipt_without_destroying_policy(
    database, catalog
):
    configure(database, catalog, account={"experience_levels": ["senior"]})
    item = sponsored(issue(database, catalog))[0]
    receipt = PartnerTrackingTokens(KEY).verify(item["receipt"])
    with database.begin() as session:
        session.get(PartnerAccount, catalog["paid"]).tier = "gold"
    assert not sponsored(issue(database, catalog))
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        output = AccountOut.model_validate(account).model_dump(mode="json")
        assert output["advanced_targeting"] is False
        assert output["audience"]["experience_levels"] == ["senior"]
        with pytest.raises(InactivePartnerAsset):
            record_delivery_event(session, receipt, "impression")


@pytest.mark.integration
def test_main_feed_fallback_respects_audience_and_explicit_campaign_shadowing(database, catalog):
    configure(database, catalog, account={"experience_levels": ["senior"]})
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        account.promotion_kind = None
        session.get(PartnerCampaign, catalog["campaign"]).status = "paused"
        ensure_automatic_assets(session, account.id)
    query = PlacementQuery(surface="feed", allow_sponsored_fallback=True)
    assert not sponsored(issue(database, catalog, user=False, query=query))
    assert len(sponsored(issue(database, catalog, query=query))) == 1
    with database.begin() as session:
        campaign = session.get(PartnerCampaign, catalog["campaign"])
        campaign.status = "active"
        campaign.audience = {"experience_levels": ["entry_level"]}
    # The explicit agreement owns this inventory, so its narrower audience cannot be bypassed.
    assert not sponsored(issue(database, catalog, query=query))


@pytest.mark.integration
def test_busy_profile_fails_closed_without_reserving_targeted_inventory(database, catalog):
    configure(database, catalog, account={"experience_levels": ["senior"]})
    with database.begin() as editor:
        editor.execute(
            select(UserAccount).where(UserAccount.id == catalog["user"]).with_for_update()
        )
        assert not sponsored(issue(database, catalog))
    assert len(sponsored(issue(database, catalog))) == 1


@pytest.mark.integration
@pytest.mark.parametrize("state", ["current", "stale", "expired", "invalidated"])
def test_only_current_stack_follows_and_fresh_valid_interests_supply_audience(
    database, catalog, state
):
    now = utcnow()
    with database.begin() as session:
        session.add_all(
            [
                Topic(id=LANGUAGE, name="Python", slug="python", kind="language", status="active"),
                Topic(
                    id=SECOND_LANGUAGE, name="Rust", slug="rust", kind="language", status="active"
                ),
            ]
        )
        session.flush()
        session.get(UserAccount, catalog["user"]).profile = {
            "since_year": 2001,
            "bio": "Senior Rust developer",
        }
        session.add_all(
            [
                UserStackAssociation(
                    user_id=catalog["user"], topic_id=LANGUAGE, section="learning", position=0
                ),
                UserStackAssociation(
                    user_id=catalog["user"], topic_id=SECOND_LANGUAGE, section="past", position=1
                ),
                UserTopic(user_id=catalog["user"], topic_id=catalog["other_topic"]),
                UserInterest(
                    user_id=catalog["user"],
                    topic_id=catalog["topic"],
                    seed_topic_id=LANGUAGE,
                    weight=100,
                    reason="related",
                ),
            ]
        )
        session.flush()  # Preference triggers create/invalidate the recommendation generation.
        generation = session.get(UserRecommendationState, catalog["user"])
        generation.invalidated = state == "invalidated"
        generation.computed_at = now - timedelta(hours=7) if state == "stale" else now
        generation.expires_at = (
            now - timedelta(seconds=1) if state == "expired" else now + timedelta(hours=1)
        )
    with database.begin() as session:
        reader = reader_audience(session, catalog["user"], now)
        assert reader.experience_level is None  # Never inferred from bio or dates.
        assert LANGUAGE in reader.topic_ids
        assert SECOND_LANGUAGE not in reader.topic_ids
        assert catalog["other_topic"] in reader.topic_ids
        assert (catalog["topic"] in reader.topic_ids) is (state == "current")


@pytest.mark.integration
def test_reader_signals_load_once_for_multiple_candidates_and_not_for_guests(
    database, catalog, monkeypatch
):
    from devfeed_core import partner_placement_preflight

    configure(database, catalog, account={"experience_levels": ["senior"]})
    with database.begin() as session:
        session.get(PartnerAccount, catalog["free"]).tier = "platinum"
        session.get(PartnerAccount, catalog["free"]).promotion_kind = "product"
        session.get(PartnerAccount, catalog["free"]).audience = {"experience_levels": ["senior"]}
    calls = []
    original = partner_placement_preflight.reader_audience

    def counted(*args):
        calls.append(args[1])
        return original(*args)

    monkeypatch.setattr(partner_placement_preflight, "reader_audience", counted)
    assert len(issue(database, catalog)) == 2
    assert calls == [catalog["user"]]
    calls.clear()
    assert not issue(database, catalog, user=False)
    assert calls == []


@pytest.mark.integration
def test_preferences_are_partial_and_owned_and_topic_lookups_cannot_admit_invalid_ids(
    database, portal_client, admin_portal_client
):
    account_id, other_account, _, _ = seed(database)
    portal, _ = portal_client
    admin, _ = admin_portal_client
    with database.begin() as session:
        account = session.get(PartnerAccount, account_id)
        account.tier = "platinum"
        account.outbound_destination = "listing_url"
        session.add_all(
            [
                Topic(id=LANGUAGE, name="Python", slug="python", kind="language", status="active"),
                Topic(id=TOOL, name="Docker", slug="docker", kind="tool", status="active"),
                Topic(
                    id=SECOND_LANGUAGE,
                    name="Retired language",
                    slug="retired",
                    kind="language",
                    status="rejected",
                ),
                Topic(
                    id=SECOND_TOOL, name="Career", slug="career", kind="concept", status="active"
                ),
            ]
        )
    url = f"/v1/partner/accounts/{account_id}"
    preferences = url + "/preferences"
    response = portal.put(preferences, json={"audience": {"language_topic_ids": [str(LANGUAGE)]}})
    assert response.status_code == 200, response.text
    assert response.json()["outbound_destination"] == "listing_url"
    assert response.json()["advanced_targeting"] is True
    assert portal.put(preferences, json={"outbound_destination": "product_url"}).json()["audience"][
        "language_topic_ids"
    ] == [str(LANGUAGE)]
    for bad in (SECOND_LANGUAGE, SECOND_TOOL, uuid.uuid4()):
        assert (
            portal.put(
                preferences, json={"audience": {"language_topic_ids": [str(bad)]}}
            ).status_code
            == 422
        )
    assert (
        portal.put(
            f"/v1/partner/accounts/{other_account}/preferences", json={"audience": {}}
        ).status_code
        == 404
    )
    assert (
        portal.get(
            f"/v1/partner/accounts/{other_account}/audience-topics?dimension=languages"
        ).status_code
        == 404
    )
    lookup = url + "/audience-topics"
    assert [
        item["id"] for item in portal.get(lookup, params={"dimension": "languages"}).json()["items"]
    ] == [str(LANGUAGE)]
    assert [
        item["id"]
        for item in portal.get(lookup, params={"dimension": "tools", "q": "Docker"}).json()["items"]
    ] == [str(TOOL)]
    assert portal.get(lookup, params={"dimension": "tools", "limit": 101}).status_code == 422
    assert portal.get(lookup, params={"dimension": "unknown"}).status_code == 422
    global_lookup = "/v1/admin/partner-accounts/audience-topics"
    assert admin.get(global_lookup, params={"dimension": "languages"}).json()["total"] == 1
    with database.begin() as session:
        session.get(Topic, LANGUAGE).status = "rejected"
    # A saved unavailable selection is resolvable by its owner, but excluded from new selections.
    assert (
        portal.get(lookup, params={"dimension": "languages", "topic_id": str(LANGUAGE)}).json()[
            "items"
        ][0]["status"]
        == "rejected"
    )
    assert (
        admin.get(
            global_lookup, params={"dimension": "languages", "topic_id": str(LANGUAGE)}
        ).json()["items"]
        == []
    )
    with database.begin() as session:
        session.get(PartnerAccount, account_id).tier = "gold"
    assert portal.put(preferences, json={"outbound_destination": "listing_url"}).status_code == 200
    assert (
        portal.put(preferences, json={"audience": {"experience_levels": ["senior"]}}).status_code
        == 422
    )
    assert portal.put(preferences, json={"audience": {}}).status_code == 200


@pytest.mark.integration
def test_admin_mutations_validate_entitlements_and_nested_uuid_serialization(
    database, admin_portal_client
):
    admin, identity = admin_portal_client
    with database.begin() as session:
        session.add(
            Topic(id=LANGUAGE, name="Python", slug="python", kind="language", status="active")
        )
    payload = {
        "name": "Audience partner",
        "tier": "gold",
        "audience": {"language_topic_ids": [str(LANGUAGE)]},
    }
    root = "/v1/admin/partner-accounts"
    assert admin.post(root, json=payload).status_code == 422
    payload["tier"] = "platinum"
    created = admin.post(root, json=payload)
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    assert created.json()["audience"]["language_topic_ids"] == [str(LANGUAGE)]
    campaign = {
        "name": "Audience agreement",
        "starts_at": (utcnow() - timedelta(days=1)).isoformat(),
        "ends_at": (utcnow() + timedelta(days=1)).isoformat(),
        "audience": {"language_topic_ids": [str(LANGUAGE)]},
    }
    campaign_url = f"{root}/{account_id}/campaigns"
    saved = admin.post(campaign_url, json=campaign)
    assert saved.status_code == 201, saved.text
    assert saved.json()["audience"]["language_topic_ids"] == [str(LANGUAGE)]
    saved_id = saved.json()["id"]
    assert (
        admin.put(f"{campaign_url}/{saved_id}", json={**campaign, "audience": None}).json()[
            "audience"
        ]
        is None
    )
    assert admin.put(f"{campaign_url}/{saved_id}", json={**campaign, "audience": {}}).json()[
        "audience"
    ] == {"experience_levels": [], "language_topic_ids": [], "tool_topic_ids": []}
    # Changing tier preserves the stored restriction. Reads remain available on the lower tier.
    payload["tier"] = "gold"
    downgraded = admin.put(f"{root}/{account_id}", json=payload)
    assert downgraded.status_code == 200, downgraded.text
    assert downgraded.json()["advanced_targeting"] is False
    assert downgraded.json()["audience"]["language_topic_ids"] == [str(LANGUAGE)]
    assert admin.post(campaign_url, json=campaign).status_code == 422
    assert (
        admin.put(
            f"{root}/{account_id}", json={**payload, "audience": {"experience_levels": ["senior"]}}
        ).status_code
        == 422
    )
    assert admin.put(f"{root}/{account_id}", json={**payload, "audience": {}}).status_code == 200
    identity.roles = ["admin"]
    assert admin.get(root + "/audience-topics?dimension=languages").status_code == 403


@pytest.mark.integration
def test_saved_campaign_unavailable_topic_resolution_is_account_scoped(database, portal_client):
    account_id, other_account, _, _ = seed(database)
    portal, _ = portal_client
    with database.begin() as session:
        session.add_all(
            [
                Topic(
                    id=LANGUAGE,
                    name="Old language",
                    slug="old-language",
                    kind="language",
                    status="rejected",
                ),
                Topic(
                    id=SECOND_LANGUAGE,
                    name="Other old language",
                    slug="other-language",
                    kind="language",
                    status="rejected",
                ),
                PartnerCampaign(
                    account_id=account_id,
                    name="Saved campaign",
                    audience={"language_topic_ids": [str(LANGUAGE)]},
                    starts_at=utcnow(),
                    ends_at=utcnow() + timedelta(days=1),
                ),
                PartnerCampaign(
                    account_id=other_account,
                    name="Other saved campaign",
                    audience={"language_topic_ids": [str(SECOND_LANGUAGE)]},
                    starts_at=utcnow(),
                    ends_at=utcnow() + timedelta(days=1),
                ),
            ]
        )
    url = f"/v1/partner/accounts/{account_id}/audience-topics"
    assert (
        portal.get(url, params={"dimension": "languages", "topic_id": str(LANGUAGE)}).json()[
            "total"
        ]
        == 1
    )
    assert (
        portal.get(url, params={"dimension": "languages", "topic_id": str(SECOND_LANGUAGE)}).json()[
            "total"
        ]
        == 0
    )


@pytest.mark.integration
def test_migration_preserves_unrestricted_inventory_and_pauses_targeted_downgrade(database):
    path = Path(__file__).parents[1] / "migrations/versions/0033_partner_audiences.py"
    spec = importlib.util.spec_from_file_location("audience_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA audience_migration_fixture"))
            connection.execute(text("SET LOCAL search_path TO audience_migration_fixture"))
            for table in ("partner_accounts", "partner_campaigns"):
                connection.execute(
                    text(f"CREATE TABLE {table} (id int PRIMARY KEY, status text, name text)")
                )
                connection.execute(text(f"INSERT INTO {table} VALUES (1, 'active', 'preserved')"))
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
                assert connection.scalar(text("SELECT audience FROM partner_accounts")) == {}
                assert connection.scalar(text("SELECT audience FROM partner_campaigns")) is None
                for table in ("partner_accounts", "partner_campaigns"):
                    connection.execute(
                        text(
                            f"INSERT INTO {table} VALUES "
                            "(2, 'active', 'targeted', '{\"experience_levels\":[\"senior\"]}'), "
                            "(3, 'active', 'partial empty', '{\"experience_levels\":[]}')"
                        )
                    )
                migration.downgrade()
                for table in ("partner_accounts", "partner_campaigns"):
                    assert connection.execute(
                        text(f"SELECT id,status,name FROM {table} ORDER BY id")
                    ).all() == [
                        (1, "active", "preserved"),
                        (2, "paused", "targeted"),
                        (3, "active", "partial empty"),
                    ]
                migration.upgrade()
                assert connection.scalar(text("SELECT count(*) FROM partner_accounts")) == 3
        finally:
            transaction.rollback()
