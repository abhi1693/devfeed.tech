"""Trusted operator controls; no public moderation or account endpoints."""

from pathlib import Path

from devfeed_aggregator.queue import get_queue
from devfeed_aggregator.scheduler import dispatch_jobs
from devfeed_core.analysis import (
    ManualClassification,
    backfill_analyses,
    classify_manually,
    request_analysis,
)
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.editorial import EditorialDecision, decide_article, publication_blockers
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleReview, Topic, utcnow
from devfeed_core.schemas import ArticleOut
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topics import RelationWrite, TopicOut, TopicWrite, relate_topics, save_topic
from sqlalchemy import select


def article_view(article):
    return {
        **ArticleOut.from_article(article).model_dump(mode="json"),
        "review_status": article.review_status,
        "publication_status": article.publication_status,
        "editorial_revision": article.editorial_revision,
        "publication_blockers": publication_blockers(article),
        "classification_provenance": article.classification_provenance,
    }


def inspect_article(args):
    with session_factory()() as session:
        article = session.get(Article, args.id)
        if article is None:
            raise RecordNotFound("Article not found")
        return article_view(article)


def classify(args):
    path = Path(args.file)
    if path.stat().st_size > 128_000:
        raise OperationConflict("Classification JSON exceeds 128 KB")
    body = ManualClassification.model_validate_json(path.read_text())
    with session_factory().begin() as session:
        return article_view(classify_manually(session, args.id, body))


def list_articles(args):
    statement = select(Article).order_by(Article.id).limit(args.limit)
    if args.after:
        statement = statement.where(Article.id > args.after)
    if args.review_status:
        statement = statement.where(Article.review_status == args.review_status)
    if args.publication_status:
        statement = statement.where(Article.publication_status == args.publication_status)
    with session_factory()() as session:
        rows = session.scalars(statement).all()
        return {
            "items": [article_view(article) for article in rows],
            "next_after": str(rows[-1].id) if len(rows) == args.limit else None,
        }


def decide(args):
    with session_factory().begin() as session:
        article = decide_article(
            session,
            args.id,
            EditorialDecision(
                action=args.action, actor=args.by, note=args.note, expected_revision=args.revision
            ),
            dry_run=args.dry_run,
        )
        return {"dry_run": args.dry_run, "action": args.action, "article": article_view(article)}


def history(args):
    with session_factory()() as session:
        return [
            {
                "id": str(row.id),
                "action": row.action,
                "actor": row.actor,
                "note": row.note,
                "revision": row.revision,
                "created_at": row.created_at,
            }
            for row in session.scalars(
                select(ArticleReview)
                .where(ArticleReview.article_id == args.id)
                .order_by(ArticleReview.created_at.desc(), ArticleReview.id)
                .limit(args.limit)
            )
        ]


def job_view(job):
    # Private snapshots are intentionally omitted from routine job output.
    return {
        name: getattr(job, name)
        for name in (
            "id",
            "article_id",
            "status",
            "attempts",
            "available_at",
            "dispatched_at",
            "finished_at",
            "input_hash",
            "editorial_revision",
            "model",
            "prompt_version",
            "outcome",
            "error",
            "result",
        )
    }


def analyze(args):
    if not get_settings().ai_enabled:
        raise OperationConflict(
            "Configure Codex and set DEVFEED_AI_ENABLED=true before queueing analysis"
        )
    with session_factory().begin() as session:
        identifier = args.id
        if args.action == "analysis-retry":
            old = session.get(ArticleAnalysisJob, identifier)
            if old is None:
                raise RecordNotFound("Analysis job not found")
            if old.status != "failed":
                raise OperationConflict("Only failed analysis jobs can be retried")
            identifier = old.article_id
        job = request_analysis(session, identifier)
        job_id = job.id
        result = job_view(job)
    if args.force:
        return dispatch_analysis(job_id)
    return result


def analysis_backfill(args):
    if not get_settings().ai_enabled:
        raise OperationConflict("Configure and enable AI before queueing analysis")
    with session_factory().begin() as session:
        jobs, scanned, after = backfill_analyses(session, args.limit, after=args.after)
        result = [job_view(job) for job in jobs]
        identifiers = [job.id for job in jobs]
    if args.dispatch:
        result = [dispatch_analysis(identifier) for identifier in identifiers]
    return {"scanned": scanned, "queued": len(result), "next_after": after, "jobs": result}


def dispatch_analysis(identifier):
    factory = session_factory()
    with factory.begin() as session:
        job = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.id == identifier).with_for_update()
        )
        if job is None:
            raise RecordNotFound("Analysis job not found")
        if job.status != "queued":
            raise OperationConflict("Only queued analysis jobs can be dispatched")
        job.available_at, job.dispatched_at = utcnow(), None
    queue = get_queue("analysis")
    try:
        dispatch_jobs(factory, queue, 1, utcnow(), job_id=identifier, analyses=True)
    finally:
        queue.connection.close()
    with factory() as session:
        return job_view(session.get(ArticleAnalysisJob, identifier))


def analyses(args):
    statement = (
        select(ArticleAnalysisJob)
        .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id)
        .limit(args.limit)
    )
    if args.article_id:
        statement = statement.where(ArticleAnalysisJob.article_id == args.article_id)
    with session_factory()() as session:
        return [job_view(job) for job in session.scalars(statement)]


def topic_list(args):
    with session_factory()() as session:
        return [
            TopicOut.model_validate(topic).model_dump(mode="json")
            for topic in session.scalars(select(Topic).order_by(Topic.slug).limit(args.limit))
        ]


def topic_write(args):
    path = Path(args.file)
    if path.stat().st_size > 128_000:
        raise OperationConflict("Topic JSON exceeds 128 KB")
    body = TopicWrite.model_validate_json(path.read_text())
    with session_factory().begin() as session:
        return TopicOut.model_validate(
            save_topic(session, body, getattr(args, "id", None))
        ).model_dump(mode="json")


def topic_relate(args):
    body = RelationWrite(
        topic_id=args.id,
        related_topic_id=args.related_id,
        relation=args.relation,
        evidence_url=args.evidence_url,
    )
    with session_factory().begin() as session:
        relate_topics(session, body)
    return body.model_dump(mode="json")


def topic_accept(args):
    with session_factory().begin() as session:
        job = session.get(ArticleAnalysisJob, args.analysis_id)
        if job is None:
            raise RecordNotFound("Analysis job not found")
        proposal = next(
            (item for item in job.result.get("proposed_topics", []) if item["slug"] == args.slug),
            None,
        )
        if proposal is None:
            raise RecordNotFound("Topic proposal not found")
        body = TopicWrite(name=proposal["name"], slug=proposal["slug"], kind=proposal["kind"])
        topic = save_topic(session, body)
        return TopicOut.model_validate(topic).model_dump(mode="json")
