"""Provider submissions resolve stable resources without granting collection approval."""

from dataclasses import asdict

import pytest
from devfeed_core import services
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    IngestionJob,
    Source,
    SourceCandidate,
    SourceDiscoveryJob,
)
from devfeed_core.schemas import ArticleOut, SourceCreate, VideoMetadata
from devfeed_core.youtube import YouTubeClient, YouTubeResource, YouTubeVideo
from devfeed_core.youtube_submission import provider_error, resource_choices
from devfeed_user_api.auth import require_user
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient
from source_suggestions import identity
from sqlalchemy import func, select

CHANNEL = "UC" + "a" * 22
PLAYLIST = "PL" + "b" * 24
VIDEO = "A1234567890"
WATCH = f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}&t=100&utm_source=test"
PATH = "/v1/user/sources/suggestions"


@pytest.fixture
def youtube_provider(monkeypatch):
    calls = []

    def resolve(self, target):
        calls.append(target)
        identifier = CHANNEL if target.kind == "channel" else target.resource_id
        return YouTubeResource(
            kind=target.kind,
            resource_id=identifier,
            collection_id=("UU" + CHANNEL[2:])
            if target.kind == "channel"
            else identifier
            if target.kind == "playlist"
            else None,
            canonical_url=f"https://www.youtube.com/channel/{CHANNEL}"
            if target.kind == "channel"
            else target.canonical_url,
            title="Developer engineering",
            description="Software engineering tutorials and practical database examples.",
            channel_id=CHANNEL,
            channel_title="Developer engineering",
            thumbnail_url=f"https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg",
        )

    # Keep all HTTP outside these submission tests, including missing credentials.
    monkeypatch.setattr(
        YouTubeClient, "from_settings", classmethod(lambda cls: object.__new__(cls))
    )
    monkeypatch.setattr(YouTubeClient, "resolve", resolve)
    monkeypatch.setattr(
        YouTubeClient,
        "videos",
        lambda self, identifiers: {
            identifier: YouTubeVideo(
                id=identifier,
                title="Database query engineering",
                description="Software engineering tutorials and practical database examples.",
                channel_id=CHANNEL,
                channel_title="Developer engineering",
                published_at=None,
                language="en",
                duration_seconds=180,
            )
            for identifier in identifiers
        },
    )
    return calls


def test_preflight_resolves_handle_to_stable_identity_without_an_rss_url(youtube_provider):
    body = SourceCreate(feed_url="@developers", source_type="publisher")
    assert body.feed_url == "https://www.youtube.com/@developers"
    validated = services.validate_source(body)
    assert validated.provider == "youtube" and validated.resource_kind == "channel"
    assert validated.provider_resource_id == CHANNEL
    assert validated.provider_collection_id == "UU" + CHANNEL[2:]
    assert validated.feed_url is None and validated.recurring
    assert validated.website_url == f"https://www.youtube.com/channel/{CHANNEL}"
    assert "youtube_target" not in asdict(validated)


def test_watch_playlist_selection_is_explicit_and_default_never_subscribes(youtube_provider):
    choices = resource_choices(WATCH)
    assert [(choice.target, choice.canonical_url) for choice in choices] == [
        ("video", f"https://www.youtube.com/watch?v={VIDEO}"),
        ("playlist", f"https://www.youtube.com/playlist?list={PLAYLIST}"),
    ]
    direct = services.validate_source(SourceCreate(feed_url=WATCH, source_type="publisher"))
    assert direct.resource_kind == "video" and direct.provider_resource_id == VIDEO
    assert not direct.recurring and direct.provider_collection_id is None
    playlist = services.validate_source(
        SourceCreate(feed_url=WATCH, source_type="aggregator", youtube_target="playlist")
    )
    assert playlist.resource_kind == "playlist" and playlist.provider_resource_id == PLAYLIST
    assert playlist.recurring
    assert resource_choices("https://example.com/rss") == []


def test_rss_input_cannot_request_a_youtube_target():
    with pytest.raises(services.OperationConflict, match="requires a supported YouTube URL"):
        services.validate_source(
            SourceCreate(
                feed_url="https://example.com/rss",
                source_type="publisher",
                youtube_target="playlist",
            )
        )


@pytest.mark.parametrize(
    "reason,code,fragment",
    [
        ("youtube_not_configured", 503, "not configured"),
        ("youtube_quota_exhausted", 503, "quota"),
        ("youtube_request_budget", 503, "quota"),
        ("youtube_resource_unavailable", 422, "public"),
        ("youtube_transport_error", 422, "retrieved"),
    ],
)
def test_provider_errors_have_clear_credential_free_submission_states(reason, code, fragment):
    status, message = provider_error(FeedError("private provider diagnostic", reason=reason))
    assert status == code and fragment in message
    assert "private provider diagnostic" not in message


def test_public_video_contract_does_not_expose_internal_evidence_or_checkpoints():
    result = VideoMetadata.model_validate(
        {
            "channel_id": CHANNEL,
            "duration_seconds": 65,
            "availability": "public",
            "description": "Internal bounded evidence",
            "api_key": "private",
            "checkpoint": "private",
        }
    ).model_dump()
    assert set(result) == {
        "channel_id",
        "channel_title",
        "channel_url",
        "duration_seconds",
        "availability",
        "retrieved_at",
    }
    assert ArticleOut.model_fields["video_metadata"].default is None


@pytest.fixture
def youtube_user(database):
    app = create_app()
    app.dependency_overrides[require_user] = identity
    with TestClient(app) as client:
        yield client


@pytest.mark.integration
def test_reader_preview_and_direct_submission_never_subscribe_or_approve_channel(
    youtube_user, database, youtube_provider
):
    body = {"feed_url": WATCH, "source_type": "publisher"}
    preview = youtube_user.post(PATH + "/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["provider"] == "youtube" and preview.json()["resource_kind"] == "video"
    assert [item["target"] for item in preview.json()["resources"]] == ["video", "playlist"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 0
    result = youtube_user.post(PATH, json=body)
    assert result.status_code == 201, result.text
    with database() as session:
        source = session.scalar(select(Source))
        assert source.provider_resource_id == VIDEO and source.resource_kind == "video"
        assert not source.recurring and not source.enabled
        assert source.feed_url is None and source.approval_status == "pending"
        assert session.scalar(select(func.count()).select_from(Source)) == 1
        assert session.scalar(select(IngestionJob.id)) is None
        assert session.scalar(select(SourceDiscoveryJob.id)) is None
    duplicate = youtube_user.post(
        PATH, json={**body, "feed_url": f"https://youtu.be/{VIDEO}?si=tracking"}
    )
    assert duplicate.status_code == 409, duplicate.text


@pytest.mark.integration
def test_playlist_choice_and_channel_handle_are_distinct_reviewable_resources(
    youtube_user, admin_client, database, youtube_provider
):
    for url, target in ((WATCH, "playlist"), ("@developers", "channel")):
        result = youtube_user.post(
            PATH,
            json={
                "feed_url": url,
                "source_type": "publisher",
                "youtube_target": target,
            },
        )
        assert result.status_code == 201, result.text
        identifier = result.json()["id"]
        approved = admin_client.post(
            f"/v1/admin/sources/{identifier}/review", json={"decision": "approved"}
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["feed_url"] is None and approved.json()["recurring"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 2
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 2
        assert session.scalar(select(SourceDiscoveryJob.id)) is None


@pytest.mark.integration
def test_admin_resource_preview_create_deduplication_and_immutable_identity(
    admin_client, database, youtube_provider
):
    body = {"feed_url": WATCH, "source_type": "publisher", "youtube_target": "playlist"}
    preview = admin_client.post("/v1/admin/sources/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["resource_kind"] == "playlist"
    result = admin_client.post("/v1/admin/sources", json=body)
    assert result.status_code == 201, result.text
    assert result.json()["provider_resource_id"] == PLAYLIST
    identifier = result.json()["id"]
    assert admin_client.post("/v1/admin/sources", json=body).status_code == 409
    changed = admin_client.patch(
        f"/v1/admin/sources/{identifier}", json={"website_url": "https://example.com/"}
    )
    assert changed.status_code == 409
    assert (
        admin_client.patch(
            f"/v1/admin/sources/{identifier}", json={"name": "Engineering videos"}
        ).status_code
        == 200
    )


@pytest.mark.integration
def test_admin_import_routes_provider_urls_to_normal_review_and_keeps_provenance(
    admin_client, database, youtube_provider
):
    body = {
        "format": "urls",
        "content": f"{WATCH}\nhttps://youtu.be/{VIDEO}\nhttps://www.youtube.com/@developers",
    }
    result = admin_client.post("/v1/admin/source-imports", json=body)
    assert result.status_code == 201, result.text
    assert result.json() == {"created": 2, "existing": 0, "total": 2}
    repeated = admin_client.post("/v1/admin/source-imports", json=body)
    assert repeated.status_code == 201 and repeated.json()["created"] == 0
    with database() as session:
        rows = session.scalars(select(Source)).all()
        assert len(rows) == 2 and all(
            row.approval_status == "pending" and not row.enabled for row in rows
        )
        assert session.scalar(select(func.count()).select_from(SourceCandidate)) == 2
        assert session.scalar(select(SourceDiscoveryJob.id)) is None
        assert session.scalar(select(IngestionJob.id)) is None


@pytest.mark.integration
def test_remote_youtube_resource_import_never_downloads_watch_html(
    admin_client, monkeypatch, youtube_provider
):
    from devfeed_core.discovery_crawler import CrawlSession

    monkeypatch.setattr(
        CrawlSession, "get", lambda *_: pytest.fail("YouTube HTML must not be fetched")
    )
    result = admin_client.post("/v1/admin/source-imports", json={"format": "urls", "url": WATCH})
    assert result.status_code == 201 and result.json()["created"] == 1


@pytest.mark.integration
def test_admin_content_submission_keeps_single_video_identity_and_channel_unapproved(
    admin_client, database, youtube_provider
):
    channel = admin_client.post(
        "/v1/admin/sources",
        json={
            "feed_url": "@developers",
            "source_type": "publisher",
        },
    )
    assert channel.status_code == 201, channel.text
    body = {
        "canonical_url": WATCH,
        "source_id": channel.json()["id"],
        "title": "Unverified submitted title",
        "summary": "Unverified submitted description",
    }
    response = admin_client.post("/v1/admin/articles", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["provider_resource_id"] == VIDEO
    assert (
        response.json()["content_format"] == "video"
        and response.json()["review_status"] == "pending"
    )
    assert response.json()["title"] == "Database query engineering"
    repeated = admin_client.post(
        "/v1/admin/articles", json={**body, "canonical_url": f"https://youtu.be/{VIDEO}?t=5"}
    )
    assert repeated.status_code == 201 and repeated.json()["id"] == response.json()["id"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 1
        sources = session.scalars(select(Source)).all()
        assert len(sources) == 2 and all(
            row.approval_status == "pending" and not row.enabled for row in sources
        )
        assert session.scalar(select(IngestionJob.id)) is None
        assert session.scalar(select(ArticleAnalysisJob.id)) is None
