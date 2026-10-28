"""Independent chart contracts over bounded ClickHouse analytical reads."""

from typing import Annotated

from devfeed_core.models import utcnow
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from devfeed_admin_api.analytics_runtime import AnalyticsRuntime, get_analytics
from devfeed_admin_api.auth import Admin
from devfeed_admin_api.overview_charts import (
    CHART_SECTIONS,
    ChartName,
    OverviewChartReport,
    chart_report,
)
from devfeed_admin_api.overview_windows import current_window, parse_report_window

router = APIRouter(prefix="/v1/admin/overview", tags=["admin-overview"])


def validate_report_query(request: Request) -> None:
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
    analytics: Annotated[AnalyticsRuntime, Depends(get_analytics)],
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
) -> OverviewChartReport:
    try:
        window = parse_report_window(from_value, to_value, utcnow())
        if chart == "attention":
            window = current_window()
    except ValueError as error:
        raise HTTPException(422, detail=str(error)) from error
    result = await analytics.section(CHART_SECTIONS[chart], window)
    if result.snapshot is not None and result.snapshot.status != "fresh":
        response.headers["X-Overview-Stale"] = "true"
        response.headers["X-Overview-Age-Seconds"] = str(int(result.snapshot.lag_seconds or 0))
    return chart_report(chart, result)
