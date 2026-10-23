"""Daily totals must be an exact transactional projection of mutable ledger rows."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from devfeed_core.inference_metrics import _ledger_activity, inference_charts
from devfeed_core.models import InferenceCall
from sqlalchemy import func, select, text

from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 18, 19, tzinfo=UTC)


def invocation(at, **changes):
    return InferenceCall(
        **{
            "id": uuid.uuid4(),
            "started_at": at,
            "finished_at": NOW,
            "operation": "partner_qualification",
            "model": "model-a",
            "reasoning_effort": "low",
            "request_hash": "0" * 64,
            "status": "returned",
            "tokens": {
                "inputTokens": 100,
                "cachedInputTokens": 200,
                "outputTokens": 20,
                "reasoningOutputTokens": 50,
            },
            "web_searches": 1,
            "duration_ms": 500,
            **changes,
        }
    )


def equal_activity(session, start=NOW - timedelta(days=30), end=NOW):
    actual = [row.model_dump() for row in inference_charts(session, start, end).activity]
    expected = [dict(row) for row in _ledger_activity(session, start, end)]

    def signature(row):
        return tuple(row[k] for k in ("date", "operation", "model", "effort")) + tuple(
            int(row[k]) for k in sorted(row) if k not in {"date", "operation", "model", "effort"}
        )

    assert sorted(map(signature, actual)) == sorted(map(signature, expected))
    return actual


@pytest.mark.parametrize("days", [0, 1, 30, 90])
def test_summary_preserves_numeric_grouping_and_exact_window_boundaries(database, days):
    start = NOW - timedelta(days=days)
    values = [
        0,
        42,
        "00042",
        "999999999999",
        None,
        True,
        False,
        -1,
        1.5,
        "",
        " 1",
        "-1",
        "1.0",
        "1000000000000",
        "١٢",
        [],
        {},
    ]
    with database.begin() as session:
        session.add_all(
            invocation(
                NOW - timedelta(days=i % max(days, 1)),
                tokens={key: value},
                model=[None, "Unknown model", "", "model-a"][i % 4],
                reasoning_effort=[None, "Inherited / unknown", "low"][i % 3],
                status=["returned", "failed", "running", "cancelled"][i % 4],
            )
            for i, (key, value) in enumerate(
                (key, value)
                for key in [
                    "inputTokens",
                    "cachedInputTokens",
                    "outputTokens",
                    "reasoningOutputTokens",
                ]
                for value in values
            )
        )
        session.add_all(
            invocation(at, operation="boundary")
            for at in [
                start - timedelta(microseconds=1),
                start,
                NOW,
                NOW + timedelta(microseconds=1),
            ]
        )
    with database() as session:
        result = equal_activity(session, start)
        assert sum(row["calls"] for row in result if row["operation"] == "boundary") == 2
        equal_activity(session, start.replace(hour=0), NOW.replace(hour=0))
        equal_activity(session, NOW + timedelta(days=1), NOW + timedelta(days=2))


def test_raw_mutations_move_buckets_and_rollback_without_stale_totals(database):
    row = invocation(NOW - timedelta(days=12), web_searches=-2147483648, duration_ms=-2147483648)
    with database.begin() as session:
        session.add(row)
    with database.begin() as session:
        session.execute(
            text("""UPDATE inference_calls SET status='failed', model=NULL,
            reasoning_effort=NULL, tokens=cast(:tokens AS jsonb),
            duration_ms=99, web_searches=3, started_at=:at WHERE id=:id"""),
            {
                "id": row.id,
                "at": NOW - timedelta(days=10),
                "tokens": '{"inputTokens":7,"outputTokens":"9"}',
            },
        )
        equal_activity(session)
    with database() as session:
        before = equal_activity(session)
        session.execute(
            text("UPDATE inference_calls SET started_at=:at WHERE id=:id"),
            {"id": row.id, "at": NOW + timedelta(days=1)},
        )
        assert equal_activity(session) == []
        session.rollback()
        assert equal_activity(session) == before
    with database.begin() as session:
        session.execute(text("DELETE FROM inference_calls WHERE id=:id"), {"id": row.id})
        assert equal_activity(session) == []
        assert session.scalar(text("SELECT count(*) FROM inference_daily_usage")) == 0


def test_truncate_clears_derived_totals(database):
    with database.begin() as session:
        session.add(invocation(NOW - timedelta(days=5)))
    with database.begin() as session:
        session.execute(text("TRUNCATE inference_calls"))
        assert equal_activity(session) == []
        assert session.scalar(text("SELECT count(*) FROM inference_daily_usage")) == 0


def test_concurrent_bucket_swaps_keep_totals_consistent(database):
    rows = [invocation(NOW - timedelta(days=5), model=name) for name in ["a", "b"]]
    with database.begin() as session:
        session.add_all(rows)
    barrier = Barrier(2)

    def change(row, model):
        with database.begin() as session:
            session.execute(text("SET LOCAL lock_timeout='5s'"))
            barrier.wait(timeout=5)
            session.execute(
                text("UPDATE inference_calls SET model=:model WHERE id=:id"),
                {"model": model, "id": row.id},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(change, row, model) for row, model in zip(rows, ["b", "a"], strict=True)
        ]
        for future in futures:
            future.result(timeout=10)
    with database() as session:
        result = equal_activity(session)
        assert sum(row["calls"] for row in result) == 2


def test_migration_backfills_history_and_old_schema_retains_exact_fallback(database):
    config = Config(str(REPO_ROOT / "alembic.ini"))
    try:
        command.downgrade(config, "0030")
        with database.begin() as session:
            session.add(invocation(NOW - timedelta(days=10)))
        with database() as session:
            assert len(equal_activity(session)) == 1
        command.upgrade(config, "head")
        with database() as session:
            assert len(equal_activity(session)) == 1
            assert session.scalar(text("SELECT sum(calls) FROM inference_daily_usage")) == 1
            assert session.scalar(select(func.count()).select_from(InferenceCall)) == 1
    finally:
        command.upgrade(config, "head")


def test_full_days_do_not_scan_their_ledger_history(database):
    from devfeed_core.inference_metrics import _daily_activity

    with database.begin() as session:
        session.add_all(invocation(NOW - timedelta(days=i % 90, seconds=i)) for i in range(4500))
        session.execute(text("ANALYZE inference_calls"))
        session.execute(text("ANALYZE inference_daily_usage"))

    class Capture:
        def __init__(self, session):
            self.session = session
            self.statement = None
            self.params = None

        def execute(self, statement, params):
            self.statement, self.params = statement, params
            return self.session.execute(statement, params)

    def visited(node):
        count = 0
        if node.get("Relation Name") == "inference_calls":
            count = (node["Actual Rows"] + node.get("Rows Removed by Filter", 0)) * node[
                "Actual Loops"
            ]
        return count + sum(visited(child) for child in node.get("Plans", []))

    with database() as session:
        plans = []
        for loader in (_ledger_activity, _daily_activity):
            capture = Capture(session)
            loader(capture, NOW - timedelta(days=89), NOW)
            plan = session.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(capture.statement)),
                capture.params,
            ).scalar_one()[0]["Plan"]
            plans.append(plan)
        assert visited(plans[1]) <= 110
        assert visited(plans[0]) >= 4400
        equal_activity(session, NOW - timedelta(days=89), NOW)
