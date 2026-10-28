"""One asynchronous ClickHouse client per HTTP service process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from devfeed_core.analytics import AnalyticsClient
from fastapi import FastAPI, Request


@asynccontextmanager
async def analytics_resources(app: FastAPI) -> AsyncIterator[None]:
    client = AnalyticsClient()
    app.state.analytics_client = client
    try:
        yield
    finally:
        await client.close()


def analytics_client(request: Request) -> AnalyticsClient:
    return cast(AnalyticsClient, request.app.state.analytics_client)
