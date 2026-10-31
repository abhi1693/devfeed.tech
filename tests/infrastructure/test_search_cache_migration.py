"""Search cache provenance preserves retained and raw-SQL event authority."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from tests.support.legacy_migrations import database_at_revision
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.integration


def test_search_cache_upgrade_keeps_retained_unknown_events_conservative(
    integration_environment, monkeypatch
):
    with database_at_revision(monkeypatch, "0040") as factory:
        with factory.begin() as session:
            session.execute(
                text(
                    "INSERT INTO search_events(kind,entity_id) SELECT 'articles',"
                    "md5(g::text)::uuid FROM generate_series(1,10000) g"
                )
            )
            before = session.scalar(
                text(
                    "SELECT md5(string_agg(id::text||':'||xmin::text,',' ORDER BY id)) "
                    "FROM search_events"
                )
            )
            heap_before = session.execute(
                text(
                    "SELECT pg_relation_filenode('search_events'),pg_relation_size('search_events')"
                )
            ).one()
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "0041")
        with factory() as session:
            assert (
                session.execute(
                    text(
                        "SELECT pg_relation_filenode('search_events'),"
                        "pg_relation_size('search_events')"
                    )
                ).one()
                == heap_before
            )
            assert session.scalar(
                text(
                    "SELECT atthasmissing FROM pg_attribute "
                    "WHERE attrelid='search_events'::regclass AND attname='public_change'"
                )
            )
            assert (
                session.scalar(text("SELECT count(*) FROM search_events WHERE public_change"))
                == 10000
            )
            assert (
                session.scalar(
                    text(
                        "SELECT md5(string_agg(id::text||':'||xmin::text,',' ORDER BY id)) "
                        "FROM search_events"
                    )
                )
                == before
            )
        command.downgrade(Config(str(REPO_ROOT / "alembic.ini")), "0040")
        with factory() as session:
            assert session.scalar(text("SELECT count(*) FROM search_events")) == 10000
