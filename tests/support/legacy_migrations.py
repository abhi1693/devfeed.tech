"""Exercise historical migration round trips outside the native analytics boundary."""

import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine, session_factory
from psycopg import sql
from sqlalchemy.engine import make_url

from tests.support.paths import REPO_ROOT


def _clear_database_cache():
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()


@contextmanager
def database_at_revision(monkeypatch, revision):
    """Own a unique historical database without downgrading any current schema."""
    settings = get_settings()
    source = make_url(settings.database_url)
    if not (source.database or "").endswith("_test"):
        pytest.fail("Historical migration tests require a disposable *_test source database")
    if urlsplit(settings.redis_url).path != "/15":
        pytest.fail("Historical migration tests require disposable Redis database 15")
    name = f"devfeed_legacy_{uuid.uuid4().hex}_test"
    admin = source.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    target = source.set(database=name).render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        _clear_database_cache()
        with monkeypatch.context() as environment:
            environment.setenv("DEVFEED_DATABASE_URL", target)
            try:
                command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), revision)
                yield session_factory()
            finally:
                _clear_database_cache()
    finally:
        # No connections to this owned database may survive a failed migration.
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def migration_database(integration_environment, monkeypatch):
    """Own a current disposable schema for historical migration round trips."""
    with database_at_revision(monkeypatch, "0038") as database:
        yield database
