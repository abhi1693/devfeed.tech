"""Reporting preserves workflow history while call facts follow transactional writes."""

import copy
import importlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier, Event

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core.inference_metrics import inference_charts
from devfeed_core.models import (
    Article,
    PipelineProjection,
    ReadModelEvent,
    TopicDecisionRun,
    TopicProposal,
)
from devfeed_core.read_models import set_state
from devfeed_core.topic_decision_metrics import decision_metrics
from devfeed_core.version import SCHEMA_REVISION
from sqlalchemy import delete, event, text

from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.integration
START = datetime(2026, 10, 20, tzinfo=UTC)
END = START + timedelta(days=2)


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


def legacy_usage(session, start=START, now=END):
    # Retain the released SQL as an independent oracle for well-formed history.
    return dict(
        session.execute(
            text("""
            WITH calls AS (
              SELECT r.proposal_id, c, n,
                (p.status IN ('approved', 'rejected')
                  AND p.reviewed_at BETWEEN :start AND :now) AS completed
              FROM topic_decision_runs r JOIN topic_proposals p ON p.id=r.proposal_id,
                jsonb_array_elements(r.state->'calls') WITH ORDINALITY AS items(c,n)
            ), totals AS (
              SELECT *, greatest(coalesce((c->'tokens'->>'totalTokens')::bigint,0),
                coalesce((c->'tokens'->>'inputTokens')::bigint,0)
                + coalesce((c->'tokens'->>'outputTokens')::bigint,0)) AS tokens,
                (c->>'started_at')::timestamptz BETWEEN :start AND :now AS in_window,
                row_number() OVER (PARTITION BY proposal_id,c->>'stage' ORDER BY n) AS stage_call
              FROM calls
            )
            SELECT count(*) FILTER (WHERE in_window) AS calls,
              coalesce(sum(tokens) FILTER (WHERE in_window),0) AS tokens,
              coalesce(sum((c->>'charged_tokens')::bigint) FILTER (WHERE in_window),0)
                AS charged_tokens,
              count(*) FILTER (WHERE in_window AND tokens=0) AS unreported_calls,
              count(*) FILTER (WHERE in_window AND (c->>'escalated')::boolean) AS escalations,
              count(*) FILTER (WHERE in_window AND stage_call>1) AS repeat_calls,
              CASE WHEN bool_and(tokens>0) FILTER (WHERE completed)
                THEN sum(tokens) FILTER (WHERE completed)::float
                  / nullif(count(DISTINCT proposal_id) FILTER (WHERE completed),0)
                ELSE NULL END AS tokens_per_completed_topic FROM totals
            """),
            {"start": start, "now": now},
        )
        .mappings()
        .one()
    )


def test_reports_match_released_contract_for_statuses_and_whole_workflows(database):
    with database.begin() as session:
        add_run(
            session,
            [
                call("discovery", START - timedelta(hours=1), 12),
                call(
                    tokens={"totalTokens": 4, "inputTokens": 10, "outputTokens": 5},
                    charged_tokens=20,
                    status="failed",
                ),
                call(started_at=START + timedelta(hours=25), tokens=30, escalated=True),
                call("verification", END, 40),
            ],
            status="approved",
            reviewed_at=START + timedelta(hours=2),
            run_status="decided",
        )
        add_run(
            session,
            [
                call("draft", START - timedelta(hours=1), 5),
                call("verification", START + timedelta(hours=26), 25),
            ],
            status="rejected",
            reviewed_at=END,
            run_status="decided",
        )
        add_run(
            session,
            [call("discovery", START + timedelta(hours=27), 50)],
            status="approved",
            reviewed_at=START - timedelta(days=1),
            run_status="decided",
        )
        add_run(
            session,
            [
                call(started_at=START - timedelta(days=1), tokens=8),
                call(started_at=START + timedelta(hours=28), tokens=11, escalated=True),
            ],
        )
        identifier = add_run(
            session,
            [call("discovery", START + timedelta(hours=30), 0, charged_tokens=100)],
            run_status="deferred",
            reason="rate_limit",
        )
        session.get(TopicDecisionRun, identifier).updated_at = START + timedelta(hours=29)
        identifier = add_run(session, run_status="deferred")
        session.get(TopicDecisionRun, identifier).updated_at = START - timedelta(days=1)
        add_run(session, run_status="review")
        add_run(session, run_status=None)
        add_run(session, status="approved", reviewed_at=START, run_status="decided")
    with database() as session:
        result = decision_metrics(session, START, END).model_dump()
        assert {key: result[key] for key in legacy_usage(session)} == legacy_usage(session)
        assert result == {
            "pending": 5,
            "actionable": 2,
            "deferred": 2,
            "awaiting_review": 1,
            "approved": 2,
            "rejected": 1,
            "decisions_per_hour": 0.06,
            "estimated_drain_hours": 33.3,
            "calls": 7,
            "tokens": 171,
            "charged_tokens": 276,
            "unreported_calls": 1,
            "repeat_calls": 2,
            "escalations": 2,
            "escalation_percent": 28.6,
            "tokens_per_completed_topic": 63.5,
            "deferred_reasons": {"rate_limit": 1, "unspecified": 1},
        }
        assert inference_charts(session, START, END).model_dump() == {
            "first_recorded_at": None,
            "activity": [],
            "topic_activity": [
                {
                    "date": "2026-10-20",
                    "approved": 2,
                    "rejected": 0,
                    "deferred": 0,
                    "calls": 1,
                    "escalations": 0,
                    "repeated_stages": 0,
                },
                {
                    "date": "2026-10-21",
                    "approved": 0,
                    "rejected": 0,
                    "deferred": 1,
                    "calls": 5,
                    "escalations": 2,
                    "repeated_stages": 2,
                },
                {
                    "date": "2026-10-22",
                    "approved": 0,
                    "rejected": 1,
                    "deferred": 0,
                    "calls": 1,
                    "escalations": 0,
                    "repeated_stages": 0,
                },
            ],
        }


@pytest.mark.parametrize("unknown", [False, True])
def test_completion_uses_undated_and_outside_window_calls_without_empty_denominators(
    database, unknown
):
    with database.begin() as session:
        add_run(
            session,
            [
                call(started_at=None, tokens=0 if unknown else 20),
                call(started_at=START - timedelta(days=30), tokens=30),
                call("verification", START, 10),
            ],
            status="approved",
            reviewed_at=END,
            run_status="decided",
        )
        add_run(session, status="rejected", reviewed_at=START, run_status="decided")
        add_run(
            session,
            [call(tokens=1000)],
            status="approved",
            reviewed_at=END + timedelta(microseconds=1),
            run_status="decided",
        )
    with database() as session:
        result = decision_metrics(session, START, END)
        assert result.calls == 2 and result.tokens == 1010
        assert result.tokens_per_completed_topic == (None if unknown else 60)
        assert (
            result.model_dump()["tokens_per_completed_topic"]
            == legacy_usage(session)["tokens_per_completed_topic"]
        )


def test_chart_buckets_and_inclusive_boundaries_use_utc_for_offset_ranges(database):
    offset = timezone(timedelta(hours=5, minutes=30))
    start = datetime(2026, 10, 20, 1, 30, tzinfo=offset)
    end = start + timedelta(days=1)
    with database.begin() as session:
        add_run(
            session,
            [
                call(started_at=start - timedelta(microseconds=1)),
                call(started_at=start),
                call(started_at=end),
                call(started_at=end + timedelta(microseconds=1)),
            ],
            status="approved",
            reviewed_at=start,
            run_status="decided",
        )
        identifier = add_run(session, run_status="deferred")
        session.get(TopicDecisionRun, identifier).updated_at = end
    with database() as session:
        session.execute(text("SET LOCAL TIME ZONE 'Pacific/Honolulu'"))
        result = decision_metrics(session, start, end)
        assert result.calls == 2 and result.repeat_calls == 2 and result.approved == 1
        assert [
            day.model_dump() for day in inference_charts(session, start, end).topic_activity
        ] == [
            {
                "date": "2026-10-19",
                "approved": 1,
                "rejected": 0,
                "deferred": 0,
                "calls": 1,
                "escalations": 0,
                "repeated_stages": 1,
            },
            {
                "date": "2026-10-20",
                "approved": 0,
                "rejected": 0,
                "deferred": 1,
                "calls": 1,
                "escalations": 0,
                "repeated_stages": 1,
            },
        ]


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
        result = decision_metrics(session, START, END)
        assert (result.calls, result.tokens, result.charged_tokens, result.unreported_calls) == (
            3,
            9,
            11,
            2,
        )
        assert result.repeat_calls == 2 and result.escalations == 1
        assert result.tokens_per_completed_topic is None
        assert inference_charts(session, START, END).topic_activity[0].calls == 3


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
        result = decision_metrics(session, START, END)
        assert result.calls == 2 and result.tokens == 2 * (2**63 - 1) + 10
        assert result.tokens_per_completed_topic == float(2 * (2**63 - 1) + 50)


def test_report_plans_read_the_window_without_expanding_or_ranking_old_calls(database):
    with database.begin() as session:
        add_run(
            session,
            [call(started_at=START - timedelta(days=365)) for _ in range(1000)]
            + [call(started_at=START)],
        )
        session.execute(text("ANALYZE topic_decision_calls"))
        session.execute(text("ANALYZE topic_proposals"))
    captured = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "topic_decision_calls" in statement:
            captured.append((statement, parameters))

    with database() as session:
        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            assert decision_metrics(session, START, END).calls == 1
            assert inference_charts(session, START, END).topic_activity[0].calls == 1
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert len(captured) == 2
        for statement, parameters in captured:
            document = (
                session.connection()
                .exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters)
                .scalar_one()[0]
            )
            pending = [document["Plan"]]
            nodes = []
            while pending:
                node = pending.pop()
                nodes.append(node)
                pending.extend(node.get("Plans", []))
            assert not {"Function Scan", "WindowAgg"}.intersection(
                node["Node Type"] for node in nodes
            )
            scans = [node for node in nodes if node.get("Relation Name") == "topic_decision_calls"]
            assert scans and all(node["Actual Rows"] <= 1 for node in scans)
            assert any("started_at" in node.get("Index Cond", "") for node in nodes)
            assert document["Plan"]["Temp Written Blocks"] == 0


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
        assert decision_metrics(session, START, END).tokens == 60
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
        assert decision_metrics(session, START, END).tokens == 30
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
        result = decision_metrics(session, START, END)
        assert (result.calls, result.tokens, result.repeat_calls) == (5, 15, 4)


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
    config = Config(str(REPO_ROOT / "alembic.ini"))
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
        set_state(session, "pipeline", {"target_policy": "retained", "serving_policy": "retained"})
        session.add(
            PipelineProjection(
                policy_version="retained",
                kind="article",
                entity_id=article_id,
                payload={"next_check_at": START.isoformat(), "title": article.title},
                stage="analysis",
                state="waiting",
                created_at=START,
                activity_at=START,
                evaluated_at=START,
            )
        )
        session.flush()
        session.execute(delete(ReadModelEvent))
        retained_triggers = session.execute(capture_sql).all()
        assert len(retained_triggers) == 2
        source_state = copy.deepcopy(session.get(TopicDecisionRun, proposal_id).state)
        expected_facts = facts(session, proposal_id)
    try:
        command.downgrade(config, "0035")
        with database.begin() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0035"
            assert session.scalar(text("SELECT to_regclass('topic_decision_calls')")) is None
            assert session.execute(capture_sql).all() == retained_triggers
            assert session.get(TopicDecisionRun, proposal_id).state == source_state
            clock = START + timedelta(hours=1)
            session.execute(
                text("UPDATE articles SET automation_next_check_at=:clock WHERE id=:id"),
                {"clock": clock, "id": article_id},
            )
            assert session.scalar(text("SELECT count(*) FROM read_model_events")) == 0
            card = session.get(PipelineProjection, ("retained", "article", article_id))
            assert datetime.fromisoformat(card.payload["next_check_at"]) == clock
            assert card.evaluated_at == START
        command.upgrade(config, "head")
        with database.begin() as session:
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
            )
            assert facts(session, proposal_id) == expected_facts
            assert session.get(TopicDecisionRun, proposal_id).state == source_state
            assert session.execute(capture_sql).all() == retained_triggers
            clock = START + timedelta(hours=2)
            session.execute(
                text("UPDATE articles SET automation_next_check_at=:clock WHERE id=:id"),
                {"clock": clock, "id": article_id},
            )
            card = session.get(PipelineProjection, ("retained", "article", article_id))
            assert datetime.fromisoformat(card.payload["next_check_at"]) == clock
            assert card.evaluated_at == START
            assert session.scalar(text("SELECT count(*) FROM read_model_events")) == 0
    finally:
        command.upgrade(config, "head")


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
