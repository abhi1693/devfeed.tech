"""Small current-state projections and bounded ClickHouse gauge publication.

Only changed identities are hydrated from PostgreSQL. Population aggregation runs
in ClickHouse over one of 256 stable shards, outside application transactions.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg import sql

from devfeed_core.analytics_schema import JOB_KINDS

SHARDS = 256
REFRESH_BATCH_SIZE = 16
FEED_METRICS = (
    "ready",
    "refreshing",
    "expired",
    "pending",
    "not_needed",
    "overdue",
    "empty_with_interests",
)
ZERO = str(uuid.UUID(int=0))
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
USER_SOURCES = {"user_accounts", "user_recommendation_states"}
MEMBERSHIP_SOURCES = {"user_topics": "follow", "user_interests": "inferred"}
DIMENSION_KINDS = {"sources": "source", "topics": "topic"}
RESET_KINDS = {
    "user_accounts": "account",
    "user_recommendation_states": "feed",
    **DIMENSION_KINDS,
    **MEMBERSHIP_SOURCES,
}
GAUGE_DOMAINS = {
    **dict.fromkeys(JOB_KINDS, "queues"),
    **dict.fromkeys(USER_SOURCES, "feed"),
    "sources": "sources",
    "topics": "topics",
    **dict.fromkeys(MEMBERSHIP_SOURCES, "topics"),
}
IDENTITY_FIELDS = {
    "sources": ("id",),
    "topics": ("id",),
    "user_accounts": ("id",),
    "user_recommendation_states": ("user_id",),
    "user_topics": ("user_id", "topic_id"),
    "user_interests": ("user_id", "topic_id"),
}


def _identity_changes(events):
    """An UPDATE before-image retires the prior canonical identity exactly once."""
    for event in events:
        before = event.get("before_record")
        keys = IDENTITY_FIELDS.get(event["source_table"])
        if (
            event.get("operation") == "I"
            and before
            and keys
            and any(str(before.get(key)) != str(event["record"].get(key)) for key in keys)
        ):
            yield {**event, "record": before, "before_record": None, "operation": "D"}
        yield event


def _feed_users(event):
    if event.get("operation") == "T":
        return []
    source, record = event["source_table"], event["record"]
    if source in USER_SOURCES:
        return [record.get("user_id") or record["id"]]
    before = event.get("before_record")
    if source in MEMBERSHIP_SOURCES and before and str(before["user_id"]) != str(record["user_id"]):
        return [before["user_id"], record["user_id"]]
    return []


def shard(identifier):
    return uuid.UUID(str(identifier)).bytes[0]


def _observe_users(event, users, accounts):
    for value in _feed_users(event):
        identifier = str(value)
        if event["source_table"] == "user_accounts":
            accounts.add(identifier)
        previous = users.get(identifier)
        version = int(event["id"])
        if previous is None or version >= previous[0]:
            users[identifier] = (
                version,
                event.get("created_at") or event["record"].get("created_at") or EPOCH,
            )


def _projection_change(event, generation):
    source, record = event["source_table"], event["record"]
    if source not in DIMENSION_KINDS and source not in MEMBERSHIP_SOURCES:
        return None
    base = {
        "event_id": int(event["id"]),
        "delivery_generation": generation,
        "captured_at": event.get("created_at") or record.get("created_at") or EPOCH,
        "target_id": ZERO,
        "deleted": int(event.get("operation") == "D"),
    }
    if source in DIMENSION_KINDS:
        identifier = str(record["id"])
        return "dimension", {
            **base,
            "kind": DIMENSION_KINDS[source],
            "shard": shard(identifier),
            "entity_id": identifier,
            "name": record.get("name") or "Unnamed",
            "consecutive_failures": max(0, record.get("consecutive_failures") or 0),
            "status": record.get("status") or record.get("approval_status") or "",
        }
    target = str(record["topic_id"])
    return "membership", {
        **base,
        "kind": MEMBERSHIP_SOURCES[source],
        "shard": shard(target),
        "entity_id": str(record["user_id"]),
        "target_id": target,
        "deleted": int(
            bool(base["deleted"])
            or (source == "user_interests" and record.get("reason") != "related_topic")
        ),
    }


def _projection_ddl(name, fields):
    base = (
        "kind LowCardinality(String), shard UInt16, entity_id UUID, target_id UUID, "
        "event_id UInt64, delivery_generation UInt64, captured_at DateTime64(6, 'UTC')"
    )
    names = [field for field, _kind in fields]
    types = [kind for _field, kind in fields]
    version = "tuple(delivery_generation, event_id)"
    payload = "tuple(" + ",".join([*names, "delivery_generation", "event_id"]) + ")"
    state_type = "Tuple(" + ",".join([*types, "UInt64", "UInt64"]) + ")"
    columns = ",".join(f"{field} {kind}" for field, kind in fields)
    keys = "kind, shard, target_id, entity_id"
    return (
        f"CREATE TABLE IF NOT EXISTS analytics_{name}_changes ({base}, {columns}) "
        "ENGINE=ReplacingMergeTree(delivery_generation) "
        "PARTITION BY toYYYYMM(captured_at) "
        f"ORDER BY ({keys},captured_at,event_id)",
        f"CREATE TABLE IF NOT EXISTS analytics_{name}_state "
        "(kind LowCardinality(String), shard UInt16, target_id UUID, entity_id UUID, "
        f"value AggregateFunction(argMax,{state_type},Tuple(UInt64,UInt64))) "
        f"ENGINE=AggregatingMergeTree ORDER BY ({keys})",
        f"CREATE MATERIALIZED VIEW IF NOT EXISTS analytics_{name}_capture "
        f"TO analytics_{name}_state AS SELECT {keys}, "
        f"argMaxState({payload},{version}) AS value FROM analytics_{name}_changes "
        f"GROUP BY {keys}",
    )


AUDIENCE_FIELDS = (
    ("deleted", "UInt8"),
    ("interest_count", "UInt32"),
    ("invalidated", "UInt8"),
    ("has_generation", "UInt8"),
    ("has_recommendations", "UInt8"),
    ("expires_at", "Nullable(DateTime64(6, 'UTC'))"),
    ("next_refresh_at", "Nullable(DateTime64(6, 'UTC'))"),
)
DIMENSION_FIELDS = (
    ("deleted", "UInt8"),
    ("name", "String"),
    ("consecutive_failures", "UInt32"),
    ("status", "LowCardinality(String)"),
)
MEMBERSHIP_FIELDS = (("deleted", "UInt8"),)


class Gauges:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client

    async def initialize(self):
        statements = [
            *_projection_ddl("audience", AUDIENCE_FIELDS),
            *_projection_ddl("dimension", DIMENSION_FIELDS),
            *_projection_ddl("membership", MEMBERSHIP_FIELDS),
            "CREATE TABLE IF NOT EXISTS analytics_gauge_resets "
            "(kind LowCardinality(String), delivery_generation UInt64,event_id UInt64) "
            "ENGINE=ReplacingMergeTree(delivery_generation) ORDER BY (kind,event_id)",
            "CREATE TABLE IF NOT EXISTS analytics_gauge_manifest "
            "(domain LowCardinality(String),shard UInt16,generation UInt64,"
            "updated_at DateTime64(6,'UTC')) ENGINE=ReplacingMergeTree(generation) "
            "ORDER BY (domain,shard)",
        ]
        targets = {
            "queue": "kind LowCardinality(String),queued UInt64,running UInt64,"
            "oldest_queued_at Nullable(DateTime64(6,'UTC'))",
            "feed": ",".join(f"{name} UInt64" for name in FEED_METRICS),
            "source": "source_id UUID,name String,consecutive_failures UInt32",
            "topic": "topic_id UUID,name String,followers UInt64,inferred_users UInt64",
        }
        for name, columns in targets.items():
            key = {"queue": "kind", "source": "source_id", "topic": "topic_id"}.get(name)
            order = "shard,generation" + (f",{key}" if key else "")
            statements.append(
                f"CREATE TABLE IF NOT EXISTS analytics_{name}_gauges "
                f"(generation UInt64,shard UInt16,{columns},"
                "built_at DateTime64(6,'UTC') DEFAULT now64(6)) "
                f"ENGINE=MergeTree ORDER BY ({order}) "
                "TTL built_at + INTERVAL 7 DAY DELETE"
            )
        for statement in statements:
            await self.client.execute(statement)

    async def _audience_rows(self, connection, users, accounts, generation):
        audience = []
        if users:
            identifiers = [uuid.UUID(identifier) for identifier in users]
            rows = await (
                await connection.execute(
                    "SELECT a.id,CASE WHEN coalesce(s.interest_count,0)>0 THEN s.interest_count "
                    "WHEN EXISTS(SELECT 1 FROM user_topics t WHERE t.user_id=a.id) OR "
                    "EXISTS(SELECT 1 FROM user_sources f WHERE f.user_id=a.id) OR "
                    "EXISTS(SELECT 1 FROM user_tags t WHERE t.user_id=a.id) THEN 1 ELSE 0 END "
                    "AS interest_count,s.invalidated,s.generation,"
                    "s.expires_at,s.next_refresh_at,"
                    "EXISTS(SELECT 1 FROM user_recommendations r WHERE r.user_id=a.id "
                    "LIMIT 1) AS has_recommendations "
                    "FROM user_accounts a LEFT JOIN user_recommendation_states s ON s.user_id=a.id "
                    "WHERE a.id=ANY(%s)",
                    (identifiers,),
                )
            ).fetchall()
            current = {str(row["id"]): row for row in rows}
            for identifier, (version, captured_at) in users.items():
                row = current.get(identifier, {})
                base = dict(
                    entity_id=identifier,
                    target_id=ZERO,
                    shard=shard(identifier),
                    event_id=version,
                    delivery_generation=generation,
                    captured_at=captured_at,
                    deleted=int(not row),
                    interest_count=max(0, row.get("interest_count") or 0),
                    invalidated=int(bool(row.get("invalidated"))),
                    has_generation=int(row.get("generation") is not None),
                    has_recommendations=int(bool(row.get("has_recommendations"))),
                    expires_at=row.get("expires_at"),
                    next_refresh_at=row.get("next_refresh_at"),
                )
                if identifier in accounts:
                    # Account presence changes only with account lifecycle events.
                    # Feed-only generations must not duplicate stable account history.
                    account = {
                        **base,
                        "interest_count": 0,
                        "invalidated": 0,
                        "has_generation": 0,
                        "has_recommendations": 0,
                        "expires_at": None,
                        "next_refresh_at": None,
                    }
                    audience.append({**account, "kind": "account"})
                audience.append({**base, "kind": "feed"})
        return audience

    async def ingest(self, connection, events, *, generation=0):
        """Durably normalize changes before their source transaction is acknowledged."""
        users: dict[str, tuple[int, datetime | str]] = {}
        accounts: set[str] = set()
        groups: dict[str, list[dict[str, Any]]] = {"dimension": [], "membership": []}
        resets = []
        for event in _identity_changes(events):
            if event.get("operation") == "T":
                kind = RESET_KINDS.get(event["source_table"])
                if kind:
                    resets.append(
                        dict(kind=kind, delivery_generation=generation, event_id=int(event["id"]))
                    )
                continue
            _observe_users(event, users, accounts)
            change = _projection_change(event, generation)
            if change:
                name, record = change
                groups[name].append(record)
        audience = await self._audience_rows(connection, users, accounts, generation)
        if audience:
            groups["audience"] = audience
        await self._insert_changes(groups, resets)

    async def _insert_changes(self, groups, resets):
        inserts = [
            self.client.insert(f"analytics_{name}_changes", records)
            for name, records in groups.items()
            if records
        ]
        if resets:
            inserts.append(self.client.insert("analytics_gauge_resets", resets))
        if inserts:
            results = await asyncio.gather(*inserts, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    async def mark_dirty(self, connection, events):
        dirty = set()
        for event in _identity_changes(events):
            source, record = event["source_table"], event["record"]
            for identifier in _feed_users(event):
                dirty.add(("feed", shard(identifier)))
            domain = GAUGE_DOMAINS.get(source)
            if domain is None:
                continue
            if domain == "queues":
                dirty.add((domain, 0))
            elif event.get("operation") == "T":
                dirty.update((domain, number) for number in range(SHARDS))
            else:
                identifier = (
                    record.get("topic_id")
                    if source in MEMBERSHIP_SOURCES
                    else record.get("user_id") or record.get("id")
                )
                dirty.add((domain, shard(identifier)))
        if dirty:
            keys = sorted(dirty)
            await connection.execute(
                "INSERT INTO analytics_gauge_refreshes(domain,shard) "
                "SELECT incoming.domain,incoming.shard FROM "
                "unnest(%s::text[],%s::smallint[]) AS incoming(domain,shard) "
                "ORDER BY incoming.domain,incoming.shard "
                "ON CONFLICT(domain,shard) DO UPDATE SET revision="
                "nextval('analytics_gauge_revision'),"
                "available_at=LEAST(analytics_gauge_refreshes.available_at,clock_timestamp())",
                ([domain for domain, _ in keys], [number for _, number in keys]),
            )

    @staticmethod
    def _reset(kind):
        return (
            "(SELECT max(tuple(delivery_generation,event_id)) "
            "FROM analytics_gauge_resets WHERE kind='" + kind + "')"
        )

    @staticmethod
    def _bounded_shards(numbers):
        numbers = list(numbers)
        if (
            not numbers
            or len(numbers) > REFRESH_BATCH_SIZE
            or len(set(numbers)) != len(numbers)
            or any(not isinstance(number, int) or not 0 <= number < SHARDS for number in numbers)
        ):
            raise ValueError("Gauge refresh requires one to sixteen distinct valid shards")
        return numbers

    def _state(self, name, kind, width):
        return (
            f"SELECT shard,entity_id,target_id,argMaxMerge(value) AS v FROM analytics_{name}_state "
            f"WHERE kind='{kind}' AND shard IN {{shards:Array(UInt16)}} "
            "GROUP BY shard,entity_id,target_id "
            f"HAVING tuple(v.{width - 1},v.{width})>={self._reset(kind)} AND v.1=0"
        )

    async def _feed_many(self, numbers):
        numbers = self._bounded_shards(numbers)
        accounts = self._state("audience", "account", 9)
        feeds = self._state("audience", "feed", 9)
        rows = await self.client.query(
            "SELECT shard,countIf(status='ready') AS ready,"
            "countIf(status='refreshing') AS refreshing,"
            "countIf(status='expired') AS expired,countIf(status='pending') AS pending,"
            "countIf(status='not_needed') AS not_needed,countIf(eligible AND due) AS overdue,"
            "countIf(eligible AND status='ready' AND NOT has_recommendations) "
            "AS empty_with_interests,"
            "minOrNullIf(next_transition,next_transition>now64(6)) AS next_transition FROM ("
            "SELECT a.shard AS shard,ifNull(f.v.2,0)>0 AS eligible,"
            "ifNull(f.v.5,0) AS has_recommendations,"
            "ifNull(f.v.7<=now64(6),0) AS due,"
            "arrayMin(arrayFilter(deadline -> deadline>now64(6),[f.v.6,f.v.7])) "
            "AS next_transition,"
            "multiIf(NOT eligible,'not_needed',f.v.4=0,'pending',f.v.3=1,'refreshing',"
            "f.v.6 IS NULL OR f.v.6<=now64(6),'expired','ready') AS status "
            f"FROM ({accounts}) a LEFT JOIN ({feeds}) f "
            "ON a.shard=f.shard AND a.entity_id=f.entity_id) GROUP BY shard",
            {"shards": numbers},
        )
        values = {row["shard"]: dict(row) for row in rows}
        transitions = {}
        records = []
        for number in numbers:
            row = values.get(number, {"shard": number, **dict.fromkeys(FEED_METRICS, 0)})
            transitions[number] = row.pop("next_transition", None)
            records.append(row)
        return records, transitions

    async def _feed(self, number):
        rows, transitions = await self._feed_many([number])
        return [
            {key: value for key, value in row.items() if key != "shard"} for row in rows
        ], transitions[number]

    async def _dimensions_many(self, domain, numbers):
        numbers = self._bounded_shards(numbers)
        kind = "source" if domain == "sources" else "topic"
        dims = self._state("dimension", kind, 6)
        if kind == "source":
            query = (
                "SELECT shard,entity_id AS source_id,v.2 AS name,v.3 AS consecutive_failures "
                f"FROM ({dims})"
            )
        else:
            follow = self._state("membership", "follow", 3)
            inferred = self._state("membership", "inferred", 3)
            query = (
                "SELECT d.shard AS shard,d.entity_id AS topic_id,d.v.2 AS name,"
                "ifNull(f.followers,0) AS followers,ifNull(i.inferred_users,0) AS inferred_users "
                f"FROM ({dims}) d LEFT JOIN (SELECT shard,target_id,count() AS followers "
                f"FROM ({follow}) GROUP BY shard,target_id) f "
                "ON d.shard=f.shard AND d.entity_id=f.target_id LEFT JOIN "
                f"(SELECT shard,target_id,count() AS inferred_users FROM ({inferred}) "
                "GROUP BY shard,target_id) i ON d.shard=i.shard AND d.entity_id=i.target_id "
                "WHERE d.v.4='active'"
            )
        rows = await self.client.query(query, {"shards": numbers})
        return rows, dict.fromkeys(numbers)

    async def _dimensions(self, domain, number):
        rows, _transitions = await self._dimensions_many(domain, [number])
        return [{key: value for key, value in row.items() if key != "shard"} for row in rows], None

    async def _queues(self, connection):
        queries = [
            sql.SQL(
                "SELECT %s AS kind,count(*) FILTER(WHERE status='queued') AS queued,"
                "count(*) FILTER(WHERE status='running') AS running,"
                "min(created_at) FILTER(WHERE status='queued') AS oldest_queued_at FROM {} "
                "WHERE status IN ('queued','running')"
            ).format(sql.Identifier(table))
            for table in JOB_KINDS
        ]
        rows = await (
            await connection.execute(
                sql.SQL(" UNION ALL ").join(queries), tuple(JOB_KINDS.values())
            )
        ).fetchall()
        return rows, datetime.now(UTC) + timedelta(seconds=15)

    async def refresh_one(self, connection, generation):
        rows = await (
            await connection.execute(
                "WITH next_domain AS MATERIALIZED ("
                "SELECT domain FROM analytics_gauge_refreshes "
                "WHERE available_at<=clock_timestamp() ORDER BY available_at,domain,shard LIMIT 1) "
                "SELECT r.domain,r.shard,r.revision FROM analytics_gauge_refreshes r "
                "JOIN next_domain d ON r.domain=d.domain WHERE r.available_at<=clock_timestamp() "
                "ORDER BY r.available_at,r.shard LIMIT 16"
            )
        ).fetchall()
        if not rows:
            return False
        domain = rows[0]["domain"]
        numbers = [row["shard"] for row in rows]
        if domain == "queues":
            records, transition = await self._queues(connection)
            records = [{**record, "shard": 0} for record in records]
            transitions = {0: transition}
            target = "queue"
        elif domain == "feed":
            records, transitions = await self._feed_many(numbers)
            target = "feed"
        else:
            records, transitions = await self._dimensions_many(domain, numbers)
            target = "source" if domain == "sources" else "topic"
        await self.client.insert(
            f"analytics_{target}_gauges",
            [{**record, "generation": generation} for record in records],
        )
        now = datetime.now(UTC)
        await self.client.insert(
            "analytics_gauge_manifest",
            [
                dict(domain=domain, shard=number, generation=generation, updated_at=now)
                for number in numbers
            ],
        )
        # Preserve raw revisions; only current snapshots expire. Quiet shards
        # renew before the seven-day TTL, independently of active source changes.
        renewal = now + timedelta(days=1)
        available = []
        for number in numbers:
            transition = transitions[number]
            if isinstance(transition, str):
                transition = datetime.fromisoformat(transition)
            available.append(min(transition, renewal) if transition is not None else renewal)
        await connection.execute(
            "UPDATE analytics_gauge_refreshes r SET available_at=done.available_at FROM "
            "unnest(%s::smallint[],%s::bigint[],%s::timestamptz[]) "
            "AS done(shard,revision,available_at) WHERE r.domain=%s "
            "AND r.shard=done.shard AND r.revision=done.revision",
            (numbers, [row["revision"] for row in rows], available, domain),
        )
        await connection.execute(
            "UPDATE analytics_gauge_refreshes SET bootstrap_required=false "
            "WHERE domain=%s AND shard=ANY(%s::smallint[]) AND bootstrap_generation<=%s",
            (domain, numbers, generation),
        )
        return True

    async def bootstrap_complete(self, connection):
        # Called only after source backfill/import coverage is complete. Establish
        # a finite barrier; later changes affect lag without starving readiness.
        async with connection.transaction():
            started = await (
                await connection.execute(
                    "SELECT started FROM analytics_gauge_bootstrap WHERE singleton FOR UPDATE"
                )
            ).fetchone()
            if not started["started"]:
                await connection.execute(
                    "UPDATE analytics_gauge_refreshes SET bootstrap_required=true,"
                    "bootstrap_generation=(SELECT lease_generation+1 "
                    "FROM analytics_pipeline_state WHERE singleton),"
                    "available_at=LEAST(available_at,clock_timestamp())"
                )
                await connection.execute(
                    "UPDATE analytics_gauge_bootstrap SET started=true WHERE singleton"
                )
        row = await (
            await connection.execute(
                "SELECT NOT EXISTS(SELECT 1 FROM analytics_gauge_refreshes "
                "WHERE bootstrap_required) AS complete"
            )
        ).fetchone()
        return row["complete"]
