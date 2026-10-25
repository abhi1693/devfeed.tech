"""The sustained workload reports combined cost and refuses incomplete evidence."""

import copy
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.support.paths import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
try:
    SPEC = importlib.util.spec_from_file_location(
        "profile_background_work", REPO_ROOT / "scripts/profile_background_work.py"
    )
    profile = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(profile)
    REPORT_SPEC = importlib.util.spec_from_file_location(
        "background_work_report", REPO_ROOT / "scripts/ci/background_work_report.py"
    )
    report = importlib.util.module_from_spec(REPORT_SPEC)
    REPORT_SPEC.loader.exec_module(report)
finally:
    sys.path.pop(0)


def phase():
    return {
        "name": "active",
        "seconds": 60,
        "writer_ms": [10, 20, 30],
        "ingested": 5,
        "planned_ingested": 5,
        "processes": {"scheduler": {"cpu_seconds": 1, "sampled_peak_rss_bytes": 1000000}},
        "database_cost": {"global_wal_position_bytes": 1000},
        "cost": {
            "total_exec_time": 1000,
            "shared_blks_hit": 100,
            "shared_blks_read": 10,
            "wal_bytes": 1000,
        },
        "backlog": {
            "oldest_event_seconds": 1,
            "automation_foreground_due": 0,
            "automation_maintenance_due": 0,
            "automation_foreground_invalidations": 0,
            "automation_maintenance_invalidations": 0,
        },
        "readers": {"board": {"p95_ms": 50, "status_counts": {200: 20}}},
    }


def verdict(phases):
    return profile.gates(
        phases,
        reader_p95_ms=1000,
        writer_p95_ms=1000,
        max_sql_seconds_per_minute=30,
        max_buffers_per_minute=1000,
        max_wal_bytes_per_minute=10000,
        max_event_age_seconds=60,
    )


@pytest.mark.parametrize(
    "regression",
    [
        "SQL",
        "buffers",
        "WAL",
        "lag",
        "writer",
        "reader",
        "errors",
        "CPU",
        "RSS",
        "maintenance",
        "offered",
        "foreground automation",
        "maintenance automation",
        "fanout progress",
        "automation instrumentation",
    ],
)
def test_combined_workload_rejects_shifted_cost_even_with_fast_reports(regression):
    row = phase()
    assert verdict([row])["passed"]
    if regression == "SQL":
        row["cost"]["total_exec_time"] = 31000
    elif regression == "buffers":
        row["cost"]["shared_blks_read"] = 1001
    elif regression == "WAL":
        row["cost"]["wal_bytes"] = 10001
    elif regression == "lag":
        row["backlog"]["oldest_event_seconds"] = 61
    elif regression == "writer":
        row["writer_ms"] = [2000]
    elif regression == "reader":
        row["readers"]["board"]["p95_ms"] = 1001
    elif regression == "errors":
        row["readers"]["board"]["status_counts"][503] = 1
    elif regression == "CPU":
        row["processes"]["scheduler"]["cpu_seconds"] = 121
    elif regression == "RSS":
        row["processes"]["scheduler"]["sampled_peak_rss_bytes"] = 2**32
    elif regression == "maintenance":
        row["database_cost"]["global_wal_position_bytes"] = 2**30
    elif regression == "foreground automation":
        row["backlog"]["oldest_automation_foreground_due_seconds"] = 61
    elif regression == "maintenance automation":
        row["backlog"]["oldest_automation_maintenance_due_seconds"] = 14401
    elif regression == "fanout progress":
        row["backlog"]["automation_maintenance_invalidation_progress_age_seconds"] = 61
    elif regression == "automation instrumentation":
        row["backlog"].pop("automation_foreground_due")
    else:
        row["ingested"] = 4
    assert not verdict([row])["passed"]


def test_actual_ai_automation_is_enabled_while_quota_provider_rpcs_stay_disabled(monkeypatch):
    from devfeed_aggregator import quota_monitor
    from devfeed_core import automation_scheduler
    from devfeed_core.config import Settings

    for key, value in profile.workload_environment(
        "postgresql+psycopg://test@127.0.0.1/profile_test", "redis://127.0.0.1/15"
    ).items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert settings.ai_enabled and settings.full_automation
    assert settings.codex_app_server_url == "ws://127.0.0.1:1"
    assert settings.codex_model == "benchmark"
    assert not settings.ai_quota_pacing_enabled
    monkeypatch.setattr(automation_scheduler, "get_settings", lambda: settings)
    monkeypatch.setattr(automation_scheduler, "schedule_tag_topic_discovery", lambda *_: {})
    monkeypatch.setattr(automation_scheduler, "schedule_source_admission", lambda *_: 0)
    entered = Mock(side_effect=RuntimeError("Real article automation admission entered"))
    monkeypatch.setattr(automation_scheduler, "schedule_article_automation", entered)
    factory = object()
    with pytest.raises(RuntimeError, match="Real article automation admission entered"):
        automation_scheduler.schedule_automation(factory)
    entered.assert_called_once_with(factory)
    monkeypatch.setattr(quota_monitor, "get_settings", lambda: settings)
    provider = Mock(side_effect=AssertionError("Unexpected provider RPC"))
    monkeypatch.setattr(quota_monitor, "CodexClient", provider)
    quota_monitor.refresh_quota()
    provider.assert_not_called()


@pytest.mark.parametrize(
    "key,lane",
    [
        ("events", "foreground"),
        ("fact_events", "foreground"),
        ("reports", "foreground"),
        ("report_errors", "foreground"),
        ("automation_foreground_invalidations", "foreground"),
        ("automation_foreground_due", "foreground"),
        ("catalog_events", "maintenance"),
        ("catalog_running", "maintenance"),
        ("automation_maintenance_invalidations", "maintenance"),
        ("automation_maintenance_due", "maintenance"),
    ],
)
def test_convergence_retains_article_debt_after_its_fanout_queue_is_empty(key, lane):
    assert profile.convergence_lanes({}) == {"foreground": True, "maintenance": True}
    state = {key: 1, "automation_waiting": 500}
    result = profile.convergence_lanes(state)
    assert result[lane] is False
    assert result["maintenance" if lane == "foreground" else "foreground"] is True


@pytest.mark.parametrize("lane", ["foreground", "maintenance"])
def test_each_convergence_lane_must_finish_inside_its_own_budget(lane):
    limits = {"foreground": 60, "maintenance": 14400}
    completed = {"foreground": 50, "maintenance": 14000}
    assert profile.convergence_outcome({}, completed, limits)["passed"]
    completed[lane] = limits[lane] + 1
    result = profile.convergence_outcome({}, completed, limits)
    assert not result["passed"] and not result["lanes"][lane]["passed"]
    completed[lane] = None
    assert not profile.convergence_outcome({}, completed, limits)["passed"]


def test_automation_backlog_keeps_both_due_debt_and_fanout_cursor_progress():
    now = datetime(2026, 10, 25, tzinfo=UTC)
    due, queue, cursor = Mock(), Mock(), Mock()
    due.mappings.return_value.one.return_value = {
        "automation_foreground_due": 3,
        "foreground_oldest": now - timedelta(seconds=10),
        "automation_maintenance_due": 5,
        "maintenance_oldest": now - timedelta(seconds=200),
        "automation_waiting": 99,
    }
    queue.mappings.return_value.one.return_value = {
        "automation_invalidations": 2,
        "automation_foreground_invalidations": 1,
        "foreground_oldest": now - timedelta(seconds=20),
        "foreground_progress": now - timedelta(seconds=2),
        "automation_maintenance_invalidations": 1,
        "maintenance_oldest": now - timedelta(seconds=300),
        "maintenance_progress": now - timedelta(seconds=3),
    }
    cursor.mappings.return_value = [{"key": "all", "revision": 4, "after_article_id": "cursor"}]
    session = Mock()
    session.execute.side_effect = [due, queue, cursor]
    result = profile.automation_backlog(session, now)
    assert result["automation_foreground_due"] == 3 and result["automation_maintenance_due"] == 5
    assert result["oldest_automation_foreground_due_seconds"] == 10
    assert result["oldest_automation_maintenance_due_seconds"] == 200
    assert result["oldest_automation_maintenance_invalidation_seconds"] == 300
    assert result["automation_maintenance_invalidation_progress_age_seconds"] == 3
    assert result["automation_invalidations"] == 2
    assert result["automation_invalidation_progress"][0]["after_article_id"] == "cursor"
    assert profile.convergence_lanes(result) == {"foreground": False, "maintenance": False}


def test_baseline_automation_backlog_does_not_require_new_models_or_wait_reasons(monkeypatch):
    from devfeed_core import models

    article = models.Article
    monkeypatch.setattr(
        models,
        "Article",
        SimpleNamespace(
            review_status=article.review_status,
            publication_status=article.publication_status,
            automation_next_check_at=article.automation_next_check_at,
        ),
    )
    monkeypatch.delattr(models, "AutomationInvalidation")
    session = Mock()
    session.execute.return_value.mappings.return_value.one.return_value = {
        "automation_foreground_due": 4,
        "foreground_oldest": None,
        "automation_maintenance_due": 0,
        "maintenance_oldest": None,
        "automation_waiting": 5,
    }
    result = profile.automation_backlog(session, datetime.now(UTC))
    assert not result["automation_invalidations_supported"]
    assert result["automation_invalidations"] == 0 and result["automation_foreground_due"] == 4
    assert session.execute.call_count == 1


def test_worktree_metadata_preserves_untracked_paths_spaces_and_rename_sources(monkeypatch):
    monkeypatch.setattr(
        profile.subprocess,
        "check_output",
        lambda *args, **kwargs: (
            " M scripts/profile_background_work.py\0"
            "R  renamed file.py\0old file.py\0?? new test.py\0"
        ),
    )
    assert profile.worktree_changes() == [
        "new test.py",
        "old file.py",
        "renamed file.py",
        "scripts/profile_background_work.py",
    ]


def test_combined_workload_requires_measured_time_and_normalizes_cost():
    assert not verdict([])["passed"]
    row = phase()
    row["seconds"] = 0
    assert not verdict([row])["passed"]
    row["seconds"] = 120
    row["cost"]["total_exec_time"] = 59000
    assert verdict([row])["passed"]
    row["seconds"] = 60
    assert not verdict([row])["passed"]


def test_statement_windows_do_not_count_lifetime_history_or_hide_resets():
    before = {"a": {"calls": 10, "total_exec_time": 50000}}
    after = {
        "a": dict.fromkeys(
            ("shared_blks_hit", "shared_blks_read", "temp_blks_written", "wal_bytes"), 0
        )
        | {"calls": 12, "total_exec_time": 50100}
    }
    assert profile.statement_delta(before, after)["total_exec_time"] == 100
    assert profile.statement_delta(before, after)["calls"] == 2
    after["a"]["calls"] = 0
    with pytest.raises(RuntimeError, match="reset"):
        profile.statement_delta(before, after)


def test_result_publication_replaces_link_without_touching_target(tmp_path):
    target = tmp_path / "private.json"
    target.write_text("private")
    output = tmp_path / "result.json"
    output.symlink_to(target)
    profile.write_result(output, {"complete": True, "path": Path("local")})
    assert target.read_text() == "private"
    assert not output.is_symlink()
    assert json.loads(output.read_text()) == {"complete": True, "path": "local"}


def completed_report():
    rows = []
    for name in ("active", "idle", "idle-expired", "recovery", "drain"):
        row = phase()
        row["name"] = name
        rows.append(row)
    return {
        "revision": "a" * 40,
        "harness_sha256": "c" * 64,
        "source_worktree_changes": [],
        "worktree_dirty": False,
        "complete": True,
        "qualification": "native-scale",
        "phases": rows,
        "gates": {"passed": True},
        "workload": {"scale": 1},
    }


@pytest.mark.parametrize(
    "incomplete", ["missing", "smoke", "commit", "cost", "volume", "workload", "nan"]
)
def test_comparison_rejects_incomplete_or_shifted_performance_evidence(incomplete):
    before = completed_report()
    after = copy.deepcopy(before)
    assert report.compare(before, after, candidate_sha="a" * 40)["passed"]
    if incomplete == "missing":
        after["phases"].pop(2)
    elif incomplete == "smoke":
        after["qualification"] = "smoke"
    elif incomplete == "commit":
        after["revision"] = "b" * 40
    elif incomplete == "cost":
        after["phases"][0]["processes"]["scheduler"]["cpu_seconds"] *= 2
    elif incomplete == "volume":
        after["phases"][0]["ingested"] = 1
    elif incomplete == "workload":
        after["workload"]["scale"] = 2
    else:
        after["phases"][0]["cost"]["total_exec_time"] = float("nan")
    assert not report.compare(before, after, candidate_sha="a" * 40)["passed"]


@pytest.mark.parametrize("change", ["dirty", "harness", "baseline", "unidentified"])
def test_comparison_requires_clean_subjects_and_identical_harness(change):
    before, after = completed_report(), completed_report()
    if change == "dirty":
        after["source_worktree_changes"] = ["packages/core/src/analysis.py"]
    elif change == "harness":
        after["harness_sha256"] = "d" * 64
    elif change == "baseline":
        before["worktree_dirty"] = True
        before["source_worktree_changes"] = ["packages/core/src/analysis.py"]
    else:
        before["worktree_dirty"] = True
    assert not report.compare(before, after)["passed"]


def test_comparison_accepts_negative_baseline_capacity_with_only_shared_harness_changes():
    before, after = completed_report(), completed_report()
    before["gates"]["passed"] = False
    before["worktree_dirty"] = True
    before["source_worktree_changes"] = ["scripts/profile_background_work.py"]
    assert report.compare(before, after)["passed"]
    # The finite drain's unchanged total work is not a regression when it
    # completes in a tenth of the time. Active phases still compare rates.
    after["phases"][-1]["seconds"] /= 10
    assert report.compare(before, after)["passed"]
    after["workload"]["maintenance_convergence_seconds"] = 86400
    assert not report.compare(before, after)["passed"]


@pytest.mark.parametrize("invalid", ["missing", "dirty", "phase", "CPU", "WAL", "commit"])
def test_scheduled_absolute_gate_requires_complete_attributed_cost(invalid):
    candidate = completed_report()
    assert report.absolute(candidate, candidate_sha="a" * 40)["passed"]
    if invalid == "missing":
        candidate["qualification"] = "smoke"
    elif invalid == "dirty":
        candidate["worktree_dirty"] = True
    elif invalid == "phase":
        candidate["phases"].pop()
    elif invalid == "CPU":
        candidate["phases"][0]["processes"] = {}
    elif invalid == "WAL":
        candidate["phases"][0]["database_cost"] = {}
    else:
        candidate["revision"] = "b" * 40
    assert not report.absolute(candidate, candidate_sha="a" * 40)["passed"]
