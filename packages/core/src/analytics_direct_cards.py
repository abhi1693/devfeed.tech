"""Direct, per-identity ClickHouse operator card observations."""

import asyncio
import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from devfeed_core.analytics_card_builders import article_rows, proposal_rows, source_rows
from devfeed_core.analytics_observation import pipeline_policy_version


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


CARD_SOURCES = {
    "articles": "article",
    "sources": "source",
    "source_candidates": "candidate",
    "topic_proposals": "topic-proposal",
    "topic_relation_proposals": "relationship-proposal",
}
ROUTES = {
    "source_candidates": (("source_id", "source"),),
    "article_origins": (("article_id", "article"),),
    "article_topics": (("article_id", "article"),),
    "article_reviews": (("article_id", "article"),),
    "article_contents": (("article_id", "article"),),
    "article_enrichment_jobs": (("article_id", "article"),),
    "article_analysis_jobs": (("article_id", "article"),),
    "article_image_jobs": (("article_id", "article"), ("source_id", "source")),
    "ingestion_jobs": (("source_id", "source"),),
    "source_reviews": (("source_id", "source"),),
    "source_enrichment_jobs": (("source_id", "source"),),
    "source_discovery_jobs": (("candidate_id", "candidate"),),
    "topic_analysis_jobs": (("proposal_id", "topic-proposal"),),
    "topic_decision_runs": (("proposal_id", "topic-proposal"),),
}


def card_identities(changes):
    result = defaultdict(set)
    for change in changes:
        source = change["source_table"]
        for row in (change.get("record"), change.get("before_record"), change.get("identity")):
            if not row:
                continue
            if source in CARD_SOURCES and row.get("id"):
                result[CARD_SOURCES[source]].add(str(row["id"]))
            for field, kind in ROUTES.get(source, ()):
                if row.get(field):
                    result[kind].add(str(row[field]))
    return result


def build_cards(settings, kind, identifiers, revision):
    from devfeed_core.db import get_engine

    factory = sessionmaker(get_engine(), class_=Session, expire_on_commit=False)
    policy = pipeline_policy_version(settings)
    now = datetime.now(UTC)
    ids = [uuid.UUID(str(value)) for value in identifiers]
    if kind == "article":
        rows = article_rows(factory, ids, policy, now)
    elif kind == "source":
        rows = source_rows(factory, ids, [], policy, now)
    elif kind == "candidate":
        rows = source_rows(factory, [], ids, policy, now)
    elif kind == "topic-proposal":
        rows = proposal_rows(factory, ids, [], policy, now)
    elif kind == "relationship-proposal":
        rows = proposal_rows(factory, [], ids, policy, now)
    else:
        raise ValueError("Unknown operator card kind")
    present = {str(row["entity_id"]): row for row in rows}
    return [
        normalize_card(present.get(str(identity)), policy, kind, identity, revision, now)
        for identity in ids
    ]


async def publish_card_ids(client, settings, kind, identifiers, revision):
    identifiers = sorted(set(map(str, identifiers)))
    for start in range(0, len(identifiers), 200):
        rows = await asyncio.to_thread(
            build_cards, settings, kind, identifiers[start : start + 200], revision
        )
        await client.insert("analytics_card_canonical", rows)


async def publish_cards(client, settings, changes, revision):
    for kind, identifiers in card_identities(changes).items():
        await publish_card_ids(client, settings, kind, identifiers, revision)


async def dependent_card_batches(connection, changes):
    """Stream source identities affected by catalog and research edits in memory."""
    catalog_changed = any(
        change["source_table"] in {"topics", "catalog_revisions"} for change in changes
    )
    research = sorted(
        {
            str(record["id"])
            for change in changes
            if change["source_table"] in {"topic_analysis_jobs", "research_verification_jobs"}
            for record in (
                change.get("record"),
                change.get("before_record"),
                change.get("identity"),
            )
            if record and record.get("id")
        }
    )
    if research:
        cursor = uuid.UUID(int=0)
        while True:
            rows = await (
                await connection.execute(
                    "SELECT id FROM topic_relation_proposals WHERE job_id=ANY(%s::uuid[]) "
                    "AND id>%s ORDER BY id LIMIT 200",
                    (research, cursor),
                )
            ).fetchall()
            if not rows:
                break
            yield [
                dict(source_table="topic_relation_proposals", identity={"id": row["id"]})
                for row in rows
            ]
            cursor = rows[-1]["id"]
    if catalog_changed:
        cursor = uuid.UUID(int=0)
        while True:
            rows = await (
                await connection.execute(
                    "SELECT id FROM articles WHERE id>%s AND publication_status<>'published' "
                    "AND review_status<>'rejected' ORDER BY id LIMIT 200",
                    (cursor,),
                )
            ).fetchall()
            if not rows:
                break
            yield [dict(source_table="articles", identity={"id": row["id"]}) for row in rows]
            cursor = rows[-1]["id"]
