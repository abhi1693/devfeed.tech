"""Independent chart contracts over bounded ClickHouse analytical reads."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.overview_charts import (
    ChartName,
    OverviewChartReport,
)

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
    raise HTTPException(410, "This analytics report has been retired.")
