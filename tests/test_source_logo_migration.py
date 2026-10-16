"""Publisher logo upgrades preserve job ownership and safe rollback ordering."""

import importlib
import io

from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration_sql(direction):
    migration = importlib.import_module("migrations.versions.0023_managed_source_logos")
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        getattr(migration, direction)()
    return output.getvalue()


def test_source_logo_upgrade_enforces_one_active_job_per_source():
    sql = migration_sql("upgrade")
    assert "managed_logo JSONB DEFAULT '{}' NOT NULL" in sql
    assert "FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE CASCADE" in sql
    assert "CREATE UNIQUE INDEX uq_source_image_active" in sql
    assert "WHERE status IN ('queued','running')" in sql
    assert "source_id IS NOT NULL AND operation IN ('source-logo','source-logo-refresh')" in sql
    assert "article_id IS NOT NULL AND topic_id IS NULL AND source_id IS NULL" in sql


def test_source_logo_rollback_removes_only_source_jobs_before_restoring_old_constraint():
    sql = migration_sql("downgrade")
    delete = "DELETE FROM article_image_jobs WHERE source_id IS NOT NULL"
    constraint = "ADD CONSTRAINT ck_image_job_subject CHECK"
    assert sql.index(delete) < sql.index(constraint) < sql.index("DROP COLUMN source_id")
    assert "DROP INDEX uq_source_image_active" in sql
    assert "DROP INDEX ix_article_image_jobs_source_id" in sql
    assert "ALTER TABLE sources DROP COLUMN managed_logo" in sql
    assert "article_id IS NULL AND topic_id IS NOT NULL AND operation = 'topic-logo'" in sql
