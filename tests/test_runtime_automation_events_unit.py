"""ORM history drives bounded automation events without evaluating SQL or locks.

Real SQLAlchemy instances/sessions provide new, dirty, deleted, and attribute
history state. Catalog, scan-reset, and logo-enqueue operations are boundaries.
"""

import uuid
from unittest.mock import Mock

import pytest
from devfeed_core import automation_events as events
from devfeed_core import logos, relationship_coverage, tag_topic_discovery, topics
from devfeed_core.models import Source, Tag, Topic, TopicReanalysis, TopicRelationshipScan
from devfeed_core.topic_relationships import topic_snapshot
from sqlalchemy.orm import Session, make_transient_to_detached
from test_source_readiness_unit import NOW, configuration, source


def topic(**changes):
    return Topic(
        **{
            "id": uuid.uuid4(),
            "name": "Python",
            "slug": "python",
            "kind": "language",
            "aliases": ["Python language"],
            "keywords": ["scripting"],
            "status": "active",
            "description": "A programming language.",
            "website_url": "https://python.example",
            "logo_url": None,
            "managed_logo": {},
            "identity_keys": [],
            **changes,
        }
    )


def attach_read(session, row):
    """Attach a supplied loaded ORM record; no database or history is fabricated."""
    make_transient_to_detached(row)
    session.add(row)
    return row


@pytest.fixture
def runtime(monkeypatch):
    settings = configuration(ai_enabled=True, auto_reanalyze_topics=True)
    monkeypatch.setattr(events, "get_settings", lambda: settings)
    lock, reset, changed, queued = Mock(), Mock(), Mock(), Mock()
    monkeypatch.setattr(topics, "lock_topics", lock)
    monkeypatch.setattr(relationship_coverage, "reset_scan", reset)
    monkeypatch.setattr(tag_topic_discovery, "catalog_changed", changed)
    monkeypatch.setattr(logos, "queue_logo", queued)
    with Session(autoflush=False) as session:
        yield session, settings, lock, reset, changed, queued


@pytest.mark.parametrize(
    "field,replacement",
    [("name", "Python runtime"), ("aliases", ["Py"]), ("keywords", ["automation"])],
)
def test_reanalysis_snapshot_retains_removed_identity_terms_without_duplicate_aliases(
    runtime, field, replacement
):
    session, _, _, _, _, _ = runtime
    current = attach_read(session, topic())
    previous = getattr(current, field)
    setattr(current, field, replacement)

    events.topic_changes(session, None, None)

    requests = [row for row in session.new if isinstance(row, TopicReanalysis)]
    assert len(requests) == 1 and requests[0].topic_id == current.id
    snapshot = requests[0].topic_snapshot
    if field == "aliases":
        assert snapshot[field][0] == "Py"
    else:
        assert snapshot[field] == replacement
    old_terms = previous if isinstance(previous, list) else [previous]
    assert all(term in snapshot["aliases"] for term in old_terms)
    assert len(snapshot["aliases"]) == len(set(snapshot["aliases"]))
    assert requests[0].topic is current and snapshot["status"] == "active"


@pytest.mark.parametrize("restriction", ["ai-off", "policy-off", "inactive", "description-only"])
def test_reanalysis_ignores_disabled_policy_inactive_topics_and_nonmatching_metadata(
    runtime, restriction
):
    session, settings, *_ = runtime
    current = attach_read(session, topic())
    if restriction == "ai-off":
        settings.ai_enabled = False
    elif restriction == "policy-off":
        settings.auto_reanalyze_topics = False
    elif restriction == "inactive":
        current.status = "rejected"
    current.description = "Updated explanation."

    events.topic_changes(session, None, None)

    assert not any(isinstance(row, TopicReanalysis) for row in session.new)


def test_new_active_topic_gets_its_identity_before_a_reanalysis_event_is_recorded(runtime):
    session, *_ = runtime
    current = topic(id=None)
    session.add(current)

    events.topic_changes(session, None, None)

    assert isinstance(current.id, uuid.UUID)
    request = next(row for row in session.new if isinstance(row, TopicReanalysis))
    assert request.topic_id == current.id and request.topic_snapshot["aliases"] == current.aliases


def test_relationship_coverage_is_reset_for_meaningful_edits_even_when_ai_is_disabled(
    runtime, monkeypatch
):
    session, settings, lock, reset, *_ = runtime
    settings.ai_enabled = False
    current = attach_read(session, topic())
    scan = TopicRelationshipScan(
        topic_id=current.id, generation=3, topic_snapshot=topic_snapshot(current)
    )
    monkeypatch.setattr(session, "get", Mock(return_value=scan))
    current.description = "Updated language documentation."

    events.relationship_changes(session, None, None)

    reset.assert_called_once_with(session, current, scan)
    lock.assert_called_once_with(session)


def test_new_topic_reuses_an_already_pending_relationship_scan(runtime):
    session, _, _, reset, *_ = runtime
    current = topic()
    scan = TopicRelationshipScan(topic_id=current.id, topic=current)
    session.add_all([current, scan])

    events.relationship_changes(session, None, None)

    reset.assert_called_once_with(session, current, scan)


def test_retiring_a_topic_finishes_its_existing_scan_without_starting_another(runtime, monkeypatch):
    session, _, _, reset, *_ = runtime
    current = attach_read(session, topic())
    scan = TopicRelationshipScan(
        topic_id=current.id,
        generation=3,
        topic_snapshot=topic_snapshot(current),
        job_id=uuid.uuid4(),
        finished_at=None,
    )
    monkeypatch.setattr(session, "get", Mock(return_value=scan))
    monkeypatch.setattr("devfeed_core.models.utcnow", lambda: NOW)
    current.status = "rejected"

    events.relationship_changes(session, None, None)

    assert scan.finished_at == NOW and scan.job_id is None
    reset.assert_not_called()


@pytest.mark.parametrize("kind", ["new-topic", "changed-topic", "deleted-topic"])
def test_topic_identity_changes_refresh_keys_and_request_one_catalog_revision(runtime, kind):
    session, _, lock, _, changed, _ = runtime
    current = topic(name="  C++ ", slug="cpp", aliases=["C Plus Plus"])
    if kind == "new-topic":
        session.add(current)
    else:
        attach_read(session, current)
        if kind == "changed-topic":
            current.aliases = ["C PLUS PLUS", " Cpp "]
        else:
            session.delete(current)

    events.tag_topic_changes(session, None, None)

    assert current.identity_keys == tag_topic_discovery.record_keys(current)
    assert "c++" in current.identity_keys
    changed.assert_called_once_with(session)
    lock.assert_called_once_with(session)


@pytest.mark.parametrize("manual", [False, True])
def test_new_tag_respects_explicit_topic_assignment_instead_of_scheduling_automatic_relink(
    runtime, manual
):
    session, _, _, _, changed, _ = runtime
    current = Tag(
        id=uuid.uuid4(),
        name="Python tips",
        slug="python-tips",
        aliases=[],
        topic_id=uuid.uuid4() if manual else None,
        auto_link_topic=None,
    )
    session.add(current)

    events.tag_topic_changes(session, None, None)

    assert current.auto_link_topic is (not manual)
    assert current.topic_match_status == ("manual" if manual else "pending")
    assert current.topic_match_revision == 0 and current.topic_match_checked_at is None
    changed.assert_not_called()


def test_tag_identity_edit_invalidates_the_matching_receipt_without_changing_its_manual_choice(
    runtime,
):
    session, *_ = runtime
    current = attach_read(
        session,
        Tag(
            id=uuid.uuid4(),
            name="Python",
            slug="python",
            aliases=[],
            topic_id=uuid.uuid4(),
            auto_link_topic=False,
            topic_match_revision=7,
            topic_match_status="manual",
            topic_match_checked_at=NOW,
        ),
    )
    current.name = "Python programming"

    events.tag_topic_changes(session, None, None)

    assert current.topic_match_status == "manual" and not current.auto_link_topic
    assert current.topic_match_revision == 0 and current.topic_match_checked_at is None


@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("model", [Topic, Source])
def test_logo_url_edit_requests_the_correct_owned_outbox_subject(
    runtime, monkeypatch, persisted, model
):
    session, settings, *_, queued = runtime
    settings.image_storage_enabled = True
    current = topic() if model is Topic else source()
    if persisted:
        attach_read(session, current)
    else:
        session.add(current)
    current.logo_url = "https://publisher.example/new-logo.png"
    read = Mock(return_value=current.id)
    monkeypatch.setattr(session, "scalar", read)

    events.logo_changes(session, None, None)

    queued.assert_called_once_with(session, current)
    assert read.call_count == int(persisted)


def test_current_logo_asset_and_disabled_storage_do_not_request_redundant_work(runtime):
    session, settings, *_, queued = runtime
    current = topic(logo_url="https://publisher.example/logo.png")
    current.managed_logo = {
        "source_url": current.logo_url,
        "version": logos.LOGO_VERSION,
        "variants": [{"width": width} for width in logos.logo_sizes(Topic)],
    }
    session.add(current)
    settings.image_storage_enabled = True
    events.logo_changes(session, None, None)
    queued.assert_not_called()
    current.logo_url = "https://publisher.example/replacement.png"
    settings.image_storage_enabled = False
    events.logo_changes(session, None, None)
    queued.assert_not_called()
