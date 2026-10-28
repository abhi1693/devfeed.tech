"""Process-wide analytical admission and bounded, coalesced Overview reads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, Required, TypedDict, TypeVar, Unpack, overload

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_card_types import (
    PipelineBoard,
    SourcePipelineBoard,
    SourcePipelineStage,
    Stage,
)
from devfeed_core.analytics_observation import AnalyticsSnapshot, pipeline_policy_version
from devfeed_core.config import Settings, get_settings
from devfeed_core.redis import create_async_redis
from fastapi import HTTPException, Request
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from devfeed_admin_api.analytics_current import CurrentCards, SourceBoardQuery, preparing, timestamp
from devfeed_admin_api.analytics_reports import Reports
from devfeed_admin_api.overview_sections import OverviewSectionReport, SectionName
from devfeed_admin_api.overview_windows import ReportWindow

FRESH_SECONDS = 30
MAX_ENTRIES = 64
MAX_CACHE_BYTES = 16_000_000
MAX_INFLIGHT = 32


class CacheRedis(Protocol):
    def get(self, key: str, /) -> Awaitable[bytes | str | None]: ...
    def set(
        self, key: str, value: bytes, /, *, ex: int
    ) -> Awaitable[bool | bytes | str | None]: ...


class ArticleBoardParameters(TypedDict, total=False):
    q: Required[str | None]
    source_id: Required[uuid.UUID | None]
    needs_attention: Required[bool]
    limit: Required[int]
    stage: Stage | None
    cursor: int
    group_by: Literal["source"] | None
    source_group: uuid.UUID | Literal["unknown"] | None
    normalized_q: str | None


class SourceBoardParameters(TypedDict, total=False):
    stage: SourcePipelineStage | None
    cursor: int
    matching_ids: dict[str, list[uuid.UUID]] | None


Result = TypeVar("Result")


class AnalyticsRuntime:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: AnalyticsClient | None = None,
        redis: Callable[[], CacheRedis | None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
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
            create_async_redis(
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
        self.prefix = "admin:analytics:v2:" + hashlib.sha256(namespace.encode()).hexdigest()[:20]
        self.max_entry_bytes = settings.cache_max_bytes
        self.cache: OrderedDict[str, bytes | str] = OrderedDict()
        self.cache_bytes = 0
        self.inflight: dict[str, asyncio.Task[OverviewSectionReport]] = {}

    async def close(self) -> None:
        tasks = list(self.inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self.client.close()
        finally:
            if self._cache_redis is not None:
                await self._cache_redis.aclose()

    def cards(self) -> CurrentCards:
        return CurrentCards(self.client, now=self.clock())

    async def _board(self, operation: Awaitable[Result]) -> Result:
        try:
            async with asyncio.timeout(12):
                return await operation
        except TimeoutError as error:
            raise AnalyticsUnavailable("Pipeline request deadline reached") from error

    async def article_board(self, **parameters: Unpack[ArticleBoardParameters]) -> PipelineBoard:
        return await self._board(self.cards().article_board(**parameters))

    async def source_board(
        self, query: SourceBoardQuery, **parameters: Unpack[SourceBoardParameters]
    ) -> SourcePipelineBoard:
        return await self._board(self.cards().source_board(query, **parameters))

    def _remember(self, key: str, value: bytes | str) -> None:
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

    @overload
    async def _redis(self, method: Literal["get"], key: str) -> bytes | str | None: ...

    @overload
    async def _redis(self, method: Literal["set"], key: str, value: bytes, *, ex: int) -> None: ...

    async def _redis(
        self,
        method: Literal["get", "set"],
        key: str,
        value: bytes | None = None,
        *,
        ex: int = FRESH_SECONDS,
    ) -> bytes | str | None:
        redis = self.redis
        if redis is None:
            return None
        try:
            client = redis()
            if client is None:
                return None
            if method == "get":
                return await client.get(key)
            if value is None:
                return None
            await client.set(key, value, ex=ex)
        except Exception:
            # An unavailable cache is a miss; every result still comes from the
            # current ClickHouse calculation and its normal freshness window.
            return None
        return None

    def _decode(self, value: bytes | str | None) -> OverviewSectionReport | None:
        if value is None:
            return None
        try:
            envelope = json.loads(value)
            expires_at = timestamp(envelope["expires_at"])
            if self.clock() >= expires_at:
                return None
            return OverviewSectionReport.model_validate(envelope["report"])
        except (ValueError, TypeError, KeyError):
            return None

    def _observed(self, report: OverviewSectionReport) -> OverviewSectionReport:
        report = report.model_copy(deep=True)
        if report.snapshot is not None:
            age = max(0, (self.clock() - report.generated_at).total_seconds())
            report.snapshot.lag_seconds = (report.snapshot.lag_seconds or 0) + age
            if report.snapshot.lag_seconds > 60:
                report.snapshot.status = "stale"
        return report

    async def section(
        self, group: SectionName | Literal["queues"], window: ReportWindow
    ) -> OverviewSectionReport:
        key = f"{self.prefix}:{self.policy}:{group}:{window.key}"
        value = self.cache.get(key)
        cached = self._decode(value)
        if cached is not None:
            self.cache.move_to_end(key)
            return self._observed(cached)
        if value is not None:
            self.cache_bytes -= len(self.cache.pop(key))
        task = self.inflight.get(key)
        if task is None:
            if len(self.inflight) >= MAX_INFLIGHT:
                raise preparing("This chart is being prepared.", retry_after=5)
            task = asyncio.create_task(self._section(key, group, window))
            self.inflight[key] = task
            task.add_done_callback(lambda completed: self.inflight.pop(key, None))
        # Client disconnects must not cancel a calculation shared by other cards.
        return self._observed(await asyncio.shield(task))

    async def _calculate(
        self, group: SectionName | Literal["queues"], window: ReportWindow, now: datetime
    ) -> OverviewSectionReport:
        if group == "operations":
            return await CurrentCards(self.client, now=now).operations()
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
            generation=str(status["version"]),
            policy_version=self.policy,
            lag_seconds=lag,
        )
        return report

    async def _section(
        self, key: str, group: SectionName | Literal["queues"], window: ReportWindow
    ) -> OverviewSectionReport:
        value = await self._redis("get", key)
        cached = self._decode(value)
        if cached is not None and value is not None:
            self._remember(key, value)
            return cached
        now = self.clock()
        try:
            async with asyncio.timeout(12):
                report = await self._calculate(group, window, now)
        except (AnalyticsUnavailable, TimeoutError, HTTPException) as error:
            if isinstance(error, HTTPException) and error.status_code != 503:
                raise
            raise preparing("This chart is being prepared.", retry_after=5) from error
        envelope = json.dumps(
            {
                "expires_at": (now + timedelta(seconds=FRESH_SECONDS)).isoformat(),
                "report": report.model_dump(mode="json"),
            },
            separators=(",", ":"),
        ).encode()
        self._remember(key, envelope)
        if len(envelope) <= self.max_entry_bytes:
            await self._redis("set", key, envelope, ex=FRESH_SECONDS)
        return report


def get_analytics(request: Request) -> AnalyticsRuntime:
    runtime: AnalyticsRuntime = request.app.state.analytics
    return runtime
