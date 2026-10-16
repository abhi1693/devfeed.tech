"""Read-only acquisition and activation cohort export."""

from datetime import date
from typing import Annotated

import typer
from devfeed_core.db import session_factory
from devfeed_core.reader_acquisition_report import reader_acquisition_report
from sqlalchemy import text

from devfeed_cli.options import group
from devfeed_cli.runtime import invoke

app = group("Export verified reader acquisition and first-open outcomes.")


def _report(args):
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    with session_factory().begin() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '15s'"))
        return reader_acquisition_report(session, start=start, end=end)


@app.command("acquisition-report")
def acquisition_report(
    ctx: typer.Context,
    start: Annotated[
        str, typer.Option(help="Inclusive UTC signup date, YYYY-MM-DD (window at most 93 days).")
    ],
    end: Annotated[str, typer.Option(help="Exclusive UTC signup date, YYYY-MM-DD.")],
):
    """Group new reader signups by acquisition date/source; show seven-day maturity."""
    invoke(ctx, _report, locals())
