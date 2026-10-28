"""One asynchronous ClickHouse client per HTTP service process."""

from contextlib import asynccontextmanager

from devfeed_core.analytics import AnalyticsClient
from fastapi import Request


@asynccontextmanager
async def analytics_resources(app):
    client = AnalyticsClient()
    app.state.analytics_client = client
    try:
        yield
    finally:
        await client.close()


def analytics_client(request: Request) -> AnalyticsClient:
    return request.app.state.analytics_client
