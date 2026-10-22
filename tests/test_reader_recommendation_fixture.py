"""Recommendation evidence uses real domain preparation and refuses fallback payloads."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


fixture = module("reader_recommendation_fixture")


def ready_payload(start=0, count=24):
    identifiers = [str(index) for index in range(start, start + count)]
    return {
        "status": "ready",
        "refresh_state": "idle",
        "feed_kind": "personalized",
        "has_interests": True,
        "generation": "actual-redis-generation",
        "items": [{"id": identifier} for identifier in identifiers],
        "reasons": {identifier: {"kind": "followed_source"} for identifier in identifiers},
        "next_cursor": "server-issued-cursor",
    }


def ready_proof():
    return {
        "qualified": True,
        "generation": "actual-redis-generation",
        "materialized_count": 100,
        "sequence_ids": [str(index) for index in range(100)],
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "refreshing"},
        {"refresh_state": "preparing"},
        {"feed_kind": "latest"},
        {"generation": "database-fallback-generation"},
        {"has_interests": False},
        {"items": []},
        {"reasons": {}},
        {"next_cursor": None},
    ],
)
def test_ready_qualification_rejects_empty_stale_and_fallback_responses(changes):
    with pytest.raises(AssertionError):
        fixture.validate_ready_page(ready_payload() | changes, ready_proof())


def test_ready_qualification_requires_disjoint_real_generation_pages():
    first = fixture.validate_ready_page(ready_payload(), ready_proof())
    assert fixture.validate_ready_page(ready_payload(24), ready_proof(), previous_ids=first)
    with pytest.raises(AssertionError):
        fixture.validate_ready_page(ready_payload(), ready_proof(), previous_ids=first)
    with pytest.raises(AssertionError):
        fixture.validate_ready_page(ready_payload(), ready_proof() | {"qualified": False})
    with pytest.raises(AssertionError):
        fixture.validate_ready_page(ready_payload(), ready_proof() | {"sequence_ids": []})


def test_pending_qualification_rejects_latest_ready_and_retry_fast_paths():
    payload = ready_payload() | {
        "status": "refreshing",
        "refresh_state": "preparing",
        "feed_kind": "following",
        "generation": None,
        "next_cursor": None,
    }
    assert len(fixture.validate_pending_page(payload)) == 24
    original = copy.deepcopy(payload)
    for changes in (
        {"feed_kind": "latest"},
        {"status": "ready"},
        {"refresh_state": "retrying"},
        {"items": []},
    ):
        with pytest.raises(AssertionError):
            fixture.validate_pending_page(payload | changes)
    assert payload == original


def test_payload_proof_guards_remain_active_under_optimized_python():
    program = """
import runpy, sys
module = runpy.run_path(sys.argv[1])
calls = [
    (module['validate_pending_page'], [{'status': 'ready', 'refresh_state': 'idle'}]),
    (module['validate_ready_page'], [{}, {'qualified': False, 'generation': None}]),
]
for function, arguments in calls:
    try:
        function(*arguments)
    except AssertionError:
        pass
    else:
        raise SystemExit('Invalid payload accepted under optimized Python')
"""
    subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            program,
            str(ROOT / "scripts/reader_recommendation_fixture.py"),
        ],
        check=True,
        timeout=20,
    )


@pytest.mark.integration
def test_real_recommendation_fixture_prepares_worker_and_unmeasured_smoke_population(database):
    from devfeed_core.db import get_engine
    from devfeed_core.models import UserAccount, UserRecommendation

    profile = module("profile_reader_views")
    helper = profile.helper_module()
    engine = get_engine()
    profile.seed_fixture(engine, profile.fixture_counts(0.001), helper)
    report = fixture.prepare_fixture(engine, database, helper)
    assert report["cold_build"]["completed"]
    assert 0 < report["cold_build"]["returned_candidates"] <= 500
    assert report["cold_build"]["worker_statement_timeout"] == "5s"
    assert any("statement_timeout" in query["statement"] for query in report["cold_build"]["sql"])
    assert all(query["status"] == "completed" for query in report["cold_build"]["sql"])
    qualified = report["qualification"]
    assert qualified["pending"]["qualified"] and qualified["ready"]["qualified"]
    assert qualified["population"]["states"] == 25
    assert not qualified["above_production"]
    assert qualified["filler"]["qualified"]
    assert qualified["filler"]["accounts"] == 20
    assert 0 < report["fixture"]["candidates_per_account"] < 500
    assert qualified["filler"]["rows"] == report["fixture"]["expected_rows"]
    assert report["ready_proof"]["generation"]
    assert "synthetic precomputed" in report["fixture"]["origin"]
    with database() as session:
        before = list(session.execute(select(UserAccount.id, UserAccount.last_seen_at)))
        recommendations = session.scalar(select(func.count()).select_from(UserRecommendation))
    assert fixture.qualification(database, helper) == qualified
    with database() as session:
        assert list(session.execute(select(UserAccount.id, UserAccount.last_seen_at))) == before
        assert (
            session.scalar(select(func.count()).select_from(UserRecommendation)) == recommendations
        )
    json.dumps(report)


@pytest.mark.integration
def test_preparation_refuses_unfollowed_filler_without_populating_recommendations(database):
    from devfeed_core.db import get_engine
    from devfeed_core.models import UserRecommendation, UserSource

    profile = module("profile_reader_views")
    helper = profile.helper_module()
    engine = get_engine()
    profile.seed_fixture(engine, profile.fixture_counts(0.001), helper)
    with database.begin() as session:
        session.execute(
            delete(UserSource).where(
                UserSource.user_id == helper.fixture_id("user_accounts", 6),
                UserSource.source_id == helper.fixture_id("sources", 3),
            )
        )
    with pytest.raises(ValueError, match="already follow"):
        fixture.prepare_fixture(engine, database, helper)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(UserRecommendation)) == 0


@pytest.mark.integration
def test_sql_timer_records_postgresql_timeout_without_exposing_bound_values(database):
    from devfeed_core.db import get_engine

    secret = "bound-values-must-not-appear-in-the-report"
    with (
        fixture.capture_sql(get_engine()) as records,
        pytest.raises(DBAPIError),
        database.begin() as session,
    ):
        session.execute(text("SET LOCAL statement_timeout='1ms'"))
        session.execute(
            text("SELECT pg_sleep(:delay), :private"), {"delay": 0.05, "private": secret}
        )
    assert records[-1]["status"] == "failed"
    assert records[-1]["sqlstate"] == "57014"
    assert records[-1]["ms"] > 0
    assert secret not in json.dumps(records)
