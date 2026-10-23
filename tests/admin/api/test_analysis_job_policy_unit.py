"""Mixed AI run pages retain operator history, labels and retry presentation.

The union page and current job details are scoped repository results. These tests
verify composition after selection, not database filtering or correlated eligibility.
"""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import jobs
from devfeed_http.pagination import ListQuery
from fastapi import HTTPException

from tests.admin.taxonomy.test_taxonomy_policy_unit import queued_metadata
from tests.analysis.test_research_input_guards_unit import clock as clock
from tests.analysis.test_research_input_guards_unit import completed as completed
from tests.support.admin_editorial import operator_api
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


def failed_copy(previous):
    current = copy.deepcopy(previous)
    current.id = uuid.uuid4()
    current.status, current.attempts = "failed", 3
    current.outcome, current.result, current.error = None, {}, "provider_unavailable"
    current.created_at = current.available_at = NOW + timedelta(minutes=1)
    current.finished_at, current.lease_token, current.lease_until = (
        NOW + timedelta(minutes=2),
        None,
        None,
    )
    return current


@pytest.mark.parametrize("mode", ["chronological", "failed", "retried", "status-sort", "running"])
def test_mixed_analysis_page_preserves_union_order_and_current_retry_presentation(
    processing_case, completed, mode
):
    article_job = failed_copy(processing_case[1]["analysis"])
    topic_job = failed_copy(queued_metadata(completed[1], {"subject": "operator"}))
    retryable = mode not in {"retried", "running"}
    status = None if mode in {"chronological", "status-sort"} else mode
    if mode == "running":
        for current in (article_job, topic_job):
            current.status, current.finished_at, current.error = "running", None, None
            current.attempts, current.lease_token, current.lease_until = (
                1,
                uuid.uuid4(),
                NOW + timedelta(minutes=5),
            )
    page = [
        dict(
            id=topic_job.id,
            kind="topic-analysis",
            status=topic_job.status,
            retryable=retryable,
            target_name=completed[1].proposed["name"],
        ),
        dict(
            id=article_job.id,
            kind="analysis",
            status=article_job.status,
            retryable=retryable,
            target_name="Angular routing",
        ),
    ]
    needs_retry = mode in {"failed", "retried", "status-sort"}
    selected = iter(
        [
            Result(page),
            Result([(article_job, False if needs_retry else retryable)]),
            Result([(topic_job, False if needs_retry else retryable)]),
        ]
    )
    session = SimpleNamespace(scalar=lambda _: 21, execute=lambda _: next(selected))
    query = ListQuery(q="", sort="status" if mode == "status-sort" else None, limit=2, offset=4)
    report = jobs.ai_analysis_jobs(session, query, status=status)
    assert (report["total"], report["limit"], report["offset"]) == (21, 2, 4)
    assert [item.id for item in report["items"]] == [topic_job.id, article_job.id]
    assert [item.target_name for item in report["items"]] == [
        completed[1].proposed["name"],
        "Angular routing",
    ]
    expected_status = "running" if mode == "running" else "retried" if not retryable else "failed"
    assert all(
        item.status == expected_status and item.retryable is (retryable and mode != "running")
        for item in report["items"]
    )
    wire = " ".join(item.model_dump_json() for item in report["items"])
    assert "input_snapshot" not in wire and "catalog_snapshot" not in wire
    assert "lease_token" not in wire and "lease_until" not in wire
    assert topic_job.status == article_job.status == ("running" if mode == "running" else "failed")


def test_concurrent_job_removal_does_not_fabricate_a_placeholder_or_change_union_page_total(
    processing_case, completed
):
    article_job, topic_job = processing_case[1]["analysis"], completed[0]
    page = [
        dict(
            id=article_job.id,
            kind="analysis",
            status="succeeded",
            retryable=False,
            target_name="Angular routing",
        ),
        dict(
            id=topic_job.id,
            kind="topic-analysis",
            status="succeeded",
            retryable=False,
            target_name=completed[1].proposed["name"],
        ),
    ]
    selected = iter([Result(page), Result([]), Result([(topic_job, False)])])
    session = SimpleNamespace(scalar=lambda _: 8, execute=lambda _: next(selected))
    report = jobs.ai_analysis_jobs(session, ListQuery(q="", sort="created_at", limit=2, offset=2))
    assert report["total"] == 8 and report["offset"] == 2
    assert [item.id for item in report["items"]] == [topic_job.id]
    assert report["items"][0].status == "succeeded"


@pytest.mark.parametrize("kind", ["articles", "topics"])
def test_subject_scoped_page_returns_only_its_supplied_kind_and_current_subject_label(
    processing_case, completed, kind
):
    current = processing_case[1]["analysis"] if kind == "articles" else completed[0]
    name = "Angular routing" if kind == "articles" else completed[1].proposed["name"]
    page = [
        dict(
            id=current.id,
            kind="analysis" if kind == "articles" else "topic-analysis",
            status="succeeded",
            retryable=False,
            target_name=name,
        )
    ]
    selected = iter([Result(page), Result([(current, False)])])
    session = SimpleNamespace(scalar=lambda _: 1, execute=lambda _: next(selected))
    report = jobs.ai_analysis_jobs(
        session,
        ListQuery(q=name, sort="-created_at", limit=10, offset=0),
        analysis_type=kind,
        article_id=current.article_id if kind == "articles" else None,
        proposal_id=current.proposal_id if kind == "topics" else None,
    )
    [item] = report["items"]
    assert item.id == current.id and item.target_name == name
    assert item.kind == page[0]["kind"] and not item.retryable


@pytest.mark.parametrize("sort", ["title", "-attempts"])
def test_unsupported_mixed_run_sort_is_rejected_before_storage_reads(sort):
    session = Mock()
    with pytest.raises(HTTPException, match="Unsupported sort field") as error:
        jobs.ai_analysis_jobs(session, ListQuery(q="", sort=sort, limit=5, offset=0))
    assert error.value.status_code == 422
    session.scalar.assert_not_called()
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "parameters", [{"limit": 0}, {"status": "superseded"}, {"analysis_type": "sources"}]
)
def test_invalid_mixed_run_http_inputs_fail_before_repository_access(parameters):
    with operator_api(jobs) as api:
        response = api.client.get("/v1/admin/jobs/ai-analysis", params=parameters)
        assert response.status_code == 422
        api.session.scalar.assert_not_called()
        api.session.execute.assert_not_called()
