"""Minimal authoritative job owners for report capture integration contracts."""

import uuid

from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    IngestionJob,
    ResearchVerificationJob,
    Source,
    Topic,
    TopicAnalysisJob,
)


def add_reporting_job(session, kind, **values):
    identifier = values.pop("id", uuid.uuid4())
    if kind == "article_analysis":
        owner = Article(
            title="Report capture",
            url_hash=uuid.uuid4().hex,
            canonical_url=f"https://example.test/{uuid.uuid4()}",
        )
        session.add(owner)
        session.flush()
        job = ArticleAnalysisJob(id=identifier, article_id=owner.id, **values)
    elif kind == "ingestion":
        owner = Source(
            name="Report capture",
            source_type="publisher",
            approval_status="approved",
            enabled=True,
            feed_url=f"https://example.test/{uuid.uuid4()}/feed",
        )
        session.add(owner)
        session.flush()
        job = IngestionJob(id=identifier, source_id=owner.id, **values)
    else:
        owner = Topic(
            name="Report capture", slug=uuid.uuid4().hex, kind="technology", status="active"
        )
        session.add(owner)
        session.flush()
        parent = TopicAnalysisJob(
            id=identifier,
            topic_id=owner.id,
            input_hash="a" * 64,
            input_snapshot={},
            requested_by={},
            prompt_version="report-capture",
            **(values if kind == "topic_analysis" else {"status": "failed"}),
        )
        session.add(parent)
        session.flush()
        job = (
            parent
            if kind == "topic_analysis"
            else ResearchVerificationJob(id=identifier, relationships=False, **values)
        )
    session.add(job)
    session.flush()
    return job
