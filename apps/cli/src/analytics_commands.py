"""Explicit ClickHouse schema operations; parsing and help remain offline."""

import asyncio
from dataclasses import asdict

import typer
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_migrations import ClickHouseMigrationRunner, upgrade_analytics
from devfeed_core.config import get_settings

from devfeed_cli.options import group
from devfeed_cli.runtime import invoke

app = group("Apply or inspect versioned ClickHouse schema migrations.")


async def migrate_analytics(action: str) -> dict:
    settings = get_settings()
    if action == "upgrade":
        return asdict(await upgrade_analytics(settings))
    async with AnalyticsClient(settings) as client:
        runner = ClickHouseMigrationRunner(client)
        status = await runner.check() if action == "check" else await runner.current()
        return asdict(status)


@app.command("upgrade", help="Apply pending ClickHouse migrations and prepare the import boundary.")
@app.command("check", help="Fail if the ClickHouse schema differs from this application version.")
@app.command("current", help="Display applied and pending ClickHouse schema versions.")
def migrate(ctx: typer.Context):
    invoke(ctx, lambda args: asyncio.run(migrate_analytics(args.action)), locals())
