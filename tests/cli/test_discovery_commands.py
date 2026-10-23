"""Discovery commands preserve bounded network work, seed provenance and receipts."""

import hashlib
import uuid
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_cli import discovery_commands as cli
from devfeed_cli.commands import InputError
from devfeed_core import discovery
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.models import DiscoverySeed
from devfeed_core.services import RecordNotFound

from tests.support.source_readiness import NOW, configuration

BODY = b"https://publisher.example/engineering\n"


@pytest.fixture
def seed_store(monkeypatch):
    saved = DiscoverySeed(
        id=uuid.uuid4(),
        url="https://collection.example/publishers.txt",
        format="urls",
        checksum=None,
        last_checked_at=None,
        created_at=NOW,
    )
    session, phases = Mock(), []
    session.get.return_value = saved
    session.scalar.return_value = saved

    @contextmanager
    def factory():
        phases.append("open")
        try:
            yield session
        finally:
            phases.append("closed")

    factory.begin = factory
    provider = Mock(return_value=factory)
    monkeypatch.setattr(cli, "session_factory", provider)
    monkeypatch.setattr(cli, "utcnow", lambda: NOW)
    return NS(seed=saved, session=session, provider=provider, phases=phases)


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("allow_ai", [False, True])
def test_run_stops_at_empty_queue_preserves_completed_jobs_and_keeps_progress_off_json_stdout(
    monkeypatch, capsys, quiet, allow_ai
):
    from devfeed_core import config

    monkeypatch.setattr(config, "get_settings", lambda: configuration(ai_enabled=allow_ai))
    outcomes = iter(
        [
            {
                "id": str(uuid.uuid4()),
                "status": "succeeded",
                "candidate_status": "ready",
                "feeds_found": 1,
            },
            {"id": str(uuid.uuid4()), "status": "failed", "error": "robots_denied"},
            None,
        ]
    )
    calls = []

    def run_one(**options):
        calls.append(options)
        options["on_start"](
            {
                "stage": "discover",
                "name": "Publisher\x1b]0;title\x07",
                "candidate_id": uuid.uuid4(),
                "attempt": 1,
            }
        )
        return next(outcomes)

    monkeypatch.setattr(discovery, "run_one", run_one)
    output = cli.run_jobs(NS(limit=5, quiet=quiet, allow_ai=allow_ai))
    assert output["processed"] == 2 and len(calls) == 3
    assert [row["status"] for row in output["jobs"]] == ["succeeded", "failed"]
    assert all(
        options["allow_ai"] is allow_ai and options["assessor"] is cli.assess_sample
        for options in calls
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (captured.err == "") is quiet
    if not quiet:
        assert "No job claimed" in captured.err and "robots_denied" in captured.err
        assert "1 feed(s) found" in captured.err and "[3/5]" in captured.err
        assert "\x1b" not in captured.err and "\x07" not in captured.err


def test_allow_ai_requires_configured_ai_before_claiming_any_work(monkeypatch):
    from devfeed_core import config

    monkeypatch.setattr(config, "get_settings", lambda: configuration(ai_enabled=False))
    claim = Mock()
    monkeypatch.setattr(discovery, "run_one", claim)
    with pytest.raises(InputError, match="Enable AI"):
        cli.run_jobs(NS(limit=1, quiet=True, allow_ai=True))
    claim.assert_not_called()


def test_discovery_transport_error_reports_safe_reason_without_origin_credentials(monkeypatch):
    monkeypatch.setattr(cli, "runtime_invoke", lambda _ctx, execute, values: execute(NS(**values)))

    def failed(args):
        raise FeedError(
            "https://private:password@internal-host", reason="http_503", retryable=True, status=503
        )

    with pytest.raises(InputError, match=r"Publisher request failed \(http_503\)") as rejected:
        cli.invoke(None, failed, {})
    assert "password" not in str(rejected.value) and "internal-host" not in str(rejected.value)


@pytest.mark.parametrize(
    "format,body",
    [
        ("urls", BODY),
        (
            "opml",
            b'<opml version="2.0"><body><outline text="Engineering" '
            b'xmlUrl="https://publisher.example/feed" '
            b'htmlUrl="https://publisher.example/engineering"/></body></opml>',
        ),
    ],
)
def test_import_binds_parsed_publisher_hints_to_exact_file_checksum_and_origin(
    monkeypatch, tmp_path, format, body
):
    path = tmp_path / "publishers.txt"
    path.write_bytes(body)
    submit = Mock(return_value={"submitted": 1, "created": 1})
    monkeypatch.setattr(discovery, "import_publishers", submit)
    output = cli.import_file(NS(file=str(path), format=format, origin="operator-review"))
    hints, origin = submit.call_args.args
    assert len(hints) == 1 and hints[0].homepage == "https://publisher.example/engineering"
    assert hints[0].feed_hint == ("https://publisher.example/feed" if format == "opml" else None)
    assert origin == "operator-review" and submit.call_args.kwargs == {
        "checksum": hashlib.sha256(body).hexdigest()
    }
    assert output == {"submitted": 1, "created": 1}


@pytest.mark.parametrize(
    "url,format",
    [("http://127.0.0.1/private", "urls"), ("https://collection.example/list", "unsupported")],
)
def test_seed_registration_validates_network_target_and_format_before_database(
    seed_store, url, format
):
    with pytest.raises(ValueError):
        cli.seed_add(NS(url=url, format=format))
    seed_store.provider.assert_not_called()


def test_seed_registration_and_listing_return_the_scoped_saved_seed_without_network(
    monkeypatch, seed_store
):
    value = seed_store
    crawler = Mock()
    monkeypatch.setattr(cli, "CrawlSession", crawler)
    output = cli.seed_add(NS(url=value.seed.url, format=value.seed.format))
    requested = value.session.execute.call_args.args[0].compile().params
    assert requested["url"] == value.seed.url and requested["format"] == "urls"
    assert output["id"] == value.seed.id and output["checksum"] is None
    value.session.scalars.return_value = [value.seed]
    assert cli.seed_list(NS(limit=1, offset=2)) == [output]
    crawler.assert_not_called()


@pytest.mark.parametrize("failure", [None, "missing", "transport", "import"])
def test_seed_sync_closes_read_scope_before_fetch_and_updates_receipt_only_after_success(
    monkeypatch, seed_store, failure
):
    value = seed_store
    if failure == "missing":
        value.session.get.return_value = None

    def fetch(url):
        assert value.phases == ["open", "closed"] and url == value.seed.url
        if failure == "transport":
            raise FeedError("Upstream unavailable", reason="http_503", retryable=True)
        return FetchResult(status=200, body=BODY, final_url=url)

    get = Mock(side_effect=fetch)
    monkeypatch.setattr(cli, "CrawlSession", lambda: NS(get=get))

    def submit(hints, origin, **options):
        assert value.phases == ["open", "closed"]
        assert (
            origin == value.seed.url
            and hints[0].homepage == "https://publisher.example/engineering"
        )
        assert options == {"seed_id": value.seed.id, "checksum": hashlib.sha256(BODY).hexdigest()}
        if failure == "import":
            raise ValueError("Rejected import")
        return {"submitted": 1, "created": 1}

    publish = Mock(side_effect=submit)
    monkeypatch.setattr(discovery, "import_publishers", publish)
    if failure:
        with pytest.raises(
            {"missing": RecordNotFound, "transport": FeedError, "import": ValueError}[failure]
        ):
            cli.seed_sync(NS(id=value.seed.id))
        assert value.seed.checksum is None and value.seed.last_checked_at is None
        assert value.phases == ["open", "closed"]
        if failure == "missing":
            get.assert_not_called()
    else:
        assert cli.seed_sync(NS(id=value.seed.id)) == {"submitted": 1, "created": 1}
        assert value.phases == ["open", "closed", "open", "closed"]
        assert value.seed.checksum == hashlib.sha256(BODY).hexdigest()
        assert value.seed.last_checked_at == NOW
