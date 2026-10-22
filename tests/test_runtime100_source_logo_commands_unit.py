"""Source assessment and logo outbox commands retain admission and pending-work authority."""

from unittest.mock import Mock

import pytest
from devfeed_core import config, logos, services, source_enrichment
from devfeed_core.models import Source
from devfeed_core.schemas import SourceDecision
from devfeed_core.services import OperationConflict, RecordNotFound
from sqlalchemy.orm import Session
from test_source_readiness_unit import NOW, configuration, profile, source


@pytest.mark.parametrize("state", ["deleted", "rejected", "profiling"])
def test_assessment_request_checks_current_source_authority_and_coalesces_upstream_work(
    monkeypatch, state
):
    current = source(approval="approved" if state == "rejected" else "pending")
    session = Mock()
    if state == "rejected":
        session.scalar.return_value = current
        monkeypatch.setattr(services, "utcnow", lambda: NOW)
        services.review_source(
            session,
            current.id,
            SourceDecision(
                decision="rejected",
                actor="editor",
                note="The feed does not serve software readers.",
            ),
        )
        session.reset_mock()
    active = profile(current, status="queued") if state == "profiling" else None
    session.scalar.side_effect = [
        None if state == "deleted" else current,
        *([active] if active else []),
    ]
    monkeypatch.setattr(config, "get_settings", lambda: configuration(ai_enabled=True))
    if state == "profiling":
        assert source_enrichment.request_relevance(session, current.id) is active
        assert active.operation == "profile" and active.status == "queued" and active.attempts == 0
    else:
        with pytest.raises(RecordNotFound if state == "deleted" else OperationConflict):
            source_enrichment.request_relevance(session, current.id)
        assert session.scalar.call_count == 1
    session.add.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize("deleted", [False, True])
def test_manual_profile_dispatch_refuses_a_real_rejection_or_an_expired_job_identity(
    monkeypatch, deleted
):
    current = source(approval="approved")
    queued = profile(current, status="queued")
    session = Mock()
    session.scalar.return_value = current
    monkeypatch.setattr(services, "utcnow", lambda: NOW)
    services.review_source(
        session,
        current.id,
        SourceDecision(
            decision="rejected",
            actor="editor",
            note="The publication is no longer relevant to the software audience.",
        ),
    )
    session.reset_mock()
    session.scalar.return_value = None if deleted else queued
    session.get.return_value = current
    with pytest.raises(RecordNotFound if deleted else OperationConflict):
        source_enrichment.prepare_enrichment_dispatch(session, queued.id)
    assert queued.status == "queued" and queued.attempts == 0
    session.flush.assert_not_called()
    if deleted:
        session.get.assert_not_called()


@pytest.mark.parametrize("refresh", [False, True])
def test_logo_outbox_assigns_a_pending_subject_identity_and_coalesces_in_the_actual_orm_session(
    monkeypatch, refresh
):
    settings = configuration(image_storage_enabled=True, image_public_url="https://images.example")
    monkeypatch.setattr(logos, "get_settings", lambda: settings)
    current = Source(
        name="Pending publisher",
        feed_url="https://publisher.example/feed",
        logo_url="https://publisher.example/logo.png",
        source_type="publisher",
        approval_status="pending",
        enabled=False,
        managed_logo={},
    )
    with Session() as session:
        session.add(current)
        read = Mock(return_value=None)
        monkeypatch.setattr(session, "scalar", read)
        first = logos.queue_logo(session, current, refresh=refresh)
        assert current.id is not None and first.source_id == current.id and first.source is current
        assert first.operation == ("source-logo-refresh" if refresh else "source-logo")
        assert first.storage_version == "v1" and first.image_url == current.logo_url
        assert logos.queue_logo(session, current, refresh=refresh) is first
        assert read.call_count == 1
        assert first in session.new
