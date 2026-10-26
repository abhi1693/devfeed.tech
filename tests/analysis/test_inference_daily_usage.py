"""Native ClickHouse totals preserve mutable invocation accounting exactly."""

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from devfeed_core.models import InferenceCall
from sqlalchemy import func, select, text

from tests.support import analytics as analytics_reports

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


def expected_activity(session, start, end):
    groups = {}
    for call in session.scalars(select(InferenceCall)):
        if not start <= call.started_at <= end:
            continue
        day = call.started_at.astimezone(UTC).date().isoformat()
        model = call.model or "Unknown model"
        effort = call.reasoning_effort or "Inherited / unknown"
        key = (day, call.operation, model, effort)
        if key not in groups:
            groups[key] = dict(
                date=day,
                operation=call.operation,
                model=model,
                effort=effort,
                calls=0,
                returned=0,
                failed=0,
                running=0,
                unreported=0,
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                web_searches=0,
                duration_ms=0,
            )
        point = groups[key]
        parsed = {}
        for field, name in (
            ("input_tokens", "inputTokens"),
            ("cached_input_tokens", "cachedInputTokens"),
            ("output_tokens", "outputTokens"),
            ("reasoning_tokens", "reasoningOutputTokens"),
        ):
            value = str(call.tokens.get(name))
            parsed[field] = int(value) if re.fullmatch(r"[0-9]{1,12}", value) else 0
        point["calls"] += 1
        for status in ("returned", "failed", "running"):
            point[status] += call.status == status
        point["unreported"] += parsed["input_tokens"] + parsed["output_tokens"] == 0
        point["input_tokens"] += parsed["input_tokens"]
        point["cached_input_tokens"] += min(parsed["input_tokens"], parsed["cached_input_tokens"])
        point["output_tokens"] += parsed["output_tokens"]
        point["reasoning_tokens"] += min(parsed["output_tokens"], parsed["reasoning_tokens"])
        point["web_searches"] += call.web_searches
        point["duration_ms"] += call.duration_ms
    return list(groups.values())


def equal_activity(session, start=NOW - timedelta(days=30), end=NOW):
    actual = [row for row in analytics_reports.inference_rows(session, start, end)]
    expected = expected_activity(session, start, end)

    def signature(row):
        return tuple(row[k] for k in ("date", "operation", "model", "effort")) + tuple(
            int(row[k]) for k in sorted(row) if k not in {"date", "operation", "model", "effort"}
        )

    assert sorted(map(signature, actual)) == sorted(map(signature, expected))
    assert sum(row["calls"] for row in actual) == session.scalar(
        select(func.count())
        .select_from(InferenceCall)
        .where(InferenceCall.started_at >= start, InferenceCall.started_at <= end)
    )
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


def test_truncate_clears_derived_totals(database):
    with database.begin() as session:
        session.add(invocation(NOW - timedelta(days=5)))
    with database.begin() as session:
        session.execute(text("TRUNCATE inference_calls"))
        assert equal_activity(session) == []


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


def test_migration_preserves_invocation_ledger_and_retires_postgres_aggregate_triggers(database):
    with database.begin() as session:
        session.add(invocation(NOW - timedelta(days=10)))
    with database() as session:
        expected = expected_activity(session, NOW - timedelta(days=30), NOW)
        assert equal_activity(session) == expected
        assert session.scalar(select(func.count()).select_from(InferenceCall)) == 1
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid='inference_calls'::regclass "
                    "AND tgname LIKE 'inference_daily_usage_%'"
                )
            )
            == 0
        )


def test_historical_queries_use_clickhouse_without_postgres_aggregation(database):
    with database.begin() as session:
        session.add_all(invocation(NOW - timedelta(days=i % 90, seconds=i)) for i in range(300))
    with database() as session:
        result = equal_activity(session, NOW - timedelta(days=89), NOW)
    assert sum(row["calls"] for row in result) > 250


def test_unknown_display_labels_merge_without_losing_raw_invocation_identities_or_usage(database):
    from devfeed_core.analytics_rollups import canonical_sql

    rows = [
        invocation(
            NOW,
            model=model,
            reasoning_effort=effort,
            tokens={"inputTokens": index, "outputTokens": index + 3},
        )
        for index, (model, effort) in enumerate(
            [(None, None), ("", ""), ("Unknown model", "Inherited / unknown")], start=1
        )
    ]
    with database.begin() as session:
        session.add_all(rows)
    with database() as session:
        activity = equal_activity(session, NOW, NOW)
        assert len(activity) == 1
        assert activity[0]["model"] == "Unknown model"
        assert activity[0]["effort"] == "Inherited / unknown"
        assert activity[0]["calls"] == 3
        assert activity[0]["input_tokens"] == 6 and activity[0]["output_tokens"] == 15

        async def identities(client, now):
            return await client.query(
                "SELECT toString(fact_id) AS id,calls FROM (" + canonical_sql("inference") + ")",
                {
                    "bucket": NOW.replace(minute=0, second=0, microsecond=0),
                    "end": NOW.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
                },
            )

        facts = analytics_reports._read(session, NOW, identities)
        assert {row["id"] for row in facts} == {str(row.id) for row in rows}
        assert all(row["calls"] == 1 for row in facts)
