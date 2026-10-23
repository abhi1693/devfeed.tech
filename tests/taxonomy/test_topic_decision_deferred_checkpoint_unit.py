"""Unresolved topic decisions retain checkpoints and cannot manufacture approval."""

import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import topic_decision_budget, topic_decisions
from devfeed_core.topic_decisions import DecisionDeferred

from tests.support.source_readiness import NOW, configuration
from tests.taxonomy.test_topic_decisions import IDENTIFIER, TEXT, TOPIC, answer
from tests.taxonomy.test_topic_decisions import bundle as original_bundle


def bundle():
    evidence = original_bundle()
    evidence["pages"][0]["content_hash"] = hashlib.sha256(TEXT.encode()).hexdigest()
    return evidence


@pytest.mark.parametrize("checkpointed", [False, True])
def test_discovery_without_identity_sources_defers_without_repeating_a_saved_model_call(
    checkpointed,
):
    discovery = {"sources": [], "reason": "No primary source identifies the proposed entity."}
    state = {"discovery": discovery} if checkpointed else {}
    call, fetch = Mock(return_value=discovery), Mock()
    for _ in range(2):
        with pytest.raises(DecisionDeferred, match="identity_sources_unavailable"):
            topic_decisions.run_decision(
                IDENTIFIER, TOPIC, state, call=call, save=state.__setitem__, fetch=fetch
            )
    assert state == {"discovery": discovery}
    assert call.call_count == (0 if checkpointed else 1)
    if not checkpointed:
        assert call.call_args.args[0] == "discovery" and call.call_args.kwargs == {"web": True}
    fetch.assert_not_called()


@pytest.mark.parametrize("empty_text", [False, True])
def test_missing_or_unusable_fetched_evidence_cannot_spend_draft_or_verification_calls(empty_text):
    evidence = {
        "pages": [],
        "failures": [{"url": "https://example.com/", "reason": "transport_error"}],
    }
    if empty_text:
        evidence = bundle()
        evidence["pages"][0] = topic_decisions.page_snapshot(
            "https://example.com/",
            {
                "text": "Go",
                "final_url": "https://example.com/",
                "content_hash": hashlib.sha256(b"Go").hexdigest(),
                "validated_at": NOW.isoformat(),
            },
        )
    state = {"discovery": answer("discovery", "", bundle())}
    call, fetch = Mock(), Mock(return_value=evidence)
    for _ in range(2):
        with pytest.raises(DecisionDeferred, match="evidence_unavailable"):
            topic_decisions.run_decision(
                IDENTIFIER, TOPIC, state, call=call, save=state.__setitem__, fetch=fetch
            )
    assert state["evidence"] == evidence
    fetch.assert_called_once_with(["https://example.com/"])
    call.assert_not_called()


@pytest.mark.parametrize("failure", ["uncertain-draft", "duplicate-verification-selectors"])
def test_unsuccessful_stage_escalation_is_bounded_and_replay_does_not_respend(failure):
    evidence, state, stages = bundle(), {}, []
    state["evidence"] = evidence

    def call(stage, prompt, _schema, **options):
        stages.append((stage, options))
        result = answer(stage, prompt, evidence)
        if failure == "uncertain-draft":
            assert stage == "draft"
            result["outcome"] = "uncertain"
            result["reason"] = "The evidence cannot resolve this exact identity."
        elif stage == "verification":
            result["relevance"]["sentence_ids"] *= 2
        return result

    fetch = Mock()
    for _ in range(2):
        with pytest.raises(DecisionDeferred, match="validation_unresolved"):
            topic_decisions.run_decision(
                IDENTIFIER, TOPIC, state, call=call, save=state.__setitem__, fetch=fetch
            )
    assert stages == (
        [("draft", {"escalated": False}), ("draft", {"escalated": True})]
        if failure == "uncertain-draft"
        else [
            ("draft", {"escalated": False}),
            ("verification", {"escalated": False}),
            ("verification", {"escalated": True}),
        ]
    )
    assert set(state) == (
        {"evidence", "draft", "escalated_draft"}
        if failure == "uncertain-draft"
        else {"evidence", "draft", "draft_verification", "escalated_verification"}
    )
    fetch.assert_not_called()


def test_bundle_deadline_includes_time_waiting_for_an_evidence_worker(monkeypatch):
    # One real executor worker observes an already exhausted shared deadline.
    clock = iter([100.0, 111.0])
    monkeypatch.setattr(topic_decisions, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(
        topic_decisions,
        "get_settings",
        lambda: configuration(evidence_timeout_seconds=10, topic_evidence_concurrency=1),
    )
    fetch = Mock()
    result = topic_decisions.fetch_bundle(["https://example.com/"], fetch=fetch)
    assert result == {
        "version": topic_decisions.VERSION,
        "pages": [],
        "failures": [{"url": "https://example.com/", "reason": "verification_timeout"}],
    }
    fetch.assert_not_called()


def test_disabling_both_quota_pacing_and_bounded_topics_preserves_legacy_relationship_admission(
    monkeypatch,
):
    monkeypatch.setattr(
        topic_decision_budget,
        "get_settings",
        lambda: configuration(ai_quota_pacing_enabled=False, ai_bounded_topics_enabled=False),
    )
    session = Mock()
    assert topic_decision_budget.relationship_allowance(session) is True
    session.scalar.assert_not_called()
