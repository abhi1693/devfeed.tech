"""Disposable persistence, leases, collection checkpoints and revalidation."""

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from devfeed_aggregator import tasks, youtube_tasks
from devfeed_core.config import get_settings
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleOrigin,
    IngestionJob,
    Source,
    YouTubeMembership,
    utcnow,
)
from devfeed_core.youtube import YouTubeError, YouTubePage, YouTubePlaylistItem, YouTubeVideo
from devfeed_core.youtube_ingestion import (
    apply_video_metadata,
    remove_memberships,
    store_direct_submission,
    store_videos,
)
from devfeed_core.youtube_revalidation import schedule_youtube_revalidation
from pydantic import SecretStr
from sqlalchemy import func, select
from test_youtube import CHANNEL, PLAYLIST, VIDEO

pytestmark = pytest.mark.integration


def video(identifier=VIDEO):
    return YouTubeVideo(
        identifier,
        "TypeScript generics",
        "Implement generic functions with TypeScript type parameters.",
        CHANNEL,
        "Engineering",
        utcnow() - timedelta(days=2),
        duration_seconds=300,
        language="en",
    )


def source(database, *, kind="playlist", identifier=PLAYLIST, approved=True):
    with database.begin() as session:
        record = Source(
            name=f"YouTube {identifier}",
            provider="youtube",
            resource_kind=kind,
            provider_resource_id=identifier,
            provider_collection_id=identifier
            if kind == "playlist"
            else "UU" + "a" * 22
            if kind == "channel"
            else None,
            recurring=kind != "video",
            feed_url=None,
            website_url=f"https://www.youtube.com/playlist?list={identifier}"
            if kind == "playlist"
            else f"https://www.youtube.com/watch?v={identifier}",
            source_type="publisher",
            approval_status="approved" if approved else "pending",
        )
        session.add(record)
        session.flush()
        return record.id


def import_video(database, source_id, value=None):
    value = value or video()
    with database.begin() as session:
        origin = session.get(Source, source_id)
        result = store_videos(
            session,
            origin,
            {value.id: value},
            [YouTubePlaylistItem(value.id, "membership", utcnow())],
            uuid.uuid4(),
        )
        return result, session.scalar(
            select(Article.id).where(Article.provider_resource_id == value.id)
        )


def run_job(database, source_id):
    with database.begin() as session:
        origin = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
        job_id = request_ingestion(session, origin).id
    tasks.ingest(str(job_id))
    return job_id


class Provider:
    def __init__(self, identifiers):
        self.items = [
            YouTubePlaylistItem(identifier, f"membership-{identifier}", utcnow())
            for identifier in identifiers
        ]
        self.values = {identifier: video(identifier) for identifier in identifiers}
        self.calls = []
        self.requests = 0
        self.request_limit = 20
        self.error = None
        self.invalidate_token = False

    def reset(self):
        self.requests = 0

    def _request(self):
        self.requests += 1
        if self.error:
            raise self.error
        assert self.requests <= self.request_limit

    def playlist_items(self, collection_id, page_token=None, max_results=50, *, video_id=None):
        self._request()
        self.calls.append((collection_id, page_token, video_id))
        if video_id is not None:
            return YouTubePage(
                [item for item in self.items if item.video_id == video_id][:max_results], None
            )
        if self.invalidate_token and page_token:
            self.invalidate_token = False
            raise YouTubeError("Expired page", reason="youtube_invalid_page_token")
        start = int(page_token[1:]) if page_token else 0
        items = self.items[start : start + max_results]
        end = start + len(items)
        return YouTubePage(items, f"p{end}" if end < len(self.items) else None)

    def videos(self, identifiers):
        self._request()
        return {
            identifier: self.values[identifier]
            for identifier in identifiers
            if identifier in self.values
        }


@pytest.fixture
def provider(monkeypatch):
    remote = Provider([f"{index:011d}" for index in range(8)])

    def create():
        remote.reset()
        return remote

    monkeypatch.setattr(youtube_tasks.YouTubeClient, "from_settings", create)
    monkeypatch.setattr(get_settings(), "youtube_initial_import_limit", 4)
    monkeypatch.setattr(get_settings(), "youtube_refresh_item_limit", 4)
    return remote


def test_video_identity_is_shared_across_sources_and_retries(database):
    first, second = source(database), source(database, kind="video", identifier=VIDEO)
    assert import_video(database, first)[0] == (1, 0)
    assert import_video(database, first)[0] == (0, 0)
    assert import_video(database, second)[0] == (0, 0)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 1
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 2
        assert session.scalar(select(func.count()).select_from(YouTubeMembership)) == 2
        article = session.scalar(select(Article))
        assert article.content_format == "video" and article.provider_resource_id == VIDEO
        assert session.get(ArticleContent, article.id).text == video().description
        assert article.video_metadata["language"] == "en"


def test_pending_single_video_submission_is_idempotent_and_does_not_queue_jobs(database):
    identifier = source(database, kind="video", identifier=VIDEO, approved=False)
    with database.begin() as session:
        origin = session.get(Source, identifier)
        article = store_direct_submission(session, origin, video())
        article_id = article.id
        assert store_direct_submission(session, origin, video()).id == article_id
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 1
        assert session.scalar(select(func.count()).select_from(ArticleEnrichmentJob)) == 0
        article = session.get(Article, article_id)
        assert (article.review_status, article.publication_status) == ("pending", "unpublished")
        assert session.get(Source, identifier).approval_status == "pending"
        assert article.video_metadata["channel_id"] == CHANNEL


def test_existing_canonical_rss_article_is_adopted_without_changing_identity(database):
    origin = source(database)
    value = video()
    with database.begin() as session:
        from devfeed_core.urls import fingerprint

        article = Article(
            canonical_url=value.canonical_url,
            url_hash=fingerprint(value.canonical_url),
            title="Old feed metadata",
        )
        session.add(article)
        session.flush()
        identifier = article.id
    assert import_video(database, origin)[1] == identifier
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 1
        assert session.get(Article, identifier).provider == "youtube"


def test_playlist_insertion_reordering_and_retrieval_do_not_renew_article_dates(database):
    origin = source(database)
    value = video()
    _, identifier = import_video(database, origin, value)
    with database() as session:
        article = session.get(Article, identifier)
        dates = article.published_at, article.feed_at, article.discovered_at
        revision = article.editorial_revision
    import_video(database, origin, replace(value, retrieved_at=utcnow()))
    with database() as session:
        article = session.get(Article, identifier)
        assert (article.published_at, article.feed_at, article.discovered_at) == dates
        assert article.editorial_revision == revision


@pytest.mark.parametrize("provider_language,reviewed_language", [("en-us", "en"), (None, "en")])
def test_unchanged_provider_refresh_preserves_reviewed_language_and_publication(
    database, provider_language, reviewed_language
):
    origin = source(database)
    value = replace(video(), language=provider_language)
    _, identifier = import_video(database, origin, value)
    with database.begin() as session:
        article = session.get(Article, identifier)
        article.language = reviewed_language
        article.review_status, article.publication_status = "approved", "published"
        revision = article.editorial_revision
    import_video(database, origin, replace(value, retrieved_at=utcnow()))
    with database() as session:
        article = session.get(Article, identifier)
        assert article.language == reviewed_language
        assert article.editorial_revision == revision and article.publication_status == "published"


def test_removal_retracts_only_one_origin_and_reappearance_restores_it(database):
    first, second = source(database), source(database, kind="video", identifier=VIDEO)
    _, identifier = import_video(database, first)
    import_video(database, second)
    with database.begin() as session:
        remove_memberships(session, first, [identifier])
    with database() as session:
        origins = {row.source_id: row.active for row in session.scalars(select(ArticleOrigin))}
        assert origins == {first: False, second: True}
        assert not session.get(YouTubeMembership, (first, identifier)).active
    import_video(database, first)
    with database() as session:
        assert session.get(YouTubeMembership, (first, identifier)).active


def test_missing_video_is_hidden_without_erasing_evidence_or_origin(database):
    origin = source(database)
    _, identifier = import_video(database, origin)
    with database.begin() as session:
        article = session.get(Article, identifier)
        article.review_status, article.publication_status = "approved", "published"
        session.flush()
        assert apply_video_metadata(session, article, None)
    with database() as session:
        article = session.get(Article, identifier)
        assert article.video_metadata["availability"] == "unavailable"
        assert article.publication_status == "unpublished"
        assert session.get(ArticleContent, identifier).text
        assert session.scalar(select(ArticleOrigin.active)).__bool__()


def test_ingestion_preserves_checkpoint_and_eventually_scans_history(database, provider):
    identifier = source(database)
    for expected in (4, 6, 8):
        job_id = run_job(database, identifier)
        with database() as session:
            assert session.get(IngestionJob, job_id).status == "succeeded"
            assert session.scalar(select(func.count()).select_from(Article)) == expected
            assert session.get(Source, identifier).provider_state["status"] == "ready"
        assert provider.requests <= 20
    assert any(token is not None for _, token, _ in provider.calls)


def test_quota_does_not_advance_checkpoint_or_hide_retained_videos(database, provider):
    identifier = source(database)
    run_job(database, identifier)
    with database() as session:
        before = session.get(Source, identifier).provider_state
    provider.error = YouTubeError(
        "YouTube metadata quota exhausted",
        reason="youtube_quota_exhausted",
        status=403,
        retryable=True,
        retry_after=3600,
    )
    job_id = run_job(database, identifier)
    with database() as session:
        job = session.get(IngestionJob, job_id)
        assert job.status == "queued" and job.available_at > utcnow() + timedelta(minutes=59)
        state = session.get(Source, identifier).provider_state
        assert (
            state["page_token"] == before["page_token"]
            and state["error_code"] == "youtube_quota_exhausted"
        )
        assert all(
            row.video_metadata["availability"] == "public"
            for row in session.scalars(select(Article))
        )


def test_expired_checkpoint_restarts_generation_without_removing_memberships(database, provider):
    identifier = source(database)
    run_job(database, identifier)
    with database() as session:
        previous = session.get(Source, identifier).provider_state["generation"]
    provider.invalidate_token = True
    job_id = run_job(database, identifier)
    with database() as session:
        assert session.get(IngestionJob, job_id).status == "succeeded"
        assert session.get(Source, identifier).provider_state["generation"] != previous
        assert all(row.active for row in session.scalars(select(YouTubeMembership)))


def test_direct_source_only_refreshes_video_availability(database, provider):
    value = next(iter(provider.values.values()))
    identifier = source(database, kind="video", identifier=value.id)
    run_job(database, identifier)
    assert provider.calls == []
    provider.values.clear()
    run_job(database, identifier)
    with database() as session:
        article = session.scalar(select(Article))
        assert article.video_metadata["availability"] == "unavailable"
        assert not session.get(Source, identifier).recurring
        assert session.scalar(select(func.count()).select_from(Source)) == 1


def test_stale_revalidation_is_bounded_coalesced_and_respects_active_approved_origins(
    database, monkeypatch
):
    monkeypatch.setattr(get_settings(), "youtube_api_key", SecretStr("test-no-network"))
    monkeypatch.setattr(get_settings(), "youtube_revalidation_batch_size", 1)
    origin = source(database)
    old = replace(video(), retrieved_at=utcnow() - timedelta(days=2))
    _, identifier = import_video(database, origin, old)
    assert schedule_youtube_revalidation(database) == 1
    assert schedule_youtube_revalidation(database) == 0
    with database.begin() as session:
        job = session.scalar(select(ArticleEnrichmentJob))
        job.status, job.finished_at, job.error = "failed", utcnow(), "YouTube quota exhausted"
    assert schedule_youtube_revalidation(database) == 0
    with database.begin() as session:
        job = session.scalar(select(ArticleEnrichmentJob))
        job.created_at = job.finished_at = utcnow() - timedelta(days=2)
        session.get(YouTubeMembership, (origin, identifier)).active = False
        session.scalar(select(ArticleOrigin)).active = False
    assert schedule_youtube_revalidation(database) == 0


@pytest.mark.parametrize("withdrawal", ["membership", "approval", "availability"])
def test_public_feed_and_detail_withhold_withdrawn_videos(client, database, withdrawal):
    from devfeed_core.publication import visible_article
    from devfeed_core.search_records import hit, public_records

    first = source(database)
    second = source(database, kind="video", identifier=VIDEO)
    _, identifier = import_video(database, first)
    import_video(database, second)
    with database.begin() as session:
        article = session.get(Article, identifier)
        article.review_status, article.publication_status = "approved", "published"
        article.slug = "tested-developer-video"
    with database() as session:
        records = public_records(session, "articles", [identifier])
        search_hit = hit("articles", records[identifier])
        assert search_hit["published_at"] == records[identifier]["published_at"].isoformat()
        assert search_hit["video_metadata"]["channel_title"] == "Engineering"
    response = client.get("/v1/feed")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["items"]] == [str(identifier)]
    value = client.get("/v1/articles/tested-developer-video")
    assert value.status_code == 200
    assert value.json()["content_format"] == "video"
    assert value.json()["video_metadata"]["channel_title"] == "Engineering"
    with database.begin() as session:
        remove_memberships(session, first, [identifier])
    # A second approved origin retains visibility after playlist withdrawal.
    assert client.get("/v1/articles/tested-developer-video").status_code == 200
    with database.begin() as session:
        if withdrawal == "membership":
            remove_memberships(session, second, [identifier])
        elif withdrawal == "approval":
            session.get(Source, second).approval_status = "pending"
        else:
            # Even stale editorial status cannot leak unavailable provider content.
            article = session.get(Article, identifier)
            article.video_metadata = {**article.video_metadata, "availability": "unavailable"}
    assert client.get("/v1/feed").json()["items"] == []
    assert client.get("/v1/articles/tested-developer-video").status_code == 404
    with database() as session:
        assert session.scalars(select(Article).where(visible_article())).all() == []
        assert public_records(session, "articles", [identifier]) == {}


def test_youtube_polling_counts_new_origins_without_recounting_refreshes(database, provider):
    identifier = source(database)
    job_id = run_job(database, identifier)
    with database() as session:
        assert session.get(IngestionJob, job_id).new_source_entries == 4
    next_id = run_job(database, identifier)
    with database() as session:
        assert session.get(IngestionJob, next_id).new_source_entries == 2
    run_job(database, identifier)
    final_id = run_job(database, identifier)
    with database() as session:
        assert session.get(IngestionJob, final_id).new_source_entries == 0
