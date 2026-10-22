"""First delivery and refreshed projections preserve their real continuation contracts.

Repository results are already selected. These cases check orchestration and
projection composition, without evaluating audience/admission SQL predicates.
"""

import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import test_discovery_worker_contracts_unit as discovery_fixtures
import test_runtime_feed_notifications_unit as notification_fixtures
from devfeed_core import discovery, feed_notifications, overview_daily
from devfeed_core.models import OverviewDaily
from test_discovery_worker_contracts_unit import feeds as feeds
from test_runtime_overview_daily_unit import metrics, midnight
from test_source_readiness_unit import NOW, configuration

discovery_runtime = discovery_fixtures.runtime
notification_runtime = notification_fixtures.runtime


def test_targeted_discovery_can_start_when_the_enabled_assessment_lane_has_no_waiting_work(
    discovery_runtime, monkeypatch
):
    current = discovery_runtime
    monkeypatch.setattr(discovery, "get_settings", lambda: configuration(ai_enabled=True))
    # No running discovery, the targeted job, no due assessment, and its candidate.
    current.read.scalar.side_effect = [None, current.job, None, current.row]

    result = discovery.run_one(target_job_id=current.job.id)

    assert result["status"] == "succeeded" and result["feeds_found"] == 2
    assert current.job.attempts == 1 and current.job.lease_token is None
    assert current.source.approval_status == "pending" and not current.source.enabled
    current.crawler.assert_called_once_with(
        current.row.identity_url, ["https://publisher.example/rss"]
    )
    current.handoff.assert_called_once_with(current.write, current.row)


def test_first_notification_page_delivers_without_requiring_an_existing_recipient_cursor(
    notification_runtime,
):
    current = notification_runtime
    user = SimpleNamespace(
        id=uuid.UUID(int=1),
        issuer="https://identity.example",
        subject="first-reader",
        organization_id="readers",
    )
    article_read, audience_read = Mock(), Mock()
    article_read.first.return_value = current.article
    audience_read.all.return_value = [user]
    current.session.execute.side_effect = [article_read, audience_read, Mock()]
    assert current.event.recipient_cursor is None

    assert feed_notifications.expand_feed_notifications(current.factory, batch=2) == 1

    assert current.event.recipient_cursor == user.id and current.event.completed_at == NOW
    parameters = current.session.execute.call_args.args[0].compile().params
    assert parameters["payload_m0"]["user_id"] == str(user.id)
    assert parameters["payload_m0"]["article_id"] == str(current.article.id)
    assert user.subject not in str(parameters) and user.issuer not in str(parameters)


def test_live_daily_measurements_replace_previously_retained_rollup_counts(monkeypatch):
    today = NOW.date()
    yesterday = today - timedelta(days=1)
    old = metrics(opens=90, readers=40, multi_article_readers=20)
    values = {
        yesterday: metrics(opens=8, readers=4, multi_article_readers=2),
        today: metrics(opens=3, readers=2, multi_article_readers=1),
    }
    latest = OverviewDaily(day=today, metrics=old, updated_at=NOW - timedelta(minutes=6))
    session = Mock()
    session.scalar.side_effect = [True, latest]
    session.execute.return_value.all.return_value = [(yesterday, old), (today, old)]
    compute = Mock(return_value=values)
    monkeypatch.setattr(overview_daily, "daily_metrics", compute)
    monkeypatch.setattr(overview_daily, "utcnow", lambda: NOW)

    @contextmanager
    def begin():
        yield session

    overview_daily.refresh_overview_daily(SimpleNamespace(begin=begin))

    compute.assert_called_once_with(session, midnight(yesterday), NOW)
    assert values[yesterday] == metrics(opens=8, readers=4, multi_article_readers=2)
    assert values[today] == metrics(opens=3, readers=2, multi_article_readers=1)
    assert old == metrics(opens=90, readers=40, multi_article_readers=20)
    writes = session.execute.call_args.args[0].compile().params
    assert writes["metrics_m1"] == values[today] and writes["updated_at_m1"] == NOW
