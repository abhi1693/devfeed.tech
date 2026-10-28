"""Submit and inspect image lookups without running HTTP work in the CLI."""

from types import SimpleNamespace

from devfeed_aggregator.dispatch import dispatch_now
from devfeed_core.db import session_factory
from devfeed_core.image_jobs import (
    backfill_images,
    backfill_storage,
    backfill_thumbnail_encoding,
    backfill_thumbnail_sizes,
    request_image,
    retry_image,
)
from devfeed_core.models import ArticleImageJob
from devfeed_core.schemas import ImageJobOut
from devfeed_core.services import RecordNotFound
from devfeed_core.source_logos import backfill_source_logos, request_source_logo
from devfeed_core.topic_logos import backfill_topic_logos, request_topic_logo
from sqlalchemy import select

from devfeed_cli.contracts import CommandOutput


def fetch(args: SimpleNamespace) -> CommandOutput:
    with session_factory().begin() as session:
        job = request_image(session, args.id)
        result = ImageJobOut.model_validate(job).model_dump(mode="json") if job else None
        identifier = job.id if job else None
    if identifier and args.force:
        result = dispatch_now(identifier, kind="images")
    return {"article_id": str(args.id), "already_present": result is None, "job": result}


def backfill(args: SimpleNamespace) -> CommandOutput:
    with session_factory().begin() as session:
        schedule = (
            backfill_source_logos
            if getattr(args, "sources", False)
            else backfill_topic_logos
            if getattr(args, "topics", False)
            else backfill_storage
            if getattr(args, "store", False)
            else backfill_images
        )
        jobs = schedule(session, args.limit)
        return {
            "queued": len(jobs),
            "jobs": [ImageJobOut.model_validate(job).model_dump(mode="json") for job in jobs],
        }


def jobs(args: SimpleNamespace) -> list[CommandOutput]:
    statement = select(ArticleImageJob)
    if args.status:
        statement = statement.where(ArticleImageJob.status == args.status)
    if args.article_id:
        statement = statement.where(ArticleImageJob.article_id == args.article_id)
    with session_factory()() as session:
        return [
            ImageJobOut.model_validate(job).model_dump(mode="json")
            for job in session.scalars(
                statement.order_by(ArticleImageJob.created_at.desc(), ArticleImageJob.id)
                .offset(args.offset)
                .limit(args.limit)
            )
        ]


def show(args: SimpleNamespace) -> CommandOutput:
    with session_factory()() as session:
        job = session.get(ArticleImageJob, args.id)
        if job is None:
            raise RecordNotFound("Image job not found")
        return ImageJobOut.model_validate(job).model_dump(mode="json")


def retry(args: SimpleNamespace) -> CommandOutput:
    with session_factory().begin() as session:
        job = retry_image(session, args.id)
        result = ImageJobOut.model_validate(job).model_dump(mode="json") if job else None
        identifier = job.id if job else None
    if identifier and args.force:
        result = dispatch_now(identifier, kind="images")
    return {"already_present": result is None, "job": result}


def dispatch(args: SimpleNamespace) -> CommandOutput:
    return dispatch_now(args.id, kind="images")


def topic_logo(args: SimpleNamespace) -> CommandOutput | None:
    with session_factory().begin() as session:
        job = request_topic_logo(session, args.id)
        return ImageJobOut.model_validate(job).model_dump(mode="json") if job else None


def source_logo(args: SimpleNamespace) -> CommandOutput | None:
    with session_factory().begin() as session:
        job = request_source_logo(session, args.id, refresh=args.refresh)
        return ImageJobOut.model_validate(job).model_dump(mode="json") if job else None


def recompress(args: SimpleNamespace) -> CommandOutput:
    with session_factory().begin() as session:
        jobs = backfill_thumbnail_encoding(session, args.limit)
        return {
            "queued": len(jobs),
            "jobs": [ImageJobOut.model_validate(job).model_dump(mode="json") for job in jobs],
        }


def resize(args: SimpleNamespace) -> CommandOutput:
    with session_factory().begin() as session:
        jobs = backfill_thumbnail_sizes(session, args.limit)
        return {
            "queued": len(jobs),
            "jobs": [ImageJobOut.model_validate(job).model_dump(mode="json") for job in jobs],
        }
