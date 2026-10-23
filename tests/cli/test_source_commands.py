"""Source commands validate input and preserve explicit processing and editorial intent."""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_cli import commands
from devfeed_core import services
from devfeed_core.models import IngestionJob, SourceEnrichmentJob, SourceReview, Tag
from devfeed_core.source_enrichment import source_identity
from devfeed_core.source_profiles import PROFILE_FIELDS

from tests.support.source_readiness import NOW, job, source


@pytest.fixture
def command_store(monkeypatch):
    session, phases = Mock(), []

    @contextmanager
    def factory():
        phases.append("open")
        try:
            yield session
        finally:
            phases.append("closed")

    factory.begin = factory
    provider = Mock(return_value=factory)
    monkeypatch.setattr(commands, "session_factory", provider)
    return NS(session=session, factory=factory, provider=provider, phases=phases)


def publisher(*, approval="approved", **changes):
    return source(
        approval=approval,
        publication_policy="manual",
        publication_policy_revision=1,
        submission_channel="cli",
        consecutive_failures=0,
        polling_mode="fixed",
        polling_state={},
        poll_interval_seconds=3600,
        next_fetch_at=NOW + timedelta(hours=1),
        **changes,
    )


def ingest(current, **changes):
    return job(
        IngestionJob,
        source_id=current.id,
        dispatched_at=None,
        http_status=None,
        automatic=False,
        new_source_entries=0,
        entries_seen=0,
        articles_created=0,
        entries_skipped=0,
        **changes,
    )


def enrichment(current, **changes):
    return job(
        SourceEnrichmentJob,
        source_id=current.id,
        operation="profile",
        result={"input": source_identity(current)},
        changed_fields=[],
        dispatched_at=None,
        **changes,
    )


def test_submitter_profile_without_attribution_fails_before_preflight_or_database(
    monkeypatch, command_store
):
    validate = Mock()
    monkeypatch.setattr(services, "validate_source", validate)
    args = NS(
        url="https://publisher.example/feed",
        source_type="publisher",
        name=None,
        poll_interval=3600,
        polling_mode="fixed",
        disabled=False,
        submitted_by=None,
        submitter_url="https://operator.example/profile",
        **dict.fromkeys(PROFILE_FIELDS),
    )
    with pytest.raises(commands.InputError, match="requires --submitted-by"):
        commands.source_add(args)
    validate.assert_not_called()
    command_store.provider.assert_not_called()


@pytest.mark.parametrize("oversized", [True, False])
def test_import_rejects_byte_size_and_feed_count_before_preflight_or_transaction(
    tmp_path, monkeypatch, command_store, oversized
):
    path = tmp_path / "feeds.txt"
    path.write_text(
        "é" * 500001
        if oversized
        else "\n".join(f"https://publisher{index}.example/feed" for index in range(1001))
    )
    validate = Mock()
    monkeypatch.setattr(services, "validate_source", validate)
    with pytest.raises(
        commands.InputError, match="exceeds 1 MB" if oversized else "limited to 1000"
    ):
        commands.source_import(NS(file=str(path), source_type="publisher", poll_interval=3600))
    validate.assert_not_called()
    command_store.provider.assert_not_called()


def test_source_list_serializes_only_preselected_page_without_internal_processing_receipts(
    command_store,
):
    current = publisher(approval="pending")
    current.processing_request = {
        "kind": "reopen_review",
        "id": str(uuid.uuid4()),
        "actor": {"subject": "internal-owner"},
        "requested_at": (NOW - timedelta(days=1)).isoformat(),
        "manual_review": True,
        "required": ["profile", "branding"],
    }
    current.reviewed_at, current.reviewed_by = NOW - timedelta(hours=36), "editor"
    current.review_note = "Previous rejection; a new inspection was explicitly requested."
    command_store.session.scalars.return_value = [current]
    [output] = commands.source_list(
        NS(source_type="publisher", enabled=False, approval_status="pending", limit=1, offset=3)
    )
    assert output["id"] == str(current.id) and output["approval_status"] == "pending"
    assert output["publication_policy"] == "manual"
    assert "processing_request" not in output and "internal-owner" not in str(output)
    assert command_store.phases == ["open", "closed"]


@pytest.mark.parametrize("found", [False, True])
def test_source_show_handles_absent_identity_without_fabricating_empty_output(command_store, found):
    current = publisher()
    command_store.session.get.return_value = current if found else None
    if found:
        assert commands.source_show(NS(id=current.id))["feed_url"] == current.feed_url
    else:
        with pytest.raises(services.RecordNotFound, match="Source not found"):
            commands.source_show(NS(id=current.id))


def test_source_patch_preserves_explicit_clear_and_omits_unmentioned_fields(
    monkeypatch, command_store
):
    current, update = publisher(description=None), Mock()
    update.return_value = current
    monkeypatch.setattr(services, "update_source", update)
    output = commands.source_update(NS(id=current.id, description=None, action="update"))
    session, identifier, body = update.call_args.args
    assert session is command_store.session and identifier == current.id
    assert body.model_dump(exclude_unset=True) == {"description": None}
    assert output["description"] is None and output["feed_url"] == current.feed_url


def test_source_review_uses_explicit_actor_note_and_validated_decision(monkeypatch, command_store):
    current = publisher(
        reviewed_at=NOW, reviewed_by="operator", review_note="Reviewed engineering coverage."
    )
    review = Mock(return_value=current)
    monkeypatch.setattr(services, "review_source", review)
    output = commands.source_review(
        NS(id=current.id, decision="approved", by="operator", note=current.review_note)
    )
    session, identifier, body = review.call_args.args
    assert session is command_store.session and identifier == current.id
    assert body.model_dump() == {
        "decision": "approved",
        "actor": "operator",
        "note": current.review_note,
    }
    assert output["reviewed_by"] == "operator" and output["approval_status"] == "approved"


@pytest.mark.parametrize("found", [False, True])
def test_review_history_preserves_audit_fields_and_requires_an_existing_source(
    command_store, found
):
    current = publisher()
    command_store.session.get.return_value = current if found else None
    review = SourceReview(
        id=uuid.uuid4(),
        source_id=current.id,
        decision="approved",
        actor="operator",
        note="Inspected publication scope.",
        created_at=NOW,
    )
    command_store.session.scalars.return_value = [review]
    if not found:
        with pytest.raises(services.RecordNotFound, match="Source not found"):
            commands.source_review_history(NS(id=current.id, offset=0, limit=10))
        command_store.session.scalars.assert_not_called()
    else:
        [output] = commands.source_review_history(NS(id=current.id, offset=0, limit=10))
        assert output["actor"] == "operator" and output["decision"] == "approved"
        assert datetime.fromisoformat(output["created_at"]) == NOW


@pytest.mark.parametrize("kind", ["ingestion", "source-enrichment"])
def test_force_submission_dispatches_typed_job_only_after_command_scope_ends(
    monkeypatch, command_store, kind
):
    current = publisher()
    queued = ingest(current) if kind == "ingestion" else enrichment(current)
    command = Mock(return_value=queued)
    monkeypatch.setattr(services, "fetch_source", command)
    monkeypatch.setattr(commands, "request_enrichment", command)

    def dispatch(identifier, **options):
        assert command_store.phases == ["open", "closed"]
        assert identifier == queued.id
        assert options == ({} if kind == "ingestion" else {"kind": kind})
        return {"id": str(identifier), "status": "queued", "dispatched": True}

    monkeypatch.setattr(commands.dispatch, "dispatch_now", dispatch)
    result = (commands.source_fetch if kind == "ingestion" else commands.source_enrich)(
        NS(id=current.id, force=True)
    )
    assert result["dispatched"] and result["id"] == str(queued.id)
    command.assert_called_once_with(command_store.session, current.id)


@pytest.mark.parametrize("kind", ["ingestion", "source-enrichment"])
def test_job_pages_serialize_scoped_runs_without_exposing_leases_or_private_profiles(
    command_store, kind
):
    current = publisher()
    queued = ingest(current) if kind == "ingestion" else enrichment(current)
    command_store.session.scalars.return_value = [queued]
    result = (commands.job_list if kind == "ingestion" else commands.source_enrichment_jobs)(
        NS(source_id=current.id, status="queued", offset=4, limit=1)
    )
    assert len(result) == 1 and result[0]["id"] == str(queued.id)
    assert result[0]["source_id"] == str(current.id) and result[0]["status"] == "queued"
    assert "lease_token" not in result[0] and "result" not in result[0]


@pytest.mark.parametrize("found", [False, True])
def test_ingestion_job_show_requires_a_current_record_and_preserves_run_identity(
    command_store, found
):
    current, identifier = publisher(), uuid.uuid4()
    queued = ingest(current, id=identifier)
    command_store.session.get.return_value = queued if found else None
    if found:
        assert commands.job_show(NS(id=identifier))["id"] == str(identifier)
    else:
        with pytest.raises(services.RecordNotFound, match="Job not found"):
            commands.job_show(NS(id=identifier))


def test_ingestion_retry_returns_new_domain_run_as_typed_result(monkeypatch, command_store):
    current = publisher()
    queued, previous = ingest(current), uuid.uuid4()
    retry = Mock(return_value=queued)
    monkeypatch.setattr(services, "retry_job", retry)
    output = commands.job_retry(NS(id=previous))
    assert output["id"] == str(queued.id) and output["id"] != str(previous)
    retry.assert_called_once_with(command_store.session, previous)


@pytest.mark.parametrize("action", ["add", "update"])
def test_tag_writes_keep_explicit_topic_unlink_separate_from_automatic_matching(
    monkeypatch, command_store, action
):
    tag = Tag(
        id=uuid.uuid4(),
        name="PostgreSQL",
        slug="postgresql",
        aliases=[],
        topic_id=None,
        auto_link_topic=False,
        topic_match_status="unmatched",
        topic_match_checked_at=NOW,
    )
    create, update = Mock(return_value=tag), Mock(return_value=tag)
    monkeypatch.setattr(services, "create_tag", create)
    monkeypatch.setattr(services, "update_tag", update)
    args = NS(action=action, topic_id=None, auto_link_topic=False)
    if action == "add":
        args.name, args.slug = tag.name, tag.slug
    else:
        args.id = tag.id
    output = commands.taxonomy_write(args)
    called = create if action == "add" else update
    body = called.call_args.args[-1]
    assert body.topic_id is None and body.auto_link_topic is False
    assert {"topic_id", "auto_link_topic"} <= body.model_fields_set
    assert output["id"] == str(tag.id) and output["topic_id"] is None


@pytest.mark.parametrize("queue", ["all", "article-analysis"])
def test_worker_flags_forward_only_an_explicit_queue(monkeypatch, queue):
    run = Mock()
    monkeypatch.setattr(commands.worker, "run", run)
    commands.run_worker(NS(queue=queue, burst=True, name="maintenance", max_jobs=3))
    run.assert_called_once_with(
        burst=True,
        name="maintenance",
        max_jobs=3,
        **({"queue_name": queue} if queue != "all" else {}),
    )


@pytest.mark.parametrize("once", [False, True])
def test_scheduler_once_exits_after_one_tick_instead_of_starting_the_loop(monkeypatch, once):
    tick, run = Mock(return_value={"dispatched": 2}), Mock()
    monkeypatch.setattr(commands.scheduler, "tick", tick)
    monkeypatch.setattr(commands.scheduler, "run", run)
    result = commands.run_scheduler(NS(once=once))
    assert result == ({"dispatched": 2} if once else None)
    assert tick.call_count == int(once) and run.call_count == int(not once)


@pytest.mark.parametrize("action", ["upgrade", "check", "current"])
def test_migration_finds_configuration_in_ancestor_and_forwards_supported_action(
    tmp_path, monkeypatch, action
):
    path = tmp_path / "alembic.ini"
    path.write_text("[alembic]\nscript_location=migrations\n")
    nested = tmp_path / "nested" / "directory"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    operation = Mock()
    monkeypatch.setattr(commands.command, action, operation)
    commands.migrate(NS(config=None, action=action))
    config = operation.call_args.args[0]
    assert config.config_file_name == str(path.resolve())
    assert config.get_main_option("script_location") == "migrations"
    assert operation.call_args.args[1:] == (("head",) if action == "upgrade" else ())


def test_missing_explicit_migration_config_fails_before_alembic_runs(tmp_path, monkeypatch):
    upgrade = Mock()
    monkeypatch.setattr(commands.command, "upgrade", upgrade)
    with pytest.raises(commands.InputError, match="Cannot find alembic.ini"):
        commands.migrate(NS(config=tmp_path / "absent.ini", action="upgrade"))
    upgrade.assert_not_called()
