"""Current operator reads over one frozen vector of ClickHouse shard snapshots."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_observation import AnalyticsSnapshot, pipeline_policy_version
from fastapi import HTTPException

CARD_KINDS = ("article", "source", "candidate", "topic-proposal", "relationship-proposal")
FRESH_SECONDS = 60
SHARDS = 256


def preparing():
    return HTTPException(
        503,
        {"code": "projection_initializing", "message": "Pipeline views are being prepared."},
        headers={"Retry-After": "2"},
    )


def timestamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


@dataclass(frozen=True)
class FrozenCards:
    policy: str
    kinds: tuple[str, ...]
    manifests: tuple[tuple[str, int, int], ...]
    snapshot: AnalyticsSnapshot

    def parameters(self):
        return {
            "policy": self.policy,
            "kinds": self.kinds,
            "manifest_kinds": [row[0] for row in self.manifests],
            "manifest_shards": [row[1] for row in self.manifests],
            "manifest_generations": [row[2] for row in self.manifests],
        }

    @staticmethod
    def source():
        # Three arrays avoid relying on driver-specific Array(Tuple) binding.
        # The same immutable vector is bound to counts, ranked keys and payloads.
        return """analytics_cards c INNER JOIN (
            SELECT tupleElement(item,1) AS kind,tupleElement(item,2) AS shard,
              tupleElement(item,3) AS generation FROM (
                SELECT arrayJoin(arrayZip({manifest_kinds:Array(String)},
                  {manifest_shards:Array(UInt16)},
                  {manifest_generations:Array(UInt64)})) AS item
              )
          ) frozen ON c.kind=frozen.kind AND c.shard=frozen.shard
              AND c.generation=frozen.generation
          WHERE c.policy={policy:String} AND c.kind IN {kinds:Array(String)}
            AND c.deleted=0
            AND tuple(c.kind,c.shard,c.generation) IN arrayZip(
              {manifest_kinds:Array(String)}, {manifest_shards:Array(UInt16)},
              {manifest_generations:Array(UInt64)})"""


class CurrentCards:
    def __init__(self, client, *, now=None):
        self.client = client
        self.now = now or datetime.now(UTC)

    async def freeze(self, kinds):
        kinds = tuple(sorted(set(kinds)))
        if not kinds or set(kinds) - set(CARD_KINDS):
            raise ValueError("Unknown current analytical kind")
        status, policy, manifests = await self._manifest_observation(kinds)
        expected = {(kind, shard) for kind in kinds for shard in range(SHARDS)}
        observed = {(row["kind"], int(row["shard"])) for row in manifests}
        # Refuse partial coverage and snapshots approaching their seven-day TTL.
        # The durable canonical store lets the exporter restore expired shards.
        if expected != observed or any(
            self.now - timestamp(row["updated_at"]) >= timedelta(days=6, hours=23)
            for row in manifests
        ):
            raise preparing()
        vector = tuple(
            (row["kind"], int(row["shard"]), int(row["generation"])) for row in manifests
        )
        lag = max(
            float(status.get("lag_seconds") or 0),
            (self.now - timestamp(status["updated_at"])).total_seconds(),
            0,
        )
        revision = json.loads(status.get("catalog_revision") or "[]")
        target_revision = json.loads(status.get("catalog_target_revision") or "[]")
        catalog_pending = revision != target_revision
        catalog_lag = None
        if catalog_pending and status.get("catalog_progress_at"):
            catalog_lag = max(
                0, (self.now - timestamp(status["catalog_progress_at"])).total_seconds()
            )
            lag = max(lag, catalog_lag)
        rebuilding = policy != status.get("target_policy") or policy != pipeline_policy_version()
        snapshot = AnalyticsSnapshot(
            status="rebuilding"
            if rebuilding
            else "stale"
            if lag > FRESH_SECONDS
            else "catching_up"
            if catalog_pending
            else "fresh",
            generated_at=timestamp(status["generated_at"]),
            generation=policy + ":" + hashlib.sha256(repr(vector).encode()).hexdigest()[:16],
            policy_version=policy,
            article_generation=max((row[2] for row in vector), default=0),
            catalog_revision=revision,
            catalog_target_revision=target_revision,
            catalog_lag_seconds=catalog_lag,
            lag_seconds=lag,
        )
        return FrozenCards(policy, kinds, vector, snapshot)

    async def _manifest_observation(self, kinds):
        status_sql = (
            "/* admin:card-status */ SELECT * FROM analytics_card_status FINAL WHERE singleton=1"
        )
        for _ in range(2):
            rows = await self.client.query(status_sql)
            if not rows or not rows[0]["ready"] or not rows[0]["policy"]:
                raise preparing()
            status = rows[0]
            policy = status["policy"]
            manifests = await self.client.query(
                "/* admin:card-manifest */ SELECT kind,shard,generation,updated_at,item_count "
                "FROM analytics_card_manifest FINAL WHERE policy={policy:String} "
                "AND kind IN {kinds:Array(String)} ORDER BY kind,shard",
                {"policy": policy, "kinds": kinds},
            )
            observed = await self.client.query(status_sql)
            if not observed or not observed[0]["ready"]:
                raise preparing()
            # A same-policy reset withdraws readiness before replacing manifests.
            # Never pair its empty/partial new vector with a previous ready status.
            # One retry also absorbs a policy rollover or ordinary status heartbeat.
            identity = ("version", "policy", "target_policy", "ready")
            if all(status[field] == observed[0][field] for field in identity):
                return observed[0], policy, manifests
        raise preparing()

    async def _query(self, frozen, query, **parameters):
        return await self.client.query(query, frozen.parameters() | parameters)

    async def _payloads(self, frozen, keys):
        if not keys:
            return {}
        rows = await self._query(
            frozen,
            "/* admin:card-payloads */ SELECT c.kind,toString(c.entity_id) AS id,c.payload "
            "FROM " + frozen.source() + " AND c.entity_id IN {ids:Array(UUID)}",
            ids=list({row["id"] for row in keys}),
        )
        payloads = {(row["kind"], str(row["id"])): json.loads(row["payload"]) for row in rows}
        if any((row["kind"], row["id"]) not in payloads for row in keys):
            raise AnalyticsUnavailable("Current card snapshot is being renewed")
        return payloads

    async def article_board(
        self,
        *,
        q,
        source_id,
        needs_attention,
        limit,
        stage=None,
        cursor=0,
        group_by=None,
        source_group=None,
        normalized_q=None,
    ):
        from devfeed_core.analytics_card_types import (
            STAGES,
            PipelineBoard,
            PipelineCard,
            PipelineColumn,
            PipelineSourceGroup,
        )

        frozen = await self.freeze(("article",))
        since = self.now - timedelta(days=7)
        params = {
            "since": since,
            "stages": (stage,) if stage else STAGES,
            "offset": cursor if stage else 0,
            "limit": limit,
        }
        condition = " AND (c.terminal_at IS NULL OR c.terminal_at>={since:DateTime64(6,'UTC')})"
        if q:
            if normalized_q is None:
                raise ValueError("Article search requires the database-normalized scalar needle")
            needle = normalized_q
            condition += (
                " AND position(c.search_text,{needle:String})>0"
                if needle
                else " AND arrayExists(value -> positionCaseInsensitiveUTF8(value,"
                "{needle:String})>0,JSONExtract(c.literal_search_text,'Array(String)'))"
            )
            params["needle"] = needle or q
        if source_id:
            condition += " AND has(c.source_ids,{source_id:UUID})"
            params["source_id"] = str(source_id)
        if source_group is not None:
            condition += (
                " AND c.source_id IS NULL"
                if source_group == "unknown"
                else " AND c.source_id={source_group:UUID}"
            )
            if source_group != "unknown":
                params["source_group"] = str(source_group)
        if needs_attention:
            condition += " AND c.state IN ('blocked','retry_wait')"
        base = frozen.source() + condition
        counts = await self._query(
            frozen,
            "/* admin:article-counts */ SELECT c.stage,count() AS total FROM "
            + base
            + " GROUP BY c.stage",
            **params,
        )
        totals = {row["stage"]: int(row["total"]) for row in counts}
        stages = (stage,) if stage else STAGES
        if group_by == "source":
            rows = await self._query(
                frozen,
                """/* admin:article-source-groups */ SELECT * FROM (
                    SELECT stage,source_id,source_name,total,state_counts,
                      count() OVER(PARTITION BY stage) AS group_count,
                      row_number() OVER(PARTITION BY stage ORDER BY priority,
                        isNull(source_name),source_sort,toString(source_id)) AS position FROM (
                        SELECT stage,source_id,min(source_name) AS source_name,
                          sum(n) AS total,mapFromArrays(groupArray(state),groupArray(n))
                            AS state_counts,min(priority) AS priority,
                          min(source_sort) AS source_sort FROM (
                            SELECT c.stage,c.source_id,c.state,min(c.source_name) AS source_name,
                              min(c.source_sort) AS source_sort,count() AS n,
                              multiIf(c.state='running',0,c.state IN ('blocked','retry_wait'),1,2)
                                AS priority FROM """
                + base
                + """
                              AND c.stage IN {stages:Array(String)}
                            GROUP BY c.stage,c.source_id,c.state
                          ) GROUP BY stage,source_id
                      )
                  ) WHERE position>{offset:UInt64} AND position<={offset:UInt64}+{limit:UInt64}
                  ORDER BY stage,position""",
                **params,
            )
            columns = []
            for key in stages:
                groups = [row for row in rows if row["stage"] == key]
                group_count = int(groups[0]["group_count"]) if groups else 0
                offset = params["offset"]
                columns.append(
                    PipelineColumn(
                        stage=key,
                        total=totals.get(key, 0),
                        items=[],
                        source_groups=[
                            PipelineSourceGroup(
                                **{
                                    field: row[field]
                                    for field in (
                                        "source_id",
                                        "source_name",
                                        "total",
                                        "state_counts",
                                    )
                                }
                            )
                            for row in groups
                        ],
                        next_cursor=offset + limit if offset + limit < group_count else None,
                    )
                )
        else:
            keys = await self._query(
                frozen,
                """/* admin:article-page-keys */ SELECT kind,id,stage FROM (
                    SELECT c.kind,toString(c.entity_id) AS id,c.stage,
                      row_number() OVER(PARTITION BY c.stage ORDER BY
                        if(c.stage IN ('published','rejected'),0,if(c.state='running',0,1)),
                        if(c.stage IN ('published','rejected'),c.terminal_at,NULL) DESC,
                        if(c.stage IN ('published','rejected'),NULL,c.created_at) ASC,
                        if(c.stage IN ('published','rejected'),toString(c.entity_id),'') DESC,
                        if(c.stage IN ('published','rejected'),'',toString(c.entity_id)) ASC)
                          AS position FROM """
                + base
                + """
                      AND c.stage IN {stages:Array(String)}
                  ) WHERE position>{offset:UInt64} AND position<={offset:UInt64}+{limit:UInt64}
                  ORDER BY stage,position""",
                **params,
            )
            payloads = await self._payloads(frozen, keys)
            columns = []
            for key in stages:
                total = totals.get(key, 0)
                items = [
                    PipelineCard.model_validate(payloads[(row["kind"], row["id"])])
                    for row in keys
                    if row["stage"] == key
                ]
                offset = params["offset"]
                columns.append(
                    PipelineColumn(
                        stage=key,
                        total=total,
                        items=items,
                        next_cursor=offset + limit if offset + limit < total else None,
                    )
                )
        return PipelineBoard(
            as_of=self.now,
            terminal_since=since,
            counts={column.stage: column.total for column in columns},
            columns=columns,
            snapshot=frozen.snapshot,
        )

    async def source_board(self, query, *, stage=None, cursor=0, matching_ids=None):
        from devfeed_core.analytics_card_types import (
            SOURCE_STAGES,
            SOURCE_TERMINAL_STAGES,
            SourcePipelineBoard,
            SourcePipelineCard,
            SourcePipelineColumn,
        )

        frozen = await self.freeze(("source", "candidate"))
        params = {
            "since": query.terminal_since,
            "terminal_stages": tuple(SOURCE_TERMINAL_STAGES),
            "stages": (stage,) if stage else SOURCE_STAGES,
            "offset": cursor,
            "limit": query.limit,
        }
        condition = (
            " AND (c.stage NOT IN {terminal_stages:Array(String)} OR c.has_active_work=1"
            " OR c.activity_at>={since:DateTime64(6,'UTC')})"
        )
        if query.q:
            if matching_ids is None:
                raise ValueError("Private URI search requires a bounded source lookup")
            condition += (
                " AND ((c.kind='source' AND c.entity_id IN {sources:Array(UUID)})"
                " OR (c.kind='candidate' AND c.entity_id IN {candidates:Array(UUID)}))"
            )
            params.update(
                sources=list(map(str, matching_ids["source"])),
                candidates=list(map(str, matching_ids["candidate"])),
            )
        if query.source_type:
            condition += " AND c.source_type={source_type:String}"
            params["source_type"] = query.source_type
        if query.needs_attention:
            condition += " AND (c.state IN ('blocked','retry_wait') OR c.stage='review')"
        base = frozen.source() + condition
        counts = await self._query(
            frozen,
            "/* admin:source-counts */ SELECT c.stage,count() AS total FROM "
            + base
            + " GROUP BY c.stage",
            **params,
        )
        totals = {row["stage"]: int(row["total"]) for row in counts}
        keys = await self._query(
            frozen,
            """/* admin:source-page-keys */ SELECT kind,id,stage FROM (
                SELECT c.kind,toString(c.entity_id) AS id,c.stage,
                  row_number() OVER(PARTITION BY c.stage ORDER BY if(c.state='running',0,1),
                    if(c.stage IN {terminal_stages:Array(String)},
                      -toUnixTimestamp64Micro(c.activity_at),toUnixTimestamp64Micro(c.created_at)),
                    c.kind,toString(c.entity_id)) AS position FROM """
            + base
            + """
                  AND c.stage IN {stages:Array(String)}
              ) WHERE position>{offset:UInt64} AND position<={offset:UInt64}+{limit:UInt64}
              ORDER BY stage,position""",
            **params,
        )
        payloads = await self._payloads(frozen, keys)
        columns = []
        for key in params["stages"]:
            items = [
                SourcePipelineCard.model_validate(payloads[(row["kind"], row["id"])])
                for row in keys
                if row["stage"] == key
            ]
            total = totals.get(key, 0)
            columns.append(
                SourcePipelineColumn(
                    stage=key,
                    total=total,
                    items=items,
                    next_cursor=cursor + len(items) if cursor + len(items) < total else None,
                )
            )
        return SourcePipelineBoard(
            as_of=self.now,
            terminal_since=query.terminal_since,
            counts={column.stage: column.total for column in columns},
            columns=columns,
            snapshot=frozen.snapshot,
        )

    async def operations(self):
        from devfeed_core.analytics_blockers import RecoveryTarget, blocker_catalog
        from devfeed_core.config import get_settings

        from devfeed_admin_api.overview_sections import (
            OperationsSection,
            OverviewSectionReport,
            ReviewQueue,
        )

        frozen = await self.freeze(CARD_KINDS)
        if frozen.snapshot.status == "rebuilding":
            raise preparing()
        review_rows = await self._query(
            frozen,
            """/* admin:operations-reviews */ SELECT c.kind,countIf(c.review_pending=1) AS pending,
              minIf(toNullable(c.created_at),c.review_pending=1) AS oldest,
              countIf(c.kind='source' AND c.enabled=1 AND c.approval_status='approved'
                AND c.consecutive_failures>0) AS failures FROM """
            + frozen.source()
            + " GROUP BY c.kind",
        )
        reviews = {row["kind"]: row for row in review_rows}
        blockers = blocker_catalog()
        by_code = {item.code: item for item in blockers}
        groups = await self._query(
            frozen,
            """/* admin:operations-blocker-keys */ SELECT code,count() AS total,
              groupArraySorted(5)(tuple(created_at,toString(entity_id),kind)) AS targets FROM (
                SELECT c.created_at,c.entity_id,c.kind,arrayJoin(c.blocker_codes) AS code FROM """
            + frozen.source()
            + ") GROUP BY code",
        )
        target_ids = {str(target[1]) for row in groups for target in row["targets"]}
        target_rows = []
        if target_ids:
            target_rows = await self._query(
                frozen,
                "/* admin:operations-targets */ SELECT c.kind,toString(c.entity_id) AS id,"
                "c.title,c.editorial_revision FROM "
                + frozen.source()
                + " AND c.entity_id IN {ids:Array(UUID)}",
                ids=sorted(target_ids),
            )
        targets = {(row["kind"], row["id"]): row for row in target_rows}
        for row in groups:
            if row["code"] not in by_code:
                raise AnalyticsUnavailable("Current blocker policy is being prepared")
            item = by_code[row["code"]]
            item.count = int(row["total"])
            item.targets = [
                RecoveryTarget(
                    id=uuid.UUID(str(key[1])),
                    title=target["title"],
                    kind=key[2],
                    revision=int(target["editorial_revision"]),
                )
                for key in row["targets"]
                if (target := targets.get((key[2], str(key[1])))) is not None
            ]
        mapping = {
            "article": "articles",
            "source": "sources",
            "topic-proposal": "topics",
            "relationship-proposal": "relationships",
        }
        return OverviewSectionReport(
            generated_at=self.now,
            days=1,
            snapshot=frozen.snapshot,
            data=OperationsSection(
                full_automation=get_settings().full_automation,
                reviews=[
                    ReviewQueue(
                        kind=name,
                        count=int(reviews.get(kind, {}).get("pending", 0)),
                        oldest_at=reviews.get(kind, {}).get("oldest"),
                    )
                    for kind, name in mapping.items()
                ],
                source_failures=sum(int(row["failures"]) for row in review_rows),
                blockers=blockers,
            ),
        )
