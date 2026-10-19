"""Exercise profiling safeguards, report comparisons and transactional writer samples."""

import json
import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from scripts import profile_inference_reports as profile

NOW = datetime(2026, 10, 18, 19, tzinfo=UTC)


@pytest.mark.parametrize(
    "url,options,message",
    [
        ("postgresql://ci@production.invalid/devfeed_test", [], "disposable local database"),
        ("postgresql://ci@localhost/devfeed", [], "disposable local database"),
        ("postgresql://ci@localhost", [], "disposable local database"),
        ("postgresql://ci@localhost/devfeed_test", ["--rows", "0"], "profiling bounds"),
        ("postgresql://ci@localhost/devfeed_test", ["--rows", "1000001"], "profiling bounds"),
        ("postgresql://ci@localhost/devfeed_test", ["--decisions", "-1"], "profiling bounds"),
        ("postgresql://ci@localhost/devfeed_test", ["--decisions", "10001"], "profiling bounds"),
        ("postgresql://ci@localhost/devfeed_test", ["--repeats", "0"], "profiling bounds"),
        ("postgresql://ci@localhost/devfeed_test", ["--repeats", "11"], "profiling bounds"),
        ("postgresql://ci@localhost/devfeed_test", ["--writes", "-1"], "profiling bounds"),
        ("postgresql://ci@localhost/devfeed_test", ["--writes", "1001"], "profiling bounds"),
    ],
)
def test_cli_rejects_unsafe_database_and_unbounded_work_before_connecting(
    monkeypatch, capsys, url, options, message
):
    monkeypatch.setenv("DEVFEED_TEST_DATABASE_URL", url)
    monkeypatch.setattr(sys, "argv", ["profile-inference", *options])
    monkeypatch.setattr(
        profile, "create_engine", lambda *_: pytest.fail("Invalid profile opened a database")
    )
    with pytest.raises(SystemExit) as error:
        profile.main()
    assert error.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.integration
def test_cli_profiles_real_reports_and_leaves_writer_samples_rolled_back(
    database, monkeypatch, capsys
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile-inference",
            "--seed",
            "--rows",
            "120",
            "--decisions",
            "3",
            "--repeats",
            "2",
            "--writes",
            "2",
        ],
    )
    profile.main()
    result = json.loads(capsys.readouterr().out)
    assert [report["days"] for report in result["reports"]] == [30, 90]
    for report in result["reports"]:
        assert report["result_equality"] is True
        assert set(report["strategies"]) == {"ledger", "daily"}
        for strategy in report["strategies"].values():
            assert len(strategy["samples_ms"]) == 2
            assert strategy["median_ms"] >= 0
            assert strategy["activity_plan"]["execution_ms"] >= 0
    assert result["writes"]["invocations_per_strategy"] == 2
    assert set(result["writes"]["insert_and_finish_ms_per_invocation"]) == {"ledger", "daily"}
    with database() as session:
        assert session.scalar(text("SELECT count(*) FROM inference_calls")) == 120
        assert session.scalar(text("SELECT count(*) FROM topic_proposals")) == 3
        assert session.scalar(text("SELECT count(*) FROM topic_decision_runs")) == 3
        assert session.scalar(text("SELECT sum(calls) FROM inference_daily_usage")) == 120
        triggers = session.execute(
            text("""SELECT tgenabled FROM pg_trigger
                WHERE tgrelid = 'inference_calls'::regclass
                  AND tgname LIKE 'inference_daily_usage_%'""")
        ).scalars()
        assert list(triggers) == ["O"] * 4


@pytest.mark.integration
def test_cli_without_seed_or_writes_preserves_existing_fixture(database, monkeypatch, capsys):
    with database() as session:
        engine = session.get_bind()
    profile.seed(engine, NOW, rows=5, decisions=0)
    monkeypatch.setattr(sys, "argv", ["profile-inference", "--repeats", "1"])
    profile.main()
    output = json.loads(capsys.readouterr().out)
    assert "writes" not in output
    assert all(report["result_equality"] for report in output["reports"])
    with database() as session:
        assert session.scalar(text("SELECT count(*) FROM inference_calls")) == 5
        assert session.scalar(text("SELECT count(*) FROM topic_proposals")) == 0


@pytest.mark.integration
def test_report_profile_refuses_unequal_strategy_results(database, monkeypatch):
    original = profile.inference_charts

    def unequal(session, start, now):
        result = original(session, start, now)
        if session.legacy:
            result.first_recorded_at = NOW
        return result

    monkeypatch.setattr(profile, "inference_charts", unequal)
    with pytest.raises(AssertionError, match="Report results differ"):
        profile.report_profiles(database, NOW, repeats=1)


@pytest.mark.integration
def test_failed_writer_sample_restores_disabled_triggers(database, monkeypatch):
    from sqlalchemy.engine import Connection

    with database() as session:
        engine = session.get_bind()
    execute = Connection.execute

    def fail_after_insert(connection, statement, *args, **kwargs):
        if "UPDATE inference_calls SET status='returned'" in str(statement):
            raise RuntimeError("Writer sample failed")
        return execute(connection, statement, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", fail_after_insert)
    with pytest.raises(RuntimeError, match="Writer sample failed"):
        profile.write_profiles(engine, NOW, count=1)
    with database() as session:
        assert session.scalar(text("SELECT count(*) FROM inference_calls")) == 0
        triggers = session.execute(
            text("""SELECT tgenabled FROM pg_trigger
                WHERE tgrelid = 'inference_calls'::regclass
                  AND tgname LIKE 'inference_daily_usage_%'""")
        ).scalars()
        assert list(triggers) == ["O"] * 4
