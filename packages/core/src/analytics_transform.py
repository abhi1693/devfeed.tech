"""Normalize captured changes into typed domain facts and an exact decision ledger."""

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from devfeed_core.analytics_facts import ZERO_UUID, empty_fact
from devfeed_core.analytics_schema import JOB_KINDS, JOB_TABLES, TABLES

FACT_SOURCES = (
    "inference_calls",
    "topic_decision_calls",
    "topic_decision_runs",
    "topic_relation_proposals",
    "articles",
    "article_opens",
    "user_accounts",
    "topic_proposals",
    *JOB_TABLES,
)
NAMESPACE = uuid.UUID("f578f44d-3ce0-4b5e-a903-879be66906ad")


def moment(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def number(value, *, digits=12):
    # Match the telemetry contract: booleans, signed values, floating-point and
    # whitespace are unknown; cached/reasoning tokens are subsets, not additions.
    raw = str(value)
    return int(raw) if re.fullmatch(rf"[0-9]{{1,{digits}}}", raw) else 0


def _identity(*parts):
    return uuid.uuid5(NAMESPACE, ":".join(str(part) for part in parts))


def _fact(source, record, event, generation, *, deleted=False, side=1, context=None):
    if not record:
        return None
    identifier = record.get("id")
    at = None
    domain = ""
    values: dict[str, Any] = {"entity_id": str(identifier or record.get("article_id") or ZERO_UUID)}
    if source == "inference_calls":
        domain, at = "inference", moment(record.get("started_at"))
        tokens = record.get("tokens") or {}
        if isinstance(tokens, str):
            import json

            tokens = json.loads(tokens)
        input_tokens = number(tokens.get("inputTokens"))
        output_tokens = number(tokens.get("outputTokens"))
        values = dict(
            task=record.get("operation") or "unknown",
            model=record.get("model") or "Unknown model",
            effort=record.get("reasoning_effort") or "Inherited / unknown",
            status=record.get("status") or "unknown",
            calls=1,
            failed=int(record.get("status") == "failed"),
            unreported=int(input_tokens + output_tokens == 0),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=min(input_tokens, number(tokens.get("cachedInputTokens"))),
            reasoning_tokens=min(output_tokens, number(tokens.get("reasoningOutputTokens"))),
            web_searches=max(int(record.get("web_searches") or 0), 0),
        )
    elif source in {"topic_decision_runs", "topic_relation_proposals"}:
        domain = "decisions"
        at = moment(record.get("updated_at") or record.get("created_at"))
        identifier = _identity(source, record.get("proposal_id") or identifier)
        values = dict(
            task="decision-run" if source == "topic_decision_runs" else "relationship-proposal",
            status=record.get("status") or "unknown",
            outcome=record.get("reason") or "unknown",
        )
    elif source == "topic_decision_calls":
        domain = "decisions"
        at = moment(record.get("started_at")) or datetime(1970, 1, 1, tzinfo=UTC)
        identifier = _identity("decision-call", record["proposal_id"], record["ordinal"])
        values = dict(
            ordinal=int(record["ordinal"]),
            stage=record.get("stage") or "unknown",
            stage_call=int(record.get("stage_call") or 0),
            escalated=int(bool(record.get("escalated"))),
            decision_tokens=int(record.get("tokens") or 0),
            charged_tokens=(
                None if record.get("charged_tokens") is None else int(record["charged_tokens"])
            ),
        )
    elif source in JOB_KINDS:
        if record.get("status") not in {"succeeded", "failed"}:
            return None
        domain, at = "processing", moment(record.get("finished_at"))
        attempt = record.get("attempts") or 0
        identifier = (
            _identity("attempt", source, identifier, attempt)
            if attempt
            else _identity("attempt", source, identifier, 0, at.isoformat() if at else None)
        )
        values = dict(
            task=JOB_KINDS[source],
            status=record["status"],
            outcome=record.get("outcome") or "unknown",
            completed=int(record["status"] == "succeeded"),
            failed=int(record["status"] == "failed"),
            useful=int(source == "article_analysis_jobs" and record.get("outcome") == "applied"),
        )
        usage = record.get("usage") or {}
        if isinstance(usage, str):
            import json

            usage = json.loads(usage)
        values["job_tokens"] = number(usage.get("totalTokens"), digits=18)
        values["has_job_tokens"] = int(
            re.fullmatch(r"[0-9]{1,18}", str(usage.get("totalTokens"))) is not None
        )
    elif source == "articles":
        domain, at = "publishing", moment(record.get("published_to_feed_at"))
        context = context or {}
        discovered = moment(record.get("discovered_at"))
        values = dict(
            published=1,
            status=record.get("publication_status") or "published",
            automated=int(context.get("automated", False)),
            content_type=record.get("content_type") or "article",
            source_ids=sorted({str(value) for value in context.get("source_ids", [])}),
            topic_ids=sorted({str(value) for value in context.get("topic_ids", [])}),
        )
        if at is not None and discovered is not None and at >= discovered:
            values.update(delay_seconds=(at - discovered).total_seconds(), has_delay=1)
    elif source == "article_opens":
        domain, at = "audience", moment(record.get("opened_hour"))
        identifier = _identity("open", record["article_id"], record["viewer_key"], at)
        values = dict(task="open", opens=1, viewer_key=record["viewer_key"])
    elif source == "user_accounts":
        domain, at = "audience", moment(record.get("created_at"))
        values = dict(task="signup", accounts_created=1)
    elif source == "topic_proposals":
        if record.get("status") not in {"approved", "rejected"}:
            return None
        domain, at = "processing", moment(record.get("reviewed_at"))
        identifier = _identity("topic-decision", identifier)
        values = dict(
            task="topic-decision", status=record["status"], completed=1, useful=1, topic_decisions=1
        )
    if not domain or at is None or identifier is None:
        return None
    row = empty_fact(
        domain,
        identifier,
        at,
        event_id=event["id"],
        captured_at=event["created_at"],
        generation=generation,
        side=side,
        deleted=deleted,
    )
    if source in JOB_KINDS or source == "inference_calls":
        duration = record.get("duration_ms")
        if duration is not None and int(duration) >= 0:
            values.update(duration_ms=int(duration), has_duration=1)
    row.update(values)
    row["entity_id"] = str(
        record.get("id") or record.get("article_id") or record.get("proposal_id") or identifier
    )
    row["parent_id"] = str(
        record.get("job_id")
        or record.get("proposal_id")
        or (record.get("topic_id") or record.get("article_id") if source in JOB_KINDS else None)
        or ZERO_UUID
    )
    row["attempt"] = max(int(record.get("attempts") or record.get("attempt") or 0), 0)
    row["origin_created_at"] = moment(record.get("discovered_at") or record.get("created_at"))
    row["ended_at"] = moment(record.get("finished_at"))
    if source in JOB_KINDS and record.get("source_id"):
        row["source_ids"] = [str(record["source_id"])]
    if source == "topic_decision_calls":
        row["event_time"] = moment(record.get("started_at"))
    return row


def normalize_event(event, generation, *, context=None):
    """Corrections cancel the old cohort; retries keep earlier attempt history."""
    source = event["source_table"]
    before = event.get("before_record")
    after = None if event["operation"] == "D" else event.get("record")
    if event["operation"] == "D" and before is None:
        before = event.get("record")
    old = _fact(source, before, event, generation, deleted=True, side=0, context=context)
    new = _fact(source, after, event, generation, context=context)
    rows = []
    if old is not None:
        old_key = (old["domain"], old["bucket_hour"], old["fact_id"])
        new_key = None if new is None else (new["domain"], new["bucket_hour"], new["fact_id"])
        preserve_attempt = (
            source in JOB_KINDS
            and after is not None
            and (new is None or (before.get("attempts") or 0) != (after.get("attempts") or 0))
        )
        if old_key != new_key and not preserve_attempt:
            rows.append(old)
    if new is not None:
        rows.append(new)
    return rows


async def _publication_records(connection, identifiers):
    if not identifiers:
        return {}, {}
    ids = list(identifiers)
    records = await (
        await connection.execute(
            f"SELECT {TABLES['articles'].record_sql('a')} AS record FROM articles a "
            "WHERE a.id=ANY(%s::uuid[])",
            (ids,),
        )
    ).fetchall()
    records = {str(row["record"]["id"]): row["record"] for row in records}
    contexts: dict[str, dict[str, Any]] = {
        identity: {"source_ids": [], "topic_ids": [], "automated": False} for identity in ids
    }
    for source, column, key in (
        ("article_origins", "source_id", "source_ids"),
        ("article_topics", "topic_id", "topic_ids"),
    ):
        where = " AND role IN ('primary','supporting')" if source == "article_topics" else ""
        rows = await (
            await connection.execute(
                f"SELECT article_id,{column} AS identity FROM {source} "
                f"WHERE article_id=ANY(%s::uuid[]){where}",
                (ids,),
            )
        ).fetchall()
        for row in rows:
            contexts[str(row["article_id"])][key].append(str(row["identity"]))
    rows = await (
        await connection.execute(
            """SELECT a.id, EXISTS(SELECT 1 FROM article_reviews r WHERE r.article_id=a.id
          AND r.created_at<=a.published_to_feed_at AND r.action='publish'
          AND r.automation<>'{}'::jsonb)
          AND NOT EXISTS(SELECT 1 FROM article_reviews r WHERE r.article_id=a.id
          AND r.created_at<=a.published_to_feed_at AND r.automation='{}'::jsonb) AS automated
          FROM articles a WHERE a.id=ANY(%s::uuid[])""",
            (ids,),
        )
    ).fetchall()
    for row in rows:
        contexts[str(row["id"])]["automated"] = row["automated"]
    return records, contexts


async def transform_events(connection, events, generation, publication_context=None):
    result = []
    publications: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event["source_table"] in {
            "articles",
            "article_origins",
            "article_topics",
            "article_reviews",
        }:
            record = event.get("record") or event.get("before_record") or {}
            identity = (
                record.get("id")
                if event["source_table"] == "articles"
                else record.get("article_id")
            )
            if identity:
                publications.setdefault(str(identity), []).append(event)
        elif event["operation"] != "T":
            result.extend(normalize_event(event, generation))
    if not publications:
        return result
    if publication_context is None:
        records, contexts = await _publication_records(connection, publications)
    else:
        records = {
            key: events[-1].get("record")
            for key, events in publications.items()
            if events[-1]["operation"] != "D"
        }
        contexts = publication_context
    for identity, changes in publications.items():
        latest = max(changes, key=lambda event: event["id"])
        context = contexts.get(identity, {})
        template = dict(
            latest, source_table="articles", record=records.get(identity), operation="I"
        )
        new = _fact("articles", records.get(identity), template, generation, context=context)
        old_keys = {}
        for event in changes:
            if event["source_table"] == "articles":
                for previous in (event.get("before_record"), event.get("record")):
                    old = _fact(
                        "articles",
                        previous,
                        template,
                        generation,
                        deleted=True,
                        side=0,
                        context=context,
                    )
                    if old is not None:
                        old_keys[(old["bucket_hour"], old["fact_id"])] = old
        if new is not None:
            old_keys.pop((new["bucket_hour"], new["fact_id"]), None)
            result.append(new)
        result.extend(old_keys.values())
    return result
