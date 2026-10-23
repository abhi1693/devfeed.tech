"""Partner operator pages compose bounded reads and keep private evidence in detail.

Page and aggregate rows are preselected repository facts. These units do not prove
SQL filters, catalog membership, sorting, uniqueness, locks or transactions.
"""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import partner_accounts as accounts
from devfeed_admin_api import partner_jobs as jobs
from devfeed_admin_api import partner_tools as tools
from devfeed_core import partner_tools
from devfeed_core.models import PartnerEvaluation, PartnerMembership, UserAccount
from devfeed_core.partner_tools import article_snapshot, product_snapshot, validate_result
from devfeed_http.pagination import ListQuery
from fastapi import HTTPException
from sqlalchemy.orm import Session

from tests.partners.connections.test_connection_workflow_unit import connection, work
from tests.support.admin_editorial import identity
from tests.support.partner_accounts import account
from tests.support.partner_matching import NOW, article, product, result


def evaluation(value, story, **changes):
    snapshot = {
        "version": "1",
        "product": product_snapshot(value),
        "articles": [article_snapshot(story)],
    }
    decision = {
        "article_id": str(story.id),
        "relevant": True,
        "reason": "Checks the same incompatible OpenAPI changes in this tutorial.",
        "article_quote": story.summary,
        "evidence_index": 0,
        "technology": "OpenAPI",
    }
    return PartnerEvaluation(
        **{
            "id": uuid.uuid4(),
            "product_id": value.id,
            "status": "succeeded",
            "attempts": 1,
            "created_at": NOW - timedelta(minutes=2),
            "available_at": NOW - timedelta(minutes=2),
            "finished_at": NOW - timedelta(minutes=1),
            "error": None,
            "reviews": [],
            "snapshot": snapshot,
            "result": validate_result(snapshot, {"decisions": [decision]}),
            "requested_by": tools.actor(identity()),
            **changes,
        }
    )


def page(items):
    return {"items": items, "total": len(items), "limit": 1, "offset": 0}


@pytest.mark.parametrize("empty", [False, True])
def test_pipeline_summary_batches_product_names_and_preserves_effective_running_state(
    monkeypatch, empty
):
    saved, session = connection(), Mock(spec=Session)
    queued = work(saved)
    supplied = page([] if empty else [queued])
    monkeypatch.setattr(jobs, "paginate", Mock(return_value=supplied))
    session.execute.return_value.all.return_value = []
    output = jobs.pipeline(
        session,
        ListQuery(q="nick", limit=1, offset=0),
        status="running",
        operation="sync",
        provider=saved.provider,
    )
    assert output["total"] == len(output["items"]) == int(not empty)
    if not empty:
        [row] = output["items"]
        validated = jobs.PartnerPipelineOut.model_validate(row)
        assert validated.status == "running" and validated.product_name is None
        assert validated.available_at == queued.available_at
        assert set(row) == {*tools.PartnerJobOut.model_fields, "available_at", "product_name"}
        assert "payload" not in row and "connector" not in row
    else:
        session.execute.assert_not_called()


def test_product_child_history_keeps_parent_scope_and_resolves_canonical_product_name(monkeypatch):
    saved, value, session = connection(), product(), Mock(spec=Session)
    parent = work(saved, status="running")
    completed = work(
        saved,
        operation="sync_product",
        status="succeeded",
        parent_id=parent.id,
        product_id=value.id,
        external_id="checker",
    )
    completed.attempts, completed.finished_at = 1, NOW - timedelta(minutes=1)
    monkeypatch.setattr(jobs, "paginate", Mock(return_value=page([completed])))
    session.execute.return_value.all.return_value = [(value.id, value.name)]
    output = jobs.pipeline(
        session,
        ListQuery(q="checker", limit=1, offset=0),
        status="succeeded",
        operation="sync_product",
        provider=saved.provider,
        product_id=value.id,
        parent_id=parent.id,
    )
    [row] = output["items"]
    assert row["parent_id"] == parent.id and row["product_name"] == value.name
    assert row["status"] == "succeeded" and row["finished_at"] == completed.finished_at


def test_pipeline_detail_uses_same_public_operator_projection():
    value, session = product(), Mock(spec=Session)
    queued = work(connection(), operation="assess", product_id=value.id)
    session.scalar.return_value = queued
    session.execute.return_value.all.return_value = [(value.id, value.name)]
    output = jobs.pipeline_detail(queued.id, session)
    assert output["id"] == queued.id and output["product_name"] == value.name
    assert output["status"] == "queued" and "payload" not in output


@pytest.mark.parametrize("empty", [False, True])
def test_evaluation_summary_counts_decisions_and_omits_private_snapshots(monkeypatch, empty):
    value, story, session = product(), article(), Mock(spec=Session)
    completed = evaluation(value, story)
    supplied = page([] if empty else [completed])
    monkeypatch.setattr(jobs, "paginate", Mock(return_value=supplied))
    session.execute.return_value.all.return_value = [(completed.id, value.name)]
    output = jobs.evaluations(
        session,
        ListQuery(q="checker", limit=1, offset=0),
        status="succeeded",
        product_id=value.id,
        provider="nick-launches",
    )
    assert output["total"] == int(not empty)
    if not empty:
        [row] = output["items"]
        checked = jobs.PartnerEvaluationSummary.model_validate(row)
        assert checked.matches == checked.articles == 1 and checked.product_name == value.name
        assert set(row) == set(jobs.PartnerEvaluationSummary.model_fields)
    else:
        assert output["items"] == []
        session.execute.assert_not_called()


@pytest.mark.parametrize("changed", [False, True])
def test_evaluation_detail_preserves_evidence_and_identifies_changed_current_inputs(
    monkeypatch, changed
):
    value, story, session = product(), article(), Mock(spec=Session)
    completed = evaluation(value, story)
    original = copy.deepcopy(completed.snapshot)
    if changed:
        story.summary += " A later publisher correction."
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    session.scalar.side_effect = [completed, value.id]
    session.get.return_value = value
    session.execute.return_value.all.return_value = [story]
    output = jobs.PartnerEvaluationDetail.model_validate(
        jobs.evaluation_detail(completed.id, session)
    )
    assert output.current is (not changed)
    assert output.snapshot.model_dump(mode="json") == original
    assert output.result.decisions[0].relevant and output.attempts == 1
    session.commit.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_product_evaluation_history_requires_catalog_identity_and_keeps_currentness(
    monkeypatch, missing
):
    value, story, session = product(), article(), Mock(spec=Session)
    completed = evaluation(value, story)
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    session.scalar.side_effect = [value, None if missing else value.id, value.id]
    session.scalars.return_value = result([completed])
    session.get.return_value = value
    session.execute.return_value.all.return_value = [story]
    if missing:
        with pytest.raises(HTTPException) as denied:
            tools.evaluations(value.id, session)
        assert denied.value.status_code == 404
        session.scalars.assert_not_called()
    else:
        [row] = tools.evaluations(value.id, session)
        assert row["current"] is True and row["snapshot"] == completed.snapshot
        assert row["result"] == completed.result


def test_catalog_page_combines_batched_provenance_without_granting_eligibility(monkeypatch):
    value, session = product(), Mock(spec=Session)
    listing_read, manual_read = Mock(return_value={value.id: []}), Mock(return_value={value.id: []})
    monkeypatch.setattr(tools, "paginate", Mock(return_value=page([value])))
    monkeypatch.setattr(tools, "listing_views", listing_read)
    monkeypatch.setattr(tools, "manual_product_views", manual_read)
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    output = tools.listing(
        session,
        ListQuery(q="checker", limit=1, offset=0),
        provider="nick-launches",
        product_id=value.id,
        status="approved",
    )
    [row] = output["items"]
    assert row["status"] == "approved" and not row["eligible"]
    assert row["listings"] == row["manual_sources"] == []
    listing_read.assert_called_once_with(session, [value.id])
    manual_read.assert_called_once_with(session, [value.id])


@pytest.mark.parametrize("archived", [False, True])
def test_connection_job_page_preserves_supplied_scope_and_rejects_archived_parent(
    monkeypatch, archived
):
    saved, session = connection(archived_at=NOW if archived else None), Mock(spec=Session)
    session.get.return_value = saved
    queued = work(saved)
    scoped_page = page([queued])
    paginate = Mock(return_value=scoped_page)
    monkeypatch.setattr(tools, "paginate", paginate)
    if archived:
        with pytest.raises(HTTPException) as denied:
            tools.connection_jobs(
                saved.provider, session, ListQuery(q="checker", limit=1, offset=0)
            )
        assert denied.value.status_code == 404
        paginate.assert_not_called()
    else:
        assert (
            tools.connection_jobs(
                saved.provider, session, ListQuery(q="checker", limit=1, offset=0)
            )
            is scoped_page
        )


@pytest.mark.parametrize("user_present", [False, True])
def test_member_inspection_retains_immutable_membership_when_local_account_is_absent(user_present):
    owner, session, admin = account(), Mock(spec=Session), identity()
    membership = PartnerMembership(
        account_id=owner.id,
        issuer=admin.issuer,
        subject="reader-id",
        created_at=NOW - timedelta(days=1),
    )
    user = UserAccount(
        id=uuid.uuid4(),
        issuer=admin.issuer,
        subject=membership.subject,
        organization_id=admin.organization_id,
        name="Reader",
        email="reader@example.com",
    )
    session.scalar.return_value = owner
    session.execute.return_value.all.return_value = [(membership, user if user_present else None)]
    [row] = accounts.members(owner.id, admin, session)
    assert row.issuer == membership.issuer and row.subject == membership.subject
    assert row.user_id == (user.id if user_present else None)
    assert row.email == (user.email if user_present else None)


@pytest.mark.parametrize("query", ["", "Launch"])
def test_account_directory_returns_bounded_page_and_global_count(query):
    owner, session = account(), Mock(spec=Session)
    session.scalars.return_value = result([owner])
    session.scalar.return_value = 1
    output = accounts.accounts(session, identity(), limit=1, offset=0, q=query)
    assert output == {"items": [owner], "total": 1}
