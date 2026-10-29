"""Native range boundaries apply to event and current-state Overview cohorts."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_admin_api.analytics_current import CurrentCards
from devfeed_admin_api.overview_windows import ReportWindow
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_direct_cards import normalize_card
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics

from tests.support import analytics


@pytest.mark.integration
def test_selected_day_bounds_current_cohorts_and_preserves_full_publication_wait(
    integration_environment,
):
    start = datetime(2026, 10, 29, tzinfo=UTC)
    end = start + timedelta(days=1)
    inside = start + timedelta(hours=12)
    moments = [
        start - timedelta(microseconds=1),
        start,
        inside,
        end,
        end + timedelta(microseconds=1),
    ]
    users = [uuid.uuid4() for _ in moments]
    jobs = [uuid.uuid4() for _ in moments]
    topic, publication_only = uuid.uuid4(), uuid.uuid4()
    configured = analytics.settings()

    async def run():
        target = f"overview_cohorts_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(configured) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                settings = configured.model_copy(update={"clickhouse_database": target})
                async with AnalyticsClient(settings) as client:
                    await analytics.prepare(client, start)
                    writer = DirectOverviewAnalytics(client)
                    await writer.jobs(
                        [
                            {
                                "id": identity,
                                "kind": "analysis",
                                "status": "running" if index == 2 else "queued",
                                "created_at": moment,
                                "revision": 100,
                            }
                            for index, (identity, moment) in enumerate(
                                zip(jobs, moments, strict=True)
                            )
                        ]
                    )
                    # Mutable cohort fields must be filtered after the latest revision wins.
                    await writer.jobs(
                        [
                            {
                                "id": jobs[index],
                                "kind": "analysis",
                                "status": "queued",
                                "created_at": moment,
                                "revision": 101,
                            }
                            for index, moment in [(0, inside), (1, moments[0])]
                        ]
                    )
                    queues = await writer.queues(start, end)
                    assert [(row["kind"], row["queued"], row["running"]) for row in queues] == [
                        ("analysis", 1, 1)
                    ]

                    feeds = [
                        {
                            "user_id": user,
                            "created_at": inside if index == 4 else start - timedelta(days=30),
                            "computed_at": moment,
                            "has_generation": True,
                            "interest_count": 1,
                            "has_recommendations": True,
                            "expires_at": end + timedelta(days=1),
                            "revision": 100,
                        }
                        for index, (user, moment) in enumerate(zip(users, moments, strict=True))
                    ]
                    never_computed = uuid.uuid4()
                    feeds.extend(
                        {
                            "user_id": identity,
                            "created_at": moment,
                            "computed_at": None,
                            "has_generation": False,
                            "interest_count": 1,
                            "revision": 100,
                        }
                        for identity, moment in [
                            (never_computed, inside),
                            (uuid.uuid4(), moments[0]),
                        ]
                    )
                    await writer.feeds(feeds)
                    health = (await writer.feed_health(inside, start, end))[0]
                    assert health["ready"] == 2
                    assert health["pending"] == 1
                    assert (
                        sum(
                            health[key]
                            for key in ("ready", "refreshing", "expired", "pending", "not_needed")
                        )
                        == 3
                    )

                    await writer.memberships(
                        [
                            {
                                "user_id": users[index],
                                "topic_id": topic,
                                "kind": "follow",
                                "occurred_at": moment,
                                "revision": 100,
                            }
                            for index, moment in zip([2, 4, 0, 1, 3], moments, strict=True)
                        ]
                        + [
                            {
                                "user_id": user,
                                "topic_id": topic,
                                "kind": "inferred",
                                "occurred_at": moments[0],
                                "revision": 100,
                            }
                            for user in [*users, never_computed]
                        ]
                    )
                    await writer.publications(
                        [
                            {
                                "article_id": uuid.uuid4(),
                                "published_at": moment,
                                "discovered_at": moment - timedelta(hours=172),
                                "source_ids": [],
                                "topic_ids": [topic],
                                "automated": True,
                                "revision": 100,
                            }
                            for moment in moments
                        ]
                        + [
                            {
                                "article_id": uuid.uuid4(),
                                "published_at": inside,
                                "discovered_at": inside - timedelta(hours=1),
                                "source_ids": [],
                                "topic_ids": [publication_only],
                                "automated": True,
                                "revision": 100,
                            }
                        ]
                    )
                    coverage = {row["id"]: row for row in await writer.topic_coverage(start, end)}
                    assert set(coverage) == {str(topic), str(publication_only)}
                    assert coverage[str(topic)]["followers"] == 2
                    assert coverage[str(topic)]["inferred_users"] == 2
                    assert coverage[str(topic)]["publications"] == 2
                    assert coverage[str(publication_only)]["publications"] == 1
                    assert coverage[str(publication_only)]["followers"] == 0
                    assert coverage[str(publication_only)]["inferred_users"] == 0
                    publications = await writer.publication_report(start, end)
                    assert sum(row["published"] for row in publications) == 3
                    assert publications[0]["percentiles"][1] > 24 * 3600

                    policy = pipeline_policy_version(settings)
                    cards = []
                    for moment in moments:
                        cards.append(
                            normalize_card(
                                {
                                    "stage": "review",
                                    "state": "blocked",
                                    "created_at": moment,
                                    "activity_at": moment,
                                    "review_pending": True,
                                    "blocker_codes": ["awaiting_enrichment"],
                                },
                                policy,
                                "article",
                                uuid.uuid4(),
                                100,
                                inside,
                            )
                        )
                        cards.append(
                            normalize_card(
                                {
                                    "stage": "active",
                                    "state": "blocked",
                                    "created_at": moments[0],
                                    "activity_at": moment,
                                    "enabled": True,
                                    "approval_status": "approved",
                                    "consecutive_failures": 1,
                                },
                                policy,
                                "source",
                                uuid.uuid4(),
                                100,
                                inside,
                            )
                        )
                    await client.insert("analytics_card_canonical", cards)
                    report = await CurrentCards(client, now=inside).operations(
                        ReportWindow(start.date(), end.date())
                    )
                    assert report.days == 1 and report.start == start.date()
                    assert report.end == start.date()
                    assert {row.kind: row.count for row in report.data.reviews}["articles"] == 2
                    assert report.data.source_failures == 2
                    blocker = next(
                        row for row in report.data.blockers if row.code == "awaiting_enrichment"
                    )
                    assert blocker.count == 2
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())
