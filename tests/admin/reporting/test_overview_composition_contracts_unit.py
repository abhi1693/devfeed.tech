"""Overview composition keeps inventory, active work and windowed history distinct.

Aggregate rows and domain read services are preselected boundaries. These tests
do not evaluate SQL filtering/counting, database snapshots or rollup publication.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import overview, overview_processing
from devfeed_admin_api.automation import AutomationOverview
from devfeed_admin_api.overview_insights import (
    OverviewInsights,
    OverviewProcessing,
    OverviewReaderDay,
)
from devfeed_core.models import ReadModelState
from sqlalchemy.orm import Session

from tests.admin.reporting.test_report_worker_policy_unit import counts, rollup
from tests.support.source_readiness import NOW


class ReadResult:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value

    def mappings(self):
        return self.value

    def __iter__(self):
        return iter(self.value)


@pytest.fixture
def calculation(monkeypatch):
    first = NOW.date() - timedelta(days=2)
    stored = rollup()
    stored.payload["active"]["analysis"].update(queued=3, running=1)
    stored.payload["active"]["topic-analysis"].update(
        queued=1, running=1, oldest_queued_at=(NOW - timedelta(days=100)).isoformat()
    )
    stored.payload["active"]["ingestion"].update(queued=100, running=1)
    for index, (completed, failed) in enumerate(((2, 1), (3, 0), (4, 1))):
        stored.payload["daily"][str(first + timedelta(days=index))] = {
            "analysis": counts(completed, failed),
            "topic-analysis": counts(index, (0, 2, 1)[index]),
            "ingestion": counts(999),
        }
    session = Mock(spec=Session)
    session.get.return_value = stored
    processing = overview_processing.processing_facts(session, NOW - timedelta(days=2), NOW)
    session.reset_mock()
    insights = OverviewInsights(
        reader_activity=[
            OverviewReaderDay(date=str(first + timedelta(days=index)), added=added, published=2)
            for index, added in enumerate((2, 4, 1))
        ],
        processing=[OverviewProcessing(**row) for row in processing],
    )
    automation = AutomationOverview(
        blockers=[],
        published_in_window=6,
        published_without_intervention=2,
        automatic_publication_percent=100 / 3,
        median_ingestion_to_publication_seconds=120,
        analysis_tokens=900,
        analysis_duration_ms=5000,
        usage_reported_runs=2,
    )
    topic_id = uuid.uuid4()
    reads = [
        ReadResult((12, 4, 3)),
        ReadResult((5, 1, 3, 1)),
        ReadResult((8, 6, 2, 1)),
        ReadResult([{"id": topic_id, "name": "Angular", "articles": 3}]),
    ]
    session.execute.side_effect = reads
    monkeypatch.setattr(overview, "utcnow", lambda: NOW)
    insight_read, automation_read = Mock(return_value=insights), Mock(return_value=automation)
    monkeypatch.setattr(overview, "overview_insights", insight_read)
    monkeypatch.setattr(overview, "automation_metrics", automation_read)
    return SimpleNamespace(
        session=session,
        stored=stored,
        reads=reads,
        first=first,
        insights=insights,
        automation=automation,
        insight_read=insight_read,
        automation_read=automation_read,
        topic_id=topic_id,
    )


@pytest.mark.parametrize("mode", ["live", "saved", "saved-empty-window"])
def test_overview_maps_current_inventory_and_windowed_activity_without_hiding_old_active_work(
    calculation, mode
):
    value = calculation
    if mode == "saved-empty-window":
        # Retained older completions remain valid, but none belong to this requested range.
        for index in range(3):
            value.stored.payload["daily"][str(value.first + timedelta(days=index))] = {}
        rows = overview_processing.processing_facts(value.session, NOW - timedelta(days=2), NOW)
        value.insights.processing = [OverviewProcessing(**row) for row in rows]
        value.session.reset_mock()
    projected = mode != "live"
    if not projected:
        value.session.execute.side_effect = [
            *value.reads,
            ReadResult((4, 2, 12, 5)),
            ReadResult(
                [(value.first, 2, 1), (value.first + timedelta(days=1), 4, 2), (NOW.date(), 6, 2)]
            ),
        ]
    output = overview.overview_metrics(value.session, 3, projected=projected)
    assert (output.articles, output.articles_pending_review, output.articles_published) == (
        12,
        4,
        3,
    )
    assert (
        output.sources,
        output.sources_pending_review,
        output.sources_active,
        output.sources_failing,
    ) == (5, 1, 3, 1)
    assert (
        output.topics,
        output.topics_active,
        output.topic_proposals_pending,
        output.relationship_proposals_pending,
    ) == (8, 6, 2, 1)
    assert output.generated_at == NOW and output.days == 3
    assert output.top_topics[0].model_dump() == {
        "id": value.topic_id,
        "name": "Angular",
        "articles": 3,
    }
    assert output.automation is value.automation and output.insights is value.insights
    assert [row.added for row in output.activity] == [2, 4, 1]
    # Six first-publication events can include stories subsequently withdrawn;
    # they must not replace the three stories currently published in inventory.
    assert sum(row.published for row in output.activity) == 6
    assert output.analysis.model_dump() == {
        "queued": 4,
        "running": 2,
        "succeeded": 0 if mode == "saved-empty-window" else 12,
        "failed": 0 if mode == "saved-empty-window" else 5,
    }
    assert [row.date for row in output.analysis_activity] == [
        value.first,
        value.first + timedelta(days=1),
        NOW.date(),
    ]
    assert [(row.succeeded, row.failed) for row in output.analysis_activity] == (
        [(0, 0)] * 3 if mode == "saved-empty-window" else [(2, 1), (4, 2), (6, 2)]
    )
    value.insight_read.assert_called_once_with(value.session, 3, NOW)
    start = NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    value.automation_read.assert_called_once_with(value.session, start, NOW, projected=projected)
    if not projected:
        value.session.get.assert_not_called()
    value.session.add.assert_not_called()
    value.session.commit.assert_not_called()


def test_projected_report_refuses_partial_output_when_rollup_state_disappears_after_insights_read(
    calculation,
):
    value = calculation
    value.session.get.return_value = None
    with pytest.raises(RuntimeError, match="Processing rollups are still rebuilding"):
        overview.overview_metrics(value.session, 3, projected=True)
    value.session.get.assert_called_once_with(ReadModelState, overview_processing.STATE_NAME)
    value.automation_read.assert_not_called()
    value.session.commit.assert_not_called()
