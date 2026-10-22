"""Advisory batches preserve delivery policy and reject stale evaluation facts.

These tests supply scoped reads. They do not prove SQL eligibility, capacity
atomicity, catalog locking or visibility across real database transactions.
"""

from datetime import timedelta
from unittest.mock import Mock

from devfeed_admin_api import partner_tools as admin_tools
from devfeed_core import partner_catalog, partner_tools
from devfeed_core.partner_placement_destinations import PlacementDestinations
from devfeed_core.partner_placement_preflight import (
    PlacementPreflight,
    automatic_configuration_available,
)
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy.orm import Session
from test_admin_partner_read_contracts_unit import evaluation
from test_partner_matching_workflow_unit import NOW, article, product
from test_partner_placement_workflow_unit import account, asset, campaign


def test_ordinary_product_recommendations_do_not_fill_header_sponsorship_inventory():
    owner, value = account(), product()
    creative = asset(owner, value)
    assert automatic_configuration_available(creative, owner, None, NOW, surface="feed")
    assert not automatic_configuration_available(creative, owner, None, NOW, surface="header")


def test_unrestricted_ordinary_product_passes_preflight_without_campaign_or_reader_queries():
    owner, value, session = account(), product(), Mock(spec=Session)
    row = (asset(owner, value), owner, value, None)
    preflight = PlacementPreflight(session, PlacementQuery(surface="feed"), NOW, None)
    assert preflight.eligible_rows([row]) == [row]
    assert not preflight.destinations.unavailable(owner.id, value.id)
    assert session.mock_calls == []


def test_final_campaign_rejection_is_remembered_across_later_candidate_batches():
    owner, value, session = account(), product(), Mock(spec=Session)
    agreement = campaign(owner)
    row = (asset(owner, value, agreement), owner, value, agreement)
    preflight = PlacementPreflight(session, PlacementQuery(surface="feed"), NOW, None)
    assert preflight.eligible_rows([row]) == [row]
    # The caller reports a failed final locked check; no DB failure is simulated.
    preflight.reject_campaign(agreement)
    assert preflight.campaign_exhausted(agreement)
    assert preflight.eligible_rows([row]) == []
    assert session.mock_calls == []


def test_public_http_product_facts_are_not_an_https_delivery_destination():
    owner, session = account(), Mock(spec=Session)
    insecure, secure = product(product_url="http://checker.example/"), product()
    # Import validation accepts public HTTP; delivery requires HTTPS independently.
    assert partner_tools.ProductFacts.model_validate(insecure, from_attributes=True).product_url
    rejected = (asset(owner, insecure), owner, insecure, None)
    retained = (asset(owner, secure), owner, secure, None)
    destinations = PlacementDestinations(session)
    assert destinations.eligible_rows([rejected, retained]) == [retained]
    assert destinations.unavailable(owner.id, insecure.id)
    assert not destinations.unavailable(owner.id, secure.id)
    assert destinations.eligible_rows([rejected]) == []
    assert session.mock_calls == []


def test_completed_evaluation_expires_when_its_developer_qualification_expires(monkeypatch):
    value, session = product(), Mock(spec=Session)
    story = article()
    prior = evaluation(value, story)
    session.get.return_value = value
    session.execute.return_value.all.return_value = [story]
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW + partner_tools.QUALIFICATION_TTL)
    source_read = Mock(return_value=True)
    monkeypatch.setattr(partner_catalog, "product_has_active_source", source_read)
    output = admin_tools.evaluation_view(session, prior)
    assert not output["current"] and output["status"] == "succeeded"
    assert output["result"] == prior.result
    source_read.assert_not_called()
    session.execute.assert_not_called()
    assert prior.finished_at < NOW


def test_requalified_new_product_revision_does_not_reuse_old_evaluation(monkeypatch):
    value, session = product(), Mock(spec=Session)
    story = article()
    prior = evaluation(value, story)
    # A later catalog edit and completed assessment qualify revision 3, while the
    # succeeded shadow evaluation remains bound to revision 2's original facts.
    value.name, value.revision = "API Checker Pro", 3
    value.updated_at = NOW - timedelta(seconds=30)
    value.assessment_revision, value.verified_at = 3, NOW - timedelta(seconds=10)
    session.get.return_value = value
    session.execute.return_value.all.return_value = [story]
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    source_read = Mock(return_value=True)
    monkeypatch.setattr(partner_catalog, "product_has_active_source", source_read)
    assert partner_tools.eligible(value)
    assert not partner_tools.snapshot_current(session, prior)
    assert prior.snapshot["product"]["revision"] == 2
    source_read.assert_not_called()
    session.execute.assert_not_called()


def test_unlocked_evaluation_read_rechecks_provenance_after_loading_qualified_product(monkeypatch):
    value, session = product(), Mock(spec=Session)
    prior = evaluation(value, article())
    session.get.return_value = value
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    # The admin read has no catalog lock: its already-loaded product can predate
    # a concurrent completed last-source withdrawal. The next service read is
    # fresh and says no active provenance. This is not a persisted orphan fixture.
    source_read = Mock(return_value=False)
    monkeypatch.setattr(partner_catalog, "product_has_active_source", source_read)
    output = admin_tools.evaluation_view(session, prior)
    assert output["current"] is False and output["result"] == prior.result
    source_read.assert_called_once_with(session, value.id)
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_empty_manual_provenance_batch_does_not_scan_partner_inventory():
    session = Mock(spec=Session)
    assert partner_tools.manual_product_views(session, []) == {}
    assert session.mock_calls == []
