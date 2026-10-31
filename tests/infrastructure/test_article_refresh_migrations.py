"""Private maintenance DDL preserves ordinary jobs and repairs real interrupted builds."""

import asyncio
import importlib.util
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from devfeed_core.analytics_cutover import LegacyAnalyticsCutover
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from psycopg.rows import dict_row
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.support.legacy_migrations import database_at_revision
from tests.support.paths import REPO_ROOT

JOB_TABLES = ("article_enrichment_jobs", "article_analysis_jobs", "article_image_jobs")


@pytest.fixture
def index_revision(monkeypatch):
    path = REPO_ROOT / "migrations/versions/0040_article_refreshes.py"
    spec = importlib.util.spec_from_file_location("refresh_index_revision", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    binding = Mock()
    binding.execute.return_value.mappings.return_value.one_or_none.return_value = None
    operation = SimpleNamespace(
        get_context=Mock(return_value=SimpleNamespace(as_sql=False)),
        get_bind=Mock(return_value=binding),
        drop_index=Mock(),
    )
    monkeypatch.setattr(module, "op", operation)
    return module, operation, binding


def matching_index(**changes):
    return {
        "table_name": JOB_TABLES[0],
        "relation_kind": "i",
        "indisvalid": True,
        "indisunique": False,
        "method": "btree",
        "attributes": 1,
        "expressions": False,
        "predicate": "(refresh_id IS NOT NULL)",
        "columns": ["refresh_id"],
        **changes,
    }


@pytest.mark.parametrize("existing", [None, matching_index()])
def test_matching_valid_or_missing_refresh_index_requires_no_destructive_repair(
    index_revision, existing
):
    revision, operation, binding = index_revision
    binding.execute.return_value.mappings.return_value.one_or_none.return_value = existing
    revision._repair_interrupted_index("owned_index", JOB_TABLES[0])
    operation.drop_index.assert_not_called()


def test_matching_invalid_refresh_index_is_dropped_concurrently_for_retry(index_revision):
    revision, operation, binding = index_revision
    binding.execute.return_value.mappings.return_value.one_or_none.return_value = matching_index(
        indisvalid=False
    )
    revision._repair_interrupted_index("owned_index", JOB_TABLES[0])
    operation.drop_index.assert_called_once_with(
        "owned_index", table_name=JOB_TABLES[0], postgresql_concurrently=True, if_exists=True
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"relation_kind": "r"},
        {"table_name": JOB_TABLES[1]},
        {"columns": ["article_id"]},
        {"indisunique": True},
        {"predicate": "(refresh_id IS NULL)"},
        {"predicate": None},
        {"method": "hash"},
        {"attributes": 2},
        {"expressions": True},
    ],
)
def test_refresh_index_name_collision_is_rejected_without_dropping_it(index_revision, changes):
    revision, operation, binding = index_revision
    binding.execute.return_value.mappings.return_value.one_or_none.return_value = matching_index(
        indisvalid=False, **changes
    )
    with pytest.raises(RuntimeError, match="Unexpected definition"):
        revision._repair_interrupted_index("owned_index", JOB_TABLES[0])
    operation.drop_index.assert_not_called()


def test_offline_refresh_index_sql_does_not_query_or_repair_live_relations(index_revision):
    revision, operation, _ = index_revision
    operation.get_context.return_value.as_sql = True
    revision._repair_interrupted_index("owned_index", JOB_TABLES[0])
    operation.get_bind.assert_not_called()
    operation.drop_index.assert_not_called()


@pytest.fixture
def historical_refresh_database(integration_environment, monkeypatch):
    with database_at_revision(monkeypatch, "0039") as database:
        yield database


def prepare_refresh_schema():
    """Simulate the committed schema phase before concurrent index admission."""
    revision = ScriptDirectory.from_config(config()).get_revision("0040").module
    with (
        get_engine().begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        revision._create_schema()


def config():
    return Config(str(REPO_ROOT / "alembic.ini"))


def dsn():
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def seed_ordinary_records(database, counts=(1, 1, 1)):
    """Retained terminal jobs are ordinary data, with nullable future ownership."""
    article = uuid.uuid4()
    with database.begin() as session:
        session.execute(
            text(
                "INSERT INTO articles(id,canonical_url,url_hash,title,summary,content_type,"
                "feed_at,discovered_at,review_status,publication_status,published_to_feed_at,"
                "language,metadata_source_type) VALUES (:id,:url,:hash,'Published article',"
                "'Keep published content','article',now(),now(),'approved','published',now(),"
                "'en','publisher')"
            ),
            {"id": article, "url": f"https://migration.invalid/{article}", "hash": article.hex},
        )
        for table, count in zip(JOB_TABLES, counts, strict=True):
            extra_columns, extra_values = {
                "article_enrichment_jobs": ("changed_fields,result", "ARRAY[]::varchar[],'{}'"),
                "article_analysis_jobs": (
                    "input_snapshot,catalog_snapshot,result",
                    "'{}','{}','{}'",
                ),
                "article_image_jobs": ("operation", "'discover'"),
            }[table]
            # Only this historical test database is owned here. Transactional DDL
            # restores triggers on rollback too; internal FK triggers stay active.
            session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
            session.execute(
                text(
                    f"INSERT INTO {table}(id,article_id,status,attempts,available_at,created_at,"
                    f"finished_at,{extra_columns}) SELECT md5(:prefix||g::text)::uuid,:article,"
                    f"'succeeded',1,now(),now(),now(),{extra_values} "
                    "FROM generate_series(1,:count) g"
                ),
                {"prefix": table, "article": article, "count": count},
            )
            session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
    return article


def ordinary_fingerprints(database):
    with database() as session:
        return {
            table: tuple(
                session.execute(
                    text(
                        "SELECT count(*),md5(string_agg(id::text||':'||xmin::text||':'||"
                        "md5((to_jsonb(source)-'refresh_id')::text),',' ORDER BY id)) "
                        f"FROM {table} source"
                    )
                ).one()
            )
            for table in ("articles", *JOB_TABLES)
        }


def index_state(database):
    with database() as session:
        return {
            row["name"]: dict(row)
            for row in session.execute(
                text(
                    "SELECT c.relname AS name,c.oid,i.indisvalid,pg_relation_size(c.oid) AS bytes,"
                    "pg_get_expr(i.indpred,i.indrelid) AS predicate FROM pg_class c "
                    "JOIN pg_index i ON i.indexrelid=c.oid WHERE c.relname IN "
                    "('ix_article_enrichment_jobs_refresh_id','ix_article_analysis_jobs_refresh_id',"
                    "'ix_article_image_jobs_refresh_id')"
                )
            ).mappings()
        }


def assert_owner_constraints(database, *, validated):
    with database() as session:
        rows = (
            session.execute(
                text(
                    "SELECT conname,convalidated,condeferrable,condeferred,confdeltype "
                    "FROM pg_constraint WHERE conname IN ('fk_article_enrichment_jobs_refresh_id',"
                    "'fk_article_analysis_jobs_refresh_id','fk_article_image_jobs_refresh_id')"
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 3
        assert all(
            row["convalidated"] == validated
            and row["condeferrable"]
            and row["condeferred"]
            and row["confdeltype"] == "a"
            for row in rows
        )


def add_owner(database, article, *, status="active", bind=False):
    owner = uuid.uuid4()
    with database.begin() as session:
        session.execute(
            text(
                "INSERT INTO article_refreshes(id,article_id,target,status,base_revision,"
                "base_hash,payload,requested_by,created_at) VALUES (:id,:article,'analysis',"
                ":status,0,:hash,'{}','{}',now())"
            ),
            {"id": owner, "article": article, "status": status, "hash": "a" * 64},
        )
        if bind:
            for table in JOB_TABLES:
                session.execute(text(f"UPDATE {table} SET refresh_id=:id"), {"id": owner})
            # Real owners also reference their completed stage jobs. Exercise the
            # complete FK cycle rather than only nullable child-to-owner links.
            session.execute(
                text(
                    "UPDATE article_image_jobs SET operation='store' WHERE id=("
                    "SELECT id FROM article_image_jobs ORDER BY id DESC LIMIT 1)"
                )
            )
            for field, table, ordering in (
                ("enrichment_job_id", JOB_TABLES[0], "ASC"),
                ("analysis_job_id", JOB_TABLES[1], "ASC"),
                ("discovery_job_id", JOB_TABLES[2], "ASC"),
                ("storage_job_id", JOB_TABLES[2], "DESC"),
            ):
                session.execute(
                    text(
                        f"UPDATE article_refreshes SET {field}=(SELECT id FROM {table} "
                        f"ORDER BY id {ordering} LIMIT 1) WHERE id=:id"
                    ),
                    {"id": owner},
                )
    return owner


@pytest.mark.integration
def test_refresh_upgrade_preserves_large_ordinary_history_and_post_import_schema(
    historical_refresh_database,
):
    database = historical_refresh_database
    seed_ordinary_records(database, (40_000, 40_000, 20_000))
    before = ordinary_fingerprints(database)
    assert [before[table][0] for table in JOB_TABLES] == [40_000, 40_000, 20_000]
    prepare_refresh_schema()
    assert ordinary_fingerprints(database) == before
    assert_owner_constraints(database, validated=False)
    command.upgrade(config(), "head")
    assert ordinary_fingerprints(database) == before
    assert_owner_constraints(database, validated=True)
    indexes = index_state(database)
    assert len(indexes) == 3
    assert all(
        row["indisvalid"]
        and row["predicate"] == "(refresh_id IS NOT NULL)"
        and row["bytes"] <= 32768
        for row in indexes.values()
    )
    with database() as session:
        assert session.scalar(text("SELECT count(*) FROM article_refreshes")) == 0
        for table in JOB_TABLES:
            assert (
                session.scalar(text(f"SELECT count(*) FROM {table} WHERE refresh_id IS NOT NULL"))
                == 0
            )

    async def post_import():
        async with await psycopg.AsyncConnection.connect(
            dsn(), autocommit=True, row_factory=dict_row
        ) as connection:
            cutover = LegacyAnalyticsCutover()
            await cutover.freeze(connection)
            await cutover.cleanup(connection)
            assert not await cutover.tables(connection)

    asyncio.run(post_import())
    assert ordinary_fingerprints(database) == before
    command.check(config())
    print(
        json.dumps(
            {
                "retained_ordinary_jobs": sum(before[table][0] for table in JOB_TABLES),
                "ordinary_row_xmin_payload_hashes_preserved": True,
                "partial_index_bytes": {name: row["bytes"] for name, row in indexes.items()},
                "fully_validated_deferred_no_action_foreign_keys": 3,
                "post_import_schema_check": "passed",
            }
        )
    )


@pytest.mark.integration
def test_cancelled_concurrent_refresh_index_repairs_without_rebuilding_valid_sibling(
    historical_refresh_database,
):
    database = historical_refresh_database
    seed_ordinary_records(database)
    before = ordinary_fingerprints(database)
    ready, execute = threading.Event(), threading.Event()
    observed = {}
    engine = get_engine()
    second = "ix_article_analysis_jobs_refresh_id"

    def before_create(connection, _cursor, statement, _parameters, _context, _many):
        if "CREATE INDEX CONCURRENTLY" in statement and second in statement:
            observed["pid"] = connection.connection.driver_connection.info.backend_pid
            ready.set()
            assert execute.wait(15), "Test did not admit the concurrent index statement"

    event.listen(engine, "before_cursor_execute", before_create)
    with (
        psycopg.connect(dsn(), autocommit=True, row_factory=dict_row) as observer,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        future = executor.submit(command.upgrade, config(), "head")
        writer = None
        try:
            assert ready.wait(15), "Migration did not reach the second concurrent index"
            writer = psycopg.connect(dsn())
            writer.execute("UPDATE article_analysis_jobs SET attempts=attempts")
            execute.set()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = observer.execute(
                    "SELECT i.indisvalid,a.wait_event_type,p.phase FROM pg_index i "
                    "JOIN pg_class c ON c.oid=i.indexrelid CROSS JOIN pg_stat_activity a "
                    "LEFT JOIN pg_stat_progress_create_index p ON p.pid=a.pid "
                    "WHERE c.relname=%s AND a.pid=%s AND a.datname=current_database()",
                    (second, observed["pid"]),
                ).fetchone()
                if state and not state["indisvalid"] and state["wait_event_type"] == "Lock":
                    break
                time.sleep(0.02)
            else:
                pytest.fail("Concurrent index did not wait on the owned writer")
            assert "waiting for writers" in state["phase"]
            assert observer.execute(
                "SELECT pg_cancel_backend(pid) AS cancelled FROM pg_stat_activity "
                "WHERE pid=%s AND datname=current_database() AND query LIKE %s",
                (observed["pid"], "%CREATE INDEX CONCURRENTLY%"),
            ).fetchone()["cancelled"]
            error = future.exception(timeout=15)
            assert isinstance(error, DBAPIError) and isinstance(
                error.orig, psycopg.errors.QueryCanceled
            )
        finally:
            execute.set()
            if writer is not None:
                writer.rollback()
                writer.close()
            event.remove(engine, "before_cursor_execute", before_create)
    with database() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0039"
    failed = index_state(database)
    first = "ix_article_enrichment_jobs_refresh_id"
    assert failed[first]["indisvalid"] and not failed[second]["indisvalid"]
    assert ordinary_fingerprints(database) == before
    command.upgrade(config(), "head")
    repaired = index_state(database)
    assert len(repaired) == 3 and all(row["indisvalid"] for row in repaired.values())
    assert repaired[first]["oid"] == failed[first]["oid"]
    assert repaired[second]["oid"] != failed[second]["oid"]
    assert ordinary_fingerprints(database) == before
    assert_owner_constraints(database, validated=True)
    print(
        json.dumps(
            {
                "interrupted_concurrent_index_phase": state["phase"],
                "durable_failed_revision": "0039",
                "valid_sibling_oid_preserved": repaired[first]["oid"] == failed[first]["oid"],
                "invalid_index_replaced": repaired[second]["oid"] != failed[second]["oid"],
                "ordinary_row_xmin_payload_hashes_preserved": True,
            }
        )
    )


@pytest.mark.integration
@pytest.mark.parametrize("collision", ["wrong_index", "ordinary_relation"])
def test_refresh_index_name_collision_preserves_the_unexpected_relation(
    historical_refresh_database, collision
):
    database = historical_refresh_database
    seed_ordinary_records(database)
    before = ordinary_fingerprints(database)
    prepare_refresh_schema()
    name = "ix_article_enrichment_jobs_refresh_id"
    with database.begin() as session:
        statement = (
            f"CREATE INDEX {name} ON article_enrichment_jobs(article_id) "
            "WHERE refresh_id IS NOT NULL"
            if collision == "wrong_index"
            else f"CREATE TABLE {name}(id integer)"
        )
        session.execute(text(statement))
        oid = session.scalar(
            text("SELECT :name::regclass::oid".replace(":name::", "CAST(:name AS regclass)::")),
            {"name": name},
        )
    with pytest.raises(RuntimeError, match="Unexpected definition"):
        command.upgrade(config(), "head")
    with database() as session:
        assert session.scalar(text("SELECT CAST(:name AS regclass)::oid"), {"name": name}) == oid
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0039"
    assert ordinary_fingerprints(database) == before


@pytest.mark.integration
def test_unused_refresh_schema_downgrades_without_changing_ordinary_history(
    historical_refresh_database,
):
    database = historical_refresh_database
    seed_ordinary_records(database)
    before = ordinary_fingerprints(database)
    command.upgrade(config(), "head")
    command.downgrade(config(), "0039")
    assert ordinary_fingerprints(database) == before
    with database() as session:
        assert session.scalar(text("SELECT to_regclass('article_refreshes')")) is None
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0039"
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_name IN "
                    "('article_enrichment_jobs','article_analysis_jobs','article_image_jobs') "
                    "AND column_name='refresh_id'"
                )
            )
            == 0
        )


@pytest.mark.integration
def test_refresh_history_refuses_downgrade_and_owner_deletion_but_article_cascade_commits(
    historical_refresh_database,
):
    database = historical_refresh_database
    article = seed_ordinary_records(database, (1, 1, 2))
    command.upgrade(config(), "head")
    owner = add_owner(database, article, status="cancelled", bind=True)
    before = ordinary_fingerprints(database)
    indexes = index_state(database)
    with database() as session:
        original_owner = tuple(
            session.execute(
                text("SELECT xmin::text,to_jsonb(owner) FROM article_refreshes owner")
            ).one()
        )
    with pytest.raises(DBAPIError, match="Cannot discard article maintenance history"):
        command.downgrade(config(), "0039")
    assert index_state(database) == indexes
    assert ordinary_fingerprints(database) == before
    with database() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0040"
        assert session.scalar(text("SELECT count(*) FROM article_refreshes")) == 1
        assert (
            tuple(
                session.execute(
                    text("SELECT xmin::text,to_jsonb(owner) FROM article_refreshes owner")
                ).one()
            )
            == original_owner
        )
    deleted = False
    with pytest.raises(IntegrityError) as failure, database.begin() as session:
        assert (
            session.execute(
                text("DELETE FROM article_refreshes WHERE id=:id"), {"id": owner}
            ).rowcount
            == 1
        )
        deleted = True
    assert deleted and isinstance(failure.value.orig, psycopg.errors.ForeignKeyViolation)
    with database() as session:
        assert (
            tuple(
                session.execute(
                    text("SELECT xmin::text,to_jsonb(owner) FROM article_refreshes owner")
                ).one()
            )
            == original_owner
        )
    with database.begin() as session:
        session.execute(text("DELETE FROM articles WHERE id=:id"), {"id": article})
    with database() as session:
        for table in ("articles", "article_refreshes", *JOB_TABLES):
            assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.integration
def test_refresh_upgrade_rejects_unowned_existing_table(historical_refresh_database):
    database = historical_refresh_database
    with database.begin() as session:
        session.execute(text("CREATE TABLE article_refreshes(id integer)"))
        oid = session.scalar(text("SELECT 'article_refreshes'::regclass::oid"))
    with pytest.raises(RuntimeError, match="Unexpected existing article_refreshes"):
        command.upgrade(config(), "head")
    with database() as session:
        assert session.scalar(text("SELECT 'article_refreshes'::regclass::oid")) == oid
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0039"


@pytest.mark.integration
def test_refresh_schema_phase_failure_rolls_back_all_additions(historical_refresh_database):
    database = historical_refresh_database
    seed_ordinary_records(database)
    before = ordinary_fingerprints(database)
    with database.begin() as session:
        session.execute(text("CREATE TABLE ix_article_refresh_due(id integer)"))
    with pytest.raises(DBAPIError, match="already exists"):
        command.upgrade(config(), "head")
    assert ordinary_fingerprints(database) == before
    with database() as session:
        assert session.scalar(text("SELECT to_regclass('article_refreshes')")) is None
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0039"
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_name IN "
                    "('article_enrichment_jobs','article_analysis_jobs','article_image_jobs') "
                    "AND column_name='refresh_id'"
                )
            )
            == 0
        )
    with database.begin() as session:
        session.execute(text("DROP TABLE ix_article_refresh_due"))
    command.upgrade(config(), "head")
    assert ordinary_fingerprints(database) == before
    assert_owner_constraints(database, validated=True)
