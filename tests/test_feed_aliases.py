"""Redirect aliases share source identity without merging distinct feeds."""

import pytest
from devfeed_core import discovery, services
from devfeed_core.discovery_import import publisher_hint
from devfeed_core.feeds import validation
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.models import CandidateFeed, Source, SourceCandidate
from devfeed_core.schemas import SourceCreate
from source_suggestions import suggest_source
from sqlalchemy import func, select

pytestmark = pytest.mark.integration
DESTINATION = "https://publisher.example/blog/feed/"
ALIAS = "https://publisher.example/blog/feed.xml"


@pytest.fixture
def alias_feed(database, monkeypatch, rss_bytes):
    monkeypatch.setattr(
        validation, "fetch_feed", lambda url: FetchResult(200, rss_bytes, DESTINATION)
    )


def validated(url):
    return services.validate_source(SourceCreate(feed_url=url, source_type="publisher"))


@pytest.mark.parametrize("channel", ["admin", "user"])
def test_alias_submission_stores_destination_and_rejects_repeat(
    alias_feed, database, admin_client, channel
):
    def submit(url):
        body = {"feed_url": url, "source_type": "publisher"}
        if channel == "admin":
            return admin_client.post("/v1/admin/sources", json=body)
        return suggest_source(json=body)

    first = submit(ALIAS)
    assert first.status_code == 201, first.text
    repeated = submit(DESTINATION)
    assert repeated.status_code == 409, repeated.text
    assert submit(ALIAS).status_code == 409
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Source)) == 1
        assert session.scalar(select(Source.feed_url)) == DESTINATION


def test_cli_alias_reuses_existing_source_without_changing_review(alias_feed, database):
    with database.begin() as session:
        first, created, _ = services.submit_source(session, validated(ALIAS))
        assert created and first.feed_url == DESTINATION
        identifier = first.id
    with database.begin() as session:
        repeated, created, job = services.submit_source(session, validated(DESTINATION))
        assert repeated.id == identifier and not created and job is None
        assert repeated.approval_status == "pending"


def test_discovery_handoff_reuses_source_created_through_alias(alias_feed, database):
    with database.begin() as session:
        source = services.create_source(session, validated(ALIAS))
        identifier = source.id
    discovery.import_publishers([publisher_hint("https://publisher.example/")], "test")
    with database.begin() as session:
        candidate = session.scalar(select(SourceCandidate))
        feed = CandidateFeed(candidate_id=candidate.id, url=DESTINATION, evidence={})
        session.add(feed)
        session.flush()
        candidate.selected_feed_id = feed.id
        discovery.handoff(session, candidate)
        session.flush()
        assert candidate.source_id == identifier and candidate.status == "linked"
        assert session.scalar(select(func.count()).select_from(Source)) == 1
