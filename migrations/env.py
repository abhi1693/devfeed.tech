import logging

from alembic import context
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.logging import configure_logging, log_context
from devfeed_core.models import Base

settings = get_settings()
configure_logging("migration", settings.log_level, settings.log_format)
logger = logging.getLogger("devfeed_core.migrations")


def prepare_online_upgrade(migration_fn):
    """Prepare large indexes before any transactional upgrade step runs."""

    def migrations(revisions, migration_context):
        steps = tuple(migration_fn(revisions, migration_context))
        if revisions:
            for step in steps:
                info = step.info
                if info.is_upgrade and not info.is_stamp and "0040" in info.up_revision_ids:
                    # The existing Alembic transaction has only read its version.
                    # A separate connection leaves that version and old writers
                    # available throughout the concurrent historical table scans.
                    with (
                        get_engine()
                        .connect()
                        .execution_options(isolation_level="AUTOCOMMIT") as connection
                    ):
                        revision = next(
                            revision
                            for revision in info.up_revisions
                            if revision.revision == "0040"
                        )
                        revision.module.prepare_active_indexes(connection)
        return steps

    return migrations


def run_migration_context():
    if context.is_offline_mode():
        context.configure(
            url=settings.database_url,
            target_metadata=Base.metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        with get_engine().connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            migration_fn = context.get_context().opts["fn"]
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                fn=prepare_online_upgrade(migration_fn),
            )
            with context.begin_transaction():
                context.run_migrations()


with log_context(service="migration"):
    logger.info("migration_context_started")
    try:
        run_migration_context()
    except Exception:
        logger.exception("migration_context_failed")
        raise
    logger.info("migration_context_completed")
