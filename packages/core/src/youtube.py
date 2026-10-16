"""Bounded public YouTube metadata API; no watch-page scraping or search calls."""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import httpcore

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, PublicNetworkBackend, retry_after_seconds
from devfeed_core.models import utcnow
from devfeed_core.urls import validate_public_url

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
RESOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{2,128}$")
API_BASE = "https://www.googleapis.com/youtube/v3/"


class YouTubeError(FeedError):
    pass


@dataclass(frozen=True)
class YouTubeTarget:
    kind: str
    resource_id: str
    lookup: str = "id"
    canonical_url: str = ""
    video_id: str | None = None
    playlist_id: str | None = None


@dataclass(frozen=True)
class YouTubeResource:
    kind: str
    resource_id: str
    collection_id: str | None
    canonical_url: str
    title: str = ""
    description: str = ""
    channel_id: str = ""
    channel_title: str = ""
    thumbnail_url: str | None = None


@dataclass(frozen=True)
class YouTubeVideo:
    id: str
    title: str
    description: str
    channel_id: str
    channel_title: str
    published_at: datetime | None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    language: str | None = None
    availability: str = "public"
    retrieved_at: datetime = field(default_factory=utcnow)
    default_audio_language: str | None = None
    default_language: str | None = None

    @property
    def canonical_url(self):
        return f"https://www.youtube.com/watch?v={self.id}"

    @property
    def channel_url(self):
        return f"https://www.youtube.com/channel/{self.channel_id}"

    @property
    def metadata(self):
        return {
            "channel_id": self.channel_id,
            "channel_title": self.channel_title,
            "channel_url": self.channel_url,
            "duration_seconds": self.duration_seconds,
            "availability": self.availability,
            "retrieved_at": self.retrieved_at.isoformat(),
            "description": self.description,
            "default_audio_language": self.default_audio_language,
            "default_language": self.default_language,
            "language": self.language,
        }


@dataclass(frozen=True)
class YouTubePlaylistItem:
    video_id: str
    item_id: str
    added_at: datetime | None


@dataclass(frozen=True)
class YouTubePage:
    items: list[YouTubePlaylistItem]
    next_page_token: str | None


def parse_youtube_url(value: str, target: str | None = None) -> YouTubeTarget | None:
    """Watch+list URLs select the video unless the caller explicitly selects playlist."""
    if value.startswith("@"):
        value = "https://www.youtube.com/" + value
    parsed = urlsplit(validate_public_url(value))
    host = parsed.hostname
    if host not in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }:
        return None
    if parsed.port not in {None, 80, 443}:
        raise ValueError("YouTube URLs must use their standard public origin")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(len(query.get(key, [])) > 1 for key in ("v", "list")):
        raise ValueError("YouTube resource identifiers must be unambiguous")
    path = unquote(parsed.path).strip("/").split("/")
    video = None
    playlist = query.get("list", [None])[0]
    if playlist is not None and not RESOURCE_ID.fullmatch(playlist):
        raise ValueError("Invalid YouTube playlist ID")
    if host.endswith("youtu.be"):
        video = path[0]
    elif path[0] == "watch":
        video = query.get("v", [None])[0]
    elif path[0] in {"shorts", "embed", "live"} and len(path) == 2:
        video = path[1]
    if video is not None:
        if not VIDEO_ID.fullmatch(video):
            raise ValueError("Invalid YouTube video ID")
        if target in {None, "video"}:
            return YouTubeTarget(
                "video",
                video,
                canonical_url=f"https://www.youtube.com/watch?v={video}",
                video_id=video,
                playlist_id=playlist,
            )
    if playlist and target in {None, "playlist"}:
        return YouTubeTarget(
            "playlist",
            playlist,
            canonical_url=f"https://www.youtube.com/playlist?list={playlist}",
            video_id=video,
            playlist_id=playlist,
        )
    if target not in {None, "channel"}:
        raise ValueError("The selected YouTube resource is not present in this URL")
    if (
        len(path) in {1, 2}
        and path[0].startswith("@")
        and re.fullmatch(r"@[\w.\-\u00b7]{3,30}", path[0])
    ):
        return YouTubeTarget("channel", path[0], "handle", f"https://www.youtube.com/{path[0]}")
    if len(path) == 2 and path[0] == "channel" and CHANNEL_ID.fullmatch(path[1]):
        return YouTubeTarget(
            "channel", path[1], canonical_url=f"https://www.youtube.com/channel/{path[1]}"
        )
    if len(path) == 2 and path[0] == "user" and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", path[1]):
        return YouTubeTarget(
            "channel", path[1], "username", f"https://www.youtube.com/user/{path[1]}"
        )
    raise ValueError("Use a YouTube channel ID, handle, playlist URL or video URL")


def resource_from_source(source) -> YouTubeResource:
    return YouTubeResource(
        kind=source.resource_kind,
        resource_id=source.provider_resource_id,
        collection_id=source.provider_collection_id,
        canonical_url=source.website_url or "",
        title=source.name,
        description=source.description or "",
    )


def _date(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(UTC) if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def _text(value, limit):
    # Data API descriptions are plain text, including literal generic/code
    # syntax such as <T>. Do not interpret them as publisher HTML.
    return " ".join(value.replace("\x00", "").split())[:limit] if isinstance(value, str) else ""


def _language(value):
    value = value.lower() if isinstance(value, str) else ""
    return (
        value
        if re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", value) and len(value) <= 35
        else None
    )


def _thumbnail(snippet):
    thumbnails = snippet.get("thumbnails", {})
    for size in ("maxres", "standard", "high", "medium", "default"):
        value = thumbnails.get(size, {}).get("url")
        if isinstance(value, str):
            try:
                value = validate_public_url(value)
                if urlsplit(value).scheme == "https" and urlsplit(value).hostname in {
                    "i.ytimg.com",
                    "img.youtube.com",
                    "yt3.ggpht.com",
                    "yt3.googleusercontent.com",
                }:
                    return value
            except ValueError:
                continue
    return None


def _duration(value):
    match = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return None
    return sum(
        int(count or 0) * factor
        for count, factor in zip(match.groups(), (86400, 3600, 60, 1), strict=True)
    )


class YouTubeClient:
    def __init__(self, api_key, *, request_limit=10, timeout=15):
        if not api_key:
            raise YouTubeError(
                "YouTube metadata API is not configured", reason="youtube_not_configured"
            )
        self._api_key = (
            api_key.get_secret_value() if hasattr(api_key, "get_secret_value") else api_key
        )
        self.request_limit = request_limit
        self.requests = 0
        self.timeout = min(timeout, 15)
        self.started = time.monotonic()

    @classmethod
    def from_settings(cls):
        settings = get_settings()
        return cls(
            settings.youtube_api_key,
            request_limit=settings.youtube_request_limit,
            timeout=settings.feed_timeout_seconds,
        )

    def _request(self, endpoint, **params):
        if endpoint not in {"channels", "playlists", "playlistItems", "videos"}:
            raise ValueError("Unsupported YouTube metadata endpoint")
        if self.requests >= self.request_limit or time.monotonic() - self.started >= 90:
            raise YouTubeError(
                "YouTube request budget exhausted",
                reason="youtube_request_budget",
                retryable=True,
                retry_after=300,
            )
        self.requests += 1
        url = API_BASE + endpoint + "?" + urlencode(params)
        try:
            with (
                httpcore.ConnectionPool(
                    network_backend=PublicNetworkBackend(),
                    max_connections=1,
                    max_keepalive_connections=0,
                ) as pool,
                pool.stream(
                    "GET",
                    url,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "X-Goog-Api-Key": self._api_key,
                    },
                    extensions={
                        "timeout": dict.fromkeys(("connect", "read", "write", "pool"), self.timeout)
                    },
                ) as response,
            ):
                headers = {
                    key.decode("ascii").lower(): value.decode("latin-1")
                    for key, value in response.headers
                }
                if 300 <= response.status < 400:
                    raise YouTubeError(
                        "YouTube metadata redirects are not allowed",
                        reason="youtube_redirect",
                        status=response.status,
                    )
                if headers.get("content-encoding", "identity").lower() != "identity":
                    raise YouTubeError(
                        "YouTube returned unsupported response encoding",
                        reason="youtube_encoding",
                    )
                body = bytearray()
                for chunk in response.iter_stream():
                    body.extend(chunk)
                    if len(body) > 2_000_000 or time.monotonic() - self.started >= 90:
                        raise YouTubeError(
                            "YouTube metadata response exceeded its budget",
                            reason="youtube_response_budget",
                            retryable=True,
                        )
                try:
                    data = json.loads(body)
                except (ValueError, UnicodeError):
                    raise YouTubeError(
                        "YouTube returned invalid metadata JSON",
                        reason="youtube_invalid_response",
                        retryable=True,
                    ) from None
                if response.status != 200:
                    reasons = (
                        {
                            item.get("reason")
                            for item in data.get("error", {}).get("errors", [])
                            if isinstance(item, dict)
                        }
                        if isinstance(data, dict)
                        else set()
                    )
                    quota = (
                        bool(
                            reasons
                            & {
                                "quotaExceeded",
                                "dailyLimitExceeded",
                                "rateLimitExceeded",
                                "userRateLimitExceeded",
                            }
                        )
                        or response.status == 429
                    )
                    raise YouTubeError(
                        "YouTube metadata quota exhausted"
                        if quota
                        else "YouTube metadata request failed",
                        reason="youtube_quota_exhausted"
                        if quota
                        else "youtube_invalid_page_token"
                        if "invalidPageToken" in reasons
                        else "youtube_http_error",
                        status=response.status,
                        retryable=quota or response.status >= 500,
                        retry_after=max(
                            3600 if quota else 0,
                            retry_after_seconds(headers.get("retry-after")),
                        ),
                    )
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("items"), list)
                    or any(not isinstance(item, dict) for item in data["items"])
                ):
                    raise YouTubeError(
                        "YouTube returned incomplete metadata",
                        reason="youtube_invalid_response",
                        retryable=True,
                    )
                return data
        except (httpcore.NetworkError, httpcore.TimeoutException, httpcore.ProtocolError):
            raise YouTubeError(
                "YouTube metadata transport unavailable",
                reason="youtube_transport_error",
                retryable=True,
            ) from None

    def resolve(self, target: YouTubeTarget) -> YouTubeResource:
        if target.kind == "video":
            video = self.videos([target.resource_id]).get(target.resource_id)
            if video is None or video.availability != "public":
                raise YouTubeError(
                    "YouTube video is not publicly available", reason="youtube_resource_unavailable"
                )
            return YouTubeResource(
                "video",
                video.id,
                None,
                video.canonical_url,
                video.title,
                video.description,
                video.channel_id,
                video.channel_title,
                video.thumbnail_url,
            )
        endpoint = "channels" if target.kind == "channel" else "playlists"
        field_name = {"id": "id", "handle": "forHandle", "username": "forUsername"}[target.lookup]
        data = self._request(
            endpoint,
            part="snippet,contentDetails",
            **{field_name: target.resource_id},
            maxResults=1,
        )
        if len(data["items"]) != 1:
            raise YouTubeError(
                "YouTube collection is not publicly available",
                reason="youtube_resource_unavailable",
            )
        item = data["items"][0]
        snippet = item.get("snippet", {})
        identifier = item.get("id", "")
        if not (CHANNEL_ID if target.kind == "channel" else RESOURCE_ID).fullmatch(identifier):
            raise YouTubeError(
                "YouTube returned an invalid collection identity",
                reason="youtube_invalid_response",
                retryable=True,
            )
        collection = (
            item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            if target.kind == "channel"
            else identifier
        )
        if not isinstance(collection, str) or not RESOURCE_ID.fullmatch(collection):
            raise YouTubeError(
                "YouTube channel has no public uploads collection",
                reason="youtube_resource_unavailable",
            )
        canonical = (
            f"https://www.youtube.com/channel/{identifier}"
            if target.kind == "channel"
            else f"https://www.youtube.com/playlist?list={identifier}"
        )
        return YouTubeResource(
            target.kind,
            identifier,
            collection,
            canonical,
            _text(snippet.get("title", ""), 200),
            _text(snippet.get("description", ""), 5000),
            identifier if target.kind == "channel" else snippet.get("channelId", ""),
            _text(snippet.get("channelTitle", ""), 200),
            _thumbnail(snippet),
        )

    def playlist_items(
        self, collection_id, page_token=None, max_results=50, *, video_id=None
    ) -> YouTubePage:
        if not RESOURCE_ID.fullmatch(collection_id) or not 1 <= max_results <= 50:
            raise ValueError("Invalid YouTube collection request")
        params = {
            "part": "snippet,contentDetails",
            "playlistId": collection_id,
            "maxResults": max_results,
        }
        if page_token:
            if (
                not isinstance(page_token, str)
                or len(page_token) > 1000
                or any(ord(char) < 33 for char in page_token)
            ):
                raise YouTubeError(
                    "YouTube returned invalid pagination", reason="youtube_invalid_pagination"
                )
            params["pageToken"] = page_token
        if video_id:
            if not VIDEO_ID.fullmatch(video_id):
                raise ValueError("Invalid YouTube video ID")
            params["videoId"] = video_id
        data = self._request("playlistItems", **params)
        if len(data["items"]) > max_results:
            raise YouTubeError(
                "YouTube playlist page exceeded its item budget",
                reason="youtube_invalid_response",
                retryable=True,
            )
        items = []
        for item in data["items"]:
            snippet = item.get("snippet", {})
            identifier = item.get("contentDetails", {}).get("videoId") or snippet.get(
                "resourceId", {}
            ).get("videoId")
            item_id = item.get("id")
            if (
                not isinstance(identifier, str)
                or not VIDEO_ID.fullmatch(identifier)
                or not isinstance(item_id, str)
                or not 1 <= len(item_id) <= 200
            ):
                raise YouTubeError(
                    "YouTube returned an incomplete playlist item",
                    reason="youtube_invalid_response",
                    retryable=True,
                )
            items.append(
                YouTubePlaylistItem(identifier, item_id, _date(snippet.get("publishedAt")))
            )
        token = data.get("nextPageToken")
        if token is not None and (
            not isinstance(token, str) or not token or len(token) > 1000 or token == page_token
        ):
            raise YouTubeError(
                "YouTube pagination did not advance",
                reason="youtube_invalid_pagination",
                retryable=True,
            )
        return YouTubePage(items, token)

    def videos(self, identifiers) -> dict[str, YouTubeVideo]:
        identifiers = list(dict.fromkeys(identifiers))
        if (
            not identifiers
            or len(identifiers) > 50
            or any(not VIDEO_ID.fullmatch(identifier) for identifier in identifiers)
        ):
            raise ValueError("YouTube video metadata requires between 1 and 50 IDs")
        data = self._request(
            "videos", part="snippet,contentDetails,status", id=",".join(identifiers), maxResults=50
        )
        result = {}
        for item in data["items"]:
            identifier = item.get("id")
            snippet = item.get("snippet", {})
            if (
                identifier not in identifiers
                or identifier in result
                or not isinstance(snippet, dict)
            ):
                raise YouTubeError(
                    "YouTube returned mismatched video metadata",
                    reason="youtube_invalid_response",
                    retryable=True,
                )
            title, channel = _text(snippet.get("title", ""), 500), snippet.get("channelId", "")
            if not title or not CHANNEL_ID.fullmatch(channel):
                raise YouTubeError(
                    "YouTube returned incomplete video metadata",
                    reason="youtube_invalid_response",
                    retryable=True,
                )
            status = item.get("status", {})
            available = (
                status.get("privacyStatus") == "public"
                and status.get("uploadStatus") == "processed"
            )
            audio, default = (
                _language(snippet.get("defaultAudioLanguage")),
                _language(snippet.get("defaultLanguage")),
            )
            result[identifier] = YouTubeVideo(
                identifier,
                title,
                _text(snippet.get("description", ""), 5000),
                channel,
                _text(snippet.get("channelTitle", ""), 200),
                _date(snippet.get("publishedAt")),
                _thumbnail(snippet),
                _duration(item.get("contentDetails", {}).get("duration")),
                audio or default,
                "public" if available else "unavailable",
                default_audio_language=audio,
                default_language=default,
            )
        return result

    def sample_resource(self, resource: YouTubeResource, limit=10) -> list[YouTubeVideo]:
        if not 1 <= limit <= 50:
            raise ValueError("YouTube samples must contain at most 50 videos")
        identifiers = (
            [resource.resource_id]
            if resource.kind == "video"
            else [
                item.video_id
                for item in self.playlist_items(resource.collection_id, max_results=limit).items
            ]
        )
        return list(self.videos(identifiers).values()) if identifiers else []
