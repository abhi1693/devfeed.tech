"""Untrusted research must earn each applied field without modifying the review boundary."""

import copy
import uuid
from datetime import timedelta

import pytest
from devfeed_core import topic_analysis
from devfeed_core.job_lifecycle import start_job
from pydantic import ValidationError
from taxonomy_command_helpers import ACTOR, NOW, proposal
from test_topic_analysis import output


@pytest.fixture
def research(monkeypatch):
    current = proposal(evidence=[{"provider": "operator_import"}])
    job = topic_analysis._new_job(current, ACTOR)
    job.id, job.status, job.attempts = uuid.uuid4(), "queued", 0
    job.created_at = job.available_at = NOW - timedelta(minutes=1)
    job.result, job.model = {}, "research-model"
    start_job(job, NOW, 300)
    monkeypatch.setattr(topic_analysis, "utcnow", lambda: NOW)
    return current, job


@pytest.mark.parametrize("outcome", ["insufficient_evidence", "ready"])
def test_research_without_applicable_evidence_leaves_the_pending_draft_untouched(research, outcome):
    current, job = research
    result = topic_analysis.TopicResearchResult.model_validate(
        {
            **output(),
            "outcome": outcome,
            "description": None,
            "keywords": [],
            "website_url": None,
            "facts": [],
            "sources": [],
            "reasons": ["The exact project identity could not be independently verified."],
        }
    )
    before = copy.deepcopy((current.proposed, current.evidence, job.result))
    assert topic_analysis.apply_topic_research(current, job, result) == "insufficient_evidence"
    assert (current.proposed, current.evidence, job.result) == before
    assert current.status == "pending" and current.topic_id is None
    assert job.status == "running" and job.lease_token is not None


def test_a_fact_cannot_borrow_a_different_citations_field_coverage(research):
    current, job = research
    document = output()
    document["facts"][0]["source_url"] = "https://other-project.example/license"
    result = topic_analysis.TopicResearchResult.model_validate(document)
    before = copy.deepcopy((current.proposed, current.evidence, job.result))
    with pytest.raises(ValueError, match="Each fact must cite a research source"):
        topic_analysis.apply_topic_research(current, job, result)
    assert (current.proposed, current.evidence, job.result) == before
    assert current.status == "pending" and current.topic_id is None


def test_research_reason_limit_accepts_the_boundary_and_rejects_an_oversized_model_response():
    boundary = {**output(), "reasons": ["a" * 500]}
    assert topic_analysis.TopicResearchResult.model_validate(boundary).reasons == ["a" * 500]
    with pytest.raises(ValidationError, match="Research reasons must be bounded"):
        topic_analysis.TopicResearchResult.model_validate({**boundary, "reasons": ["a" * 501]})
