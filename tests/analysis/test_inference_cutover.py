"""Exercise migration verification, atomic admission and terminal replay on PostgreSQL."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from devfeed_core.config import get_settings
from devfeed_core.inference_history_import import import_history
from devfeed_core.inference_ledger import InferenceLedger
from devfeed_core.inference_observation import observation


@pytest.mark.integration
def test_cutover_preserves_rolling_budget_and_serializes_workers(integration_environment):
    async def run():
        ledger = InferenceLedger(get_settings())
        async with await ledger.connect() as conn:
            await conn.execute("TRUNCATE inference_usage_ledger, inference_quota_cutover")
        start = datetime.now(UTC) - timedelta(minutes=2)

        def call():
            return {
                "id": str(uuid.uuid4()),
                "started_at": start,
                "operation": "relationship_research",
                "status": "running",
            }

        historical = call()
        source = AsyncMock()
        source.query.side_effect = [
            [],
            [{"complete": 1}],
            [observation(historical)],
            [{"complete": 1}],
            [observation(historical)],
        ]
        with pytest.raises(RuntimeError, match="incomplete"):
            await import_history(source, ledger)
        assert not await ledger.admit(call(), 2)
        assert await import_history(source, ledger) == 1
        assert await import_history(source, ledger) == 1
        assert not await ledger.admit(historical, 2)
        assert sum(await asyncio.gather(*(ledger.admit(call(), 2) for _ in range(8)))) == 1
        await ledger.write(historical | {"status": "returned", "tokens": {"inputTokens": 50}})
        await ledger.write(historical)
        async with await ledger.connect() as conn:
            cursor = await conn.execute(
                "SELECT revision,usage FROM inference_usage_ledger WHERE id=%s", (historical["id"],)
            )
            saved = await cursor.fetchone()
        assert saved["revision"] == 1 and saved["usage"]["input_tokens"] == 50
        assert not await ledger.admit(call(), 2)

    asyncio.run(run())


@pytest.mark.integration
def test_pruning_removes_only_old_observations_in_bounded_batches(integration_environment):
    from devfeed_core.db import get_engine, session_factory
    from devfeed_core.inference_ledger import prune_inference_history
    from sqlalchemy import text

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE inference_usage_ledger"))
        conn.execute(
            text("""INSERT INTO inference_usage_ledger
            SELECT now() - interval '31 days', gen_random_uuid(), 'test', 0, '{}'
            FROM generate_series(1,1001)""")
        )
        conn.execute(
            text("""INSERT INTO inference_usage_ledger
            VALUES (now(), gen_random_uuid(), 'relationship_research', 0, '{}')""")
        )
    prune_inference_history(session_factory())
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT count(*) FROM inference_usage_ledger")) == 2
    prune_inference_history(session_factory())
    with engine.begin() as conn:
        assert (
            conn.scalar(text("SELECT task FROM inference_usage_ledger")) == "relationship_research"
        )


@pytest.mark.integration
@pytest.mark.parametrize(("revision", "expected_markers"), [("head", 1), ("0041", 0)])
def test_only_fresh_migration_opens_budget_without_import(
    integration_environment, monkeypatch, revision, expected_markers
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from tests.support.legacy_migrations import database_at_revision
    from tests.support.paths import REPO_ROOT

    with database_at_revision(monkeypatch, revision) as factory:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(config, "head")
        with factory.begin() as session:
            assert (
                session.scalar(text("SELECT count(*) FROM inference_quota_cutover"))
                == expected_markers
            )
