"""Managed logo presentation and durable scheduling; no remote I/O on reads."""

from sqlalchemy import func, select

from devfeed_core.config import get_settings
from devfeed_core.models import ArticleImageJob, Topic
from devfeed_core.services import OperationConflict, RecordNotFound

LOGO_VERSION = "v1"
# UI logos are 22–36 CSS pixels; card exports need at most 96 pixels.
# 32/64 cover normal/high-density list icons; 96 covers card exports.
LOGO_SIZES = (32, 64, 96)


def logo_variants(topic) -> list[dict]:
    settings = get_settings()
    asset = getattr(topic, "managed_logo", None) or {}
    # A replacement can fail: continue serving the last complete asset. Clearing
    # the source URL explicitly removes the logo, including its previous asset.
    if not topic.logo_url or not settings.image_public_url or not asset.get("variants"):
        return []
    return [
        {"url": f"{settings.image_public_url}/{item['key']}", "width": item["width"]}
        for item in sorted(asset["variants"], key=lambda item: item["width"])
    ]


def logo_url(topic, size=64) -> str | None:
    variants = logo_variants(topic)
    if variants:
        return next((v["url"] for v in variants if v["width"] >= size), variants[-1]["url"])
    asset = getattr(topic, "managed_logo", None) or {}
    public_url = get_settings().image_public_url
    if topic.logo_url and public_url and asset.get("original_key"):
        return f"{public_url}/{asset['original_key']}"
    return None


def logo_current(topic) -> bool:
    asset = topic.managed_logo or {}
    return (
        asset.get("source_url") == topic.logo_url
        and asset.get("version") == LOGO_VERSION
        and {v["width"] for v in asset.get("variants", [])} == set(LOGO_SIZES)
    )


def request_topic_logo(session, topic_id, *, automatic=False):
    topic = session.scalar(select(Topic).where(Topic.id == topic_id).with_for_update())
    if topic is None:
        raise RecordNotFound("Topic not found")
    if not get_settings().image_storage_enabled or not topic.logo_url or logo_current(topic):
        return None
    active = session.scalar(
        select(ArticleImageJob).where(
            ArticleImageJob.topic_id == topic_id, ArticleImageJob.status.in_(["queued", "running"])
        )
    )
    if active:
        return active
    if automatic and session.scalar(
        select(ArticleImageJob.id)
        .where(
            ArticleImageJob.topic_id == topic_id,
            ArticleImageJob.image_url == topic.logo_url,
            ArticleImageJob.storage_version == LOGO_VERSION,
        )
        .limit(1)
    ):
        return None
    job = ArticleImageJob(
        topic_id=topic_id,
        operation="topic-logo",
        image_url=topic.logo_url,
        storage_version=LOGO_VERSION,
    )
    session.add(job)
    session.flush()
    return job


def backfill_topic_logos(session, limit=100):
    if not 1 <= limit <= 500:
        raise ValueError("Backfill limit must be between 1 and 500")
    if not get_settings().image_storage_enabled:
        raise OperationConflict("Enable image storage before scheduling the backfill")
    attempted = select(ArticleImageJob.id).where(
        ArticleImageJob.topic_id == Topic.id,
        ArticleImageJob.status.in_(["queued", "running"])
        | (
            (ArticleImageJob.image_url == Topic.logo_url)
            & (ArticleImageJob.storage_version == LOGO_VERSION)
        ),
    )
    ids = session.scalars(
        select(Topic.id)
        .where(
            Topic.logo_url.is_not(None),
            ~attempted.exists(),
            ~(
                (func.coalesce(Topic.managed_logo["source_url"].as_string(), "") == Topic.logo_url)
                & (func.coalesce(Topic.managed_logo["version"].as_string(), "") == LOGO_VERSION)
                & func.coalesce(
                    Topic.managed_logo["variants"].contains(
                        [{"width": size} for size in LOGO_SIZES]
                    ),
                    False,
                )
                & (
                    func.coalesce(func.jsonb_array_length(Topic.managed_logo["variants"]), 0)
                    == len(LOGO_SIZES)
                )
            ),
        )
        .order_by(Topic.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    return [
        job
        for identifier in ids
        if (job := request_topic_logo(session, identifier, automatic=True)) is not None
    ]
