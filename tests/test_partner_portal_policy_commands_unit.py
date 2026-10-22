"""Partner previews, policy edits and report composition over scoped read results.

These tests inspect requested writes and returned documents. SQL catalog ownership,
aggregation, conflict handling and query ordering remain integration contracts.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core import partner_product_images
from devfeed_core.models import PartnerProductPromotion
from devfeed_core.partner_placement_destinations import AccountProductCreative
from devfeed_http import partner_portal as portal
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session
from test_partner_catalog_commands_unit import policy
from test_partner_matching_workflow_unit import NOW, product, result
from test_partner_placement_workflow_unit import account
from test_source_readiness_unit import configuration


@pytest.fixture(autouse=True)
def storage_policy(monkeypatch):
    settings = configuration(image_storage_enabled=True, image_public_url="https://images.example")
    monkeypatch.setattr(portal, "get_settings", lambda: settings)
    monkeypatch.setattr(partner_product_images, "get_settings", lambda: settings)


@pytest.mark.parametrize("case", ["no_inventory", "no_owned_creative", "ready"])
@pytest.mark.parametrize("kind", ["destination", "creative"])
def test_previews_use_actual_accepted_inventory_and_owned_copy_without_issuing_a_receipt(
    monkeypatch, case, kind
):
    owner, value = account(approval_mode="trusted"), product()
    session = Mock(spec=Session)
    session.scalars.return_value = [] if case == "no_inventory" else [value]
    session.get.return_value = policy(owner, value)
    owned_copy = Mock(
        return_value=None
        if case == "no_owned_creative"
        else AccountProductCreative(
            "Check API contracts before merging changes.", "https://launch.example/checker"
        )
    )
    monkeypatch.setattr(portal, "account_product_creative", owned_copy)
    output = (
        portal.destination_preview(session, owner)
        if kind == "destination"
        else portal.creative_preview(session, owner, value.id)
    )
    if case != "ready":
        assert output.product is None
    else:
        assert output.product.id == value.id
        assert output.product.listing_url == "https://launch.example/checker"
        assert output.product.product_url.startswith(value.product_url)
        if kind == "creative":
            assert output.product.tagline == "Check API contracts before merging changes."
            assert output.product.ad_image_status == "default"
            assert output.product.link_rel == ["nofollow"]
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    assert "receipt" not in output.model_dump_json()


@pytest.mark.parametrize(
    "setting,expected",
    [
        ({"ad_formats": ["feed", "feed"]}, "unique automatic"),
        ({"ad_formats": ["sponsored_content"]}, "requires a campaign"),
        ({"tier": "bronze", "promotion_kind": "ad"}, "not included"),
        ({"promotion_surfaces": ["feed", "feed"]}, "surfaces must be unique"),
    ],
)
def test_account_schema_rejects_impossible_or_duplicated_entitlements(setting, expected):
    with pytest.raises(ValidationError, match=expected):
        portal.AccountInput(**{"name": "Launch platform", "tier": "platinum", **setting})


def test_null_account_relationship_preference_cannot_silently_mean_default():
    with pytest.raises(ValidationError, match="must be a selection"):
        portal.PartnerPreferencesInput(link_rel=None, outbound_destination="listing_url")


def test_future_trusted_measurements_are_rejected():
    with pytest.raises(ValidationError, match="future"):
        portal.MetricInput(
            day=portal.datetime.now(portal.UTC).date() + timedelta(days=1), impressions=1, clicks=0
        )


@pytest.mark.parametrize("owned", [False, True])
def test_product_policy_needs_active_owned_inventory_even_without_an_existing_override(owned):
    owner, value, session = account(), product(), Mock(spec=Session)
    session.execute.return_value.first.return_value = (value,) if owned else None
    session.get.return_value = None
    if not owned:
        with pytest.raises(HTTPException) as missing:
            portal.product_promotion(session, owner, value.id)
        assert missing.value.status_code == 404
        session.get.assert_not_called()
    else:
        current = portal._product_promotion_policy(session, owner, value.id, lock=True)
        assert current.account_id == owner.id and current.product_id == value.id
        assert current.excluded is False and current.ad_image_revision == 0
        session.add.assert_not_called()


@pytest.mark.parametrize(
    "case", ["replace_image", "clear_image", "unchanged_image", "restrict_formats", "relationship"]
)
def test_promotion_edits_preserve_explicit_fields_and_revision_bound_image_work(monkeypatch, case):
    owner, value = account(), product()
    old_url = "https://creative.example/old.png"
    previous = policy(owner, value, ad_image_url=old_url, ad_image_revision=2)
    values = {
        "replace_image": {"ad_image_url": "https://creative.example/replacement.png"},
        "clear_image": {"ad_image_url": None},
        "unchanged_image": {"ad_image_url": old_url},
        "restrict_formats": {"ad_formats": [], "promotion_surfaces": []},
        "relationship": {"link_rel": ["sponsored", "ugc"]},
    }[case]
    saved = policy(
        owner,
        value,
        ad_image_url=old_url,
        ad_image_revision=2,
        **({k: v for k, v in values.items() if k not in {"ad_image_url"}}),
    )
    if "ad_image_url" in values:
        saved.ad_image_url = values["ad_image_url"]
        saved.ad_image_revision = 3 if case != "unchanged_image" else 2
    session = Mock(spec=Session)
    session.execute.return_value.first.return_value = (value,)
    session.get.side_effect = lambda model, _key: (
        previous if model is PartnerProductPromotion else value
    )
    session.scalar.side_effect = [value, saved, "queued"]
    dirty, image, enrollment = Mock(), Mock(), Mock()
    monkeypatch.setattr(portal, "mark_dirty", dirty)
    monkeypatch.setattr(portal, "request_product_ad_image", image)
    monkeypatch.setattr(portal, "ensure_automatic_assets", enrollment)
    output = portal.save_product_promotion(
        session, owner, value.id, portal.ProductPromotionInput(**values)
    )
    writes = [call.args[0] for call in session.execute.call_args_list if call.args[0].is_insert]
    requested = writes[0].compile().params
    updates = dict(writes[0]._post_values_clause.update_values_to_set)
    assert requested["account_id"] == owner.id and requested["product_id"] == value.id
    for name, expected in values.items():
        assert requested[name] == expected
    if "ad_image_url" in values:
        image.assert_called_once_with(session, owner.id, value.id)
        assert output.ad_image_url == values["ad_image_url"]
        if case != "unchanged_image":
            assert requested["ad_image_revision"] == 3 and requested["managed_ad_image"] == {}
        else:
            assert "ad_image_revision" not in updates
    else:
        image.assert_not_called()
    assert dirty.call_count == (1 if case == "relationship" else 0)
    enrollment.assert_called_once_with(session, owner.id, product_ids=[value.id])
    session.commit.assert_not_called()


@pytest.mark.parametrize("restriction", ["image_storage", "relationship_tier", "format_tier"])
def test_disallowed_policy_edits_fail_before_requested_write_or_enrollment(
    monkeypatch, restriction
):
    owner, value = account(tier="bronze"), product()
    previous = policy(owner, value)
    session = Mock(spec=Session)
    session.execute.return_value.first.return_value = (value,)
    session.get.return_value = previous
    payload = (
        {"ad_image_url": "https://creative.example/ad.png"}
        if restriction == "image_storage"
        else {"link_rel": ["ugc"]}
        if restriction == "relationship_tier"
        else {"ad_formats": ["engagement"]}
    )
    monkeypatch.setattr(portal, "get_settings", lambda: configuration(image_storage_enabled=False))
    enrollment = Mock()
    monkeypatch.setattr(portal, "ensure_automatic_assets", enrollment)
    with pytest.raises(HTTPException) as rejected:
        portal.save_product_promotion(
            session, owner, value.id, portal.ProductPromotionInput(**payload)
        )
    assert rejected.value.status_code == 422
    assert not any(call.args[0].is_insert for call in session.execute.call_args_list)
    enrollment.assert_not_called()


@pytest.mark.parametrize("status", ["default", "queued", "running", "failed"])
def test_grouped_policy_page_presents_only_current_revision_image_state(status):
    owner, value = account(), product()
    current = policy(
        owner,
        value,
        ad_image_url=None if status == "default" else "https://creative.example/ad.png",
        ad_image_revision=1,
    )
    session = Mock(spec=Session)
    session.execute.return_value.all.return_value = [
        (current, value, owner, "succeeded" if status == "failed" else status)
    ]
    [output] = portal.list_product_promotions(session, owner.id)
    assert output.ad_image_status == status and output.product_id == value.id
    session.scalar.assert_not_called()


@pytest.mark.parametrize("measured", [False, True])
def test_reporting_composes_complete_aggregates_and_distinguishes_unmeasured_zero(measured):
    owner, value, session = account(), product(), Mock(spec=Session)
    asset_id = uuid.uuid4()
    report_day = portal.datetime.now(portal.UTC).date()
    rows = [
        {
            "id": asset_id,
            "account_id": owner.id,
            "account_name": owner.name,
            "product_id": value.id,
            "name": value.name,
            "kind": "ad",
            "status": "active",
            "automatic": True,
            "aggregated": True,
            "ad_format": "feed",
            "ad_formats": ["feed", "engagement"],
            "campaign_id": None,
            "article_id": None,
            "impressions": 200 if measured else None,
            "clicks": 5 if measured else None,
            "measured_days": 1 if measured else None,
        }
    ]
    session.execute.side_effect = [
        NS(one=lambda: (200, 5, 1, NOW) if measured else (None, None, 0, None)),
        result([("feed", 120, 3, 1), ("engagement", 80, 2, 1)] if measured else []),
        NS(
            mappings=lambda: result(
                [{"day": report_day, "impressions": 200, "clicks": 5}] if measured else []
            )
        ),
        NS(mappings=lambda: result(rows)),
    ]
    session.scalar.return_value = 1
    output = portal.reporting_dashboard(
        owner.id,
        session,
        owner,
        days=7,
        limit=1,
        offset=0,
        sort="ctr",
        order="desc",
        ad_format=None,
    )
    assert output["account"] is owner and output["asset_total"] == 1
    assert output["end"] - output["start"] == timedelta(days=6)
    assert output["totals"] == {
        "impressions": 200 if measured else 0,
        "clicks": 5 if measured else 0,
        "ctr": 2.5 if measured else None,
        "measured_days": int(measured),
    }
    assert output["assets"][0]["ctr"] == (2.5 if measured else None)
    assert output["assets"][0]["ad_formats"] == ["feed", "engagement"]
    assert len(output["format_totals"]) == (2 if measured else 0)
    session.add.assert_not_called()
