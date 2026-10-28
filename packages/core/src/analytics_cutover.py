"""Finite PostgreSQL retirement for the direct ClickHouse analytics rollout.

Only legacy analytics are frozen. Source/business tables remain writable and
legacy foreign keys are removed so their deletion cannot block business work.
The write guard is temporary and disappears with the imported legacy tables.
"""

import asyncio

from psycopg import sql

# TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Retire this entire module once all
# supported installations have completed their one-time analytical import.
LEGACY_TABLES = frozenset(
    [
        "overview_daily",
        "inference_calls",
        "inference_daily_usage",
        "topic_decision_calls",
        "article_opens",
        "article_engagement",
        "partner_daily_metrics",
        "partner_tracked_daily_metrics",
        "partner_delivery_events",
        "pipeline_projections",
        "read_model_events",
        "read_model_states",
        "report_snapshots",
        "report_refreshes",
        "analytics_export_state",
        "analytics_export_cursors",
        "analytics_outbox",
        "analytics_pipeline_state",
        "analytics_bootstrap_cursors",
        "analytics_capture_events",
        "analytics_ingest_events",
        "analytics_fact_refresh",
        "analytics_rollup_compaction",
        "analytics_upgrade_checkpoint",
        "analytics_gauge_refreshes",
        "analytics_gauge_bootstrap",
        "analytics_card_refresh",
        "analytics_card_backfills",
        "analytics_card_fanout",
        "analytics_card_timers",
        "analytics_card_timer_bootstrap",
    ]
)
TRIGGERS = frozenset(
    [
        "analytics_capture",
        "analytics_capture_truncate",
        "read_model_changed",
        "read_model_truncated",
        "read_model_article_image_changed",
        "read_model_source_image_changed",
        "read_model_catalog_changed",
        "read_model_catalog_truncated",
        "read_model_article_clock_changed",
        "topic_decision_calls_sync",
        "inference_daily_usage_insert",
        "inference_daily_usage_update",
        "inference_daily_usage_delete",
        "inference_daily_usage_truncate",
    ]
)
CAPTURE_SOURCES = LEGACY_TABLES | frozenset(
    [
        "articles",
        "sources",
        "topics",
        "article_topics",
        "article_origins",
        "user_accounts",
        "user_topics",
        "user_sources",
        "user_tags",
        "user_interests",
        "user_recommendations",
        "user_recommendation_states",
        "topic_proposals",
        "topic_relation_proposals",
        "topic_decision_runs",
        "article_reviews",
        "source_candidates",
        "article_contents",
        "source_reviews",
        "source_discovery_jobs",
        "catalog_revisions",
        "article_likes",
        "ingestion_jobs",
        "article_enrichment_jobs",
        "article_image_jobs",
        "source_enrichment_jobs",
        "article_analysis_jobs",
        "topic_analysis_jobs",
        "research_verification_jobs",
        "notification_deliveries",
    ]
)

GUARD = "devfeed_analytics_frozen"
SEARCH_GUARD = "devfeed_search_analytics_frozen"


class AnalyticsCutoverBusy(RuntimeError):
    """A short lock admission attempt encountered an active source writer."""


async def _rows(connection, statement, parameters=None):
    cursor = await connection.execute(statement, parameters)
    return await cursor.fetchall()


def _values(row):
    return tuple(row.values()) if isinstance(row, dict) else tuple(row)


class LegacyAnalyticsCutover:
    """Freeze and later drop legacy analytics under bounded NOWAIT DDL locks."""

    def __init__(self, *, timeout=2):
        if type(timeout) not in (int, float) or not 0 < timeout <= 10:
            raise ValueError("Invalid analytics cutover timeout")
        self.timeout = timeout

    async def tables(self, connection):
        return {
            _values(row)[0]
            for row in await _rows(
                connection,
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname=ANY(%s)",
                (sorted(LEGACY_TABLES),),
            )
        }

    async def _triggers(self, connection):
        return await _rows(
            connection,
            "SELECT c.relname,t.tgname,p.proname FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_proc p ON p.oid=t.tgfoid WHERE n.nspname='public' "
            "AND NOT t.tgisinternal AND t.tgname=ANY(%s) "
            "AND c.relname=ANY(%s) ORDER BY c.relname,t.tgname",
            (sorted(TRIGGERS), sorted(CAPTURE_SOURCES)),
        )

    async def _foreign_keys(self, connection, tables):
        return await _rows(
            connection,
            "SELECT c.relname,k.conname,r.relname AS referenced FROM pg_constraint k "
            "JOIN pg_class c ON c.oid=k.conrelid JOIN pg_class r ON r.oid=k.confrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND k.contype='f' AND c.relname=ANY(%s) ORDER BY c.relname,k.conname",
            (sorted(tables),),
        )

    async def _lock(self, connection, names):
        await connection.execute(
            "SELECT set_config('statement_timeout',%s,true),set_config('lock_timeout','1ms',true)",
            (f"{self.timeout}s",),
        )
        for name in sorted(names):
            await connection.execute(
                sql.SQL("LOCK TABLE public.{} IN ACCESS EXCLUSIVE MODE NOWAIT").format(
                    sql.Identifier(name)
                )
            )

    async def freeze(self, connection):
        """Atomically stop old analytics writers, without scanning source data."""
        import psycopg

        try:
            async with asyncio.timeout(self.timeout), connection.transaction():
                tables = await self.tables(connection)
                triggers = await self._triggers(connection)
                foreign_keys = await self._foreign_keys(connection, tables)
                has_search = await connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='search_query_stats' "
                    "AND column_name='successful_count')"
                )
                search = _values(await has_search.fetchone())[0]
                names = tables | {_values(row)[0] for row in triggers}
                names |= {_values(row)[2] for row in foreign_keys}
                if search:
                    names.add("search_query_stats")
                await self._lock(connection, names)
                for row in triggers:
                    table, trigger, _ = _values(row)
                    await connection.execute(
                        sql.SQL("DROP TRIGGER {} ON public.{}").format(
                            sql.Identifier(trigger), sql.Identifier(table)
                        )
                    )
                for row in foreign_keys:
                    table, constraint, _ = _values(row)
                    await connection.execute(
                        sql.SQL("ALTER TABLE public.{} DROP CONSTRAINT {}").format(
                            sql.Identifier(table), sql.Identifier(constraint)
                        )
                    )
                await connection.execute(
                    "CREATE OR REPLACE FUNCTION public.devfeed_analytics_frozen() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                    "'legacy analytics are being imported into ClickHouse' USING ERRCODE='55000'; "
                    "END $$"
                )
                for table in sorted(tables):
                    await connection.execute(
                        sql.SQL("DROP TRIGGER IF EXISTS {} ON public.{}").format(
                            sql.Identifier(GUARD), sql.Identifier(table)
                        )
                    )
                    await connection.execute(
                        sql.SQL(
                            "CREATE TRIGGER {} BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE "
                            "ON public.{} FOR EACH STATEMENT EXECUTE FUNCTION public.{}()"
                        ).format(
                            sql.Identifier(GUARD), sql.Identifier(table), sql.Identifier(GUARD)
                        )
                    )
                if search:
                    await self._freeze_search(connection)
                return tables
        except (psycopg.errors.LockNotAvailable, TimeoutError) as error:
            raise AnalyticsCutoverBusy("Legacy analytics are waiting for active writers") from error

    async def _freeze_search(self, connection):
        await connection.execute(
            "CREATE OR REPLACE FUNCTION public.devfeed_search_analytics_frozen() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "IF TG_OP='INSERT' THEN IF NEW.successful_count<>0 THEN RAISE EXCEPTION "
            "'legacy search analytics are retired' USING ERRCODE='55000'; END IF; "
            "ELSIF (OLD.successful_count,OLD.first_seen_at,OLD.last_seen_at) IS DISTINCT FROM "
            "(NEW.successful_count,NEW.first_seen_at,NEW.last_seen_at) THEN RAISE EXCEPTION "
            "'legacy search analytics are retired' USING ERRCODE='55000'; END IF; "
            "RETURN NEW; END $$"
        )
        await connection.execute(
            "DROP TRIGGER IF EXISTS devfeed_search_analytics_frozen ON public.search_query_stats"
        )
        await connection.execute(
            "CREATE TRIGGER devfeed_search_analytics_frozen BEFORE INSERT OR UPDATE "
            "ON public.search_query_stats FOR EACH ROW "
            "EXECUTE FUNCTION public.devfeed_search_analytics_frozen()"
        )

    async def frozen(self, connection):
        """Prove guard coverage and absence of old calculation/capture triggers."""
        tables = await self.tables(connection)
        if await self._triggers(connection):
            return False
        guarded = {
            _values(row)[0]
            for row in await _rows(
                connection,
                "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
                "AND NOT t.tgisinternal AND t.tgname=%s AND t.tgenabled='O'",
                (GUARD,),
            )
        }
        search = await _rows(
            connection,
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='search_query_stats' "
            "AND column_name='successful_count') AS has_counts, "
            "EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND c.relname='search_query_stats' AND t.tgname=%s AND t.tgenabled='O') AS guarded",
            (SEARCH_GUARD,),
        )
        has_counts, search_guarded = _values(search[0])
        return tables <= guarded and (not has_counts or search_guarded)

    async def _drop_functions(self, connection):
        functions = await _rows(
            connection,
            "SELECT p.proname,pg_get_function_identity_arguments(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND "
            "(p.proname LIKE 'analytics_capture_%' OR p.proname LIKE 'devfeed_read_model_%' "
            "OR p.proname LIKE 'inference_daily_usage_%' OR p.proname IN "
            "('topic_decision_calls_sync','topic_decision_call_values',"
            "'devfeed_analytics_frozen','devfeed_search_analytics_frozen'))",
        )
        for row in functions:
            name, arguments = _values(row)
            await connection.execute(
                sql.SQL("DROP FUNCTION public.{}({})").format(
                    sql.Identifier(name), sql.SQL(arguments)
                )
            )

    async def cleanup(self, connection):
        """Drop imported analytics only after the caller verifies CH completion."""
        import psycopg

        try:
            async with asyncio.timeout(self.timeout), connection.transaction():
                tables = await self.tables(connection)
                await self._lock(connection, tables | {"search_query_stats"})
                if tables:
                    await connection.execute(
                        sql.SQL("DROP TABLE {}").format(
                            sql.SQL(",").join(
                                sql.Identifier("public", name) for name in sorted(tables)
                            )
                        )
                    )
                await connection.execute(
                    "DROP TRIGGER IF EXISTS devfeed_search_analytics_frozen ON search_query_stats"
                )
                for column in ("successful_count", "first_seen_at", "last_seen_at"):
                    await connection.execute(
                        sql.SQL("ALTER TABLE search_query_stats DROP COLUMN IF EXISTS {}").format(
                            sql.Identifier(column)
                        )
                    )
                await self._drop_functions(connection)
                await connection.execute("DROP SEQUENCE IF EXISTS analytics_export_generation")
                await connection.execute("DROP SEQUENCE IF EXISTS analytics_gauge_revision")
        except (psycopg.errors.LockNotAvailable, TimeoutError) as error:
            raise AnalyticsCutoverBusy(
                "Legacy analytics cleanup is waiting for active readers"
            ) from error
