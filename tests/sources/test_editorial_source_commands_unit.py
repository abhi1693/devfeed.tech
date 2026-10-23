"""Source identity and tag ownership commands; SQL conflict/rollback checks live elsewhere."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import discovery, services, source_enrichment, source_lifecycle, source_logos
from devfeed_core.models import SourceCandidate, Tag, Topic
from devfeed_core.schemas import SourcePatch, TagPatch, TagWrite
from devfeed_core.services import OperationConflict, RecordNotFound

from tests.support.source_readiness import NOW, source


@pytest.fixture
def runtime(monkeypatch):
    current = source(poll_interval_seconds=1800, polling_mode="fixed")
    session = Mock()
    session.scalar.return_value = current
    schedule, enrich, discover = Mock(), Mock(), Mock()
    monkeypatch.setattr(services, "reconcile_source_schedule", schedule)
    monkeypatch.setattr(source_enrichment, "request_enrichment", enrich)
    monkeypatch.setattr(discovery, "enqueue", discover)
    monkeypatch.setattr(services, "utcnow", lambda: NOW)
    return SimpleNamespace(
        source=current, session=session, schedule=schedule, enrich=enrich, discover=discover
    )


@pytest.mark.parametrize("duplicate", [False, True])
def test_correcting_an_undiscovered_publisher_restarts_discovery_but_preserves_import_identity(
    runtime, duplicate
):
    current, session = runtime.source, runtime.session
    current.feed_url = None
    candidate = SourceCandidate(
        id=uuid.uuid4(),
        source_id=current.id,
        identity_url=current.website_url,
        name=current.name,
        status="failed",
        approval_status="pending",
        selected_feed_id=None,
        created_at=NOW - timedelta(days=1),
    )
    session.scalar.side_effect = [current, candidate, uuid.uuid4() if duplicate else None]
    body = SourcePatch(website_url="https://publisher.example/new-site")
    if duplicate:
        with pytest.raises(OperationConflict, match="publisher already exists"):
            services.update_source(session, current.id, body)
        session.execute.assert_not_called()
        runtime.discover.assert_not_called()
    else:
        assert services.update_source(session, current.id, body) is current
        assert candidate.status == "pending" and candidate.selected_feed_id is None
        assert candidate.identity_url == current.website_url == body.website_url
        assert candidate.source_id == current.id
        assert session.execute.call_count == 2  # Cancel obsolete work and remove stale feed hints.
        updates = [call.args[0].compile().params for call in session.execute.call_args_list]
        assert updates[0]["error"] == "website_changed" and updates[0]["lease_token"] is None
        assert updates[1]["feed_hint"] is None
        runtime.discover.assert_called_once_with(session, candidate.id, background=True)
    runtime.enrich.assert_not_called()
    assert current.approval_status == "pending" and not current.enabled


def test_undiscovered_publisher_cannot_lose_its_only_discovery_address(runtime):
    runtime.source.feed_url = None
    before = runtime.source.website_url
    with pytest.raises(OperationConflict, match="Keep a website"):
        services.update_source(runtime.session, runtime.source.id, SourcePatch(website_url=None))
    assert runtime.source.website_url == before
    runtime.session.execute.assert_not_called()
    runtime.discover.assert_not_called()


def test_correcting_a_discovered_source_requests_a_profile_for_the_new_identity(runtime):
    current = runtime.source
    services.update_source(
        runtime.session, current.id, SourcePatch(website_url="https://publisher.example/corrected")
    )
    runtime.enrich.assert_called_once_with(runtime.session, current.id)
    assert current.website_url.endswith("/corrected") and current.approval_status == "pending"
    runtime.discover.assert_not_called()
    runtime.schedule.assert_not_called()


@pytest.mark.parametrize("blocked", [False, True])
def test_reenabling_an_approved_source_obeys_processing_prerequisites_before_polling(
    runtime, monkeypatch, blocked
):
    current = runtime.source
    current.approval_status = "approved"
    current.enabled = False
    current.processing_request = {
        "kind": "upstream_retry",
        "id": str(uuid.uuid4()),
        "target": "profile",
        "required": ["profile", "branding"],
        "requested_at": NOW.isoformat(),
    }
    blockers = Mock(return_value=["profile_running"] if blocked else [])
    monkeypatch.setattr(source_lifecycle, "source_activation_blockers", blockers)
    if blocked:
        with pytest.raises(OperationConflict, match="profile_running"):
            services.update_source(runtime.session, current.id, SourcePatch(enabled=True))
        assert not current.enabled and current.processing_request
        runtime.schedule.assert_not_called()
    else:
        services.update_source(runtime.session, current.id, SourcePatch(enabled=True))
        assert current.enabled and current.processing_request == {}
        runtime.schedule.assert_called_once_with(runtime.session, current, immediate=True)
    blockers.assert_called_once_with(runtime.session, current)


def test_submit_with_a_logo_requests_branding_for_the_new_pending_source(runtime, monkeypatch):
    current = runtime.source
    current.logo_url = "https://publisher.example/logo.png"
    runtime.session.scalar.side_effect = [current.id, current]
    review, logo = Mock(), Mock()
    monkeypatch.setattr(source_enrichment, "request_source_review", review)
    monkeypatch.setattr(source_logos, "request_source_logo", logo)
    before = current.name
    result = services.submit_source(
        runtime.session,
        services.ValidatedSource(
            name=current.name,
            feed_url=current.feed_url,
            source_type=current.source_type,
            enabled=True,
            poll_interval_seconds=1800,
            logo_url=current.logo_url,
        ),
    )
    assert result == (current, True, None) and current.name == before
    assert not current.enabled and current.approval_status == "pending"
    review.assert_called_once_with(runtime.session, current)
    logo.assert_called_once_with(runtime.session, current.id, automatic=True)


@pytest.mark.parametrize("active", [False, True])
def test_tag_create_requires_an_active_target_and_treats_explicit_assignment_as_manual(active):
    identifier = uuid.uuid4()
    session = Mock()
    session.get.return_value = Topic(
        id=identifier,
        name="Python",
        slug="python",
        kind="language",
        status="active" if active else "rejected",
    )
    if active:
        created = services.create_tag(
            session, TagWrite(name="Python tips", slug="python-tips", topic_id=identifier)
        )
        assert created.topic_id == identifier and created.auto_link_topic is False
        session.add.assert_called_once_with(created)
    else:
        with pytest.raises(RecordNotFound, match="Active topic"):
            services.create_tag(
                session, TagWrite(name="Python tips", slug="python-tips", topic_id=identifier)
            )
        session.add.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        TagPatch(name="Renamed"),
        TagPatch(topic_id=None),
        TagPatch(auto_link_topic=True),
        TagWrite(name="Replacement", slug="replacement"),
    ],
)
def test_tag_writes_preserve_unspecified_patch_fields_and_reset_only_matching_intent(body):
    identifier = uuid.uuid4()
    current = Tag(
        id=uuid.uuid4(),
        name="Routing",
        slug="routing",
        aliases=["Routes"],
        topic_id=identifier,
        auto_link_topic=False,
        topic_match_revision=5,
        topic_match_status="manual",
        topic_match_checked_at=NOW,
    )
    session = Mock()
    session.scalar.return_value = current
    session.get.return_value = Topic(
        id=identifier, name="Python", slug="python", kind="language", status="active"
    )
    assert services.update_tag(session, current.id, body) is current
    if isinstance(body, TagWrite):
        assert (
            current.name == "Replacement"
            and current.slug == "replacement"
            and current.aliases == []
        )
        assert (
            current.topic_id is None
            and not current.auto_link_topic
            and current.topic_match_revision == 0
        )
    elif body.model_fields_set == {"name"}:
        assert (
            current.name == "Renamed"
            and current.slug == "routing"
            and current.aliases == ["Routes"]
        )
        assert current.topic_id == identifier and current.topic_match_revision == 5
    elif "topic_id" in body.model_fields_set:
        assert current.topic_id is None and not current.auto_link_topic
        assert current.topic_match_status == "manual" and current.topic_match_checked_at is None
    else:
        assert current.topic_id == identifier and current.auto_link_topic
        assert current.topic_match_status == "pending" and current.topic_match_checked_at is None
    session.flush.assert_called_once()


def test_tag_patch_refuses_a_missing_record_before_validation_or_mutation():
    session = Mock()
    session.scalar.return_value = None
    with pytest.raises(RecordNotFound, match="Tag not found"):
        services.update_tag(session, uuid.uuid4(), TagPatch(name="Renamed"))
    session.get.assert_not_called()
    session.add.assert_not_called()
