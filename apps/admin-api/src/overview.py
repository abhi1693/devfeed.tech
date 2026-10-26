"""Independent card reads over shared durable analytical documents."""

from typing import Annotated

from devfeed_core.models import utcnow
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.overview_charts import (
    CHART_SECTIONS,
    ChartName,
    OverviewChartReport,
    chart_report,
)
from devfeed_admin_api.overview_projection import read_report
from devfeed_admin_api.overview_sections import OverviewSectionReport
from devfeed_admin_api.overview_windows import current_window, parse_report_window
from devfeed_admin_api.reporting import reporting_sessions as session_factory

router = APIRouter(prefix="/v1/admin/overview", tags=["admin-overview"])


def validate_report_query(request: Request):
    unknown = set(request.query_params) - {"from", "to"}
    if unknown:
        raise HTTPException(
            422, detail=f"Unknown Overview query parameters: {', '.join(sorted(unknown))}"
        )


@router.get(
    "/charts/{chart}",
    response_model=OverviewChartReport,
    operation_id="admin_overview_chart",
    dependencies=[Depends(validate_report_query)],
)
async def overview_chart(
    chart: ChartName,
    response: Response,
    admin: Admin,
    sessions: Annotated[sessionmaker[Session], Depends(session_factory)],
    from_value: str | None = Query(
        default=None,
        alias="from",
        min_length=1,
        max_length=64,
        description="Inclusive UTC start: ISO date or Grafana date math",
    ),
    to_value: str | None = Query(
        default=None,
        alias="to",
        min_length=1,
        max_length=64,
        description="Inclusive UTC end: ISO date or Grafana date math",
    ),
):
    try:
        window = parse_report_window(from_value, to_value, utcnow())
        if chart == "attention":
            window = current_window()
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error
    result = await run_in_threadpool(
        read_report, sessions, CHART_SECTIONS[chart], window, OverviewSectionReport
    )
    if result.snapshot is not None and result.snapshot.status != "fresh":
        response.headers["X-Overview-Stale"] = "true"
        response.headers["X-Overview-Age-Seconds"] = str(int(result.snapshot.lag_seconds))
    return chart_report(chart, result)
