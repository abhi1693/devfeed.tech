"""Metadata smoke fixtures contain business state without analytics controls."""

import importlib.util
import uuid

import pytest
from devfeed_core.models import Base
from sqlalchemy import create_engine, text

from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.integration


def test_fleet_metadata_fixture_has_no_postgres_analytics(database, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "fleet_runtime_metadata", REPO_ROOT / "scripts/ci/fleet_contracts/runtime.py"
    )
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    owner = database.kw["bind"]
    schema = "fleet_metadata_" + uuid.uuid4().hex
    with owner.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(owner.url, connect_args={"options": f"-csearch_path={schema},public"})
    fixture = engine.execution_options(schema_translate_map={None: schema})
    monkeypatch.setattr(runtime, "get_engine", lambda: fixture)
    try:
        runtime.metadata_setup()
        runtime.retained()
        with fixture.connect() as connection:
            names = set(
                connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname=:schema"),
                    {"schema": schema},
                )
            )
            assert names == set(Base.metadata.tables) | {"alembic_version"}
            assert not any(name.startswith("analytics_") for name in names)
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
