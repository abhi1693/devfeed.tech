"""Restart-safe preservation of the first ClickHouse layout before native cutover.

Only this background migration reads the retired versioned tables. Atomic table
renames retain their UUID and every physical row without copying or re-encoding
historical data. Serving queries never use these cold archives.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from devfeed_core.analytics_rollups import RollupPublisher

# Frozen v1 identities, independent of future operational models and capture fields.
V1_KEYS = {
    **dict.fromkeys(
        (
            "articles",
            "sources",
            "topics",
            "article_origins",
            "user_accounts",
            "topic_proposals",
            "topic_relation_proposals",
            "inference_calls",
            "article_reviews",
            "ingestion_jobs",
            "article_enrichment_jobs",
            "article_image_jobs",
            "source_enrichment_jobs",
            "article_analysis_jobs",
            "topic_analysis_jobs",
            "research_verification_jobs",
            "notification_deliveries",
        ),
        ("id",),
    ),
    "article_topics": ("article_id", "topic_id"),
    "article_likes": ("article_id", "user_id"),
    "article_opens": ("opened_hour", "article_id", "viewer_key"),
    "overview_daily": ("day",),
    "user_topics": ("user_id", "topic_id"),
    "user_sources": ("user_id", "source_id"),
    "user_tags": ("user_id", "tag_id"),
    "user_interests": ("user_id", "topic_id"),
    "user_recommendations": ("user_id", "article_id"),
    "user_recommendation_states": ("user_id",),
    "topic_decision_runs": ("proposal_id",),
    "topic_decision_calls": ("proposal_id", "ordinal"),
}
ARCHIVES = {
    **{f"{name}_versions": f"analytics_v1_archive_{name}_versions" for name in V1_KEYS},
    "_analytics_status": "analytics_v1_archive_status",
    "_analytics_truncates": "analytics_v1_archive_truncates",
}
PENDING_ARCHIVE = "analytics_v1_pending_archive"
ATTEMPT_EVIDENCE = "analytics_v1_attempt_evidence"


def _datetime(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


class NativeUpgrade:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client
        self.rollups = RollupPublisher(client)
        self._pending_initialized = False

    @staticmethod
    def _record_key(table, record):
        from devfeed_core.analytics_schema import TABLES

        return tuple(
            _datetime(record[name]) if name == "opened_hour" else str(record[name])
            for name in TABLES[table].keys
        )

    async def _pending_current(self, connection, table, records):
        """One indexed, bounded primary-key probe per source in the delivery page."""
        from sqlalchemy.dialects.postgresql import dialect

        from devfeed_core.analytics_schema import TABLES
        from devfeed_core.models import Base

        definition = TABLES[table]
        keys = definition.keys
        wanted = {self._record_key(table, record): record for record in records}
        columns = Base.metadata.tables[table].columns
        types = ",".join(
            f'"{name}" {columns[name].type.compile(dialect=dialect())}' for name in keys
        )
        match = " AND ".join(f'p."{name}"=k."{name}"' for name in keys)
        rows = await (
            await connection.execute(
                f"SELECT {definition.record_sql('p')} AS record FROM {table} p "
                f"JOIN jsonb_to_recordset(%s) AS k({types}) ON {match}",
                (Jsonb([{key: record[key] for key in keys} for record in wanted.values()]),),
            )
        ).fetchall()
        return {self._record_key(table, row["record"]): row["record"] for row in rows}

    async def _pending_cold(self, table, records, tables, truncates):
        """Read only logical rows that the finite v1 importer can make live later."""
        source = table + "_versions"
        archive = ARCHIVES[source]
        name = archive if archive in tables else source if source in tables else None
        if name is None:
            return {}
        keys = V1_KEYS[table]
        unique = {self._record_key(table, record): record for record in records}
        parameters: dict[str, Any] = {"truncate": truncates.get(table, 0)}
        probes = []
        for index, record in enumerate(unique.values()):
            bindings = []
            for key in keys:
                parameter = f"key_{index}_{key}"
                kind = (
                    "DateTime64(6, 'UTC')"
                    if key == "opened_hour"
                    else "Int64"
                    if key == "ordinal"
                    else "String"
                )
                bindings.append(f"{{{parameter}:{kind}}}")
                parameters[parameter] = (
                    _datetime(record[key]) if key == "opened_hour" else record[key]
                )
            probes.append("tuple(" + ",".join(bindings) + ")")
        rows = await self.client.query(
            f"SELECT * FROM `{name}` FINAL WHERE _deleted=0 "
            "AND _version>={truncate:UInt64} AND tuple("
            + ",".join(keys)
            + ") IN ("
            + ",".join(probes)
            + ")",
            parameters,
        )
        return {
            self._record_key(table, row): {
                key: value for key, value in row.items() if key not in {"_version", "_deleted"}
            }
            for row in rows
        }

    async def _pending_retries(self, source, changes, cold):
        """A queued transition proves the preceding terminal observation was a retry.

        The ordered audit provides a reverse, one-row probe even when that
        completion was acknowledged on an earlier delivery page. No distinct
        attempt is inferred from a corrected timestamp alone.
        """
        promoted = {}
        evidence = []
        for event in changes:
            record = event["record"]
            if event["operation"] != "I" or record.get("status") != "queued":
                continue
            previous = await self.client.query(
                f"SELECT record,operation FROM {PENDING_ARCHIVE} FINAL "
                "WHERE source_table={source:String} AND entity_key={entity:String} "
                "AND event_id<{before:UInt64} AND (operation='D' OR "
                "JSONExtractString(record,'status') IN ('succeeded','failed')) "
                "ORDER BY event_id DESC LIMIT 1",
                {"source": source, "entity": str(record["id"]), "before": event["id"]},
            )
            if not previous or previous[0]["operation"] == "D":
                continue
            terminal = json.loads(previous[0]["record"])
            finished = _datetime(terminal.get("finished_at"))
            if finished is None:
                continue
            promoted[event["id"]] = terminal
            evidence.append(
                dict(
                    source_table=source,
                    entity_key=str(record["id"]),
                    finished_at=finished,
                    record=json.dumps(terminal, default=str),
                    event_id=event["id"],
                )
            )
        await self.client.insert(ATTEMPT_EVIDENCE, evidence)
        probes = {}
        observations = [event["record"] for event in changes] + list(cold.values())
        for record in observations:
            finished = _datetime(record.get("finished_at"))
            if finished is not None:
                probes[(str(record["id"]), finished)] = record
        if not probes:
            return promoted, set()
        parameters: dict[str, Any] = {"source": source}
        bindings = []
        for index, (identity, finished) in enumerate(probes):
            parameters[f"id_{index}"] = identity
            parameters[f"at_{index}"] = finished
            bindings.append(f"tuple({{id_{index}:String}},{{at_{index}:DateTime64(6,'UTC')}})")
        found = await self.client.query(
            f"SELECT entity_key,finished_at FROM {ATTEMPT_EVIDENCE} FINAL "
            "WHERE source_table={source:String} AND tuple(entity_key,finished_at) IN ("
            + ",".join(bindings)
            + ")",
            parameters,
        )
        return promoted, {(row["entity_key"], _datetime(row["finished_at"])) for row in found}

    async def _pending_days(self, connection, changes, generation):
        days = {str(event["record"]["day"]): event["record"] for event in changes}
        current = await (
            await connection.execute(
                "SELECT day,metrics FROM overview_daily WHERE day=ANY(%s::date[])",
                (list(days),),
            )
        ).fetchall()
        days.update({str(row["day"]): row for row in current})
        boundary = await (
            await connection.execute(
                "SELECT coalesce((SELECT (cursor->>'capture_started_at')::timestamptz "
                "FROM analytics_upgrade_checkpoint WHERE name='v1'),"
                "native_capture_started_at,capture_started_at) AS captured "
                "FROM analytics_pipeline_state WHERE singleton"
            )
        ).fetchone()
        if boundary is None or boundary["captured"] is None:
            raise RuntimeError("Legacy daily history requires a capture boundary")
        await self._archive_days(
            list(days.values()), _datetime(boundary["captured"]), version=generation
        )

    async def normalize_pending(self, connection, events, generation):
        """Reconcile finite pre-cutover events whose UPDATEs lack before-images.

        Raw observations are archived before acknowledgement. Current PG entities
        own their native value; obsolete observations only retire old cohorts.
        This path disappears once the format-1 queue is drained.
        """
        from devfeed_core.analytics_schema import JOB_KINDS, TABLES
        from devfeed_core.analytics_transform import FACT_SOURCES, _fact

        legacy = [event for event in events if event.get("format_version", 2) == 1]
        if not legacy:
            return events
        if not self._pending_initialized:
            await self.client.execute(
                f"CREATE TABLE IF NOT EXISTS {PENDING_ARCHIVE} "
                "(event_id UInt64,source_table String,entity_key String,operation String,"
                "created_at DateTime64(6,'UTC'),record String,before_record Nullable(String),"
                "format_version UInt8) ENGINE=ReplacingMergeTree "
                "ORDER BY (source_table,entity_key,event_id)"
            )
            await self.client.execute(
                f"CREATE TABLE IF NOT EXISTS {ATTEMPT_EVIDENCE} "
                "(source_table String,entity_key String,finished_at DateTime64(6,'UTC'),"
                "record String,event_id UInt64) ENGINE=ReplacingMergeTree(event_id) "
                "ORDER BY (source_table,entity_key,finished_at)"
            )
            self._pending_initialized = True
        await self.client.insert(
            PENDING_ARCHIVE,
            [
                dict(
                    event_id=event["id"],
                    source_table=event["source_table"],
                    entity_key=str((event.get("record") or {}).get("id") or ""),
                    operation=event["operation"],
                    created_at=event["created_at"],
                    record=json.dumps(event.get("record"), default=str),
                    before_record=(
                        json.dumps(event["before_record"], default=str)
                        if event.get("before_record") is not None
                        else None
                    ),
                    format_version=1,
                )
                for event in legacy
            ],
        )
        daily = [
            event
            for event in legacy
            if event["source_table"] == "overview_daily" and event["operation"] != "T"
        ]
        if daily:
            await self._pending_days(connection, daily, generation)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in legacy:
            source = event["source_table"]
            # Old TRUNCATEs describe state already represented by the native
            # bootstrap. Replaying them would erase freshly captured gauges.
            if source in TABLES and event["operation"] != "T":
                grouped.setdefault(source, []).append(event)
        tables = await self._tables() if grouped else {}
        truncate_table = (
            ARCHIVES["_analytics_truncates"]
            if ARCHIVES["_analytics_truncates"] in tables
            else "_analytics_truncates"
        )
        truncates = (
            {
                row["source_table"]: row["version"]
                for row in await self.client.query(
                    f"SELECT source_table,max(version) AS version FROM `{truncate_table}` "
                    "GROUP BY source_table"
                )
            }
            if truncate_table in tables
            else {}
        )
        replacements: dict[int, list[dict[str, Any]]] = {}
        for source, changes in grouped.items():
            records = [event["record"] for event in changes]
            current = await self._pending_current(connection, source, records)
            cold = (
                await self._pending_cold(source, records, tables, truncates)
                if source in FACT_SOURCES
                else {}
            )
            promoted, retained_attempts = (
                await self._pending_retries(source, changes, cold)
                if source in JOB_KINDS
                else ({}, set())
            )
            for event in changes:
                key = self._record_key(source, event["record"])
                present = current.get(key)
                observations = [event["record"]]
                if key in cold:
                    observations.append(cold[key])
                if event["id"] in promoted:
                    observations.append(promoted[event["id"]])
                normalized = []

                positive = _fact(source, present, event, generation)
                positive_key = (
                    (positive["domain"], positive["bucket_hour"], positive["fact_id"])
                    if positive is not None
                    else None
                )
                for observation in observations:
                    previous = dict(observation)
                    if source in JOB_KINDS:
                        matched = (
                            present is not None
                            and present.get("status") in {"succeeded", "failed"}
                            and _datetime(present.get("finished_at"))
                            == _datetime(previous.get("finished_at"))
                        )
                        # Retire any previously imported unknown-attempt identity
                        # before hydrating a now-known terminal attempt.
                        if matched:
                            previous["attempts"] = 0
                        known_retry = (
                            str(previous["id"]),
                            _datetime(previous.get("finished_at")),
                        ) in retained_attempts or previous == promoted.get(event["id"])
                        deleting_latest = (
                            event["operation"] == "D"
                            and event["record"].get("status") in {"succeeded", "failed"}
                            and _datetime(event["record"].get("finished_at"))
                            == _datetime(previous.get("finished_at"))
                        )
                        operation = (
                            "I" if known_retry and not deleting_latest and not matched else "D"
                        )
                    elif source == "article_opens" and event["operation"] == "I":
                        # V1 intentionally encoded retention DELETE as I.
                        operation = "I"
                    else:
                        operation = "D"
                    old = _fact(source, previous, event, generation)
                    old_key = (
                        (old["domain"], old["bucket_hour"], old["fact_id"])
                        if old is not None
                        else None
                    )
                    if old_key is not None:
                        if old_key != positive_key:
                            normalized.append(
                                dict(
                                    event, record=previous, before_record=None, operation=operation
                                )
                            )
                    elif present is None:
                        normalized.append(
                            dict(event, record=previous, before_record=None, operation="D")
                        )
                if present is not None:
                    normalized.append(
                        dict(event, record=present, before_record=None, operation="I")
                    )
                replacements[event["id"]] = normalized
        return [
            normalized
            for event in events
            for normalized in (
                replacements.get(event["id"], [])
                if event.get("format_version", 2) == 1
                else [event]
            )
        ]

    async def _tables(self):
        names = ", ".join(f"'{name}'" for name in (*ARCHIVES, *ARCHIVES.values(), *V1_KEYS))
        rows = await self.client.query(
            "SELECT name, toString(uuid) AS uuid, engine FROM system.tables "
            f"WHERE database = currentDatabase() AND name IN ({names})"
        )
        return {row["name"]: row for row in rows}

    async def _save(self, connection, state, fence, *, complete=False, facts=(), generation=0):
        async with connection.transaction():
            await fence(connection)
            if facts:
                await self.rollups.mark_dirty(connection, facts, generation)
            await connection.execute(
                "INSERT INTO analytics_upgrade_checkpoint(name,cursor,complete) "
                "VALUES ('v1',%s,%s) "
                "ON CONFLICT(name) DO UPDATE SET cursor=excluded.cursor,complete=excluded.complete",
                (Jsonb(state), complete),
            )
            if complete:
                await connection.execute(
                    "UPDATE analytics_pipeline_state SET native_import_complete=true, "
                    "native_capture_started_at=coalesce(%s,native_capture_started_at,"
                    "capture_started_at), "
                    "reader_coverage_start=coalesce(%s,reader_coverage_start, "
                    "(coalesce(%s,native_capture_started_at,capture_started_at)"
                    "-interval '29 days')::date) "
                    "WHERE singleton",
                    (
                        state.get("capture_started_at"),
                        state.get("reader_coverage_start"),
                        state.get("capture_started_at"),
                    ),
                )

    async def _initialize(self, connection, fence):
        tables = await self._tables()
        if not any(name in tables for name in (*ARCHIVES, *ARCHIVES.values())):
            await self._save(connection, {}, fence, complete=True)
            return
        engine = await self.client.query(
            "SELECT engine FROM system.databases WHERE name = currentDatabase()"
        )
        if engine[0]["engine"] != "Atomic":
            raise RuntimeError("Native analytics upgrade requires an Atomic ClickHouse database")
        state: dict[str, Any] = {"phase": "archive", "index": 0, "archived": {}}
        original = await (
            await connection.execute(
                "SELECT capture_started_at FROM analytics_pipeline_state WHERE singleton"
            )
        ).fetchone()
        if original and original["capture_started_at"]:
            captured = _datetime(original["capture_started_at"])
            state["capture_started_at"] = captured.isoformat()
            state["reader_coverage_start"] = (captured.date() - timedelta(days=29)).isoformat()
        status = (
            "_analytics_status" if "_analytics_status" in tables else ARCHIVES["_analytics_status"]
        )
        if status in tables:
            rows = await self.client.query(f"SELECT capture_started_at FROM `{status}` FINAL")
            if rows:
                captured = _datetime(rows[0]["capture_started_at"])
                state["capture_started_at"] = captured.isoformat()
                state["reader_coverage_start"] = (captured.date() - timedelta(days=29)).isoformat()
        await self._save(connection, state, fence)

    async def _archive(self, connection, state, fence):
        tables = await self._tables()
        names = tuple(ARCHIVES)
        while state["index"] < len(names):
            source = names[state["index"]]
            target = ARCHIVES[source]
            before, after = tables.get(source), tables.get(target)
            if before is None and after is None:
                state["index"] += 1
                continue
            if before is not None and after is not None:
                raise RuntimeError(f"Native archive conflicts with existing source: {source}")
            current = before or after
            if "MergeTree" not in current["engine"]:
                raise RuntimeError(f"Unexpected analytical archive engine: {source}")
            intent = state.get("intent")
            if intent is None:
                state["intent"] = current["uuid"]
                await self._save(connection, state, fence)
                return
            if current["uuid"] != intent:
                raise RuntimeError(f"Analytical archive identity changed: {source}")
            frozen = source if before is not None else target
            # Preserve every remaining version, including after server restart.
            # This cold archive receives no further inserts or OPTIMIZE commands.
            await self.client.execute(f"SYSTEM STOP MERGES `{frozen}`")
            await self.client.execute(
                f"ALTER TABLE `{frozen}` MODIFY SETTING "
                "max_bytes_to_merge_at_max_space_in_pool=0,min_age_to_force_merge_seconds=0"
            )
            if before is not None:
                await self.client.execute(f"RENAME TABLE `{source}` TO `{target}`")
                verified = (await self._tables()).get(target)
                if verified is None or verified["uuid"] != intent:
                    raise RuntimeError(f"Analytical archive identity was not preserved: {source}")
            state["archived"][source] = intent
            state["index"] += 1
            state.pop("intent")
            await self._save(connection, state, fence)
            return
        state.update(phase="import", index=0)
        truncate_table = ARCHIVES["_analytics_truncates"]
        state["truncates"] = (
            {
                row["source_table"]: row["version"]
                for row in await self.client.query(
                    f"SELECT source_table, max(version) AS version FROM `{truncate_table}` "
                    "GROUP BY source_table"
                )
            }
            if truncate_table in tables
            else {}
        )
        await self._save(connection, state, fence)

    def _page(self, table, state):
        keys = V1_KEYS[table]
        parameters: dict[str, Any] = {
            "batch": self.settings.analytics_backfill_batch_size,
            "truncate": state.get("truncates", {}).get(table, 0),
        }
        query = (
            f"SELECT * FROM `{ARCHIVES[table + '_versions']}` FINAL "
            "WHERE _deleted = 0 AND _version >= {truncate:UInt64}"
        )
        if state.get("after") is not None:
            bindings = []
            for index, (name, value) in enumerate(zip(keys, state["after"], strict=True)):
                kind = (
                    "DateTime64(6, 'UTC')"
                    if name == "opened_hour"
                    else "Date"
                    if name == "day"
                    else "Int64"
                    if name == "ordinal"
                    else "String"
                )
                bindings.append("{key" + str(index) + ":" + kind + "}")
                parameters["key" + str(index)] = (
                    _datetime(value) if name == "opened_hour" else value
                )
            query += " AND tuple(" + ", ".join(keys) + ") > tuple(" + ", ".join(bindings) + ")"
        query += " ORDER BY " + ", ".join(keys) + " LIMIT {batch:UInt32}"
        return query, parameters

    async def _publication_context(self, rows, state):
        ids = [row["id"] for row in rows]
        context = {
            identity: {"source_ids": [], "topic_ids": [], "automated": False} for identity in ids
        }
        for table, select, extra, field in (
            ("article_origins", "groupUniqArray(source_id)", "", "source_ids"),
            (
                "article_topics",
                "groupUniqArray(topic_id)",
                " AND role IN ('primary','supporting')",
                "topic_ids",
            ),
        ):
            if table + "_versions" not in state["archived"]:
                continue
            found = await self.client.query(
                f"SELECT article_id, {select} AS value "
                f"FROM `{ARCHIVES[table + '_versions']}` FINAL "
                "WHERE article_id IN {ids:Array(String)} AND _deleted = 0 "
                "AND _version >= {truncate:UInt64}" + extra + " GROUP BY article_id",
                {"ids": ids, "truncate": state.get("truncates", {}).get(table, 0)},
            )
            for row in found:
                context[row["article_id"]][field] = row["value"]
        table = "article_reviews"
        if table + "_versions" in state["archived"]:
            moment = "parseDateTime64BestEffortOrNull(mapFromArrays({ids:Array(String)}, "
            moment += "{published:Array(String)})[article_id])"
            found = await self.client.query(
                "SELECT article_id, countIf(action='publish' AND automation!='{}' "
                f"AND created_at <= {moment}) > 0 AND "
                f"countIf(automation='{{}}' AND created_at <= {moment}) = 0 AS value "
                f"FROM `{ARCHIVES[table + '_versions']}` FINAL "
                "WHERE article_id IN {ids:Array(String)} AND _deleted=0 "
                "AND _version >= {truncate:UInt64} GROUP BY article_id",
                {
                    "ids": ids,
                    "published": [row.get("published_to_feed_at") or "" for row in rows],
                    "truncate": state.get("truncates", {}).get(table, 0),
                },
            )
            for row in found:
                context[row["article_id"]]["automated"] = bool(row["value"])
        return context

    async def _attempts(self, connection, table, rows):
        current = await (
            await connection.execute(
                f"SELECT id,attempts,finished_at,status FROM {table} WHERE id=ANY(%s::uuid[])",
                ([row["id"] for row in rows],),
            )
        ).fetchall()
        current = {str(row["id"]): row for row in current}
        for row in rows:
            match = current.get(row["id"])
            if (
                match
                and match["status"] in {"succeeded", "failed"}
                and _datetime(match["finished_at"]) == _datetime(row.get("finished_at"))
            ):
                row["attempts"] = match["attempts"]

    async def _missing_entities(self, connection, table, rows):
        """Current PG backfill owns mutable entities, including corrected cohort dates."""
        current = await (
            await connection.execute(
                f"SELECT id FROM {table} WHERE id=ANY(%s::uuid[])",
                ([row["id"] for row in rows],),
            )
        ).fetchall()
        present = {str(row["id"]) for row in current}
        return [row for row in rows if row["id"] not in present]

    async def _archive_days(self, rows, captured, *, version=0):
        archive = []
        for row in rows:
            if str(row["day"]) >= captured.date().isoformat():
                continue
            metrics = row["metrics"]
            metrics = json.loads(metrics) if isinstance(metrics, str) else metrics
            archive.append(
                dict(
                    day=row["day"],
                    opens=metrics.get("opens"),
                    readers=metrics.get("readers"),
                    accounts_created=metrics.get("accounts"),
                    published=metrics.get("published"),
                    version=version,
                )
            )
        await self.client.insert("analytics_audience_archive_daily", archive)

    async def _import(self, connection, state, generation, fence):
        from devfeed_core.analytics_transform import FACT_SOURCES, JOB_KINDS, transform_events

        names = tuple(name for name in V1_KEYS if name in {*FACT_SOURCES, "overview_daily"})
        while state["index"] < len(names):
            table = names[state["index"]]
            if table + "_versions" not in state["archived"]:
                state["index"] += 1
                continue
            query, parameters = self._page(table, state)
            rows = await self.client.query(query, parameters)
            facts = []
            retained = rows
            if rows and table in {
                "articles",
                "inference_calls",
                "user_accounts",
                "topic_proposals",
            }:
                retained = await self._missing_entities(connection, table, rows)
            if retained:
                captured = _datetime(state.get("capture_started_at")) or datetime(
                    1970, 1, 1, tzinfo=UTC
                )
                if table in JOB_KINDS:
                    await self._attempts(connection, table, retained)
                events = [
                    dict(
                        id=row["_version"],
                        source_table=table,
                        operation="I",
                        before_record=None,
                        created_at=captured,
                        record={
                            key: value
                            for key, value in row.items()
                            if key not in {"_version", "_deleted"}
                        },
                    )
                    for row in retained
                ]
                context = (
                    await self._publication_context(retained, state)
                    if table == "articles"
                    else None
                )
                facts = await transform_events(connection, events, 0, publication_context=context)
                await self.client.insert("analytics_fact_events", facts)
                if table == "overview_daily":
                    await self._archive_days(retained, captured)
            if len(rows) < self.settings.analytics_backfill_batch_size:
                state["index"] += 1
                state.pop("after", None)
            else:
                state["after"] = [rows[-1][name] for name in V1_KEYS[table]]
            await self._save(connection, state, fence, facts=facts, generation=generation)
            return
        state.update(phase="retire", index=0)
        await self._save(connection, state, fence)

    async def _retire(self, connection, state, fence):
        tables = await self._tables()
        names = tuple(V1_KEYS)
        while state["index"] < len(names):
            name = names[state["index"]]
            state["index"] += 1
            if name not in tables:
                continue
            if tables[name]["engine"] != "View":
                raise RuntimeError(f"Refusing to retire a non-view analytical object: {name}")
            await self.client.execute(f"DROP VIEW `{name}`")
            await self._save(connection, state, fence)
            return
        await self._save(connection, state, fence, complete=True)

    async def once(self, connection, generation, *, fence):
        row = await (
            await connection.execute(
                "SELECT cursor,complete FROM analytics_upgrade_checkpoint WHERE name='v1'"
            )
        ).fetchone()
        if row is not None and row["complete"]:
            return False
        if row is None:
            await self._initialize(connection, fence)
        else:
            state = row["cursor"]
            if state["phase"] == "archive":
                await self._archive(connection, state, fence)
            elif state["phase"] == "import":
                await self._import(connection, state, generation, fence)
            elif state["phase"] == "retire":
                await self._retire(connection, state, fence)
            else:
                raise RuntimeError("Unknown native analytics upgrade checkpoint")
        return True
