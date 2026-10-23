"""Retry adapters preserve work identity; database eligibility is a supplied boundary."""

import copy
import hashlib
import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_aggregator.analysis_tasks import ArticleAnalysisService
from devfeed_core import analysis, job_retries, source_enrichment
from devfeed_core.article_analysis_steps import (
    STEP_SPECS,
    prepare_article_step,
    validate_article_step,
)
from devfeed_core.models import SourceEnrichmentJob
from devfeed_core.source_relevance import VERSION

from tests.cli.test_editorial_commands import operation as operation
from tests.support.source_readiness import NOW, configuration, job, profile, source


@pytest.mark.parametrize("kind", ["legacy", "profile", "relevance"])
def test_source_retry_preserves_the_failed_operation_instead_of_broadening_its_scope(
    monkeypatch, kind
):
    current = source()
    failed = job(
        SourceEnrichmentJob,
        status="failed",
        source_id=current.id,
        operation=kind,
        result={"input": source_enrichment.source_identity(current)},
    )
    queued = job(
        SourceEnrichmentJob,
        source_id=current.id,
        operation="relevance" if kind == "relevance" else "profile",
        created_at=NOW,
        available_at=NOW,
        result={"input": source_enrichment.source_identity(current)},
    )
    if kind == "relevance":
        sample = [{"title": "Application routing", "summary": "Engineering guidance for routing."}]
        completed = profile(
            current,
            created_at=NOW - timedelta(hours=2),
            available_at=NOW - timedelta(minutes=119),
            finished_at=NOW - timedelta(minutes=90),
        )
        completed.result.update(
            sample=sample,
            sample_hash=hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest(),
        )
        receipt = {
            "input": source_enrichment.source_identity(current),
            "profile_job_id": str(completed.id),
            "sample": sample,
            "sample_hash": completed.result["sample_hash"],
            "policy_version": VERSION,
        }
        failed.result, queued.result = copy.deepcopy(receipt), copy.deepcopy(receipt)
        assert completed.finished_at < failed.created_at
    # Eligibility is already checked by the repository query. These units test dispatch only.
    session = Mock()
    session.scalar.return_value = failed.id
    profile_request, relevance_request = Mock(return_value=queued), Mock(return_value=queued)
    monkeypatch.setattr(job_retries, "request_enrichment", profile_request)
    monkeypatch.setattr(source_enrichment, "request_relevance", relevance_request)
    before = copy.deepcopy(failed.result)
    assert job_retries.retry_failed_job(session, failed, "source-enrichment", {}) is queued
    chosen, other = (
        (relevance_request, profile_request)
        if kind == "relevance"
        else (profile_request, relevance_request)
    )
    chosen.assert_called_once_with(session, current.id)
    other.assert_not_called()
    assert failed.status == "failed" and failed.result == before
    assert failed.finished_at < queued.created_at


def completed_step(previous):
    name = "developer_relevance"
    _prompt, _schema, digest = prepare_article_step(
        name, previous.input_snapshot, previous.catalog_snapshot, {}
    )
    config = configuration(codex_model="test-model")
    return {
        "status": "succeeded",
        "input_hash": digest,
        "prompt_version": STEP_SPECS[name].prompt_version,
        "model": config.codex_model,
        "routing_binding": ArticleAnalysisService(config, Mock(), Mock()).routing_binding(),
        "attempts": 1,
        "started_at": (NOW - timedelta(minutes=8)).isoformat(),
        "finished_at": (NOW - timedelta(minutes=7)).isoformat(),
        "output": validate_article_step(
            name,
            {
                "outcome": "ready",
                "reason": "Application routing guidance for developers.",
                "developer_relevance": "relevant",
            },
            previous.input_snapshot,
            previous.catalog_snapshot,
            {},
        ),
        "error": None,
        "tokens": {},
        "resolved_model": config.codex_model,
    }


@pytest.mark.parametrize("successor_state", ["new", "changed-input", "started", "checkpointed"])
def test_analysis_retry_transfers_only_completed_checkpoints_into_an_unstarted_matching_run(
    monkeypatch, operation, successor_state
):
    previous, successor = operation.previous, operation.queued
    previous.available_at = NOW - timedelta(minutes=9)
    step = completed_step(previous)
    previous.steps = {
        "developer_relevance": step,
        "page_kind": {
            "status": "failed",
            "attempts": 3,
            "output": None,
            "error": "codex_timeout",
            "started_at": (NOW - timedelta(minutes=6)).isoformat(),
            "finished_at": previous.finished_at.isoformat(),
        },
    }
    expected = {}
    if successor_state == "changed-input":
        successor.input_snapshot = {**successor.input_snapshot, "text": "Updated source evidence."}
        successor.input_hash = analysis.snapshot_hash(successor.input_snapshot)
    elif successor_state == "started":
        # A concurrent requester can win the subject lock after our eligibility read.
        # Its queued retry has already spent attempts and cannot inherit our checkpoints.
        successor.attempts = 1
    elif successor_state == "checkpointed":
        successor.steps = {"developer_relevance": {**step, "tokens": {"input_tokens": 7}}}
        expected = copy.deepcopy(successor.steps)
    else:
        expected = {"developer_relevance": copy.deepcopy(step)}
    before = copy.deepcopy(previous.usage)
    operation.session.scalar.return_value = previous.id
    request = Mock(return_value=successor)
    monkeypatch.setattr(job_retries, "request_analysis", request)
    result = job_retries.retry_failed_job(
        operation.session, previous, "analysis", {"subject": "operator"}
    )
    assert result is successor and result.steps == expected
    assert previous.status == "failed" and previous.usage == before
    request.assert_called_once_with(operation.session, previous.article_id)
    if successor_state == "new":
        assert result.steps["developer_relevance"] is not previous.steps["developer_relevance"]
        result.steps["developer_relevance"]["status"] = "running"
        assert previous.steps["developer_relevance"]["status"] == "succeeded"
