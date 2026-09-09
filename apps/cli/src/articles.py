"""Article metadata maintenance and durable background page-enrichment commands."""

from devfeed_aggregator.dispatch import dispatch_article_now
from devfeed_aggregator.language_backfill import backfill_languages
from devfeed_aggregator.tag_backfill import backfill_tags
from devfeed_core.article_jobs import backfill_articles, request_article_enrichment, retry_article
from devfeed_core.db import session_factory
from devfeed_core.models import ArticleEnrichmentJob
from devfeed_core.schemas import ArticleEnrichmentJobOut
from devfeed_core.services import RecordNotFound
from sqlalchemy import select


def detect_languages(args):
    return backfill_languages(
        limit=args.limit, after=args.after, source_id=args.source_id, dry_run=args.dry_run
    )


def restore_tags(args):
    return backfill_tags(
        limit=args.limit, after=args.after, source_id=args.source_id, dry_run=args.dry_run
    )


def enrich(args):
    with session_factory().begin() as session:
        job = request_article_enrichment(session, args.id)
        result = (
            ArticleEnrichmentJobOut.model_validate(job).model_dump(mode="json") if job else None
        )
        identifier = job.id if job else None
    if identifier and args.force:
        result = dispatch_article_now(identifier)
    return {"article_id": str(args.id), "publisher_metadata_present": result is None, "job": result}


def backfill(args):
    with session_factory().begin() as session:
        queued = backfill_articles(session, args.limit, source_id=args.source_id)
        result = [
            ArticleEnrichmentJobOut.model_validate(job).model_dump(mode="json") for job in queued
        ]
        identifiers = [job.id for job in queued]
    # Jobs commit before publication. Redis failure leaves a durable queued row.
    if args.dispatch:
        result = [dispatch_article_now(identifier) for identifier in identifiers]
    return {"queued": len(identifiers), "jobs": result}


def jobs(args):
    statement = select(ArticleEnrichmentJob)
    if args.status:
        statement = statement.where(ArticleEnrichmentJob.status == args.status)
    if args.article_id:
        statement = statement.where(ArticleEnrichmentJob.article_id == args.article_id)
    with session_factory()() as session:
        return [
            ArticleEnrichmentJobOut.model_validate(job).model_dump(mode="json")
            for job in session.scalars(
                statement.order_by(ArticleEnrichmentJob.created_at.desc(), ArticleEnrichmentJob.id)
                .offset(args.offset)
                .limit(args.limit)
            )
        ]


def show(args):
    with session_factory()() as session:
        job = session.get(ArticleEnrichmentJob, args.id)
        if job is None:
            raise RecordNotFound("Article enrichment job not found")
        return ArticleEnrichmentJobOut.model_validate(job).model_dump(mode="json")


def retry(args):
    with session_factory().begin() as session:
        job = retry_article(session, args.id)
        result = (
            ArticleEnrichmentJobOut.model_validate(job).model_dump(mode="json") if job else None
        )
        identifier = job.id if job else None
    if identifier and args.force:
        result = dispatch_article_now(identifier)
    return {"publisher_metadata_present": result is None, "job": result}


def dispatch(args):
    return dispatch_article_now(args.id)
