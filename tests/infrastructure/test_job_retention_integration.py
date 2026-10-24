"""Durable retention pages preserve recoverable evidence under concurrent work."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from devfeed_core import job_retention
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    JobPayloadRetention,
    Topic,
    TopicAnalysisJob,
    utcnow,
)
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError, InternalError

from tests.support.paths import REPO_ROOT
from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import relationship_proposal

pytestmark = pytest.mark.integration


@pytest.fixture
def retention(monkeypatch):
    clock = SimpleNamespace(now=utcnow() + timedelta(seconds=2))
    monkeypatch.setattr(job_retention, "utcnow", lambda: clock.now)
    monkeypatch.setattr(
        job_retention,
        "get_settings",
        lambda: configuration(job_payload_scan_batch_size=5, job_payload_prune_batch_size=2),
    )
    return clock


def history(session, clock, *, kind="article", statuses=("succeeded", "succeeded"), **values):
    identifier = uuid.uuid4()
    if kind == "article":
        parent = Article(
            id=identifier,
            canonical_url=f"https://retention.example/{identifier}",
            url_hash=identifier.hex,
            title="Historical analysis",
            review_status="approved",
        )
        model, target = ArticleAnalysisJob, {"article_id": identifier}
    else:
        parent = Topic(
            name=str(identifier), slug=str(identifier), kind="technology", status="active"
        )
        session.add(parent)
        session.flush()
        model, target = TopicAnalysisJob, {"topic_id": parent.id}
    session.add(parent)
    session.flush()
    old = clock.now - timedelta(days=40)
    jobs = [
        model(
            **target,
            status=status,
            input_hash="a" * 64,
            input_snapshot={"private_evidence": index},
            prompt_version="historical-analysis-v1",
            created_at=old + timedelta(seconds=index),
            finished_at=old + timedelta(seconds=index),
            **({"requested_by": {}} if kind != "article" else {}),
            **values,
        )
        for index, status in enumerate(statuses)
    ]
    session.add_all(jobs)
    session.flush()
    return model, parent, jobs


def due(database, clock, kind="article"):
    with database.begin() as session:
        state = session.get(JobPayloadRetention, kind)
        state.next_run_at = clock.now


def test_empty_sweep_persists_cadence_and_ordinary_ticks_do_not_read_history(database, retention):
    assert job_retention.prune_job_payloads(database) == 0
    with database() as session:
        states = session.scalars(select(JobPayloadRetention)).all()
        assert len(states) == 2
        for state in states:
            assert state.next_run_at == retention.now + timedelta(hours=1)
            assert state.cutoff is state.cursor_finished_at is state.cursor_id is None
    statements = []
    engine = database.kw["bind"]

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert job_retention.prune_job_payloads(database) == 0
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements
    assert not any("analysis_jobs" in statement for statement in statements)
    assert not any(statement.lstrip().upper().startswith("UPDATE") for statement in statements)


@pytest.mark.parametrize("kind", ["article", "topic"])
@pytest.mark.parametrize("newer_status", ["failed", "queued", "running"])
def test_newer_unsuccessful_work_cannot_remove_the_last_successful_evidence(
    database, retention, kind, newer_status
):
    with database.begin() as session:
        model, _, jobs = history(
            session, retention, kind=kind, statuses=("succeeded", newer_status)
        )
        if newer_status == "running":
            jobs[1].lease_token = uuid.uuid4()
            jobs[1].lease_until = retention.now + timedelta(minutes=5)
        identifiers = [job.id for job in jobs]
    assert job_retention.prune_job_payloads(database) == 0
    with database() as session:
        assert all(session.get(model, identifier).input_snapshot for identifier in identifiers)


@pytest.mark.parametrize("kind", ["article", "topic"])
def test_newer_already_pruned_success_does_not_discard_the_last_retained_success(
    database, retention, kind
):
    with database.begin() as session:
        model, _, jobs = history(session, retention, kind=kind)
        jobs[1].inputs_pruned_at = retention.now - timedelta(days=1)
        jobs[1].input_snapshot = {}
        oldest = jobs[0].id
    assert job_retention.prune_job_payloads(database) == 0
    with database() as session:
        assert session.get(model, oldest).input_snapshot


@pytest.mark.parametrize("kind", ["article", "topic"])
@pytest.mark.parametrize("lease_field", ["lease_token", "lease_until"])
def test_unreleased_lease_bookkeeping_protects_a_successful_row(
    database, retention, kind, lease_field
):
    with database.begin() as session:
        model, _, jobs = history(session, retention, kind=kind)
        setattr(
            jobs[0],
            lease_field,
            uuid.uuid4() if lease_field == "lease_token" else retention.now + timedelta(minutes=5),
        )
        oldest = jobs[0].id
    assert job_retention.prune_job_payloads(database) == 0
    with database() as session:
        assert session.get(model, oldest).input_snapshot


def test_recent_success_supersedes_old_evidence_without_becoming_age_eligible(database, retention):
    with database.begin() as session:
        _, _, jobs = history(session, retention, statuses=("succeeded",) * 3)
        jobs[1].finished_at = retention.now - timedelta(days=29)
        jobs[2].created_at = jobs[2].finished_at = retention.now
        identifiers = [job.id for job in jobs]
    assert job_retention.prune_job_payloads(database) == 1
    with database() as session:
        rows = [session.get(ArticleAnalysisJob, identifier) for identifier in identifiers]
        assert rows[0].input_snapshot == {}
        assert rows[1].input_snapshot and rows[2].input_snapshot


def test_pending_relationship_review_preserves_the_historical_evidence(database, retention):
    with database.begin() as session:
        _, focal, jobs = history(session, retention, kind="topic")
        peer = Topic(name="Peer", slug="peer", kind="language", status="active")
        session.add(peer)
        session.flush()
        session.add(relationship_proposal(focal, peer, jobs[0]))
        oldest = jobs[0].id
    assert job_retention.prune_job_payloads(database) == 0
    with database() as session:
        assert session.get(TopicAnalysisJob, oldest).input_snapshot


def test_checkpoint_lock_excludes_another_process_and_release_restores_progress(
    database, retention
):
    with database.begin() as session:
        _, _, jobs = history(session, retention)
        oldest = jobs[0].id
    with database.begin() as owner:
        owner.scalar(
            select(JobPayloadRetention)
            .where(JobPayloadRetention.kind == "article")
            .with_for_update()
        )
        assert job_retention.prune_job_payloads(database) == 0
        with database() as observer:
            assert observer.get(ArticleAnalysisJob, oldest).input_snapshot
            assert observer.get(JobPayloadRetention, "article").cutoff is None
    assert job_retention.prune_job_payloads(database) == 1


def test_interrupted_page_rolls_back_payload_and_checkpoint_before_restart(
    database, retention, monkeypatch
):
    with database.begin() as session:
        _, _, jobs = history(session, retention, statuses=("succeeded",) * 4)
        identifiers = [job.id for job in jobs]
    redact = job_retention._prune_payload

    def interrupted(job, now):
        redact(job, now)
        raise RuntimeError("Scheduler interrupted before checkpoint commit")

    monkeypatch.setattr(job_retention, "_prune_payload", interrupted)
    with pytest.raises(RuntimeError, match="Scheduler interrupted"):
        job_retention.prune_job_payloads(database)
    with database() as session:
        assert all(
            session.get(ArticleAnalysisJob, identifier).input_snapshot for identifier in identifiers
        )
        state = session.get(JobPayloadRetention, "article")
        assert state.cutoff is state.cursor_finished_at is state.cursor_id is None
        assert state.next_run_at <= retention.now
    monkeypatch.setattr(job_retention, "_prune_payload", redact)
    assert job_retention.prune_job_payloads(database) == 2
    # A restarted scheduler resumes a committed page rather than redoing it.
    with database() as session:
        state = session.get(JobPayloadRetention, "article")
        checkpoint = (state.cursor_finished_at, state.cursor_id)
        assert state.cutoff == retention.now - timedelta(days=30)
        assert checkpoint == (
            session.get(ArticleAnalysisJob, identifiers[1]).finished_at,
            identifiers[1],
        )
    assert job_retention.prune_job_payloads(database) == 0
    retention.now += timedelta(seconds=31)
    assert job_retention.prune_job_payloads(database) == 1


def test_transaction_timeout_rolls_back_flushed_payload_and_checkpoint_then_recovers(
    database, retention, caplog
):
    with database.begin() as session:
        _, _, jobs = history(session, retention)
        oldest = jobs[0].id
    flushed = []

    def interrupt_after_flush(session, _flush_context):
        progress = session.info.get("job_payload_retention_page")
        if progress and progress["kind"] == "article" and progress["pruned"]:
            connection = session.connection()
            # Both writes have reached PostgreSQL, but the transaction has not
            # committed. The server deadline must undo the complete page.
            persisted_in_transaction = connection.execute(
                text(
                    "SELECT j.inputs_pruned_at IS NOT NULL, r.next_run_at > :now "
                    "FROM article_analysis_jobs j CROSS JOIN job_payload_retention r "
                    "WHERE j.id = :id AND r.kind = 'article'"
                ),
                {"id": oldest, "now": retention.now},
            ).one()
            flushed.append(tuple(persisted_in_transaction))
            # PostgreSQL leaves an armed timer unchanged when replacing a
            # nonzero limit; disarm the page's 5s timer before shortening it.
            connection.execute(text("SET LOCAL transaction_timeout = '0'"))
            connection.execute(text("SET LOCAL transaction_timeout = '50ms'"))
            connection.execute(text("SELECT pg_sleep(0.2)"))

    event.listen(database.class_, "after_flush_postexec", interrupt_after_flush)
    try:
        with pytest.raises(InternalError) as failure:
            job_retention.prune_job_payloads(database)
    finally:
        event.remove(database.class_, "after_flush_postexec", interrupt_after_flush)
    assert failure.value.orig.sqlstate == "25P04"
    assert flushed == [(True, True)]
    assert not any(record.msg == "job_payload_retention_page" for record in caplog.records)
    with database() as session:
        assert session.get(ArticleAnalysisJob, oldest).input_snapshot
        state = session.get(JobPayloadRetention, "article")
        assert state.cutoff is state.cursor_finished_at is state.cursor_id is None
        assert state.next_run_at <= retention.now
    assert job_retention.prune_job_payloads(database) == 1


def test_completion_committed_after_candidate_selection_is_rechecked(database, retention):
    with database.begin() as session:
        _, _, jobs = history(session, retention, statuses=("succeeded", "running"))
        oldest, newer = [job.id for job in jobs]
    writer = database()
    current = writer.get(ArticleAnalysisJob, newer)
    current.status, current.finished_at = "succeeded", retention.now
    writer.flush()
    engine = database.kw["bind"]
    completed = []

    def finish_after_page_read(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            not completed
            and "FROM article_analysis_jobs" in statement
            and "LIMIT" in statement
            and "newer" not in statement
            and "FOR UPDATE" not in statement
        ):
            completed.append(True)
            writer.commit()

    event.listen(engine, "after_cursor_execute", finish_after_page_read)
    try:
        assert job_retention.prune_job_payloads(database) == 1
    finally:
        event.remove(engine, "after_cursor_execute", finish_after_page_read)
        writer.close()
    assert completed
    with database() as session:
        assert session.get(ArticleAnalysisJob, oldest).input_snapshot == {}
        assert session.get(ArticleAnalysisJob, newer).input_snapshot


def test_protected_prefix_and_large_backlog_converge_in_bounded_persisted_pages(
    database, retention
):
    with database.begin() as session:
        protected = []
        for _ in range(7):
            _, _, jobs = history(session, retention, statuses=("succeeded",))
            jobs[0].finished_at -= timedelta(days=1)
            protected.append(jobs[0].id)
        latest = []
        for _ in range(10):
            _, _, jobs = history(session, retention, statuses=("succeeded",) * 3)
            latest.append(jobs[-1].id)
    total, checkpoints = 0, []
    initial_cutoff = retention.now - timedelta(days=30)
    for _ in range(30):
        pruned = job_retention.prune_job_payloads(database)
        assert 0 <= pruned <= 2
        total += pruned
        with database() as session:
            state = session.get(JobPayloadRetention, "article")
            if state.cutoff is None:
                assert state.next_run_at == retention.now + timedelta(hours=1)
                break
            assert state.cutoff == initial_cutoff
            checkpoints.append((state.cursor_finished_at, state.cursor_id))
        retention.now += timedelta(seconds=31)
    else:
        pytest.fail("Bounded pages did not converge through protected history and backlog")
    assert total == 20
    assert checkpoints == sorted(set(checkpoints))
    with database() as session:
        assert all(
            session.get(ArticleAnalysisJob, identifier).input_snapshot
            for identifier in protected + latest
        )
        assert (
            len(
                session.scalars(
                    select(ArticleAnalysisJob).where(
                        ArticleAnalysisJob.inputs_pruned_at.is_not(None)
                    )
                ).all()
            )
            == 20
        )


def test_locked_parent_is_revisited_on_the_next_sweep(database, retention):
    with database.begin() as session:
        _, parent, jobs = history(session, retention)
        parent_id, oldest = parent.id, jobs[0].id
    with database.begin() as editor:
        editor.scalar(select(Article).where(Article.id == parent_id).with_for_update())
        assert job_retention.prune_job_payloads(database) == 0
    due(database, retention)
    assert job_retention.prune_job_payloads(database) == 1
    with database() as session:
        assert session.get(ArticleAnalysisJob, oldest).input_snapshot == {}


def test_migration_roundtrip_restores_cadence_rows_and_valid_partial_indexes(database):
    config = Config(str(REPO_ROOT / "alembic.ini"))
    try:
        command.downgrade(config, "0034")
        with database() as session:
            assert session.scalar(text("SELECT to_regclass('job_payload_retention')")) is None
        command.upgrade(config, "head")
        with database() as session:
            states = session.scalars(
                select(JobPayloadRetention).order_by(JobPayloadRetention.kind)
            ).all()
            assert [state.kind for state in states] == ["article", "topic"]
            assert all(state.cutoff is None and state.cursor_id is None for state in states)
            indexes = session.execute(
                text(
                    "SELECT c.relname, i.indisvalid, pg_get_expr(i.indpred, i.indrelid) "
                    "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname LIKE 'ix_%_retained_success' ORDER BY c.relname"
                )
            ).all()
            assert len(indexes) == 3
            assert all(
                valid and "succeeded" in predicate and "inputs_pruned_at IS NULL" in predicate
                for _, valid, predicate in indexes
            )
    finally:
        command.upgrade(config, "head")


def test_migration_retry_repairs_an_invalid_concurrent_index_and_preserves_checkpoint(
    database, retention
):
    config = Config(str(REPO_ROOT / "alembic.ini"))
    index_name = "ix_article_analysis_jobs_article_id_retained_success"
    engine = database.kw["bind"]
    try:
        command.downgrade(config, "0034")
        # Simulate DDL committed by an interrupted concurrent-index migration.
        JobPayloadRetention.__table__.create(engine)
        checkpoint_id = uuid.uuid4()
        with database.begin() as session:
            history(session, retention)
            session.add(
                JobPayloadRetention(
                    kind="article",
                    next_run_at=retention.now + timedelta(minutes=5),
                    cutoff=retention.now - timedelta(days=30),
                    cursor_finished_at=retention.now - timedelta(days=40),
                    cursor_id=checkpoint_id,
                )
            )
        # A failed concurrent build leaves the name present but indisvalid=false.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        f"CREATE UNIQUE INDEX CONCURRENTLY {index_name} "
                        "ON article_analysis_jobs (article_id) "
                        "WHERE status = 'succeeded' AND inputs_pruned_at IS NULL"
                    )
                )
            assert (
                connection.scalar(
                    text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"),
                    {"name": index_name},
                )
                is False
            )
        command.upgrade(config, "head")
        with database() as session:
            state = session.get(JobPayloadRetention, "article")
            assert state.cursor_id == checkpoint_id
            assert state.next_run_at == retention.now + timedelta(minutes=5)
            assert session.get(JobPayloadRetention, "topic") is not None
            assert (
                session.scalar(
                    text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"),
                    {"name": index_name},
                )
                is True
            )
    finally:
        command.upgrade(config, "head")
