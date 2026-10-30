"""Bounded repair of retained overview observations after best-effort delivery.

This scanner never reopens retired analytics tables or changes import boundaries.
Each retained source has its own cursor and readiness acknowledgement. Completed
attempts no longer present in an operational row cannot be reconstructed.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg import AsyncConnection, sql

from devfeed_core.analytics import (
    AnalyticsClient,
    AnalyticsPreparing,
    AnalyticsUnavailable,
    SqlRecord,
)
from devfeed_core.analytics_observation import AnalyticsSnapshot, pipeline_policy_version
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics, _base, _identity
from devfeed_core.analytics_producer import FEED_SOURCES, hydrate_changes, read_current_changes
from devfeed_core.analytics_schema import JOB_KINDS, JOB_TABLES, TABLES
from devfeed_core.config import Settings

OVERVIEW_SOURCES = (
    "articles",
    "sources",
    "topics",
    "user_accounts",
    "user_topics",
    "user_interests",
    "user_recommendation_states",
    "topic_proposals",
    *JOB_TABLES,
)
PROGRESS_TABLE = "analytics_reconcile_progress"
FINGERPRINT_TABLE = "analytics_reconcile_fingerprints"
FINAL_GUARDS = (
    " SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0"
)
PIPELINE_TABLE = "analytics_pipeline_events"
USER_TABLE = "analytics_user_events"
USER_DOMAINS = {"account", "feed", "membership"}
EAGER_SOURCES = {"articles", "user_accounts", "user_interests", "user_recommendation_states"}
OWNED_TARGETS = {
    "articles": (PIPELINE_TABLE, ("publication",), ""),
    "sources": (PIPELINE_TABLE, ("source",), ""),
    "topics": (PIPELINE_TABLE, ("topic",), ""),
    "user_accounts": (USER_TABLE, ("account", "feed"), ""),
    "user_topics": (USER_TABLE, ("membership",), "follow"),
    "user_interests": (USER_TABLE, ("membership",), "inferred"),
}
IMMUTABLE_FIELDS = (
    "id",
    "revision",
    "occurred_at",
    "task",
    "status",
    "outcome",
    "attempt",
    "duration_ms",
)
REPORT_SOURCES = {
    "publishing": ("articles",),
    "processing": ("articles", "topic_proposals", *JOB_TABLES),
    "audience": ("user_accounts", "user_topics", "user_interests", "user_recommendation_states"),
    "coverage": (
        "articles",
        "sources",
        "topics",
        "user_accounts",
        "user_topics",
        "user_interests",
        "user_recommendation_states",
    ),
}
type Snapshot = Callable[
    [], AbstractAsyncContextManager[tuple[AsyncConnection[SqlRecord], int, datetime]]
]


def scope_for(source: str) -> str:
    if source not in OVERVIEW_SOURCES:
        raise ValueError("Unknown retained overview source")
    return "overview:" + source


def _time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise AnalyticsUnavailable("Invalid overview repair timestamp")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _semantic(row: SqlRecord, table: str) -> str:
    fields = _base("", uuid.UUID(int=0), 0)
    fields.pop("revision")
    if table == USER_TABLE:
        fields["user_id"] = None
    values = {key: row.get(key) for key in fields}
    for key in ("occurred_at", "created_at", "expires_at", "next_refresh_at"):
        values[key] = _time(values[key])
    return _hash(values)


def _descriptor(row: SqlRecord) -> str:
    table = USER_TABLE if row["domain"] in USER_DOMAINS else PIPELINE_TABLE
    return json.dumps(
        [table, row["domain"], str(row["id"]), _semantic(row, table)], separators=(",", ":")
    )


def _references(values: Sequence[str]) -> list[list[str]]:
    result = []
    for raw in values:
        row = json.loads(raw)
        if (
            not isinstance(row, list)
            or len(row) != 4
            or row[0] not in {USER_TABLE, PIPELINE_TABLE}
            or row[1] not in {"publication", "job", "source", "topic", *USER_DOMAINS}
            or not isinstance(row[3], str)
            or len(row[3]) != 64
        ):
            raise AnalyticsUnavailable("Invalid overview repair target")
        uuid.UUID(row[2])
        result.append(row)
    return result


def _source_id(source: str, row: SqlRecord) -> str:
    return _identity("overview-source", source, *(row[key] for key in TABLES[source].keys))


def _target_id(source: str, domain: str, identity: str) -> str:
    return _identity("overview-target", source, domain, identity)


def _source_key(source: str, key: Any) -> None:
    if not isinstance(key, list) or len(key) != len(TABLES[source].keys):
        raise AnalyticsUnavailable("Invalid overview repair source key")
    for value in key:
        uuid.UUID(value)


def _target_key(key: Any) -> None:
    if not isinstance(key, list) or len(key) != 2 or not isinstance(key[0], str):
        raise AnalyticsUnavailable("Invalid overview repair target cursor")
    uuid.UUID(key[1])


def _pending(source: str, value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"keys", "revision", "targets", "immutable"}
        or type(value.get("revision")) is not int
    ):
        raise AnalyticsUnavailable("Invalid pending overview page")
    if not 0 < value["revision"] < 2**64:
        raise AnalyticsUnavailable("Invalid pending overview revision")
    keys = value.get("keys")
    if not isinstance(keys, list) or not 0 < len(keys) <= 1000:
        raise AnalyticsUnavailable("Invalid pending overview keys")
    for key in keys:
        _source_key(source, key)
    for name in ("targets", "immutable"):
        if not isinstance(value.get(name), list) or len(value[name]) > 2000:
            raise AnalyticsUnavailable("Invalid pending overview observations")
    _references(value["targets"])
    task = JOB_KINDS.get(source, "topic-decision" if source == "topic_proposals" else None)
    for record in value["immutable"]:
        row = _immutable(record)
        if row["task"] != task or row["revision"] > value["revision"]:
            raise AnalyticsUnavailable("Pending immutable observation does not match its source")


def _immutable(record: SqlRecord) -> SqlRecord:
    if set(record) != set(IMMUTABLE_FIELDS):
        raise AnalyticsUnavailable("Invalid retained immutable observation")
    if record["task"] not in {*JOB_KINDS.values(), "topic-decision"}:
        raise AnalyticsUnavailable("Invalid retained immutable task")
    if record["status"] not in {"succeeded", "failed"} or _time(record["occurred_at"]) is None:
        raise AnalyticsUnavailable("Invalid retained immutable completion")
    for name, maximum in (("revision", 2**64), ("attempt", 2**32), ("duration_ms", 2**64)):
        value = record[name]
        if value is None and name == "duration_ms":
            continue
        if type(value) is not int or not 0 <= value < maximum:
            raise AnalyticsUnavailable("Invalid retained immutable counter")
    if not isinstance(record["outcome"], str) or not 0 < len(record["outcome"]) <= 100:
        raise AnalyticsUnavailable("Invalid retained immutable outcome")
    row = _base("job", record["id"], record["revision"])
    row.update(record)
    return row


def _cursor(source: str, row: SqlRecord | None) -> dict[str, Any]:
    if row and len(row["cursor"].encode()) > 2_000_000:
        raise AnalyticsUnavailable("Overview repair cursor exceeds its bound")
    state = json.loads(row["cursor"]) if row else {"phase": "new"}
    if not isinstance(state, dict) or state.get("phase") not in {
        "new",
        "source",
        "target",
        "complete",
    }:
        raise AnalyticsUnavailable("Invalid overview repair cursor")
    if state["phase"] == "new":
        return state
    if type(state.get("fence")) is not int or not 0 < state["fence"] < 2**64:
        raise AnalyticsUnavailable("Invalid overview repair fence")
    if _time(state.get("started_at")) is None:
        raise AnalyticsUnavailable("Overview repair boundary is missing")
    for name in ("upper", "after"):
        key = state.get(name)
        if key is None or (name == "upper" and key == []):
            continue
        _source_key(source, key)
    target = state.get("target_after")
    if target is not None:
        _target_key(target)
    if "pending_page" in state:
        _pending(source, state["pending_page"])
    return state


async def read_overview_snapshot(
    client: AnalyticsClient, policy: str, sources: Sequence[str], now: datetime, *, generation: str
) -> AnalyticsSnapshot:
    if not sources or len(set(sources)) != len(sources):
        raise ValueError("Overview coverage requires distinct retained sources")
    scopes = [scope_for(source) for source in sources]
    rows = await client.query(
        "/* analytics:overview-recovery-status */ SELECT scope,source_at,completed_at,pending "
        f"FROM {PROGRESS_TABLE} FINAL WHERE policy={{policy:String}} "
        "AND scope IN {scopes:Array(String)}" + FINAL_GUARDS,
        {"policy": policy, "scopes": scopes},
    )
    observed = {row["scope"]: row for row in rows}
    if len(observed) != len(rows) or set(observed) - set(scopes):
        raise AnalyticsUnavailable("Ambiguous overview repair coverage")
    if any(
        scope not in observed
        or observed[scope]["completed_at"] is None
        or observed[scope]["source_at"] is None
        for scope in scopes
    ):
        raise AnalyticsPreparing("Retained overview observations are being recovered")
    if any(row["pending"] not in (0, 1) for row in rows):
        raise AnalyticsUnavailable("Invalid overview repair coverage")
    covered = min(value for row in rows if (value := _time(row["source_at"])) is not None)
    age = max(600, math.ceil(2 * client.settings.analytics_reconcile_sweep_seconds))
    expires = covered + timedelta(seconds=age)
    status = (
        "stale"
        if now >= expires
        else "catching_up"
        if any(row["pending"] for row in rows)
        else "fresh"
    )
    return AnalyticsSnapshot(
        status=status,
        generated_at=covered,
        coverage_expires_at=expires,
        generation=generation,
        policy_version=policy,
        lag_seconds=max(0, (now - covered).total_seconds()),
    )


class _Projection(DirectOverviewAnalytics):
    def __init__(self, client: AnalyticsClient) -> None:
        super().__init__(client)
        self.rows: list[SqlRecord] = []

    async def _write(self, rows: Sequence[SqlRecord]) -> None:
        self.rows.extend(dict(row) for row in rows)


class OverviewReconciler:
    """Independent finite source cycles; no PostgreSQL transaction spans CH I/O."""

    def __init__(
        self,
        client: AnalyticsClient,
        settings: Settings,
        snapshot: Snapshot,
        *,
        policy: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.client, self.settings, self.snapshot = client, settings, snapshot
        self.policy = policy or pipeline_policy_version(settings)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.batch = min(1000, settings.analytics_backfill_batch_size)
        self._turn, self._priority_turn = 0, False
        self._check_after = 0.0

    @property
    def due(self) -> bool:
        return time.monotonic() >= self._check_after

    def _due(self, row: SqlRecord | None) -> bool:
        if row is None or row.get("pending"):
            return True
        completed = _time(row.get("completed_at"))
        return (
            completed is None
            or (self.clock() - completed).total_seconds()
            >= self.settings.analytics_reconcile_sweep_seconds
        )

    async def _save(
        self,
        source: str,
        state: dict[str, Any],
        previous: SqlRecord | None,
        *,
        complete: bool = False,
    ) -> None:
        previous = previous or {}
        cursor = json.dumps(state, default=str, separators=(",", ":"))
        if len(cursor.encode()) > 2_000_000:
            raise AnalyticsUnavailable("Overview repair page exceeds its bound")
        await self.client.insert(
            PROGRESS_TABLE,
            [
                {
                    "policy": self.policy,
                    "scope": scope_for(source),
                    "version": max(time.time_ns(), int(previous.get("version", 0)) + 1),
                    "source_at": _time(state["started_at"])
                    if complete
                    else _time(previous.get("source_at")),
                    "completed_at": self.clock()
                    if complete
                    else _time(previous.get("completed_at")),
                    "pending": int(not complete),
                    "cursor": cursor,
                }
            ],
        )

    async def _initialize(self, source: str, previous: SqlRecord | None) -> None:
        keys = TABLES[source].keys
        projection = sql.SQL(",").join(map(sql.Identifier, keys))
        order = sql.SQL(",").join(sql.SQL("{} DESC").format(sql.Identifier(key)) for key in keys)
        async with self.snapshot() as (connection, fence, observed):
            rows = await (
                await connection.execute(
                    sql.SQL("SELECT {} FROM {} ORDER BY {} LIMIT 1").format(
                        projection, sql.Identifier(source), order
                    )
                )
            ).fetchall()
        await self._save(
            source,
            {
                "phase": "source",
                "upper": [str(rows[0][k]) for k in keys] if rows else [],
                "after": None,
                "target_after": None,
                "fence": fence,
                "started_at": observed.isoformat(),
            },
            previous,
        )

    async def _metadata(self, source: str, state: dict[str, Any]) -> tuple[list[SqlRecord], int]:
        keys = TABLES[source].keys
        columns = sql.SQL(",").join(sql.Identifier("source", key) for key in keys)
        bindings = sql.SQL(",").join(sql.SQL("%s::uuid") for _ in keys)
        statement = sql.SQL(
            "SELECT {},source.xmin::text AS stamp FROM {} source WHERE ({})<=({})"
        ).format(columns, sql.Identifier(source), columns, bindings)
        parameters: list[object] = list(state["upper"])
        if state["after"] is not None:
            statement += sql.SQL(" AND ({})>({})").format(columns, bindings)
            parameters.extend(state["after"])
        statement += sql.SQL(" ORDER BY {} LIMIT %s").format(columns)
        parameters.append(self.batch)
        async with self.snapshot() as (connection, revision, _):
            rows = await (await connection.execute(statement, parameters)).fetchall()
        return rows, revision

    async def _fingerprints(self, kind: str, identities: Sequence[str]) -> dict[str, SqlRecord]:
        if not identities:
            return {}
        rows = await self.client.query(
            f"SELECT * FROM {FINGERPRINT_TABLE} FINAL WHERE policy={{policy:String}} "
            "AND kind={kind:String} AND entity_id IN {ids:Array(UUID)} AND deleted=0"
            + FINAL_GUARDS,
            {"policy": self.policy, "kind": kind, "ids": list(identities)},
        )
        result = {str(row["entity_id"]): row for row in rows}
        if len(result) != len(rows):
            raise AnalyticsUnavailable("Ambiguous overview repair fingerprint")
        return result

    async def _targets(self, descriptors: Sequence[str]) -> dict[tuple[str, str, str], str]:
        references = _references(descriptors)
        result = {}
        for table in (PIPELINE_TABLE, USER_TABLE):
            keys = sorted({(r[1], r[2]) for r in references if r[0] == table})
            if not keys:
                continue
            rows = await self.client.query(
                f"SELECT * FROM {table} FINAL WHERE domain IN {{domains:Array(String)}} "
                "AND id IN {ids:Array(UUID)}" + FINAL_GUARDS,
                {
                    "domains": sorted({key[0] for key in keys}),
                    "ids": sorted({key[1] for key in keys}),
                },
            )
            for row in rows:
                key = (table, row["domain"], str(row["id"]))
                if key in result:
                    raise AnalyticsUnavailable("Ambiguous overview repair target")
                result[key] = _semantic(row, table)
        return result

    async def _fetch(
        self, source: str, selected: Sequence[SqlRecord]
    ) -> tuple[list[SqlRecord], int]:
        keys = TABLES[source].keys
        columns = sql.SQL(",").join(sql.Identifier("source", key) for key in keys)
        row_binding = sql.SQL("({})").format(sql.SQL(",").join(sql.SQL("%s::uuid") for _ in keys))
        bindings = sql.SQL(",").join(row_binding for _ in selected)
        statement = sql.SQL(
            "SELECT {} AS record,source.xmin::text AS stamp FROM {} source WHERE ({}) IN ({})"
        ).format(
            sql.SQL(TABLES[source].record_sql("source")), sql.Identifier(source), columns, bindings
        )
        parameters = tuple(row[key] for row in selected for key in keys)
        async with self.snapshot() as (connection, revision, _):
            rows = await (await connection.execute(statement, parameters)).fetchall()
            changes = [
                {
                    "source_table": source,
                    "record": row["record"],
                    "attempt_only": source in JOB_TABLES,
                }
                for row in rows
            ]
            hydrated = await hydrate_changes(connection, changes)
        groups = self._groups(source, hydrated)
        return [
            dict(row, changes=groups[_source_id(source, row["record"])]) for row in rows
        ], revision

    @staticmethod
    def _groups(source: str, changes: Sequence[SqlRecord]) -> dict[str, list[SqlRecord]]:
        publications = {c["publication"]["article_id"]: c for c in changes if "publication" in c}
        feeds = {c["feed"]["user_id"]: c for c in changes if "feed" in c}
        result = {}
        for change in changes:
            if change["source_table"] != source or change.get("record") is None:
                continue
            record = change["record"]
            group = [change]
            if source == "articles":
                group.append(publications[str(record["id"])])
            if source in FEED_SOURCES:
                identity = record["id"] if source == "user_accounts" else record["user_id"]
                group.append(feeds[str(identity)])
            result[_source_id(source, record)] = group
        return result

    def _fingerprint(
        self, source: str, identity: str, revision: int, stamp: str, descriptors: list[str]
    ) -> SqlRecord:
        return {
            "policy": self.policy,
            "kind": "overview-source:" + source,
            "entity_id": identity,
            "version": revision,
            "fingerprint": _hash(descriptors),
            "target_hash": stamp,
            "owners": descriptors,
            "deleted": 0,
        }

    async def _prepare(
        self, source: str, rows: Sequence[SqlRecord], previous: dict[str, SqlRecord], revision: int
    ) -> tuple[dict[str, list[str]], list[SqlRecord], list[SqlRecord]]:
        descriptors, writes, fingerprints = {}, {}, []
        for row in rows:
            identity = _source_id(source, row["record"])
            projection = _Projection(self.client)
            await projection.publish(row["changes"], revision=revision)
            if any(value["domain"] == "queue" for value in projection.rows):
                raise AnalyticsUnavailable("Overview recovery cannot publish queue state")
            owners = sorted({_descriptor(value) for value in projection.rows})
            descriptors[identity] = owners
            old = previous.get(identity)
            if old is None or old["fingerprint"] != _hash(owners):
                for value in projection.rows:
                    writes[value["domain"], str(value["id"])] = value
            fingerprints.append(self._fingerprint(source, identity, revision, row["stamp"], owners))
        targets = await self._targets(
            [owner for values in descriptors.values() for owner in values]
        )
        for row in rows:
            identity = _source_id(source, row["record"])
            if any(
                targets.get((ref[0], ref[1], ref[2])) != ref[3]
                for ref in _references(descriptors[identity])
            ):
                projection = _Projection(self.client)
                await projection.publish(row["changes"], revision=revision)
                writes.update(
                    {(value["domain"], str(value["id"])): value for value in projection.rows}
                )
        return descriptors, list(writes.values()), fingerprints

    async def _write_observations(self, rows: Sequence[SqlRecord]) -> None:
        for offset in range(0, len(rows), 1000):
            await DirectOverviewAnalytics(self.client)._write(rows[offset : offset + 1000])

    async def _checkpoint_page(
        self,
        source: str,
        state: dict[str, Any],
        previous: SqlRecord,
        keys: list[list[str]],
        revision: int,
        rows: Sequence[SqlRecord],
    ) -> None:
        state["pending_page"] = {
            "keys": keys,
            "revision": revision,
            "targets": sorted({_descriptor(row) for row in rows if row["domain"] != "job"}),
            "immutable": [
                {key: row[key] for key in IMMUTABLE_FIELDS}
                for row in rows
                if row["domain"] == "job"
            ],
        }
        await self._save(source, state, previous)

    async def _resume_page(
        self,
        source: str,
        state: dict[str, Any],
        previous: SqlRecord,
    ) -> None:
        pending = state["pending_page"]
        immutable = [_immutable(row) for row in pending["immutable"]]
        changes = [
            {
                "source_table": source,
                "identity": dict(zip(TABLES[source].keys, key, strict=True)),
                "attempt_only": source in JOB_TABLES,
            }
            for key in pending["keys"]
        ]
        async with self.snapshot() as (connection, revision, _):
            current = await read_current_changes(connection, changes)
            hydrated = await hydrate_changes(connection, current)
        projection = _Projection(self.client)
        for offset in range(0, len(hydrated), 1000):
            await projection.publish(hydrated[offset : offset + 1000], revision=revision)
        if any(row["domain"] == "queue" for row in projection.rows):
            raise AnalyticsUnavailable("Overview recovery cannot replay queue state")
        rows = {(row["domain"], str(row["id"])): row for row in immutable}
        # A pending retry preserves its original immutable observations. The
        # unchanged source cursor discovers any newer retained attempt next;
        # repeated failures cannot grow one checkpoint with unlimited attempts.
        rows.update(
            {
                (row["domain"], str(row["id"])): row
                for row in projection.rows
                if row["domain"] != "job"
            }
        )
        expected = {(ref[0], ref[1], ref[2]) for ref in _references(pending["targets"])}
        observed = {
            (ref[0], ref[1], ref[2])
            for ref in _references([_descriptor(row) for row in projection.rows])
        }
        if expected - observed:
            raise AnalyticsUnavailable("Pending overview owner could not be recovered")
        await self._checkpoint_page(
            source, state, previous, pending["keys"], revision, list(rows.values())
        )
        await self._write_observations(list(rows.values()))
        state.pop("pending_page")
        await self._save(source, state, previous)

    async def _presence(
        self, source: str, descriptors: Sequence[str], revision: int, epoch: int
    ) -> None:
        owned = OWNED_TARGETS.get(source)
        if owned is None:
            return
        rows = []
        for ref in _references(sorted(set(descriptors))):
            if ref[0] != owned[0] or ref[1] not in owned[1]:
                continue
            rows.append(
                {
                    "policy": self.policy,
                    "kind": "overview-target:" + source,
                    "entity_id": _target_id(source, ref[1], ref[2]),
                    "version": revision,
                    "fingerprint": str(epoch),
                    "target_hash": "",
                    "owners": [json.dumps(ref)],
                    "deleted": 0,
                }
            )
        if rows:
            await self.client.insert(FINGERPRINT_TABLE, rows)

    async def _source_page(self, source: str, state: dict[str, Any], previous: SqlRecord) -> None:
        metadata, revision = (
            await self._metadata(source, state) if state["upper"] else ([], state["fence"])
        )
        identities = [_source_id(source, row) for row in metadata]
        old = await self._fingerprints("overview-source:" + source, identities)
        descriptors = {
            identity: old[identity]["owners"] for identity in identities if identity in old
        }
        targets = await self._targets([v for owners in descriptors.values() for v in owners])
        selected = [row for row in metadata if self._changed(source, row, old, targets)]
        if selected:
            fetched, revision = await self._fetch(source, selected)
            observed, writes, fingerprints = await self._prepare(source, fetched, old, revision)
            descriptors.update(observed)
            if writes:
                keys = [[str(row["record"][key]) for key in TABLES[source].keys] for row in fetched]
                await self._checkpoint_page(source, state, previous, keys, revision, writes)
                await self._write_observations(writes)
            if fingerprints:
                await self.client.insert(FINGERPRINT_TABLE, fingerprints)
            existing = {_source_id(source, row["record"]) for row in fetched}
            for row in selected:
                identity = _source_id(source, row)
                if identity not in existing:
                    descriptors.pop(identity, None)
        await self._presence(
            source, [v for owners in descriptors.values() for v in owners], revision, state["fence"]
        )
        if len(metadata) == self.batch:
            state["after"] = [str(metadata[-1][key]) for key in TABLES[source].keys]
        else:
            state["phase"] = "target" if source in OWNED_TARGETS else "complete"
        state.pop("pending_page", None)
        await self._save(source, state, previous, complete=state["phase"] == "complete")

    @staticmethod
    def _changed(
        source: str,
        row: SqlRecord,
        old: dict[str, SqlRecord],
        targets: dict[tuple[str, str, str], str],
    ) -> bool:
        before = old.get(_source_id(source, row))
        if source in EAGER_SOURCES or before is None or before["target_hash"] != row["stamp"]:
            return True
        return any(
            targets.get((ref[0], ref[1], ref[2])) != ref[3] for ref in _references(before["owners"])
        )

    async def _target_page(self, source: str, state: dict[str, Any], previous: SqlRecord) -> None:
        table, domains, task = OWNED_TARGETS[source]
        task_clause = " AND task={task:String}" if task else ""
        after_clause = (
            " AND (domain,id)>({after_domain:String},{after_id:UUID})"
            if state["target_after"]
            else ""
        )
        parameters = {
            "domains": list(domains),
            "task": task,
            "fence": state["fence"],
            "limit": self.batch,
        }
        if state["target_after"]:
            parameters.update(
                after_domain=state["target_after"][0], after_id=state["target_after"][1]
            )
        rows = await self.client.query(
            f"SELECT domain,id FROM {table} FINAL WHERE deleted=0 "
            "AND domain IN {domains:Array(String)} "
            "AND revision<={fence:UInt64}"
            + task_clause
            + after_clause
            + " ORDER BY domain,id LIMIT {limit:UInt32}"
            + FINAL_GUARDS,
            parameters,
        )
        ids = [_target_id(source, row["domain"], str(row["id"])) for row in rows]
        present = await self._fingerprints("overview-target:" + source, ids)
        missing = [
            row
            for row, identity in zip(rows, ids, strict=True)
            if identity not in present or present[identity]["fingerprint"] != str(state["fence"])
        ]
        await DirectOverviewAnalytics(self.client)._write(
            [_base(row["domain"], row["id"], state["fence"], deleted=True) for row in missing]
        )
        if len(rows) == self.batch:
            state["target_after"] = [rows[-1]["domain"], str(rows[-1]["id"])]
        else:
            state["phase"] = "complete"
        await self._save(source, state, previous, complete=state["phase"] == "complete")

    def _choose(self, rows: dict[str, SqlRecord]) -> str | None:
        self._priority_turn = not self._priority_turn
        if self._priority_turn and self._due(rows.get(scope_for("articles"))):
            return "articles"
        for _ in OVERVIEW_SOURCES:
            source = OVERVIEW_SOURCES[self._turn % len(OVERVIEW_SOURCES)]
            self._turn += 1
            if self._due(rows.get(scope_for(source))):
                return source
        return None

    async def once(self, source: str | None = None) -> bool:
        if source is not None:
            scope_for(source)
        rows = await self.client.query(
            f"SELECT * FROM {PROGRESS_TABLE} FINAL WHERE policy={{policy:String}} "
            "AND scope IN {scopes:Array(String)}" + FINAL_GUARDS,
            {"policy": self.policy, "scopes": [scope_for(value) for value in OVERVIEW_SOURCES]},
        )
        previous = {row["scope"]: row for row in rows}
        if len(previous) != len(rows):
            raise AnalyticsUnavailable("Ambiguous overview repair progress")
        source = source or self._choose(previous)
        if source is None:
            self._check_after = time.monotonic() + min(
                30, self.settings.analytics_reconcile_sweep_seconds
            )
            return False
        row = previous.get(scope_for(source))
        if not self._due(row):
            return False
        state = _cursor(source, row)
        if "pending_page" in state:
            await self._resume_page(source, state, row or {})
        elif state["phase"] in {"new", "complete"}:
            await self._initialize(source, row)
        elif state["phase"] == "source":
            await self._source_page(source, state, row or {})
        else:
            await self._target_page(source, state, row or {})
        return True
