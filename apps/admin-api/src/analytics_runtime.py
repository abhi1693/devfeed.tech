"""Process-wide analytical admission and bounded, coalesced Overview reads."""

import asyncio
import hashlib
import json
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_observation import AnalyticsSnapshot, pipeline_policy_version
from devfeed_core.config import get_settings
from devfeed_core.redis import create_redis
from fastapi import HTTPException, Request
from redis.backoff import NoBackoff
from redis.retry import Retry
from starlette.concurrency import run_in_threadpool

from devfeed_admin_api.analytics_current import CurrentCards, timestamp
from devfeed_admin_api.analytics_reports import Reports
from devfeed_admin_api.overview_sections import OverviewSectionReport

FRESH_SECONDS = 30
LAST_GOOD_SECONDS = 86400
MAX_ENTRIES = 64
MAX_CACHE_BYTES = 16_000_000
MAX_INFLIGHT = 32


def preparing():
    return HTTPException(
        503,
        {"code": "report_refreshing", "message": "This chart is being prepared."},
        headers={"Retry-After": "5"},
    )


class AnalyticsRuntime:
    def __init__(self, settings=None, *, client=None, redis=None, clock=None):
        settings = settings or get_settings()
        # All board and chart requests share these two slots, including connection
        # setup. A request never creates an independent ClickHouse query budget.
        self.client = client or AnalyticsClient(
            settings.model_copy(
                update={
                    "clickhouse_max_concurrent_queries": min(
                        2, settings.clickhouse_max_concurrent_queries
                    )
                }
            )
        )
        self._cache_redis = (
            create_redis(
                settings,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
                retry=Retry(NoBackoff(), 0),
                max_connections=4,
            )
            if settings.cache_enabled and redis is None
            else None
        )
        self.redis = (redis or (lambda: self._cache_redis)) if settings.cache_enabled else None
        self.clock = clock or (lambda: datetime.now(UTC))
        self.policy = pipeline_policy_version(settings)
        namespace = settings.database_url + ":" + settings.clickhouse_database
        self.prefix = "admin:analytics:v1:" + hashlib.sha256(namespace.encode()).hexdigest()[:20]
        self.max_entry_bytes = settings.cache_max_bytes
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.inflight = {}

    async def close(self):
        tasks = list(self.inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self.client.close()
        finally:
            if self._cache_redis is not None:
                await run_in_threadpool(self._cache_redis.close)

    def cards(self):
        return CurrentCards(self.client, now=self.clock())

    async def article_board(self, **parameters):
        try:
            async with asyncio.timeout(12):
                return await self.cards().article_board(**parameters)
        except TimeoutError as error:
            raise AnalyticsUnavailable("Pipeline request deadline reached") from error

    async def source_board(self, query, **parameters):
        try:
            async with asyncio.timeout(12):
                return await self.cards().source_board(query, **parameters)
        except TimeoutError as error:
            raise AnalyticsUnavailable("Pipeline request deadline reached") from error

    def _remember(self, key, value):
        size = len(value)
        if size > self.max_entry_bytes:
            return
        previous = self.cache.pop(key, None)
        self.cache_bytes -= len(previous) if previous is not None else 0
        self.cache[key] = value
        self.cache_bytes += size
        while len(self.cache) > MAX_ENTRIES or self.cache_bytes > MAX_CACHE_BYTES:
            _, removed = self.cache.popitem(last=False)
            self.cache_bytes -= len(removed)

    async def _redis(self, method, *args, **kwargs):
        redis = self.redis
        if redis is None:
            return None
        try:
            return await run_in_threadpool(lambda: getattr(redis(), method)(*args, **kwargs))
        except Exception:
            # Redis only improves cache survival; analytical reads remain available
            # when the optional cache has a connection or decoding failure.
            return None

    def _decode(self, value):
        if value is None:
            return None
        try:
            envelope = json.loads(value)
            if self.clock() >= timestamp(envelope["expires_at"]):
                return None
            report = OverviewSectionReport.model_validate(envelope["report"])
            return report, timestamp(envelope["fresh_until"])
        except (ValueError, TypeError, KeyError):
            return None

    def _observed(self, report, *, stale=False):
        report = report.model_copy(deep=True)
        if report.snapshot is not None:
            age = max(0, (self.clock() - report.generated_at).total_seconds())
            report.snapshot.lag_seconds = (report.snapshot.lag_seconds or 0) + age
            if stale or report.snapshot.lag_seconds > 60:
                report.snapshot.status = "stale"
        return report

    async def section(self, group, window):
        key = f"{self.prefix}:{self.policy}:{group}:{window.key}"
        value = self.cache.get(key)
        cached = self._decode(value)
        if cached is not None and self.clock() < cached[1]:
            self.cache.move_to_end(key)
            return self._observed(cached[0])
        task = self.inflight.get(key)
        if task is None:
            if len(self.inflight) >= MAX_INFLIGHT:
                if cached is not None:
                    return self._observed(cached[0], stale=True)
                raise preparing()
            task = asyncio.create_task(self._section(key, group, window, cached))
            self.inflight[key] = task
            task.add_done_callback(lambda completed: self.inflight.pop(key, None))
        # Client disconnects must not cancel a calculation shared by other cards.
        return self._observed(await asyncio.shield(task))

    async def _section(self, key, group, window, cached):
        if cached is None:
            value = await self._redis("get", key)
            cached = self._decode(value)
            if cached is not None:
                self._remember(key, value)
                if self.clock() < cached[1]:
                    return cached[0]
        try:
            async with asyncio.timeout(12):
                now = self.clock()
                if group == "operations":
                    report = await CurrentCards(self.client, now=now).operations()
                else:
                    status = await self.client.status()
                    if not status.get("ready"):
                        raise AnalyticsUnavailable("Analytics is being prepared")
                    report = await Reports(
                        self.client,
                        window,
                        now,
                        capture_started_at=timestamp(status["capture_started_at"]),
                    ).section(group)
                    lag = max(0, float(status.get("lag_seconds") or 0))
                    report.snapshot = AnalyticsSnapshot(
                        status="stale" if lag > 60 else "fresh",
                        generated_at=now,
                        generation=str(status.get("generation", status.get("version", ""))),
                        policy_version=self.policy,
                        lag_seconds=lag,
                    )
        except (AnalyticsUnavailable, TimeoutError, HTTPException) as error:
            if isinstance(error, HTTPException) and error.status_code != 503:
                raise
            if cached is not None:
                report = cached[0].model_copy(deep=True)
                if report.snapshot is not None:
                    report.snapshot.status = "stale"
                return report
            raise preparing() from error
        envelope = json.dumps(
            {
                "fresh_until": (now + timedelta(seconds=FRESH_SECONDS)).isoformat(),
                "expires_at": (now + timedelta(seconds=LAST_GOOD_SECONDS)).isoformat(),
                "report": report.model_dump(mode="json"),
            },
            separators=(",", ":"),
        ).encode()
        self._remember(key, envelope)
        if len(envelope) <= self.max_entry_bytes:
            await self._redis("set", key, envelope, ex=LAST_GOOD_SECONDS)
        return report


def get_analytics(request: Request) -> AnalyticsRuntime:
    return request.app.state.analytics
