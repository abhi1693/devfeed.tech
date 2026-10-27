import sys
from collections.abc import Collection
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, TypedDict, Unpack

from alembic import command
from alembic.config import Config
from devfeed_aggregator import dispatch, scheduler, worker
from devfeed_core import services
from devfeed_core.cache import get_cache
from devfeed_core.db import session_factory
from devfeed_core.models import (
    IngestionJob,
    Source,
    SourceEnrichmentJob,
    SourceReview,
    Tag,
)
from devfeed_core.schemas import (
    JobOut,
    SourceCreate,
    SourceDecision,
    SourceEnrichmentJobOut,
    SourceOut,
    SourcePatch,
    SourceReviewOut,
    TagOut,
    TagPatch,
    TagWrite,
)
from devfeed_core.source_enrichment import request_enrichment
from devfeed_core.source_profiles import PROFILE_FIELDS
from devfeed_core.source_types import SourceType
from sqlalchemy import select
from sqlalchemy.orm import Session

from devfeed_cli.contracts import CommandOutput, CommandValue


class SubmissionOutput(TypedDict):
    created: bool
    source: dict[str, CommandValue]
    job: dict[str, CommandValue] | None


class SourceImportOutput(TypedDict):
    submitted: int
    created: int
    items: list[SubmissionOutput]


class SourceProfileFields(TypedDict, total=False):
    description: str | None
    website_url: str | None
    logo_url: str | None
    image_url: str | None
    language: str | None
    polling_mode: Literal["fixed", "adaptive"]
    submitted_by: dict[str, str | None] | None


MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_FEEDS = 1000


class InputError(ValueError):
    pass


def cache_clear(args: SimpleNamespace) -> CommandOutput:
    cache = get_cache()
    cache.invalidate(reasons=("manual",))
    cache.invalidate("operations", reasons=("manual",))
    return {"cleared": True, "scope": "api_get_responses"}


def source_body(
    url: str,
    *,
    source_type: SourceType,
    name: str | None = None,
    interval: int = 43200,
    enabled: bool = True,
    **profile: Unpack[SourceProfileFields],
) -> SourceCreate:
    # Leave an omitted name unresolved until shared feed preflight reads its title.
    return SourceCreate(
        name=name,
        feed_url=url,
        source_type=source_type,
        poll_interval_seconds=interval,
        enabled=enabled,
        **profile,
    )


def submission(session: Session, body: services.ValidatedSource) -> SubmissionOutput:
    source, created, job = services.submit_source(session, body)
    return {
        "created": created,
        "source": SourceOut.model_validate(source).model_dump(mode="json"),
        "job": JobOut.model_validate(job).model_dump(mode="json") if job else None,
    }


def source_add(args: SimpleNamespace) -> SubmissionOutput:
    body = source_body(
        args.url,
        source_type=args.source_type,
        name=args.name,
        interval=args.poll_interval,
        polling_mode=args.polling_mode,
        enabled=not args.disabled,
        **{field: getattr(args, field) for field in PROFILE_FIELDS},
        submitted_by={"name": args.submitted_by, "profile_url": args.submitter_url}
        if args.submitted_by
        else None,
    )
    if args.submitter_url and not args.submitted_by:
        raise InputError("--submitter-url requires --submitted-by")
    validated = services.validate_source(body)
    with session_factory().begin() as session:
        return submission(session, validated)


def source_import(args: SimpleNamespace) -> SourceImportOutput:
    if args.file == "-":
        content = sys.stdin.read(MAX_IMPORT_BYTES + 1)
    else:
        with Path(args.file).open(encoding="utf-8") as stream:
            content = stream.read(MAX_IMPORT_BYTES + 1)
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise InputError("Import file exceeds 1 MB")
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise InputError("Import contains no feed URLs")
    if len(lines) > MAX_IMPORT_FEEDS:
        raise InputError(f"Import is limited to {MAX_IMPORT_FEEDS} feed URLs")
    # Validate everything before opening a transaction; lock in URL order across imports.
    bodies = {
        body.feed_url: body
        for body in (
            source_body(url, source_type=args.source_type, interval=args.poll_interval)
            for url in lines
        )
    }
    validated = [
        services.validate_source(bodies[url])
        for url in sorted(bodies, key=lambda value: value or "")
    ]
    with session_factory().begin() as session:
        results = [submission(session, body) for body in validated]
    return {
        "submitted": len(results),
        "created": sum(item["created"] for item in results),
        "items": results,
    }


def source_list(args: SimpleNamespace) -> list[CommandOutput]:
    statement = select(Source)
    if args.source_type is not None:
        statement = statement.where(Source.source_type == args.source_type)
    if args.enabled is not None:
        statement = statement.where(Source.enabled == args.enabled)
    if args.approval_status is not None:
        statement = statement.where(Source.approval_status == args.approval_status)
    with session_factory()() as session:
        return [
            SourceOut.model_validate(row).model_dump(mode="json")
            for row in session.scalars(
                statement.order_by(Source.name, Source.id).offset(args.offset).limit(args.limit)
            )
        ]


def source_show(args: SimpleNamespace) -> CommandOutput:
    with session_factory()() as session:
        source = session.get(Source, args.id)
        if source is None:
            raise services.RecordNotFound("Source not found")
        return SourceOut.model_validate(source).model_dump(mode="json")


def changes(args: SimpleNamespace, fields: Collection[str]) -> dict[str, object]:
    values = {key: value for key, value in vars(args).items() if key in fields}
    if not values:
        raise InputError("Specify at least one field to update")
    return values


def source_update(args: SimpleNamespace) -> CommandOutput:
    body = SourcePatch.model_validate(changes(args, SourcePatch.model_fields))
    with session_factory().begin() as session:
        return SourceOut.model_validate(services.update_source(session, args.id, body)).model_dump(
            mode="json"
        )


def source_fetch(args: SimpleNamespace) -> CommandOutput:
    with session_factory().begin() as session:
        job = services.fetch_source(session, args.id)
        result = JobOut.model_validate(job).model_dump(mode="json")
        job_id = job.id
    if args.force:
        return dispatch.dispatch_now(job_id)
    return result


def source_review(args: SimpleNamespace) -> CommandOutput:
    body = SourceDecision(decision=args.decision, actor=args.by, note=args.note)
    with session_factory().begin() as session:
        source = services.review_source(session, args.id, body)
        return SourceOut.model_validate(source).model_dump(mode="json")


def source_review_history(args: SimpleNamespace) -> list[CommandOutput]:
    with session_factory()() as session:
        if session.get(Source, args.id) is None:
            raise services.RecordNotFound("Source not found")
        return [
            SourceReviewOut.model_validate(row).model_dump(mode="json")
            for row in session.scalars(
                select(SourceReview)
                .where(SourceReview.source_id == args.id)
                .order_by(SourceReview.created_at.desc(), SourceReview.id)
                .offset(args.offset)
                .limit(args.limit)
            )
        ]


def source_enrich(args: SimpleNamespace) -> CommandOutput:
    with session_factory().begin() as session:
        job = request_enrichment(session, args.id)
        result = SourceEnrichmentJobOut.model_validate(job).model_dump(mode="json")
        job_id = job.id
    return dispatch.dispatch_now(job_id, kind="source-enrichment") if args.force else result


def source_enrichment_jobs(args: SimpleNamespace) -> list[CommandOutput]:
    statement = select(SourceEnrichmentJob)
    if args.source_id:
        statement = statement.where(SourceEnrichmentJob.source_id == args.source_id)
    if args.status:
        statement = statement.where(SourceEnrichmentJob.status == args.status)
    with session_factory()() as session:
        return [
            SourceEnrichmentJobOut.model_validate(row).model_dump(mode="json")
            for row in session.scalars(
                statement.order_by(SourceEnrichmentJob.created_at.desc(), SourceEnrichmentJob.id)
                .offset(args.offset)
                .limit(args.limit)
            )
        ]


def source_enrichment_dispatch(args: SimpleNamespace) -> CommandOutput:
    return dispatch.dispatch_now(args.id, kind="source-enrichment")


def job_dispatch(args: SimpleNamespace) -> CommandOutput:
    return dispatch.dispatch_now(args.id)


def job_list(args: SimpleNamespace) -> list[CommandOutput]:
    statement = select(IngestionJob)
    if args.source_id:
        statement = statement.where(IngestionJob.source_id == args.source_id)
    if args.status:
        statement = statement.where(IngestionJob.status == args.status)
    with session_factory()() as session:
        return [
            JobOut.model_validate(row).model_dump(mode="json")
            for row in session.scalars(
                statement.order_by(IngestionJob.created_at.desc(), IngestionJob.id)
                .offset(args.offset)
                .limit(args.limit)
            )
        ]


def job_show(args: SimpleNamespace) -> CommandOutput:
    with session_factory()() as session:
        job = session.get(IngestionJob, args.id)
        if job is None:
            raise services.RecordNotFound("Job not found")
        return JobOut.model_validate(job).model_dump(mode="json")


def job_retry(args: SimpleNamespace) -> CommandOutput:
    with session_factory().begin() as session:
        return JobOut.model_validate(services.retry_job(session, args.id)).model_dump(mode="json")


def taxonomy_list(args: SimpleNamespace) -> list[CommandOutput]:
    with session_factory()() as session:
        return [
            TagOut.model_validate(row).model_dump(mode="json")
            for row in session.scalars(
                select(Tag).order_by(Tag.slug).offset(args.offset).limit(args.limit)
            )
        ]


def taxonomy_write(args: SimpleNamespace) -> CommandOutput:
    tag_schema = TagWrite if args.action == "add" else TagPatch
    tag_body = tag_schema.model_validate(changes(args, tag_schema.model_fields))
    with session_factory().begin() as session:
        tag = (
            services.create_tag(session, tag_body)
            if isinstance(tag_body, TagWrite)
            else services.update_tag(session, args.id, tag_body)
        )
        return TagOut.model_validate(tag).model_dump(mode="json")


def run_worker(args: SimpleNamespace) -> None:
    options = {"queue_name": args.queue} if getattr(args, "queue", "all") != "all" else {}
    worker.run(burst=args.burst, name=args.name, max_jobs=args.max_jobs, **options)


def run_scheduler(args: SimpleNamespace) -> CommandOutput | None:
    if args.once:
        return scheduler.tick()
    scheduler.run()
    return None


def migrate(args: SimpleNamespace) -> None:
    config_path = args.config
    if config_path is None:
        config_path = next(
            (
                directory / "alembic.ini"
                for directory in (Path.cwd(), *Path.cwd().parents)
                if (directory / "alembic.ini").is_file()
            ),
            None,
        )
    if config_path is None or not config_path.is_file():
        raise InputError("Cannot find alembic.ini; run from the repository or supply --config PATH")
    config = Config(str(config_path.resolve()))
    if args.action == "upgrade":
        command.upgrade(config, "head")
    elif args.action == "check":
        command.check(config)
    else:
        command.current(config)
