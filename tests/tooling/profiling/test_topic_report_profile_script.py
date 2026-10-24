"""The topic profiler bounds disposable work and measures the actual report SQL."""

import json
import sys

import pytest
from devfeed_core.inference_metrics import inference_charts
from devfeed_core.topic_decision_metrics import decision_metrics
from sqlalchemy import text

from scripts import profile_topic_reports as profile


@pytest.fixture(autouse=True)
def clear_libpq_routes(monkeypatch):
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://test@production.invalid/devfeed_test",
        "postgresql://test@localhost/devfeed",
        "postgresql://test@localhost",
        "postgresql://test@/devfeed_test",
        "sqlite://localhost/devfeed_test",
        "postgresql://test@localhost/devfeed_test?host=production.invalid",
        "postgresql://test@localhost/devfeed_test?hostaddr=192.0.2.1",
        "postgresql://test@localhost/devfeed_test?service=production",
        "postgresql://test@localhost/devfeed_test?options=-csearch_path%3Dother",
    ],
)
def test_profiler_rejects_nonlocal_databases_and_connection_query_overrides(url):
    with pytest.raises(ValueError, match="explicitly local PostgreSQL"):
        profile.validate_database(url)


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_profiler_rejects_libpq_routing_overrides(monkeypatch, name):
    monkeypatch.setenv(name, "unexpected-route")
    with pytest.raises(ValueError, match="routing overrides"):
        profile.validate_database("postgresql+psycopg://test@127.0.0.1/devfeed_test")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_profiler_accepts_explicit_local_disposable_database(host):
    value = f"postgresql+psycopg://test@{host}:55499/devfeed_test"
    assert str(profile.validate_database(value)) == value


@pytest.mark.parametrize(
    "options",
    [
        ["--articles", "9"],
        ["--articles", "1000001"],
        ["--runs", "9"],
        ["--runs", "100001"],
        ["--history-bytes", "-1"],
        ["--history-bytes", "40001"],
        ["--repeats", "0"],
        ["--repeats", "11"],
        ["--workers", "0"],
        ["--workers", "9"],
        ["--writes", "0"],
        ["--writes", "1001"],
    ],
)
def test_cli_rejects_unbounded_work_before_loading_code_or_connecting(
    monkeypatch, tmp_path, capsys, options
):
    monkeypatch.setenv("DEVFEED_TEST_DATABASE_URL", "postgresql://test@localhost/devfeed_test")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "topic-profiler",
            "--baseline-root",
            str(tmp_path),
            "--output",
            str(tmp_path / "result.json"),
            *options,
        ],
    )
    monkeypatch.setattr(profile, "create_engine", lambda *_a, **_kw: pytest.fail("Opened database"))
    monkeypatch.setattr(profile, "report_functions", lambda *_a: pytest.fail("Loaded report code"))
    with pytest.raises(SystemExit) as error:
        profile.main()
    assert error.value.code == 2
    assert "profiling bounds" in capsys.readouterr().err
    assert not (tmp_path / "result.json").exists()


def fixture_snapshot(session):
    return {
        "articles": session.scalar(text("SELECT count(*) FROM articles")),
        "runs": session.execute(
            text("""SELECT proposal_id,state,updated_at
            FROM topic_decision_runs ORDER BY proposal_id""")
        ).all(),
        "facts": session.execute(
            text("""SELECT * FROM topic_decision_calls
            ORDER BY proposal_id,ordinal""")
        ).all(),
        "inference": session.scalar(text("SELECT count(*) FROM inference_calls")),
    }


@pytest.mark.integration
def test_seed_refuses_existing_history_and_report_sample_captures_actual_plans(database):
    with database() as session:
        engine = session.get_bind()
    profile.seed(engine, articles=20, runs=20, history_bytes=64)
    with database() as session:
        before = fixture_snapshot(session)
    with pytest.raises(ValueError, match="requires empty fixture tables"):
        profile.seed(engine, articles=20, runs=20, history_bytes=64)
    loaded = profile.report_functions(profile.ROOT, "test-direct")
    assert loaded[0] is not decision_metrics and loaded[1] is not inference_charts
    actual = profile.report_sample(engine, loaded, days=30, explain=True)
    expected = profile.report_sample(engine, [decision_metrics, inference_charts], days=30)
    assert actual["output"] == expected["output"]
    assert actual["output"][0]["calls"] == 60
    assert len(actual["output"][1]["topic_activity"]) == 31
    assert len(actual["plans"]) == 2
    assert actual["plan_totals"]["execution_ms"] >= 0
    assert actual["plan_totals"]["temp_written_blocks"] == 0
    assert all("Execution Time" in plan and "Plan" in plan for plan in actual["plans"])
    assert any("topic_decision_calls" in json.dumps(plan) for plan in actual["plans"])
    with database() as session:
        assert fixture_snapshot(session) == before


@pytest.mark.integration
@pytest.mark.parametrize("fail", [False, True])
def test_cli_preserves_facts_and_restores_projection_after_concurrent_samples(
    database, monkeypatch, tmp_path, capsys, fail
):
    with database() as session:
        engine = session.get_bind()
    profile.seed(engine, articles=10, runs=10, history_bytes=64)
    with database() as session:
        before = fixture_snapshot(session)
    output = tmp_path / "profile.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "topic-profiler",
            "--baseline-root",
            str(profile.ROOT),
            "--output",
            str(output),
            "--repeats",
            "1",
            "--workers",
            "2",
            "--writes",
            "2",
        ],
    )
    if fail:

        def failure(*_args, **_kwargs):
            raise RuntimeError("Concurrent reporter failed")

        monkeypatch.setattr(profile, "concurrent", failure)
        with pytest.raises(RuntimeError, match="Concurrent reporter failed"):
            profile.main()
        assert not output.exists()
    else:
        profile.main()
        assert json.loads(capsys.readouterr().out)["fixture"]["articles"] == 10
        result = json.loads(output.read_text())
        assert [report["days"] for report in result["reports"]] == [1, 7, 30, 90]
        assert all(report["exact_output_parity"] for report in result["reports"])
        for sample in result["concurrent"].values():
            assert sample["ingestion"]["committed"] is False
            assert sample["ingestion"]["iterations"] == 2
            assert sample["report_pairs"] == 2
            assert len(sample["plans"]) == 2
    with database() as session:
        assert fixture_snapshot(session) == before
        assert (
            session.scalar(
                text("""SELECT tgenabled FROM pg_trigger
            WHERE tgrelid='topic_decision_runs'::regclass
            AND tgname='topic_decision_calls_sync'""")
            )
            == "O"
        )
