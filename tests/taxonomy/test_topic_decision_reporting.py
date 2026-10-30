"""Historical call-fact migration contracts use their own pre-retirement database."""

import copy
import importlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core import analytics_producer
from devfeed_core.models import (
    Article,
    TopicDecisionRun,
    TopicProposal,
)
from sqlalchemy import select, text

from tests.support import legacy_migrations

migration_database = legacy_migrations.migration_database

pytestmark = pytest.mark.integration
START = datetime(2026, 10, 20, tzinfo=UTC)
END = START + timedelta(days=2)


@pytest.fixture
def database(migration_database, monkeypatch):
    # Historical ORM writes must not start a native analytics publisher, just as
    # the ordinary source fixture suppresses delivery for its own test commits.
    monkeypatch.setattr(analytics_producer, "submit", lambda changes: None)
    return migration_database


def call(stage="draft", started_at=START, tokens=10, **changes):
    return {
        "stage": stage,
        "started_at": started_at.isoformat() if started_at is not None else None,
        "tokens": tokens if isinstance(tokens, dict) else {"totalTokens": tokens},
        "charged_tokens": 0 if isinstance(tokens, dict) else tokens,
        "escalated": False,
        **changes,
    }


def add_run(session, calls=(), *, status="pending", reviewed_at=None, run_status="active", **kw):
    identifier = uuid.uuid4()
    proposal = TopicProposal(
        id=identifier,
        batch_id=uuid.uuid4(),
        slug=f"report-{identifier.hex}",
        action="create",
        origin="import",
        source_name="Reporting fixture",
        proposed={"name": "Reporting fixture"},
        created_by={"sub": "report-test"},
        status=status,
        reviewed_at=reviewed_at,
        reviewed_by={"sub": "reviewer"} if reviewed_at else None,
        applied={} if status == "approved" else None,
    )
    session.add(proposal)
    session.flush()
    if run_status is not None:
        session.add(
            TopicDecisionRun(
                proposal_id=identifier,
                input_hash="a" * 64,
                status=run_status,
                state={"calls": list(calls)},
                updated_at=START,
                **kw,
            )
        )
        session.flush()
    return identifier


def facts(session, identifier):
    return session.execute(
        text("""SELECT ordinal, stage, stage_call, started_at, tokens, charged_tokens, escalated
            FROM topic_decision_calls WHERE proposal_id=:id ORDER BY ordinal"""),
        {"id": identifier},
    ).all()


def test_missing_and_malformed_calls_are_safe_unknown_facts(database):
    malformed = [
        None,
        "broken",
        7,
        {},
        call(stage=None, started_at=None, tokens=None),
        call(
            stage="draft",
            started_at=START,
            tokens={"totalTokens": "invalid", "inputTokens": {}, "outputTokens": 1.2},
            escalated="invalid",
            charged_tokens="overflow",
        ),
        call(
            stage="draft",
            started_at=START,
            tokens={"totalTokens": "3", "inputTokens": 4, "outputTokens": 5},
            escalated="true",
            charged_tokens="11",
        ),
        {"stage": "verification", "started_at": "not-a-timestamp", "tokens": {}},
        call(
            "verification",
            START,
            {"totalTokens": 10**40, "inputTokens": str(2**63), "outputTokens": False},
        ),
    ]
    with database.begin() as session:
        identifier = add_run(session, malformed, status="approved", reviewed_at=START)
        for value in ({}, None, "not-an-array", 3):
            empty = add_run(session)
            session.get(TopicDecisionRun, empty).state = {"calls": value}
        empty = add_run(session)
        session.get(TopicDecisionRun, empty).state = {}
    with database() as session:
        rows = facts(session, identifier)
        assert [row.ordinal for row in rows] == list(range(1, 10))
        assert [row.stage_call for row in rows] == [1, 2, 3, 4, 5, 1, 2, 1, 2]
        assert [int(row.tokens) for row in rows] == [0, 0, 0, 0, 0, 0, 9, 0, 0]
        assert rows[5].charged_tokens is None and rows[6].charged_tokens == 11
        assert not rows[5].escalated and rows[6].escalated
        assert rows[7].started_at is None
        timed = [row for row in rows if row.started_at and START <= row.started_at <= END]
        assert len(timed) == 3
        assert sum(int(row.tokens) for row in timed) == 9
        assert sum(row.charged_tokens or 0 for row in timed) == 11
        assert sum(row.tokens == 0 for row in timed) == 2
        assert sum(row.stage_call > 1 for row in timed) == 2
        assert sum(row.escalated for row in timed) == 1


def test_special_timestamps_stay_undated_and_large_token_addition_cannot_overflow(database):
    timestamps = ["now", "today", "infinity", "-infinity", "2026-02-30T12:00:00Z"]
    calls = [{**call(tokens=10), "started_at": value} for value in timestamps]
    calls.append(call(tokens={"inputTokens": 2**63 - 1, "outputTokens": 2**63 - 1}))
    with database.begin() as session:
        session.execute(text("SET LOCAL TIME ZONE 'Pacific/Honolulu'"))
        identifier = add_run(session, calls, status="approved", reviewed_at=START)
        naive = add_run(session, [{**call(), "started_at": "2026-10-20T00:00:00"}])
    with database() as session:
        rows = facts(session, identifier)
        assert all(row.started_at is None for row in rows[:-1])
        assert rows[-1].tokens == 2 * (2**63 - 1)
        assert facts(session, naive)[0].started_at == START
        assert sum(int(row.tokens) for row in rows) == 2 * (2**63 - 1) + 50
        assert sum(row.started_at is not None for row in rows) == 1


def test_facts_follow_append_settlement_correction_reorder_removal_and_noop(database):
    initial = [call("draft", START, 10), call("verification", START, 20)]
    with database.begin() as session:
        identifier = add_run(session, initial)
    with database.begin() as session:
        run = session.get(TopicDecisionRun, identifier)
        run.state = {"calls": [*initial, call("draft", END, 0, charged_tokens=100)]}
    with database() as session:
        assert [row.stage_call for row in facts(session, identifier)] == [1, 1, 2]
        before = dict(
            session.execute(
                text("""SELECT ordinal,xmin::text FROM topic_decision_calls
            WHERE proposal_id=:id"""),
                {"id": identifier},
            ).all()
        )
    settled = [*initial, call("draft", END, 30, charged_tokens=30)]
    with database.begin() as session:
        session.get(TopicDecisionRun, identifier).state = {"calls": settled}
    with database() as session:
        after = dict(
            session.execute(
                text("""SELECT ordinal,xmin::text FROM topic_decision_calls
            WHERE proposal_id=:id"""),
                {"id": identifier},
            ).all()
        )
        assert after[1] == before[1] and after[2] == before[2] and after[3] != before[3]
        assert sum(int(row.tokens) for row in facts(session, identifier)) == 60
    with database.begin() as session:
        # Checkpoints and exact replay must not rewrite facts on every state save.
        session.execute(
            text("""UPDATE topic_decision_runs
            SET state=state || '{"checkpoint":"saved"}'::jsonb WHERE proposal_id=:id"""),
            {"id": identifier},
        )
        session.execute(
            text("UPDATE topic_decision_runs SET state=state WHERE proposal_id=:id"),
            {"id": identifier},
        )
    with database() as session:
        assert (
            dict(
                session.execute(
                    text("""SELECT ordinal,xmin::text FROM topic_decision_calls
            WHERE proposal_id=:id"""),
                    {"id": identifier},
                ).all()
            )
            == after
        )
    corrected = [settled[2], {**settled[0], "stage": "verification"}, settled[1]]
    with database.begin() as session:
        session.get(TopicDecisionRun, identifier).state = {"calls": corrected}
    with database() as session:
        assert [
            (row.ordinal, row.stage, row.stage_call, int(row.tokens))
            for row in facts(session, identifier)
        ] == [(1, "draft", 1, 30), (2, "verification", 1, 10), (3, "verification", 2, 20)]
    with database() as session:
        session.get(TopicDecisionRun, identifier).state = {"calls": []}
        session.flush()
        assert facts(session, identifier) == []
        session.rollback()
    with database.begin() as session:
        assert len(facts(session, identifier)) == 3
        session.get(TopicDecisionRun, identifier).state = {"calls": corrected[:1]}
    with database() as session:
        assert len(facts(session, identifier)) == 1
        assert sum(int(row.tokens) for row in facts(session, identifier)) == 30
    with database.begin() as session:
        session.get(TopicDecisionRun, identifier).state = {}
    with database() as session:
        assert facts(session, identifier) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"tokens": {"totalTokens": 99}}, {"tokens": 99}),
        ({"started_at": END.isoformat()}, {"started_at": END}),
        ({"stage": "verification"}, {"stage": "verification", "stage_call": 1}),
        ({"charged_tokens": None}, {"charged_tokens": None}),
        ({"escalated": True}, {"escalated": True}),
        ({"status": "returned"}, {}),
    ],
)
def test_correcting_one_call_does_not_lock_unchanged_call_facts(database, changes, expected):
    initial = [call(tokens=index + 1) for index in range(20)]
    corrected = [*initial[:-1], {**initial[-1], **changes}]
    with database.begin() as session:
        identifier = add_run(session, initial)
    with database() as session:
        before = facts(session, identifier)
        engine = session.get_bind()
    with engine.connect() as holder, holder.begin():
        holder.execute(
            text("""SELECT ordinal FROM topic_decision_calls
            WHERE proposal_id=:id AND ordinal=1 FOR UPDATE"""),
            {"id": identifier},
        )
        with database.begin() as session:
            session.execute(text("SET LOCAL lock_timeout='2s'"))
            run = session.get(TopicDecisionRun, identifier)
            run.state = {"calls": corrected}
            # The former upsert attempted to lock ordinal 1 before checking that
            # it was unchanged, so a correction of ordinal 20 timed out here.
            session.flush()
    with database() as session:
        after = facts(session, identifier)
        assert after[:-1] == before[:-1]
        assert after[-1]._asdict() == {**before[-1]._asdict(), **expected}
        assert session.get(TopicDecisionRun, identifier).state["calls"] == corrected


def test_concurrent_atomic_appends_never_duplicate_or_lose_fact_occurrences(database):
    with database.begin() as session:
        identifier = add_run(session, [call(tokens=1)])
    writers = 4
    barrier = Barrier(writers)

    def append(index):
        with database.begin() as session:
            barrier.wait(timeout=10)
            session.execute(
                text("""UPDATE topic_decision_runs
                SET state=jsonb_set(state,'{calls}',(state->'calls') || CAST(:call AS jsonb))
                WHERE proposal_id=:id"""),
                {"id": identifier, "call": json.dumps([call(tokens=index + 2)])},
            )

    with ThreadPoolExecutor(max_workers=writers) as pool:
        list(pool.map(append, range(writers)))
    with database() as session:
        rows = facts(session, identifier)
        assert [row.ordinal for row in rows] == [1, 2, 3, 4, 5]
        assert [row.stage_call for row in rows] == [1, 2, 3, 4, 5]
        assert sorted(int(row.tokens) for row in rows) == [1, 2, 3, 4, 5]
        assert sum(int(row.tokens) for row in rows) == 15
        assert sum(row.stage_call > 1 for row in rows) == 4


@pytest.mark.parametrize("deleted", ["run", "proposal", "truncate"])
def test_source_deletion_and_transactional_truncate_remove_call_facts(database, deleted):
    with database.begin() as session:
        identifier = add_run(session, [call()])
    with database() as session:
        if deleted == "truncate":
            session.execute(text("TRUNCATE topic_decision_runs CASCADE"))
        elif deleted == "run":
            session.delete(session.get(TopicDecisionRun, identifier))
        else:
            session.delete(session.get(TopicProposal, identifier))
        session.flush()
        assert facts(session, identifier) == []
        session.rollback()
    with database() as session:
        assert len(facts(session, identifier)) == 1


def test_migration_backfill_matches_live_projection_and_downgrade_preserves_json(database):
    migration = importlib.import_module("migrations.versions.0036_topic_decision_calls")
    calls = [
        call("draft", START - timedelta(days=1), 10),
        call("draft", START, 20, escalated=True),
        {"stage": "verification", "started_at": "invalid", "tokens": {}},
        None,
    ]
    with database.begin() as session:
        identifier = add_run(session, calls)
    with database() as session:
        expected = facts(session, identifier)
        original = copy.deepcopy(session.get(TopicDecisionRun, identifier).state)
        engine = session.get_bind()
    with engine.connect() as connection, connection.begin() as transaction:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert connection.scalar(text("SELECT to_regclass('topic_decision_calls')")) is None
            assert (
                connection.scalar(
                    text("SELECT state FROM topic_decision_runs WHERE proposal_id=:id"),
                    {"id": identifier},
                )
                == original
            )
            migration.upgrade()
            actual = connection.execute(
                text("""SELECT ordinal,stage,stage_call,started_at,
                tokens,charged_tokens,escalated FROM topic_decision_calls
                WHERE proposal_id=:id ORDER BY ordinal"""),
                {"id": identifier},
            ).all()
            assert actual == expected
            migration.downgrade()
            migration.upgrade()
            assert connection.scalar(text("SELECT count(*) FROM topic_decision_calls")) == len(
                calls
            )
        transaction.rollback()


def test_topic_migration_round_trip_keeps_scheduler_clock_capture_and_source_history(database):
    migration = importlib.import_module("migrations.versions.0036_topic_decision_calls")
    capture_sql = text("""SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger
        WHERE tgrelid='articles'::regclass
        AND tgname IN ('read_model_changed','read_model_article_clock_changed') ORDER BY tgname""")
    with database.begin() as session:
        proposal_id = add_run(session, [call(tokens=10), call(tokens=20)])
        article = Article(
            canonical_url=f"https://publisher.example/migration/{proposal_id}",
            url_hash=proposal_id.hex,
            title="Keep the scheduler clock during topic rollback",
            automation_next_check_at=START,
        )
        session.add(article)
        session.flush()
        article_id = article.id
        retained_triggers = session.execute(capture_sql).all()
        # Historical 0037 capture remains until the finite ClickHouse import
        # confirms retained history. This unrelated topic migration must retain it.
        assert {row.tgname for row in retained_triggers} == {
            "read_model_changed",
            "read_model_article_clock_changed",
        }
        source_state = copy.deepcopy(session.get(TopicDecisionRun, proposal_id).state)
        expected_facts = facts(session, proposal_id)
    # Exercise only the historical topic migration in one rolled-back transaction.
    # The live import owns analytical retirement separately from schema upgrade.
    with database.kw["bind"].connect() as connection, connection.begin() as transaction:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert connection.scalar(text("SELECT to_regclass('topic_decision_calls')")) is None
            assert connection.execute(capture_sql).all() == retained_triggers
            assert (
                connection.scalar(
                    select(TopicDecisionRun.state).where(
                        TopicDecisionRun.proposal_id == proposal_id
                    )
                )
                == source_state
            )
            for index in (1, 2):
                if index == 2:
                    migration.upgrade()
                    rebuilt = connection.execute(
                        text("""SELECT ordinal,stage,stage_call,started_at,
                                tokens,charged_tokens,escalated
                            FROM topic_decision_calls WHERE proposal_id=:id ORDER BY ordinal"""),
                        {"id": proposal_id},
                    ).all()
                    assert rebuilt == expected_facts
                    assert connection.execute(capture_sql).all() == retained_triggers
                    assert (
                        connection.scalar(
                            select(TopicDecisionRun.state).where(
                                TopicDecisionRun.proposal_id == proposal_id
                            )
                        )
                        == source_state
                    )
                clock = START + timedelta(hours=index)
                connection.execute(
                    text("UPDATE articles SET automation_next_check_at=:clock WHERE id=:id"),
                    {"clock": clock, "id": article_id},
                )
                assert (
                    connection.scalar(
                        text("SELECT automation_next_check_at FROM articles WHERE id=:id"),
                        {"id": article_id},
                    )
                    == clock
                )
                assert (
                    connection.scalar(text("SELECT to_regclass('read_model_events')")) is not None
                )
        transaction.rollback()


@pytest.fixture
def isolated_reporting_schema(database):
    with database() as session:
        engine = session.get_bind()
    schema = f"topic_reporting_lock_{uuid.uuid4().hex}"
    with engine.begin() as connection:
        original_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
        connection.execute(text(f"CREATE SCHEMA {schema}"))
        connection.execute(
            text(
                f"CREATE TABLE {schema}.topic_proposals (id uuid PRIMARY KEY, "
                "reviewed_at timestamptz)"
            )
        )
        connection.execute(
            text(
                f"CREATE TABLE {schema}.topic_decision_runs (proposal_id uuid PRIMARY KEY, "
                "state jsonb NOT NULL)"
            )
        )
        connection.execute(
            text(f"INSERT INTO {schema}.topic_proposals VALUES (:id, :reviewed)"),
            {"id": uuid.UUID(int=1), "reviewed": START},
        )
        connection.execute(
            text(f"INSERT INTO {schema}.topic_decision_runs VALUES (:id, CAST(:state AS jsonb))"),
            {"id": uuid.UUID(int=1), "state": json.dumps({"calls": [call()]})},
        )
    try:
        yield engine, schema, original_head
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))


def wait_for_migration_retry(connection, started, migration_pid):
    assert started.wait(timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        connection.execute(text("SELECT pg_stat_clear_snapshot()"))
        if (
            connection.scalar(
                text("SELECT wait_event FROM pg_stat_activity WHERE pid=:pid"),
                {"pid": migration_pid[0]},
            )
            == "PgSleep"
        ):
            return
        time.sleep(0.01)
    pytest.fail("Migration did not release its partial locks and retry admission")


@pytest.mark.parametrize("flushed", [False, True])
def test_migration_retries_source_lock_admission_without_blocking_reverse_order_writer(
    isolated_reporting_schema, flushed
):
    engine, schema, original_head = isolated_reporting_schema
    migration = importlib.import_module("migrations.versions.0036_topic_decision_calls")
    started = Event()
    migration_pid = []

    def upgrade():
        with engine.connect() as connection, connection.begin() as transaction:
            connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            migration_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert connection.scalar(text("SELECT count(*) FROM topic_decision_calls")) == 1
            transaction.rollback()

    with engine.connect() as writer, writer.begin(), ThreadPoolExecutor(max_workers=1) as pool:
        writer.execute(text(f"SET LOCAL search_path TO {schema}"))
        writer.execute(text("SET LOCAL lock_timeout = '2s'"))
        writer.execute(text("SELECT * FROM topic_decision_runs FOR UPDATE"))
        if flushed:
            writer.execute(text("UPDATE topic_decision_runs SET state=state"))
        task = pool.submit(upgrade)
        try:
            wait_for_migration_retry(writer, started, migration_pid)
            # This is the ORM's run -> proposal flush order. It must finish
            # while migration admission retries, rather than forming a cycle.
            writer.execute(text("UPDATE topic_decision_runs SET state=state"))
            writer.execute(text("UPDATE topic_proposals SET reviewed_at=reviewed_at"))
        finally:
            writer.rollback()
        task.result(timeout=10)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT to_regclass(:table)"),
                {"table": f"{schema}.topic_decision_calls"},
            )
            is None
        )
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == original_head


@pytest.mark.parametrize("blocker", ["writer", "report"])
def test_downgrade_retries_all_table_locks_without_blocking_existing_work(
    isolated_reporting_schema, blocker
):
    engine, schema, original_head = isolated_reporting_schema
    migration = importlib.import_module("migrations.versions.0036_topic_decision_calls")
    with engine.begin() as connection:
        connection.execute(text(f"SET LOCAL search_path TO {schema}"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    started = Event()
    migration_pid = []

    def downgrade():
        with engine.connect() as connection, connection.begin() as transaction:
            connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            migration_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert (
                connection.scalar(
                    text("SELECT to_regclass(:table)"), {"table": f"{schema}.topic_decision_calls"}
                )
                is None
            )
            assert connection.scalar(text("SELECT count(*) FROM topic_decision_runs")) == 1
            transaction.rollback()

    with engine.connect() as active, active.begin(), ThreadPoolExecutor(max_workers=1) as pool:
        active.execute(text(f"SET LOCAL search_path TO {schema}"))
        active.execute(text("SET LOCAL lock_timeout = '2s'"))
        if blocker == "writer":
            active.execute(text("UPDATE topic_decision_runs SET state=state"))
        else:
            active.execute(text("SELECT * FROM topic_decision_calls"))
        task = pool.submit(downgrade)
        try:
            wait_for_migration_retry(active, started, migration_pid)
            if blocker == "writer":
                active.execute(text("UPDATE topic_proposals SET reviewed_at=reviewed_at"))
            else:
                # A report can retain facts AccessShare before asking for sources.
                assert active.scalar(text("SELECT count(*) FROM topic_decision_runs")) == 1
                assert active.scalar(text("SELECT count(*) FROM topic_proposals")) == 1
        finally:
            active.rollback()
        task.result(timeout=10)
    with engine.connect() as connection:
        assert connection.scalar(text(f"SELECT count(*) FROM {schema}.topic_decision_calls")) == 1
        assert connection.scalar(text(f"SELECT state FROM {schema}.topic_decision_runs")) == {
            "calls": [call()]
        }
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == original_head
