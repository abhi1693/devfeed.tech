"""Managed logo presentation and durable scheduling; no remote I/O on reads."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from devfeed_core.config import get_settings
from devfeed_core.models import ArticleImageJob, Source, Topic
from devfeed_core.services import OperationConflict, RecordNotFound

LOGO_VERSION = "v1"
# UI logos are 22–36 CSS pixels; card exports need at most 96 pixels.
# 32/64 cover normal/high-density list icons; 96 covers card exports.
LOGO_SIZES = (32, 64, 96)
SOURCE_LOGO_SIZES = (16, 32, 64, 96)


@dataclass(frozen=True)
class LogoTarget:
    model: type[Topic] | type[Source]
    job_field: str
    sizes: tuple[int, ...]

    @property
    def job_column(self):
        return getattr(ArticleImageJob, self.job_field)

    def new_job(self, subject, *, refresh=False):
        relationship = self.job_field.removesuffix("_id")
        operation = f"{relationship}-logo"
        if self.model is Source and refresh:
            operation += "-refresh"
        return ArticleImageJob(
            **{self.job_field: subject.id, relationship: subject},
            operation=operation,
            image_url=subject.logo_url,
            storage_version=LOGO_VERSION,
        )


TOPIC_LOGO = LogoTarget(Topic, "topic_id", LOGO_SIZES)
SOURCE_LOGO = LogoTarget(Source, "source_id", SOURCE_LOGO_SIZES)


def logo_target(model):
    return SOURCE_LOGO if model is Source else TOPIC_LOGO


def logo_job_target(job):
    if job.source_id is not None:
        return SOURCE_LOGO
    return TOPIC_LOGO if job.topic_id is not None else None


def logo_sizes(model):
    return logo_target(model).sizes


def logo_variants(subject) -> list[dict]:
    settings = get_settings()
    asset = getattr(subject, "managed_logo", None) or {}
    # A replacement can fail: continue serving the last complete asset. Clearing
    # the source URL explicitly removes the logo, including its previous asset.
    if not subject.logo_url or not settings.image_public_url or not asset.get("variants"):
        return []
    return [
        {"url": f"{settings.image_public_url}/{item['key']}", "width": item["width"]}
        for item in sorted(asset["variants"], key=lambda item: item["width"])
    ]


def logo_url(subject, size=64) -> str | None:
    variants = logo_variants(subject)
    if variants:
        return next((v["url"] for v in variants if v["width"] >= size), variants[-1]["url"])
    asset = getattr(subject, "managed_logo", None) or {}
    public_url = get_settings().image_public_url
    if subject.logo_url and public_url and asset.get("original_key"):
        return f"{public_url}/{asset['original_key']}"
    return None


def logo_current(subject) -> bool:
    asset = subject.managed_logo or {}
    return (
        asset.get("source_url") == subject.logo_url
        and asset.get("version") == LOGO_VERSION
        and {v["width"] for v in asset.get("variants", [])} == set(logo_sizes(type(subject)))
    )


def request_logo(session, model, subject_id, *, automatic=False, refresh=False):
    subject = session.scalar(select(model).where(model.id == subject_id).with_for_update())
    if subject is None:
        raise RecordNotFound(f"{model.__name__} not found")
    job = queue_logo(session, subject, automatic=automatic, refresh=refresh)
    if job is not None:
        session.flush()
    return job


def queue_logo(session, subject, *, automatic=False, refresh=False):
    """Add to the outbox without flushing; callers lock persisted subjects first."""
    if (
        not get_settings().image_storage_enabled
        or not subject.logo_url
        or (logo_current(subject) and not refresh)
    ):
        return None
    target = logo_target(type(subject))
    if subject.id is None:
        subject.id = uuid.uuid4()
    for job in session.new:
        if isinstance(job, ArticleImageJob) and getattr(job, target.job_field) == subject.id:
            return job
    active = session.scalar(
        select(ArticleImageJob).where(
            target.job_column == subject.id, ArticleImageJob.status.in_(["queued", "running"])
        )
    )
    if active:
        return active
    if automatic and session.scalar(
        select(ArticleImageJob.id)
        .where(
            target.job_column == subject.id,
            ArticleImageJob.image_url == subject.logo_url,
            ArticleImageJob.storage_version == LOGO_VERSION,
        )
        .limit(1)
    ):
        return None
    job = target.new_job(subject, refresh=refresh)
    session.add(job)
    return job


def backfill_logos(session, model, limit=100):
    target = logo_target(model)
    if not 1 <= limit <= 500:
        raise ValueError("Backfill limit must be between 1 and 500")
    if not get_settings().image_storage_enabled:
        raise OperationConflict("Enable image storage before scheduling the backfill")
    attempted = select(ArticleImageJob.id).where(
        target.job_column == model.id,
        ArticleImageJob.status.in_(["queued", "running"])
        | (
            (ArticleImageJob.image_url == model.logo_url)
            & (ArticleImageJob.storage_version == LOGO_VERSION)
        ),
    )
    ids = session.scalars(
        select(model.id)
        .where(
            model.logo_url.is_not(None),
            ~attempted.exists(),
            ~(
                (func.coalesce(model.managed_logo["source_url"].as_string(), "") == model.logo_url)
                & (func.coalesce(model.managed_logo["version"].as_string(), "") == LOGO_VERSION)
                & func.coalesce(
                    model.managed_logo["variants"].contains(
                        [{"width": size} for size in target.sizes]
                    ),
                    False,
                )
                & (
                    func.coalesce(func.jsonb_array_length(model.managed_logo["variants"]), 0)
                    == len(target.sizes)
                )
            ),
        )
        .order_by(model.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    return [
        job
        for identifier in ids
        if (job := request_logo(session, model, identifier, automatic=True)) is not None
    ]
