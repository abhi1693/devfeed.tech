"""Editorial and analysis commands preserve operator authority, admission and dispatch scopes."""

import copy
import json
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_cli import editorial, runtime, search_commands
from devfeed_core import db, search_engine, search_index
from devfeed_core.analysis import AnalysisResult
from devfeed_core.config import get_settings
from devfeed_core.models import ArticleAnalysisJob, ArticleReview
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topics import TopicWrite
from devfeed_search_indexer import runtime as indexer
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from typer import Exit

from tests.admin.articles.test_article_board_policy_unit import history as article_history
from tests.api.test_feed_snapshot_contracts_unit import NOW as ANALYSIS_NOW
from tests.articles.test_article_lifecycle import facts
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import NOW, topic


@pytest.fixture
def command(monkeypatch, processing_case):
    article, facts = article_history(processing_case, "editorial-command")
    article.slug = "angular-routing"
    article.feed_at = article.discovered_at
    for name in ("analysis", "enrichment"):
        facts[name].available_at = facts[name].created_at
    facts["analysis"].result = AnalysisResult(
        outcome="ready",
        developer_relevance="relevant",
        page_kind="article",
        language=article.language,
        content_type=article.content_type,
        content_format=article.content_format,
        ai_title=article.ai_title,
        title_evidence=None,
        ai_summary=article.ai_summary,
        ai_description=article.ai_description,
        topics=[
            {
                "topic_id": article.topic_links[0].topic_id,
                "role": "primary",
                "relevance": 0.9,
                "evidence": "Angular routing",
            }
        ],
        tags=[],
        reasons=[],
    ).model_dump(mode="json")
    source = article.origins[0].source
    source.slug, source.managed_logo = "publisher", {}
    for origin in article.origins:
        origin.source_metadata = {}
    article.tag_links = []
    session = Mock()
    phases = []

    @contextmanager
    def factory():
        phases.append("open")
        try:
            yield session
        finally:
            phases.append("closed")

    factory.begin = factory
    monkeypatch.setattr(editorial, "session_factory", lambda: factory)
    session.get.return_value = article
    return NS(article=article, facts=facts, session=session, phases=phases)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_preselected_article_page_keeps_private_review_context_and_bounded_continuation(
    command, count
):
    rows = [command.article] if count else []
    if count == 2:
        other, _ = article_history((command.article, command.facts), "next-editorial-command")
        other.slug = "second-routing"
        rows.append(other)
    command.session.scalars.return_value.all.return_value = rows
    output = editorial.list_articles(
        NS(limit=2, after=uuid.uuid4(), review_status="approved", publication_status="unpublished")
    )
    assert [row["id"] for row in output["items"]] == [str(row.id) for row in rows]
    assert output["next_after"] == (str(rows[-1].id) if count == 2 else None)
    if rows:
        item = output["items"][0]
        assert item["review_status"] == "approved" and item["publication_status"] == "unpublished"
        assert item["editorial_revision"] == command.article.editorial_revision
        assert item["classification_provenance"]["analysis_id"] == str(command.facts["analysis"].id)
        assert item["publication_blockers"] == []


def test_inspection_of_missing_article_is_an_error_and_closes_read_scope(command):
    command.session.get.return_value = None
    with pytest.raises(RecordNotFound, match="Article not found"):
        editorial.inspect_article(NS(id=uuid.uuid4()))
    assert command.phases == ["open", "closed"]


@pytest.mark.parametrize("operation", ["classify", "topic_write"])
@pytest.mark.parametrize("invalid", ["oversized", "json", "schema"])
def test_invalid_operator_documents_fail_before_opening_a_write_scope(
    command, tmp_path, operation, invalid
):
    path = tmp_path / "operator.json"
    path.write_text(" " * 128_001 if invalid == "oversized" else "{" if invalid == "json" else "{}")
    with pytest.raises(OperationConflict if invalid == "oversized" else ValidationError):
        getattr(editorial, operation)(NS(file=path, id=command.article.id))
    assert command.phases == []


def test_classification_document_passes_validated_actor_and_expected_revision_to_domain_command(
    command, monkeypatch, tmp_path
):
    body = {
        "developer_relevance": "relevant",
        "page_kind": "article",
        "language": "en",
        "content_type": "tutorial",
        "content_format": "article",
        "topics": [],
        "tags": [],
        "actor": "editor",
        "note": "Checked source evidence",
        "expected_revision": command.article.editorial_revision,
    }
    path = tmp_path / "classification.json"
    path.write_text(json.dumps(body))
    apply = Mock(return_value=command.article)
    monkeypatch.setattr(editorial, "classify_manually", apply)
    output = editorial.classify(NS(file=path, id=command.article.id))
    session, identifier, parsed = apply.call_args.args
    assert session is command.session and identifier == command.article.id
    assert parsed.model_dump(mode="json") == editorial.ManualClassification(**body).model_dump(
        mode="json"
    )
    assert (
        parsed.expected_revision == command.article.editorial_revision and parsed.actor == "editor"
    )
    assert output["id"] == str(command.article.id) and output["publication_status"] == "unpublished"


@pytest.mark.parametrize("dry_run", [False, True])
def test_editorial_decision_preserves_preview_revision_and_actor_without_implicit_publication(
    command, monkeypatch, dry_run
):
    decision = Mock(return_value=command.article)
    monkeypatch.setattr(editorial, "decide_article", decision)
    output = editorial.decide(
        NS(
            id=command.article.id,
            action="approve",
            by="reviewer",
            note="Read original",
            revision=2,
            dry_run=dry_run,
        )
    )
    parsed = decision.call_args.args[2]
    assert parsed.model_dump() == {
        "action": "approve",
        "actor": "reviewer",
        "note": "Read original",
        "expected_revision": 2,
    }
    assert decision.call_args.kwargs == {"dry_run": dry_run}
    assert output["dry_run"] is dry_run and output["action"] == "approve"
    assert output["article"]["publication_status"] == "unpublished"


def test_review_history_preserves_saved_revision_actor_and_time_without_exposing_transport_state(
    command,
):
    audit = ArticleReview(
        id=uuid.uuid4(),
        article_id=command.article.id,
        action="approve",
        actor="reviewer",
        note="Read original",
        revision=command.article.editorial_revision,
        created_at=NOW,
    )
    command.session.scalars.return_value = [audit]
    assert editorial.history(NS(id=command.article.id, limit=10)) == [
        {
            "id": str(audit.id),
            "action": "approve",
            "actor": "reviewer",
            "note": "Read original",
            "revision": command.article.editorial_revision,
            "created_at": NOW,
        }
    ]


@pytest.mark.parametrize("existing", [False, True])
def test_topic_write_uses_validated_document_and_explicit_update_identity(
    command, monkeypatch, tmp_path, existing
):
    current = topic()
    path = tmp_path / "topic.json"
    path.write_text(
        TopicWrite(name=current.name, slug=current.slug, kind=current.kind).model_dump_json()
    )
    save = Mock(return_value=current)
    monkeypatch.setattr(editorial, "save_topic", save)
    args = NS(file=path, **({"id": current.id} if existing else {}))
    output = editorial.topic_write(args)
    assert save.call_args.args[0] is command.session
    assert save.call_args.args[1].name == current.name
    assert save.call_args.args[2] == (current.id if existing else None)
    assert output["id"] == str(current.id) and output["kind"] == "technology"


def test_topic_listing_and_explicit_relation_keep_canonical_identity_and_public_evidence(
    command, monkeypatch
):
    first, second = topic(), topic("JavaScript")
    command.session.scalars.return_value = [first, second]
    assert [row["id"] for row in editorial.topic_list(NS(limit=2))] == [
        str(first.id),
        str(second.id),
    ]
    relate = Mock()
    monkeypatch.setattr(editorial, "relate_topics", relate)
    body = NS(
        id=first.id,
        related_id=second.id,
        relation="uses_language",
        evidence_url="https://react.dev/learn",
    )
    output = editorial.topic_relate(body)
    parsed = relate.call_args.args[1]
    assert parsed.topic_id == first.id and parsed.related_topic_id == second.id
    assert (
        parsed.relation == "uses_language" and output["evidence_url"] == "https://react.dev/learn"
    )


@pytest.mark.parametrize("with_key", [False, True])
def test_search_setup_grants_reader_search_only_and_never_prints_credentials(
    monkeypatch, capsys, with_key
):
    engine = Mock()
    engine.collection.side_effect = lambda kind: "unit_" + kind
    monkeypatch.setattr(search_engine, "Typesense", Mock(return_value=engine))
    monkeypatch.setattr(search_engine, "KINDS", ("articles", "topics"))
    settings = configuration(search_query_key=SecretStr("private-search-key") if with_key else None)
    monkeypatch.setattr("devfeed_core.config.get_settings", lambda: settings)
    search_commands.setup()
    engine.setup.assert_called_once_with()
    assert engine.request.call_count == int(with_key)
    if with_key:
        assert engine.request.call_args.args == ("POST", "/keys")
        assert engine.request.call_args.kwargs == {
            "data": {
                "description": "DevFeed reader search",
                "actions": ["documents:search"],
                "collections": ["unit_articles", "unit_topics"],
                "value": "private-search-key",
            },
            "allowed": (409,),
        }
    assert json.loads(capsys.readouterr().out) == {"status": "ready"}


def test_search_backfill_reports_persisted_enqueue_count(monkeypatch, capsys):
    factory = Mock()
    enqueue = Mock(return_value=7)
    monkeypatch.setattr(db, "session_factory", lambda: factory)
    monkeypatch.setattr(search_index, "backfill", enqueue)
    search_commands.backfill()
    enqueue.assert_called_once_with(factory)
    assert json.loads(capsys.readouterr().out) == {"queued": 7}


@pytest.mark.parametrize("failed", [False, True])
def test_search_worker_forwards_once_and_propagates_runtime_failure(monkeypatch, failed):
    run = Mock(return_value=int(failed))
    monkeypatch.setattr(indexer, "run_indexer", run)
    if failed:
        with pytest.raises(Exit) as failure:
            search_commands.worker(once=True)
        assert failure.value.exit_code == 1
    else:
        assert search_commands.worker(once=True) is None
    assert run.call_args.kwargs["once"] is True
    assert callable(run.call_args.kwargs["output"])


@pytest.mark.parametrize(
    "error,message",
    [
        (SQLAlchemyError("postgresql://owner:private@db.invalid"), "Database operation failed"),
        (OSError("private-file-location"), "Unable to read the requested file"),
        (UnicodeError("private invalid bytes"), "Unable to read the requested file"),
    ],
)
def test_runtime_dependency_failures_are_actionable_without_echoing_private_input(
    monkeypatch, capsys, error, message
):
    monkeypatch.setattr(runtime, "get_settings", configuration)
    monkeypatch.setattr(runtime, "configure_logging", Mock())
    # Diagnostic logging is a separate boundary; assert the operator error text
    # without inheriting handlers configured by another CLI invocation.
    monkeypatch.setattr(runtime, "logger", Mock())
    monkeypatch.setattr(runtime, "current", lambda: object())
    handler = Mock(side_effect=error)
    assert runtime.execute(NS(command="search", action="setup", execute=handler)) == 1
    output = capsys.readouterr()
    assert output.out == "" and message in output.err
    assert "private" not in output.err and "Traceback" not in output.err


@pytest.fixture
def operation(monkeypatch):
    article, values = facts()
    article.origins[0].source.feed_url = "https://publisher.example/feed"
    article.origins[0].source.enabled = True
    article.origins[0].entry_key = str(article.id)
    article.origins[0].original_url = article.canonical_url
    original = values["analysis"]
    original.created_at = ANALYSIS_NOW - timedelta(minutes=10)
    original.available_at = ANALYSIS_NOW - timedelta(minutes=9)
    original.finished_at = ANALYSIS_NOW - timedelta(minutes=3)
    queued = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=article.id,
        status="queued",
        attempts=0,
        input_hash=original.input_hash,
        input_snapshot=copy.deepcopy(original.input_snapshot),
        catalog_hash=original.catalog_hash,
        catalog_snapshot=copy.deepcopy(original.catalog_snapshot),
        editorial_revision=article.editorial_revision,
        prompt_version=original.prompt_version,
        result={},
        created_at=ANALYSIS_NOW,
        available_at=ANALYSIS_NOW + timedelta(minutes=2),
        model="test-model",
        dispatched_at=None,
    )
    previous = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=article.id,
        status="failed",
        attempts=3,
        input_hash=original.input_hash,
        input_snapshot=copy.deepcopy(original.input_snapshot),
        catalog_hash=original.catalog_hash,
        catalog_snapshot=copy.deepcopy(original.catalog_snapshot),
        editorial_revision=article.editorial_revision,
        prompt_version=original.prompt_version,
        result={},
        created_at=ANALYSIS_NOW - timedelta(minutes=10),
        available_at=ANALYSIS_NOW - timedelta(minutes=5),
        finished_at=ANALYSIS_NOW - timedelta(minutes=3),
        error="provider_unavailable",
        model="test-model",
    )
    session = Mock()
    session.get.return_value = previous
    events = []

    @contextmanager
    def scope():
        events.append("enter")
        try:
            yield session
        finally:
            events.append("exit")

    factory = Mock(side_effect=scope)
    factory.begin.side_effect = scope
    monkeypatch.setattr(editorial, "session_factory", lambda: factory)
    monkeypatch.setattr(editorial, "utcnow", lambda: ANALYSIS_NOW)
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    requester = Mock(return_value=queued)
    monkeypatch.setattr(editorial, "request_analysis", requester)
    return SimpleNamespace(
        article=article,
        completed=original,
        queued=queued,
        previous=previous,
        session=session,
        factory=factory,
        events=events,
        requester=requester,
    )


def existing_job(operation, status):
    if status is None:
        return None
    if status == "succeeded":
        return operation.completed
    if status == "failed":
        return operation.previous
    job = operation.queued
    if status == "running":
        job.status, job.attempts = "running", 1
        job.created_at = ANALYSIS_NOW - timedelta(minutes=2)
        job.available_at = ANALYSIS_NOW - timedelta(minutes=1)
        job.lease_token, job.lease_until = uuid.uuid4(), ANALYSIS_NOW + timedelta(minutes=3)
    return job


@pytest.mark.parametrize("action", ["analyze", "analysis-retry", "analysis-backfill"])
def test_disabled_analysis_fails_before_opening_a_database_scope(operation, monkeypatch, action):
    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    handler = editorial.analysis_backfill if action == "analysis-backfill" else editorial.analyze
    with pytest.raises(OperationConflict, match="Configure"):
        handler(SimpleNamespace(action=action, id=operation.article.id))
    assert operation.events == []
    operation.requester.assert_not_called()


@pytest.mark.parametrize("retry", [False, True])
def test_analysis_admission_targets_the_article_and_does_not_dispatch_implicitly(operation, retry):
    args = SimpleNamespace(
        action="analysis-retry" if retry else "analyze",
        id=operation.previous.id if retry else operation.article.id,
        force=False,
    )
    result = editorial.analyze(args)
    operation.requester.assert_called_once_with(operation.session, operation.article.id)
    assert result["id"] == operation.queued.id and result["article_id"] == operation.article.id
    assert result["status"] == "queued" and operation.events == ["enter", "exit"]


@pytest.mark.parametrize("status", [None, "queued", "running", "succeeded"])
def test_retry_admission_rejects_missing_or_nonfailed_jobs_without_requesting_work(
    operation, status
):
    old = existing_job(operation, status)
    operation.session.get.return_value = old
    expected = RecordNotFound if status is None else OperationConflict
    with pytest.raises(expected):
        editorial.analyze(
            SimpleNamespace(
                action="analysis-retry", id=old.id if old is not None else uuid.uuid4(), force=False
            )
        )
    operation.requester.assert_not_called()


def test_explicit_force_dispatch_starts_after_the_admission_scope_has_closed(
    operation, monkeypatch
):
    def dispatch(identifier):
        assert operation.events == ["enter", "exit"]
        assert identifier == operation.queued.id
        return {"id": identifier, "dispatched": True}

    monkeypatch.setattr(editorial, "dispatch_analysis", dispatch)
    result = editorial.analyze(
        SimpleNamespace(action="analyze", id=operation.article.id, force=True)
    )
    assert result == {"id": operation.queued.id, "dispatched": True}


@pytest.mark.parametrize("dispatch", [False, True])
def test_backfill_preserves_scan_cursor_and_dispatches_only_admitted_jobs_after_scope_close(
    operation, monkeypatch, dispatch
):
    operation.article.review_status = "pending"
    after = uuid.UUID(int=1)
    next_after = str(operation.article.id)
    scan = Mock(return_value=([operation.queued], 10, next_after))
    monkeypatch.setattr(editorial, "backfill_analyses", scan)

    def deliver(identifier):
        assert operation.events == ["enter", "exit"]
        return {"id": identifier, "dispatched": True}

    delivery = Mock(side_effect=deliver)
    monkeypatch.setattr(editorial, "dispatch_analysis", delivery)
    args = SimpleNamespace(limit=10, after=after, force=True, dispatch=dispatch)
    result = editorial.analysis_backfill(args)
    scan.assert_called_once_with(operation.session, 10, after=after, force=True)
    assert result["scanned"] == 10 and result["queued"] == 1 and result["next_after"] == next_after
    assert result["jobs"][0]["id"] == operation.queued.id
    if dispatch:
        delivery.assert_called_once_with(operation.queued.id)
        assert result["jobs"][0]["dispatched"]
    else:
        delivery.assert_not_called()


def test_routine_job_projection_omits_private_source_and_catalog_snapshots(operation):
    job = operation.queued
    result = editorial.job_view(job)
    assert result["input_hash"] == job.input_hash and result["prompt_version"] == job.prompt_version
    assert {"input_snapshot", "catalog_snapshot", "usage", "lease_token"}.isdisjoint(result)
    assert result["result"] == {} and result["id"] == job.id


@pytest.mark.parametrize("status", [None, "running", "succeeded", "failed"])
def test_dispatch_requires_an_existing_queued_job_before_touching_redis(
    operation, monkeypatch, status
):
    job = existing_job(operation, status)
    operation.session.scalar.return_value = job
    broker = Mock(side_effect=AssertionError("Redis must not be touched"))
    monkeypatch.setattr(editorial, "get_queue", broker)
    with pytest.raises(RecordNotFound if status is None else OperationConflict):
        editorial.dispatch_analysis(job.id if job is not None else uuid.uuid4())
    broker.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_dispatch_closes_redis_after_success_or_failure_and_uses_the_analysis_lane(
    operation, monkeypatch, fails
):
    job = operation.queued
    operation.session.scalar.return_value = operation.session.get.return_value = job
    queue = Mock()
    monkeypatch.setattr(editorial, "get_queue", Mock(return_value=queue))

    def publish(factory, selected, batch, now, **options):
        assert operation.events == ["enter", "exit"]
        assert factory is operation.factory and selected is queue
        assert (batch, now, options) == (1, ANALYSIS_NOW, {"job_id": job.id, "kind": "analysis"})
        assert job.available_at == ANALYSIS_NOW and job.dispatched_at is None
        if fails:
            raise RuntimeError("broker unavailable")
        job.dispatched_at = now
        return 1

    monkeypatch.setattr(editorial, "dispatch_jobs", publish)
    if fails:
        with pytest.raises(RuntimeError, match="broker unavailable"):
            editorial.dispatch_analysis(job.id)
    else:
        result = editorial.dispatch_analysis(job.id)
        assert result["id"] == job.id and result["dispatched_at"] == ANALYSIS_NOW
    assert job.available_at == ANALYSIS_NOW and job.dispatched_at == (
        None if fails else ANALYSIS_NOW
    )
    queue.connection.close.assert_called_once_with()
    editorial.get_queue.assert_called_once_with("article-analysis")
