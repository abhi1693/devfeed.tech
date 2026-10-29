"""Bounded scalar source identities and row-version fingerprints for card repair.

Fingerprints never read article bodies, source credentials, prompts, or job JSON.
An incomplete dependency sample has no fingerprint and must be rebuilt instead
of being accepted as unchanged. Callers own the read-only snapshot transaction.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from psycopg import AsyncConnection, sql

from devfeed_core.analytics import SqlRecord

MAX_IDENTITIES = 200
MAX_DEPENDENCIES = 200
TABLES = {
    "article": "articles",
    "source": "sources",
    "candidate": "source_candidates",
    "topic-proposal": "topic_proposals",
    "relationship-proposal": "topic_relation_proposals",
}
CURRENT_FILTERS = {
    "article": "((publication_status<>'published' AND review_status<>'rejected') "
    "OR (publication_status='published' "
    "AND (published_to_feed_at IS NULL OR published_to_feed_at>=%s)) "
    "OR (review_status='rejected' AND (EXISTS (SELECT 1 FROM article_reviews r "
    "WHERE r.article_id=articles.id AND r.action='reject' AND r.created_at>=%s) "
    "OR (discovered_at>=%s AND NOT EXISTS (SELECT 1 FROM article_reviews r "
    "WHERE r.article_id=articles.id AND r.action='reject')))))",
    "topic-proposal": "(status='pending' OR reviewed_at>=%s)",
    "relationship-proposal": "(status='pending' OR reviewed_at>=%s)",
}

# Trusted SQL fragments only. Each dependency returns at most 201 narrow
# metadata rows per owner, including the first possible overflow row.
DEPENDENCIES = {
    "article": (
        (
            "origins",
            "article_origins c LEFT JOIN sources p ON p.id=c.source_id",
            "c.article_id=b.id",
            "c.id::text||':'||c.xmin::text||':'||coalesce(p.xmin::text,'')",
        ),
        (
            "topics",
            "article_topics c LEFT JOIN topics p ON p.id=c.topic_id",
            "c.article_id=b.id",
            "c.topic_id::text||':'||c.xmin::text||':'||coalesce(p.xmin::text,'')",
        ),
        (
            "tags",
            "article_tags c LEFT JOIN tags p ON p.id=c.tag_id",
            "c.article_id=b.id",
            "c.tag_id::text||':'||c.xmin::text||':'||coalesce(p.xmin::text,'')",
        ),
        ("content", "article_contents c", "c.article_id=b.id", "c.xmin::text"),
        ("reviews", "article_reviews c", "c.article_id=b.id", "c.id::text||':'||c.xmin::text"),
        (
            "enrichment",
            "article_enrichment_jobs c",
            "c.article_id=b.id",
            "c.id::text||':'||c.xmin::text",
        ),
        (
            "analysis",
            "article_analysis_jobs c",
            "c.article_id=b.id",
            "c.id::text||':'||c.xmin::text",
        ),
        ("images", "article_image_jobs c", "c.article_id=b.id", "c.id::text||':'||c.xmin::text"),
    ),
    "source": (
        ("candidates", "source_candidates c", "c.source_id=b.id", "c.id::text||':'||c.xmin::text"),
        (
            "discovery",
            "source_discovery_jobs c JOIN source_candidates p ON p.id=c.candidate_id",
            "p.source_id=b.id",
            "c.id::text||':'||c.xmin::text",
        ),
        (
            "enrichment",
            "source_enrichment_jobs c",
            "c.source_id=b.id",
            "c.id::text||':'||c.xmin::text",
        ),
        (
            "branding",
            "article_image_jobs c",
            "c.source_id=b.id AND c.operation IN ('source-logo','source-logo-refresh')",
            "c.id::text||':'||c.xmin::text",
        ),
        ("ingestion", "ingestion_jobs c", "c.source_id=b.id", "c.id::text||':'||c.xmin::text"),
    ),
    "candidate": (
        (
            "discovery",
            "source_discovery_jobs c",
            "c.candidate_id=b.id",
            "c.id::text||':'||c.xmin::text",
        ),
    ),
    "topic-proposal": (
        (
            "research",
            "topic_analysis_jobs c",
            "c.proposal_id=b.id",
            "c.id::text||':'||c.xmin::text",
        ),
        (
            "decisions",
            "topic_decision_runs c",
            "c.proposal_id=b.id",
            "c.xmin::text",
        ),
        (
            "verification",
            "research_verification_jobs c JOIN topic_analysis_jobs p ON p.id=c.id",
            "p.proposal_id=b.id",
            "c.id::text||':'||c.xmin::text",
        ),
    ),
    "relationship-proposal": (
        ("research", "topic_analysis_jobs c", "c.id=b.job_id", "c.id::text||':'||c.xmin::text"),
        (
            "verification",
            "research_verification_jobs c",
            "c.id=b.job_id",
            "c.id::text||':'||c.xmin::text",
        ),
    ),
}


def _table(kind: str) -> str:
    if kind not in TABLES:
        raise ValueError("Unknown analytical card kind")
    return TABLES[kind]


async def card_ids(
    connection: AsyncConnection[SqlRecord],
    kind: str,
    cursor: str | None,
    limit: int,
    *,
    current_only: bool,
    now: datetime,
) -> list[str]:
    table = _table(kind)
    if type(limit) is not int or not 1 <= limit <= MAX_IDENTITIES:
        raise ValueError("Card identity pages must contain at most 200 identities")
    if type(current_only) is not bool or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Card scans require an explicit mode and aware source time")
    conditions: list[str] = []
    parameters: list[object] = []
    if cursor is not None:
        conditions.append("id>%s::uuid")
        parameters.append(str(uuid.UUID(cursor)))
    if current_only and kind in CURRENT_FILTERS:
        conditions.append(CURRENT_FILTERS[kind])
        parameters.extend([now - timedelta(days=7)] * CURRENT_FILTERS[kind].count("%s"))
    parameters.append(limit)
    query = sql.SQL(
        "/* analytics:reconcile-card-ids */ SELECT id::text AS id FROM {} "
        "WHERE {} ORDER BY id LIMIT %s"
    ).format(sql.Identifier(table), sql.SQL(" AND ".join(conditions) or "true"))
    rows = await (await connection.execute(query, parameters)).fetchall()
    return [str(uuid.UUID(row["id"])) for row in rows]


async def _dependency_versions(
    connection: AsyncConnection[SqlRecord],
    table: str,
    identifiers: Sequence[str],
    dependency: tuple[str, str, str, str],
) -> list[SqlRecord]:
    name, source, condition, token = dependency
    query = sql.SQL(
        "/* analytics:reconcile-card-dependency:{} */ "
        "SELECT b.id::text AS id,coalesce(array_agg(d.token ORDER BY d.token) "
        "FILTER (WHERE d.token IS NOT NULL),ARRAY[]::text[]) AS versions,"
        "count(d.token)>{} AS incomplete FROM {} b LEFT JOIN LATERAL "
        "(SELECT {} AS token FROM {} WHERE {} LIMIT {}) d ON true "
        "WHERE b.id=ANY(%s::uuid[]) GROUP BY b.id"
    ).format(
        sql.SQL(name),
        sql.Literal(MAX_DEPENDENCIES),
        sql.Identifier(table),
        sql.SQL(token),
        sql.SQL(source),
        sql.SQL(condition),
        sql.Literal(MAX_DEPENDENCIES + 1),
    )
    return await (await connection.execute(query, (list(identifiers),))).fetchall()


async def _base_versions(
    connection: AsyncConnection[SqlRecord], table: str, identifiers: Sequence[str]
) -> dict[str, list[object]]:
    query = sql.SQL(
        "/* analytics:reconcile-card-base */ SELECT id::text AS id,xmin::text AS version "
        "FROM {} WHERE id=ANY(%s::uuid[]) ORDER BY id"
    ).format(sql.Identifier(table))
    rows = await (await connection.execute(query, (identifiers,))).fetchall()
    versions: dict[str, list[object]] = {}
    for row in rows:
        identifier = row["id"]
        if identifier not in identifiers or identifier in versions:
            raise ValueError("Ambiguous analytical card source identities")
        versions[identifier] = ["card-source-v1", row["version"]]
    return versions


async def _catalog_versions(
    connection: AsyncConnection[SqlRecord], versions: dict[str, list[object]]
) -> bool:
    catalogs = await (
        await connection.execute(
            "SELECT name,revision::text AS revision FROM catalog_revisions "
            "WHERE name IN ('topics','tags') ORDER BY name"
        )
    ).fetchall()
    if [row["name"] for row in catalogs] != ["tags", "topics"]:
        return False
    for value in versions.values():
        value.append([(row["name"], row["revision"]) for row in catalogs])
    return True


async def _add_dependencies(
    connection: AsyncConnection[SqlRecord], kind: str, versions: dict[str, list[object]]
) -> set[str]:
    incomplete: set[str] = set()
    for dependency in DEPENDENCIES[kind]:
        seen: set[str] = set()
        rows = await _dependency_versions(connection, TABLES[kind], list(versions), dependency)
        for row in rows:
            identifier = row["id"]
            if identifier not in versions or identifier in seen:
                raise ValueError("Ambiguous analytical card dependency identities")
            seen.add(identifier)
            tokens = row["versions"]
            if row["incomplete"] or len(tokens) > MAX_DEPENDENCIES:
                incomplete.add(identifier)
            versions[identifier].append([dependency[0], sorted(tokens)])
        incomplete.update(set(versions) - seen)
    return incomplete


async def card_fingerprints(
    connection: AsyncConnection[SqlRecord], kind: str, identifiers: Sequence[str]
) -> dict[str, str]:
    table = _table(kind)
    if isinstance(identifiers, (str, bytes)) or len(identifiers) > MAX_IDENTITIES:
        raise ValueError("Card fingerprint batches must contain at most 200 identities")
    identifiers = list(dict.fromkeys(str(uuid.UUID(value)) for value in identifiers))
    result = dict.fromkeys(identifiers, "")
    if not identifiers:
        return result
    versions = await _base_versions(connection, table, identifiers)
    if not versions or (kind == "article" and not await _catalog_versions(connection, versions)):
        return result
    incomplete = await _add_dependencies(connection, kind, versions)
    for identifier, value in versions.items():
        if identifier not in incomplete:
            result[identifier] = hashlib.sha256(
                json.dumps(value, separators=(",", ":")).encode()
            ).hexdigest()
    return result
