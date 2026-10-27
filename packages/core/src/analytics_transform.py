"""Normalize captured changes into typed domain facts and an exact decision ledger."""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from devfeed_core.analytics_facts import ZERO_UUID, empty_fact
from devfeed_core.analytics_schema import JOB_KINDS, JOB_TABLES, TABLES

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


@dataclass(slots=True)
class _Observation:
    domain: str
    identifier: Any
    at: datetime | None
    values: dict[str, Any]


def _usage(value):
    value = value or {}
    return json.loads(value) if isinstance(value, str) else value


def _inference(source, record, context):
    tokens = _usage(record.get("tokens"))
    input_tokens = number(tokens.get("inputTokens"))
    output_tokens = number(tokens.get("outputTokens"))
    return _Observation(
        "inference",
        record.get("id"),
        moment(record.get("started_at")),
        dict(
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
        ),
    )


def _decision(source, record, context):
    return _Observation(
        "decisions",
        _identity(source, record.get("proposal_id") or record.get("id")),
        moment(record.get("updated_at") or record.get("created_at")),
        dict(
            task="decision-run" if source == "topic_decision_runs" else "relationship-proposal",
            status=record.get("status") or "unknown",
            outcome=record.get("reason") or "unknown",
        ),
    )


def _decision_call(source, record, context):
    return _Observation(
        "decisions",
        _identity("decision-call", record["proposal_id"], record["ordinal"]),
        moment(record.get("started_at")) or datetime(1970, 1, 1, tzinfo=UTC),
        dict(
            ordinal=int(record["ordinal"]),
            stage=record.get("stage") or "unknown",
            stage_call=int(record.get("stage_call") or 0),
            escalated=int(bool(record.get("escalated"))),
            decision_tokens=int(record.get("tokens") or 0),
            charged_tokens=(
                None if record.get("charged_tokens") is None else int(record["charged_tokens"])
            ),
        ),
    )


def _completion(source, record, context):
    if record.get("status") not in {"succeeded", "failed"}:
        return None
    at = moment(record.get("finished_at"))
    attempt = record.get("attempts") or 0
    identifier = (
        _identity("attempt", source, record.get("id"), attempt)
        if attempt
        else _identity("attempt", source, record.get("id"), 0, at.isoformat() if at else None)
    )
    usage = _usage(record.get("usage"))
    return _Observation(
        "processing",
        identifier,
        at,
        dict(
            task=JOB_KINDS[source],
            status=record["status"],
            outcome=record.get("outcome") or "unknown",
            completed=int(record["status"] == "succeeded"),
            failed=int(record["status"] == "failed"),
            useful=int(source == "article_analysis_jobs" and record.get("outcome") == "applied"),
            job_tokens=number(usage.get("totalTokens"), digits=18),
            has_job_tokens=int(
                re.fullmatch(r"[0-9]{1,18}", str(usage.get("totalTokens"))) is not None
            ),
        ),
    )


def _publication(source, record, context):
    at = moment(record.get("published_to_feed_at"))
    discovered = moment(record.get("discovered_at"))
    context = context or {}
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
    return _Observation("publishing", record.get("id"), at, values)


def _audience(source, record, context):
    if source == "article_opens":
        at = moment(record.get("opened_hour"))
        return _Observation(
            "audience",
            _identity("open", record["article_id"], record["viewer_key"], at),
            at,
            dict(task="open", opens=1, viewer_key=record["viewer_key"]),
        )
    return _Observation(
        "audience",
        record.get("id"),
        moment(record.get("created_at")),
        dict(task="signup", accounts_created=1),
    )


def _topic_decision(source, record, context):
    if record.get("status") not in {"approved", "rejected"}:
        return None
    return _Observation(
        "processing",
        _identity("topic-decision", record.get("id")),
        moment(record.get("reviewed_at")),
        dict(
            task="topic-decision", status=record["status"], completed=1, useful=1, topic_decisions=1
        ),
    )


_FACT_BUILDERS = {
    "inference_calls": _inference,
    "topic_decision_calls": _decision_call,
    "topic_decision_runs": _decision,
    "topic_relation_proposals": _decision,
    "articles": _publication,
    "article_opens": _audience,
    "user_accounts": _audience,
    "topic_proposals": _topic_decision,
    **dict.fromkeys(JOB_TABLES, _completion),
}
FACT_SOURCES = tuple(_FACT_BUILDERS)


def _publication_owners(event):
    if event["source_table"] == "articles":
        record = event.get("record") or event.get("before_record") or {}
        return {str(record["id"])} if record.get("id") else set()
    # Reparenting removes attribution from the previous article as well as
    # adding it to the new article. Hydrate each current owner only once.
    return {
        str(record["article_id"])
        for record in (event.get("record") or {}, event.get("before_record") or {})
        if record.get("article_id")
    }


class FactTransformer:
    """Build a generation of facts, hydrating publication owners asynchronously."""

    def __init__(self, connection):
        self.connection = connection

    def fact(self, source, record, event, generation, *, deleted=False, side=1, context=None):
        builder = _FACT_BUILDERS.get(source)
        if not record or builder is None:
            return None
        observed = builder(source, record, context)
        if observed is None or observed.at is None or observed.identifier is None:
            return None
        row = empty_fact(
            observed.domain,
            observed.identifier,
            observed.at,
            event_id=event["id"],
            captured_at=event["created_at"],
            generation=generation,
            side=side,
            deleted=deleted,
        )
        if source in JOB_KINDS or source == "inference_calls":
            duration = record.get("duration_ms")
            if duration is not None and int(duration) >= 0:
                observed.values.update(duration_ms=int(duration), has_duration=1)
        row.update(observed.values)
        row["entity_id"] = str(
            record.get("id")
            or record.get("article_id")
            or record.get("proposal_id")
            or observed.identifier
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

    def normalize(self, event, generation, *, context=None):
        """Corrections cancel the old cohort; retries keep earlier attempt history."""
        source = event["source_table"]
        before = event.get("before_record")
        after = None if event["operation"] == "D" else event.get("record")
        if event["operation"] == "D" and before is None:
            before = event.get("record")
        old = self.fact(source, before, event, generation, deleted=True, side=0, context=context)
        new = self.fact(source, after, event, generation, context=context)
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

    async def _publication_records(self, identifiers):
        if not identifiers:
            return {}, {}
        ids = list(identifiers)
        records = await (
            await self.connection.execute(
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
                await self.connection.execute(
                    f"SELECT article_id,{column} AS identity FROM {source} "
                    f"WHERE article_id=ANY(%s::uuid[]){where}",
                    (ids,),
                )
            ).fetchall()
            for row in rows:
                contexts[str(row["article_id"])][key].append(str(row["identity"]))
        rows = await (
            await self.connection.execute(
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

    def _publication_facts(self, changes, record, context, generation):
        latest = max(changes, key=lambda event: event["id"])
        template = dict(latest, source_table="articles", record=record, operation="I")
        new = self.fact("articles", record, template, generation, context=context)
        old_keys = {}
        for event in changes:
            if event["source_table"] != "articles":
                continue
            for previous in (event.get("before_record"), event.get("record")):
                old = self.fact(
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
            yield new
        yield from old_keys.values()

    async def transform(self, events, generation, publication_context=None):
        result = []
        publications: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            if event["source_table"] in {
                "articles",
                "article_origins",
                "article_topics",
                "article_reviews",
            }:
                for identity in _publication_owners(event):
                    publications.setdefault(identity, []).append(event)
            elif event["operation"] != "T":
                result.extend(self.normalize(event, generation))
        if not publications:
            return result
        if publication_context is None:
            records, contexts = await self._publication_records(publications)
        else:
            records = {
                key: events[-1].get("record")
                for key, events in publications.items()
                if events[-1]["operation"] != "D"
            }
            contexts = publication_context
        for identity, changes in publications.items():
            result.extend(
                self._publication_facts(
                    changes, records.get(identity), contexts.get(identity, {}), generation
                )
            )
        return result
