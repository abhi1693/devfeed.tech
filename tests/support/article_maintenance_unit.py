"""Detached approved/private subjects for maintenance policy and worker behavior."""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from devfeed_core import analysis
from devfeed_core import article_refresh_processing as processing
from devfeed_core.article_refresh import PreparedRefreshBase
from devfeed_core.article_refresh_types import (
    RefreshDraft,
    RefreshFields,
    RefreshTopic,
)
from devfeed_core.config import get_settings
from devfeed_core.models import (
    ArticleRefresh,
)
from sqlalchemy.orm import Session

from tests.articles.test_article_lifecycle import facts as completed_facts

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def ready_analysis(topic_id, **changes):
    return analysis.AnalysisResult.model_validate(
        {
            "outcome": "ready",
            "developer_relevance": "relevant",
            "page_kind": "article",
            "language": "en",
            "content_type": "tutorial",
            "content_format": "article",
            "ai_title": "Trace Angular navigation",
            "title_evidence": "Angular routing helps developers organize navigation.",
            "ai_summary": "Trace Angular routing decisions to diagnose application navigation.",
            "ai_description": "A practical guide to diagnosing Angular navigation.",
            "topics": [
                {
                    "topic_id": str(topic_id),
                    "role": "primary",
                    "relevance": 0.9,
                    "evidence": "Angular routing is the practical focus.",
                }
            ],
            "tags": [],
            "reasons": [],
            **changes,
        }
    )


@pytest.fixture
def prepared(monkeypatch):
    public, completed = completed_facts()
    public.publication_status, public.slug = "published", "approved-angular-navigation"
    public.sponsor_names = []
    public.metadata_source_type = "publisher"
    public.origins[0].source.enabled = True
    public.origins[0].source.publication_policy = "auto"
    identifier = uuid.uuid4()
    facts = {
        name: completed.get(name)
        for name in ("content", "enrichment", "analysis", "discovery", "storage")
    }
    for name in ("enrichment", "analysis"):
        facts[name].refresh_id = identifier
        facts[name].finished_at = NOW
    topic = public.topic_links[0].topic
    result = ready_analysis(topic.id)
    draft = RefreshDraft(
        fields=RefreshFields.model_validate(public, from_attributes=True),
        topics=[
            RefreshTopic(
                topic_id=topic.id,
                role="primary",
                relevance=1,
                evidence="Preserved operator assignment",
                origin="manual",
            )
        ],
        tags=[],
        analysis=result,
    )
    snapshot = processing.candidate_snapshot(public, draft)
    job = facts["analysis"]
    job.input_snapshot, job.input_hash = snapshot, analysis.snapshot_hash(snapshot)
    job.catalog_snapshot = analysis.analysis_candidates(completed["taxonomy"], snapshot)
    job.catalog_hash = analysis.snapshot_hash(job.catalog_snapshot)
    job.result = result.model_dump(mode="json")
    public.classification_provenance = {
        "analysis_id": str(job.id),
        "input_hash": job.input_hash,
        "developer_relevance": "relevant",
        "page_kind": "article",
    }
    base = PreparedRefreshBase(
        refresh_id=identifier,
        article_id=public.id,
        revision=public.editorial_revision,
        base_hash="b" * 64,
        source_hash=job.input_hash,
        draft=draft,
        image_url=None,
        managed_image={},
        jobs={
            name: getattr(facts[name], "id", None)
            for name in ("enrichment", "analysis", "discovery", "storage")
        },
    )
    session = Mock(spec=Session)
    session.scalars.side_effect = [[topic], []]
    inputs = processing.prepare_initial_refresh_inputs(
        session, public, base, facts, completed["taxonomy"]
    )
    owner = ArticleRefresh(
        id=identifier,
        article_id=public.id,
        status="active",
        target="analysis",
        base_revision=public.editorial_revision,
        base_hash=base.base_hash,
        source_hash=inputs.input_hash,
        version=1,
        payload=draft.model_dump(mode="json"),
        enrichment_job_id=facts["enrichment"].id,
        analysis_job_id=job.id,
        image_url=None,
        managed_image={},
    )
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    return public, owner, inputs, base


class WorkerSessions:
    def __init__(self, read, write):
        self.read, self.write = read, write
        self.writing = False

    @contextmanager
    def __call__(self):
        yield self.read

    @contextmanager
    def begin(self):
        assert not self.writing
        self.writing = True
        try:
            yield self.write
        finally:
            self.writing = False
