"""Bounded, resumable automation using the existing durable analysis outbox."""

from sqlalchemy import select
from sqlalchemy.orm import lazyload

from devfeed_core.analysis import candidate_score, request_analysis, source_snapshot
from devfeed_core.article_automation import schedule_article_automation, schedule_source_admission
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleContent,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicReanalysis,
    utcnow,
)
from devfeed_core.relationship_coverage import schedule_relationship_coverage
from devfeed_core.tag_topic_discovery import schedule_tag_topic_discovery
from devfeed_core.topic_analysis import missing_fields, request_topic_analysis
from devfeed_core.topic_remediation import finalize_relationship_reviews, schedule_topic_corrections

ACTOR = {
    "subject": "research-automation",
    "issuer": "devfeed",
    "organization_id": "system",
    "name": "Automatic research",
}


def schedule_automation(factory) -> dict[str, int]:
    settings = get_settings()
    counts = {
        "topic_research_scheduled": 0,
        "articles_reanalyzed": 0,
        "reanalysis_scanned": 0,
        "relationship_jobs_scheduled": 0,
        "relationship_scans_completed": 0,
    }
    # Exact identity matching works even when AI is disabled or unavailable.
    counts.update(schedule_tag_topic_discovery(factory))
    if not settings.ai_enabled:
        return counts
    batch = settings.automation_batch_size
    counts["sources_admitted"] = schedule_source_admission(factory)
    counts.update(schedule_article_automation(factory))
    if settings.auto_research_imports:
        with factory.begin() as session:
            proposals = session.scalars(
                select(TopicProposal)
                .where(
                    TopicProposal.status == "pending",
                    TopicProposal.research_requested.is_(True),
                )
                .order_by(TopicProposal.created_at, TopicProposal.id)
                .limit(batch)
                .with_for_update(skip_locked=True)
            ).all()
            for proposal in proposals:
                proposal.research_requested = False
                # Explicit requests and prior attempts are never repeated by polling.
                attempted = session.scalar(
                    select(TopicAnalysisJob.id)
                    .where(TopicAnalysisJob.proposal_id == proposal.id)
                    .limit(1)
                )
                if attempted is None and missing_fields(proposal.proposed):
                    request_topic_analysis(session, proposal.id, ACTOR)
                    counts["topic_research_scheduled"] += 1
    counts.update(schedule_relationship_coverage(factory))
    counts["topic_corrections_scheduled"] = schedule_topic_corrections(factory)
    counts["relationships_rejected"] = finalize_relationship_reviews(factory)
    if settings.auto_reanalyze_topics:
        with factory.begin() as session:
            task = session.scalar(
                select(TopicReanalysis)
                .where(TopicReanalysis.finished_at.is_(None))
                .order_by(TopicReanalysis.created_at, TopicReanalysis.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                return counts
            topic = session.get(Topic, task.topic_id)
            if topic is None or topic.status != "active":
                task.finished_at = utcnow()
                return counts
            query = (
                select(Article)
                .options(lazyload("*"))
                .where(
                    Article.review_status == "pending", Article.publication_status == "unpublished"
                )
                .order_by(Article.id)
                .limit(batch)
            )
            if task.after_article_id:
                query = query.where(Article.id > task.after_article_id)
            # Do not skip locked articles and advance past them permanently.
            articles = session.scalars(query.with_for_update(of=Article)).all()
            for article in articles:
                snapshot = source_snapshot(article, session.get(ArticleContent, article.id))
                if candidate_score(task.topic_snapshot, snapshot) > 0:
                    from devfeed_core.article_jobs import approved_sources

                    if approved_sources(session, article.id):
                        job = request_analysis(session, article.id, automatic=True)
                        counts["articles_reanalyzed"] += job is not None
                task.after_article_id = article.id
            counts["reanalysis_scanned"] = len(articles)
            if len(articles) < batch:
                task.finished_at = utcnow()
    return counts
