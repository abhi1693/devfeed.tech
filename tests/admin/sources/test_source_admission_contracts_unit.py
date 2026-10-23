"""Source admission keeps network validation outside reads and preserves review ownership.

Validation/persistence services and preselected repository results are boundaries.
Scope entry/exit is observed without emulating SQL filtering, locks or transactions.
"""

import uuid
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import sources
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.feeds.validation import FeedValidationError
from devfeed_core.models import Source, SourceReview
from devfeed_core.services import ValidatedSource
from devfeed_core.source_preview import SourcePreview
from devfeed_core.source_profiles import SourceProfile
from sqlalchemy.exc import IntegrityError

from tests.support.admin_editorial import source_policy as source_policy
from tests.support.source_readiness import NOW


@pytest.fixture
def admission(source_policy):
    api = source_policy
    api.phases = []

    @contextmanager
    def scope():
        api.phases.append("enter")
        try:
            yield api.session
        finally:
            api.phases.append("exit")

    api.session.begin.side_effect = scope
    api.session.scalar.return_value = None
    return api


@pytest.mark.parametrize("solver", [False, True])
def test_preview_preserves_validated_profile_and_warnings_without_creating_source_or_ingestion(
    admission, monkeypatch, solver
):
    api = admission
    result = SourcePreview(
        name="Engineering publisher",
        profile=SourceProfile(
            description="Engineering systems",
            website_url="https://publisher.example/",
            language="en",
        ),
        entries_seen=12,
        entries_skipped=2,
        warnings=("Website details incomplete",),
        final_url="https://publisher.example/final.xml",
    )

    def preview(url, kind, **options):
        assert api.phases == ["enter", "exit"]
        assert url == api.source.feed_url and kind == "publisher"
        assert options == ({"solver": sources.solve_feed} if solver else {})
        return result

    monkeypatch.setattr(sources, "preview_source", preview)
    response = api.client.post(
        "/v1/admin/sources/preview",
        json={
            "feed_url": api.source.feed_url,
            "source_type": "publisher",
            "source_id": str(api.source.id),
            "use_solver": solver,
        },
    )
    assert response.status_code == 200, response.text
    output = response.json()
    assert output["description"] == "Engineering systems" and output["language"] == "en"
    assert (output["entries_seen"], output["entries_skipped"], output["warnings"]) == (
        12,
        2,
        ["Website details incomplete"],
    )
    assert api.phases == ["enter", "exit", "enter", "exit"]
    api.session.add.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "change", [{"feed_url": "https://another.example/feed"}, {"source_type": "aggregator"}]
)
def test_preview_of_existing_source_cannot_change_its_bound_feed_or_type(
    admission, monkeypatch, change
):
    preview = Mock()
    monkeypatch.setattr(sources, "preview_source", preview)
    response = admission.client.post(
        "/v1/admin/sources/preview",
        json={
            "feed_url": admission.source.feed_url,
            "source_type": "publisher",
            "source_id": str(admission.source.id),
            **change,
        },
    )
    assert (
        response.status_code == 422
        and response.json()["detail"] == "Use the existing source feed URL and type."
    )
    preview.assert_not_called()
    admission.session.add.assert_not_called()


@pytest.mark.parametrize("operation", ["preview", "create"])
@pytest.mark.parametrize("reason", ["browser_challenge", "solver_unavailable", "http_404"])
def test_validation_failures_retain_solver_recovery_codes_without_writing(
    admission, monkeypatch, operation, reason
):
    failure = FeedValidationError(
        FeedError(
            "Publisher validation failed",
            reason=reason,
            status=404 if reason == "http_404" else 503,
            retryable=reason != "http_404",
        )
    )
    validation = Mock(side_effect=failure)
    if operation == "preview":
        monkeypatch.setattr(sources, "preview_source", validation)
    else:
        monkeypatch.setattr(sources.services, "validate_source", validation)
    response = admission.client.post(
        "/v1/admin/sources/preview" if operation == "preview" else "/v1/admin/sources",
        json={"feed_url": admission.source.feed_url, "source_type": "publisher"},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    if reason in {"browser_challenge", "solver_unavailable"}:
        assert detail == {"code": reason, "message": str(failure)}
    else:
        assert detail == str(failure)
    assert admission.phases == ["enter", "exit"]
    admission.session.add.assert_not_called()
    admission.session.commit.assert_not_called()


@pytest.mark.parametrize("location", ["feed", "feed-with-solver", "website"])
def test_create_passes_only_validated_source_to_persistence_and_does_not_grant_editorial_approval(
    admission, monkeypatch, location
):
    api, saved = admission, admission.source
    saved.approval_status, saved.enabled = "pending", False
    saved.reviewed_at = saved.reviewed_by = saved.review_note = saved.last_success_at = None
    saved.created_at = saved.updated_at = saved.next_fetch_at = NOW
    saved.feed_url = None if location == "website" else "https://publisher.example/final.xml"
    prepared = ValidatedSource(
        name=saved.name,
        feed_url=saved.feed_url,
        source_type="publisher",
        enabled=True,
        poll_interval_seconds=3600,
        website_url=saved.website_url,
    )

    def validate(body, **options):
        assert api.phases == ["enter", "exit"]
        assert "use_solver" not in body.model_dump()
        assert options == ({"solver": sources.solve_feed} if location == "feed-with-solver" else {})
        return prepared

    validate = Mock(side_effect=validate)
    persist = Mock(return_value=saved)
    monkeypatch.setattr(sources.services, "validate_source", validate)
    monkeypatch.setattr(sources.services, "create_source", persist)
    body = {
        "name": saved.name,
        "source_type": "publisher",
        "poll_interval_seconds": 3600,
        "use_solver": location == "feed-with-solver",
        "website_url": saved.website_url,
    }
    if location != "website":
        body["feed_url"] = "https://publisher.example/request.xml"
    response = api.client.post("/v1/admin/sources", json=body)
    assert response.status_code == 201, response.text
    persist.assert_called_once_with(api.session, prepared)
    output = response.json()
    assert output["approval_status"] == "pending" and output["enabled"] is False
    assert output["feed_url"] == saved.feed_url and output["submission_channel"] == "api"
    api.session.commit.assert_called_once_with()


def test_duplicate_website_candidate_stops_before_network_validation_or_persistence(
    admission, monkeypatch
):
    validate = Mock()
    monkeypatch.setattr(sources.services, "validate_source", validate)
    admission.session.scalar.return_value = uuid.uuid4()
    response = admission.client.post(
        "/v1/admin/sources",
        json={"website_url": "https://publisher.example/", "source_type": "publisher"},
    )
    assert response.status_code == 409
    validate.assert_not_called()
    admission.session.commit.assert_not_called()


def test_persistence_unique_conflict_is_rechecked_after_rollback_and_returns_field_level_duplicate(
    admission, monkeypatch
):
    prepared = ValidatedSource(
        name="Publisher",
        feed_url=admission.source.feed_url,
        source_type="publisher",
        enabled=True,
        poll_interval_seconds=3600,
    )
    monkeypatch.setattr(sources.services, "validate_source", Mock(return_value=prepared))
    monkeypatch.setattr(
        sources.services,
        "create_source",
        Mock(side_effect=IntegrityError("private SQL", {}, Exception("private driver"))),
    )
    admission.session.scalar.side_effect = [None, None, admission.source.id]
    response = admission.client.post(
        "/v1/admin/sources",
        json={"feed_url": admission.source.feed_url, "source_type": "publisher"},
    )
    assert response.status_code == 409
    (issue,) = response.json()["detail"]
    assert issue["loc"] == ["body", "feed_url"] and issue["type"] == "duplicate_feed_url"
    assert "private" not in response.text
    admission.session.rollback.assert_called_once_with()
    admission.session.commit.assert_not_called()


def test_source_detail_materializes_real_schema_before_releasing_read_session(source_policy):
    api = source_policy
    response = api.client.get(f"/v1/admin/sources/{api.source.id}")
    assert response.status_code == 200 and response.json()["id"] == str(api.source.id)
    assert response.json()["approval_status"] == "approved" and response.json()["enabled"] is True
    api.session.close.assert_called_once_with()
    api.session.commit.assert_not_called()


def test_source_review_page_preserves_authenticated_decision_history_and_global_window(
    source_policy, monkeypatch
):
    api = source_policy
    api.source.reviewed_at, api.source.reviewed_by = NOW - timedelta(days=1), "reviewer"
    api.source.review_note = "Reviewed engineering feed"
    review = SourceReview(
        id=uuid.uuid4(),
        source_id=api.source.id,
        decision="approved",
        actor="reviewer",
        note="Reviewed engineering feed",
        created_at=api.source.reviewed_at,
    )
    require = Mock()
    monkeypatch.setattr(sources, "require_record", require)
    monkeypatch.setattr(
        sources,
        "paginate",
        Mock(return_value={"items": [review], "total": 1, "limit": 25, "offset": 0}),
    )
    response = api.client.get(
        f"/v1/admin/sources/{api.source.id}/reviews", params={"q": "reviewer"}
    )
    assert response.status_code == 200, response.text
    (item,) = response.json()["items"]
    assert (
        item["source_id"] == str(api.source.id)
        and item["actor"] == "reviewer"
        and item["decision"] == "approved"
    )
    require.assert_called_once_with(api.session, Source, api.source.id)
    api.session.commit.assert_not_called()
