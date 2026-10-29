"""One finite, paged refresh of current audience event timestamps.

This projection repair has its own durable version. It neither resets historical
imports nor emits events for the time at which this repair happens to run.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection, sql

from devfeed_core.analytics import (
    AnalyticsClient,
    AnalyticsQueries,
    AnalyticsUnavailable,
    SqlRecord,
)
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics, _base
from devfeed_core.analytics_producer import _feed_rows

REFRESH_POLICY = "audience-event-time-v1"
REFRESH_SCOPE = "metadata"
PROGRESS_TABLE = "analytics_reconcile_progress"
FINAL_GUARDS = (
    " SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0"
)
SOURCES = (
    ("user_accounts", ("id",), 100),
    ("user_topics", ("user_id", "topic_id"), 200),
    ("user_interests", ("user_id", "topic_id"), 200),
)
COMPLETE_PHASE = len(SOURCES) + 1
type Snapshot = Callable[
    [], AbstractAsyncContextManager[tuple[AsyncConnection[SqlRecord], int, datetime] | None]
]


def _state(row: SqlRecord | None) -> dict[str, Any]:
    state = (
        json.loads(row["cursor"])
        if row
        else {"phase": 0, "upper": None, "after": None, "start_revision": 0}
    )
    if not isinstance(state, dict) or type(state.get("phase")) is not int:
        raise AnalyticsUnavailable("Invalid audience metadata cursor")
    phase = state["phase"]
    if not 0 <= phase <= COMPLETE_PHASE:
        raise AnalyticsUnavailable("Invalid audience metadata phase")
    start = state.get("start_revision", 0)
    if type(start) is not int or not 0 <= start < 2**64:
        raise AnalyticsUnavailable("Invalid audience metadata fence")
    if not start and (phase > 0 or state.get("upper") is not None):
        raise AnalyticsUnavailable("Audience metadata fence is missing")
    for name in ("upper", "after"):
        key = state.get(name)
        if key is None or (name == "upper" and key == []):
            continue
        if phase >= len(SOURCES) or not isinstance(key, list) or len(key) != len(SOURCES[phase][1]):
            raise AnalyticsUnavailable("Invalid audience metadata key")
        for value in key:
            uuid.UUID(value)
    return state


async def audience_metadata_ready(client: AnalyticsQueries) -> bool:
    """Only the acknowledged complete projection can certify range cohorts."""
    rows = await client.query(
        "/* analytics:audience-metadata-status */ SELECT cursor,pending FROM "
        f"{PROGRESS_TABLE} FINAL "
        "WHERE policy={policy:String} AND scope={scope:String}" + FINAL_GUARDS,
        {"policy": REFRESH_POLICY, "scope": REFRESH_SCOPE},
    )
    if len(rows) > 1:
        raise AnalyticsUnavailable("Ambiguous audience metadata progress")
    if not rows:
        return False
    return (
        _state(rows[0])["phase"] == COMPLETE_PHASE
        and rows[0]["pending"] == 0
        and not await _metadata_holes(client)
    )


async def _metadata_holes(
    client: AnalyticsQueries, *, fence: int | None = None, limit: int = 1
) -> list[SqlRecord]:
    bound = " AND revision<={fence:UInt64}" if fence is not None else ""
    rows = await client.query(
        "/* analytics:audience-metadata-holes */ SELECT domain,id,revision FROM "
        "(SELECT * FROM analytics_user_events FINAL WHERE domain IN ('feed','membership')) "
        "WHERE deleted=0 AND ((domain='feed' AND created_at IS NULL) OR "
        "(domain='membership' AND (user_id IS NULL OR "
        "(task='follow' AND occurred_at IS NULL))))"
        + bound
        + " ORDER BY domain,id LIMIT {limit:UInt32}"
        + FINAL_GUARDS,
        {"limit": limit, **({"fence": fence} if fence is not None else {})},
    )
    if len(rows) > limit or any(row["domain"] not in {"feed", "membership"} for row in rows):
        raise AnalyticsUnavailable("Invalid audience metadata target page")
    return rows


def _query(
    phase: int, state: dict[str, Any], *, boundary: bool
) -> tuple[sql.Composed, tuple[object, ...]]:
    table, keys, limit = SOURCES[phase]
    key_sql = sql.SQL(",").join(sql.Identifier("source", key) for key in keys)
    conditions: list[sql.Composable] = (
        [sql.SQL("source.reason='related_topic'")] if phase == 2 else []
    )
    parameters: list[object] = []
    if not boundary:
        bindings = sql.SQL(",").join(sql.SQL("%s::uuid") for _ in keys)
        conditions.append(sql.SQL("({})<=({})").format(key_sql, bindings))
        parameters.extend(state["upper"])
        if state["after"] is not None:
            conditions.append(sql.SQL("({})>({})").format(key_sql, bindings))
            parameters.extend(state["after"])
    projection = key_sql
    join = sql.SQL("")
    if not boundary and phase == 1:
        projection += sql.SQL(",source.created_at AS occurred_at")
    if not boundary and phase == 2:
        projection += sql.SQL(",state.computed_at AS occurred_at")
        join = sql.SQL(
            " LEFT JOIN user_recommendation_states state ON state.user_id=source.user_id"
        )
    statement = sql.SQL("SELECT {} FROM {} source{}").format(
        projection, sql.Identifier(table), join
    )
    if conditions:
        statement += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    order = sql.SQL(",").join(
        sql.SQL("{}{}").format(sql.Identifier("source", key), sql.SQL(" DESC" if boundary else ""))
        for key in keys
    )
    parameters.append(1 if boundary else limit)
    return statement + sql.SQL(" ORDER BY {} LIMIT %s").format(order), tuple(parameters)


class AudienceMetadataRefresh:
    """ACK data before cursor, retaining a fixed primary-key ceiling per source."""

    def __init__(self, client: AnalyticsClient, snapshot: Snapshot) -> None:
        self.client, self.snapshot = client, snapshot
        self.complete = False
        self._probe_after = 0.0

    @property
    def due(self) -> bool:
        return not self.complete or time.monotonic() >= self._probe_after

    async def _save(
        self, state: dict[str, Any], previous: SqlRecord | None, **updates: object
    ) -> None:
        now = datetime.now(UTC)
        await self.client.insert(
            PROGRESS_TABLE,
            [
                {
                    "policy": REFRESH_POLICY,
                    "scope": REFRESH_SCOPE,
                    "version": max(time.time_ns(), int((previous or {}).get("version", 0)) + 1),
                    "source_at": (previous or {}).get("source_at") or now,
                    "completed_at": None,
                    "pending": 1,
                    "cursor": json.dumps(state, separators=(",", ":")),
                    **updates,
                }
            ],
        )

    async def _initialize(self, state: dict[str, Any], previous: SqlRecord | None) -> bool:
        async with self.snapshot() as snapshot:
            if snapshot is None:
                return False
            connection, revision, source_at = snapshot
            statement, parameters = _query(state["phase"], state, boundary=True)
            rows = await (await connection.execute(statement, parameters)).fetchall()
        keys = SOURCES[state["phase"]][1]
        state["upper"] = [str(rows[0][key]) for key in keys] if rows else []
        if not state.get("start_revision"):
            state["start_revision"] = revision
        await self._save(state, previous, source_at=(previous or {}).get("source_at") or source_at)
        return True

    async def _page(self, state: dict[str, Any], previous: SqlRecord | None) -> bool:
        phase = state["phase"]
        _, keys, limit = SOURCES[phase]
        async with self.snapshot() as snapshot:
            if snapshot is None:
                return False
            connection, revision, _ = snapshot
            statement, parameters = _query(phase, state, boundary=False)
            rows = await (await connection.execute(statement, parameters)).fetchall()
            if len(rows) > limit:
                raise AnalyticsUnavailable("Audience metadata page exceeds its bound")
            feeds = (
                await _feed_rows(connection, [str(row["id"]) for row in rows]) if phase == 0 else {}
            )
        writer = DirectOverviewAnalytics(self.client)
        if phase == 0:
            await writer.feeds([record | {"revision": revision} for record in feeds.values()])
        else:
            await writer.memberships(
                [
                    {
                        "user_id": row["user_id"],
                        "topic_id": row["topic_id"],
                        "kind": "follow" if phase == 1 else "inferred",
                        "occurred_at": row["occurred_at"],
                        "revision": revision,
                    }
                    for row in rows
                ]
            )
        if len(rows) == limit:
            state["after"] = [str(rows[-1][key]) for key in keys]
        else:
            state.update(phase=phase + 1, upper=None, after=None)
        await self._save(state, previous)
        return True

    async def _orphans(self, state: dict[str, Any], previous: SqlRecord | None) -> bool:
        fence = state["start_revision"]  # Validated before the target phase is admitted.
        rows = await _metadata_holes(self.client, fence=fence, limit=200)
        if rows:
            # The START fence is deliberate: a valid publisher racing between
            # this SELECT and INSERT has a higher revision and must survive.
            await DirectOverviewAnalytics(self.client)._write(
                [_base(row["domain"], row["id"], fence, deleted=True) for row in rows]
            )
            return True
        state["phase"] = COMPLETE_PHASE
        await self._save(state, previous, pending=0, completed_at=datetime.now(UTC))
        self.complete = True
        self._probe_after = time.monotonic() + 30
        return True

    async def once(self) -> bool:
        rows = await self.client.query(
            f"SELECT * FROM {PROGRESS_TABLE} FINAL WHERE policy={{policy:String}} "
            "AND scope={scope:String}" + FINAL_GUARDS,
            {"policy": REFRESH_POLICY, "scope": REFRESH_SCOPE},
        )
        if len(rows) > 1:
            raise AnalyticsUnavailable("Ambiguous audience metadata progress")
        previous = rows[0] if rows else None
        state = _state(previous)
        if state["phase"] == COMPLETE_PHASE:
            if previous is not None and previous["pending"] == 0:
                if await _metadata_holes(self.client):
                    state = {"phase": 0, "upper": None, "after": None, "start_revision": 0}
                    await self._save(state, previous, source_at=None)
                    self.complete = False
                    return True
                self.complete = True
                self._probe_after = time.monotonic() + 30
                return False
            raise AnalyticsUnavailable("Audience metadata completion was not acknowledged")
        if state["phase"] == len(SOURCES):
            return await self._orphans(state, previous)
        if state["upper"] is None:
            return await self._initialize(state, previous)
        if not state["upper"]:
            state.update(phase=state["phase"] + 1, upper=None, after=None)
            await self._save(state, previous)
            return True
        return await self._page(state, previous)
