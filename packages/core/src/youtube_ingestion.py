"""Idempotent YouTube video metadata and retained collection provenance."""

import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.editorial import invalidate_editorial
from devfeed_core.models import Article, ArticleContent, ArticleOrigin, YouTubeMembership, utcnow
from devfeed_core.urls import fingerprint
from devfeed_core.youtube import YouTubePlaylistItem, YouTubeVideo


def apply_video_metadata(session, article: Article, video: YouTubeVideo | None) -> bool:
    """Caller locks the article and its approved origins; transport is already done."""
    before = article.video_metadata or {}
    if video is None:
        metadata = {**before, "availability": "unavailable", "retrieved_at": utcnow().isoformat()}
        changed = before.get("availability") != "unavailable"
        article.video_metadata = metadata
        if changed:
            invalidate_editorial(article)
        return changed
    if article.provider_resource_id != video.id:
        raise ValueError("Video metadata does not match the stored identity")
    metadata = video.metadata
    semantic_fields = (
        "description",
        "channel_id",
        "channel_title",
        "duration_seconds",
        "availability",
        "default_audio_language",
        "default_language",
        "language",
    )
    changed = (
        article.title != video.title
        or article.summary != video.description
        or any(before.get(key) != metadata.get(key) for key in semantic_fields)
        or (video.published_at is not None and article.published_at != video.published_at)
    )
    article.title, article.summary = video.title, video.description
    article.author = video.channel_title or None
    article.image_url = video.thumbnail_url
    # Analysis may normalize language or an operator may resolve missing provider
    # evidence. Unchanged provider metadata must retain that editorial value.
    if before.get("language") != metadata.get("language"):
        article.language = video.language
    if video.published_at is not None:
        article.published_at = video.published_at
    article.video_metadata = metadata
    article.content_format = "video"
    if changed:
        invalidate_editorial(article)
    content = session.get(ArticleContent, article.id)
    if content is None:
        content = ArticleContent(article_id=article.id)
        session.add(content)
    content.url, content.text = video.canonical_url, video.description
    content.content_hash, content.method = fingerprint(video.description), "youtube_metadata"
    content.retrieved_at = video.retrieved_at
    return changed


def store_videos(
    session,
    source,
    videos: dict[str, YouTubeVideo],
    memberships,
    generation: uuid.UUID,
    *,
    _allow_pending_direct=False,
    activity: list | None = None,
) -> tuple[int, int]:
    """Only an owned, approved-source ingestion transaction may attach origins."""
    pending_direct = (
        _allow_pending_direct
        and source.approval_status == "pending"
        and source.resource_kind == "video"
        and set(videos) == {source.provider_resource_id}
    )
    if (
        source.approval_status != "approved" and not pending_direct
    ) or source.provider != "youtube":
        raise ValueError("YouTube ingestion requires an approved provider source")
    from devfeed_core.analysis import request_analysis
    from devfeed_core.config import get_settings
    from devfeed_core.image_jobs import request_image

    by_video = {item.video_id: item for item in memberships}
    created = skipped = 0
    for video_id in sorted(by_video):
        new_article = False
        video = videos.get(video_id)
        article_id = session.scalar(
            select(Article.id).where(
                Article.provider == "youtube", Article.provider_resource_id == video_id
            )
        )
        canonical = f"https://www.youtube.com/watch?v={video_id}"
        if article_id is None:
            # A legacy RSS article with the canonical watch URL shares identity.
            article_id = session.scalar(
                select(Article.id).where(Article.url_hash == fingerprint(canonical))
            )
        if article_id is None and (video is None or video.availability != "public"):
            skipped += 1
            continue
        if article_id is None:
            assert video is not None
            article_id = session.scalar(
                insert(Article)
                .values(
                    canonical_url=canonical,
                    url_hash=fingerprint(canonical),
                    provider="youtube",
                    provider_resource_id=video_id,
                    title=video.title,
                    summary=video.description,
                    author=video.channel_title,
                    published_at=video.published_at,
                    feed_at=video.published_at or utcnow(),
                    image_url=video.thumbnail_url,
                    language=video.language,
                    content_type="article",
                    content_format="video",
                    metadata_source_type="publisher",
                    video_metadata=video.metadata,
                )
                .on_conflict_do_nothing()
                .returning(Article.id)
            )
            if article_id is not None:
                created += 1
                new_article = True
            else:
                article_id = session.scalar(
                    select(Article.id).where(
                        or_(
                            Article.url_hash == fingerprint(canonical),
                            (Article.provider == "youtube")
                            & (Article.provider_resource_id == video_id),
                        )
                    )
                )
        assert article_id is not None
        article = session.scalar(
            select(Article).where(Article.id == article_id).with_for_update(of=Article)
        )
        assert article is not None
        article.provider, article.provider_resource_id, article.content_format = (
            "youtube",
            video_id,
            "video",
        )
        changed = apply_video_metadata(session, article, video)
        item = by_video[video_id]
        if (
            activity is not None
            and session.scalar(
                select(ArticleOrigin.id).where(
                    ArticleOrigin.source_id == source.id, ArticleOrigin.entry_key == video_id
                )
            )
            is None
        ):
            activity.append(video.published_at if video else article.published_at)
        source_metadata = {
            "provider": "youtube",
            "resource_kind": source.resource_kind,
            "video_id": video_id,
            "playlist_item_id": item.item_id,
            "playlist_item_added_at": item.added_at.isoformat() if item.added_at else None,
            "channel_id": video.channel_id if video else article.video_metadata.get("channel_id"),
            "channel_title": video.channel_title
            if video
            else article.video_metadata.get("channel_title"),
        }
        session.execute(
            insert(ArticleOrigin)
            .values(
                article_id=article.id,
                source_id=source.id,
                entry_key=video_id,
                original_url=canonical,
                source_metadata=source_metadata,
                active=True,
            )
            .on_conflict_do_update(
                index_elements=[ArticleOrigin.source_id, ArticleOrigin.entry_key],
                set_={"active": True, "source_metadata": source_metadata},
            )
        )
        session.execute(
            insert(YouTubeMembership)
            .values(
                source_id=source.id,
                article_id=article.id,
                playlist_item_id=item.item_id[:128] if item.item_id else None,
                added_at=item.added_at,
                seen_generation=generation,
                active=True,
            )
            .on_conflict_do_update(
                index_elements=["source_id", "article_id"],
                set_={
                    "playlist_item_id": item.item_id[:128] if item.item_id else None,
                    "added_at": item.added_at,
                    "seen_generation": generation,
                    "active": True,
                },
            )
        )
        session.flush()
        if (
            source.approval_status == "approved"
            and video is not None
            and video.availability == "public"
        ):
            if (new_article or changed) and get_settings().ai_enabled and video.description:
                # Batched metadata is already the evidence. Reuse the analysis
                # outbox without another per-video metadata/network lookup.
                request_analysis(session, article.id, automatic=True)
            if get_settings().image_storage_enabled and article.image_url:
                request_image(session, article.id, automatic=True)
    return created, skipped


def store_direct_submission(session, source, video: YouTubeVideo) -> Article:
    """A pending single-video origin never subscribes to or approves its channel."""
    if (
        (source.provider, source.resource_kind, source.provider_resource_id)
        != ("youtube", "video", video.id)
        or source.approval_status not in {"pending", "approved"}
        or video.availability != "public"
    ):
        raise ValueError("Direct content submission requires its public single-video origin")
    store_videos(
        session,
        source,
        {video.id: video},
        [YouTubePlaylistItem(video.id, "", None)],
        uuid.uuid4(),
        _allow_pending_direct=True,
    )
    article = session.scalar(
        select(Article).where(
            Article.provider == "youtube", Article.provider_resource_id == video.id
        )
    )
    assert article is not None
    return article


def remove_memberships(session, source_id, article_ids):
    if not article_ids:
        return
    session.execute(
        update(YouTubeMembership)
        .where(
            YouTubeMembership.source_id == source_id,
            YouTubeMembership.article_id.in_(article_ids),
        )
        .values(active=False)
    )
    session.execute(
        update(ArticleOrigin)
        .where(
            ArticleOrigin.source_id == source_id,
            ArticleOrigin.article_id.in_(article_ids),
        )
        .values(active=False)
    )
