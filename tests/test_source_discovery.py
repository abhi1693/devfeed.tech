import uuid
from datetime import timedelta

import pytest
from devfeed_cli.main import app
from devfeed_core import discovery
from devfeed_core.discovery_crawler import CrawlSession, Links, discover, feed_evidence
from devfeed_core.discovery_import import parse_import, publisher_hint
from devfeed_core.discovery_quality import score
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.models import (
    CandidateDiscovery,
    Source,
    SourceDiscoveryJob,
    SourceReview,
    utcnow,
)
from devfeed_core.services import OperationConflict
from sqlalchemy import func, select
from typer.testing import CliRunner


@pytest.fixture
def feed():
    items = "".join(
        f"<item><title>Implementing database indexes {i}</title>"
        f"<link>https://publisher.example/posts/{i}</link>"
        "<description>How to measure query plans and improve database indexes.</description>"
        "<pubDate>Mon, 14 Sep 2026 10:00:00 GMT</pubDate></item>"
        for i in range(3)
    )
    return (
        '<rss version="2.0"><channel><title>Engineering</title>' + items + "</channel></rss>"
    ).encode()


def test_import_keeps_hosted_publications_separate_and_hints():
    body = (
        b'<opml><body><outline text="A" htmlUrl="https://medium.com/a" '
        b'xmlUrl="https://medium.com/feed/a"/><outline text="B" '
        b'htmlUrl="https://medium.com/b" xmlUrl="https://medium.com/feed/b"/></body></opml>'
    )
    hints = parse_import(body, "opml")
    assert [h.homepage for h in hints] == ["https://medium.com/a", "https://medium.com/b"]
    assert hints[0].feed_hint == "https://medium.com/feed/a"
    assert (
        publisher_hint("https://publisher.example/blog/?utm_source=test").homepage
        == "https://publisher.example/blog"
    )


@pytest.mark.parametrize(
    "body,format",
    [
        (b'<!DOCTYPE opml [<!ENTITY x SYSTEM "file:///etc/passwd">]><opml/>', "opml"),
        (b"<opml", "opml"),
        (b"https://127.0.0.1/feed", "urls"),
        (b"https://user:password@publisher.example/", "urls"),
        (b'[{"homepage_url":"https://publisher.example", "name":12}]', "json"),
        (b"x" * 1_000_001, "urls"),
        ("\n".join(f"https://publisher{i}.example/" for i in range(1001)).encode(), "urls"),
    ],
)
def test_import_rejects_unsafe_or_unbounded_inputs(body, format):
    with pytest.raises(ValueError):
        parse_import(body, format)


def test_json_and_markdown_adapters():
    assert parse_import(
        b'[{"homepage_url":"https://publisher.example/blog","feed_url":"https://publisher.example/rss"}]',
        "json",
    )[0].feed_hint.endswith("/rss")
    assert (
        len(
            parse_import(
                b"[Blog](https://publisher.example) [Navigation](https://github.com/org/repo)",
                "markdown",
            )
        )
        == 1
    )


def test_offline_cli_help(monkeypatch):
    monkeypatch.setattr(discovery, "session_factory", lambda: pytest.fail("Help opened database"))
    result = CliRunner().invoke(app, ["discovery", "--help"])
    assert result.exit_code == 0
    assert "approve" in result.stdout and "seeds" in result.stdout
    assert CliRunner().invoke(app, ["discovery", "run", "--limit", "0"]).exit_code == 2


def test_link_extraction_rejects_private_links():
    links = Links("https://publisher.example/blog/")
    links.feed(
        '<link rel="alternate" type="application/atom+xml" href="feed.xml">'
        '<a href="http://127.0.0.1/rss">Feed</a><a href="/news">News</a>'
    )
    assert links.feeds == ["https://publisher.example/blog/feed.xml"]
    assert links.pages == ["https://publisher.example/news"]


def test_robots_denial_and_request_budget(monkeypatch):
    from devfeed_core import discovery_crawler as crawler

    calls = []

    def fetch(url, *args, before_request, **kwargs):
        before_request(url)
        calls.append(url)
        return FetchResult(200, b"User-agent: *\nDisallow: /private", url)

    monkeypatch.setattr(crawler, "_fetch", fetch)
    monkeypatch.setattr(crawler.time, "sleep", lambda _: None)
    client = CrawlSession()
    with pytest.raises(FeedError, match="Robots disallows"):
        client.get("https://publisher.example/private")
    assert calls == ["https://publisher.example/robots.txt"]
    client.requests = 20
    with pytest.raises(FeedError, match="budget"):
        client.get("https://publisher.example/public")
    assert len(calls) == 1


def test_robots_failure_never_fetches_content(monkeypatch):
    calls = []

    def fetch(url, *args, before_request, **kwargs):
        before_request(url)
        calls.append(url)
        raise FeedError("Unavailable", status=503, retryable=True)

    monkeypatch.setattr("devfeed_core.discovery_crawler._fetch", fetch)
    with pytest.raises(FeedError) as error:
        CrawlSession().get("https://publisher.example/blog")
    assert error.value.reason == "robots_unavailable"
    assert error.value.retryable
    assert calls == ["https://publisher.example/robots.txt"]


def test_discovery_retains_multiple_feeds(monkeypatch, feed):
    def get(self, url):
        if url == "https://publisher.example/":
            return FetchResult(
                200,
                (
                    b'<link rel="alternate" type="application/rss+xml" href="/feed">'
                    b'<link rel="alternate" type="application/atom+xml" href="/atom">'
                ),
                url,
            )
        if url not in {"https://publisher.example/feed", "https://publisher.example/atom"}:
            raise FeedError("Not found", status=404, reason="http_error")
        return FetchResult(200, feed, url)

    monkeypatch.setattr(CrawlSession, "get", get)
    result = discover("https://publisher.example/", [])
    assert {entry["url"] for entry in result["feeds"]} == {
        "https://publisher.example/feed",
        "https://publisher.example/atom",
    }
    assert all(item["publisher_type"] == "direct" for item in result["feeds"])


def test_feed_ownership_and_unknown_scores(feed):
    evidence = feed_evidence(feed, "https://publisher.example/feed", "https://publisher.example/")
    assert evidence["publisher_ratio"] == 1
    assert score(evidence)["score"] is None
    assert score(evidence)["recommendation"] == "review"
    hosted = feed_evidence(feed, "https://medium.com/feed/a", "https://medium.com/a")
    assert hosted["publisher_ratio"] == 0
    assert hosted["publisher_type"] == "probable_aggregator"
    sparse = {**evidence, "sample": evidence["sample"][:1]}
    assert score(sparse)["components"]["publisher_ownership_heuristic"] is None


def test_quality_requires_complete_grounded_evidence(feed):
    evidence = feed_evidence(feed, "https://publisher.example/feed", "https://publisher.example/")
    output = {
        "confidence": 0.95,
        "entries": [
            {
                "index": i,
                "relevance": "relevant",
                "depth": "substantial",
                "promotion": "editorial",
                "quote": entry["summary"],
            }
            for i, entry in enumerate(evidence["sample"])
        ],
    }
    result = score(evidence, output)
    assert result["score"] == 100
    assert result["automatic_admission"] is False
    output["entries"][0]["quote"] = "This evidence was invented and is not present."
    with pytest.raises(ValueError, match="quote"):
        score(evidence, output)
    output["entries"] = output["entries"][1:]
    with pytest.raises(ValueError, match="exactly once"):
        score(evidence, output)


@pytest.mark.integration
def test_import_is_idempotent_and_rejection_survives_reimport(database):
    hint = publisher_hint("https://publisher.example/", feed="https://publisher.example/rss")
    first = discovery.import_publishers([hint], "test")
    candidate_id = uuid.UUID(first["candidates"][0])
    assert discovery.import_publishers([hint], "test")["created"] == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(CandidateDiscovery)) == 1
        assert session.scalar(select(func.count()).select_from(SourceDiscoveryJob)) == 1
        assert session.scalar(select(func.count()).select_from(Source)) == 1
    discovery.reject(candidate_id, "tester", "Not a technical publisher")
    discovery.import_publishers([hint], "another-seed")
    assert discovery.show(candidate_id)["status"] == "rejected"
    with pytest.raises(OperationConflict, match="Reopening"):
        discovery.request(candidate_id)
    discovery.request(candidate_id, reopen=True, actor="tester", reason="New technical section")
    assert discovery.show(candidate_id)["status"] == "pending"


@pytest.mark.integration
def test_worker_admission_and_existing_source_preservation(database, monkeypatch, feed):
    def get(self, url):
        if url != "https://publisher.example/":
            raise FeedError("Not found", status=404, reason="http_error")
        return FetchResult(200, feed, url)

    monkeypatch.setattr(CrawlSession, "get", get)
    added = discovery.import_publishers([publisher_hint("https://publisher.example/")], "test")
    candidate_id = uuid.UUID(added["candidates"][0])
    assert discovery.run_one()["status"] == "succeeded"
    state = discovery.show(candidate_id)
    assert state["status"] == "ready"
    assert len(state["assessments"]) == 1
    admitted = discovery.approve(candidate_id, "tester", "Reviewed original engineering content")
    assert admitted["status"] == "approved"
    with database() as session:
        source = session.get(Source, admitted["source_id"])
        assert source.approval_status == "approved"
        assert source.publication_policy == "manual"
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1
    # A second candidate finding a rejected existing feed must never reapprove it.
    with database.begin() as session:
        source = session.get(Source, admitted["source_id"])
        source.approval_status, source.enabled = "rejected", False
    second = discovery.import_publishers(
        [publisher_hint("https://publisher.example/blog", feed="https://publisher.example/")],
        "test",
    )
    second_id = uuid.UUID(second["candidates"][0])
    discovery.run_one()
    state = discovery.show(second_id)
    feed_id = next(
        item["id"] for item in state["feeds"] if item["url"] == "https://publisher.example/"
    )
    assert state["selected_feed_id"] == feed_id
    linked = discovery.approve(second_id, "tester", "Reviewed candidate")
    assert linked["status"] == "linked"
    with database() as session:
        source = session.get(Source, admitted["source_id"])
        assert source.approval_status == "rejected" and source.enabled is False
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1


@pytest.mark.integration
def test_expired_lease_and_retry_backoff(database, monkeypatch):
    candidate_id = uuid.UUID(
        discovery.import_publishers([publisher_hint("https://publisher.example/")], "test")[
            "candidates"
        ][0]
    )
    with database.begin() as session:
        job = session.scalar(select(SourceDiscoveryJob))
        job.status, job.lease_token = "running", uuid.uuid4()
        job.lease_until = utcnow() - timedelta(seconds=1)
    monkeypatch.setattr(discovery, "discover", lambda *args: {"feeds": [], "retryable": True})
    result = discovery.run_one()
    assert result["status"] == "queued"
    assert discovery.run_one() is None
    assert discovery.show(candidate_id)["status"] == "retry_wait"


@pytest.mark.integration
def test_ai_is_explicit_and_selected_feed_must_belong_to_candidate(database, monkeypatch, feed):
    monkeypatch.setattr(CrawlSession, "get", lambda self, url: FetchResult(200, feed, url))
    candidate_id = uuid.UUID(
        discovery.import_publishers([publisher_hint("https://publisher.example/")], "test")[
            "candidates"
        ][0]
    )
    discovery.run_one()
    with database.begin() as session:
        discovery.enqueue(session, candidate_id, "assess")
    assert discovery.run_one() is None

    def assess(sample):
        return {
            "confidence": 0.95,
            "entries": [
                {
                    "index": i,
                    "relevance": "relevant",
                    "depth": "substantial",
                    "promotion": "editorial",
                    "quote": entry["summary"],
                }
                for i, entry in enumerate(sample)
            ],
        }

    assert discovery.run_one(allow_ai=True, assessor=assess)["status"] == "succeeded"
    assert discovery.show(candidate_id)["assessments"][0]["evidence"]["score"] == 100
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 1


@pytest.mark.integration
def test_rejection_during_crawl_cannot_be_overwritten(database, monkeypatch):
    candidate_id = uuid.UUID(
        discovery.import_publishers([publisher_hint("https://publisher.example/")], "test")[
            "candidates"
        ][0]
    )

    def crawl(*args):
        discovery.reject(candidate_id, "tester", "Reject during running discovery")
        with pytest.raises(OperationConflict, match="running"):
            discovery.request(candidate_id, reopen=True, actor="tester", reason="Too soon")
        assert discovery.run_one() is None
        return {"feeds": [], "retryable": True}

    monkeypatch.setattr(discovery, "discover", crawl)
    assert discovery.run_one()["error"] == "candidate_reviewed"
    assert discovery.show(candidate_id)["status"] == "rejected"


@pytest.mark.integration
def test_crash_recovery_has_finite_attempts(database, monkeypatch):
    discovery.import_publishers([publisher_hint("https://publisher.example/")], "test")
    with database.begin() as session:
        job = session.scalar(select(SourceDiscoveryJob))
        job.status, job.attempts, job.lease_token = "running", 5, uuid.uuid4()
        job.lease_until = utcnow() - timedelta(seconds=1)
    monkeypatch.setattr(discovery, "discover", lambda *args: pytest.fail("Exhausted run fetched"))
    assert discovery.run_one()["status"] == "failed"
    assert discovery.run_one() is None


@pytest.mark.integration
def test_feed_selection_rejects_other_candidate_feed(database):
    from devfeed_core.models import CandidateFeed
    from devfeed_core.services import RecordNotFound

    ids = discovery.import_publishers(
        [
            publisher_hint("https://publisher.example/a"),
            publisher_hint("https://publisher.example/b"),
        ],
        "test",
    )["candidates"]
    with database.begin() as session:
        feed = CandidateFeed(
            candidate_id=uuid.UUID(ids[1]), url="https://publisher.example/feed", evidence={}
        )
        session.add(feed)
        session.flush()
        feed_id = feed.id
    with pytest.raises(RecordNotFound, match="belong"):
        discovery.select_feed(uuid.UUID(ids[0]), feed_id)


def test_quality_allows_uncertainty_without_invented_quotes(feed):
    evidence = feed_evidence(feed, "https://publisher.example/feed", "https://publisher.example/")
    output = {
        "confidence": 0.1,
        "entries": [
            {
                "index": i,
                "relevance": "uncertain",
                "depth": "uncertain",
                "promotion": "uncertain",
                "quote": "",
            }
            for i in range(3)
        ],
    }
    result = score(evidence, output)
    assert result["score"] is None
    assert result["recommendation"] == "review"


def test_ai_sample_respects_content_cutoff(monkeypatch, feed):
    from datetime import date

    from devfeed_core.config import get_settings
    from devfeed_core.discovery_quality import assessment_evidence

    monkeypatch.setattr(get_settings(), "ai_content_not_before", date(2026, 9, 15))
    evidence = feed_evidence(feed, "https://publisher.example/feed", "https://publisher.example/")
    result = assessment_evidence(evidence)
    assert result["sample"] == []
    assert result["publisher_ratio"] is None


@pytest.mark.integration
def test_sparse_sample_does_not_spend_ai_budget(database, monkeypatch, feed):
    monkeypatch.setattr(CrawlSession, "get", lambda self, url: FetchResult(200, feed, url))
    candidate_id = uuid.UUID(
        discovery.import_publishers([publisher_hint("https://publisher.example/")], "test")[
            "candidates"
        ][0]
    )
    discovery.run_one()
    from devfeed_core.models import CandidateFeed

    with database.begin() as session:
        row = session.scalar(select(CandidateFeed))
        row.evidence = {**row.evidence, "sample": row.evidence["sample"][:1]}
    with database.begin() as session:
        discovery.enqueue(session, candidate_id, "assess")
    result = discovery.run_one(
        allow_ai=True, assessor=lambda _: pytest.fail("Sparse sample called AI")
    )
    assert result["status"] == "succeeded"
    assert discovery.show(candidate_id)["assessments"][0]["evidence"]["recommendation"] == "review"


@pytest.mark.integration
def test_seed_sync_is_repeatable_and_retains_checksum(database, monkeypatch):
    from types import SimpleNamespace

    from devfeed_cli.discovery_commands import seed_add, seed_sync
    from devfeed_core.models import DiscoverySeed

    seed = seed_add(SimpleNamespace(url="https://collection.example/feeds.opml", format="opml"))
    body = (
        b'<opml><body><outline text="Publisher" htmlUrl="https://publisher.example/" '
        b'xmlUrl="https://publisher.example/feed"/></body></opml>'
    )
    monkeypatch.setattr(CrawlSession, "get", lambda self, url: FetchResult(200, body, url))
    assert seed_sync(SimpleNamespace(id=seed["id"]))["created"] == 1
    assert seed_sync(SimpleNamespace(id=seed["id"]))["created"] == 0
    with database() as session:
        row = session.get(DiscoverySeed, seed["id"])
        assert row.checksum and row.last_checked_at
        provenance = session.scalar(select(CandidateDiscovery))
        assert provenance.seed_id == row.id
        assert provenance.checksum == row.checksum


@pytest.mark.parametrize("subcommand", ["add", "list", "sync"])
def test_seed_help_is_offline(subcommand, monkeypatch):
    monkeypatch.setattr(discovery, "session_factory", lambda: pytest.fail("Help opened database"))
    assert CliRunner().invoke(app, ["discovery", "seeds", subcommand, "--help"]).exit_code == 0


@pytest.mark.parametrize("quiet", [False, True])
def test_run_progress_keeps_stdout_json_and_reports_active_work(monkeypatch, quiet):
    import json
    import time

    from devfeed_cli.discovery_commands import RunProgress

    monkeypatch.setattr(RunProgress, "heartbeat_seconds", 0.01)
    calls = []

    def run_one(*, on_start, **kwargs):
        calls.append(True)
        if len(calls) > 1:
            return None
        on_start(
            {
                "candidate_id": "candidate-id",
                "name": "Example\nPublisher",
                "stage": "discover",
                "attempt": 1,
            }
        )
        time.sleep(0.04)
        return {"status": "succeeded", "candidate_status": "ready", "feeds_found": 1}

    monkeypatch.setattr(discovery, "run_one", run_one)
    result = CliRunner().invoke(
        app, ["discovery", "run", "--limit", "2", *(["--quiet"] if quiet else [])]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["processed"] == 1
    if quiet:
        assert "[1/2]" not in result.stderr
    else:
        assert "Started discover: Example Publisher" in result.stderr
        assert "Still working" in result.stderr
        assert "1 feed(s) found" in result.stderr
        assert "No job claimed" in result.stderr
        assert result.stderr.index("Started") < result.stderr.index("Finished")


def test_progress_thread_stops_on_interruption():
    from devfeed_cli.discovery_commands import RunProgress

    progress = RunProgress(1, 20)
    with pytest.raises(KeyboardInterrupt), progress:
        raise KeyboardInterrupt
    assert progress.stop.is_set()
    assert not progress.thread.is_alive()


@pytest.mark.parametrize(
    "rules,path,allowed",
    [
        ("Disallow: /private/*", "/private/article", False),
        ("Disallow: /\nAllow: /feed", "/feed", True),
        ("Allow: /feed\nDisallow: /", "/feed", True),
        ("Disallow: /feed\nAllow: /feed", "/feed", True),
        ("Disallow: /*.xml$", "/rss.xml", False),
        ("Disallow: /*.xml$", "/rss.xml/extra", True),
        ("Disallow: /private/%7Euser", "/private/~user", False),
        ("Disallow: /\n\nUser-agent: DevFeed\nAllow: /", "/feed", True),
    ],
)
def test_discovery_robots_matching(monkeypatch, rules, path, allowed):
    def fetch(url, *args, before_request, **kwargs):
        before_request(url)
        return FetchResult(200, ("User-agent: *\n" + rules).encode(), url)

    monkeypatch.setattr("devfeed_core.discovery_crawler._fetch", fetch)
    monkeypatch.setattr("devfeed_core.discovery_crawler.time.sleep", lambda _: None)
    if allowed:
        CrawlSession().get("https://publisher.example" + path)
    else:
        with pytest.raises(FeedError) as exc:
            CrawlSession().get("https://publisher.example" + path)
        assert exc.value.reason == "robots_denied"


@pytest.mark.integration
@pytest.mark.parametrize("candidate_status", ["admitted", "linked"])
def test_admin_can_delete_discovery_linked_source(database, candidate_status):
    from devfeed_admin_api.sources import remove
    from devfeed_core.models import SourceCandidate

    with database.begin() as session:
        source = Source(
            name="Delete me", feed_url="https://publisher.example/feed", source_type="publisher"
        )
        session.add(source)
        session.flush()
        source_id = source.id
        row = SourceCandidate(
            name="Keep history",
            identity_url="https://publisher.example/",
            status=candidate_status,
            source_id=source.id,
            review={"decision": "approved", "actor": "tester"},
        )
        session.add(row)
        session.flush()
        candidate_id = row.id
    with database() as session:
        assert remove(source_id, session).status_code == 204
    with database() as session:
        assert session.get(Source, source_id) is None
        assert session.get(SourceCandidate, candidate_id) is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "code,expected_status",
    [
        ("codex_timeout", "queued"),
        ("codex_unavailable", "queued"),
        ("codex_usage_limit", "queued"),
        ("codex_rate_limited", "queued"),
        ("codex_server_overloaded", "queued"),
        ("invalid_analysis_output", "failed"),
        ("unexpected_tool_execution", "failed"),
    ],
)
def test_assessment_failure_recovery(database, monkeypatch, feed, code, expected_status):
    from devfeed_aggregator.codex_client import AnalysisError

    monkeypatch.setattr(CrawlSession, "get", lambda self, url: FetchResult(200, feed, url))
    candidate_id = uuid.UUID(
        discovery.import_publishers([publisher_hint("https://publisher.example/")], "test")[
            "candidates"
        ][0]
    )
    discovery.run_one()
    with database.begin() as session:
        discovery.enqueue(session, candidate_id, "assess")

    def fail(sample):
        raise AnalysisError(code, retry_after=3600)

    before = utcnow()
    result = discovery.run_one(allow_ai=True, assessor=fail)
    assert result["status"] == expected_status
    assert result["error"] == code
    with database.begin() as session:
        job = session.scalar(select(SourceDiscoveryJob).where(SourceDiscoveryJob.stage == "assess"))
        assert job.result["retry_after"] >= 3600
        if expected_status == "queued":
            assert job.available_at >= before + timedelta(seconds=3600)
            job.available_at = utcnow()
            job.attempts = 4
    if expected_status == "queued":
        assert discovery.run_one(allow_ai=True, assessor=fail)["status"] == "failed"
        assert discovery.run_one(allow_ai=True, assessor=fail) is None
    assert discovery.show(candidate_id)["status"] == "ready"


@pytest.mark.parametrize("atom_valid", [True, False])
@pytest.mark.integration
def test_opml_selects_atom_before_rss_and_exposes_pending_source_feed(
    atom_valid, feed, database, admin_client, monkeypatch
):
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Engineering</title>
        <id>https://publisher.example/</id><updated>2026-09-14T10:00:00Z</updated>
        <entry><id>https://publisher.example/post</id><title>Building databases</title>
        <link href="https://publisher.example/post"/>
        <updated>2026-09-14T10:00:00Z</updated></entry></feed>"""

    def get(self, url):
        if url == "https://publisher.example/":
            body = b'<link rel="alternate" type="application/atom+xml" href="/updates">'
        elif url == "https://publisher.example/updates":
            body = atom if atom_valid else b"<html>Not a feed</html>"
        elif url == "https://publisher.example/rss":
            body = feed
        else:
            raise FeedError("Not found", status=404, reason="http_error")
        return FetchResult(200, body, url)

    monkeypatch.setattr(CrawlSession, "get", get)
    hints = parse_import(
        b'<opml><body><outline text="Engineering" htmlUrl="https://publisher.example/" '
        b'xmlUrl="https://publisher.example/rss"/></body></opml>',
        "opml",
    )
    source_id = discovery.import_publishers(hints, "https://collection.example/sources.opml")[
        "candidates"
    ][0]
    assert discovery.run_one()["status"] == "succeeded"
    source = admin_client.get(f"/v1/admin/sources/{source_id}").json()
    assert source["feed_url"] == (
        "https://publisher.example/updates" if atom_valid else "https://publisher.example/rss"
    )
    assert source["approval_status"] == "pending"
    assert source["enabled"] is False


def test_discovery_does_not_accept_empty_atom():
    with pytest.raises(FeedError, match="no usable entries"):
        feed_evidence(
            b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Empty</title></feed>',
            "https://publisher.example/atom",
            "https://publisher.example/",
        )
