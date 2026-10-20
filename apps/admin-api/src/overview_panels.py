"""Fast private chart reads from durable, independently published reports."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.overview_panel_data import GROUPS, OverviewPanel, PanelName
from devfeed_admin_api.overview_projection import read_report
from devfeed_admin_api.reporting import reporting_sessions

router = APIRouter(prefix="/v1/admin/overview/panels", tags=["admin-overview"])


@router.get("/{panel}", response_model=OverviewPanel, operation_id="admin_overview_panel")
async def overview_panel(
    panel: PanelName,
    response: Response,
    admin: Admin,
    sessions: Annotated[sessionmaker[Session], Depends(reporting_sessions)],
    days: int = Query(default=30, ge=1, le=90),
):
    result = await run_in_threadpool(
        read_report, sessions, GROUPS.get(panel, panel), days, OverviewPanel
    )
    if result.snapshot is not None and result.snapshot.status != "fresh":
        response.headers["X-Overview-Stale"] = "true"
        response.headers["X-Overview-Age-Seconds"] = str(int(result.snapshot.lag_seconds))
    return result


async def close_panel_tasks(app):
    # Kept as a lifespan hook: the serving process owns no calculation tasks.
    pass
