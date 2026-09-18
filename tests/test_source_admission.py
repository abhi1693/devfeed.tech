"""Admission rules exercise real RSS/Atom parsing and both API entry points."""

from datetime import UTC, datetime, timedelta

import httpcore
import pytest
from devfeed_core.feeds.validation import FeedValidationError, validate_feed
from test_admin_auth import oidc_app as oidc_app
from test_feed_validation import api_client as api_client
from test_feed_validation import transport as transport
from test_source_preview import preview_client as preview_client

NOW = datetime(2026, 5, 31, 12, tzinfo=UTC)
CUTOFF = datetime(2026, 2, 28, 12, tzinfo=UTC)
URL = "https://example.com/rss"


def feed(dates, *, atom=False, duplicate=False):
    entries = []
    for i, date in enumerate(dates):
        url = f"https://example.com/{0 if duplicate else i}"
        date = date.isoformat() if isinstance(date, datetime) else date
        if atom:
            stamp = f"<updated>{date}</updated>" if date else ""
            entries.append(f'<entry><title>Article</title><link href="{url}"/>{stamp}</entry>')
        else:
            stamp = f"<pubDate>{date}</pubDate>" if date else ""
            entries.append(f"<item><title>Article</title><link>{url}</link>{stamp}</item>")
    body = "".join(entries)
    if atom:
        return f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()
    return f'<rss version="2.0"><channel>{body}</channel></rss>'.encode()


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr("devfeed_core.feeds.validation.datetime", Clock)


@pytest.mark.parametrize("source_type", ["publisher", "aggregator"])
@pytest.mark.parametrize("atom", [False, True])
@pytest.mark.parametrize("date", [CUTOFF, NOW])
def test_accepts_three_entries_with_one_recent_date(transport, source_type, atom, date):
    transport(httpcore.Response(200, content=feed([None, date, None], atom=atom)))
    assert len(validate_feed(URL, source_type=source_type).entries) == 3


@pytest.mark.parametrize(
    "dates,reason",
    [
        ([], "insufficient_entries"),
        ([NOW], "insufficient_entries"),
        ([NOW, NOW], "insufficient_entries"),
        ([None] * 3, "no_recent_entries"),
        (["not a date"] * 3, "no_recent_entries"),
        ([CUTOFF - timedelta(seconds=1)] * 3, "no_recent_entries"),
        ([NOW + timedelta(seconds=1)] * 3, "no_recent_entries"),
    ],
)
@pytest.mark.parametrize("source_type", ["publisher", "aggregator"])
def test_rejects_insufficient_or_inactive_feeds(transport, dates, reason, source_type):
    transport(httpcore.Response(200, content=feed(dates)))
    with pytest.raises(FeedValidationError) as caught:
        validate_feed(URL, source_type=source_type)
    assert caught.value.reason == reason
    assert not caught.value.retryable


def test_duplicates_and_unusable_entries_do_not_meet_minimum(transport):
    body = feed([NOW] * 3, duplicate=True).replace(
        b"</channel>", b"<item><title>No URL</title></item></channel>"
    )
    transport(httpcore.Response(200, content=body))
    with pytest.raises(FeedValidationError, match="at least 3"):
        validate_feed(URL, source_type="publisher")


@pytest.mark.parametrize(
    "dates,message", [([NOW] * 2, "at least 3"), ([None] * 3, "last 3 months")]
)
@pytest.mark.parametrize("path", ["/v1/admin/sources", "/v1/admin/sources/preview"])
def test_admin_rejects_before_persistence(
    preview_client, transport, monkeypatch, dates, message, path
):
    client, headers, _ = preview_client
    transport(httpcore.Response(200, content=feed(dates)))
    monkeypatch.setattr(
        "devfeed_core.services.create_source", lambda *a: pytest.fail("Saved invalid source")
    )
    response = client.post(
        path, json={"feed_url": URL, "source_type": "publisher"}, headers=headers
    )
    assert response.status_code == 422
    assert message in response.json()["detail"]


@pytest.mark.parametrize(
    "dates,message", [([NOW] * 2, "at least 3"), ([None] * 3, "last 3 months")]
)
def test_user_and_extension_submission_api_rejects_before_persistence(
    api_client, transport, monkeypatch, dates, message
):
    # Website, Chrome and Edge all submit to this authenticated user API.
    client, events = api_client
    transport(httpcore.Response(200, content=feed(dates)))
    monkeypatch.setattr(
        "devfeed_core.services.create_source", lambda *a: pytest.fail("Saved invalid source")
    )
    response = client.post(
        "/v1/user/sources/suggestions", json={"feed_url": URL, "source_type": "publisher"}
    )
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert events == []


@pytest.mark.parametrize("dates", [[NOW] * 2, [None] * 3])
def test_website_discovery_cannot_bypass_feed_admission(monkeypatch, dates):
    from devfeed_core.discovery_crawler import feed_evidence
    from devfeed_core.feeds.fetcher import FeedError

    monkeypatch.setattr("devfeed_core.discovery_crawler.utcnow", lambda: NOW)
    with pytest.raises(FeedError):
        feed_evidence(feed(dates), URL, "https://example.com/")
