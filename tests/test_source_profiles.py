import gzip
from dataclasses import asdict

import httpcore
import pytest
from devfeed_aggregator import source_tasks
from devfeed_core import services
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.feeds.parser import parse_feed
from devfeed_core.models import Source, utcnow
from devfeed_core.schemas import SourceCreate, SourcePatch, SourceSubmission
from devfeed_core.source_enrichment import fill_profile
from devfeed_core.source_profiles import PROFILE_FIELDS, website_profile
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from test_feed_validation import api_client as api_client
from test_feed_validation import source_record
from test_feed_validation import transport as transport

URL = "https://publisher.example/feed"
RSS = b"""<rss version="2.0"><channel><title>Engineering</title>
<link>https://publisher.example/engineering/</link>
<description>A &lt;b&gt;developer&lt;/b&gt; publication</description><language>en-US</language>
<image><url>/logo.png</url></image></channel></rss>"""


def test_preflight_extracts_profile_without_another_http_request(transport):
    calls = transport(httpcore.Response(200, content=RSS))
    source = services.validate_source(SourceCreate(feed_url=URL, source_type="publisher"))
    assert source.name == "Engineering" and source.description == "A developer publication"
    assert source.website_url == "https://publisher.example/engineering/"
    assert source.logo_url == "https://publisher.example/logo.png" and source.language == "en-us"
    assert len(calls) == 1 and source.submitted_by is None


def test_atom_profile_and_author_are_not_submitter_identity():
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en"><title>Example</title>
    <subtitle type="html">Developer &lt;b&gt;news&lt;/b&gt;</subtitle><link href="https://publisher.example/"/>
    <logo>/brand.png</logo><icon>/icon.png</icon>
    <author><name>Publisher author</name></author></feed>"""
    parsed = parse_feed(atom, URL, utcnow(), source_type="publisher")
    assert parsed.profile.logo_url == "https://publisher.example/brand.png"
    assert parsed.profile.description == "Developer news"
    assert "submitted_by" not in asdict(parsed.profile)


def test_manual_profile_and_submitter_take_precedence_over_feed(transport):
    transport(httpcore.Response(200, content=RSS))
    body = SourceCreate(
        feed_url=URL,
        source_type="publisher",
        name="Custom",
        description="Reviewed description",
        logo_url="https://cdn.example/chosen.png",
        submitted_by={"name": "Contributor", "profile_url": "https://example.com/person"},
    )
    source = services.validate_source(body)
    assert source.name == "Custom" and source.description == "Reviewed description"
    assert source.logo_url == "https://cdn.example/chosen.png"
    assert source.submitted_by["name"] == "Contributor"
    body.submitted_by.name = "Changed afterward"
    assert source.submitted_by["name"] == "Contributor"


@pytest.mark.parametrize("field", ["website_url", "logo_url", "image_url"])
@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/logo", "file:///logo", "https://user:password@example.com/logo"]
)
def test_untrusted_profile_urls_are_rejected(field, url):
    with pytest.raises(ValidationError):
        SourceSubmission(feed_url=URL, source_type="publisher", **{field: url})


def test_profile_edits_are_plain_text_bounded_and_cannot_edit_submission_or_review():
    assert SourcePatch(description="A <b>useful</b> feed").description == "A useful feed"
    assert SourcePatch(language=" en-US ").language == "en-us"
    assert SourcePatch(logo_url=None).model_dump(exclude_unset=True) == {"logo_url": None}
    for values in (
        {"description": "a" * 501},
        {"language": "INVALID"},
        {"submitted_by": {"name": "spoof"}},
        {"approval_status": "approved"},
    ):
        with pytest.raises(ValidationError):
            SourcePatch(**values)


def test_website_profile_uses_declared_icons_preview_and_description():
    page = FetchResult(
        200,
        b"""<html lang="en-GB"><head><base href="https://cdn.example/assets/">
    <meta name="description" content="A &lt;b&gt;great&lt;/b&gt; publication">
    <link rel="icon" href="small.ico"><link rel="apple-touch-icon" href="large.png">
    <meta property="og:image" content="cover.png"></head></html>""",
        "https://publisher.example/",
    )
    result = website_profile(page)
    assert result.description == "A great publication"
    assert result.logo_url == "https://cdn.example/assets/large.png"
    assert result.image_url == "https://cdn.example/assets/cover.png"
    assert result.language == "en-gb" and result.website_url == page.final_url


def test_no_guessed_favicon_or_unsafe_template_metadata():
    page = FetchResult(
        200,
        b"""<template><link rel="icon" href="/ignored.png"></template>
    <link rel="icon" href="http://127.0.0.1/icon"><img src="/random-logo.png">""",
        URL,
    )
    assert website_profile(page).logo_url is None


def test_enrichment_preserves_manual_values_and_ignores_changed_website():
    original = dict.fromkeys(PROFILE_FIELDS)
    source = Source(name="Kept", **original)
    source.description = "Manual edit during request"
    changes = fill_profile(
        source, {"description": "Fetched", "logo_url": "https://cdn.example/icon.png"}, original
    )
    assert changes == ["logo_url"] and source.description == "Manual edit during request"
    source.website_url = "https://replacement.example/"
    assert fill_profile(source, {"image_url": "https://old.example/image"}, original) == []
    assert source.image_url is None


def test_site_failure_returns_feed_profile_for_partial_progress_and_safe_retry(monkeypatch):
    monkeypatch.setattr(source_tasks, "fetch_feed", lambda _: FetchResult(200, RSS, URL))

    def unavailable(_):
        raise FeedError("do-not-log-page-data", status=503, retryable=True, reason="http_error")

    monkeypatch.setattr(source_tasks, "fetch_source_page", unavailable)
    profile, error = source_tasks.lookup_profile(URL, "publisher", dict.fromkeys(PROFILE_FIELDS))
    assert profile["description"] == "A developer publication"
    assert error.status == 503 and error.retryable


@pytest.mark.parametrize("encoding", ["identity", "gzip"])
def test_enrichment_reads_large_source_homepage_metadata(transport, monkeypatch, encoding):
    # Metadata after the application scripts proves the complete page is processed.
    settings = get_settings()
    monkeypatch.setattr(settings, "page_max_bytes", 2_000_000)
    monkeypatch.setattr(settings, "source_page_max_bytes", 10_000_000)
    page = (
        b"<html><head><script>" + b" " * 2_100_000 + b"</script>"
        b'<meta property="og:image" content="/cover.png"></head></html>'
    )
    calls = transport(
        httpcore.Response(200, content=RSS),
        httpcore.Response(
            200,
            headers={"content-encoding": encoding, "content-type": "text/html"},
            content=gzip.compress(page) if encoding == "gzip" else page,
        ),
    )
    profile, error = source_tasks.lookup_profile(URL, "publisher", dict.fromkeys(PROFILE_FIELDS))
    assert error is None
    assert profile["image_url"] == "https://publisher.example/cover.png"
    assert profile["description"] == "A developer publication"
    assert profile["logo_url"] == "https://publisher.example/logo.png"
    assert [request[1] for request in calls] == [URL, "https://publisher.example/engineering/"]


@pytest.mark.parametrize("encoding", ["identity", "gzip"])
def test_source_page_budget_does_not_expand_other_downloads(transport, monkeypatch, encoding):
    from devfeed_core.feeds.fetcher import (
        fetch_article_page,
        fetch_feed,
        fetch_page,
        fetch_source_page,
    )

    settings = get_settings()
    for field in ("page_max_bytes", "feed_max_bytes", "article_page_max_bytes"):
        monkeypatch.setattr(settings, field, 100)
    monkeypatch.setattr(settings, "source_page_max_bytes", 2000)
    body = b"<html><body>" + b"x" * 1000 + b"</body></html>"
    for fetch in (fetch_source_page, fetch_article_page, fetch_page, fetch_feed):
        transport(
            httpcore.Response(
                200,
                headers={"content-encoding": encoding},
                content=gzip.compress(body) if encoding == "gzip" else body,
            )
        )
        if fetch is fetch_source_page:
            assert fetch(URL).body == body
        else:
            with pytest.raises(FeedError) as error:
                fetch(URL)
            assert error.value.reason == "response_too_large"
            assert error.value.limit_bytes == 100


@pytest.mark.parametrize(
    "extra",
    [
        {"approval_status": "approved"},
        {"reviewed_by": "admin"},
        {"submission_channel": "cli"},
        {"enabled": True},
        {"poll_interval_seconds": 300},
        {"submitted_by": {"name": "Admin", "verified": True}},
    ],
)
def test_api_cannot_forge_trust_or_polling_settings_before_preflight(
    api_client, monkeypatch, extra
):
    client, events = api_client
    monkeypatch.setattr(services, "validate_source", lambda *_: pytest.fail("Network request"))
    response = client.post(
        "/v1/user/sources/suggestions", json={"feed_url": URL, "source_type": "publisher", **extra}
    )
    assert response.status_code == 422 and events == []


def test_api_submission_returns_pending_receipt_without_private_attribution(
    api_client, transport, monkeypatch
):
    client, events = api_client
    transport(httpcore.Response(200, content=RSS))
    monkeypatch.setattr(
        services, "create_source", lambda session, prepared: source_record(prepared)
    )
    result = client.post(
        "/v1/user/sources/suggestions",
        json={"feed_url": URL, "source_type": "publisher"},
    )
    assert result.status_code == 201
    value = result.json()
    assert value["approval_status"] == "pending"
    assert "submitted_by" not in value
    assert not {"review_note", "reviewed_by", "enabled", "last_error"} & value.keys()
    assert events == ["commit"]


def test_api_has_no_source_edit_approval_or_fetch_routes(public_api_client):
    client = public_api_client
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths["/v1/sources"]) == {"get"}
    assert set(paths["/v1/sources/{source_id}"]) == {"get"}
    assert "/v1/sources/{source_id}/fetch" not in paths
    assert not any("approve" in path or "reject" in path for path in paths)


@pytest.mark.parametrize("approval_status", ["pending", "approved", "rejected"])
def test_public_source_projection_and_approval_filters(public_api_client, approval_status):
    from types import SimpleNamespace

    from devfeed_api.dependencies import get_session

    client = public_api_client
    record = source_record(
        services.ValidatedSource(
            "Publication",
            URL,
            "publisher",
            True,
            1800,
            submitted_by={"name": "Private contributor"},
            description="Public description",
        )
    )
    record.approval_status = approval_status
    record.review_note = "Private review note"
    record.reviewed_by = "Private operator"
    statements = []

    def scalars(statement):
        statements.append(statement)
        return SimpleNamespace(all=lambda: [record] if approval_status == "approved" else [])

    client.app.dependency_overrides[get_session] = lambda: SimpleNamespace(
        get=lambda *args: record, scalars=scalars
    )
    response = client.get(f"/v1/sources/{record.id}")
    assert response.status_code == (200 if approval_status == "approved" else 404)
    listing = client.get("/v1/sources?enabled=true&source_type=publisher")
    assert listing.status_code == 200
    compiled = statements[0].compile(dialect=postgresql.dialect())
    assert "sources.approval_status =" in str(compiled)
    assert "approved" in compiled.params.values()
    if approval_status == "approved":
        assert listing.json() == [response.json()]
        assert response.json()["description"] == "Public description"
        assert (
            not {"reviewed_by", "review_note", "submitted_by", "feed_url", "enabled", "last_error"}
            & response.json().keys()
        )
    else:
        assert listing.json() == []


@pytest.fixture
def public_api_client():
    from devfeed_api.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        yield client
