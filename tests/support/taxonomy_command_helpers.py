"""Schema-valid taxonomy histories for command policy tests."""

import copy
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from devfeed_core import topic_analysis
from devfeed_core.analysis import snapshot_hash
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import Topic, TopicAnalysisJob, TopicProposal, TopicRelationProposal
from devfeed_core.topic_proposals import snapshot, topic_draft
from devfeed_core.topic_relationships import (
    PROMPT_VERSION,
    RelationshipResearchResult,
    RelationshipSuggestion,
    topic_snapshot,
)
from devfeed_core.topics import TopicWrite

NOW = datetime(2026, 10, 23, 1, tzinfo=UTC)
ACTOR = {"subject": "editor-123", "name": "Editorial operator", "issuer": "identity.example"}


def topic(name="React", **values):
    draft = TopicWrite(name=name, slug=name.lower(), kind="technology")
    return Topic(
        **{
            "id": uuid.uuid4(),
            "status": "active",
            "created_at": NOW - timedelta(days=2),
            "updated_at": NOW - timedelta(days=1),
            **draft.model_dump(),
            **values,
        }
    )


def proposal(current=None, **values):
    draft = (
        topic_draft(current).model_dump(mode="json")
        if current
        else TopicWrite(
            name="React",
            slug="react",
            kind="technology",
        ).model_dump(mode="json")
    )
    return TopicProposal(
        **{
            "id": uuid.uuid4(),
            "batch_id": uuid.uuid4(),
            "topic_id": current.id if current else None,
            "slug": draft["slug"],
            "action": "update" if current else "create",
            "origin": "import",
            "source_name": "Operator import",
            "proposed": copy.deepcopy(draft),
            "baseline": snapshot(current) if current else None,
            "status": "pending",
            "evidence": [],
            "created_at": NOW - timedelta(hours=2),
            "created_by": ACTOR,
            "research_requested": False,
            **values,
        }
    )


def relationship_job(focal, peer, *, status="succeeded", **values):
    peers = [peer] if peer is not None else []
    catalog = [focal, *peers]
    document = {
        "topic": topic_snapshot(focal),
        "related_topic_id": str(peer.id) if peer else None,
        "catalog": [[str(row.id), row.name, row.slug, row.kind] for row in catalog],
        "snapshots": {str(row.id): topic_snapshot(row) for row in catalog},
        "excluded_edges": [],
    }
    return TopicAnalysisJob(
        **{
            "id": uuid.uuid4(),
            "topic_id": focal.id,
            "status": status,
            "attempts": 0 if status == "queued" else 1,
            "created_at": NOW - timedelta(minutes=30),
            "available_at": NOW - timedelta(minutes=29),
            "finished_at": NOW - timedelta(minutes=20)
            if status in {"succeeded", "failed"}
            else None,
            "lease_token": uuid.uuid4() if status == "running" else None,
            "lease_until": NOW + timedelta(minutes=5) if status == "running" else None,
            "input_snapshot": document,
            "input_hash": snapshot_hash(document),
            "requested_by": ACTOR,
            "prompt_version": PROMPT_VERSION,
            "result": {},
            "usage": {},
            "outcome": "enriched" if status == "succeeded" else None,
            **values,
        }
    )


def relationship_proposal(focal, peer, job, **values):
    value = TopicRelationProposal(
        **{
            "id": uuid.uuid4(),
            "job_id": job.id,
            "topic_id": focal.id,
            "related_topic_id": peer.id,
            "relation": "uses_language",
            "explanation": "React uses JavaScript for UI components.",
            "evidence_url": "https://react.dev/learn",
            "evidence_title": "React documentation",
            "evidence_quote": "React components are JavaScript functions that return markup.",
            "topic_snapshot": topic_snapshot(focal),
            "related_topic_snapshot": topic_snapshot(peer),
            "created_at": NOW - timedelta(minutes=21),
            "created_by": ACTOR,
            "status": "pending",
            **values,
        }
    )
    if job.status == "succeeded" and job.outcome == "enriched":
        output = RelationshipResearchResult(
            relationships=[
                RelationshipSuggestion(
                    **{
                        field: getattr(value, field)
                        for field in RelationshipSuggestion.model_fields
                    }
                )
            ],
            reasons=[],
        )
        job.result = {**output.model_dump(mode="json"), "proposal_ids": [str(value.id)]}
    return value


def metadata_job(current, *, extra_sources=()):
    """Apply real sourced enrichment and finish its valid lease before independent review."""
    assert current.proposed.get("description") is None
    job = TopicAnalysisJob(
        id=uuid.uuid4(),
        proposal_id=current.id,
        status="running",
        attempts=1,
        created_at=NOW - timedelta(minutes=30),
        available_at=NOW - timedelta(minutes=29),
        lease_token=uuid.uuid4(),
        lease_until=NOW + timedelta(minutes=5),
        input_snapshot={
            "topic": copy.deepcopy(current.proposed),
            "evidence": copy.deepcopy(current.evidence),
        },
        input_hash=snapshot_hash(current.proposed),
        requested_by=ACTOR,
        prompt_version=topic_analysis.PROMPT_VERSION,
        usage={},
        model="test-model",
    )
    result = topic_analysis.TopicResearchResult(
        outcome="ready",
        kind=None,
        description="React components are JavaScript functions that return markup.",
        aliases=[],
        keywords=[],
        website_url=None,
        logo_url=None,
        facts=[],
        sources=[
            {
                "url": "https://react.dev/learn",
                "title": "React documentation",
                "quote": "React components are JavaScript functions that return markup.",
                "fields": ["description"],
            },
            *extra_sources,
        ],
        reasons=[],
    )
    job.result = result.model_dump(mode="json")
    with patch.object(topic_analysis, "utcnow", lambda: NOW - timedelta(minutes=21)):
        assert topic_analysis.apply_topic_research(current, job, result) == "enriched"
    finish_job(job, "enriched", NOW - timedelta(minutes=20))
    return job
