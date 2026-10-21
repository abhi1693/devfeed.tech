"""Compact daily job facts, maintained independently of report date ranges."""

from datetime import datetime, time, timedelta

from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.models import ReadModelState
from devfeed_core.read_models import set_state
from sqlalchemy import func, literal, select, text, union_all

STATE_NAME = "overview-processing"
VERSION = "processing-daily-v1"


def refresh_processing_facts(factory, now):
    """Read narrow indexed completion windows, plus the current active queues.

    Closed days survive job retention. Today and yesterday are reconciled each
    minute; one older day rotates through reconciliation to capture late edits.
    No date-range request reruns these aggregates.
    """
    from devfeed_admin_api.reporting import report_budget

    with factory.begin() as session, report_budget(session):
        session.execute(text("SET LOCAL statement_timeout = '10s'"))
        if not session.scalar(select(func.pg_try_advisory_xact_lock(284092))):
            return False
        stored = session.get(ReadModelState, STATE_NAME)
        if (
            stored is not None
            and stored.payload.get("version") == VERSION
            and stored.updated_at > now - timedelta(seconds=60)
        ):
            return False
        first = now.date() - timedelta(days=89)
        previous = stored.payload if stored and stored.payload.get("version") == VERSION else {}
        daily = {
            day: values for day, values in previous.get("daily", {}).items() if day >= str(first)
        }
        missing = [
            first + timedelta(days=index)
            for index in range(90)
            if str(first + timedelta(days=index)) not in daily
        ]
        # Initial backfill is one bounded 90-day window, not 90 scans.
        start_day = min(missing) if missing else now.date() - timedelta(days=1)
        windows = [(start_day, now.date())]
        old_days = [first + timedelta(days=index) for index in range(88)]
        cursor = int(previous.get("cursor", 0)) % len(old_days)
        if not missing:
            windows.append((old_days[cursor], old_days[cursor]))
        for lower, upper in windows:
            for index in range((upper - lower).days + 1):
                daily[str(lower + timedelta(days=index))] = {}
            start = datetime.combine(lower, time.min, tzinfo=now.tzinfo)
            end = datetime.combine(upper + timedelta(days=1), time.min, tzinfo=now.tzinfo)
            statements = []
            for kind, definition in JOB_DEFINITIONS.items():
                model = definition.model
                duration = getattr(model, "duration_ms", literal(0))
                day = func.date(func.timezone("UTC", model.finished_at))
                statements.append(
                    select(
                        literal(kind).label("kind"),
                        day.label("day"),
                        func.count().filter(model.status == "succeeded").label("completed"),
                        func.count().filter(model.status == "failed").label("failed"),
                        func.coalesce(func.sum(duration).filter(duration > 0), 0).label(
                            "duration_ms"
                        ),
                        func.count().filter(duration > 0).label("duration_runs"),
                    )
                    .where(
                        model.finished_at >= start,
                        model.finished_at < end,
                        model.finished_at <= now,
                    )
                    .group_by(day)
                )
            for row in session.execute(union_all(*statements)).mappings():
                daily[str(row["day"])][row["kind"]] = {
                    name: int(row[name])
                    for name in ("completed", "failed", "duration_ms", "duration_runs")
                }
        active = {}
        statements = []
        for kind, definition in JOB_DEFINITIONS.items():
            model = definition.model
            statements.append(
                select(
                    literal(kind).label("kind"),
                    func.count().filter(model.status == "queued").label("queued"),
                    func.count().filter(model.status == "running").label("running"),
                    func.min(model.created_at)
                    .filter(model.status == "queued")
                    .label("oldest_queued_at"),
                ).where(model.status.in_(("queued", "running")))
            )
        for row in session.execute(union_all(*statements)).mappings():
            active[row["kind"]] = {
                "queued": row["queued"],
                "running": row["running"],
                "oldest_queued_at": row["oldest_queued_at"].isoformat()
                if row["oldest_queued_at"]
                else None,
            }
        set_state(
            session,
            STATE_NAME,
            {"version": VERSION, "daily": daily, "active": active, "cursor": cursor + 1},
        )
        # set_state's real clock is normally identical; retaining an explicit
        # clock makes publisher recovery and date-boundary tests deterministic.
        session.flush()
        session.get(ReadModelState, STATE_NAME).updated_at = now
        return True


def processing_facts(session, start, now):
    """Return compact facts or None for legacy direct calculation callers."""
    stored = session.get(ReadModelState, STATE_NAME)
    if stored is None or stored.payload.get("version") != VERSION:
        return None
    daily = stored.payload["daily"]
    if str(start.date()) not in daily or str(now.date()) not in daily:
        return None
    output = []
    for kind in JOB_DEFINITIONS:
        values = dict(stored.payload["active"].get(kind, {}))
        values.update(kind=kind, completed=0, failed=0, duration_ms=0, duration_runs=0)
        for day, counts in daily.items():
            if str(start.date()) <= day <= str(now.date()):
                for field in ("completed", "failed", "duration_ms", "duration_runs"):
                    values[field] += counts.get(kind, {}).get(field, 0)
        output.append(values)
    return output
