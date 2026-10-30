"""Private worker decisions preserve the publication under stale or lost authority.

Repository boundaries return explicit rows here. Native worker tests exercise the
real SQL predicates, opposing lock acquisition and transactional publication.
"""

import uuid
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import article_refresh_tasks as worker
from devfeed_aggregator.analysis_tasks import AnalysisAttempt, AnalysisContext
from devfeed_core import article_refresh_processing as processing
from sqlalchemy.orm import Session

from tests.support import article_maintenance_unit as support
from tests.support import source_readiness
from tests.support.article_maintenance_unit import prepared as prepared


@pytest.fixture
def runtime(prepared, monkeypatch):
    public, owner, inputs, _ = prepared
    job = inputs.facts["analysis"]
    job.status, job.attempts, job.usage = "queued", 0, {}
    job.available_at = support.NOW - timedelta(seconds=1)
    job.lease_token = uuid.uuid4()
    read, write = Mock(spec=Session), Mock(spec=Session)
    write.info = {}
    write.scalar.return_value = public
    read.get.return_value = job
    read.execute.return_value.one_or_none.return_value = (
        owner.version,
        inputs.draft.fields.model_dump(mode="json"),
    )
    factory = support.WorkerSessions(read, write)
    context = AnalysisContext(
        job.id, job.lease_token, inputs.snapshot, public.id, owner.id, job.input_hash
    )
    settings = source_readiness.configuration(ai_enabled=True, full_automation=True)
    value = SimpleNamespace(
        public=public,
        owner=owner,
        inputs=inputs,
        job=job,
        context=context,
        read=read,
        write=write,
        factory=factory,
        settings=settings,
        metadata=(owner.version, inputs.draft.fields),
    )
    value.authority = Mock(return_value=[public.origins[0].source])
    value.owner_lookup = Mock(return_value=owner)
    value.lease = Mock(return_value=job)
    value.eligible = Mock(return_value=True)
    value.catalog = Mock(return_value=True)
    value.evidence = Mock()
    value.resume = Mock()
    monkeypatch.setattr(worker, "utcnow", lambda: support.NOW)
    monkeypatch.setattr(worker, "approved_sources", value.authority)
    monkeypatch.setattr(worker, "refresh_for_article", value.owner_lookup)
    monkeypatch.setattr(worker, "owned_job", value.lease)
    monkeypatch.setattr(worker, "eligible_article", value.eligible)
    monkeypatch.setattr(worker, "prepare_refresh_inputs", lambda *_: value.inputs)
    monkeypatch.setattr(worker, "analysis_catalog_current", value.catalog)
    monkeypatch.setattr(worker, "validate_evidence", value.evidence)
    monkeypatch.setattr(worker, "lock_topics", Mock())
    monkeypatch.setattr(worker, "snapshot_current", Mock(return_value=True))
    monkeypatch.setattr(processing, "resume_refresh", value.resume)
    value.service = worker.RefreshAnalysisService(settings, factory, Mock())
    return value


def approved_values(article):
    return {column.name: getattr(article, column.name) for column in article.__table__.columns}


@pytest.mark.parametrize("missing", [False, True])
def test_metadata_preparation_reads_only_owner_fields_without_starting_a_write(runtime, missing):
    if missing:
        runtime.read.execute.return_value.one_or_none.return_value = None
    result = worker._metadata(runtime.factory, runtime.owner.id)
    assert result == (None if missing else runtime.metadata)
    assert not runtime.factory.writing
    sql = str(runtime.read.execute.call_args.args[0])
    assert "payload" in sql and "fields" not in sql  # JSON key remains a bound value.
    assert "FOR UPDATE" not in sql


@pytest.mark.parametrize(
    "failure",
    [
        "source",
        "article",
        "owner",
        "metadata",
        "version",
        "selection",
        "source_hash",
        "job_hash",
        "url",
        "date",
    ],
)
def test_claim_and_checkpoint_guard_rejects_lost_authority_without_mutating_publication(
    runtime, failure
):
    r = runtime
    before = approved_values(r.public)
    approved, article, metadata = True, r.public, r.metadata
    if failure == "source":
        approved = False
    elif failure == "article":
        article = None
    elif failure == "owner":
        r.owner_lookup.return_value = None
    elif failure == "metadata":
        metadata = None
    elif failure == "version":
        metadata = (99, r.inputs.draft.fields)
    elif failure == "selection":
        r.owner.analysis_job_id = uuid.uuid4()
    elif failure == "source_hash":
        r.owner.source_hash = "other-source"
    elif failure == "job_hash":
        r.job.input_hash = "other-input"
    elif failure == "url":
        r.context = replace(
            r.context, snapshot={**r.inputs.snapshot, "url": "https://other.example/"}
        )
    else:
        r.eligible.return_value = False
    assert worker._guard(r.write, r.context, r.job, metadata, article, approved) is None
    assert r.job.status == "succeeded"
    assert r.job.outcome == (
        "unapproved"
        if failure == "source"
        else "content_date_deferred"
        if failure == "date"
        else "superseded"
    )
    assert approved_values(r.public) == before
    assert r.owner.status == "active"


def test_checkpoint_guard_accepts_only_the_exact_selected_owner_and_hash(runtime):
    r = runtime
    assert worker._guard(r.write, r.context, r.job, r.metadata, r.public, True, r.owner) is r.owner
    r.owner_lookup.assert_not_called()
    assert r.job.status == "queued"


@pytest.mark.parametrize(
    "failure", ["missing", "foreign", "claimed", "future", "disabled", "source", "locked_missing"]
)
def test_private_analysis_does_not_start_inference_for_an_ineligible_claim(
    runtime, monkeypatch, failure
):
    r = runtime
    run = Mock()
    monkeypatch.setattr(worker.RefreshAnalysisService, "run", run)
    r.write.scalar.side_effect = [r.public, r.job]
    if failure == "missing":
        r.read.get.return_value = None
    elif failure == "foreign":
        r.job.refresh_id = uuid.uuid4()
    elif failure == "claimed":
        r.job.status = "running"
    elif failure == "future":
        r.job.available_at = support.NOW + timedelta(minutes=1)
    elif failure == "disabled":
        r.settings.ai_enabled = False
    elif failure == "locked_missing":
        r.write.scalar.side_effect = [r.public, None]
    else:
        r.authority.return_value = []
    worker.analyze_refresh(r.factory, r.settings, r.job.id, r.owner.id)
    run.assert_not_called()
    assert not r.factory.writing
    assert r.public.publication_status == "published"


def test_private_claim_commits_lease_before_running_specialists(runtime, monkeypatch):
    r = runtime
    r.write.scalar.side_effect = [r.public, r.job]
    calls = []

    def run(_service, context):
        assert not r.factory.writing
        calls.append(context)

    monkeypatch.setattr(worker.RefreshAnalysisService, "run", run)
    worker.analyze_refresh(r.factory, r.settings, r.job.id, r.owner.id)
    assert len(calls) == 1 and calls[0].refresh_id == r.owner.id
    assert calls[0].token == r.job.lease_token and r.job.attempts == 1
    assert r.job.status == "running" and r.job.usage["active_step"] is None


@pytest.mark.parametrize(
    "failure", ["lease", "source", "article", "owner", "selection", "source_hash", "job_hash"]
)
def test_private_owned_attempt_cannot_checkpoint_a_foreign_or_withdrawn_subject(runtime, failure):
    r = runtime
    if failure == "lease":
        r.lease.return_value = None
    elif failure == "source":
        r.authority.return_value = []
    elif failure == "article":
        r.write.scalar.return_value = None
    elif failure == "owner":
        r.owner_lookup.return_value = None
    elif failure == "selection":
        r.owner.analysis_job_id = uuid.uuid4()
    elif failure == "source_hash":
        r.owner.source_hash = "changed"
    else:
        r.job.input_hash = "changed"
    assert r.service.owned_job(r.write, r.context) is None
    assert worker.LOCKED_REFRESH not in r.write.info
    assert r.public.publication_status == "published"


def test_specialist_checkpoint_uses_locked_subject_and_explicit_metadata_version(
    runtime, monkeypatch
):
    r = runtime
    record = Mock(return_value={"status": "running"})
    monkeypatch.setattr(worker.RefreshAnalysisService, "record_step", record)
    assert r.service.start_step(r.context, "overview", "digest") == {"status": "running"}
    record.assert_called_once_with(r.job, r.context, "overview", "digest")
    assert r.write.info[worker.LOCKED_REFRESH] == (r.public, r.owner)
    assert not r.factory.writing
    r.service.propose_topics(r.context, r.inputs.draft.analysis)


@pytest.mark.parametrize("failure", ["lease", "version"])
def test_lost_specialist_attempt_commits_its_terminal_marker_without_starting_a_step(
    runtime, monkeypatch, failure
):
    r = runtime
    record = Mock()
    monkeypatch.setattr(worker.RefreshAnalysisService, "record_step", record)
    if failure == "lease":
        r.lease.return_value = None
    else:
        r.read.execute.return_value.one_or_none.return_value = (
            999,
            r.inputs.draft.fields.model_dump(mode="json"),
        )
    assert r.service.start_step(r.context, "overview", "digest") is None
    record.assert_not_called()
    assert not r.factory.writing
    if failure == "version":
        assert r.job.outcome == "superseded"


@pytest.mark.parametrize(
    "variant",
    [
        "ready",
        "insufficient",
        "catalog",
        "lease",
        "version",
        "receipt",
        "retry",
        "churn",
        "prior",
        "input",
        "model_fallback",
        "no_topic_status",
    ],
)
def test_private_apply_keeps_public_state_and_resumes_only_committed_replacement_work(
    runtime, monkeypatch, variant
):
    r = runtime
    before = approved_values(r.public)
    result = r.inputs.draft.analysis
    if variant == "insufficient":
        result = support.ready_analysis(
            r.inputs.draft.topics[0].topic_id, outcome="insufficient_evidence"
        )
    if variant == "catalog":
        r.catalog.return_value = False
    elif variant == "lease":
        r.lease.return_value = None
    elif variant == "version":
        r.owner.version += 1
    elif variant == "receipt":

        def owned(_self, session, _context):
            session.info[worker.LOCKED_REFRESH] = (r.public, r.owner)
            r.job.catalog_hash = "changed"
            return r.job

        monkeypatch.setattr(worker.RefreshAnalysisService, "owned_job", owned)
    elif variant in {"retry", "churn"}:
        monkeypatch.setattr(
            worker,
            "snapshot_current",
            Mock(side_effect=[False, True] if variant == "retry" else [False] * 3),
        )
    if variant == "prior":
        r.inputs.facts["analysis"] = None
    elif variant == "input":
        r.inputs = replace(r.inputs, input_hash="changed-private-input")
    attempt = AnalysisAttempt(
        client=None if variant == "model_fallback" else SimpleNamespace(model="replacement-model")
    )

    def operation():
        return r.service.apply_result(
            r.context, result, None if variant == "no_topic_status" else "matched", attempt
        )

    if variant == "churn":
        with pytest.raises(worker.analysis_tasks._ApplicationInputsChanged):
            operation()
        r.resume.assert_not_called()
    else:
        outcome = operation()
        r.resume.assert_called_once_with(r.factory, r.owner.id)
        if variant in {"ready", "insufficient", "retry", "model_fallback", "no_topic_status"}:
            assert outcome == ("insufficient_evidence" if variant == "insufficient" else "applied")
            assert r.owner.payload["analysis"] == result.model_dump(mode="json")
            assert r.owner.version == 2
            if variant == "no_topic_status":
                assert "topic_match_status" not in r.job.result
            else:
                assert r.job.result["topic_match_status"] == "matched"
            assert r.job.model == (
                r.settings.codex_model if variant == "model_fallback" else "replacement-model"
            )
        elif variant == "lease":
            assert outcome is None
        else:
            assert outcome == "superseded"
    assert approved_values(r.public) == before
    assert not r.factory.writing


@pytest.mark.parametrize("status", ["cancelled", "superseded", "promoted"])
def test_late_closed_owner_claim_does_not_hydrate_private_body_or_run_specialists(
    runtime, monkeypatch, status
):
    r = runtime

    class ClosedPreview:
        article_id = r.public.id
        refresh_id = r.owner.id

        @property
        def input_snapshot(self):
            raise AssertionError("Closed private work must not read retained input")

    r.owner.status = status
    r.owner.payload_pruned_at = support.NOW
    r.read.get.return_value = ClosedPreview()
    r.read.execute.return_value.one_or_none.return_value = None
    r.write.scalar.side_effect = [r.public, r.job]
    run = Mock()
    monkeypatch.setattr(worker.RefreshAnalysisService, "run", run)
    worker.analyze_refresh(r.factory, r.settings, r.job.id, r.owner.id)
    run.assert_not_called()
    assert r.job.outcome == "superseded" and r.owner.status == status
    assert r.public.publication_status == "published"
