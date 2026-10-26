"""Native analytical profiling is bounded and never connects to PostgreSQL."""

import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core.analytics import AnalyticsUnavailable

from scripts import profile_analytics_reports as profile


@pytest.mark.parametrize(
    "arguments",
    [
        ["--from", "invalid"],
        ["--from", "now-3661d"],
        ["--repeats", "21"],
        ["--concurrency", "5"],
        ["--max-queries", "0"],
        ["--max-queries", "2001"],
    ],
)
def test_cli_rejects_unbounded_reports_before_opening_any_connection(monkeypatch, arguments):
    connect = Mock(side_effect=AssertionError("Unsafe profiling connected"))
    monkeypatch.setattr(profile, "AnalyticsClient", connect)
    monkeypatch.setattr("sys.argv", ["profile-analytics", *arguments])
    with pytest.raises(SystemExit) as caught:
        profile.main()
    assert caught.value.code == 2
    connect.assert_not_called()


def test_cli_requires_an_explicit_disposable_analytics_target(monkeypatch):
    monkeypatch.delenv("DEVFEED_TEST_CLICKHOUSE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["profile-analytics"])
    with pytest.raises(SystemExit) as caught:
        profile.main()
    assert caught.value.code == 2


@pytest.fixture
def native(monkeypatch):
    state = NS(
        closed=False,
        ready=True,
        queries=[],
        active=0,
        peak=0,
        tagged=[],
        flushes=0,
        omit_log=False,
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            state.closed = True

        async def status(self):
            return {"ready": state.ready, "capture_started_at": "2026-10-26T00:00:00+00:00"}

        async def query(self, sql, parameters=None):
            if "FROM system.query_log" in sql:
                assert "query AS" not in sql
                assert "event_date >=" in sql and "event_time_microseconds >=" in sql
                assert parameters["started"].utcoffset().total_seconds() == 0
                if state.omit_log:
                    return []
                counts = {}
                for tag in state.tagged:
                    section = tag.removeprefix(parameters["prefix"])
                    counts[section] = counts.get(section, 0) + 1
                return [
                    {
                        "section": section,
                        "queries": count,
                        "total_query_duration_ms": count * 2,
                        "read_rows": count * 10,
                        "read_bytes": count * 100,
                        "peak_query_memory_bytes": 2000,
                    }
                    for section, count in sorted(counts.items())
                ]
            state.active += 1
            state.peak = max(state.peak, state.active)
            try:
                await asyncio.sleep(0)
                state.queries.append((sql, parameters))
                return [{"count": 2}]
            finally:
                state.active -= 1

        async def execute(self, sql):
            assert sql == "SYSTEM FLUSH LOGS"
            state.flushes += 1

        def _limits(self):
            return {"max_threads": 1}

        async def _operation(self, method, sql, *, parameters, fmt, settings):
            assert method == "raw_query" and fmt == "JSONEachRow"
            assert settings["max_threads"] == 1
            state.tagged.append(settings["log_comment"])
            await self.query(sql, parameters)
            return b'{"count":2}\n'

    class Reports:
        def __init__(self, client, window, now, *, capture_started_at):
            assert window.days >= 1
            state.window = window
            self.client = client

        async def section(self, group):
            await self.client.query("SELECT count()", {"group": group})
            return NS(model_dump_json=lambda: '{"count":2}')

    monkeypatch.setattr(profile, "AnalyticsClient", lambda _settings: Client())
    monkeypatch.setattr(profile, "Reports", Reports)
    return state


def test_profile_measures_only_native_queries_and_respects_concurrency(native):
    result = asyncio.run(
        profile.profile(
            None,
            groups=["inference", "publishing"],
            from_value="now-89d/d",
            to_value="now",
            repeats=3,
            concurrency=2,
            max_queries=10,
        )
    )
    assert result["complete"] and result["backend"] == "clickhouse"
    assert result["queries"] == len(native.queries) == 6
    assert native.peak == 2 and native.closed
    assert result["reports"]["inference"]["samples"] == 3
    assert result["reports"]["publishing"]["max_response_bytes"] == len('{"count":2}')


def test_profile_keeps_an_explicit_historical_leap_year_window(native):
    result = asyncio.run(
        profile.profile(
            None,
            groups=["inference"],
            from_value="2024-01-01",
            to_value="2024-12-31",
            repeats=1,
            concurrency=1,
            max_queries=1,
        )
    )
    assert result["days"] == native.window.days == 366
    assert result["start"] == "2024-01-01" and result["end"] == "2024-12-31"
    assert result["from"] == "2024-01-01" and result["to"] == "2024-12-31"


def test_profile_costs_tag_concurrent_sections_without_exposing_sql_or_parameters(native):
    result = asyncio.run(
        profile.profile(
            None,
            groups=["inference", "publishing"],
            from_value="now-29d/d",
            to_value="now",
            repeats=2,
            concurrency=2,
            max_queries=4,
            collect_costs=True,
        )
    )
    assert native.closed and native.flushes == 1 and native.peak == 2
    assert {row["section"] for row in result["query_costs"]} == {"inference", "publishing"}
    assert sum(row["queries"] for row in result["query_costs"]) == result["queries"] == 4
    assert result["metadata_queries"] == 3
    assert all(row["read_rows"] == 20 for row in result["query_costs"])
    assert all(row["read_bytes"] == 200 for row in result["query_costs"])
    assert all(row["peak_query_memory_bytes"] == 2000 for row in result["query_costs"])
    assert "SELECT count" not in json.dumps(result)
    assert profile.SECTION.get() == "metadata"


def test_profile_refuses_cost_artifact_when_query_log_is_incomplete(native):
    native.omit_log = True
    with pytest.raises(RuntimeError, match="did not capture every"):
        asyncio.run(
            profile.profile(
                None,
                groups=["inference"],
                from_value="now-29d/d",
                to_value="now",
                repeats=1,
                concurrency=1,
                max_queries=1,
                collect_costs=True,
            )
        )
    assert native.closed and native.flushes == 1


def test_profile_refuses_incomplete_history_and_closes_client(native):
    native.ready = False
    with pytest.raises(AnalyticsUnavailable, match="backfill"):
        asyncio.run(
            profile.profile(
                None,
                groups=["inference"],
                from_value="now-29d/d",
                to_value="now",
                repeats=1,
                concurrency=1,
                max_queries=10,
            )
        )
    assert not native.queries and native.closed


def test_query_budget_stops_before_submitting_an_extra_query(native):
    with pytest.raises(RuntimeError, match="query budget exhausted"):
        asyncio.run(
            profile.profile(
                None,
                groups=["inference"],
                from_value="now-29d/d",
                to_value="now",
                repeats=2,
                concurrency=1,
                max_queries=1,
            )
        )
    assert len(native.queries) == 1 and native.closed


def test_cli_uses_only_validated_test_settings_and_prints_json(monkeypatch, native, capsys):
    monkeypatch.setattr(
        profile,
        "analytics_environment",
        lambda: {
            "DEVFEED_CLICKHOUSE_URL": "http://127.0.0.1:8123",
            "DEVFEED_CLICKHOUSE_DATABASE": "analytics_test",
            "DEVFEED_CLICKHOUSE_USERNAME": "fixture",
            "DEVFEED_CLICKHOUSE_PASSWORD": "fixture-private-password",
        },
    )
    monkeypatch.setattr("sys.argv", ["profile-analytics", "--group", "inference", "--repeats", "1"])
    assert profile.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result["reports"]) == {"inference"}
    assert result["queries"] == 1
    assert "fixture-private-password" not in json.dumps(result)
