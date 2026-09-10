"""Submit and inspect image lookups without running HTTP work in the CLI."""

from devfeed_aggregator.dispatch import dispatch_now
from devfeed_core.db import session_factory
from devfeed_core.image_jobs import backfill_images, request_image, retry_image
from devfeed_core.models import ArticleImageJob
from devfeed_core.schemas import ImageJobOut
from devfeed_core.services import RecordNotFound
from sqlalchemy import select


def fetch(args):
    with session_factory().begin() as session:
        job = request_image(session, args.id)
        result = ImageJobOut.model_validate(job).model_dump(mode="json") if job else None
        identifier = job.id if job else None
    if identifier and args.force:
        result = dispatch_now(identifier, kind="images")
    return {"article_id": str(args.id), "already_present": result is None, "job": result}


def backfill(args):
    with session_factory().begin() as session:
        jobs = backfill_images(session, args.limit)
        return {
            "queued": len(jobs),
            "jobs": [ImageJobOut.model_validate(job).model_dump(mode="json") for job in jobs],
        }


def jobs(args):
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


def show(args):
    with session_factory()() as session:
        job = session.get(ArticleImageJob, args.id)
        if job is None:
            raise RecordNotFound("Image job not found")
        return ImageJobOut.model_validate(job).model_dump(mode="json")


def retry(args):
    with session_factory().begin() as session:
        job = retry_image(session, args.id)
        result = ImageJobOut.model_validate(job).model_dump(mode="json") if job else None
        identifier = job.id if job else None
    if identifier and args.force:
        result = dispatch_now(identifier, kind="images")
    return {"already_present": result is None, "job": result}


def dispatch(args):
    return dispatch_now(args.id, kind="images")
