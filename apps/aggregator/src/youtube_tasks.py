"""Bounded head discovery, resumable collection scans and availability refresh."""

import math
import uuid
from contextlib import suppress
from dataclasses import dataclass

from devfeed_core.config import get_settings
from devfeed_core.models import Article, YouTubeMembership, utcnow
from devfeed_core.youtube import (
    YouTubeClient,
    YouTubeError,
    YouTubePlaylistItem,
    resource_from_source,
)
from devfeed_core.youtube_ingestion import apply_video_metadata, remove_memberships, store_videos
from sqlalchemy import select


@dataclass(frozen=True)
class YouTubeRefresh:
    state: dict
    memberships: list
    videos: dict
    revalidated_ids: list[str]
    removed_article_ids: list[uuid.UUID]


def source_snapshot(source) -> dict:
    return {
        "id": source.id,
        "resource": resource_from_source(source),
        "state": dict(source.provider_state or {}),
        "initial": source.last_success_at is None,
    }


def _known(session, source_id, after, *, generation=None, limit=50):
    statement = (
        select(Article.id, Article.provider_resource_id)
        .join(YouTubeMembership, YouTubeMembership.article_id == Article.id)
        .where(YouTubeMembership.source_id == source_id, YouTubeMembership.active.is_(True))
    )
    if generation is not None:
        statement = statement.where(YouTubeMembership.seen_generation.is_distinct_from(generation))
    if after:
        with suppress(ValueError, TypeError):
            statement = statement.where(Article.id > uuid.UUID(after))
    return session.execute(statement.order_by(Article.id).limit(limit)).all()


def fetch_refresh(factory, snapshot) -> YouTubeRefresh:
    """All provider requests run without an open database transaction."""
    client = YouTubeClient.from_settings()
    settings = get_settings()
    resource, state = snapshot["resource"], dict(snapshot["state"])
    if resource.kind == "video":
        item = YouTubePlaylistItem(resource.resource_id, "", None)
        return YouTubeRefresh(
            {"status": "ready", "last_refresh_at": utcnow().isoformat()},
            [item],
            client.videos([resource.resource_id]),
            [resource.resource_id],
            [],
        )
    generation = uuid.UUID(state["generation"]) if state.get("generation") else uuid.uuid4()
    with factory() as session:
        known = _known(session, snapshot["id"], state.get("availability_after"))
        if not known and state.get("availability_after"):
            known = _known(session, snapshot["id"], None)
    phase = state.get("phase", "scan")
    limit = (
        settings.youtube_initial_import_limit
        if snapshot["initial"]
        else settings.youtube_refresh_item_limit
    )
    # Reserve part of each pass for checkpoint progress; an endless sequence of
    # new uploads must not permanently starve history or removal reconciliation.
    head_limit = min(10, max(1, limit // 2))
    head = client.playlist_items(resource.collection_id, max_results=head_limit)
    memberships = {item.video_id: item for item in head.items}
    if not state.get("generation"):
        token = head.next_page_token
        phase = "scan" if token else "reconcile"
    else:
        token = state.get("page_token")
    remaining = max(0, limit - len(head.items))
    visited = set()
    while phase == "scan" and token and remaining:
        page_size = min(50, remaining)
        metadata_budget = math.ceil((len(memberships) + page_size + len(known)) / 50)
        if client.requests + 1 + metadata_budget > client.request_limit:
            break
        if token in visited:
            raise YouTubeError(
                "YouTube pagination cycle rejected",
                reason="youtube_invalid_pagination",
                retryable=True,
            )
        visited.add(token)
        try:
            page = client.playlist_items(
                resource.collection_id, page_token=token, max_results=page_size
            )
        except YouTubeError as exc:
            if exc.reason != "youtube_invalid_page_token":
                raise
            # Provider tokens can expire when collections change. Restart a
            # generation at the current head; never reconcile an incomplete tail.
            generation = uuid.uuid4()
            token = head.next_page_token
            phase = "scan" if token else "reconcile"
            state["reconcile_after"] = None
            break
        memberships.update((item.video_id, item) for item in page.items)
        remaining -= len(page.items)
        token = page.next_page_token
        if not token:
            phase = "reconcile"
    with factory() as session:
        capacity = max(
            0,
            min(
                10,
                client.request_limit
                - client.requests
                - math.ceil((len(memberships) + len(known) + 10) / 50),
            ),
        )
        candidates = (
            _known(
                session,
                snapshot["id"],
                state.get("reconcile_after"),
                generation=generation,
                limit=capacity,
            )
            if phase == "reconcile" and capacity
            else None
        )
    removed = []
    reconcile_after = state.get("reconcile_after")
    for article_id, video_id in candidates or []:
        if video_id in memberships:
            reconcile_after = str(article_id)
            continue
        # Pagination is not a snapshot. Confirm each apparent removal directly,
        # so playlist reordering cannot withdraw a still-present membership.
        check = client.playlist_items(resource.collection_id, max_results=1, video_id=video_id)
        if check.items:
            memberships[video_id] = check.items[0]
        elif check.next_page_token is None:
            removed.append(article_id)
        reconcile_after = str(article_id)
    if phase == "reconcile" and candidates == []:
        generation = uuid.uuid4()
        # The current head starts the next complete sweep, never a partial tail.
        token = head.next_page_token
        phase = "scan" if token else "reconcile"
        reconcile_after = None
    identifiers = sorted(set(memberships) | {video_id for _, video_id in known})
    videos = {}
    for offset in range(0, len(identifiers), 50):
        videos.update(client.videos(identifiers[offset : offset + 50]))
    state = {
        "status": "ready",
        "generation": str(generation),
        "phase": phase,
        "page_token": token,
        "reconcile_after": reconcile_after,
        "availability_after": str(known[-1][0]) if len(known) == 50 else None,
        "last_refresh_at": utcnow().isoformat(),
    }
    return YouTubeRefresh(
        state, list(memberships.values()), videos, [video_id for _, video_id in known], removed
    )


def apply_refresh(session, source, refresh, *, activity: list | None = None) -> tuple[int, int]:
    generation = (
        uuid.UUID(refresh.state["generation"]) if refresh.state.get("generation") else uuid.uuid4()
    )
    created, skipped = store_videos(
        session, source, refresh.videos, refresh.memberships, generation, activity=activity
    )
    imported = {item.video_id for item in refresh.memberships}
    for video_id in sorted(set(refresh.revalidated_ids) - imported):
        article = session.scalar(
            select(Article)
            .where(Article.provider == "youtube", Article.provider_resource_id == video_id)
            .with_for_update(of=Article)
        )
        if article is not None:
            apply_video_metadata(session, article, refresh.videos.get(video_id))
    remove_memberships(session, source.id, refresh.removed_article_ids)
    source.provider_state = refresh.state
    return created, skipped
