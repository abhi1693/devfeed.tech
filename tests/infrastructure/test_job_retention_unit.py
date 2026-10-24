"""Retention cadence and page checkpoints stay bounded at repository boundaries."""

import uuid
from collections import namedtuple
from datetime import timedelta
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import job_retention
from devfeed_core.models import ArticleAnalysisJob, JobPayloadRetention, TopicAnalysisJob

from tests.support.source_readiness import NOW, configuration

Candidate = namedtuple("Candidate", "id finished_at subject_id")


def test_not_due_histories_never_select_candidates_or_write_checkpoints(monkeypatch):
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.scalar.return_value = None
    page = Mock()
    monkeypatch.setattr(job_retention, "_prune_page", page)
    monkeypatch.setattr(job_retention, "get_settings", configuration)
    monkeypatch.setattr(job_retention, "utcnow", lambda: NOW)
    assert job_retention.prune_job_payloads(factory) == 0
    assert factory.begin.call_count == session.scalar.call_count == 2
    page.assert_not_called()
    session.scalars.assert_not_called()


@pytest.mark.parametrize(
    "model,kind", [(ArticleAnalysisJob, "article"), (TopicAnalysisJob, "topic")]
)
@pytest.mark.parametrize(
    "outcome", ["protected-page", "prune-budget", "locked-candidates", "end-of-sweep"]
)
def test_cursor_advances_over_protected_rows_and_stops_at_the_prune_budget(model, kind, outcome):
    old = NOW - timedelta(days=40)
    state = JobPayloadRetention(kind=kind, next_run_at=NOW)
    session = MagicMock()
    candidates = [
        Candidate(uuid.UUID(int=index + 1), old + timedelta(seconds=index), uuid.uuid4())
        for index in range(2)
    ]
    selected = []
    if outcome == "prune-budget":
        first = candidates[0]
        selected = [model(id=first.id, finished_at=first.finished_at, usage={})]
    page = [] if outcome == "end-of-sweep" else candidates
    eligible = candidates[:1] if outcome in {"prune-budget", "locked-candidates"} else []
    session.execute.side_effect = [Mock(all=lambda: page), Mock(all=lambda: eligible)]
    session.scalars.side_effect = [
        Mock(all=lambda: [row.subject_id for row in eligible]),
        Mock(all=lambda: [row.id for row in selected]),
        Mock(all=lambda: selected),
    ]
    settings = configuration(job_payload_scan_batch_size=2, job_payload_prune_batch_size=1)
    assert job_retention._prune_page(session, model, state, NOW, settings) == len(selected)
    if outcome == "end-of-sweep":
        assert state.cutoff is state.cursor_finished_at is state.cursor_id is None
        assert state.next_run_at == NOW + timedelta(hours=1)
        session.scalars.assert_not_called()
    else:
        expected = candidates[0] if eligible else candidates[-1]
        assert (state.cursor_finished_at, state.cursor_id) == (expected.finished_at, expected.id)
        assert state.cutoff == NOW - timedelta(days=30)
        assert state.next_run_at == NOW + timedelta(seconds=30)
        if selected:
            assert selected[0].input_snapshot == {}
            assert selected[0].inputs_pruned_at == NOW


def test_resumed_page_keeps_the_original_age_cutoff():
    cutoff = NOW - timedelta(days=31)
    state = JobPayloadRetention(kind="article", next_run_at=NOW, cutoff=cutoff)
    session = MagicMock()
    page = [Candidate(uuid.uuid4(), cutoff - timedelta(days=1), uuid.uuid4())]
    session.execute.side_effect = [Mock(all=lambda: page), Mock(all=lambda: [])]
    settings = configuration(job_payload_scan_batch_size=1)
    assert job_retention._prune_page(session, ArticleAnalysisJob, state, NOW, settings) == 0
    assert state.cutoff == cutoff
