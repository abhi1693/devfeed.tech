import logging

from alembic import context
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.logging import configure_logging, log_context
from devfeed_core.models import Base

settings = get_settings()
configure_logging("migration", settings.log_level, settings.log_format)
logger = logging.getLogger("devfeed_core.migrations")


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
