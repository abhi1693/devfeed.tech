"""Selection preserves grounded evidence and fails closed at service boundaries.

Unavailable locked-read and later visible-row results are repository outcomes,
not an implementation or proof of database locking and contention.
"""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import partner_matching, partner_placements, partner_promotions
from devfeed_core.partner_tools import ProductFacts
from devfeed_core.partner_tracking import BusyPartnerAsset
from devfeed_http import partner_connection_logos, partner_portal
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session
from test_partner_catalog_commands_unit import policy
from test_partner_connection_workflow_unit import connection
from test_partner_matching_workflow_unit import NOW, article, product
from test_partner_placement_workflow_unit import account
from test_source_readiness_unit import configuration


def test_weaker_later_evidence_cannot_replace_a_stronger_grounded_task_match():
    value, story = product(), article()
    strong = copy.deepcopy(value.evidence[0])
    weak = {
        "url": "https://checker.example/docs/basic",
        "quote": "OpenAPI detects changes before release.",
        "capability": "Detect changes before release.",
    }
    ProductFacts.model_validate(value, from_attributes=True)
    best = partner_matching.article_match(value, story)
    value.evidence = [weak]
    weaker = partner_matching.article_match(value, story)
    assert best[0] and weaker[0] and best[1] > weaker[1]
    value.evidence = [strong, weak]
    assert partner_matching.article_match(value, story) == best


def test_trusted_creative_preview_skips_unusable_http_destination_before_valid_inventory():
    owner, session = account(approval_mode="trusted"), Mock(spec=Session)
    insecure, secure = product(product_url="http://checker.example/"), product()
    # These already-scoped rows are independently qualified inventory, which is
    # also allowed on trusted accounts. No SQL acceptance/filtering is simulated.
    session.scalars.return_value = [insecure, secure]
    selected, destination = partner_portal._preview_product(session, owner)
    assert selected is secure
    assert destination == "https://checker.example/?utm_source=devfeed"
    session.commit.assert_not_called()


@pytest.mark.parametrize("ad_format,article_id", [("feed", True), ("sponsored_content", False)])
def test_campaign_article_identity_is_required_only_for_sponsored_content(ad_format, article_id):
    with pytest.raises(ValidationError, match="Supply an article only for sponsored content"):
        partner_portal.CampaignInput(
            name="Developer campaign",
            ad_format=ad_format,
            article_id=uuid.uuid4() if article_id else None,
            starts_at=NOW,
            ends_at=NOW + timedelta(days=1),
        )


def test_unavailable_trusted_acceptance_reports_retryable_policy_contention():
    owner, session = account(approval_mode="trusted"), Mock(spec=Session)
    value = product(status="pending", verified_at=None, assessment={}, assessment_revision=None)
    saved = policy(
        owner, value, approval_source="trusted", commercial_approved_at=NOW - timedelta(minutes=1)
    )
    # The repository could not return a protected receipt, but confirms it
    # exists. This test checks the command's response, not why the read skipped.
    session.scalar.side_effect = [None, saved]
    with pytest.raises(BusyPartnerAsset, match="commercial acceptance is being updated"):
        partner_promotions.commercially_eligible(session, owner, value, NOW, busy_error=True)
    assert value.status == "pending" and value.verified_at is None
    assert saved.approval_source == "trusted"
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_unavailable_product_policy_is_ineligible_for_best_effort_placement_reads():
    owner, value, session = account(), product(), Mock(spec=Session)
    # Ownership was supplied, while a protected policy read was unavailable and
    # the following unprotected read confirms a row. None must not mean opt-in.
    session.scalar.side_effect = [uuid.uuid4(), None, False]
    assert not partner_placements.account_product_available(
        session, owner.id, value.id, busy_error=False, ad_format="feed", surface="feed"
    )
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_logo_preparation_with_disabled_managed_storage_is_unavailable_before_fetch(monkeypatch):
    settings = configuration(image_storage_enabled=False, image_public_url="https://images.example")
    monkeypatch.setattr(partner_connection_logos, "get_settings", lambda: settings)
    fetch = Mock()
    monkeypatch.setattr(partner_connection_logos, "_fetch", fetch)
    saved = connection()
    edit = partner_connection_logos.ConnectionLogoEdit(
        saved, source_url="https://imports.example/logo.png"
    )
    assert edit.changed
    with pytest.raises(HTTPException) as unavailable:
        edit.prepare()
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail == "Public HTTPS image storage is required for logos"
    assert saved.logo_url is None and saved.managed_logo == {}
    fetch.assert_not_called()
