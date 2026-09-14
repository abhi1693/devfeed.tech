"""Per-chart asynchronous snapshots with bounded reporting concurrency."""

import asyncio
import time
from contextlib import suppress
from typing import Annotated

from devfeed_core.cache import CacheUnavailable, get_cache
from devfeed_core.config import get_settings
from devfeed_core.models import utcnow
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.overview import refreshing
from devfeed_admin_api.overview_panel_data import GROUPS, OverviewPanel, PanelName, panel_data
from devfeed_admin_api.reporting import reporting_sessions

router = APIRouter(prefix="/v1/admin/overview/panels", tags=["admin-overview"])
FRESH_SECONDS = 60
MAX_AGE_SECONDS = 300
WAIT_SECONDS = 5


def load_panel(sessions, group, days):
    with sessions() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '10s'"))
        session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '10s'"))
        return panel_data(session, group, days, utcnow())


async def refresh_panel(state, sessions, group, days):
    key = (group, days)
    lookup = None
    cache = None
    try:
        cache = await run_in_threadpool(get_cache) if get_settings().cache_enabled else None
        deadline = time.monotonic() + 35
        while cache is not None:
            try:
                lookup = await run_in_threadpool(
                    cache.lookup, f"admin-panel-v1:{group}:{days}", "admin-overview-panels"
                )
            except CacheUnavailable:
                break
            if lookup.body is not None:
                try:
                    result = OverviewPanel.model_validate_json(lookup.body)
                    state.overview_panel_snapshots[key] = result
                    return result
                except ValueError:
                    # Corrupt payloads do not trigger an unbounded duplicate load.
                    raise refreshing() from None
            if lookup.token:
                break
            if time.monotonic() >= deadline:
                raise refreshing()
            await asyncio.sleep(0.1)
        async with state.overview_panel_slots:
            result = await run_in_threadpool(load_panel, sessions, group, days)
        state.overview_panel_snapshots[key] = result
        if cache is not None and lookup is not None:
            with suppress(CacheUnavailable):
                await run_in_threadpool(
                    cache.publish, lookup, result.model_dump_json().encode(), FRESH_SECONDS
                )
        return result
    finally:
        if cache is not None and lookup is not None:
            with suppress(CacheUnavailable):
                await run_in_threadpool(cache.release, lookup)
        state.overview_panel_tasks.pop(key, None)


@router.get("/{panel}", response_model=OverviewPanel, operation_id="admin_overview_panel")
async def overview_panel(
    panel: PanelName,
    request: Request,
    response: Response,
    admin: Admin,
    sessions: Annotated[sessionmaker[Session], Depends(reporting_sessions)],
    days: int = Query(default=30, ge=1, le=90),
):
    state = request.app.state
    group = GROUPS.get(panel, panel)
    key = (group, days)
    cached = state.overview_panel_snapshots.get(key)
    age = (utcnow() - cached.generated_at).total_seconds() if cached else float("inf")
    if age < FRESH_SECONDS:
        return cached
    task = state.overview_panel_tasks.get(key)
    if task is None:
        # Drop expired local ranges; Redis and each chart remain independently keyed.
        state.overview_panel_snapshots = {
            k: v
            for k, v in state.overview_panel_snapshots.items()
            if (utcnow() - v.generated_at).total_seconds() < MAX_AGE_SECONDS
        }
        task = asyncio.create_task(refresh_panel(state, sessions, group, days))
        state.overview_panel_tasks[key] = task
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    if age < MAX_AGE_SECONDS:
        response.headers["X-Overview-Stale"] = "true"
        response.headers["X-Overview-Age-Seconds"] = str(int(age))
        return cached
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=WAIT_SECONDS)
    except TimeoutError:
        raise refreshing() from None


async def close_panel_tasks(app):
    await asyncio.gather(*list(app.state.overview_panel_tasks.values()), return_exceptions=True)
