"""Direct observations keep chart state in ClickHouse and evaluate current deadlines."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics
from devfeed_core.config import Settings


class Sink:
    def __init__(self):
        self.rows = []

    async def insert(self, table, rows):
        assert table in {"analytics_pipeline_events", "analytics_user_events"}
        self.rows.extend(rows)


def test_writes_only_safe_fields_and_retains_every_job_attempt():
    async def run():
        sink = Sink()
        store = DirectOverviewAnalytics(sink)
        job = uuid.uuid4()
        at = datetime(2026, 1, 1, tzinfo=UTC)
        await store.jobs(
            [
                {
                    "id": job,
                    "kind": "analysis",
                    "status": "failed",
                    "revision": 1,
                    "attempt": 1,
                    "finished_at": at,
                    "prompt": "private",
                },
                {
                    "id": job,
                    "kind": "analysis",
                    "status": "succeeded",
                    "revision": 2,
                    "attempt": 2,
                    "finished_at": at,
                    "outcome": "applied",
                    "prompt": "private",
                },
            ]
        )
        current = [row for row in sink.rows if row["domain"] == "queue"]
        attempts = [row for row in sink.rows if row["domain"] == "job"]
        assert len(attempts) == 2 and attempts[0]["id"] != attempts[1]["id"]
        assert len(current) == 2 and current[0]["id"] == current[1]["id"]
        assert "private" not in str(sink.rows)

    asyncio.run(run())


def test_invalid_values_rejected_before_insert():
    async def run():
        sink = Sink()
        store = DirectOverviewAnalytics(sink)
        with pytest.raises(ValueError):
            await store.jobs(
                [{"id": uuid.uuid4(), "kind": "unexpected", "status": "queued", "revision": 1}]
            )
        with pytest.raises(ValueError):
            await store.feeds([{"user_id": uuid.uuid4(), "revision": -1}])
        with pytest.raises(ValueError):
            await store.publications(
                [{"article_id": uuid.uuid4(), "revision": 1, "published_at": datetime(2026, 1, 1)}]
            )
        assert sink.rows == []

    asyncio.run(run())


@pytest.mark.integration
def test_direct_charts_handle_reparent_retry_and_feed_expiration():
    url = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for ClickHouse integration")
    database = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not database.endswith("_test"):
        pytest.fail("Disposable ClickHouse database name must end with _test")
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://fixture@database.invalid/fixture_test",
        redis_url="redis://redis.invalid/15",
        clickhouse_url=url,
        clickhouse_database=database,
        clickhouse_username=os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
    )

    async def run():
        target = f"overview_direct_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    store = DirectOverviewAnalytics(client)
                    await client.initialize()
                    start = datetime(2026, 1, 1, tzinfo=UTC)
                    end = start + timedelta(days=2)
                    a, b, source, topic, job, user = [uuid.uuid4() for _ in range(6)]
                    base = {
                        "published_at": start + timedelta(hours=1),
                        "discovered_at": start,
                        "source_ids": [source],
                        "topic_ids": [topic],
                        "automated": True,
                    }
                    await store.publications(
                        [
                            {**base, "article_id": a, "revision": 1},
                            {
                                **base,
                                "article_id": b,
                                "revision": 1,
                                "source_ids": [],
                                "topic_ids": [],
                            },
                        ]
                    )
                    # An association moves from A to B. Both complete owner snapshots change.
                    changes = [
                        {**base, "article_id": a, "revision": 2, "source_ids": [], "topic_ids": []},
                        {**base, "article_id": b, "revision": 2},
                    ]
                    await store.publications(changes)
                    await store.publications(changes)
                    assert (await store.publication_dimensions(start, end, dimension="source")) == [
                        {"id": str(source), "publications": 1}
                    ]
                    assert (await store.publication_dimensions(start, end, dimension="topic")) == [
                        {"id": str(topic), "publications": 1}
                    ]
                    pubs = await store.publication_report(start, end)
                    assert pubs[0]["published"] == 2 and pubs[0]["automated"] == 2
                    overview = await store.publication_overview(start, end)
                    assert len(overview) == 2
                    assert {int(row["total"]) for row in overview} == {0, 1}
                    assert all(row["published"] == 2 for row in overview)
                    await store.jobs(
                        [
                            {
                                "id": job,
                                "kind": "analysis",
                                "status": "failed",
                                "revision": 1,
                                "attempt": 1,
                                "created_at": start,
                                "finished_at": start,
                                "duration_ms": 2000,
                            },
                            {
                                "id": job,
                                "kind": "analysis",
                                "status": "succeeded",
                                "revision": 2,
                                "attempt": 2,
                                "created_at": start,
                                "finished_at": start,
                                "duration_ms": 4000,
                                "outcome": "applied",
                            },
                        ]
                    )
                    jobs = await store.job_outcomes(start, end)
                    assert jobs[0]["completed"] == jobs[0]["failed"] == 1
                    approved, rejected = uuid.uuid4(), uuid.uuid4()
                    await store.decisions(
                        [
                            {
                                "id": approved,
                                "revision": 1,
                                "status": "approved",
                                "reviewed_at": start,
                            },
                            {
                                "id": rejected,
                                "revision": 1,
                                "status": "rejected",
                                "reviewed_at": start,
                            },
                        ]
                    )
                    throughput = await store.throughput(start, end)
                    assert throughput[0]["article_analyses"] == 1
                    assert throughput[0]["topic_decisions"] == 2
                    await store.publish(
                        [
                            {
                                "source_table": "topic_proposals",
                                "identity": {"id": approved},
                                "record": None,
                                "deleted": True,
                            }
                        ],
                        revision=2,
                    )
                    await store.decisions(
                        [
                            {"id": rejected, "revision": 2, "status": "pending"},
                            {
                                "id": rejected,
                                "revision": 3,
                                "status": "approved",
                                "reviewed_at": None,
                            },
                        ]
                    )
                    retained = await store.throughput(start, end)
                    assert sum(row["topic_decisions"] for row in retained) == 2
                    await store.decisions(
                        [
                            {
                                "id": rejected,
                                "revision": 4,
                                "status": "approved",
                                "reviewed_at": start + timedelta(hours=1),
                            }
                        ]
                    )
                    retained = await store.throughput(start, end)
                    assert sum(row["topic_decisions"] for row in retained) == 2
                    assert (
                        await client.query(
                            "SELECT outcome,deleted FROM analytics_pipeline_events FINAL "
                            "WHERE domain='job' AND task='topic-decision'"
                        )
                        == [{"outcome": "approved", "deleted": 0}] * 2
                    )
                    assert await store.queues() == []
                    await store.feeds(
                        [
                            {
                                "user_id": user,
                                "revision": 1,
                                "created_at": start,
                                "computed_at": start,
                                "interest_count": 1,
                                "has_generation": True,
                                "expires_at": start + timedelta(hours=4),
                                "next_refresh_at": start + timedelta(hours=1),
                            }
                        ]
                    )
                    now = start + timedelta(hours=2)
                    health = (await store.feed_health(now))[0]
                    assert (
                        health["ready"] == 1 and health["overdue"] == 1 and health["expired"] == 0
                    )
                    health = (await store.feed_health(start + timedelta(hours=5)))[0]
                    assert health["ready"] == 0 and health["expired"] == 1
                    from devfeed_core.analytics_article_opens import ArticleOpenAnalytics

                    opens = ArticleOpenAnalytics(client)
                    await opens.record(a, "a" * 64, start + timedelta(hours=2))
                    await opens.record(a, "a" * 64, start + timedelta(hours=2))
                    await opens.record(b, "a" * 64, start + timedelta(hours=2))
                    await store.accounts([{"id": user, "created_at": start, "revision": 1}])
                    audience = await store.audience(start, end)
                    assert audience[0]["opens"] == 2
                    assert audience[0]["estimated_readers"] == audience[0]["new_accounts"] == 1
                    await store.sources(
                        [
                            {
                                "id": source,
                                "name": "Publisher",
                                "consecutive_failures": 3,
                                "revision": 1,
                            }
                        ]
                    )
                    await store.topics([{"id": topic, "name": "Python", "revision": 1}])
                    await store.memberships(
                        [
                            {
                                "user_id": user,
                                "topic_id": topic,
                                "kind": "follow",
                                "occurred_at": start,
                                "revision": 1,
                            },
                            {"user_id": user, "topic_id": topic, "kind": "inferred", "revision": 1},
                        ]
                    )
                    sources = await store.source_coverage(start, end)
                    assert (
                        sources[0]["name"] == "Publisher"
                        and sources[0]["consecutive_failures"] == 3
                    )
                    assert sources[0]["publications"] == sources[0]["total"] == 1
                    topics = await store.topic_coverage(start, end)
                    assert topics[0]["name"] == "Python"
                    assert (
                        topics[0]["followers"]
                        == topics[0]["inferred_users"]
                        == topics[0]["publications"]
                        == 1
                    )
                    await store.memberships(
                        [
                            {
                                "user_id": user,
                                "topic_id": topic,
                                "kind": "follow",
                                "revision": 2,
                                "deleted": True,
                            }
                        ]
                    )
                    assert (await store.topic_coverage(start, end))[0]["followers"] == 0
                    from devfeed_admin_api.analytics_reports import Reports
                    from devfeed_admin_api.overview_windows import ReportWindow
                    from devfeed_core.analytics_audience_refresh import (
                        COMPLETE_PHASE,
                        PROGRESS_TABLE,
                        REFRESH_POLICY,
                        REFRESH_SCOPE,
                    )

                    # Every known audience row above has acknowledged its actual
                    # source timestamps before this finite-projection readiness.
                    await client.insert(
                        PROGRESS_TABLE,
                        [
                            {
                                "policy": REFRESH_POLICY,
                                "scope": REFRESH_SCOPE,
                                "version": 1,
                                "source_at": start,
                                "completed_at": start,
                                "pending": 0,
                                "cursor": json.dumps(
                                    {"phase": COMPLETE_PHASE, "start_revision": 1}
                                ),
                            }
                        ],
                    )
                    report = Reports(
                        client,
                        ReportWindow(start.date(), end.date()),
                        start + timedelta(days=1),
                        capture_started_at=start,
                    )
                    publication, audience, processing, coverage, queues = await asyncio.gather(
                        report.publishing(),
                        report.audience(),
                        report.processing(),
                        report.coverage(),
                        report.queues(),
                    )
                    assert publication.summary.published == 2
                    assert sum(point.opens or 0 for point in audience.activity) == 2
                    assert sum(point.new_accounts for point in audience.activity) == 1
                    assert audience.personalization.expired == 1
                    assert sum(point.topic_decisions for point in processing.throughput) == 2
                    assert processing.outcomes[0].failed == processing.outcomes[0].completed == 1
                    assert coverage.source_publications_total == 1
                    assert coverage.sources[0].name == "Publisher"
                    assert coverage.topics[0].inferred_users == 1
                    assert queues.queues == []
                    await _verify_current_cards(client, start, a, source)
                    await _verify_job_corrections(client, store, start, end)
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())


def test_completed_decisions_survive_deleted_reopened_and_incomplete_sources():
    async def run():
        sink = Sink()
        store = DirectOverviewAnalytics(sink)
        at = datetime(2026, 1, 1, tzinfo=UTC)
        identity = uuid.uuid4()
        await store.decisions(
            [
                {"id": identity, "revision": 1, "status": "approved", "reviewed_at": at},
                {"id": uuid.uuid4(), "revision": 1, "status": "rejected", "reviewed_at": at},
            ]
        )
        assert [row["outcome"] for row in sink.rows] == ["approved", "rejected"]
        assert all(
            row["task"] == "topic-decision" and row["status"] == "succeeded" for row in sink.rows
        )
        original = list(sink.rows)
        await store.decisions(
            [
                {"id": identity, "revision": 2, "status": "pending", "reviewed_at": at},
                {"id": identity, "revision": 3, "status": "approved", "reviewed_at": None},
                {"id": identity, "revision": 4, "deleted": True},
            ]
        )
        assert len(sink.rows) == 2
        assert sink.rows == original
        await store.decisions(
            [{"id": identity, "revision": 5, "status": "rejected", "reviewed_at": at}]
        )
        assert sink.rows[-1]["id"] == sink.rows[0]["id"]
        assert sink.rows[-1]["outcome"] == "rejected"
        assert all(row["deleted"] == 0 for row in sink.rows)

    asyncio.run(run())


def test_source_snapshots_publish_only_domain_fields_and_filter_inferred_memberships():
    async def run():
        sink = Sink()
        store = DirectOverviewAnalytics(sink)
        user, topic, source = [uuid.uuid4() for _ in range(3)]
        at = datetime(2026, 1, 1, tzinfo=UTC)
        await store.publish(
            [
                {
                    "source_table": "user_accounts",
                    "record": {"id": user, "created_at": at, "email": "private"},
                },
                {
                    "source_table": "sources",
                    "record": {
                        "id": source,
                        "name": "Publisher",
                        "consecutive_failures": 2,
                        "feed_url": "private",
                    },
                },
                {
                    "source_table": "topics",
                    "record": {"id": topic, "name": "Python", "evidence": "private"},
                },
                {"source_table": "user_topics", "record": {"user_id": user, "topic_id": topic}},
                {
                    "source_table": "user_interests",
                    "record": {"user_id": user, "topic_id": topic, "reason": "something_else"},
                },
            ],
            revision=17,
        )
        assert "private" not in str(sink.rows)
        assert {row["domain"] for row in sink.rows} == {"account", "source", "topic", "membership"}
        assert all(row["revision"] == 17 for row in sink.rows)
        members = [row for row in sink.rows if row["domain"] == "membership"]
        assert {(row["task"], row["deleted"]) for row in members} == {
            ("follow", 0),
            ("inferred", 1),
        }
        await store.publish(
            [{"source_table": "sources", "operation": "D", "record": {"id": source}}], revision=18
        )
        assert sink.rows[-1]["deleted"] == 1

    asyncio.run(run())


async def _verify_current_cards(client, now, article_id, source_id):
    from types import SimpleNamespace

    from devfeed_admin_api.analytics_current import CurrentCards
    from devfeed_core.analytics_direct_cards import normalize_card
    from devfeed_core.analytics_observation import pipeline_policy_version

    await client.initialize()
    policy = pipeline_policy_version(client.settings)
    article_payload = {
        "id": str(article_id),
        "title": "Article",
        "source_name": "Publisher",
        "source_id": str(source_id),
        "discovered_at": now.isoformat(),
        "stage": "analysis",
        "state": "blocked",
        "blockers": [],
        "next_check_at": None,
        "editorial_revision": 1,
        "jobs": {},
    }
    source_payload = {
        "key": f"source:{source_id}",
        "kind": "source",
        "id": str(source_id),
        "source_id": str(source_id),
        "candidate_id": None,
        "name": "Publisher",
        "website_url": "https://example.invalid",
        "feed_url": None,
        "source_type": "publisher",
        "approval_status": "approved",
        "enabled": True,
        "stage": "active",
        "state": "complete",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "snapshot": "1",
        "can_advance": False,
        "can_approve": False,
        "can_ingest": True,
        "next_action": None,
        "advance_label": None,
        "retry_targets": [],
    }
    article = {
        "stage": "analysis",
        "state": "blocked",
        "title": "Article",
        "source_id": source_id,
        "source_ids": [source_id],
        "source_name": "Publisher",
        "source_sort": "publisher",
        "created_at": now,
        "editorial_revision": 1,
        "blocker_codes": ["analysis_failed"],
        "review_pending": True,
        "payload": article_payload,
    }
    await client.insert(
        "analytics_card_canonical",
        [
            normalize_card(article, policy, "article", article_id, 1, now),
            normalize_card(
                {
                    "stage": "active",
                    "state": "complete",
                    "title": "Publisher",
                    "source_type": "publisher",
                    "enabled": True,
                    "approval_status": "approved",
                    "consecutive_failures": 3,
                    "activity_at": now,
                    "payload": source_payload,
                },
                policy,
                "source",
                source_id,
                1,
                now,
            ),
        ],
    )

    class ReadyClient:
        settings = client.settings

        async def query(self, sql, parameters=None):
            return await client.query(sql, parameters)

        async def status(self):
            return {"version": 1, "ready": 1, "policy": policy, "updated_at": now}

    cards = CurrentCards(ReadyClient(), now=now)
    options = dict(q=None, source_id=None, needs_attention=False, limit=1)
    board = await cards.article_board(**options)
    assert board.counts["analysis"] == 1
    assert board.columns[2].items[0].id == article_id
    assert board.columns[2].items[0].editorial_revision == 1
    beyond = await cards.article_board(**options, stage="analysis", cursor=10)
    assert beyond.columns[0].total == 1 and beyond.columns[0].items == []
    grouped = await cards.article_board(**options, group_by="source")
    assert grouped.columns[2].source_groups[0].state_counts == {"blocked": 1}
    sources = await cards.source_board(
        SimpleNamespace(
            terminal_since=now - timedelta(days=7),
            limit=20,
            q=None,
            source_type=None,
            needs_attention=False,
        )
    )
    assert sources.counts["active"] == 1
    assert sources.columns[6].items[0].name == "Publisher"
    from devfeed_admin_api.overview_windows import recent_window

    operations = await cards.operations(recent_window(30, now))
    assert operations.data.source_failures == 1
    assert operations.data.reviews[0].count == 1
    blocker = next(item for item in operations.data.blockers if item.code == "analysis_failed")
    assert blocker.count == 1 and blocker.targets[0].id == article_id

    # Mutable stage filters apply after replacement, so the former blocked row
    # cannot reappear; deadline passage needs no PG timer or refresh table.
    await client.insert(
        "analytics_card_canonical",
        [
            normalize_card(
                {
                    **article,
                    "state": "retry_wait",
                    "next_transition_at": now - timedelta(seconds=1),
                    "blocker_codes": [],
                    "review_pending": False,
                },
                policy,
                "article",
                article_id,
                2,
                now,
            )
        ],
    )
    assert (await cards.article_board(**options)).columns[2].items[0].state == "waiting"
    assert (await cards.article_board(**{**options, "needs_attention": True})).counts[
        "analysis"
    ] == 0
    await client.insert(
        "analytics_card_canonical",
        [
            normalize_card(None, policy, "article", article_id, 3, now),
        ],
    )
    assert (await cards.article_board(**options)).counts["analysis"] == 0


async def _verify_job_corrections(client, store, start, end):
    job = str(uuid.uuid4())
    await store.jobs(
        [
            {
                "id": job,
                "kind": "ingestion",
                "status": "succeeded",
                "revision": 1,
                "attempt": 1,
                "finished_at": start,
            },
            {
                "id": job,
                "kind": "ingestion",
                "status": "failed",
                "revision": 1,
                "attempt": 2,
                "finished_at": start,
            },
        ]
    )
    # A newer completed snapshot corrects the same attempt without duplication.
    await store.jobs(
        [
            {
                "id": job,
                "kind": "ingestion",
                "status": "succeeded",
                "revision": 2,
                "attempt": 2,
                "finished_at": start,
                "duration_ms": 124,
            }
        ]
    )
    outcomes = await store.job_outcomes(start, end)
    ingestion = next(row for row in outcomes if row["kind"] == "ingestion")
    assert ingestion["completed"] == 2 and ingestion["failed"] == 0
    missing_finish = uuid.uuid4()
    await store.jobs(
        [
            {
                "id": missing_finish,
                "kind": "source_enrichment",
                "status": "failed",
                "revision": 1,
                "finished_at": None,
            }
        ]
    )
    assert all(row["kind"] != "source_enrichment" for row in await store.job_outcomes(start, end))
    current = await client.query(
        "SELECT status FROM analytics_pipeline_events FINAL "
        "WHERE domain='queue' AND task='source_enrichment'"
    )
    assert current == [{"status": "failed"}]
    # Completing and requeueing in one business transaction carries its saved
    # attempt separately; replay must never restore that old state to the queue.
    changes = [
        {
            "source_table": "ingestion_jobs",
            "record": {
                "id": job,
                "status": "queued",
                "attempts": 3,
            },
        },
        {
            "source_table": "ingestion_jobs",
            "attempt_only": True,
            "record": {
                "id": job,
                "status": "succeeded",
                "attempts": 2,
                "finished_at": start,
            },
        },
    ]
    await store.publish(changes, revision=3)
    await store.publish(changes, revision=3)
    queues = await store.queues()
    assert queues == [{"kind": "ingestion", "queued": 1, "running": 0, "oldest_queued_at": None}]


def test_saved_attempt_does_not_replace_current_requeued_job_on_replay():
    async def run():
        sink = Sink()
        store = DirectOverviewAnalytics(sink)
        job = uuid.uuid4()
        changes = [
            {
                "source_table": "article_analysis_jobs",
                "record": {
                    "id": job,
                    "status": "queued",
                    "attempts": 2,
                },
            },
            {
                "source_table": "article_analysis_jobs",
                "attempt_only": True,
                "record": {
                    "id": job,
                    "status": "succeeded",
                    "attempts": 1,
                    "finished_at": datetime(2026, 1, 1, tzinfo=UTC),
                    "outcome": "applied",
                },
            },
        ]
        await store.publish(changes, revision=7)
        await store.publish(changes, revision=7)
        queue = [row for row in sink.rows if row["domain"] == "queue"]
        attempts = [row for row in sink.rows if row["domain"] == "job"]
        assert len(queue) == 2 and all(row["status"] == "queued" for row in queue)
        assert len(attempts) == 2 and attempts[0] == attempts[1]

    asyncio.run(run())


def test_completed_job_without_completion_time_keeps_only_current_snapshot():
    async def run():
        sink = Sink()
        await DirectOverviewAnalytics(sink).jobs(
            [
                {
                    "id": uuid.uuid4(),
                    "kind": "analysis",
                    "status": status,
                    "revision": 1,
                    "finished_at": None,
                }
                for status in ("succeeded", "failed")
            ]
        )
        assert len(sink.rows) == 2
        assert all(row["domain"] == "queue" for row in sink.rows)
        assert {row["status"] for row in sink.rows} == {"succeeded", "failed"}

    asyncio.run(run())


@pytest.mark.parametrize(
    "method", ["publications", "jobs", "decisions", "feeds", "accounts", "sources", "memberships"]
)
@pytest.mark.parametrize("value", [None, "invalid", [None] * 1001])
def test_malformed_or_oversized_observations_never_reach_clickhouse(method, value):
    async def run():
        sink = Sink()
        with pytest.raises(ValueError):
            await getattr(DirectOverviewAnalytics(sink), method)(value)
        assert sink.rows == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"article_id": "bad", "revision": 1, "published_at": "2026-01-01T00:00:00Z"},
        {
            "article_id": str(uuid.UUID(int=1)),
            "revision": True,
            "published_at": "2026-01-01T00:00:00Z",
        },
        {"article_id": str(uuid.UUID(int=1)), "revision": 1, "published_at": "not-a-date"},
        {"article_id": str(uuid.UUID(int=1)), "revision": 1, "published_at": "2026-01-01"},
        {
            "article_id": str(uuid.UUID(int=1)),
            "revision": 1,
            "published_at": "2026-01-01T00:00:00Z",
            "automated": 1,
        },
        {
            "article_id": str(uuid.UUID(int=1)),
            "revision": 1,
            "published_at": "2026-01-01T00:00:00Z",
            "source_ids": "bad",
        },
        {
            "article_id": str(uuid.UUID(int=1)),
            "revision": 1,
            "published_at": "2026-01-01T00:00:00Z",
            "topic_ids": [None] * 1001,
        },
    ],
)
def test_publication_validation_is_atomic_with_an_earlier_valid_observation(changes):
    async def run():
        sink = Sink()
        valid = {"article_id": uuid.uuid4(), "revision": 1, "published_at": "2026-01-01T00:00:00Z"}
        with pytest.raises(ValueError):
            await DirectOverviewAnalytics(sink).publications([valid, changes])
        assert sink.rows == []

    asyncio.run(run())


def test_source_snapshots_route_hydrated_rows_and_tombstones_without_requiring_old_fields():
    async def run():
        sink = Sink()
        store = DirectOverviewAnalytics(sink)
        article, user, topic, proposal = [uuid.uuid4() for _ in range(4)]
        await store.publish(
            [
                {
                    "source_table": "articles",
                    "identity": {"id": article},
                    "publication": {"article_id": article, "deleted": True},
                },
                {
                    "source_table": "user_recommendation_states",
                    "identity": {"user_id": user},
                    "feed": {"user_id": user, "deleted": True},
                },
                {"source_table": "user_accounts", "identity": {"id": user}},
                {"source_table": "topics", "operation": "D", "before_record": {"id": topic}},
                {
                    "source_table": "topic_proposals",
                    "record": {"id": proposal, "status": "pending"},
                },
                {
                    "source_table": "user_interests",
                    "record": {"user_id": user, "topic_id": topic, "reason": "related_topic"},
                },
            ],
            revision=9,
        )
        assert len(sink.rows) == 5
        assert {row["domain"] for row in sink.rows} == {
            "publication",
            "feed",
            "account",
            "topic",
            "membership",
        }
        assert all(row["deleted"] == (row["domain"] != "membership") for row in sink.rows)
        await store.accounts(
            [{"id": user, "revision": 10, "created_at": "2026-01-01T00:00:00+00:00"}]
        )
        await store.topics([{"id": topic, "revision": 10}])
        assert sink.rows[-2]["occurred_at"] == datetime(2026, 1, 1, tzinfo=UTC)
        assert sink.rows[-1]["name"] == "Unnamed"

    asyncio.run(run())


def test_completed_jobs_are_batched_and_unnumbered_completions_keep_distinct_identities():
    async def run():
        from unittest.mock import AsyncMock

        sink = Sink()
        sink.insert = AsyncMock(side_effect=sink.insert)
        store = DirectOverviewAnalytics(sink)
        changes = [
            {
                "source_table": "ingestion_jobs",
                "record": {
                    "id": uuid.uuid4(),
                    "status": "succeeded",
                    "attempts": 1,
                    "finished_at": "2026-01-01T00:00:00Z",
                },
            }
            for _ in range(501)
        ]
        await store.publish(changes, revision=2)
        assert [len(call.args[1]) for call in sink.insert.call_args_list] == [1000, 2]
        job = uuid.uuid4()
        first = {
            "id": job,
            "kind": "analysis",
            "status": "failed",
            "revision": 3,
            "attempt": 0,
            "finished_at": "2026-01-01T00:00:00Z",
        }
        await store.jobs(
            [first, {**first, "finished_at": "2026-01-01T01:00:00Z"}, {**first, "deleted": True}]
        )
        facts = [row for row in sink.rows[1002:] if row["domain"] == "job"]
        assert len(facts) == 2 and facts[0]["id"] != facts[1]["id"]
        assert sink.rows[-1]["domain"] == "queue" and sink.rows[-1]["deleted"] == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "method,record",
    [
        ("jobs", {"id": str(uuid.UUID(int=1)), "kind": "analysis", "status": "", "revision": 1}),
        (
            "jobs",
            {"id": str(uuid.UUID(int=1)), "kind": "x" * 101, "status": "queued", "revision": 1},
        ),
        (
            "jobs",
            {
                "id": str(uuid.UUID(int=1)),
                "kind": "analysis",
                "status": "queued",
                "revision": 1,
                "attempt": 2**32,
            },
        ),
        (
            "memberships",
            {
                "user_id": str(uuid.UUID(int=1)),
                "topic_id": str(uuid.UUID(int=2)),
                "kind": "other",
                "revision": 1,
            },
        ),
        ("sources", {"id": str(uuid.UUID(int=1)), "revision": 1, "consecutive_failures": -1}),
        ("feeds", {"user_id": str(uuid.UUID(int=1)), "revision": 1, "interest_count": 2**32}),
    ],
)
def test_invalid_states_and_counter_overflow_are_rejected_before_insert(method, record):
    async def run():
        sink = Sink()
        with pytest.raises(ValueError):
            await getattr(DirectOverviewAnalytics(sink), method)([record])
        assert sink.rows == []

    asyncio.run(run())


def test_direct_query_boundaries_reject_arbitrary_granularity_and_dimension():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    async def run():
        client = SimpleNamespace(
            query=AsyncMock(return_value=[{"published": 2}]), execute=AsyncMock()
        )
        store = DirectOverviewAnalytics(client)
        start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        client.execute.assert_not_awaited()
        for method in ("audience", "publication_report", "publication_overview", "throughput"):
            with pytest.raises(ValueError):
                await getattr(store, method)(start, end, granularity="day);DROP TABLE x")
        with pytest.raises(ValueError):
            await store.publication_dimensions(start, end, dimension="other")
        client.query.assert_not_called()
        assert await store.publication_report(start, end, granularity="hour") == [{"published": 2}]
        sql, params = client.query.call_args.args
        assert params == {"start": start, "end": end} and "occurred_at<{end:" in sql
        for dimension in ("source", "topic"):
            await store.publication_dimensions(start, end, dimension=dimension)
            assert f"ARRAY JOIN {dimension}_ids" in client.query.call_args.args[0]
        await store.audience(start, end, daily=True)
        assert "AS day" in client.query.call_args.args[0]

    asyncio.run(run())


def test_raw_observation_batches_enforce_the_memory_bound_before_inserting():
    async def run():
        sink = Sink()
        with pytest.raises(ValueError, match="batch is too large"):
            await DirectOverviewAnalytics(sink)._write([{}] * 1001)
        assert sink.rows == []

    asyncio.run(run())


@pytest.mark.parametrize("method", ["queues", "feed_health"])
def test_current_cohort_bounds_are_both_required_and_validated_before_query(method):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    async def run():
        client = SimpleNamespace(query=AsyncMock(return_value=[]))
        reader = DirectOverviewAnalytics(client)
        start = datetime(2026, 10, 29, tzinfo=UTC)
        end = start + timedelta(days=1)
        args = (start,) if method == "feed_health" else ()
        call = getattr(reader, method)
        for lower, upper in ((start, None), (None, end), (end, start), (start, start)):
            with pytest.raises(ValueError):
                await call(*args, lower, upper)
        client.query.assert_not_awaited()
        await call(*args, start, end)
        sql, bound = client.query.call_args.args
        assert bound["start"] == start and bound["end"] == end
        assert "analytics_pipeline_events FINAL" in sql or "analytics_user_events FINAL" in sql
        assert "created_at" in sql and "<{end:" in sql

    asyncio.run(run())
