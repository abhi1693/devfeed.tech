"""Private maintenance of an existing approved publication.

The serving Article and its associations change only in the promotion command.
All ordinary unpublished processing keeps its existing subject and policy.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, defer, lazyload, sessionmaker

from devfeed_core.article_refresh_types import (
    RefreshContent,
    RefreshDraft,
    RefreshFields,
    RefreshImage,
    RefreshTag,
    RefreshTopic,
)
from devfeed_core.json_types import JsonRecord
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleRefresh,
    ArticleReview,
    ArticleTag,
    ArticleTopic,
    utcnow,
)
from devfeed_core.services import OperationConflict

type RefreshSlot = Literal["enrichment", "analysis", "discovery", "storage"]
type RefreshJob = ArticleEnrichmentJob | ArticleAnalysisJob | ArticleImageJob
ACTIVE_REFRESH_STATES = ("active", "review")


@dataclass(frozen=True)
class PreparedRefreshBase:
    """Detached bounded inputs, prepared before acquiring business write locks."""

    refresh_id: uuid.UUID
    article_id: uuid.UUID
    revision: int
    base_hash: str
    source_hash: str
    draft: RefreshDraft
    image_url: str | None
    managed_image: JsonRecord
    jobs: Mapping[str, uuid.UUID | None]


def approved_digest(session: Session, article: Article) -> str:
    """Hash approved metadata/association identities, reading no private body text."""
    values = {
        **RefreshFields.model_validate(article, from_attributes=True).model_dump(mode="json"),
        "id": article.id,
        "slug": article.slug,
        "published_to_feed_at": article.published_to_feed_at,
        "feed_at": article.feed_at,
        "image_url": article.image_url,
        "managed_image": article.managed_image,
        "classification_provenance": article.classification_provenance,
        "sponsor_names": article.sponsor_names,
        "topics": list(
            session.execute(
                select(
                    ArticleTopic.topic_id,
                    ArticleTopic.role,
                    ArticleTopic.relevance,
                    ArticleTopic.evidence,
                    ArticleTopic.origin,
                )
                .where(ArticleTopic.article_id == article.id)
                .order_by(ArticleTopic.topic_id)
            ).tuples()
        ),
        "tags": list(
            session.execute(
                select(ArticleTag.tag_id, ArticleTag.origin)
                .where(ArticleTag.article_id == article.id)
                .order_by(ArticleTag.tag_id)
            ).tuples()
        ),
        "origins": list(
            session.scalars(
                select(ArticleOrigin.source_id)
                .where(ArticleOrigin.article_id == article.id)
                .order_by(ArticleOrigin.source_id)
            )
        ),
        "content_hash": session.scalar(
            select(ArticleContent.content_hash).where(ArticleContent.article_id == article.id)
        ),
    }
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def prepare_refresh_base(
    session: Session, article: Article, jobs: Mapping[str, object]
) -> PreparedRefreshBase:
    """Load bounded extraction and approved inputs before Source/Article write locks."""
    from devfeed_core.analysis import snapshot_hash
    from devfeed_core.article_refresh_processing import candidate_snapshot

    content = session.get(ArticleContent, article.id)
    draft = RefreshDraft(
        fields=RefreshFields.model_validate(article, from_attributes=True),
        topics=[
            RefreshTopic.model_validate(link, from_attributes=True)
            for link in session.scalars(
                select(ArticleTopic)
                .where(ArticleTopic.article_id == article.id)
                .order_by(ArticleTopic.topic_id)
            )
        ],
        tags=[
            RefreshTag.model_validate(link, from_attributes=True)
            for link in session.scalars(
                select(ArticleTag)
                .where(ArticleTag.article_id == article.id)
                .order_by(ArticleTag.tag_id)
            )
        ],
        content=RefreshContent.model_validate(content, from_attributes=True) if content else None,
    )
    return PreparedRefreshBase(
        uuid.uuid4(),
        article.id,
        article.editorial_revision,
        approved_digest(session, article),
        snapshot_hash(candidate_snapshot(article, draft)),
        draft,
        article.image_url,
        validate_refresh_image(article.managed_image or {}),
        {
            name: getattr(jobs.get(name), "id", None)
            for name in ("enrichment", "analysis", "discovery", "storage")
        },
    )


def create_refresh(
    session: Session,
    article: Article,
    prepared: PreparedRefreshBase,
    target: Literal["enrichment", "analysis", "images"],
    actor: dict[str, str],
) -> ArticleRefresh:
    """Caller holds source authority and Article; no old projection is withdrawn."""
    from devfeed_core.config import get_settings

    if not get_settings().published_article_refresh_enabled:
        raise OperationConflict(
            "Published article maintenance is disabled until workers are upgraded"
        )
    if (
        article.id != prepared.article_id
        or article.editorial_revision != prepared.revision
        or article.publication_status != "published"
        or article.review_status != "approved"
        or approved_digest(session, article) != prepared.base_hash
    ):
        raise OperationConflict(
            "The approved publication changed; reload before retrying maintenance"
        )
    existing = session.scalar(
        select(ArticleRefresh)
        .options(defer(ArticleRefresh.payload, raiseload=True))
        .where(
            ArticleRefresh.article_id == article.id,
            ArticleRefresh.status.in_(ACTIVE_REFRESH_STATES),
        )
        .with_for_update(of=ArticleRefresh)
    )
    if existing is not None:
        if existing.target != target or existing.base_hash != prepared.base_hash:
            raise OperationConflict(
                "Another maintenance candidate is active; inspect it before replacing it"
            )
        return existing
    refresh = ArticleRefresh(
        id=prepared.refresh_id,
        article_id=article.id,
        target=target,
        base_revision=article.editorial_revision,
        base_hash=prepared.base_hash,
        source_hash=prepared.source_hash,
        payload=prepared.draft.model_dump(mode="json"),
        image_url=prepared.image_url,
        managed_image=prepared.managed_image,
        requested_by=actor,
        **{f"{slot}_job_id": identifier for slot, identifier in prepared.jobs.items()},
    )
    session.add(refresh)
    session.flush()
    return refresh


def refresh_for_article(
    session: Session, article: Article, refresh_id: uuid.UUID, *, lock: bool = False
) -> ArticleRefresh | None:
    """Check an explicit owner without hydrating private body/evidence under locks."""
    from devfeed_core.article_jobs import approved_sources

    statement = (
        select(ArticleRefresh)
        .options(defer(ArticleRefresh.payload, raiseload=True))
        .where(ArticleRefresh.id == refresh_id, ArticleRefresh.article_id == article.id)
    )
    if lock:
        statement = statement.with_for_update(of=ArticleRefresh)
    refresh = session.scalar(statement)
    if (
        refresh is None
        or refresh.status not in ACTIVE_REFRESH_STATES
        or refresh.base_revision != article.editorial_revision
        or article.publication_status != "published"
        or article.review_status != "approved"
        or not approved_sources(session, article.id)
    ):
        return None
    return refresh


def refresh_job_current(
    refresh: ArticleRefresh, article: Article, job: RefreshJob, slot: RefreshSlot
) -> bool:
    return bool(
        refresh.status in ACTIVE_REFRESH_STATES
        and article.publication_status == "published"
        and article.review_status == "approved"
        and refresh.base_revision == article.editorial_revision
        and refresh.article_id == job.article_id == article.id
        and job.refresh_id == refresh.id
        and getattr(refresh, f"{slot}_job_id") == job.id
    )


def touch_refresh(refresh: ArticleRefresh) -> None:
    if refresh.status not in ACTIVE_REFRESH_STATES:
        raise OperationConflict("This maintenance candidate is no longer active")
    refresh.version = (refresh.version or 0) + 1
    refresh.status = "active"
    refresh.approved_version = None
    refresh.next_check_at = utcnow()


def select_refresh_job(refresh: ArticleRefresh, slot: RefreshSlot, job: RefreshJob) -> None:
    if job.id is None or job.article_id != refresh.article_id or job.refresh_id != refresh.id:
        raise OperationConflict("Maintenance job does not match its article owner")
    setattr(refresh, f"{slot}_job_id", job.id)
    touch_refresh(refresh)


def validate_refresh_image(value: JsonRecord) -> JsonRecord:
    return (
        RefreshImage.model_validate(value).model_dump(mode="json", exclude_none=True)
        if value
        else {}
    )


def completed_refresh_image(refresh: ArticleRefresh, job: ArticleImageJob | None) -> bool:
    """A terminal task is not sufficient proof of a complete immutable replacement."""
    from devfeed_core.config import get_settings
    from devfeed_core.managed_images import ARTICLE_THUMBNAIL_WIDTHS, IMAGE_VERSION

    store = bool(refresh.image_url and get_settings().image_storage_enabled)
    if (
        job is None
        or job.article_id != refresh.article_id
        or job.refresh_id != refresh.id
        or job.id != (refresh.storage_job_id if store else refresh.discovery_job_id)
        or job.status != "succeeded"
        or job.outcome not in ({"found"} if store else {"found", "not_found"})
    ):
        return False
    if not store:
        return True
    asset = RefreshImage.model_validate(refresh.managed_image)
    if (
        asset.version != IMAGE_VERSION
        or asset.source_url != refresh.image_url
        or job.image_url != refresh.image_url
        or asset.source_width is None
    ):
        return False
    widths = (
        ARTICLE_THUMBNAIL_WIDTHS
        if asset.thumbnail_version == "v2" and asset.thumbnail_layout == "cards-v1"
        else (320, 640, 960)
        if asset.thumbnail_version in {None, "v1"}
        else ()
    )
    expected = {min(width, asset.source_width) for width in widths}
    return bool(expected and expected == {item.width for item in asset.variants})


def try_promote_refresh(factory: sessionmaker[Session], refresh_id: uuid.UUID) -> str:
    """Prepare replacement inputs outside business locks, then publish with a CAS."""
    from devfeed_core.article_jobs import approved_sources
    from devfeed_core.config import get_settings

    with factory() as session:
        owner = session.scalar(
            select(ArticleRefresh)
            .options(defer(ArticleRefresh.payload, raiseload=True))
            .where(ArticleRefresh.id == refresh_id)
        )
        if owner is None:
            return "missing"
        if owner.status not in ACTIVE_REFRESH_STATES:
            return owner.status
        if owner.target != "images":
            from devfeed_core.article_refresh_publication import promote_processed_refresh

            session.close()
            return promote_processed_refresh(factory, refresh_id)
        store = bool(owner.image_url and get_settings().image_storage_enabled)
        selected = owner.storage_job_id if store else owner.discovery_job_id
        job = session.get(ArticleImageJob, selected) if selected else None
        if not completed_refresh_image(owner, job):
            return "pending"
        version, article_id = owner.version, owner.article_id
        image_url, receipt = owner.image_url, validate_refresh_image(owner.managed_image)
        base_revision, base_hash = owner.base_revision, owner.base_hash
    with factory.begin() as session:
        approved = approved_sources(session, article_id, lock=True)
        article = session.scalar(
            select(Article)
            .options(lazyload("*"))
            .where(Article.id == article_id)
            .with_for_update(of=Article)
        )
        owner = session.scalar(
            select(ArticleRefresh)
            .options(defer(ArticleRefresh.payload, raiseload=True))
            .where(ArticleRefresh.id == refresh_id)
            .with_for_update(of=ArticleRefresh)
        )
        if owner is None:
            return "missing"
        if owner.status not in ACTIVE_REFRESH_STATES:
            return owner.status
        if owner.version != version:
            return "pending"
        if (
            not approved
            or article is None
            or article.publication_status != "published"
            or article.review_status != "approved"
            or article.editorial_revision != base_revision
            or approved_digest(session, article) != base_hash
        ):
            owner.status, owner.completed_at = "superseded", utcnow()
            owner.version += 1
            return owner.status
        article.image_url, article.managed_image = image_url, receipt
        owner.status, owner.completed_at = "promoted", utcnow()
        owner.version += 1
        session.add(
            ArticleReview(
                article_id=article_id,
                action="refresh",
                actor="DevFeed maintenance",
                note="Promote completed private image maintenance",
                revision=article.editorial_revision,
                automation={"kind": "maintenance_promotion", "refresh_id": str(refresh_id)},
            )
        )
        return owner.status


def review_refresh(
    session: Session,
    article_id: uuid.UUID,
    refresh_id: uuid.UUID,
    expected_version: int,
    *,
    action: Literal["approve", "reject"],
    actor: dict[str, str],
    note: str | None = None,
) -> ArticleRefresh:
    """Consent applies to one immutable private version, never the live publication."""
    if action == "approve":
        from devfeed_core.article_refresh_publication import approve_refresh

        return approve_refresh(session, article_id, refresh_id, expected_version, actor, note)
    if not note or not note.strip() or len(note) > 1000:
        raise OperationConflict("Discarding a replacement requires a bounded reason")
    article = session.scalar(
        select(Article).where(Article.id == article_id).with_for_update(of=Article)
    )
    owner = session.scalar(
        select(ArticleRefresh)
        .options(defer(ArticleRefresh.payload, raiseload=True))
        .where(ArticleRefresh.id == refresh_id, ArticleRefresh.article_id == article_id)
        .with_for_update(of=ArticleRefresh)
    )
    if article is None or owner is None:
        raise OperationConflict("The maintenance candidate is no longer available")
    if owner.version != expected_version or owner.status not in ACTIVE_REFRESH_STATES:
        raise OperationConflict("The maintenance candidate changed; reload before reviewing")
    owner.status, owner.completed_at = "cancelled", utcnow()
    owner.version += 1
    owner.approved_version = None
    owner.reviewed_by, owner.reviewed_at, owner.review_note = actor, utcnow(), note.strip()
    session.add(
        ArticleReview(
            article_id=article_id,
            action="refresh-discard",
            actor=(actor.get("name") or actor.get("subject") or "Administrator")[:200],
            note=note.strip(),
            revision=article.editorial_revision,
            automation={"kind": "maintenance_review", "refresh_id": str(refresh_id)},
        )
    )
    session.flush()
    return owner
