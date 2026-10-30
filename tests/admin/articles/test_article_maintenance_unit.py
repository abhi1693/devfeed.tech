"""Private operator previews expose bounded review decisions and selected retries."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import article_maintenance as api
from pydantic import ValidationError
from sqlalchemy.orm import Session

from tests.support.article_maintenance_unit import NOW
from tests.support.article_maintenance_unit import prepared as prepared


@pytest.fixture
def preview(prepared, monkeypatch):
    public, owner, inputs, _ = prepared
    owner.created_at = NOW
    session = Mock(spec=Session)
    session.scalar.return_value = owner
    facts = inputs.facts
    monkeypatch.setattr(api, "refresh_facts", lambda *_args, **_kwargs: facts)
    monkeypatch.setattr(api, "candidate_view", lambda *_args: inputs.article)
    return SimpleNamespace(public=public, owner=owner, inputs=inputs, session=session, facts=facts)


def test_never_published_detail_has_no_private_owner_lookup(preview):
    preview.public.publication_status = "unpublished"
    preview.public.published_to_feed_at = None
    assert api.maintenance_detail(preview.session, preview.public) is None
    preview.session.scalar.assert_not_called()


def test_no_active_owner_returns_an_empty_preview(preview):
    preview.session.scalar.return_value = None
    assert api.maintenance_detail(preview.session, preview.public) is None
    sql = str(preview.session.scalar.call_args.args[0])
    assert "article_refreshes.article_id" in sql and "article_refreshes.status IN" in sql


@pytest.mark.parametrize("state", ["active", "review", "promoted", "cancelled", "superseded"])
def test_preview_names_classifications_and_keeps_public_and_private_projections_separate(
    preview, state
):
    preview.owner.status = state
    original = deepcopy(preview.public.classification_provenance)
    result = api.maintenance_detail(preview.session, preview.public, preview.owner)
    assert result.replacement.title == "Trace Angular navigation"
    assert result.replacement.topics[0].name == "Angular"
    assert result.ready == (state == "review")
    assert result.allowed_actions == (
        ["approve", "reject"] if state == "review" else ["reject"] if state == "active" else []
    )
    assert result.retry_targets == []
    assert preview.public.classification_provenance == original
    assert preview.public.publication_status == "published"
    assert "input_snapshot" not in result.model_dump_json()


@pytest.mark.parametrize("authority", ["unpublished", "rejected", "revision", "source"])
def test_lost_authority_disables_private_approval_with_an_actionable_reason(preview, authority):
    preview.owner.status = "review"
    if authority == "unpublished":
        preview.public.publication_status = "unpublished"
    elif authority == "rejected":
        preview.public.review_status = "rejected"
    elif authority == "revision":
        preview.public.editorial_revision += 1
    else:
        preview.public.origins[0].source.approval_status = "rejected"
    result = api.maintenance_detail(preview.session, preview.public, preview.owner)
    assert not result.ready and result.allowed_actions == ["reject"]
    assert "Discard this replacement" in " ".join(result.reasons)


@pytest.mark.parametrize("target", ["enrichment", "analysis", "images"])
@pytest.mark.parametrize("status", ["queued", "running", "failed"])
def test_pending_and_failed_tasks_cannot_offer_approval_or_expose_saved_errors(
    preview, target, status
):
    preview.owner.target, preview.owner.status = target, "review"
    job = preview.facts["analysis" if target == "images" else target]
    job.status, job.error = status, "private provider token in failure details"
    preview.session.scalar.return_value = job.id
    result = api.maintenance_detail(preview.session, preview.public, preview.owner)
    assert not result.ready and result.allowed_actions == ["reject"]
    assert any(status in reason for reason in result.reasons)
    if target == "analysis" and status == "failed":
        assert result.retry_targets == ["analysis"]
    assert "private provider token" not in result.model_dump_json()


@pytest.mark.parametrize("outcome", ["insufficient_evidence", "superseded", "published_preserved"])
def test_completed_unusable_analysis_still_offers_its_selected_retry(preview, outcome):
    preview.facts["analysis"].outcome = outcome
    result = api.maintenance_detail(preview.session, preview.public, preview.owner)
    assert result.retry_targets == ["analysis"]
    assert not result.ready
    if outcome == "insufficient_evidence":
        assert "more source evidence" in " ".join(result.reasons)


def test_restricted_publisher_access_explains_why_completed_work_is_not_ready(preview):
    preview.facts["enrichment"].result = {"paywalled": True, "private": "provider secret"}
    result = api.maintenance_detail(preview.session, preview.public, preview.owner)
    assert not result.ready
    assert "Publisher access is restricted" in " ".join(result.reasons)
    assert "provider secret" not in result.model_dump_json()


def test_private_preview_bounds_prose_without_returning_source_body(preview):
    preview.inputs.article.ai_summary = "a" * 9000
    result = api.maintenance_detail(preview.session, preview.public, preview.owner)
    assert len(result.replacement.summary) == 6000
    assert not {"body", "content", "analysis", "catalog_snapshot"}.intersection(
        result.model_dump()["replacement"]
    )


@pytest.mark.parametrize(
    "body",
    [
        {"expected_version": 0, "action": "approve"},
        {"expected_version": 1, "action": "publish"},
        {"expected_version": 1, "action": "reject"},
        {"expected_version": 1, "action": "reject", "note": " "},
        {"expected_version": 1, "action": "approve", "note": "x" * 1001},
        {"expected_version": 1, "action": "approve", "publication_status": "published"},
    ],
)
def test_review_request_requires_a_version_explicit_action_and_bounded_discard_reason(body):
    with pytest.raises(ValidationError):
        api.ReviewMaintenance.model_validate(body)


def test_detail_closes_its_read_session_after_building_preview(preview, monkeypatch):
    result = object()
    monkeypatch.setattr(api, "record", lambda *_args: preview.public)
    monkeypatch.setattr(api, "maintenance_detail", lambda *_args: result)
    assert api.detail(preview.public.id, preview.session) is result
    preview.session.close.assert_called_once()


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_review_forwards_authenticated_actor_then_commits_before_reading_response(
    preview, monkeypatch, action
):
    events = []
    admin = SimpleNamespace(
        subject="editor", issuer="trusted", organization_id="org", name=None, email=None
    )
    command = Mock(return_value=preview.owner)
    monkeypatch.setattr(api, "review_refresh", command)
    monkeypatch.setattr(api, "record", lambda *_args: preview.public)
    monkeypatch.setattr(
        api, "maintenance_detail", lambda *_args: events.append("read") or preview.owner
    )
    preview.session.commit.side_effect = lambda: events.append("commit")
    body = api.ReviewMaintenance(expected_version=3, action=action, note="Reviewed source")
    assert (
        api.review(preview.public.id, preview.owner.id, body, preview.session, admin)
        is preview.owner
    )
    assert events == ["commit", "read"]
    command.assert_called_once_with(
        preview.session,
        preview.public.id,
        preview.owner.id,
        3,
        action=action,
        actor={"subject": "editor", "issuer": "trusted", "organization_id": "org"},
        note="Reviewed source",
    )
    preview.session.close.assert_called_once()
