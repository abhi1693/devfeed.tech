"""Durable current operator observations published as immutable ClickHouse shards.

PostgreSQL holds only bounded invalidations and coverage cursors. The native
analytics lease owns publication; the manifest is written after its complete
shard, and serving never reads the wide canonical store.
"""

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime

from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from devfeed_core.analysis import reuse_candidates
from devfeed_core.analytics_card_builders import article_rows, proposal_rows, source_rows
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.analytics_schema import CARD_BACKFILL_TABLES, CARD_SOURCES
from devfeed_core.catalog_cache import reuse_snapshots
from devfeed_core.config import get_settings

KINDS = tuple(CARD_BACKFILL_TABLES)
TIMER_KINDS = ("article", "source", "candidate")
SHARDS = 256
BUILD_BATCH_SIZE = 200
# Publish several dirty shards together without hydrating more source rows or
# allowing a population-sized INSERT SELECT. Each batch uses one serving insert
# and one manifest insert rather than separate writes for every dirty shard.
PUBLISH_SHARD_BATCH_SIZE = 8
REFRESH_CANDIDATE_LIMIT = BUILD_BATCH_SIZE * PUBLISH_SHARD_BATCH_SIZE
SCAN_BATCH_SIZE = 1000
QUEUE_HIGH_WATERMARK = 2000
ZERO_UUID = uuid.UUID(int=0)
TIMER_DUE_SQL = (
    "SELECT kind,entity_id,generation FROM analytics_card_timers "
    "WHERE policy=%s AND epoch=%s AND available_at<=statement_timestamp() "
    "ORDER BY available_at,kind,entity_id LIMIT %s"
)
REFRESH_DUE_SQL = (
    "WITH initial AS MATERIALIZED (SELECT kind,entity_id,generation,initial_pending,available_at "
    "FROM analytics_card_refresh WHERE initial_pending AND available_at<=statement_timestamp() "
    "ORDER BY available_at,kind,entity_id LIMIT %s), "
    "ordinary AS MATERIALIZED (SELECT kind,entity_id,generation,initial_pending,available_at "
    "FROM analytics_card_refresh WHERE NOT initial_pending "
    "AND available_at<=statement_timestamp() AND (SELECT count(*) FROM initial)<%s "
    "ORDER BY available_at,kind,entity_id LIMIT %s), "
    "candidates AS MATERIALIZED (SELECT *,get_byte(uuid_send(entity_id),0) AS shard FROM "
    "(SELECT * FROM initial UNION ALL SELECT * FROM ordinary) AS pending "
    "ORDER BY initial_pending DESC,available_at,kind,entity_id LIMIT %s), "
    "oldest AS (SELECT kind FROM candidates ORDER BY initial_pending DESC,available_at,"
    "kind,entity_id LIMIT 1), shards AS (SELECT shard,bool_or(initial_pending) AS initial,"
    "min(available_at) AS available FROM candidates WHERE kind=(SELECT kind FROM oldest) "
    "GROUP BY shard ORDER BY initial DESC,available,shard LIMIT %s) "
    "SELECT r.kind,r.entity_id,r.generation FROM candidates r JOIN shards s ON r.shard=s.shard "
    "WHERE r.kind=(SELECT kind FROM oldest) ORDER BY r.initial_pending DESC,s.initial DESC,"
    "s.available,s.shard,r.entity_id LIMIT %s"
)
COLUMNS = (
    ("policy", "String"),
    ("kind", "LowCardinality(String)"),
    ("shard", "UInt16"),
    ("entity_id", "UUID"),
    ("version", "UInt128"),
    ("deleted", "UInt8"),
    ("stage", "LowCardinality(String)"),
    ("state", "LowCardinality(String)"),
    ("source_id", "Nullable(UUID)"),
    ("source_name", "Nullable(String)"),
    ("source_sort", "String"),
    ("source_ids", "Array(UUID)"),
    ("search_text", "String"),
    ("literal_search_text", "String"),
    ("source_type", "LowCardinality(String)"),
    ("created_at", "DateTime64(6,'UTC')"),
    ("activity_at", "DateTime64(6,'UTC')"),
    ("terminal_at", "Nullable(DateTime64(6,'UTC'))"),
    ("has_active_work", "UInt8"),
    ("next_transition_at", "Nullable(DateTime64(6,'UTC'))"),
    ("evaluated_at", "DateTime64(6,'UTC')"),
    ("catalog_sensitive", "UInt8"),
    ("review_pending", "UInt8"),
    ("enabled", "UInt8"),
    ("approval_status", "LowCardinality(String)"),
    ("consecutive_failures", "UInt32"),
    ("blocker_codes", "Array(String)"),
    ("title", "String"),
    ("editorial_revision", "UInt64"),
    ("semantic_hash", "String"),
    ("payload", "String"),
)
COLUMN_NAMES = ",".join(name for name, _ in COLUMNS)
SCOPE = "policy={policy:String} AND kind={kind:String} AND shard={shard:UInt16}"
RESET = (
    "(SELECT coalesce(max(version),toUInt128(0)) FROM analytics_card_resets "
    "WHERE policy={policy:String} AND kind={kind:String})"
)
CARD_EVENT_ROUTES = {
    **{table: (("id", kind),) for kind, table in CARD_BACKFILL_TABLES.items()},
    **dict.fromkeys(
        (
            "article_contents",
            "article_origins",
            "article_topics",
            "article_reviews",
            "article_enrichment_jobs",
            "article_analysis_jobs",
        ),
        (("article_id", "article"),),
    ),
    **dict.fromkeys(
        ("source_reviews", "source_enrichment_jobs", "ingestion_jobs"),
        (("source_id", "source"),),
    ),
    "source_candidates": (("id", "candidate"), ("source_id", "source")),
    "article_image_jobs": (("article_id", "article"), ("source_id", "source")),
    "source_discovery_jobs": (("candidate_id", "candidate"),),
    **dict.fromkeys(
        ("topic_analysis_jobs", "topic_decision_runs", "topic_decision_calls"),
        (("proposal_id", "topic-proposal"),),
    ),
}


def card_initialization_sql():
    fields = ",".join(f"{name} {kind}" for name, kind in COLUMNS)
    return (
        "CREATE TABLE IF NOT EXISTS analytics_card_canonical (" + fields + ") "
        "ENGINE=ReplacingMergeTree(version) ORDER BY(policy,kind,shard,entity_id)",
        "CREATE TABLE IF NOT EXISTS analytics_cards (" + fields + ",generation UInt64,"
        "built_at DateTime64(6,'UTC')) ENGINE=MergeTree "
        "ORDER BY(policy,kind,shard,generation,entity_id) TTL built_at+INTERVAL 7 DAY DELETE",
        "CREATE TABLE IF NOT EXISTS analytics_card_manifest (policy String,"
        "kind LowCardinality(String),shard UInt16,generation UInt64,"
        "updated_at DateTime64(6,'UTC'),item_count UInt64) "
        "ENGINE=ReplacingMergeTree(generation) ORDER BY(policy,kind,shard)",
        "CREATE TABLE IF NOT EXISTS analytics_card_resets (policy String,kind String,"
        "version UInt128,updated_at DateTime64(6,'UTC')) "
        "ENGINE=ReplacingMergeTree(version) ORDER BY(policy,kind)",
        "CREATE TABLE IF NOT EXISTS analytics_card_status (singleton UInt8 DEFAULT 1,"
        "version UInt64,policy String,target_policy String,ready UInt8,"
        "generated_at DateTime64(6,'UTC'),updated_at DateTime64(6,'UTC'),"
        "catalog_revision String,catalog_target_revision String,"
        "catalog_progress_at Nullable(DateTime64(6,'UTC')),lag_seconds Float64) "
        "ENGINE=ReplacingMergeTree(version) ORDER BY singleton",
    )


def identity(value):
    return uuid.UUID(str(value))


def shard_of(value):
    return identity(value).bytes[0]


def normalize_card(row, policy, kind, entity_id, generation, now):
    """Produce one typed safe row; absent selected source identities are tombstones."""
    row = dict(row or {})
    payload = dict(row.get("payload") or {})
    result = dict(
        policy=policy,
        kind=kind,
        shard=shard_of(entity_id),
        entity_id=str(entity_id),
        version=generation << 64,
        deleted=int(not bool(row)),
        stage=row.get("stage", ""),
        state=row.get("state", ""),
        source_id=str(row["source_id"]) if row.get("source_id") else None,
        source_name=row.get("source_name"),
        source_sort=row.get("source_sort", ""),
        source_ids=[str(value) for value in row.get("source_ids", [])],
        search_text=row.get("search_text", ""),
        literal_search_text=row.get("literal_search_text", "[]"),
        source_type=str(row.get("source_type", "")),
        created_at=row.get("created_at", now),
        activity_at=row.get("activity_at", now),
        terminal_at=row.get("terminal_at"),
        has_active_work=int(row.get("has_active_work", False)),
        next_transition_at=row.get("next_transition_at"),
        evaluated_at=row.get("evaluated_at", now),
        catalog_sensitive=int(row.get("catalog_sensitive", False)),
        review_pending=int(row.get("review_pending", False)),
        enabled=int(row.get("enabled", False)),
        approval_status=row.get("approval_status", ""),
        consecutive_failures=max(0, row.get("consecutive_failures", 0)),
        blocker_codes=sorted(row.get("blocker_codes", [])),
        title=row.get("title", ""),
        editorial_revision=row.get("editorial_revision", 0),
        payload=json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str),
    )
    semantic = {
        key: value
        for key, value in result.items()
        if key not in {"version", "evaluated_at", "payload"}
    }
    semantic["payload"] = {key: value for key, value in payload.items() if key != "next_check_at"}
    if not row:
        # A repeated deletion is the same semantic state regardless of wall time.
        semantic.pop("created_at")
        semantic.pop("activity_at")
    result["semantic_hash"] = hashlib.sha256(
        json.dumps(semantic, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()
    return result


def _source_fanout(event):
    table = event["source_table"]
    if table == "source_candidates":
        return "source_id"
    if table != "sources":
        return None
    before, after = event.get("before_record"), event.get("record") or {}
    changed = (
        before is None
        or event["operation"] == "D"
        or any(
            before.get(field) != after.get(field)
            for field in ("name", "approval_status", "enabled")
        )
    )
    return "id" if changed else None


def event_invalidations(events):
    """Only identities leave captured metadata; include before and after owners."""
    dirty = set()
    fanout = set()
    research = set()
    reset = False
    for event in events:
        table = event["source_table"]
        if event["operation"] == "T":
            reset |= table in CARD_SOURCES
            continue
        fanout_field = _source_fanout(event)
        for record in (event.get("before_record"), event.get("record")):
            if not record:
                continue
            if table in {"topics", "catalog_revisions"}:
                fanout.add(("catalog", ZERO_UUID))
            for field, kind in CARD_EVENT_ROUTES.get(table, ()):
                if record.get(field):
                    dirty.add((kind, identity(record[field])))
            if fanout_field and record.get(fanout_field):
                fanout.add(("source", identity(record[fanout_field])))
            if table in {"topic_analysis_jobs", "research_verification_jobs"} and record.get("id"):
                research.add(identity(record["id"]))
    return dirty, fanout, research, reset


class CardPublisher:
    def __init__(self, client, settings=None):
        self.client = client
        self.settings = settings or get_settings()
        self._engine = None
        self._factory = None
        self._initialized = False
        self._turn = 0
        self._backfill_kind = 0
        self._last_status = 0.0
        self._last_status_key = None
        self._catalog_snapshots: dict = {}
        self._candidate_rankings: list = []
        self.bootstrapping = True

    def close(self):
        self._catalog_snapshots.clear()
        self._candidate_rankings.clear()
        if self._engine is not None:
            self._engine.dispose()
            self._engine = self._factory = None

    def _build(self, kind, identifiers, policy, now):
        if len(identifiers) > BUILD_BATCH_SIZE:
            raise ValueError("Current card hydration must contain at most 200 identities")
        if self._factory is None:
            self._engine = create_engine(self.settings.database_url, poolclass=NullPool)
            self._factory = sessionmaker(self._engine, expire_on_commit=False)
        with (
            reuse_snapshots(
                retained=self._catalog_snapshots, on_invalidate=self._candidate_rankings.clear
            ),
            reuse_candidates(retained=self._candidate_rankings),
        ):
            if kind == "article":
                return article_rows(self._factory, identifiers, policy, now)
            if kind in {"source", "candidate"}:
                return source_rows(
                    self._factory,
                    identifiers if kind == "source" else [],
                    identifiers if kind == "candidate" else [],
                    policy,
                    now,
                )
            return proposal_rows(
                self._factory,
                identifiers if kind == "topic-proposal" else [],
                identifiers if kind == "relationship-proposal" else [],
                policy,
                now,
            )

    async def _initialize(self):
        if not self._initialized:
            for statement in card_initialization_sql():
                await self.client.execute(statement)
            self._initialized = True

    async def _state(self, connection):
        return await (
            await connection.execute(
                "SELECT card_target_policy,card_serving_policy,card_bootstrap_complete,"
                "card_bootstrap_barrier,card_catalog_revision,card_catalog_target_revision,"
                "card_catalog_progress_at FROM analytics_pipeline_state WHERE singleton"
            )
        ).fetchone()

    async def _catalog_revision(self, connection):
        rows = await (
            await connection.execute(
                "SELECT name,revision FROM catalog_revisions WHERE name IN "
                "('topics','tags') ORDER BY name"
            )
        ).fetchall()
        return [[row["name"], str(row["revision"])] for row in rows]

    async def _mark(self, connection, dirty, generation, initial, *, fanout=False):
        if not dirty:
            return
        ordered = sorted(dirty)
        table = "analytics_card_fanout" if fanout else "analytics_card_refresh"
        # A fanout finishes its active cohort before restarting for the newest
        # invalidation. Exact-card refreshes keep their original deadline.
        requested = "requested_at=excluded.requested_at," if fanout else ""
        await connection.execute(
            f"INSERT INTO {table}(kind,entity_id,generation,initial_pending) "
            "SELECT kind,entity_id,%s,%s FROM unnest(%s::text[],%s::uuid[]) AS d(kind,entity_id) "
            "ON CONFLICT(kind,entity_id) DO UPDATE SET "
            f"generation=greatest({table}.generation,excluded.generation),"
            + requested
            + f"initial_pending={table}.initial_pending OR excluded.initial_pending",
            (generation, initial, [kind for kind, _ in ordered], [value for _, value in ordered]),
        )

    async def _reset(self, connection, policy, generation, *, fence):
        now = datetime.now(UTC)
        async with connection.transaction():
            if fence is not None:
                await fence(connection)
            state = await self._state(connection)
            if state["card_serving_policy"] == policy:
                # Withdraw the old same-policy manifest before exposing reset
                # generations. A failed PG commit stays safely unavailable.
                await self.client.insert(
                    "analytics_card_status",
                    [
                        dict(
                            singleton=1,
                            version=generation,
                            policy=policy,
                            target_policy=policy,
                            ready=0,
                            generated_at=now,
                            updated_at=now,
                            lag_seconds=0,
                            catalog_revision=json.dumps(state["card_catalog_revision"] or []),
                            catalog_target_revision=json.dumps(
                                state["card_catalog_target_revision"] or []
                            ),
                            catalog_progress_at=now,
                        )
                    ],
                )
            await self.client.insert(
                "analytics_card_resets",
                [
                    dict(policy=policy, kind=kind, version=generation << 64, updated_at=now)
                    for kind in KINDS
                ],
            )
            await self.client.insert(
                "analytics_card_manifest",
                [
                    dict(
                        policy=policy,
                        kind=kind,
                        shard=shard,
                        generation=generation,
                        updated_at=now,
                        item_count=0,
                    )
                    for kind in KINDS
                    for shard in range(SHARDS)
                ],
            )
            await connection.execute("DELETE FROM analytics_card_refresh")
            await connection.execute("DELETE FROM analytics_card_fanout")
            await connection.execute("DELETE FROM analytics_card_backfills")
            await connection.execute(
                "UPDATE analytics_card_timer_bootstrap SET policy=%s,epoch=%s,kind_index=0,"
                "shard=NULL,cursor=NULL,complete=true,cleanup_pending=true WHERE singleton",
                (policy, generation),
            )
            await connection.execute(
                "INSERT INTO analytics_card_backfills(kind) SELECT unnest(%s::text[])",
                (list(KINDS),),
            )
            revision = await self._catalog_revision(connection)
            await connection.execute(
                "UPDATE analytics_pipeline_state SET card_target_policy=%s,"
                "card_bootstrap_complete=false,card_bootstrap_barrier=false,"
                "card_catalog_target_revision=%s,card_catalog_progress_at=clock_timestamp() "
                "WHERE singleton",
                (policy, Jsonb(revision)),
            )

    async def ingest(self, connection, events, generation, *, fence=None):
        await self._initialize()
        dirty, fanout, research, reset = event_invalidations(events)
        policy = pipeline_policy_version(self.settings)
        state = await self._state(connection)
        if reset or state["card_target_policy"] != policy:
            await self._reset(connection, policy, generation, fence=fence)
            state = await self._state(connection)
        async with connection.transaction():
            if fence is not None:
                await fence(connection)
            if research:
                jobs = list(research)
                rows = await (
                    await connection.execute(
                        "SELECT proposal_id FROM topic_analysis_jobs WHERE id=ANY(%s) "
                        "AND proposal_id IS NOT NULL",
                        (jobs,),
                    )
                ).fetchall()
                dirty.update(("topic-proposal", row["proposal_id"]) for row in rows)
                rows = await (
                    await connection.execute(
                        "SELECT id FROM topic_relation_proposals WHERE job_id=ANY(%s)", (jobs,)
                    )
                ).fetchall()
                dirty.update(("relationship-proposal", row["id"]) for row in rows)
            candidates = [entity_id for kind, entity_id in dirty if kind == "candidate"]
            if candidates:
                rows = await (
                    await connection.execute(
                        "SELECT source_id FROM source_candidates WHERE id=ANY(%s) "
                        "AND source_id IS NOT NULL",
                        (candidates,),
                    )
                ).fetchall()
                dirty.update(("source", row["source_id"]) for row in rows)
            initial = not state["card_bootstrap_barrier"]
            await self._mark(connection, dirty, generation, initial)
            await self._mark(connection, fanout, generation, initial, fanout=True)
            if ("catalog", ZERO_UUID) in fanout:
                revision = await self._catalog_revision(connection)
                await connection.execute(
                    "UPDATE analytics_pipeline_state SET card_catalog_target_revision=%s,"
                    "card_catalog_progress_at=coalesce(card_catalog_progress_at,clock_timestamp()) "
                    "WHERE singleton",
                    (Jsonb(revision),),
                )

    async def _backfill_one(self, connection, generation, state, *, fence):
        if await self._saturated(connection):
            return False
        async with connection.transaction():
            await fence(connection)
            row = await (
                await connection.execute(
                    "SELECT kind,cursor FROM analytics_card_backfills WHERE NOT complete "
                    "ORDER BY mod(array_position(%s::text[],kind)-1-%s+5,5) LIMIT 1",
                    (list(KINDS), self._backfill_kind),
                )
            ).fetchone()
            if row is None:
                return False
            table = CARD_BACKFILL_TABLES[row["kind"]]
            rows = await (
                await connection.execute(
                    sql.SQL(
                        "SELECT id FROM {} WHERE (%s::uuid IS NULL OR id>%s::uuid) ORDER "
                        "BY id LIMIT %s"
                    ).format(sql.Identifier(table)),
                    (row["cursor"], row["cursor"], SCAN_BATCH_SIZE),
                )
            ).fetchall()
            await self._mark(
                connection, {(row["kind"], record["id"]) for record in rows}, generation, True
            )
            await connection.execute(
                "UPDATE analytics_card_backfills SET cursor=%s,complete=%s WHERE kind=%s",
                (
                    rows[-1]["id"] if rows else row["cursor"],
                    len(rows) < SCAN_BATCH_SIZE,
                    row["kind"],
                ),
            )
            self._backfill_kind = (KINDS.index(row["kind"]) + 1) % len(KINDS)
        return True

    async def _fanout_one(self, connection, generation, state, *, fence):
        if await self._saturated(connection):
            return False
        async with connection.transaction():
            await fence(connection)
            row = await (
                await connection.execute(
                    "SELECT "
                    "kind,entity_id,cursor,generation,initial_pending,requested_at,available_at "
                    "FROM analytics_card_fanout WHERE available_at<=clock_timestamp() "
                    "ORDER BY available_at,kind,entity_id LIMIT 1"
                )
            ).fetchone()
        if row is None:
            return False
        if row["kind"] == "source":
            async with connection.transaction():
                rows = await (
                    await connection.execute(
                        "SELECT DISTINCT article_id AS id FROM article_origins WHERE source_id=%s "
                        "AND (%s::uuid IS NULL OR article_id>%s::uuid) ORDER BY "
                        "article_id LIMIT %s",
                        (row["entity_id"], row["cursor"], row["cursor"], SCAN_BATCH_SIZE),
                    )
                ).fetchall()
        else:
            rows = await self.client.query(
                "SELECT entity_id AS id FROM analytics_card_canonical FINAL "
                "PREWHERE policy={policy:String} AND kind='article' "
                "WHERE deleted=0 AND catalog_sensitive=1 AND entity_id>{cursor:UUID} "
                "AND version>=" + RESET.replace("{kind:String}", "'article'") + " "
                "ORDER BY entity_id LIMIT {limit:UInt32} SETTINGS max_block_size=1024",
                dict(
                    policy=state["card_target_policy"],
                    cursor=row["cursor"] or ZERO_UUID,
                    limit=SCAN_BATCH_SIZE,
                ),
            )
        dirty = {("article", identity(record["id"])) for record in rows}
        async with connection.transaction():
            await fence(connection)
            await self._mark(connection, dirty, generation, row["initial_pending"])
            if len(rows) == SCAN_BATCH_SIZE:
                await connection.execute(
                    "UPDATE analytics_card_fanout SET cursor=%s WHERE kind=%s AND "
                    "entity_id=%s AND generation=%s",
                    (identity(rows[-1]["id"]), row["kind"], row["entity_id"], row["generation"]),
                )
            elif row["requested_at"] > row["available_at"]:
                await connection.execute(
                    "UPDATE analytics_card_fanout SET cursor=NULL,available_at=requested_at,"
                    "initial_pending=false "
                    "WHERE kind=%s AND entity_id=%s AND generation=%s",
                    (row["kind"], row["entity_id"], row["generation"]),
                )
            else:
                await connection.execute(
                    "DELETE FROM analytics_card_fanout WHERE kind=%s AND entity_id=%s "
                    "AND generation=%s",
                    (row["kind"], row["entity_id"], row["generation"]),
                )
            if row["kind"] == "catalog":
                await connection.execute(
                    "UPDATE analytics_pipeline_state SET "
                    "card_catalog_progress_at=clock_timestamp() WHERE singleton"
                )
        return True

    async def _saturated(self, connection):
        # Stop after a fixed number of narrow queue records, rather than count a
        # growing population. Dirty cards keep progressing while scans pause.
        row = await (
            await connection.execute(
                "SELECT EXISTS(SELECT 1 FROM analytics_card_refresh "
                "OFFSET %s LIMIT 1) AS saturated",
                (QUEUE_HIGH_WATERMARK,),
            )
        ).fetchone()
        return bool(row and row["saturated"])

    async def _publish_shard(self, connection, policy, kind, shard, generation, *, fence):
        await self._publish_shards(connection, policy, kind, [shard], generation, fence=fence)

    async def _publish_shards(self, connection, policy, kind, shards, generation, *, fence):
        shards = sorted(set(shards))
        if not shards:
            return
        if len(shards) > PUBLISH_SHARD_BATCH_SIZE:
            raise ValueError("Too many analytical shards in one publication")
        now = datetime.now(UTC)
        parameters = dict(policy=policy, kind=kind, shards=shards, generation=generation, now=now)
        scope = "policy={policy:String} AND kind={kind:String} AND shard IN {shards:Array(UInt16)}"
        await self.client.execute(
            "INSERT INTO analytics_cards (" + COLUMN_NAMES + ",generation,built_at) "
            "SELECT " + COLUMN_NAMES + ",{generation:UInt64},{now:DateTime64(6,'UTC')} "
            "FROM analytics_card_canonical FINAL PREWHERE " + scope + " "
            "WHERE deleted=0 AND version>=" + RESET + " SETTINGS max_block_size=1024",
            parameters,
        )
        rows = await self.client.query(
            "SELECT shard,count() AS count FROM analytics_cards WHERE " + scope + " "
            "AND generation={generation:UInt64} GROUP BY shard",
            parameters,
        )
        counts = {int(row["shard"]): int(row["count"]) for row in rows}
        async with connection.transaction():
            await fence(connection)
            await self.client.insert(
                "analytics_card_manifest",
                [
                    dict(
                        policy=policy,
                        kind=kind,
                        shard=shard,
                        generation=generation,
                        updated_at=now,
                        item_count=counts.get(shard, 0),
                    )
                    for shard in shards
                ],
            )

    async def _refresh_one(self, connection, generation, state, *, fence):
        async with connection.transaction():
            row = await (
                await connection.execute(
                    REFRESH_DUE_SQL,
                    (REFRESH_CANDIDATE_LIMIT,) * 4 + (PUBLISH_SHARD_BATCH_SIZE, BUILD_BATCH_SIZE),
                )
            ).fetchall()
        if not row:
            return False
        kind = row[0]["kind"]
        identifiers = [record["entity_id"] for record in row]
        policy = state["card_target_policy"]
        now = datetime.now(UTC)
        built = await asyncio.to_thread(self._build, kind, identifiers, policy, now)
        observed = {identity(item["entity_id"]): item for item in built}
        canonical = [
            normalize_card(observed.get(identity(value)), policy, kind, value, generation, now)
            for value in identifiers
        ]
        if kind not in TIMER_KINDS and any(
            row["next_transition_at"] is not None for row in canonical
        ):
            raise RuntimeError(f"Analytical deadlines are not indexed for card kind {kind}")
        shards = sorted({shard_of(value) for value in identifiers})
        existing = await self.client.query(
            "SELECT entity_id,semantic_hash,version FROM analytics_card_canonical FINAL PREWHERE "
            "policy={policy:String} AND kind={kind:String} AND shard IN {shards:Array(UInt16)} "
            "AND entity_id IN {ids:Array(UUID)} WHERE version>=" + RESET,
            dict(policy=policy, kind=kind, shards=shards, ids=identifiers),
        )
        hashes = {identity(item["entity_id"]): item["semantic_hash"] for item in existing}
        changed = [
            item
            for item in canonical
            if hashes.get(identity(item["entity_id"])) != item["semantic_hash"]
        ]
        if changed:
            async with connection.transaction():
                await fence(connection)
                await self.client.insert("analytics_card_canonical", changed)
        publish = {item["shard"] for item in changed}
        unchanged = sorted(set(shards) - publish)
        if unchanged:
            manifests = await self.client.query(
                "SELECT shard,generation FROM analytics_card_manifest FINAL WHERE "
                "policy={policy:String} AND kind={kind:String} AND shard IN {shards:Array(UInt16)}",
                dict(policy=policy, kind=kind, shards=unchanged),
            )
            published = {int(item["shard"]): int(item["generation"]) << 64 for item in manifests}
            # Canonical insertion can succeed before a manifest failure. The
            # replay must publish every such shard even in a batch with changes.
            publish.update(
                shard_of(item["entity_id"])
                for item in existing
                if shard_of(item["entity_id"]) in unchanged
                and int(item["version"]) > published.get(shard_of(item["entity_id"]), -1)
            )
        if publish:
            await self._publish_shards(connection, policy, kind, publish, generation, fence=fence)
        async with connection.transaction():
            await fence(connection)
            if kind in TIMER_KINDS:
                control = await self._timer_control(connection, policy)
                await self._write_timers(connection, policy, kind, canonical, generation, control)
            await connection.execute(
                "DELETE FROM analytics_card_refresh AS r USING "
                "unnest(%s::uuid[],%s::bigint[]) AS done(entity_id,generation) "
                "WHERE r.kind=%s AND r.entity_id=done.entity_id AND r.generation<=done.generation",
                (
                    [record["entity_id"] for record in row],
                    [record["generation"] for record in row],
                    kind,
                ),
            )
        return True

    async def _timer_control(self, connection, policy):
        row = await (
            await connection.execute(
                "SELECT policy,epoch,kind_index,shard,cursor,complete,cleanup_pending "
                "FROM analytics_card_timer_bootstrap WHERE singleton FOR UPDATE"
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("Analytics timer bootstrap state is missing")
        if row["policy"] is None:
            await connection.execute(
                "UPDATE analytics_card_timer_bootstrap SET policy=%s WHERE singleton", (policy,)
            )
            row["policy"] = policy
        if row["policy"] != policy:
            raise RuntimeError("Analytics timer policy changed during publication")
        return row

    @staticmethod
    async def _upsert_timers(connection, policy, kind, epoch, rows):
        if not rows:
            return
        await connection.execute(
            "INSERT INTO analytics_card_timers"
            "(policy,kind,entity_id,epoch,generation,available_at) "
            "SELECT %s,%s,t.entity_id,%s,t.generation,t.available_at FROM "
            "unnest(%s::uuid[],%s::bigint[],%s::timestamptz[]) "
            "AS t(entity_id,generation,available_at) "
            "ON CONFLICT(policy,kind,entity_id) DO UPDATE SET "
            "epoch=excluded.epoch,generation=excluded.generation,"
            "available_at=excluded.available_at "
            "WHERE (analytics_card_timers.epoch,analytics_card_timers.generation) "
            "< (excluded.epoch,excluded.generation)",
            (
                policy,
                kind,
                epoch,
                [identity(row["entity_id"]) for row in rows],
                [row["generation"] for row in rows],
                [row["available_at"] for row in rows],
            ),
        )

    async def _write_timers(self, connection, policy, kind, canonical, generation, control):
        rows = [
            dict(
                entity_id=card["entity_id"],
                generation=generation,
                available_at=card["next_transition_at"],
            )
            for card in canonical
        ]
        if control["complete"]:
            empty = [identity(row["entity_id"]) for row in rows if row["available_at"] is None]
            if empty:
                await connection.execute(
                    "DELETE FROM analytics_card_timers WHERE policy=%s AND kind=%s "
                    "AND entity_id=ANY(%s::uuid[]) AND (epoch,generation)<= (%s,%s)",
                    (policy, kind, empty, control["epoch"], generation),
                )
            rows = [row for row in rows if row["available_at"] is not None]
        await self._upsert_timers(connection, policy, kind, control["epoch"], rows)

    async def _timer_cleanup(self, connection, policy, control):
        if not control["cleanup_pending"]:
            return False
        rows = await (
            await connection.execute(
                "WITH stale AS (SELECT * FROM ("
                "(SELECT policy,kind,entity_id,epoch,generation FROM analytics_card_timers "
                "WHERE policy=%s AND epoch=%s AND available_at IS NULL "
                "ORDER BY kind,entity_id LIMIT %s) UNION ALL "
                "(SELECT policy,kind,entity_id,epoch,generation FROM analytics_card_timers "
                "WHERE (policy,epoch)<(%s,%s) "
                "ORDER BY policy DESC,epoch DESC,kind DESC,entity_id DESC LIMIT %s) UNION ALL "
                "(SELECT policy,kind,entity_id,epoch,generation FROM analytics_card_timers "
                "WHERE (policy,epoch)>(%s,%s) "
                "ORDER BY policy,epoch,kind,entity_id LIMIT %s)) candidates LIMIT %s) "
                "DELETE FROM analytics_card_timers AS t USING stale AS s "
                "WHERE (t.policy,t.kind,t.entity_id,t.epoch,t.generation)="
                "(s.policy,s.kind,s.entity_id,s.epoch,s.generation) RETURNING 1",
                (policy, control["epoch"], BUILD_BATCH_SIZE) * 3 + (BUILD_BATCH_SIZE,),
            )
        ).fetchall()
        if not rows:
            await connection.execute(
                "UPDATE analytics_card_timer_bootstrap SET cleanup_pending=false WHERE singleton"
            )
        return bool(rows)

    async def _timer_bootstrap_one(self, connection, generation, state, *, fence):
        # TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Retire only the bounded seed
        # branch and its due-scan bridge after all 0042 installations adopt it.
        # Timer epochs and bounded cleanup above this branch remain permanent.
        policy = state["card_target_policy"]
        async with connection.transaction():
            await fence(connection)
            control = await self._timer_control(connection, policy)
            if control["complete"]:
                return await self._timer_cleanup(connection, policy, control)
        kind = TIMER_KINDS[control["kind_index"]]
        cursor = ""
        shard = control["shard"] if control["shard"] is not None else 0
        parameters = dict(
            policy=policy,
            kind=kind,
            shard=shard,
            limit=getattr(self.settings, "analytics_backfill_batch_size", 1000),
        )
        if control["cursor"] is not None:
            cursor = " AND entity_id>{cursor:UUID}"
            parameters["cursor"] = control["cursor"]
        rows = await self.client.query(
            "SELECT shard,entity_id,version,deleted,next_transition_at,version>="
            + RESET
            + " AS current FROM analytics_card_canonical FINAL PREWHERE "
            "policy={policy:String} AND kind={kind:String} AND shard={shard:UInt16}"
            + cursor
            + " ORDER BY entity_id LIMIT {limit:UInt32} SETTINGS max_block_size=1024",
            parameters,
        )
        timers = []
        for row in rows:
            deadline = row["next_transition_at"]
            if row["current"] and not row["deleted"] and deadline is not None:
                if isinstance(deadline, str):
                    deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=UTC)
                timers.append(
                    dict(
                        entity_id=row["entity_id"],
                        generation=int(row["version"]) >> 64,
                        available_at=deadline,
                    )
                )
        async with connection.transaction():
            await fence(connection)
            current = await self._timer_control(connection, policy)
            if any(
                current[key] != control[key] for key in ("epoch", "kind_index", "shard", "cursor")
            ):
                return False
            await self._upsert_timers(connection, policy, kind, control["epoch"], timers)
            if len(rows) < parameters["limit"] and shard < SHARDS - 1:
                await connection.execute(
                    "UPDATE analytics_card_timer_bootstrap "
                    "SET shard=%s,cursor=NULL WHERE singleton",
                    (shard + 1,),
                )
            elif len(rows) < parameters["limit"]:
                index = control["kind_index"] + 1
                await connection.execute(
                    "UPDATE analytics_card_timer_bootstrap SET "
                    "kind_index=%s,shard=NULL,cursor=NULL,"
                    "complete=%s,cleanup_pending=%s WHERE singleton",
                    (index, index == len(TIMER_KINDS), index == len(TIMER_KINDS)),
                )
            else:
                await connection.execute(
                    "UPDATE analytics_card_timer_bootstrap SET shard=%s,cursor=%s WHERE singleton",
                    (shard, identity(rows[-1]["entity_id"])),
                )
        return True

    async def _timers_one(self, connection, generation, state, *, fence):
        policy = state["card_target_policy"]
        async with connection.transaction():
            await fence(connection)
            control = await self._timer_control(connection, policy)
            rows = await (
                await connection.execute(
                    TIMER_DUE_SQL,
                    (policy, control["epoch"], BUILD_BATCH_SIZE),
                )
            ).fetchall()
            if rows:
                await self._mark(
                    connection, {(row["kind"], row["entity_id"]) for row in rows}, generation, False
                )
                await self._consume_timers(connection, policy, control, rows, generation)
                return True
            if control["complete"]:
                return False
        return await self._timer_bootstrap_due(connection, generation, policy, control, fence=fence)

    async def _consume_timers(self, connection, policy, control, rows, generation):
        action = (
            "DELETE FROM analytics_card_timers AS t USING "
            if control["complete"]
            else "UPDATE analytics_card_timers AS t SET available_at=NULL,"
            "generation=greatest(t.generation,%s) FROM "
        )
        values = (
            [row["kind"] for row in rows],
            [row["entity_id"] for row in rows],
            [row["generation"] for row in rows],
            policy,
            control["epoch"],
        )
        await connection.execute(
            action + "unnest(%s::text[],%s::uuid[],%s::bigint[]) AS d(kind,entity_id,generation) "
            "WHERE t.policy=%s AND t.epoch=%s AND t.kind=d.kind AND t.entity_id=d.entity_id "
            "AND t.generation=d.generation",
            values if control["complete"] else (generation, *values),
        )

    async def _timer_bootstrap_due(self, connection, generation, policy, control, *, fence):
        # TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Keep old due observations
        # visible only while a pre-0043 card population is being indexed.
        rows = []
        for index in range(control["kind_index"], len(TIMER_KINDS)):
            kind = TIMER_KINDS[index]
            scope = ""
            parameters = dict(policy=policy, kind=kind, limit=BUILD_BATCH_SIZE)
            if index == control["kind_index"] and control["shard"] is not None:
                scope = " AND shard>={first_shard:UInt16}"
                parameters["first_shard"] = control["shard"]
            rows = await self.client.query(
                "SELECT kind,entity_id FROM analytics_card_canonical FINAL "
                "PREWHERE policy={policy:String} AND kind={kind:String}" + scope + " "
                "WHERE deleted=0 AND next_transition_at<=now64(6) AND version>=" + RESET + " "
                "ORDER BY next_transition_at,entity_id LIMIT {limit:UInt32} "
                "SETTINGS max_block_size=1024",
                parameters,
            )
            if rows:
                break
        if not rows:
            return False
        async with connection.transaction():
            await fence(connection)
            control = await self._timer_control(connection, policy)
            if control["complete"]:
                return False
            await self._mark(
                connection,
                {(row["kind"], identity(row["entity_id"])) for row in rows},
                generation,
                False,
            )
            await self._upsert_timers(
                connection,
                policy,
                kind,
                control["epoch"],
                [
                    dict(entity_id=row["entity_id"], generation=generation, available_at=None)
                    for row in rows
                ],
            )
        return True

    async def _renew_one(self, connection, generation, state, *, fence):
        rows = await self.client.query(
            "SELECT policy,kind,shard FROM analytics_card_manifest FINAL "
            "WHERE policy={policy:String} AND updated_at<now()-INTERVAL 1 DAY "
            "ORDER BY updated_at,policy,kind,shard LIMIT 1",
            dict(policy=state["card_target_policy"]),
        )
        if not rows:
            return False
        row = rows[0]
        await self._publish_shard(
            connection, row["policy"], row["kind"], int(row["shard"]), generation, fence=fence
        )
        return True

    async def _coverage(self, connection, *, fence):
        async with connection.transaction():
            await fence(connection)
            row = await (
                await connection.execute(
                    "SELECT NOT EXISTS(SELECT 1 FROM analytics_card_backfills WHERE NOT "
                    "complete) AS covered,"
                    "EXISTS(SELECT 1 FROM analytics_card_fanout WHERE initial_pending) AS fanout,"
                    "EXISTS(SELECT 1 FROM analytics_card_refresh WHERE initial_pending) AS dirty,"
                    "EXISTS(SELECT 1 FROM analytics_card_fanout WHERE kind='catalog') AS catalog,"
                    "EXISTS(SELECT 1 FROM analytics_card_refresh) AS any_dirty"
                )
            ).fetchone()
            if row["covered"]:
                await connection.execute(
                    "UPDATE analytics_pipeline_state SET card_bootstrap_barrier=true "
                    "WHERE singleton"
                )
                if not row["fanout"] and not row["dirty"]:
                    await connection.execute(
                        "UPDATE analytics_pipeline_state SET card_bootstrap_complete=true,"
                        "card_serving_policy=card_target_policy WHERE singleton"
                    )
            if not row["catalog"] and not row["any_dirty"]:
                await connection.execute(
                    "UPDATE analytics_pipeline_state SET "
                    "card_catalog_revision=card_catalog_target_revision,"
                    "card_catalog_progress_at=NULL WHERE singleton"
                )

    async def _status(self, connection, generation, *, fence, force=False):
        state = await self._state(connection)
        policy = state["card_serving_policy"] or ""
        target = state["card_target_policy"] or ""
        ready = bool(state["card_bootstrap_complete"] and policy and policy == target)
        self.bootstrapping = not state["card_bootstrap_complete"]
        key = (
            policy,
            target,
            ready,
            state["card_catalog_revision"],
            state["card_catalog_target_revision"],
        )
        if not force and key == self._last_status_key and time.monotonic() - self._last_status < 30:
            return
        async with connection.transaction():
            await fence(connection)
            row = await (
                await connection.execute(
                    "SELECT greatest(0,extract(epoch FROM clock_timestamp()-min(stamp))) AS lag "
                    "FROM (SELECT min(created_at) AS stamp FROM analytics_ingest_events UNION ALL "
                    "SELECT min(requested_at) FROM analytics_card_refresh UNION ALL "
                    "SELECT min(available_at) FROM analytics_card_fanout) pending"
                )
            ).fetchone()
            now = datetime.now(UTC)
            await self.client.insert(
                "analytics_card_status",
                [
                    dict(
                        singleton=1,
                        version=generation,
                        policy=policy,
                        target_policy=target,
                        ready=int(ready),
                        generated_at=now,
                        updated_at=now,
                        lag_seconds=float(row["lag"] or 0),
                        catalog_revision=json.dumps(state["card_catalog_revision"] or []),
                        catalog_target_revision=json.dumps(
                            state["card_catalog_target_revision"] or []
                        ),
                        catalog_progress_at=state["card_catalog_progress_at"],
                    )
                ],
            )
        self._last_status, self._last_status_key = time.monotonic(), key

    async def once(self, connection, generation, *, fence, renew=None):
        await self._initialize()
        if renew is not None and await renew(connection) is None:
            raise RuntimeError("Analytics card publisher lease was superseded")
        state = await self._state(connection)
        policy = pipeline_policy_version(self.settings)
        if state["card_target_policy"] != policy:
            await self._reset(connection, policy, generation, fence=fence)
            await self._status(connection, generation, fence=fence, force=True)
            return True
        # Rotate work classes: continued writes cannot starve source coverage or
        # a catalog/source fan-out, and bootstrap cannot starve ready dirty cards.
        work = (
            self._refresh_one,
            self._backfill_one,
            self._fanout_one,
            self._timers_one,
            self._timer_bootstrap_one,
            self._renew_one,
        )
        changed = False
        for offset in range(len(work)):
            index = (self._turn + offset) % len(work)
            if await work[index](connection, generation, state, fence=fence):
                self._turn = (index + 1) % len(work)
                changed = True
                break
        await self._coverage(connection, fence=fence)
        await self._status(connection, generation, fence=fence)
        return changed
