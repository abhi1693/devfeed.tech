"""Free inclusion, paid scope and grounded context remain independent delivery gates."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core import partner_placements as placements
from devfeed_core.partner_tracking import BusyPartnerAsset, PartnerTrackingTokens
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy.dialects.postgresql import dialect

NOW = datetime(2026, 10, 11, 0, 15, tzinfo=UTC)
KEY = "disposable-placement-policy-unit-secret"


@pytest.fixture
def policy(monkeypatch):
    product = NS(
        id=uuid.uuid4(),
        status="approved",
        excluded=False,
        merged_into_id=None,
        verified_at=NOW,
        revision=1,
        updated_at=NOW,
        technologies=["Kubernetes"],
    )
    account = NS(
        id=uuid.uuid4(),
        status="active",
        tier="gold",
        promotion_kind="product",
        approval_mode="qualified",
        outbound_destination="product_url",
        organic_enabled=True,
        auto_include_products=True,
        promotion_surfaces=["article", "topic", "feed"],
    )
    asset = NS(automatic=True, status="active", campaign_id=None)
    campaign = NS(
        id=uuid.uuid4(),
        status="active",
        targeting="all",
        managed=False,
        product_ids=[],
        technologies=[],
        topic_ids=[],
        surfaces=["article", "topic", "feed"],
        starts_at=NOW - timedelta(days=1),
        ends_at=NOW + timedelta(days=1),
    )
    ownership = Mock(return_value=True)
    monkeypatch.setattr(placements, "account_product_available", ownership)
    monkeypatch.setattr(placements, "prepared_product_topics", lambda *_: {"kubernetes-topic"})
    session = Mock()
    session.scalar.return_value = campaign
    return NS(
        product=product,
        account=account,
        asset=asset,
        campaign=campaign,
        session=session,
        ownership=ownership,
    )


def allowed(policy):
    return placements.automatic_asset_available(
        policy.session, policy.asset, policy.account, policy.product, NOW, surface="article"
    )


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("account", "status", "paused"),
        ("asset", "status", "paused"),
        ("product", "status", "pending"),
        ("product", "excluded", True),
        ("product", "merged_into_id", uuid.uuid4()),
        ("product", "verified_at", NOW - timedelta(days=91)),
    ],
)
def test_commercial_defaults_never_override_global_qualification(policy, target, field, value):
    setattr(getattr(policy, target), field, value)
    assert not allowed(policy)
    policy.ownership.assert_not_called()
    policy.session.scalar.assert_not_called()


@pytest.mark.parametrize("change", ["organic", "automatic", "surface", "ownership"])
def test_free_catalog_inherits_account_policy_without_paid_agreement(policy, change):
    assert allowed(policy)
    if change == "organic":
        policy.account.organic_enabled = False
    elif change == "automatic":
        policy.account.auto_include_products = False
    elif change == "surface":
        policy.account.promotion_surfaces = ["topic"]
    else:
        policy.ownership.return_value = False
    assert not allowed(policy)
    policy.session.scalar.assert_not_called()


@pytest.mark.parametrize(
    "case,expected",
    [
        ("all", True),
        ("selected", True),
        ("selected_without_auto_include", True),
        ("all_without_auto_include", False),
        ("different_product", False),
        ("technology_match", True),
        ("technology_mismatch", False),
        ("topic_match", True),
        ("topic_mismatch", False),
        ("campaign_surface", False),
        ("expired", False),
        ("paused", False),
    ],
)
def test_paid_scope_inherits_explicit_campaign_and_current_product_filters(policy, case, expected):
    policy.asset.campaign_id = policy.campaign.id
    # Sponsorship is independently authorized even when organic recommendations are off.
    policy.account.organic_enabled = False
    if case.startswith("selected") or case == "different_product":
        policy.campaign.targeting = "selected"
        policy.campaign.product_ids = [
            str(policy.product.id) if case != "different_product" else str(uuid.uuid4())
        ]
    if "without_auto_include" in case:
        policy.account.auto_include_products = False
    if case.startswith("technology"):
        policy.campaign.targeting = "filters"
        policy.campaign.technologies = ["kubernetes" if case == "technology_match" else "Figma"]
    if case.startswith("topic"):
        policy.campaign.targeting = "filters"
        policy.campaign.topic_ids = ["kubernetes-topic" if case == "topic_match" else "figma-topic"]
    if case == "campaign_surface":
        policy.campaign.surfaces = ["topic"]
    if case == "expired":
        policy.campaign.ends_at = NOW
    if case == "paused":
        policy.campaign.status = "paused"
    assert allowed(policy) is expected


def test_busy_campaign_skips_issuance_and_preserves_receipt_retry(policy):
    policy.asset.campaign_id = policy.campaign.id
    policy.session.scalar.return_value = None
    assert not allowed(policy)
    with pytest.raises(BusyPartnerAsset):
        placements.automatic_asset_available(
            policy.session, policy.asset, policy.account, policy.product, NOW, busy_error=True
        )


def test_legacy_reporting_asset_preserves_its_original_contract(policy):
    policy.asset.automatic = False
    assert placements.automatic_asset_available(
        policy.session, policy.asset, policy.account, None, NOW
    )
    policy.ownership.assert_not_called()


@pytest.mark.parametrize(
    "used,expected", [(None, True), (0, True), (1, True), (2, False), (3, False)]
)
def test_cap_reserves_each_delivery_once_and_retains_today_across_midnight(used, expected):
    session = Mock()
    session.scalar.return_value = used
    campaign = NS(id=uuid.uuid4(), daily_impression_limit=2)
    assert placements.campaign_capacity(session, campaign, NOW) is expected
    sql = session.scalar.call_args.args[0].compile(dialect=dialect())
    assert sql.params["day_1"] == NOW.date()
    assert sql.params["issued_at_1"] == NOW.replace(hour=0, minute=0) - timedelta(hours=1)
    assert sql.params["issued_at_2"] == NOW - timedelta(hours=1)
    # EXISTS joins the signed delivery and its asset; OR reserves an unreported view.
    assert "EXISTS" in str(sql) and " OR " in str(sql)
    assert "partner_delivery_events.delivery_id = partner_placement_deliveries.delivery_id" in str(
        sql
    )


def test_unlimited_campaign_needs_no_count_query():
    session = Mock()
    assert placements.campaign_capacity(session, NS(daily_impression_limit=None), NOW)
    session.scalar.assert_not_called()


def test_follows_outweigh_weaker_inferred_interest_and_stale_generations_are_filtered():
    followed, inferred = uuid.uuid4(), uuid.uuid4()
    session = Mock()
    session.scalars.return_value = [followed]
    session.execute.return_value.all.return_value = [(followed, 60), (inferred, 80)]
    assert placements.personal_topic_weights(session, uuid.uuid4(), NOW) == {
        followed: 100,
        inferred: 80,
    }
    sql = session.execute.call_args.args[0].compile(dialect=dialect())
    assert sql.params["computed_at_1"] == NOW - timedelta(hours=6)
    assert "user_recommendation_states.invalidated IS false" in str(sql)
    assert "topics.status =" in str(sql)


def test_personal_context_chooses_best_grounded_topic_per_product(monkeypatch):
    direct, weak = uuid.uuid4(), uuid.uuid4()
    best, duplicate, rejected = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = Mock()
    session.execute.return_value.scalars.return_value = [
        NS(
            product_id=best,
            topic_id=direct,
            score=95,
            reason="Specific support",
            product_revision=2,
            product_updated_at=NOW,
        ),
        NS(
            product_id=duplicate,
            topic_id=direct,
            score=65,
            reason="Related support",
            product_revision=1,
            product_updated_at=NOW,
        ),
        NS(
            product_id=best,
            topic_id=weak,
            score=100,
            reason="Weak interest",
            product_revision=2,
            product_updated_at=NOW,
        ),
        NS(
            product_id=rejected,
            topic_id=weak,
            score=100,
            reason="Weak interest",
            product_revision=1,
            product_updated_at=NOW,
        ),
    ]
    monkeypatch.setattr(placements, "personal_topic_weights", lambda *_: {direct: 100, weak: 30})
    result = placements.contextual_candidates(
        session, PlacementQuery(surface="feed"), NOW, uuid.uuid4()
    )
    assert list(result) == [best, duplicate]
    assert result[best] == (95, "Specific support", 2, NOW)
    session.reset_mock()
    assert placements.contextual_candidates(session, PlacementQuery(surface="feed"), NOW) == {}
    session.execute.assert_not_called()


def test_funded_canonical_product_appears_once_with_independent_best_organic_slot(monkeypatch):
    account = NS(id=uuid.uuid4(), name="Partner", outbound_destination="product_url")
    products = [
        NS(
            id=uuid.uuid4(),
            revision=1,
            updated_at=NOW,
            name=name,
            product_url=f"https://tools.example/{name}",
            description="Verified capabilities",
        )
        for name in ("funded", "free")
    ]
    campaigns = [
        NS(id=uuid.uuid4(), priority=priority, daily_impression_limit=None) for priority in (10, 90)
    ]
    assets = [
        NS(id=uuid.uuid4(), campaign_id=campaign.id, ad_format="feed", article_id=None)
        for campaign in campaigns
    ]
    assets += [
        NS(id=uuid.uuid4(), campaign_id=None, ad_format="feed", article_id=None) for _ in products
    ]
    rows = [
        (assets[0], account, products[0]),
        (assets[1], account, products[0]),
        (assets[2], account, products[0]),
        (assets[3], account, products[1]),
    ]
    session = Mock()
    monkeypatch.setattr(placements, "contextual_candidates", lambda *_, **__: None)

    def ranked_rows(*_, phase, **__):
        # The SQL layer supplies relevance/priority-ranked rows. This unit checks
        # attribution and product deduplication across independent commercial slots.
        ordered = (
            [rows[1], rows[0]] if phase == "sponsored" else rows[2:] if phase == "organic" else []
        )
        for asset, owner, product in ordered:
            campaign = next((item for item in campaigns if item.id == asset.campaign_id), None)
            yield asset, owner, product, campaign, 95, "Specific task", 1, NOW, True

    monkeypatch.setattr(placements, "ranked_placement_rows", ranked_rows)
    monkeypatch.setattr(placements, "lock_attribution_row", lambda *_: True)
    monkeypatch.setattr(placements, "automatic_asset_available", lambda *_, **__: True)
    result = placements.deliver_placements(session, PlacementQuery(surface="feed"), KEY, now=NOW)
    assert [(row["name"], row["sponsored"]) for row in result] == [
        ("free", False),
        ("funded", True),
    ]
    assert result[1]["campaign_id"] == campaigns[1].id
    signer = PartnerTrackingTokens(KEY, clock=lambda: NOW.timestamp())
    assert all(signer.verify(row["receipt"]).asset_id == row["asset_id"] for row in result)
    assert session.add.call_count == 2


@pytest.mark.parametrize(
    "reads,expected",
    [
        ([uuid.uuid4(), False], True),
        ([None, None, False], False),
        ([uuid.uuid4(), True], False),
        ([uuid.uuid4(), None, None], True),
        ([None, None, True], "retry"),
        ([uuid.uuid4(), None, False], "retry"),
    ],
)
def test_ownership_distinguishes_removed_catalog_from_busy_policy(reads, expected):
    session = Mock()
    session.scalar.side_effect = reads
    if expected == "retry":
        with pytest.raises(BusyPartnerAsset):
            placements.account_product_available(
                session, uuid.uuid4(), uuid.uuid4(), busy_error=True
            )
    else:
        assert (
            placements.account_product_available(
                session, uuid.uuid4(), uuid.uuid4(), busy_error=True
            )
            is expected
        )


def test_prepared_topics_fence_product_and_taxonomy_edits_with_exact_snapshots():
    product = NS(id=uuid.uuid4(), revision=7, updated_at=NOW)
    topic = uuid.uuid4()
    session = Mock()
    session.scalars.return_value = [topic, topic]
    assert placements.prepared_product_topics(session, product, NOW) == {topic}
    sql = session.scalars.call_args.args[0].compile(dialect=dialect())
    assert sql.params["product_id_1"] == product.id
    assert sql.params["product_revision_1"] == 7
    assert sql.params["product_updated_at_1"] == NOW
    assert "updated_at_1" not in sql.params
    assert "partner_product_topics.topic_updated_at = topics.updated_at" in str(sql)


def test_article_context_returns_current_task_match_and_rejects_hidden_article():
    session = Mock()
    article = NS(
        id=uuid.uuid4(),
        title="Monitor Kubernetes clusters",
        summary="Monitor Kubernetes cluster metrics in this practical production tutorial.",
        content_type="tutorial",
        publication_status="published",
        editorial_revision=9,
    )
    product = uuid.uuid4()
    session.scalar.return_value = article
    session.execute.return_value = [(product, 83, "Grounded cluster metrics task", 7, NOW)]
    query = PlacementQuery(surface="article", article_id=article.id)
    assert placements.contextual_candidates(session, query, NOW) == {
        product: (83, "Grounded cluster metrics task", 7, NOW)
    }
    sql = session.execute.call_args.args[0].compile(dialect=dialect())
    assert sql.params["editorial_revision_1"] == 9
    assert sql.params["article_fingerprint_1"] == placements.article_fingerprint(article)
    assert "partner_article_matches.product_updated_at = partner_products.updated_at" in str(sql)
    session.scalar.return_value = None
    session.execute.reset_mock()
    assert placements.contextual_candidates(session, query, NOW) == {}
    session.execute.assert_not_called()
