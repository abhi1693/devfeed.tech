"""YouTube identities, bounded provider reads and redacted error behavior."""

import json
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_core import youtube
from devfeed_core.feeds.fetcher import PublicNetworkBackend

VIDEO = "abcdefghijk"
OTHER = "lmnopqrstuv"
CHANNEL = "UC" + "a" * 22
PLAYLIST = "PL" + "b" * 22


def video_item(identifier=VIDEO, **changes):
    return {
        "id": identifier,
        "snippet": {
            "title": "TypeScript generics <T>",
            "description": "Implement type-safe generic functions using <T>.",
            "channelId": CHANNEL,
            "channelTitle": "Engineering",
            "publishedAt": "2026-10-01T12:00:00Z",
            "defaultAudioLanguage": "en-US",
            "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{identifier}/hqdefault.jpg"}},
        },
        "contentDetails": {"duration": "PT1H2M3S"},
        "status": {"privacyStatus": "public", "uploadStatus": "processed"},
        **changes,
    }


def playlist_item(identifier=VIDEO, item_id="membership-1"):
    return {
        "id": item_id,
        "snippet": {"publishedAt": "2026-10-15T12:00:00Z", "resourceId": {"videoId": identifier}},
        "contentDetails": {"videoId": identifier},
    }


@pytest.fixture
def transport(monkeypatch):
    replies, requests, backends = [], [], []

    class Response:
        def __init__(self, data, status=200, headers=None):
            self.status = status
            self.headers = headers or [(b"content-type", b"application/json")]
            self.body = json.dumps(data).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def iter_stream(self):
            yield self.body

    class Pool:
        def __init__(self, **kwargs):
            backends.append(kwargs["network_backend"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def stream(self, method, url, **kwargs):
            requests.append((url, kwargs))
            data = replies.pop(0)
            return Response(*data) if isinstance(data, tuple) else Response(data)

    monkeypatch.setattr(youtube.httpcore, "ConnectionPool", Pool)
    return replies, requests, backends


@pytest.mark.parametrize(
    "url",
    [
        f"https://youtu.be/{VIDEO}?t=12",
        f"https://m.youtube.com/watch?v={VIDEO}&utm_source=x",
        f"https://www.youtube.com/shorts/{VIDEO}",
        f"https://youtube.com/embed/{VIDEO}",
        f"https://music.youtube.com/watch?v={VIDEO}&list={PLAYLIST}",
    ],
)
def test_video_url_variants_have_one_identity(url):
    target = youtube.parse_youtube_url(url)
    assert target.kind == "video" and target.resource_id == VIDEO
    assert target.canonical_url == f"https://www.youtube.com/watch?v={VIDEO}"


def test_watch_playlist_target_must_be_explicit_and_detected_resources_are_preserved():
    url = f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}"
    assert youtube.parse_youtube_url(url).kind == "video"
    selected = youtube.parse_youtube_url(url, target="playlist")
    assert selected.kind == "playlist" and selected.resource_id == PLAYLIST
    assert (selected.video_id, selected.playlist_id) == (VIDEO, PLAYLIST)
    with pytest.raises(ValueError, match="selected"):
        youtube.parse_youtube_url(f"https://youtu.be/{VIDEO}", target="playlist")


@pytest.mark.parametrize(
    "url,lookup,value",
    [
        ("@Engineering", "handle", "@Engineering"),
        ("https://youtube.com/@Engineering/videos", "handle", "@Engineering"),
        (f"https://youtube.com/channel/{CHANNEL}", "id", CHANNEL),
        ("https://youtube.com/user/Engineering", "username", "Engineering"),
    ],
)
def test_channel_variants_preserve_resolvable_identity(url, lookup, value):
    target = youtube.parse_youtube_url(url)
    assert (target.kind, target.lookup, target.resource_id) == ("channel", lookup, value)


@pytest.mark.parametrize(
    "url",
    [
        f"https://youtube.com/watch?v={VIDEO}&v={OTHER}",
        "https://youtube.com/watch?v=bad",
        "https://youtube.com/c/ambiguous-legacy-name",
        "https://youtube.com/playlist?list=private%20id",
    ],
)
def test_ambiguous_or_unsupported_resource_is_rejected(url):
    with pytest.raises(ValueError):
        youtube.parse_youtube_url(url)


def test_unrelated_public_hosts_are_not_youtube_resources():
    assert youtube.parse_youtube_url("https://youtube.com.evil.example/watch?v=abcdefghijk") is None


def test_api_transport_has_fixed_host_guarded_dns_and_header_only_credentials(transport):
    replies, requests, backends = transport
    replies.append({"items": [video_item()]})
    result = youtube.YouTubeClient("never-log-this-key").videos([VIDEO])[VIDEO]
    assert result.title.endswith("<T>") and result.description.endswith("<T>.")
    assert result.duration_seconds == 3723 and result.language == "en-us"
    assert result.metadata["default_audio_language"] == "en-us"
    assert result.metadata["language"] == "en-us"
    from devfeed_core.video_content import video_language

    assert video_language(type("Video", (), {"video_metadata": result.metadata})()) == "en-us"
    assert all(isinstance(backend, PublicNetworkBackend) for backend in backends)
    url, arguments = requests[0]
    assert urlsplit(url).hostname == "www.googleapis.com" and "never-log-this-key" not in url
    assert arguments["headers"]["X-Goog-Api-Key"] == "never-log-this-key"
    assert parse_qs(urlsplit(url).query)["id"] == [VIDEO]


def test_resolve_handle_reads_uploads_without_search(transport):
    replies, requests, _ = transport
    replies.append(
        {
            "items": [
                {
                    "id": CHANNEL,
                    "snippet": {
                        "title": "Engineering",
                        "description": "Software engineering videos",
                    },
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU" + "a" * 22}},
                }
            ]
        }
    )
    resource = youtube.YouTubeClient("test").resolve(youtube.parse_youtube_url("@Engineering"))
    assert resource.resource_id == CHANNEL and resource.collection_id.startswith("UU")
    assert parse_qs(urlsplit(requests[0][0]).query)["forHandle"] == ["@Engineering"]
    assert "/channels?" in requests[0][0]


def test_provider_missing_private_video_is_not_replaced_with_invented_metadata(transport):
    replies, _, _ = transport
    replies.append({"items": []})
    assert youtube.YouTubeClient("test").videos([VIDEO]) == {}


@pytest.mark.parametrize("reason", ["quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"])
def test_quota_errors_are_retryable_bounded_and_never_expose_provider_messages(transport, reason):
    replies, _, _ = transport
    replies.append(
        ({"error": {"message": "key=private-secret", "errors": [{"reason": reason}]}}, 403)
    )
    with pytest.raises(youtube.YouTubeError) as caught:
        youtube.YouTubeClient("private-secret").videos([VIDEO])
    assert caught.value.reason == "youtube_quota_exhausted" and caught.value.retryable
    assert caught.value.retry_after >= 3600 and "private-secret" not in str(caught.value)


def test_missing_config_budget_and_oversized_batches_do_not_make_requests(transport):
    replies, requests, _ = transport
    with pytest.raises(youtube.YouTubeError, match="not configured"):
        youtube.YouTubeClient(None)
    with pytest.raises(ValueError):
        youtube.YouTubeClient("test").videos([f"{number:011d}" for number in range(51)])
    with pytest.raises(youtube.YouTubeError, match="budget"):
        youtube.YouTubeClient("test", request_limit=0).videos([VIDEO])
    assert requests == [] and replies == []


@pytest.mark.parametrize(
    "reply,status,reason",
    [
        ({"items": []}, 302, "youtube_redirect"),
        ({"missing": []}, 200, "youtube_invalid_response"),
        ({"items": [None]}, 200, "youtube_invalid_response"),
    ],
)
def test_redirects_and_partial_metadata_never_become_success(transport, reply, status, reason):
    replies, _, _ = transport
    replies.append((reply, status))
    with pytest.raises(youtube.YouTubeError) as caught:
        youtube.YouTubeClient("test").videos([VIDEO])
    assert caught.value.reason == reason


def test_playlist_pages_keep_insertion_time_separate_and_reject_stuck_cursor(transport):
    replies, _, _ = transport
    replies.append({"items": [playlist_item()], "nextPageToken": "next"})
    client = youtube.YouTubeClient("test")
    page = client.playlist_items(PLAYLIST)
    assert page.items[0].added_at.isoformat().startswith("2026-10-15")
    assert page.next_page_token == "next"
    replies.append({"items": [playlist_item()], "nextPageToken": "next"})
    with pytest.raises(youtube.YouTubeError, match="advance"):
        client.playlist_items(PLAYLIST, page_token="next")


def test_video_retrieval_time_does_not_change_semantic_metadata(transport):
    replies, _, _ = transport
    replies.append({"items": [video_item()]})
    video = youtube.YouTubeClient("test").videos([VIDEO])[VIDEO]
    other = replace(
        video, retrieved_at=video.retrieved_at.replace(year=video.retrieved_at.year + 1)
    )
    assert {key: value for key, value in video.metadata.items() if key != "retrieved_at"} == {
        key: value for key, value in other.metadata.items() if key != "retrieved_at"
    }


@pytest.mark.parametrize("field", ["snippet", "contentDetails", "status"])
@pytest.mark.parametrize("bad", [None, [], "malformed"])
def test_malformed_nested_video_metadata_is_a_retryable_provider_error(transport, field, bad):
    replies, _, _ = transport
    replies.append({"items": [video_item(**{field: bad})]})
    with pytest.raises(youtube.YouTubeError) as caught:
        youtube.YouTubeClient("test").videos([VIDEO])
    assert caught.value.reason == "youtube_invalid_response" and caught.value.retryable


@pytest.mark.parametrize("value", [None, 123, [], "PT", "P0D", "PT" + "9" * 101 + "S"])
def test_invalid_or_unknown_duration_never_fabricates_a_length(transport, value):
    replies, _, _ = transport
    replies.append({"items": [video_item(contentDetails={"duration": value})]})
    assert youtube.YouTubeClient("test").videos([VIDEO])[VIDEO].duration_seconds is None


@pytest.mark.parametrize("field", ["id", "channelId"])
def test_non_string_provider_id_is_rejected_as_incomplete_metadata(transport, field):
    replies, _, _ = transport
    item = video_item()
    if field == "id":
        item[field] = None
    else:
        item["snippet"][field] = None
    replies.append({"items": [item]})
    with pytest.raises(youtube.YouTubeError) as caught:
        youtube.YouTubeClient("test").videos([VIDEO])
    assert caught.value.reason == "youtube_invalid_response"
