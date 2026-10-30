"""Current operator boards query canonical ClickHouse observations directly."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, NotRequired, Protocol, TypedDict, TypeVar

from devfeed_core.analytics import AnalyticsClient, AnalyticsPreparing, SqlRecord
from devfeed_core.analytics_card_types import (
    PipelineBoard,
    SourcePipelineBoard,
    SourcePipelineStage,
    Stage,
)
from devfeed_core.analytics_observation import AnalyticsSnapshot, pipeline_policy_version
from devfeed_core.analytics_reconcile import read_reconciliation_status
from devfeed_core.source_types import SourceType
from fastapi import HTTPException
from pydantic import BaseModel

from devfeed_admin_api.overview_sections import OverviewSectionReport
from devfeed_admin_api.overview_windows import ReportWindow

Card = TypeVar("Card", bound=BaseModel)


class SourceBoardQuery(Protocol):
    q: str
    source_type: SourceType | None
    needs_attention: bool
    terminal_since: datetime
    limit: int


class ArticleQueryParameters(TypedDict):
    since: datetime
    stages: tuple[Stage, ...]
    offset: int
    limit: int
    needle: NotRequired[str]
    source_id: NotRequired[str]
    source_group: NotRequired[str]


class SourceQueryParameters(TypedDict):
    since: datetime
    terminal_stages: tuple[str, ...]
    stages: tuple[SourcePipelineStage, ...]
    offset: int
    limit: int
    sources: NotRequired[list[str]]
    candidates: NotRequired[list[str]]
    source_type: NotRequired[SourceType]


CARD_KINDS = ("article", "source", "candidate", "topic-proposal", "relationship-proposal")


def preparing(
    message: str = "Analytics are being prepared.", *, retry_after: int = 2
) -> HTTPException:
    return HTTPException(
        503,
        {"code": "analytics_preparing", "message": message},
        headers={"Retry-After": str(retry_after)},
    )


def timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


async def current_snapshot(
    client: AnalyticsClient,
    policy: str,
    now: datetime,
    *,
    scope: Literal["cards", "queues"],
    generation: str,
    card_kinds: Sequence[str] = CARD_KINDS,
) -> AnalyticsSnapshot:
    coverage = (await read_reconciliation_status(client, policy, card_kinds=card_kinds))[scope]
    completed_at = coverage["completed_at"]
    source_at = coverage["source_at"]
    if completed_at is None or source_at is None:
        return AnalyticsSnapshot(status="catching_up", generation=generation, policy_version=policy)
    covered_at = timestamp(source_at)
    expires_at = covered_at + timedelta(seconds=coverage["max_age_seconds"])
    state = "stale" if now >= expires_at else "catching_up" if coverage["pending"] else "fresh"
    return AnalyticsSnapshot(
        status=state,
        generated_at=covered_at,
        coverage_expires_at=expires_at,
        generation=generation,
        policy_version=policy,
        lag_seconds=max(0, (now - covered_at).total_seconds()),
    )


def _next_cursor(offset: int, count: int, total: int) -> int | None:
    end = offset + count
    return end if end < total else None


@dataclass(frozen=True)
class CurrentSelection:
    policy: str
    kinds: tuple[str, ...]
    snapshot: AnalyticsSnapshot

    def parameters(self) -> dict[str, object]:
        return {"policy": self.policy, "kinds": self.kinds}

    @staticmethod
    def source() -> str:
        # FINAL must choose the newest identity before applying mutable filters;
        # otherwise an old stage or a deleted card can reappear.
        return """(SELECT * REPLACE(if(state='retry_wait'
            AND next_transition_at<={as_of:DateTime64(6,'UTC')},'waiting',state) AS state)
          FROM analytics_card_canonical FINAL
          WHERE policy={policy:String} AND kind IN {kinds:Array(String)}) c
          WHERE c.deleted=0"""


class CurrentCards:
    def __init__(self, client: AnalyticsClient, *, now: datetime | None = None) -> None:
        self.client = client
        self.now = now or datetime.now(UTC)
        self.policy = pipeline_policy_version(getattr(client, "settings", None))

    async def selection(self, kinds: Iterable[str]) -> CurrentSelection:
        kinds = tuple(sorted(set(kinds)))
        if not kinds or set(kinds) - set(CARD_KINDS):
            raise ValueError("Unknown current analytical kind")
        status = await self.client.status()
        if not status.get("ready") or status.get("policy", self.policy) != self.policy:
            raise preparing()
        snapshot = await current_snapshot(
            self.client,
            self.policy,
            self.now,
            scope="cards",
            generation=str(status["version"]),
            card_kinds=kinds,
        )
        return CurrentSelection(self.policy, kinds, snapshot)

    async def _query(
        self, selection: CurrentSelection, query: str, **parameters: object
    ) -> list[SqlRecord]:
        return await self.client.query(
            query + " SETTINGS optimize_move_to_prewhere_if_final=0,"
            "enable_optimize_predicate_expression=0",
            selection.parameters() | {"as_of": self.now} | parameters,
        )

    async def _stage_page(
        self,
        selection: CurrentSelection,
        base: str,
        board: str,
        order: str,
        params: Mapping[str, object],
    ) -> list[SqlRecord]:
        # Count and page in one statement. Rank only narrow metadata, then read
        # JSON for the selected identities so a large board does not sort every
        # payload. A left join preserves totals for an out-of-range lane offset.
        return await self._query(
            selection,
            f"""/* admin:{board}-page */ WITH selected AS (
                SELECT * FROM (
                  SELECT c.kind,c.shard,c.entity_id,c.stage,c.state,
                    row_number() OVER(PARTITION BY c.stage ORDER BY {order}) AS position
                  FROM {base} AND c.stage IN {{stages:Array(String)}}
                ) WHERE position>{{offset:UInt64}}
                    AND position<={{offset:UInt64}}+{{limit:UInt64}}
              ), payloads AS (
                SELECT kind,shard,entity_id,payload FROM analytics_card_canonical FINAL
                WHERE policy={{policy:String}} AND tuple(kind,shard,entity_id) IN (
                  SELECT kind,shard,entity_id FROM selected
                )
              ), pages AS (
                SELECT s.stage,groupArray(tuple(s.position,s.kind,toString(s.entity_id),
                  p.payload,s.state)) AS items FROM selected s INNER JOIN payloads p
                  ON s.kind=p.kind AND s.shard=p.shard AND s.entity_id=p.entity_id
                GROUP BY s.stage
              ), totals AS (
                SELECT c.stage,count() AS total FROM {base}
                  AND c.stage IN {{stages:Array(String)}} GROUP BY c.stage
              ) SELECT t.stage,t.total,p.items FROM totals t
                LEFT JOIN pages p ON t.stage=p.stage""",
            **params,
        )

    @staticmethod
    def _stage_items(
        rows: Sequence[SqlRecord], card_type: type[Card]
    ) -> defaultdict[str, list[Card]]:
        result: defaultdict[str, list[Card]] = defaultdict(list)
        for row in rows:
            for _, _, _, payload, state in sorted(row["items"], key=lambda item: item[0]):
                value = json.loads(payload)
                value["state"] = state
                result[row["stage"]].append(card_type.model_validate(value))
        return result

    async def article_board(
        self,
        *,
        q: str | None,
        source_id: uuid.UUID | None,
        needs_attention: bool,
        limit: int,
        stage: Stage | None = None,
        cursor: int = 0,
        group_by: Literal["source"] | None = None,
        source_group: uuid.UUID | Literal["unknown"] | None = None,
        normalized_q: str | None = None,
    ) -> PipelineBoard:
        from devfeed_core.analytics_card_types import (
            STAGES,
            PipelineBoard,
            PipelineCard,
            PipelineColumn,
            PipelineSourceGroup,
        )

        selected = await self.selection(("article",))
        since = self.now - timedelta(days=7)
        params: ArticleQueryParameters = {
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
        base = selected.source() + condition
        stages = (stage,) if stage else STAGES
        if group_by == "source":
            rows = await self._query(
                selected,
                """/* admin:article-source-groups */ SELECT stage,sum(total) AS stage_total,
                    max(group_count) AS group_count,
                    groupArrayIf(tuple(position,source_id,source_name,total,state_counts),
                      position>{offset:UInt64} AND position<={offset:UInt64}+{limit:UInt64})
                        AS source_groups FROM (
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
                  ) GROUP BY stage""",
                **params,
            )
            columns = []
            for key in stages:
                row: SqlRecord = next((row for row in rows if row["stage"] == key), {})
                group_count = int(row.get("group_count", 0))
                groups = [
                    PipelineSourceGroup(
                        source_id=item[1], source_name=item[2], total=item[3], state_counts=item[4]
                    )
                    for item in sorted(row.get("source_groups", []), key=lambda item: item[0])
                ]
                offset = params["offset"]
                columns.append(
                    PipelineColumn(
                        stage=key,
                        total=int(row.get("stage_total", 0)),
                        items=[],
                        source_groups=groups,
                        next_cursor=_next_cursor(offset, limit, group_count),
                    )
                )
        else:
            rows = await self._stage_page(
                selected,
                base,
                "article",
                """if(c.stage IN ('published','rejected'),0,if(c.state='running',0,1)),
                    if(c.stage IN ('published','rejected'),c.terminal_at,NULL) DESC,
                    if(c.stage IN ('published','rejected'),NULL,c.created_at) ASC,
                    if(c.stage IN ('published','rejected'),toString(c.entity_id),'') DESC,
                    if(c.stage IN ('published','rejected'),'',toString(c.entity_id)) ASC""",
                params,
            )
            totals = {row["stage"]: int(row["total"]) for row in rows}
            items = self._stage_items(rows, PipelineCard)
            columns = [
                PipelineColumn(
                    stage=key,
                    total=totals.get(key, 0),
                    items=items[key],
                    next_cursor=_next_cursor(params["offset"], limit, totals.get(key, 0)),
                )
                for key in stages
            ]
        return PipelineBoard(
            as_of=self.now,
            terminal_since=since,
            counts={column.stage: column.total for column in columns},
            columns=columns,
            snapshot=selected.snapshot,
        )

    async def source_board(
        self,
        query: SourceBoardQuery,
        *,
        stage: SourcePipelineStage | None = None,
        cursor: int = 0,
        matching_ids: Mapping[str, Sequence[uuid.UUID]] | None = None,
    ) -> SourcePipelineBoard:
        from devfeed_core.analytics_card_types import (
            SOURCE_STAGES,
            SOURCE_TERMINAL_STAGES,
            SourcePipelineBoard,
            SourcePipelineCard,
            SourcePipelineColumn,
        )

        selected = await self.selection(("source", "candidate"))
        params: SourceQueryParameters = {
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
            params["sources"] = list(map(str, matching_ids["source"]))
            params["candidates"] = list(map(str, matching_ids["candidate"]))
        if query.source_type:
            condition += " AND c.source_type={source_type:String}"
            params["source_type"] = query.source_type
        if query.needs_attention:
            condition += " AND (c.state IN ('blocked','retry_wait') OR c.stage='review')"
        base = selected.source() + condition
        rows = await self._stage_page(
            selected,
            base,
            "source",
            """if(c.state='running',0,1),
                if(c.stage IN {terminal_stages:Array(String)},
                  -toUnixTimestamp64Micro(c.activity_at),toUnixTimestamp64Micro(c.created_at)),
                c.kind,toString(c.entity_id)""",
            params,
        )
        totals = {row["stage"]: int(row["total"]) for row in rows}
        items = self._stage_items(rows, SourcePipelineCard)
        columns = [
            SourcePipelineColumn(
                stage=key,
                total=totals.get(key, 0),
                items=items[key],
                next_cursor=_next_cursor(cursor, len(items[key]), totals.get(key, 0)),
            )
            for key in params["stages"]
        ]
        return SourcePipelineBoard(
            as_of=self.now,
            terminal_since=query.terminal_since,
            counts={column.stage: column.total for column in columns},
            columns=columns,
            snapshot=selected.snapshot,
        )

    async def operations(self, window: ReportWindow) -> OverviewSectionReport:
        from devfeed_core.analytics_blockers import RecoveryTarget, blocker_catalog
        from devfeed_core.config import get_settings

        from devfeed_admin_api.overview_sections import (
            OperationsSection,
            OverviewSectionReport,
            ReviewQueue,
        )

        start, end = window.bounds
        selected = await self.selection(CARD_KINDS)
        observed = await self._query(
            selected,
            """/* admin:operations */ WITH cards AS (SELECT c.kind,c.created_at,
                c.created_at>={start:DateTime64(6,'UTC')}
                  AND c.created_at<{end:DateTime64(6,'UTC')} AS created_in_period,
                c.activity_at>={start:DateTime64(6,'UTC')}
                  AND c.activity_at<{end:DateTime64(6,'UTC')} AS active_in_period,
                c.review_pending,c.enabled,c.approval_status,c.consecutive_failures,
                c.blocker_codes,c.entity_id,c.title,c.editorial_revision FROM """
            + selected.source()
            + """ AND ((c.created_at>={start:DateTime64(6,'UTC')}
                  AND c.created_at<{end:DateTime64(6,'UTC')})
                OR (c.kind='source' AND c.activity_at>={start:DateTime64(6,'UTC')}
                  AND c.activity_at<{end:DateTime64(6,'UTC')}))), reviews AS (
                SELECT kind,countIf(review_pending=1 AND created_in_period) AS pending,
                  minIf(toNullable(created_at),review_pending=1 AND created_in_period) AS oldest,
                  countIf(kind='source' AND enabled=1 AND approval_status='approved'
                    AND consecutive_failures>0 AND active_in_period) AS failures
                FROM cards GROUP BY kind
              ), blockers AS (
                SELECT code,count() AS total,
                  groupArraySorted(5)(tuple(created_at,toString(entity_id),kind,
                    title,editorial_revision)) AS targets
                FROM (SELECT *,arrayJoin(blocker_codes) AS code FROM cards WHERE created_in_period)
                GROUP BY code
              ) SELECT (SELECT groupArray(tuple(kind,pending,oldest,failures)) FROM reviews)
                  AS reviews,
                (SELECT groupArray(tuple(code,total,targets)) FROM blockers) AS blockers""",
            start=start,
            end=end,
        )
        row = observed[0] if observed else {"reviews": [], "blockers": []}
        reviews = {
            item[0]: {"pending": item[1], "oldest": item[2], "failures": item[3]}
            for item in row["reviews"]
        }
        settings = getattr(self.client, "settings", None) or get_settings()
        blockers = blocker_catalog(full_automation=settings.full_automation)
        by_code = {item.code: item for item in blockers}
        groups = [
            {"code": item[0], "total": item[1], "targets": item[2]} for item in row["blockers"]
        ]
        for row in groups:
            if row["code"] not in by_code:
                raise AnalyticsPreparing("Current blocker policy is being prepared")
            item = by_code[row["code"]]
            item.count = int(row["total"])
            item.targets = [
                RecoveryTarget(
                    id=uuid.UUID(str(key[1])),
                    title=key[3],
                    kind=key[2],
                    revision=int(key[4]),
                )
                for key in row["targets"]
            ]
        mapping = {
            "article": "articles",
            "source": "sources",
            "topic-proposal": "topics",
            "relationship-proposal": "relationships",
        }
        return OverviewSectionReport(
            generated_at=self.now,
            days=window.days,
            start=window.start,
            end=window.inclusive_end,
            snapshot=selected.snapshot,
            data=OperationsSection(
                full_automation=settings.full_automation,
                reviews=[
                    ReviewQueue(
                        kind=name,
                        count=int(reviews.get(kind, {}).get("pending", 0)),
                        oldest_at=reviews.get(kind, {}).get("oldest"),
                    )
                    for kind, name in mapping.items()
                ],
                source_failures=sum(int(row["failures"]) for row in reviews.values()),
                blockers=blockers,
            ),
        )
