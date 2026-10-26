"""Custom reporting windows remain replaceable, bounded caches."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from devfeed_admin_api import report_retention as retention

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


class Factory:
    def __init__(self, results=(), *, fail=False):
        self.results = iter(results)
        self.calls = []
        self.transactions = 0
        self.fail = fail

    @contextmanager
    def begin(self):
        self.transactions += 1
        yield self

    def execute(self, statement, parameters=None):
        query = str(statement)
        self.calls.append((query, parameters))
        if self.fail:
            raise RuntimeError("database unavailable")
        result = next(self.results, []) if "DELETE FROM" in query else []
        return SimpleNamespace(all=lambda: result)


def test_custom_cache_maintenance_expires_and_caps_each_table_on_a_fixed_cadence(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(retention, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    factory = Factory([["expired-demand"], ["overflow-demand"], [], ["overflow-document"]])
    janitor = retention.ReportRetention(factory)
    assert janitor.prune(now=NOW) == 3
    assert factory.transactions == 1
    commands = [query for query, _ in factory.calls]
    assert commands[:2] == ["SET LOCAL statement_timeout='2s'", "SET LOCAL lock_timeout='250ms'"]
    deletes = [(query, params) for query, params in factory.calls if "DELETE FROM" in query]
    assert len(deletes) == 4 and all("SKIP LOCKED" in query for query, _ in deletes)
    assert deletes[0][1]["cutoff"] == NOW - timedelta(days=7)
    assert deletes[0][1]["windows"] == ["2026-10-20:2026-10-27", "2026-09-27:2026-10-27"]
    assert deletes[0][1]["retained"] == 1013
    assert deletes[1][1]["limit"] == 99
    assert "c.lease_until" in deletes[0][0]
    assert "NOT EXISTS(SELECT 1 FROM report_refreshes" in deletes[2][0]
    assert "AS MATERIALIZED" in deletes[1][0]
    assert "c.last_requested_at=e.last_requested_at" in deletes[1][0]
    clock[0] = 399.9
    assert janitor.prune(now=NOW) == 0 and factory.transactions == 1
    clock[0] = 400
    assert janitor.prune(now=NOW) == 0 and factory.transactions == 2


def test_expiry_stops_at_the_per_table_batch_before_attempting_lru_eviction(monkeypatch):
    monkeypatch.setattr(retention, "utcnow", lambda: NOW)
    factory = Factory([["old"] * 100, ["old"] * 100])
    assert retention.ReportRetention(factory).prune() == 200
    deletes = [query for query, _ in factory.calls if "DELETE FROM" in query]
    assert len(deletes) == 2 and not any("overflow" in query for query in deletes)


def test_maintenance_failure_does_not_retry_on_every_report_poll():
    factory = Factory(fail=True)
    janitor = retention.ReportRetention(factory)
    with pytest.raises(RuntimeError, match="unavailable"):
        janitor.prune(now=NOW)
    assert janitor.prune(now=NOW) == 0 and factory.transactions == 1


@pytest.mark.parametrize("limit", [0, 101])
def test_maintenance_rejects_an_unbounded_batch_without_opening_the_database(limit):
    factory = Factory()
    with pytest.raises(ValueError, match="between 1 and 100"):
        retention.ReportRetention(factory).prune(now=NOW, limit=limit)
    assert not factory.calls and not factory.transactions


@pytest.mark.integration
def test_expiry_and_lru_keep_active_leases_and_current_defaults_without_touching_history(database):
    from devfeed_core.models import ReportRefresh, ReportSnapshot
    from sqlalchemy import func, select

    def snapshot(start, *, requested, group="publishing", window_key=None):
        end = start + timedelta(days=1)
        return ReportSnapshot(
            group=group,
            window_key=window_key or f"{start}:{end}",
            window_start=start,
            window_end=end,
            days=1,
            payload={"retained": True},
            generated_at=NOW,
            policy_version="retention-test",
            last_requested_at=requested,
        )

    old = NOW - timedelta(days=8)
    earliest = NOW.date() - timedelta(days=3000)
    protected_start, protected_end = NOW.date() - timedelta(days=6), NOW.date() + timedelta(days=1)
    with database.begin() as session:
        session.add_all(
            [snapshot(earliest + timedelta(days=n), requested=NOW) for n in range(1030)]
        )
        expired = snapshot(earliest - timedelta(days=1), requested=old)
        active = snapshot(earliest - timedelta(days=2), requested=old)
        protected = ReportSnapshot(
            group="publishing",
            window_key=f"{protected_start}:{protected_end}",
            window_start=protected_start,
            window_end=protected_end,
            days=7,
            payload={"retained": True},
            generated_at=NOW,
            policy_version="retention-test",
            last_requested_at=old,
        )
        session.add_all([expired, active, protected])
        for item, lease in (
            (expired, None),
            (active, NOW + timedelta(seconds=30)),
            (protected, None),
        ):
            session.add(
                ReportRefresh(
                    group=item.group,
                    window_key=item.window_key,
                    window_start=item.window_start,
                    window_end=item.window_end,
                    days=item.days,
                    last_requested_at=old,
                    requested_at=old,
                    available_at=old,
                    lease_until=lease,
                )
            )
        active_key, expired_key, protected_key = (
            active.window_key,
            expired.window_key,
            protected.window_key,
        )
    removed = retention.ReportRetention(database).prune(now=NOW)
    assert 2 < removed <= 200
    with database() as session:
        assert session.get(ReportSnapshot, ("publishing", expired_key)) is None
        assert session.get(ReportRefresh, ("publishing", expired_key)) is None
        for key in (active_key, protected_key):
            assert session.get(ReportSnapshot, ("publishing", key)) is not None
            assert session.get(ReportRefresh, ("publishing", key)) is not None
        assert session.scalar(select(func.count()).select_from(ReportSnapshot)) <= 1024
