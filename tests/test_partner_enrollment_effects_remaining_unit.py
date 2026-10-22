"""Enrollment composes accepted commercial receipts and repository insertion results.

The repository supplies accepted/inserted identities, not a simulated conflict or
eligibility evaluator. These units prove follow-up effects and returned counts;
PostgreSQL owns uniqueness, qualification selection and advisory-lock semantics.
"""

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from devfeed_core import logos, partner_product_images
from devfeed_core import partner_promotions as promotions
from sqlalchemy.orm import Session
from test_partner_matching_workflow_unit import NOW, article, product
from test_partner_placement_workflow_unit import account, campaign


@pytest.mark.parametrize("new_acceptance", [False, True])
@pytest.mark.parametrize("inserted", ["all", "organic", "none"])
def test_trusted_enrollment_schedules_only_new_acceptance_and_dirties_only_new_sponsorship(
    monkeypatch, new_acceptance, inserted
):
    owner = account(approval_mode="trusted", ad_formats=["feed", "sponsored_content"])
    values = [
        product(
            id=uuid.UUID(int=index),
            name=f"API checker {index}",
            product_url=f"https://checker{index}.example/",
            status="pending",
            verified_at=None,
        )
        for index in (1, 2)
    ]
    story = article()
    agreement = campaign(owner, ad_format="sponsored_content", article_id=story.id)
    generated = [uuid.UUID(int=index) for index in range(101, 105)]
    sequence = iter(generated)
    # Limit deterministic entropy to this module; UUID generation elsewhere remains real.
    monkeypatch.setattr(promotions, "uuid", SimpleNamespace(uuid4=lambda: next(sequence)))
    session = Mock(spec=Session)
    acceptance = Mock()
    acceptance.scalars.return_value.all.return_value = [values[0].id] if new_acceptance else []
    exclusions = Mock()
    exclusions.all.return_value = []
    asset_insert = Mock()
    returned = {"all": generated, "organic": generated[::2], "none": []}[inserted]
    asset_insert.all.return_value = [(identifier,) for identifier in returned]
    session.execute.side_effect = [acceptance, exclusions, asset_insert]
    session.scalars.return_value = [agreement]
    managed = Mock()
    monkeypatch.setattr(promotions, "ensure_managed_campaign", managed)
    logo, image, dirty = Mock(), Mock(), Mock()
    monkeypatch.setattr(logos, "request_logo", logo)
    monkeypatch.setattr(partner_product_images, "request_product_image", image)
    monkeypatch.setattr(promotions, "mark_dirty", dirty)

    created = promotions._enroll_automatic_rows(session, [(owner, value) for value in values], NOW)

    assert created == len(returned)
    assert [value.status for value in values] == ["pending", "pending"]
    assert all(value.verified_at is None for value in values)
    managed.assert_has_calls([call(session, owner, NOW, kind) for kind in owner.ad_formats])
    if new_acceptance:
        logo.assert_called_once_with(session, type(values[0]), values[0].id, automatic=True)
        image.assert_called_once_with(session, values[0].id, automatic=True)
    else:
        logo.assert_not_called()
        image.assert_not_called()
    if inserted == "all":
        dirty.assert_called_once_with(session, "partner_assets")
    else:
        dirty.assert_not_called()
    session.commit.assert_not_called()


def test_empty_enrollment_has_no_campaign_or_media_side_effects():
    session = Mock(spec=Session)
    assert promotions._enroll_automatic_rows(session, [], NOW) == 0
    assert session.mock_calls == []


@pytest.mark.parametrize("eligible", [False, True])
def test_catalog_repair_returns_enrollment_result_only_for_the_supplied_owned_rows(
    monkeypatch, eligible
):
    owner, value, session = account(), product(), Mock(spec=Session)
    rows = [(owner, value)] if eligible else []
    session.execute.return_value.all.return_value = rows
    enroll = Mock(return_value=2)
    monkeypatch.setattr(promotions, "_enroll_automatic_rows", enroll)
    assert promotions.ensure_automatic_assets(session, owner.id, NOW, [value.id]) == (
        2 if eligible else 0
    )
    if eligible:
        enroll.assert_called_once_with(session, rows, NOW)
    else:
        enroll.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("admitted", [False, True])
def test_bounded_repair_keeps_completed_enrollment_count_when_the_periodic_lane_has_no_more_work(
    monkeypatch, admitted
):
    session, factory = Mock(spec=Session), Mock()
    factory.begin.return_value = nullcontext(session)
    session.scalar.return_value = admitted
    session.execute.return_value.all.return_value = []
    pending = Mock(return_value=3)
    monkeypatch.setattr(promotions, "process_promotion_enrollment", pending)
    assert promotions.sync_partner_promotions(factory, limit=25, now=NOW) == 3
    pending.assert_called_once_with(factory, limit=25, now=NOW)
    if admitted:
        session.execute.assert_called_once()
    else:
        session.execute.assert_not_called()
