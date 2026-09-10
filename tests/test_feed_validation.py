import io
import json
import uuid
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict
from types import SimpleNamespace

import httpcore
import pytest
from devfeed_cli import commands
from devfeed_cli.main import run
from devfeed_core import services
from devfeed_core.feeds.parser import ParsedFeed
from devfeed_core.feeds.validation import FeedValidationError, validate_feed
from devfeed_core.models import Source, utcnow
from devfeed_core.schemas import SourceCreate

URL = "https://example.com/rss"
EMPTY_RSS = b'<rss version="2.0"><channel><title>Empty</title></channel></rss>'
EMPTY_ATOM = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Empty</title></feed>'


@pytest.fixture
def transport(monkeypatch):
    """Exercise the real fetch/parse path with an in-memory HTTP transport, no servers."""

    def configure(*responses, events=None):
        requests = []
        pending = iter(responses)

        @contextmanager
        def stream(method, url, **kwargs):
            httpcore.URL(url)
            requests.append((method, url, dict(kwargs["headers"])))
            if events is not None:
                events.append("fetch")
            response = next(pending)
            if isinstance(response, Exception):
                raise response
            yield response

        @contextmanager
        def pool(**kwargs):
            yield SimpleNamespace(stream=stream)

        monkeypatch.setattr(httpcore, "ConnectionPool", pool)
        return requests

    return configure


@pytest.mark.parametrize("body", [EMPTY_RSS, EMPTY_ATOM])
def test_preflight_accepts_valid_empty_feeds(transport, body):
    calls = transport(httpcore.Response(200, content=body))
    validated = validate_feed(URL, source_type="publisher")
    assert validated.entries == []
    assert validated.title == "Empty"
    assert calls[0][:2] == ("GET", URL)
    assert "If-None-Match" not in calls[0][2]
    assert "If-Modified-Since" not in calls[0][2]


def test_preflight_accepts_unicode_url_through_ascii_transport(transport):
    calls = transport(httpcore.Response(200, content=EMPTY_RSS))
    assert (
        validate_feed("https://example.com/日本語?tag=café", source_type="publisher").title
        == "Empty"
    )
    assert calls[0][1] == "https://example.com/%E6%97%A5%E6%9C%AC%E8%AA%9E?tag=caf%C3%A9"


@pytest.mark.parametrize(
    "body",
    [
        b"<html><title>A website, not a feed</title></html>",
        b"not xml",
        b"",
        b"<rss version='2.0'>",
        (
            b'<rss version="2.0"><channel><title>Broken</title>'
            b"<item><title>No link</title></item></channel></rss>"
        ),
    ],
)
def test_preflight_rejects_unreadable_or_unusable_content(transport, body):
    transport(httpcore.Response(200, content=body))
    with pytest.raises(FeedValidationError):
        validate_feed(URL, source_type="publisher")


@pytest.mark.parametrize("status", [401, 403, 404, 408, 425, 429, 500, 503, 304])
def test_preflight_rejects_bad_http_responses(transport, status):
    transport(httpcore.Response(status))
    with pytest.raises(FeedValidationError) as caught:
        validate_feed(URL, source_type="publisher")
    assert caught.value.upstream_status == status
    assert caught.value.retryable is (status in {408, 425, 429} or status >= 500)


@pytest.mark.parametrize("error", [httpcore.ConnectError, httpcore.ReadTimeout])
def test_preflight_reports_transport_errors_without_raw_details(transport, error):
    transport(error("private transport details"))
    with pytest.raises(FeedValidationError) as caught:
        validate_feed(URL, source_type="publisher")
    assert caught.value.retryable is True
    assert "private transport details" not in str(caught.value)


def test_preflight_follows_redirect_and_uses_final_url_for_article_links(transport):
    atom = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title>'
        b'<entry><title>Test</title><link href="/post"/></entry></feed>'
    )
    calls = transport(
        httpcore.Response(302, headers={"location": "https://publisher.example/atom"}),
        httpcore.Response(200, content=atom),
    )
    validate_feed(URL, source_type="publisher")
    assert [request[1] for request in calls] == [URL, "https://publisher.example/atom"]


def test_preflight_keeps_private_redirect_guard_and_size_limits(transport, monkeypatch):
    from devfeed_core.config import get_settings

    transport(httpcore.Response(302, headers={"location": "http://169.254.169.254/"}))
    with pytest.raises(FeedValidationError):
        validate_feed(URL, source_type="publisher")
    monkeypatch.setattr(get_settings(), "feed_max_bytes", 4)
    transport(httpcore.Response(200, content=b"12345"))
    with pytest.raises(FeedValidationError, match="maximum response size"):
        validate_feed(URL, source_type="publisher")


def test_preparation_is_immutable_and_does_not_keep_fetch_validators(transport, rss_bytes):
    transport(
        httpcore.Response(
            200, headers={"etag": '"v1"', "last-modified": "Yesterday"}, content=rss_bytes
        )
    )
    body = SourceCreate(name="Example", feed_url=URL, source_type="publisher")
    prepared = services.validate_source(body)
    body.feed_url = "https://changed.example/rss"
    assert prepared.feed_url == URL
    with pytest.raises(FrozenInstanceError):
        prepared.feed_url = "https://changed.example/rss"
    stored = []
    session = SimpleNamespace(add=stored.append, flush=lambda: None)
    source = services.create_source(session, prepared)
    assert stored == [source]
    assert source.etag is None and source.last_modified is None
    assert source.last_success_at is None


@pytest.fixture
def api_client():
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app
    from fastapi.testclient import TestClient

    events = []
    fake_session = SimpleNamespace(commit=lambda: events.append("commit"))
    app = create_app()
    app.dependency_overrides[get_session] = lambda: fake_session
    with TestClient(app) as client:
        yield client, events


def source_record(prepared):
    return Source(
        publication_policy="manual",
        publication_policy_revision=0,
        id=uuid.uuid4(),
        **asdict(prepared),
        next_fetch_at=utcnow(),
        consecutive_failures=0,
        created_at=utcnow(),
        updated_at=utcnow(),
        approval_status="pending",
        submission_channel="api",
    )


@pytest.mark.parametrize("source_type", ["publisher", "aggregator"])
def test_api_fetches_and_parses_before_persistence(
    api_client, transport, rss_bytes, monkeypatch, source_type
):
    client, events = api_client
    transport(httpcore.Response(200, content=rss_bytes), events=events)

    def persist(session, prepared):
        assert isinstance(prepared, services.ValidatedSource)
        assert prepared.name == "Example"
        events.append("save")
        return source_record(prepared)

    monkeypatch.setattr(services, "create_source", persist)
    response = client.post(
        "/v1/sources",
        json={"name": "Example", "feed_url": URL, "source_type": source_type},
    )
    assert response.status_code == 201, response.text
    assert response.json()["feed_url"] == URL
    assert response.json()["approval_status"] == "pending"
    assert "enabled" not in response.json()
    assert response.json()["source_type"] == source_type
    assert events == ["fetch", "save", "commit"]


@pytest.mark.parametrize("status,body", [(404, b""), (503, b""), (200, b"<html>Website</html>")])
def test_api_rejects_invalid_feed_without_saving(api_client, transport, monkeypatch, status, body):
    client, events = api_client
    transport(httpcore.Response(status, content=body))
    monkeypatch.setattr(services, "create_source", lambda *args: pytest.fail("Saved invalid feed"))
    response = client.post(
        "/v1/sources",
        json={"name": "Example", "feed_url": URL, "source_type": "publisher"},
    )
    assert response.status_code == 422
    assert "Feed validation failed" in response.json()["detail"]
    assert response.json()["upstream_status"] == (status if status != 200 else None)
    assert response.json()["retryable"] is (status >= 500)
    assert events == []


def test_api_reports_network_failure_without_saving(api_client, transport, monkeypatch):
    client, events = api_client
    transport(httpcore.ReadTimeout("private transport details"))
    monkeypatch.setattr(services, "create_source", lambda *args: pytest.fail("Saved invalid feed"))
    response = client.post(
        "/v1/sources", json={"name": "Example", "feed_url": URL, "source_type": "publisher"}
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": "Feed validation failed: Feed transport error: ReadTimeout",
        "upstream_status": None,
        "retryable": True,
    }
    assert events == []


@pytest.mark.parametrize("disabled", [False, True])
def test_cli_rejects_invalid_feed_before_opening_transaction(
    transport, monkeypatch, capsys, disabled
):
    transport(httpcore.Response(200, content=b"<html>Not a feed</html>"))
    monkeypatch.setattr(commands, "session_factory", lambda: pytest.fail("Opened database"))
    arguments = ["sources", "add", URL, "--type", "publisher"]
    if disabled:
        arguments.append("--disabled")
    assert run(arguments) == 2
    output = capsys.readouterr()
    assert output.out == "" and "Feed validation failed" in output.err


def test_cli_import_checks_every_feed_before_opening_transaction(transport, monkeypatch, capsys):
    calls = transport(httpcore.Response(200, content=EMPTY_RSS), httpcore.Response(404))
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{URL}\nhttps://other.example/rss\n"))
    monkeypatch.setattr(commands, "session_factory", lambda: pytest.fail("Opened database"))
    assert run(["sources", "import", "-", "--type", "publisher"]) == 2
    assert len(calls) == 2
    assert "HTTP 404" in capsys.readouterr().err


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("source_type", ["publisher", "aggregator"])
def test_cli_success_only_persists_after_preflight(
    transport, monkeypatch, capsys, batch, source_type
):
    events = []
    calls = transport(httpcore.Response(200, content=EMPTY_RSS), events=events)

    @contextmanager
    def begin():
        events.append("begin")
        yield None
        events.append("commit")

    def persist(session, prepared):
        assert isinstance(prepared, services.ValidatedSource)
        assert prepared.source_type == source_type
        assert prepared.name == "Empty"
        events.append("save")
        return source_record(prepared), True, None

    monkeypatch.setattr(commands, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(services, "submit_source", persist)
    if batch:
        monkeypatch.setattr("sys.stdin", io.StringIO(f"{URL}\n{URL}\n"))
        arguments = ["sources", "import", "-", "--type", source_type]
    else:
        arguments = ["sources", "add", URL, "--type", source_type]
    assert run(arguments) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["created"] == 1
    assert len(calls) == 1
    assert events == ["fetch", "begin", "save", "commit"]


@pytest.mark.parametrize("extra", [{}, {"source_type": None}, {"source_type": "unknown"}])
def test_api_rejects_missing_or_invalid_type_before_preflight(api_client, monkeypatch, extra):
    client, events = api_client
    monkeypatch.setattr(services, "validate_source", lambda *_: pytest.fail("Fetched a source"))
    response = client.post("/v1/sources", json={"name": "Example", "feed_url": URL, **extra})
    assert response.status_code == 422
    assert events == []


@pytest.mark.parametrize("source_type", ["publisher", "aggregator"])
@pytest.mark.parametrize("extra", [{}, {"name": None}, {"name": ""}, {"name": " \n "}])
def test_api_uses_feed_title_when_name_is_missing(
    api_client, transport, rss_bytes, monkeypatch, source_type, extra
):
    client, events = api_client
    calls = transport(httpcore.Response(200, content=rss_bytes), events=events)

    def persist(session, prepared):
        assert prepared.name == "Engineering Example"
        events.append("save")
        return source_record(prepared)

    monkeypatch.setattr(services, "create_source", persist)
    response = client.post(
        "/v1/sources", json={"feed_url": URL, "source_type": source_type, **extra}
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Engineering Example"
    assert events == ["fetch", "save", "commit"] and len(calls) == 1


@pytest.mark.parametrize("body", [EMPTY_RSS, EMPTY_ATOM])
def test_explicit_name_takes_precedence_over_feed_title(transport, body):
    calls = transport(httpcore.Response(200, content=body))
    prepared = services.validate_source(
        SourceCreate(feed_url=URL, source_type="publisher", name=" Custom display name ")
    )
    assert prepared.name == "Custom display name" and len(calls) == 1


@pytest.mark.parametrize(
    "title", ["", "<title> \n </title>", "<title><![CDATA[<script>hidden</script>]]></title>"]
)
def test_untitled_feed_falls_back_to_hostname(transport, title):
    body = f'<rss version="2.0"><channel>{title}</channel></rss>'.encode()
    calls = transport(httpcore.Response(200, content=body))
    prepared = services.validate_source(SourceCreate(feed_url=URL, source_type="aggregator"))
    assert prepared.name == "example.com" and len(calls) == 1


def test_preflight_name_resolution_uses_input_snapshot(monkeypatch):
    body = SourceCreate(feed_url=URL, source_type="publisher")

    def preflight(url, *, source_type):
        assert url == URL and source_type == "publisher"
        body.feed_url = "https://changed.example/rss"
        body.name = "Changed during fetch"
        body.source_type = "aggregator"
        return ParsedFeed([], 0, 0, title="Original Feed")

    monkeypatch.setattr(services, "validate_feed", preflight)
    prepared = services.validate_source(body)
    assert prepared.feed_url == URL
    assert prepared.name == "Original Feed" and prepared.source_type == "publisher"


def test_cli_import_uses_each_feeds_title_without_extra_fetches(transport, monkeypatch, capsys):
    calls = transport(
        httpcore.Response(200, content=EMPTY_RSS.replace(b"Empty", b"First Feed")),
        httpcore.Response(200, content=EMPTY_ATOM.replace(b"Empty", b"Second Feed")),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{URL}\nhttps://other.example/atom\n{URL}\n"))
    saved = []

    @contextmanager
    def begin():
        assert len(calls) == 2
        yield None

    def persist(session, prepared):
        saved.append((prepared.feed_url, prepared.name))
        return source_record(prepared), True, None

    monkeypatch.setattr(commands, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(services, "submit_source", persist)
    assert run(["sources", "import", "-", "--type", "publisher"]) == 0
    assert saved == [(URL, "First Feed"), ("https://other.example/atom", "Second Feed")]
    assert len(calls) == 2
    assert json.loads(capsys.readouterr().out)["created"] == 2


def test_plain_text_404_is_two_readable_lines_not_duplicate_records(transport, monkeypatch, capsys):
    monkeypatch.setenv("DEVFEED_LOG_FORMAT", "text")
    monkeypatch.setenv("DEVFEED_LOG_LEVEL", "INFO")
    transport(httpcore.Response(404))
    monkeypatch.setattr(commands, "session_factory", lambda: pytest.fail("Opened database"))
    assert run(["sources", "add", URL, "--type", "publisher"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    lines = output.err.splitlines()
    assert len(lines) == 2
    assert "[cli] Checking RSS/Atom feed" in lines[0]
    assert (
        lines[1] == "error: Feed validation failed: HTTP 404 (not found). Check the RSS/Atom URL."
    )
    assert "command_id" not in output.err and "feed_id" not in output.err
    assert "command_rejected" not in output.err and "command_completed" not in output.err


def test_text_debug_keeps_full_command_diagnostics(transport, monkeypatch, capsys):
    monkeypatch.setenv("DEVFEED_LOG_FORMAT", "text")
    monkeypatch.setenv("DEVFEED_LOG_LEVEL", "DEBUG")
    transport(httpcore.Response(404))
    assert run(["sources", "add", URL, "--type", "publisher"]) == 2
    output = capsys.readouterr()
    assert "event=feed_validation_failed" in output.err
    assert "event=command_completed" in output.err
    assert "command_id=" in output.err and "feed_id=" in output.err
    assert URL not in output.err
