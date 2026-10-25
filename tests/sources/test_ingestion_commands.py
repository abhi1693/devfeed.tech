"""Ingestion requests and worker application preserve source authority and retry state."""

import copy
import time
import uuid
from datetime import timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import tasks
from devfeed_core import config, jobs, polling, services
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import IngestionJob
from devfeed_core.schemas import SourceDecision
from devfeed_core.source_types import SourceType

from tests.images.test_worker_storage import Factory
from tests.support.source_readiness import NOW, configuration, source


@pytest.fixture
def ingestion(monkeypatch):
    current = source(
        approval="approved",
        feed_url="https://publisher.example/feed",
        polling_mode="fixed",
        poll_interval_seconds=1800,
        scheduled_polling_mode="fixed",
        scheduled_interval_seconds=1800,
        polling_state={"observed_at": (NOW - timedelta(hours=1)).isoformat()},
        last_attempt_at=NOW - timedelta(seconds=30),
        consecutive_failures=2,
        last_error="Previous attempt failed",
        etag='"old"',
        last_modified="old-date",
    )
    row = IngestionJob(
        id=uuid.uuid4(),
        source_id=current.id,
        status="queued",
        attempts=0,
        created_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
        automatic=True,
        articles_created=0,
        entries_seen=0,
        entries_skipped=0,
    )
    start_job(row, current.last_attempt_at, 300)
    session = Mock()
    session.scalar.return_value = current
    factory = Factory(session)
    monkeypatch.setattr(tasks, "utcnow", lambda: NOW)
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)
    settings = configuration()
    monkeypatch.setattr(polling, "get_settings", lambda: settings)
    owner = Mock(return_value=row)
    monkeypatch.setattr(tasks, "owned_job", owner)
    return SimpleNamespace(source=current, row=row, session=session, factory=factory, owner=owner)


def apply(v):
    tasks._ingest_claimed(
        v.factory,
        v.row.id,
        v.row.lease_token,
        v.source.feed_url,
        v.source.etag,
        v.source.last_modified,
        time.perf_counter(),
        SourceType.PUBLISHER,
    )


@pytest.mark.parametrize("response", ["new-validators", "clear-validators", "unchanged"])
def test_success_resets_failures_and_emits_committed_counts_with_bounded_discovery_delays(
    ingestion, monkeypatch, response
):
    v = ingestion
    status = 304 if response == "unchanged" else 200
    activity = [None, None, NOW - timedelta(minutes=10), NOW - timedelta(days=8)]
    entries = "".join(
        f"<item><title>Software API design {index}</title>"
        f"<link>https://publisher.example/guide-{index}</link>"
        "<description>Practical API engineering guidance.</description>"
        + (f"<pubDate>{format_datetime(stamp)}</pubDate>" if stamp else "")
        + "</item>"
        for index, stamp in enumerate(activity)
    )
    body = (
        "<rss version='2.0'><channel><title>Engineering</title>" + entries + "</channel></rss>"
    ).encode()
    result = FetchResult(
        status,
        body if status == 200 else b"",
        v.source.feed_url,
        etag='"new"' if response == "new-validators" else None,
        last_modified="new-date" if response == "new-validators" else None,
    )
    fetched = []

    def fetch(*args):
        assert not v.factory.open
        fetched.append(args)
        return result

    monkeypatch.setattr(tasks, "fetch_feed", fetch)

    def store(session, identifier, parsed, *, activity):
        assert v.factory.open and session is v.session and identifier == v.source.id
        assert parsed.entries and parsed.entries[0].canonical_url.startswith("https://")
        assert len(parsed.entries) == parsed.seen == 4 and parsed.skipped == 0
        activity.extend(entry.published_at for entry in parsed.entries)
        return 2

    store_call = Mock(side_effect=store)
    monkeypatch.setattr(tasks, "store_entries", store_call)
    instruments = {
        name: Mock()
        for name in (
            "polling_interval",
            "polling_queue_delay",
            "polling_new_entries",
            "polling_unchanged",
            "polling_discovery_delay",
        )
    }

    # Telemetry is read after the database scope; an unavailable recorder must
    # never be needed to make the feed's durable decision.
    def telemetry():
        assert not v.factory.open
        return SimpleNamespace(instruments=instruments)

    monkeypatch.setattr(tasks, "current", telemetry)
    observe = Mock(wraps=polling.observe)
    monkeypatch.setattr(tasks, "observe", observe)
    apply(v)
    assert fetched == [(v.source.feed_url, '"old"', "old-date")]
    assert v.row.status == "succeeded" and v.row.http_status == status
    assert v.row.finished_at == NOW and v.row.lease_token is v.row.lease_until is None
    assert v.source.last_success_at == NOW and v.source.last_error is None
    assert v.source.consecutive_failures == 0
    assert v.source.next_fetch_at == NOW + timedelta(seconds=1800)
    attributes = {"mode": "fixed"}
    instruments["polling_interval"].record.assert_called_once_with(1800, attributes)
    instruments["polling_queue_delay"].record.assert_called_once_with(30, attributes)
    if status == 200:
        store_call.assert_called_once()
        assert v.row.new_source_entries == len(activity) and v.row.articles_created == 2
        observe.assert_called_once_with(v.source, NOW, 3, automatic=True)
        instruments["polling_new_entries"].add.assert_called_once_with(1, attributes)
        instruments["polling_unchanged"].add.assert_not_called()
        instruments["polling_discovery_delay"].record.assert_called_once_with(600.0, attributes)
    else:
        store_call.assert_not_called()
        assert v.row.new_source_entries == 0 and v.row.articles_created == 0
        observe.assert_called_once_with(v.source, NOW, 0, automatic=True)
        instruments["polling_unchanged"].add.assert_called_once_with(1, attributes)
        instruments["polling_new_entries"].add.assert_not_called()
        instruments["polling_discovery_delay"].record.assert_not_called()
    assert (v.source.etag, v.source.last_modified) == {
        "new-validators": ('"new"', "new-date"),
        "clear-validators": (None, None),
        "unchanged": ('"old"', "old-date"),
    }[response]


@pytest.mark.parametrize("authority", ["lost-lease", "paused"])
def test_results_cannot_apply_after_ownership_is_lost_or_the_operator_pauses_polling(
    ingestion, monkeypatch, authority
):
    v = ingestion
    before = (
        v.source.last_success_at,
        v.source.last_error,
        v.source.consecutive_failures,
        dict(v.source.polling_state),
        v.source.etag,
    )

    def fetch(*_):
        if authority == "lost-lease":
            v.owner.return_value = None
        else:
            v.source.enabled = False
        return FetchResult(304, b"", v.source.feed_url)

    monkeypatch.setattr(tasks, "fetch_feed", fetch)
    store, telemetry = Mock(), Mock()
    monkeypatch.setattr(tasks, "store_entries", store)
    monkeypatch.setattr(tasks, "current", telemetry)
    apply(v)
    assert (
        v.source.last_success_at,
        v.source.last_error,
        v.source.consecutive_failures,
        v.source.polling_state,
        v.source.etag,
    ) == before
    store.assert_not_called()
    telemetry.assert_not_called()
    if authority == "paused":
        assert v.row.status == "failed" and v.row.error == "Source is disabled"
        assert v.row.lease_token is v.row.lease_until is None
    else:
        assert v.row.status == "running" and v.row.lease_token is not None


def test_duplicate_delivery_without_a_claim_never_fetches_or_starts_a_second_attempt(monkeypatch):
    session, factory = Mock(), Mock()
    from contextlib import nullcontext

    factory.begin.return_value = nullcontext(session)
    monkeypatch.setattr(tasks, "session_factory", lambda: factory)
    claim, fetch = Mock(return_value=None), Mock()
    monkeypatch.setattr(tasks, "claim_job", claim)
    monkeypatch.setattr(tasks, "fetch_feed", fetch)
    identifier = uuid.uuid4()
    tasks._ingest(str(identifier))
    claim.assert_called_once_with(session, identifier)
    fetch.assert_not_called()


@pytest.mark.parametrize("classification", [None, "manual", "ai", "enabled-ai"])
def test_feed_import_preserves_editorial_classification_and_only_uses_heuristics_as_a_baseline(
    monkeypatch, rss_bytes, classification
):
    from devfeed_core.feeds.parser import ParsedFeed, parse_feed
    from devfeed_core.models import ArticleTag, ArticleTopic, Tag, Topic
    from sqlalchemy.dialects import postgresql

    parsed = parse_feed(rss_bytes, "https://example.com/rss", NOW, source_type=SourceType.PUBLISHER)
    entry = parsed.entries[0]
    article_id, source_id, supplied_id = (uuid.uuid4() for _ in range(3))
    topic = Topic(
        id=uuid.uuid4(),
        name="Python",
        slug="python",
        status="active",
        kind="language",
        keywords=["Python"],
    )
    tag = Tag(id=uuid.uuid4(), name="PostgreSQL", slug="postgresql", aliases=[])
    session = Mock()
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [topic]),
        SimpleNamespace(all=lambda: [tag]),
    ]
    # Retained editorial classifications belong to an existing publisher row.
    # This supplies the insert-conflict result and subsequent identity/provenance
    # reads; the conditional placeholder-update predicate is not evaluated here.
    existing = classification in {"manual", "ai"}
    session.scalar.side_effect = (
        [None, None, article_id, classification]
        if existing
        else [None, article_id, entry.image_url]
        if classification == "enabled-ai"
        else [None, article_id, classification]
    )
    settings = configuration(ai_enabled=classification == "enabled-ai", image_storage_enabled=True)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(
        tasks, "resolve_source_tags", lambda _session, names: ({"python": supplied_id}, 0)
    )
    attach, enrich, image = Mock(), Mock(), Mock()
    monkeypatch.setattr(tasks, "attach_source_tags", attach)
    monkeypatch.setattr(tasks, "request_article_enrichment", enrich)
    monkeypatch.setattr(tasks, "request_image", image)
    activity = []
    assert tasks.store_entries(
        session, source_id, ParsedFeed([entry], 1, 0), activity=activity
    ) == (0 if existing else 1)
    attach.assert_called_once_with(session, article_id, [supplied_id])
    assert activity == [entry.published_at]
    image.assert_called_once_with(session, article_id, automatic=True)
    if classification == "enabled-ai":
        enrich.assert_called_once_with(session, article_id, automatic=True)
    else:
        enrich.assert_not_called()
    writes = [call.args[0] for call in session.execute.call_args_list]
    topic_writes = [
        statement.compile(dialect=postgresql.dialect()).params
        for statement in writes
        if statement.table.name == ArticleTopic.__tablename__
    ]
    tag_writes = [
        statement.compile(dialect=postgresql.dialect()).params
        for statement in writes
        if statement.table.name == ArticleTag.__tablename__
    ]
    if classification is None:
        assert session.scalars.call_count == 2
        assert len(topic_writes) == len(tag_writes) == 1
        assert {
            key: topic_writes[0][key]
            for key in ("article_id", "topic_id", "role", "origin", "relevance")
        } == {
            "article_id": article_id,
            "topic_id": topic.id,
            "role": "supporting",
            "origin": "heuristic",
            "relevance": 0.5,
        }
        assert tag_writes[0]["article_id"] == article_id and tag_writes[0]["tag_id"] == tag.id
    else:
        assert topic_writes == tag_writes == []
        session.scalars.assert_not_called()


def test_retained_origin_can_attach_new_explicit_labels_without_reimporting_or_counting_arrivals(
    monkeypatch, rss_bytes
):
    from devfeed_core.feeds.parser import ParsedFeed, parse_feed

    entry = parse_feed(
        rss_bytes, "https://example.com/rss", NOW, source_type=SourceType.PUBLISHER
    ).entries[0]
    session = Mock()
    session.scalars.return_value.all.return_value = []
    article_id, supplied_id = uuid.uuid4(), uuid.uuid4()
    session.scalar.return_value = article_id
    monkeypatch.setattr(
        tasks, "resolve_source_tags", lambda _session, names: ({"python": supplied_id}, 0)
    )
    attach, enrich, image = Mock(), Mock(), Mock()
    monkeypatch.setattr(tasks, "attach_source_tags", attach)
    monkeypatch.setattr(tasks, "request_article_enrichment", enrich)
    monkeypatch.setattr(tasks, "request_image", image)
    activity = []
    assert (
        tasks.store_entries(session, uuid.uuid4(), ParsedFeed([entry], 1, 0), activity=activity)
        == 0
    )
    assert activity == []
    attach.assert_called_once_with(session, article_id, [supplied_id])
    session.execute.assert_not_called()
    session.scalars.assert_not_called()
    enrich.assert_not_called()
    image.assert_not_called()


@pytest.mark.parametrize("empty_catalog", [False, True])
def test_heuristic_feed_batch_reuses_one_lazily_loaded_catalog(
    monkeypatch, rss_bytes, empty_catalog
):
    from dataclasses import replace

    from devfeed_core.feeds.parser import ParsedFeed, parse_feed
    from devfeed_core.models import ArticleTag, ArticleTopic, Tag, Topic
    from sqlalchemy.dialects import postgresql

    original = parse_feed(
        rss_bytes, "https://example.com/rss", NOW, source_type=SourceType.PUBLISHER
    ).entries[0]
    entries = [
        replace(original, entry_key=str(index), canonical_url=f"https://example.com/{index}")
        for index in range(2)
    ]
    identifiers = [uuid.uuid4(), uuid.uuid4()]
    topic = Topic(
        id=uuid.uuid4(), name="Python", slug="python", status="active", keywords=["Python"]
    )
    tag = Tag(id=uuid.uuid4(), name="PostgreSQL", slug="postgresql", aliases=[])
    session = Mock()
    session.scalar.side_effect = [
        value for identifier in identifiers for value in (None, identifier, None)
    ]
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [] if empty_catalog else [topic]),
        SimpleNamespace(all=lambda: [] if empty_catalog else [tag]),
    ]
    monkeypatch.setattr(tasks, "get_settings", lambda: configuration(ai_enabled=False))
    monkeypatch.setattr(tasks, "resolve_source_tags", lambda *_: ({"python": uuid.uuid4()}, 0))
    monkeypatch.setattr(tasks, "attach_source_tags", Mock())
    monkeypatch.setattr(tasks, "request_image", Mock())
    assert tasks.store_entries(session, uuid.uuid4(), ParsedFeed(entries, 2, 0)) == 2
    assert session.scalars.call_count == 2
    for model, field, expected in (
        (ArticleTopic, "topic_id", topic.id),
        (ArticleTag, "tag_id", tag.id),
    ):
        writes = [
            call.args[0].compile(dialect=postgresql.dialect()).params
            for call in session.execute.call_args_list
            if call.args[0].table.name == model.__tablename__
        ]
        assert [(row["article_id"], row[field]) for row in writes] == (
            [] if empty_catalog else [(identifier, expected) for identifier in identifiers]
        )


@pytest.mark.parametrize("approval", ["pending", "rejected"])
def test_request_requires_editorial_approval_before_any_job_read_or_write(approval):
    current, session = source(approval=approval), Mock()
    with pytest.raises(ValueError, match="Only approved"):
        jobs.request_ingestion(session, current, automatic=True)
    assert session.mock_calls == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_ingestion_is_coalesced_without_changing_its_due_time_or_lease(status):
    current = source(approval="approved")
    row = IngestionJob(
        id=uuid.uuid4(),
        source_id=current.id,
        status="queued",
        attempts=0,
        created_at=NOW - timedelta(hours=1),
        available_at=NOW - timedelta(minutes=10),
    )
    if status == "running":
        start_job(row, NOW - timedelta(minutes=1), 300)
        current.last_attempt_at = NOW - timedelta(minutes=1)
    before = copy.deepcopy(
        (row.status, row.available_at, row.attempts, row.lease_token, row.lease_until)
    )
    session = Mock()
    session.scalar.return_value = row
    assert jobs.request_ingestion(session, current, automatic=True) is row
    assert (row.status, row.available_at, row.attempts, row.lease_token, row.lease_until) == before
    session.add.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize("adaptive_failure", [False, True])
def test_new_ingestion_preserves_adaptive_failure_cooldown_without_delaying_a_healthy_request(
    monkeypatch, adaptive_failure
):
    settings = configuration(adaptive_source_polling_enabled=True)
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)
    monkeypatch.setattr(polling, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    current = source(
        approval="approved",
        poll_interval_seconds=1800,
        polling_mode="adaptive" if adaptive_failure else "fixed",
        polling_state={"interval": 3600},
        consecutive_failures=2 if adaptive_failure else 0,
        next_fetch_at=NOW + timedelta(minutes=20),
    )
    session = Mock()
    session.scalar.return_value = None
    row = jobs.request_ingestion(session, current, automatic=True)
    assert isinstance(row, IngestionJob) and row.source_id == current.id and row.automatic is True
    assert row.available_at == (NOW + timedelta(minutes=20) if adaptive_failure else NOW)
    assert current.next_fetch_at >= row.available_at
    session.add.assert_called_once_with(row)
    session.flush.assert_called_once_with()


def test_late_failure_after_a_real_rejection_does_not_increment_upstream_failure_counters(
    monkeypatch,
):
    current = source(
        approval="approved",
        poll_interval_seconds=1800,
        consecutive_failures=2,
        next_fetch_at=NOW + timedelta(minutes=20),
        last_error="Prior upstream failure",
    )
    row = IngestionJob(
        id=uuid.uuid4(),
        source_id=current.id,
        status="queued",
        attempts=0,
        created_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
    )
    start_job(row, NOW - timedelta(seconds=30), 300)
    session = Mock()
    session.scalar.return_value = current
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)
    monkeypatch.setattr(services, "utcnow", lambda: NOW)
    services.review_source(
        session,
        current.id,
        SourceDecision(
            decision="rejected",
            actor="editor",
            note="The feed no longer serves the software audience.",
        ),
    )
    before = (
        current.consecutive_failures,
        current.next_fetch_at,
        current.last_error,
        current.last_success_at,
    )
    jobs.fail_job(session, row, "Upstream timeout", retryable=True)
    assert row.status == "failed" and row.error == "Source is not approved for ingestion"
    assert row.lease_token is row.lease_until is None and row.finished_at == NOW
    assert row.attempts == 1
    assert (
        current.consecutive_failures,
        current.next_fetch_at,
        current.last_error,
        current.last_success_at,
    ) == before


def test_delivery_for_a_deleted_source_and_cascaded_job_has_no_claim_or_attempt():
    session = Mock()
    session.scalar.return_value = None
    assert jobs.claim_job(session, uuid.uuid4()) is None
    assert session.scalar.call_count == 1
    session.add.assert_not_called()
    session.flush.assert_not_called()
