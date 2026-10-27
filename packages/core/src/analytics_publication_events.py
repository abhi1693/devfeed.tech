"""Direct, retry-safe publication and attribution observations in ClickHouse.

Each committed mutation receives one stable UUID. Reusing that UUID for a
retry deduplicates the same deltas; a different mutation must use a new UUID.
The caller emits changes only after its source transaction commits and must
supply both old and new associations for moves. The historical import must
finish before live events are exposed to reports.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

MAX_CHANGES = 500
_ZERO = str(uuid.UUID(int=0))
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_TOPIC_NAMESPACE = uuid.UUID("f578f44d-3ce0-4b5e-a903-879be66906ad")

DDL = """CREATE TABLE IF NOT EXISTS analytics_publication_events (
    event_id UUID, kind LowCardinality(String), article_id UUID,
    relation_id UUID, target_id UUID, delta Int8,
    published_at DateTime64(6, 'UTC'), discovered_at Nullable(DateTime64(6, 'UTC')),
    automated UInt8
) ENGINE=ReplacingMergeTree ORDER BY event_id"""

_BUCKETS = {
    "hour": "toStartOfHour(published_at)",
    "day": "toStartOfDay(published_at)",
    "week": "toStartOfInterval(published_at, INTERVAL 1 WEEK, 'UTC')",
    "month": "toDateTime(toStartOfMonth(published_at), 'UTC')",
}


def _uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("Invalid publication identity") from error


def _time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Invalid publication timestamp") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Publication timestamps require a timezone")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Publication:
    """The historical publication fact; current unpublished status is irrelevant."""

    article_id: str
    published_at: datetime
    discovered_at: datetime | None = None
    automated: bool = False

    def __post_init__(self):
        object.__setattr__(self, "article_id", _uuid(self.article_id))
        object.__setattr__(self, "published_at", _time(self.published_at))
        if self.discovered_at is not None:
            object.__setattr__(self, "discovered_at", _time(self.discovered_at))
        if type(self.automated) is not bool:
            raise ValueError("Invalid publication automation flag")


@dataclass(frozen=True, slots=True)
class Attribution:
    """A single source-origin or eligible article-topic association."""

    kind: str
    article_id: str
    relation_id: str
    target_id: str

    def __post_init__(self):
        if self.kind not in {"source", "topic"}:
            raise ValueError("Invalid publication attribution kind")
        for name in ("article_id", "relation_id", "target_id"):
            object.__setattr__(self, name, _uuid(getattr(self, name)))

    @classmethod
    def source(cls, article_id, origin_id, source_id):
        """Use the actual origin ID so multiple origins retain multiplicity."""
        return cls("source", article_id, origin_id, source_id)

    @classmethod
    def topic(cls, article_id, topic_id, role):
        """Use a stable composite identity and ignore non-attributed roles."""
        if role not in {"primary", "supporting", "comparison", "incidental"}:
            raise ValueError("Invalid publication topic role")
        article, topic = _uuid(article_id), _uuid(topic_id)
        if role not in {"primary", "supporting"}:
            return None
        relation = uuid.uuid5(_TOPIC_NAMESPACE, f"article-topic:{article}:{topic}")
        return cls("topic", article, str(relation), topic)


class PublicationEvents:
    """Insert signed immutable changes and aggregate current historical attribution."""

    def __init__(self, client):
        self.client = client

    async def initialize(self):
        await self.client.execute(DDL)

    @staticmethod
    def _validate_change(mutation_id, before, after, old_links, new_links):
        try:
            mutation = uuid.UUID(str(mutation_id))
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError("Invalid publication mutation identity") from error
        if before is not None and not isinstance(before, Publication):
            raise ValueError("Invalid prior publication")
        if after is not None and not isinstance(after, Publication):
            raise ValueError("Invalid current publication")
        if not isinstance(old_links, (tuple, list)) or not isinstance(new_links, (tuple, list)):
            raise ValueError("Invalid publication attribution batch")
        if len(old_links) + len(new_links) > MAX_CHANGES or any(
            not isinstance(item, Attribution) for item in (*old_links, *new_links)
        ):
            raise ValueError("Invalid publication attribution batch")
        return mutation, set(old_links), set(new_links)

    def _change_rows(self, mutation, before, after, old, new):
        rows = []
        if before != after:
            if before is not None:
                rows.append(self._publication(mutation, before, -1))
            if after is not None:
                rows.append(self._publication(mutation, after, 1))
        for link in sorted(old - new, key=self._sort_link):
            rows.append(self._attribution(mutation, link, -1))
        for link in sorted(new - old, key=self._sort_link):
            rows.append(self._attribution(mutation, link, 1))
        if len(rows) > MAX_CHANGES:
            raise ValueError("Publication mutation exceeds the batch limit")
        return rows

    async def change(self, mutation_id, *, before=None, after=None, old_links=(), new_links=()):
        """Apply one committed mutation; unchanged snapshots emit no events.

        For an unpublish, pass the same publication fact before and after.
        For a deletion, pass the old fact and no new fact. A moved link appears
        in ``old_links`` under the old owner and in ``new_links`` under the new.
        """
        mutation, old, new = self._validate_change(mutation_id, before, after, old_links, new_links)
        rows = self._change_rows(mutation, before, after, old, new)
        if rows:
            await self.client.insert("analytics_publication_events", rows)
        return len(rows)

    @staticmethod
    def _sort_link(link):
        return link.kind, link.article_id, link.relation_id, link.target_id

    @staticmethod
    def _publication(mutation, snapshot, delta):
        key = ":".join(
            (
                "publication",
                snapshot.article_id,
                str(delta),
                snapshot.published_at.isoformat(),
                snapshot.discovered_at.isoformat() if snapshot.discovered_at else "",
                str(int(snapshot.automated)),
            )
        )
        return {
            "event_id": str(uuid.uuid5(mutation, key)),
            "kind": "publication",
            "article_id": snapshot.article_id,
            "relation_id": _ZERO,
            "target_id": _ZERO,
            "delta": delta,
            "published_at": snapshot.published_at,
            "discovered_at": snapshot.discovered_at,
            "automated": int(snapshot.automated),
        }

    @staticmethod
    def _attribution(mutation, link, delta):
        key = ":".join((link.kind, link.article_id, link.relation_id, link.target_id, str(delta)))
        return {
            "event_id": str(uuid.uuid5(mutation, key)),
            "kind": link.kind,
            "article_id": link.article_id,
            "relation_id": link.relation_id,
            "target_id": link.target_id,
            "delta": delta,
            "published_at": _EPOCH,
            "discovered_at": None,
            "automated": 0,
        }

    async def overview(self, start, end, *, granularity="day"):
        """Return publication count and latency directly from current signed facts."""
        bucket = _BUCKETS.get(granularity)
        if bucket is None:
            raise ValueError("Invalid publication granularity")
        return await self.client.query(
            f"SELECT {bucket} AS chart_bucket,grouping(chart_bucket) AS total,"
            "count() AS published,sum(automated) AS automated,"
            "quantilesTDigestIf(0.5,0.95)(dateDiff('millisecond',discovered_at,published_at)/1000.,"
            "discovered_at IS NOT NULL AND published_at>=discovered_at) AS percentiles "
            "FROM (SELECT article_id,published_at,discovered_at,automated,sum(delta) AS net "
            "FROM analytics_publication_events FINAL WHERE kind='publication' "
            "GROUP BY article_id,published_at,discovered_at,automated HAVING net>0) "
            "WHERE published_at>={start:DateTime64(6,'UTC')} "
            "AND published_at<{end:DateTime64(6,'UTC')} "
            "GROUP BY chart_bucket WITH ROLLUP "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end)},
        )

    async def attribution(self, start, end, *, kind):
        """Count a publication once per source or topic, including duplicate origins."""
        if kind not in {"source", "topic"}:
            raise ValueError("Invalid publication attribution kind")
        return await self.client.query(
            "SELECT toString(a.target_id) AS id,count() AS publications FROM "
            "(SELECT article_id,published_at FROM analytics_publication_events FINAL "
            "WHERE kind='publication' GROUP BY article_id,published_at,discovered_at,automated "
            "HAVING sum(delta)>0) AS p INNER JOIN "
            "(SELECT article_id,target_id FROM (SELECT article_id,target_id,relation_id,"
            "sum(delta) AS net FROM analytics_publication_events FINAL "
            "WHERE kind={kind:String} GROUP BY article_id,target_id,relation_id HAVING net>0) "
            "GROUP BY article_id,target_id) AS a ON p.article_id=a.article_id "
            "WHERE p.published_at>={start:DateTime64(6,'UTC')} "
            "AND p.published_at<{end:DateTime64(6,'UTC')} "
            "GROUP BY a.target_id ORDER BY a.target_id "
            "SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0",
            {"start": _time(start), "end": _time(end), "kind": kind},
        )
