"""Short schema handoffs admit locks before mutations and fail closed on contention."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest
from devfeed_core import analytics_cutover as cutover


class MetadataConnection:
    """Return catalog boundary results without simulating database mutations."""

    def __init__(self, results=(), *, failure=None):
        self.results = iter(results)
        self.failure = failure
        self.statements = []
        self.active = False
        self.failed_transaction = False

    @asynccontextmanager
    async def transaction(self):
        assert not self.active
        self.active = True
        try:
            yield
        except BaseException:
            self.failed_transaction = True
            raise
        finally:
            self.active = False

    async def execute(self, statement, parameters=None):
        value = statement if isinstance(statement, str) else statement.as_string()
        self.statements.append((value, parameters))
        if self.failure is not None and value.startswith("LOCK TABLE"):
            raise self.failure
        rows = (
            next(self.results, [])
            if value.startswith("SELECT") and "set_config" not in value
            else []
        )
        return SimpleNamespace(
            fetchall=AsyncMock(return_value=rows),
            fetchone=AsyncMock(return_value=rows[0] if rows else None),
        )


@pytest.mark.parametrize("timeout", [None, True, 0, 11])
def test_cutover_rejects_unbounded_or_invalid_lock_budgets(timeout):
    with pytest.raises(ValueError, match="timeout"):
        cutover.LegacyAnalyticsCutover(timeout=timeout)


@pytest.mark.parametrize("search", [False, True])
def test_freeze_admits_every_sorted_lock_before_disabling_derived_writers(search, monkeypatch):
    async def run():
        service = cutover.LegacyAnalyticsCutover()
        monkeypatch.setattr(service, "tables", AsyncMock(return_value={"article_engagement"}))
        monkeypatch.setattr(
            service,
            "_triggers",
            AsyncMock(
                return_value=[
                    {"table": "articles", "trigger": "read_model_changed", "function": "old_writer"}
                ]
            ),
        )
        monkeypatch.setattr(
            service,
            "_foreign_keys",
            AsyncMock(return_value=[("article_engagement", "article_fk", "articles")]),
        )
        connection = MetadataConnection([[(search,)]])
        assert await service.freeze(connection) == {"article_engagement"}
        statements = [sql for sql, _ in connection.statements]
        locks = [sql for sql in statements if sql.startswith("LOCK TABLE")]
        assert locks == [
            f'LOCK TABLE public."{name}" IN ACCESS EXCLUSIVE MODE NOWAIT'
            for name in sorted(
                {"articles", "article_engagement"} | ({"search_query_stats"} if search else set())
            )
        ]
        assert max(statements.index(sql) for sql in locks) < statements.index(
            'DROP TRIGGER "read_model_changed" ON public."articles"'
        )
        assert 'ALTER TABLE public."article_engagement" DROP CONSTRAINT "article_fk"' in statements
        assert any("BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE" in sql for sql in statements)
        assert (
            any("CREATE TRIGGER devfeed_search_analytics_frozen" in sql for sql in statements)
            is search
        )
        assert not connection.active and not connection.failed_transaction

    asyncio.run(run())


def test_empty_install_has_no_legacy_table_guards_or_business_ddl(monkeypatch):
    async def run():
        service = cutover.LegacyAnalyticsCutover()
        monkeypatch.setattr(service, "tables", AsyncMock(return_value=set()))
        monkeypatch.setattr(service, "_triggers", AsyncMock(return_value=[]))
        monkeypatch.setattr(service, "_foreign_keys", AsyncMock(return_value=[]))
        connection = MetadataConnection([[{"search": False}]])
        assert await service.freeze(connection) == set()
        assert not any(
            sql.startswith(("LOCK TABLE", "DROP TRIGGER", "ALTER TABLE", "CREATE TRIGGER"))
            for sql, _ in connection.statements
        )

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["freeze", "cleanup"])
@pytest.mark.parametrize(
    "failure", [psycopg.errors.LockNotAvailable("writer"), TimeoutError("deadline")]
)
def test_busy_admission_aborts_handoff_without_any_schema_mutation(monkeypatch, operation, failure):
    async def run():
        service = cutover.LegacyAnalyticsCutover()
        monkeypatch.setattr(service, "tables", AsyncMock(return_value={"article_engagement"}))
        monkeypatch.setattr(service, "_triggers", AsyncMock(return_value=[]))
        monkeypatch.setattr(service, "_foreign_keys", AsyncMock(return_value=[]))
        connection = MetadataConnection([[(False,)]], failure=failure)
        with pytest.raises(cutover.AnalyticsCutoverBusy, match="waiting"):
            await getattr(service, operation)(connection)
        assert connection.failed_transaction and not connection.active
        assert not any(
            sql.startswith(("DROP", "ALTER", "CREATE")) for sql, _ in connection.statements
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "guarded,counts,search_guard,expected",
    [
        ([], False, False, False),
        ([("article_engagement",)], False, False, True),
        ([("article_engagement",)], True, False, False),
        ([("article_engagement",)], True, True, True),
    ],
)
def test_guard_proof_requires_every_legacy_relation_and_search_counter_fence(
    monkeypatch, guarded, counts, search_guard, expected
):
    async def run():
        service = cutover.LegacyAnalyticsCutover()
        monkeypatch.setattr(service, "tables", AsyncMock(return_value={"article_engagement"}))
        monkeypatch.setattr(service, "_triggers", AsyncMock(return_value=[]))
        connection = MetadataConnection([guarded, [(counts, search_guard)]])
        assert await service.frozen(connection) is expected

    asyncio.run(run())


def test_old_derived_trigger_prevents_readiness_without_reading_guard_inventory(monkeypatch):
    async def run():
        service = cutover.LegacyAnalyticsCutover()
        monkeypatch.setattr(service, "tables", AsyncMock(return_value=set()))
        monkeypatch.setattr(service, "_triggers", AsyncMock(return_value=[("articles",)]))
        connection = MetadataConnection()
        assert await service.frozen(connection) is False
        assert not connection.statements

    asyncio.run(run())


@pytest.mark.parametrize("tables", [set(), {"article_engagement", "inference_calls"}])
def test_cleanup_removes_only_retired_schema_and_its_guard_functions(monkeypatch, tables):
    async def run():
        service = cutover.LegacyAnalyticsCutover()
        monkeypatch.setattr(service, "tables", AsyncMock(return_value=tables))
        connection = MetadataConnection([[("devfeed_analytics_frozen", "")]])
        await service.cleanup(connection)
        statements = [sql for sql, _ in connection.statements]
        assert any(sql.startswith("DROP TABLE") for sql in statements) is bool(tables)
        assert 'DROP FUNCTION public."devfeed_analytics_frozen"()' in statements
        assert not any('DROP TABLE "public"."articles"' in sql for sql in statements)
        assert {sql for sql in statements if sql.startswith("DROP SEQUENCE")} == {
            "DROP SEQUENCE IF EXISTS analytics_export_generation",
            "DROP SEQUENCE IF EXISTS analytics_gauge_revision",
        }

    asyncio.run(run())


def test_catalog_queries_bind_explicit_source_allowlists():
    async def run():
        service = cutover.LegacyAnalyticsCutover()
        connection = MetadataConnection(
            [
                [{"relation": "article_engagement"}],
                [],
                [],
            ]
        )
        assert await service.tables(connection) == {"article_engagement"}
        assert await service._triggers(connection) == []
        assert await service._foreign_keys(connection, {"article_engagement"}) == []
        assert connection.statements[0][1] == (sorted(cutover.LEGACY_TABLES),)
        assert connection.statements[1][1] == (
            sorted(cutover.TRIGGERS),
            sorted(cutover.CAPTURE_SOURCES),
        )
        assert connection.statements[2][1] == (["article_engagement"],)

    asyncio.run(run())
