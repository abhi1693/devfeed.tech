"""Review-first source discovery. No API or Redis connection required."""

import hashlib
import time
from pathlib import Path
from threading import Event, Thread
from typing import Annotated
from uuid import UUID

import typer
from devfeed_aggregator.discovery_tasks import assess_sample
from devfeed_core import discovery
from devfeed_core.db import session_factory
from devfeed_core.discovery_crawler import CrawlSession
from devfeed_core.discovery_import import MAX_IMPORT_BYTES, parse_import, publisher_hint
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import DiscoverySeed, utcnow
from devfeed_core.urls import validate_public_url
from sqlalchemy import select

from devfeed_cli.commands import InputError
from devfeed_cli.options import Identifier, Limit, Offset, group
from devfeed_cli.runtime import invoke as runtime_invoke


def invoke(ctx, handler, values):
    def execute(args):
        try:
            return handler(args)
        except FeedError as exc:
            raise InputError(
                f"Publisher request failed ({exc.reason}); inspect the URL or retry later."
            ) from exc

    runtime_invoke(ctx, execute, values)


app = group("Discover publishers and review feeds before admission. Outputs JSON.")
seeds = group("Register and sync bounded publisher collections.")
app.add_typer(seeds, name="seeds")


@app.command()
def add(ctx: typer.Context, url: str, name: str | None = None, feed_hint: str | None = None):
    """Queue a publisher homepage; an optional feed is only a discovery hint."""
    invoke(
        ctx,
        lambda a: discovery.import_publishers([publisher_hint(a.url, a.name, a.feed_hint)], "cli"),
        locals(),
    )


def import_file(args):
    with Path(args.file).open("rb") as stream:
        body = stream.read(MAX_IMPORT_BYTES + 1)
    return discovery.import_publishers(
        parse_import(body, args.format), args.origin, checksum=hashlib.sha256(body).hexdigest()
    )


@app.command("import")
def import_candidates(
    ctx: typer.Context, file: str, format: str = "opml", origin: str = "cli-import"
):
    """Import OPML, URLs, JSON or Markdown atomically; preserve provenance."""
    invoke(ctx, import_file, locals())


@app.command("list")
def list_candidates(
    ctx: typer.Context, status: str | None = None, limit: Limit = 50, offset: Offset = 0
):
    invoke(ctx, lambda a: discovery.list_candidates(a.status, a.limit, a.offset), locals())


@app.command()
def show(ctx: typer.Context, id: Identifier):
    """Show candidate, feed evidence, assessments and the last 100 jobs/reviews."""
    invoke(ctx, lambda a: discovery.show(a.id), locals())


class RunProgress:
    """Flush progress to stderr while the main thread performs blocking work."""

    heartbeat_seconds = 10

    def __init__(self, index, limit, quiet=False):
        self.prefix = f"[{index}/{limit}]"
        self.label = "Waiting for the next available job"
        self.quiet = quiet
        self.started_at = time.monotonic()
        self.stop = Event()
        self.thread = Thread(target=self.heartbeat, daemon=True)

    def emit(self, message):
        if not self.quiet:
            typer.echo(f"{self.prefix} {message}", err=True)

    def started(self, info):
        name = "".join(char if char.isprintable() else " " for char in info["name"])[:200]
        self.label = f"{info['stage']}: {name} ({info['candidate_id']}, attempt {info['attempt']})"
        self.emit(f"Started {self.label}")

    def heartbeat(self):
        while not self.stop.wait(self.heartbeat_seconds):
            self.emit(
                f"Still working — {self.label}; {time.monotonic() - self.started_at:.0f}s elapsed"
            )

    def __enter__(self):
        self.emit(self.label)
        if not self.quiet:
            self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join()

    def finished(self, result):
        elapsed = time.monotonic() - self.started_at
        if result is None:
            self.emit(
                "No job claimed: queue is empty, delayed, AI-only, or another runner is active."
            )
            return
        details = result.get("candidate_status") or result["status"]
        if result.get("feeds_found") is not None:
            details += f"; {result['feeds_found']} feed(s) found"
        if result.get("error"):
            details += f"; {result['error']}"
        self.emit(f"Finished: {details} ({elapsed:.1f}s)")


def run_jobs(args):
    if args.allow_ai:
        from devfeed_core.config import get_settings

        if not get_settings().ai_enabled:
            raise InputError("Enable AI before using --allow-ai")
    results = []
    for index in range(1, args.limit + 1):
        with RunProgress(index, args.limit, args.quiet) as progress:
            result = discovery.run_one(
                allow_ai=args.allow_ai, assessor=assess_sample, on_start=progress.started
            )
        progress.finished(result)
        if result is None:
            break
        results.append(result)
    return {"processed": len(results), "jobs": results}


@app.command()
def run(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 10,
    allow_ai: bool = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Suppress progress on stderr; retain JSON output.")
    ] = False,
):
    """Process due jobs, then exit. AI requires explicit --allow-ai. Safe to resume."""
    invoke(ctx, run_jobs, locals())


@app.command()
def assess(ctx: typer.Context, id: Identifier):
    """Queue optional AI quality assessment of the selected feed sample."""
    invoke(ctx, lambda a: discovery.request(a.id, "assess"), locals())


@app.command()
def retry(
    ctx: typer.Context,
    id: Identifier,
    reopen: bool = False,
    by: str | None = None,
    reason: str | None = None,
):
    """Queue discovery again; rejected candidates require an explicit audited reopen."""
    invoke(
        ctx,
        lambda a: discovery.request(a.id, reopen=a.reopen, actor=a.by, reason=a.reason),
        locals(),
    )


@app.command("select")
def select_feed(ctx: typer.Context, id: Identifier, feed_id: UUID):
    """Resolve multiple discovered feeds by explicitly selecting one."""
    invoke(ctx, lambda a: discovery.select_feed(a.id, a.feed_id), locals())


@app.command()
def approve(
    ctx: typer.Context,
    id: Identifier,
    by: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
):
    """Revalidate and admit explicitly; preserve existing source decisions/settings."""
    invoke(ctx, lambda a: discovery.approve(a.id, a.by, a.reason), locals())


@app.command()
def reject(
    ctx: typer.Context,
    id: Identifier,
    by: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
):
    invoke(ctx, lambda a: discovery.reject(a.id, a.by, a.reason), locals())


def seed_add(args):
    from devfeed_core.discovery_import import FORMATS
    from sqlalchemy.dialects.postgresql import insert

    url = validate_public_url(args.url)
    if args.format not in FORMATS:
        raise ValueError("Unsupported import format")
    with session_factory().begin() as session:
        session.execute(
            insert(DiscoverySeed).values(url=url, format=args.format).on_conflict_do_nothing()
        )
        row = session.scalar(select(DiscoverySeed).where(DiscoverySeed.url == url))
        return discovery.record(row)


@seeds.command("add")
def register_seed(ctx: typer.Context, url: str, format: str = "opml"):
    invoke(ctx, seed_add, locals())


def seed_list(args):
    with session_factory()() as session:
        return [
            discovery.record(row)
            for row in session.scalars(
                select(DiscoverySeed)
                .order_by(DiscoverySeed.created_at)
                .limit(args.limit)
                .offset(args.offset)
            )
        ]


@seeds.command("list")
def list_seeds(ctx: typer.Context, limit: Limit = 50, offset: Offset = 0):
    invoke(ctx, seed_list, locals())


def seed_sync(args):
    from devfeed_core.services import RecordNotFound

    with session_factory()() as session:
        row = session.get(DiscoverySeed, args.id)
        if row is None:
            raise RecordNotFound("Discovery seed not found")
        url, format = row.url, row.format
    response = CrawlSession().get(url)
    hints = parse_import(response.body, format)
    checksum = hashlib.sha256(response.body).hexdigest()
    result = discovery.import_publishers(hints, url, seed_id=args.id, checksum=checksum)
    with session_factory().begin() as session:
        row = session.get(DiscoverySeed, args.id)
        assert row is not None
        row.checksum, row.last_checked_at = checksum, utcnow()
    return result


@seeds.command("sync")
def sync_seed(ctx: typer.Context, id: Identifier):
    """Fetch a registered collection with bounded guarded HTTP, then import hints."""
    invoke(ctx, seed_sync, locals())
