"""Source lookup is authenticated preflight, not a submission or ingestion job."""

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import httpcore
import pytest
from devfeed_admin_api import sources
from devfeed_admin_api.dependencies import get_session
from devfeed_core import services
from devfeed_core.source_preview import preview_source
from sqlalchemy.exc import IntegrityError
from test_admin_auth import ORIGIN, complete, logout_headers
from test_admin_auth import oidc_app as oidc_app
from test_feed_validation import transport as transport

URL = "https://publication.example/rss"
RSS = b"""<rss version="2.0"><channel><title>Developer News</title>
<link>https://publication.example/news/</link>
<description>A &lt;b&gt;developer&lt;/b&gt; feed</description><language>en-US</language>
<image><url>/logo.svg</url></image></channel></rss>"""
HTML = b"""<html lang="fr"><head><meta name="description" content="Website description">
<link rel="icon" href="/favicon.ico"><meta property="og:image" content="/cover.png">
</head></html>"""


class PreviewSession:
    def __init__(self):
        self.existing = None
        self.in_transaction = False
        self.rollbacks = 0

    @contextmanager
    def begin(self):
        self.in_transaction = True
        try:
            yield self
        finally:
            self.in_transaction = False

    def scalar(self, statement):
        return self.existing

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def preview_client(oidc_app):
    complete(oidc_app)
    session = PreviewSession()
    oidc_app.client.app.dependency_overrides[get_session] = lambda: session
    return oidc_app.client, logout_headers(oidc_app), session


def test_preview_merges_feed_and_declared_site_without_overwriting_feed(transport):
    calls = transport(
        httpcore.Response(200, content=RSS),
        httpcore.Response(200, headers={"content-type": "text/html"}, content=HTML),
    )
    result = preview_source(URL, "publisher")
    assert result.name == "Developer News"
    assert result.profile.description == "A developer feed"
    assert result.profile.language == "en-us"
    assert result.profile.logo_url == "https://publication.example/logo.svg"
    assert result.profile.image_url == "https://publication.example/cover.png"
    assert result.warnings == ()
    assert [call[1] for call in calls] == [URL, "https://publication.example/news/"]


def test_preview_without_declared_website_does_not_guess_or_crawl(transport):
    calls = transport(httpcore.Response(200, content=b'<rss version="2.0"><channel/></rss>'))
    result = preview_source(URL, "publisher")
    assert result.name == "publication.example"
    assert result.profile.website_url is None
    assert result.profile.logo_url is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpcore.Response(503),
        httpcore.Response(302, headers={"location": "http://169.254.169.254/"}),
        httpcore.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"),
    ],
)
def test_site_failure_keeps_usable_feed_metadata_with_safe_warning(transport, response):
    transport(httpcore.Response(200, content=RSS), response)
    result = preview_source(URL, "aggregator")
    assert result.profile.description == "A developer feed"
    assert result.profile.image_url is None
    assert len(result.warnings) == 1
    assert "169.254" not in result.warnings[0]


def test_preview_api_has_no_writes_and_finishes_db_read_before_network(
    preview_client, transport, monkeypatch
):
    client, headers, session = preview_client
    transport(
        httpcore.Response(200, content=RSS),
        httpcore.Response(200, headers={"content-type": "text/html"}, content=HTML),
    )
    original = sources.preview_source

    def lookup(*args):
        assert not session.in_transaction
        return original(*args)

    monkeypatch.setattr(sources, "preview_source", lookup)
    monkeypatch.setattr(services, "create_source", lambda *a: pytest.fail("Preview saved a source"))
    response = client.post(
        "/v1/admin/sources/preview",
        json={"feed_url": URL, "source_type": "publisher"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Developer News"
    assert response.json()["language"] == "en-us"
    assert not {"id", "job", "submitted_by", "approval_status"} & response.json().keys()
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("status,body", [(404, b""), (200, b"<html>Not RSS</html>")])
def test_preview_invalid_feed_returns_actionable_error(preview_client, transport, status, body):
    client, headers, _ = preview_client
    transport(httpcore.Response(status, content=body))
    response = client.post(
        "/v1/admin/sources/preview",
        json={"feed_url": URL, "source_type": "publisher"},
        headers=headers,
    )
    assert response.status_code == 422
    assert "Feed validation failed" in response.json()["detail"]


@pytest.mark.parametrize("path", ["/v1/admin/sources/preview", "/v1/admin/sources"])
def test_duplicate_feed_returns_field_error_before_network(preview_client, monkeypatch, path):
    client, headers, session = preview_client
    session.existing = uuid.uuid4()
    monkeypatch.setattr(sources, "preview_source", lambda *a: pytest.fail("Fetched duplicate"))
    monkeypatch.setattr(services, "validate_source", lambda *a: pytest.fail("Fetched duplicate"))
    response = client.post(
        path, json={"feed_url": URL, "source_type": "publisher"}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["detail"][0]["loc"] == ["body", "feed_url"]
    assert "already exists" in response.json()["detail"][0]["msg"]


def test_concurrent_duplicate_save_has_same_field_error(preview_client, monkeypatch):
    client, headers, session = preview_client
    monkeypatch.setattr(services, "validate_source", lambda *a: SimpleNamespace())

    def conflicting_insert(*args):
        session.existing = uuid.uuid4()
        raise IntegrityError("insert", {}, Exception("unique constraint"))

    monkeypatch.setattr(services, "create_source", conflicting_insert)
    response = client.post(
        "/v1/admin/sources", json={"feed_url": URL, "source_type": "publisher"}, headers=headers
    )
    assert response.status_code == 409
    assert session.rollbacks == 1
    assert response.json()["detail"][0]["type"] == "duplicate_feed_url"


@pytest.mark.parametrize("headers", [{}, {"Origin": ORIGIN}, {"Origin": "https://evil.example"}])
def test_preview_requires_csrf_before_database_or_network(oidc_app, monkeypatch, headers):
    complete(oidc_app)
    monkeypatch.setattr(sources, "preview_source", lambda *a: pytest.fail("Unauthorized lookup"))
    response = oidc_app.client.post(
        "/v1/admin/sources/preview",
        json={"feed_url": URL, "source_type": "publisher"},
        headers=headers,
    )
    assert response.status_code == 403


def test_preview_rejects_anonymous_users(oidc_app):
    assert oidc_app.client.post("/v1/admin/sources/preview").status_code == 401


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/rss", "https://user:pass@example.com/feed", "file:///rss"]
)
def test_preview_rejects_unsafe_urls_before_fetch(preview_client, monkeypatch, url):
    client, headers, _ = preview_client
    monkeypatch.setattr(sources, "preview_source", lambda *a: pytest.fail("Fetched unsafe URL"))
    response = client.post(
        "/v1/admin/sources/preview",
        json={"feed_url": url, "source_type": "publisher"},
        headers=headers,
    )
    assert response.status_code == 422
