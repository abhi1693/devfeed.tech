"""Operator input and ORM histories preserve catalog and publication boundaries."""

import json
from unittest.mock import Mock

import pytest
from devfeed_core import analysis, automation_events, cache_events, relationship_coverage, topics
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import Tag, TopicRelationshipScan
from devfeed_core.topic_kind_map import load_topic_kind_map
from sqlalchemy.orm import Session
from test_analysis_admission_contracts_unit import admission as admission
from test_analysis_application_remaining_unit import application as application
from test_analysis_catalog import entry
from test_runtime_automation_events_unit import attach_read, topic
from test_source_readiness_unit import configuration


@pytest.mark.parametrize("invalid", ["missing", "json", "array", "empty-key", "invalid-kind"])
def test_mounted_topic_kind_map_rejects_unreadable_or_invalid_operator_input(tmp_path, invalid):
    path = tmp_path / "kind-map.json"
    if invalid != "missing":
        path.write_text(
            "{malformed"
            if invalid == "json"
            else json.dumps(
                []
                if invalid == "array"
                else {"": "technology"}
                if invalid == "empty-key"
                else {"legacy-kind": "not-a-canonical-kind"}
            ),
            encoding="utf-8",
        )
    with pytest.raises(ValueError) as raised:
        load_topic_kind_map(path)
    if invalid in {"missing", "json"}:
        assert raised.value.__cause__ is not None and str(path) in str(raised.value)
    elif invalid == "array":
        assert "JSON object" in str(raised.value)
    elif invalid == "empty-key":
        assert "non-empty strings" in str(raised.value)
    else:
        assert "legacy-kind" in str(raised.value)


def test_mounted_kind_map_preserves_the_explicit_historical_identity(tmp_path):
    path = tmp_path / "kind-map.json"
    path.write_text(json.dumps({"historical-framework": "technology"}), encoding="utf-8")
    assert load_topic_kind_map(path) == {"historical-framework": "technology"}


def test_full_topic_candidate_quota_still_allows_independently_ranked_tag_evidence(monkeypatch):
    settings = configuration(analysis_max_candidates=1, analysis_fallback_candidates=0)
    monkeypatch.setattr(analysis, "get_settings", lambda: settings)
    taxonomy = {
        "topics": [entry(f"A topic {i}", aliases=["Routing correctness"]) for i in range(3)],
        "tags": [entry("Z routing tag", aliases=["Routing correctness"])],
    }
    selected = analysis.analysis_candidates(taxonomy, {"title": "Routing correctness"})
    assert [[row["id"] for row in selected[field]] for field in ("topics", "tags")] == [
        [taxonomy["topics"][0]["id"]],
        [taxonomy["tags"][0]["id"]],
    ]


@pytest.mark.parametrize("preloaded_catalog", [False, True])
def test_superseded_analysis_with_unchanged_source_revision_and_catalog_does_not_respend(
    application, monkeypatch, preloaded_catalog
):
    v = application
    finish_job(v.job, "superseded", v.facts["now"])
    catalog, enqueue = Mock(return_value=v.taxonomy), Mock()
    monkeypatch.setattr(analysis, "catalog", catalog)
    monkeypatch.setattr(analysis, "request_analysis", enqueue)
    assert analysis.analysis_catalog_current(
        v.job, v.taxonomy, analysis.source_snapshot(v.article, None)
    )
    assert (
        analysis.refresh_superseded_analysis(
            v.session, v.article, v.job, **({"taxonomy": v.taxonomy} if preloaded_catalog else {})
        )
        is None
    )
    assert catalog.call_count == int(not preloaded_catalog)
    enqueue.assert_not_called()
    v.session.flush.assert_not_called()


def test_new_topic_assigns_identity_before_creating_relationship_coverage_even_with_ai_disabled(
    monkeypatch,
):
    current = topic(id=None)
    lock, reset = Mock(), Mock()
    monkeypatch.setattr(topics, "lock_topics", lock)
    monkeypatch.setattr(relationship_coverage, "reset_scan", reset)
    monkeypatch.setattr(automation_events, "get_settings", lambda: configuration(ai_enabled=False))
    with Session(autoflush=False) as session:
        session.add(current)
        monkeypatch.setattr(session, "get", Mock(return_value=None))
        automation_events.relationship_changes(session, None, None)
        assert current.id is not None
        session.get.assert_called_once_with(TopicRelationshipScan, current.id)
        reset.assert_called_once_with(session, current, None)
        lock.assert_called_once_with(session)


def test_retiring_a_topic_without_a_retained_scan_does_not_create_background_work(monkeypatch):
    reset = Mock()
    monkeypatch.setattr(topics, "lock_topics", Mock())
    monkeypatch.setattr(relationship_coverage, "reset_scan", reset)
    with Session(autoflush=False) as session:
        current = attach_read(session, topic())
        current.status = "rejected"
        monkeypatch.setattr(session, "get", Mock(return_value=None))
        automation_events.relationship_changes(session, None, None)
        assert current.status == "rejected"
        reset.assert_not_called()
        assert not session.new


def test_loaded_tag_reassigned_its_current_value_does_not_evict_public_responses():
    current = Tag(
        id=topic().id,
        name="Python",
        slug="python",
        aliases=[],
        auto_link_topic=True,
        topic_match_revision=0,
        topic_match_status="pending",
    )
    with cache_events.AppSession(autoflush=False) as session:
        attach_read(session, current)
        current.name = "Python"
        assert current in session.dirty and not session.is_modified(current)
        cache_events.track_objects(session, None, None)
        assert not session.info.get(cache_events.DIRTY)
        assert not session.info.get(cache_events.REASONS)
