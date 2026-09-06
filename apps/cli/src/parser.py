import argparse
import uuid
from pathlib import Path

from devfeed_core.source_types import SourceType

from devfeed_cli import articles, editorial, images
from devfeed_cli.commands import (
    cache_clear,
    job_dispatch,
    job_list,
    job_retry,
    job_show,
    migrate,
    run_scheduler,
    run_worker,
    source_add,
    source_enrich,
    source_enrichment_dispatch,
    source_enrichment_jobs,
    source_fetch,
    source_import,
    source_list,
    source_review,
    source_review_history,
    source_show,
    source_update,
    taxonomy_list,
    taxonomy_write,
)
from devfeed_cli.status import snapshot


def bounded_integer(lower: int, upper: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("Must be an integer") from None
        if not lower <= number <= upper:
            raise argparse.ArgumentTypeError(f"Must be between {lower} and {upper}")
        return number

    return parse


def pagination(parser):
    parser.add_argument("--limit", type=bounded_integer(1, 500), default=100)
    parser.add_argument("--offset", type=bounded_integer(0, 2_147_483_647), default=0)


def configure(parser: argparse.ArgumentParser) -> None:
    groups = parser.add_subparsers(dest="command", required=True)
    article_commands = groups.add_parser(
        "articles", help="Maintain article metadata"
    ).add_subparsers(dest="action", required=True)
    editorial.configure(groups, article_commands, bounded_integer)
    languages = article_commands.add_parser(
        "detect-languages",
        help="Detect article languages offline; correct old feed hints in a bounded batch",
    )
    languages.add_argument("--limit", type=bounded_integer(1, 500), default=100)
    languages.add_argument("--after", type=uuid.UUID, help="Resume from the previous next_after ID")
    languages.add_argument("--source-id", type=uuid.UUID)
    languages.add_argument(
        "--dry-run", action="store_true", help="Preview without writing metadata"
    )
    languages.set_defaults(execute=articles.detect_languages)
    for action, handler, help_text in (
        ("enrich", articles.enrich, "Queue original-page lookup for an article ID"),
        ("retry", articles.retry, "Retry a failed article enrichment job ID"),
        ("show", articles.show, "Inspect an article enrichment job ID"),
        ("dispatch", articles.dispatch, "Send a queued article enrichment job ID to RQ now"),
    ):
        item = article_commands.add_parser(action, help=help_text)
        item.add_argument("id", type=uuid.UUID)
        if action in {"enrich", "retry"}:
            item.add_argument(
                "--force", action="store_true", help="Dispatch now; never steal running jobs"
            )
        item.set_defaults(execute=handler)
    article_backfill = article_commands.add_parser(
        "backfill", help="Queue original-page enrichment for never-checked articles"
    )
    article_backfill.add_argument("--limit", type=bounded_integer(1, 500), default=100)
    article_backfill.add_argument("--source-id", type=uuid.UUID)
    article_backfill.add_argument(
        "--dispatch", action="store_true", help="Publish the saved jobs immediately"
    )
    article_backfill.set_defaults(execute=articles.backfill)
    article_listing = article_commands.add_parser(
        "jobs", help="List article enrichment outcomes and failures"
    )
    pagination(article_listing)
    article_listing.add_argument("--article-id", type=uuid.UUID)
    article_listing.add_argument("--status", choices=["queued", "running", "succeeded", "failed"])
    article_listing.set_defaults(execute=articles.jobs)
    cache_commands = groups.add_parser(
        "cache", help="Manage disposable GET response caching (never RQ data)"
    ).add_subparsers(dest="action", required=True)
    cache_commands.add_parser(
        "clear", help="Invalidate this database's API responses without flushing Redis"
    ).set_defaults(execute=cache_clear)
    image_commands = groups.add_parser(
        "images", help="Discover missing article image URLs using RQ"
    ).add_subparsers(dest="action", required=True)
    for action, handler, help_text in (
        ("fetch", images.fetch, "Queue image discovery for an article ID; keep existing images"),
        ("retry", images.retry, "Retry a failed image job ID, preserving its history"),
        ("show", images.show, "Inspect an image job ID"),
        ("dispatch", images.dispatch, "Publish a queued image job ID to RQ immediately"),
    ):
        item = image_commands.add_parser(action, help=help_text)
        item.add_argument("id", type=uuid.UUID)
        if action in {"fetch", "retry"}:
            item.add_argument(
                "--force",
                action="store_true",
                help="Dispatch immediately; never replace images or steal running jobs",
            )
        item.set_defaults(execute=handler)
    backfill = image_commands.add_parser(
        "backfill", help="Queue a bounded batch of missing images that have never been checked"
    )
    backfill.add_argument("--limit", type=bounded_integer(1, 500), default=100)
    backfill.set_defaults(execute=images.backfill)
    listing = image_commands.add_parser("jobs", help="List image lookup jobs")
    pagination(listing)
    listing.add_argument("--article-id", type=uuid.UUID)
    listing.add_argument("--status", choices=["queued", "running", "succeeded", "failed"])
    listing.set_defaults(execute=images.jobs)
    source_group = groups.add_parser(
        "sources", help="Submit, inspect and configure RSS/Atom sources"
    )
    sources = source_group.add_subparsers(dest="action", required=True)
    add = sources.add_parser(
        "add",
        help="Validate a feed, trust new sources and queue work; duplicates keep their review",
        description="Fetch and parse the RSS/Atom feed before saving it or scheduling ingestion.",
    )
    add.add_argument("url")
    add.add_argument("--type", dest="source_type", choices=list(SourceType), required=True)
    add.add_argument(
        "--name", help="Display name (defaults to RSS/Atom title, then hostname if untitled)"
    )
    add.add_argument(
        "--poll-interval", type=bounded_integer(300, 604800), default=1800, metavar="SECONDS"
    )
    add.add_argument("--disabled", action="store_true", help="Create without scheduling ingestion")
    for field in ("description", "website-url", "logo-url", "image-url", "language"):
        add.add_argument(f"--{field}")
    add.add_argument(
        "--submitted-by", help="Optional submitter name; attribution is not verified identity"
    )
    add.add_argument("--submitter-url", help="Optional submitter public profile URL")
    add.set_defaults(execute=source_add)
    batch = sources.add_parser(
        "import",
        help="Validate and submit URLs from a UTF-8 file or stdin (-)",
        description="Fetch and parse every unique feed before saving the batch; one URL per line.",
    )
    batch.add_argument("file")
    batch.add_argument("--type", dest="source_type", choices=list(SourceType), required=True)
    batch.add_argument(
        "--poll-interval", type=bounded_integer(300, 604800), default=1800, metavar="SECONDS"
    )
    batch.set_defaults(execute=source_import)
    listing = sources.add_parser("list", help="List sources, including disabled ones")
    pagination(listing)
    listing.add_argument("--type", dest="source_type", choices=list(SourceType))
    listing.add_argument(
        "--status", dest="approval_status", choices=["pending", "approved", "rejected"]
    )
    enabled = listing.add_mutually_exclusive_group()
    enabled.add_argument("--enabled", dest="enabled", action="store_const", const=True)
    enabled.add_argument("--disabled", dest="enabled", action="store_const", const=False)
    listing.set_defaults(execute=source_list, enabled=None)
    show = sources.add_parser("show", help="Inspect source profile, review and polling state")
    show.add_argument("id", type=uuid.UUID)
    show.set_defaults(execute=source_show)
    update = sources.add_parser("update", help="Change source profile or polling settings")
    update.add_argument("id", type=uuid.UUID)
    update.add_argument("--name", default=argparse.SUPPRESS)
    for field in ("description", "website-url", "logo-url", "image-url", "language"):
        group = update.add_mutually_exclusive_group()
        group.add_argument(f"--{field}", default=argparse.SUPPRESS)
        group.add_argument(
            f"--clear-{field}",
            dest=field.replace("-", "_"),
            action="store_const",
            const=None,
            default=argparse.SUPPRESS,
        )
    update.add_argument(
        "--poll-interval",
        dest="poll_interval_seconds",
        type=bounded_integer(300, 604800),
        default=argparse.SUPPRESS,
    )
    enabled = update.add_mutually_exclusive_group()
    enabled.add_argument("--enable", dest="enabled", action="store_true", default=argparse.SUPPRESS)
    enabled.add_argument(
        "--disable", dest="enabled", action="store_false", default=argparse.SUPPRESS
    )
    update.set_defaults(execute=source_update)
    fetch = sources.add_parser("fetch", help="Queue a refresh, coalescing an existing active job")
    fetch.add_argument("id", type=uuid.UUID)
    fetch.add_argument(
        "--force",
        action="store_true",
        help="Dispatch the queued job to RQ now, bypassing retry/redispatch delays; "
        "never steal a running job",
    )
    fetch.set_defaults(execute=source_fetch)
    for action, decision in (("approve", "approved"), ("reject", "rejected")):
        review = sources.add_parser(action, help=f"Record an explicit {action} decision")
        review.add_argument("id", type=uuid.UUID)
        review.add_argument("--by", help="Optional operator attribution; no account lookup")
        review.add_argument(
            "--reason" if action == "reject" else "--note", dest="note", required=action == "reject"
        )
        review.set_defaults(execute=source_review, decision=decision)
    history = sources.add_parser("review-history", help="Inspect source approval/rejection history")
    history.add_argument("id", type=uuid.UUID)
    pagination(history)
    history.set_defaults(execute=source_review_history)
    enrich = sources.add_parser(
        "enrich",
        help="Queue missing source profile metadata lookup (also works for pending review)",
    )
    enrich.add_argument("id", type=uuid.UUID)
    enrich.add_argument(
        "--force", action="store_true", help="Dispatch the queued enrichment job immediately"
    )
    enrich.set_defaults(execute=source_enrich)
    enrichment_jobs = sources.add_parser(
        "enrichment-jobs", help="Inspect source metadata jobs and failures"
    )
    enrichment_jobs.add_argument("--source-id", type=uuid.UUID)
    enrichment_jobs.add_argument("--status", choices=["queued", "running", "succeeded", "failed"])
    pagination(enrichment_jobs)
    enrichment_jobs.set_defaults(execute=source_enrichment_jobs)
    enrichment_dispatch = sources.add_parser(
        "enrichment-dispatch", help="Dispatch a queued source enrichment job ID"
    )
    enrichment_dispatch.add_argument("id", type=uuid.UUID)
    enrichment_dispatch.set_defaults(execute=source_enrichment_dispatch)

    jobs = groups.add_parser(
        "jobs", help="Inspect ingestion runs and retry failures"
    ).add_subparsers(dest="action", required=True)
    listing = jobs.add_parser("list")
    pagination(listing)
    listing.add_argument("--source-id", type=uuid.UUID)
    listing.add_argument("--status", choices=["queued", "running", "succeeded", "failed"])
    listing.set_defaults(execute=job_list)
    for action, handler, help_text in (
        ("show", job_show, "Inspect a run's status, counters and error"),
        ("retry", job_retry, "Request a new run for a failed job, preserving its history"),
        ("dispatch", job_dispatch, "Dispatch a queued job to RQ now, bypassing queued delays"),
    ):
        item = jobs.add_parser(action, help=help_text)
        item.add_argument("id", type=uuid.UUID)
        item.set_defaults(execute=handler)

    for resource in ("categories", "tags"):
        taxonomy = groups.add_parser(
            resource, help=f"Configure database-managed {resource}"
        ).add_subparsers(dest="action", required=True)
        listing = taxonomy.add_parser("list")
        pagination(listing)
        listing.set_defaults(execute=taxonomy_list)
        for action in ("add", "update"):
            item = taxonomy.add_parser(action)
            if action == "update":
                item.add_argument("id", type=uuid.UUID)
            item.add_argument("--name", required=action == "add", default=argparse.SUPPRESS)
            item.add_argument("--slug", required=action == "add", default=argparse.SUPPRESS)
            identity = item.add_mutually_exclusive_group()
            identity.add_argument("--topic-id", type=uuid.UUID, default=argparse.SUPPRESS)
            identity.add_argument(
                "--clear-topic",
                dest="topic_id",
                action="store_const",
                const=None,
                default=argparse.SUPPRESS,
            )
            if resource == "categories":
                parent = item.add_mutually_exclusive_group()
                parent.add_argument("--parent-id", type=uuid.UUID, default=argparse.SUPPRESS)
                parent.add_argument(
                    "--root",
                    dest="parent_id",
                    action="store_const",
                    const=None,
                    default=argparse.SUPPRESS,
                )
                keywords = item.add_mutually_exclusive_group()
                keywords.add_argument(
                    "--keyword", dest="keywords", action="append", default=argparse.SUPPRESS
                )
                keywords.add_argument(
                    "--clear-keywords",
                    dest="keywords",
                    action="store_const",
                    const=[],
                    default=argparse.SUPPRESS,
                )
            else:
                category = item.add_mutually_exclusive_group()
                category.add_argument("--category-id", type=uuid.UUID, default=argparse.SUPPRESS)
                category.add_argument(
                    "--ungroup",
                    dest="category_id",
                    action="store_const",
                    const=None,
                    default=argparse.SUPPRESS,
                )
                aliases = item.add_mutually_exclusive_group()
                aliases.add_argument(
                    "--alias", dest="aliases", action="append", default=argparse.SUPPRESS
                )
                aliases.add_argument(
                    "--clear-aliases",
                    dest="aliases",
                    action="store_const",
                    const=[],
                    default=argparse.SUPPRESS,
                )
            item.set_defaults(execute=taxonomy_write)

    running = groups.add_parser(
        "worker", help="Run an RQ worker in the foreground (Ctrl+C stops it)"
    )
    running.add_argument("--burst", action="store_true", help="Exit when the queue is empty")
    running.add_argument("--name", help="Optional unique name for this worker")
    running.add_argument("--max-jobs", type=bounded_integer(1, 2_147_483_647))
    running.add_argument("--queue", choices=["ingestion", "analysis"], default="ingestion")
    running.set_defaults(execute=run_worker)
    scheduling = groups.add_parser(
        "scheduler", help="Run the polling scheduler and durable job dispatcher"
    )
    scheduling.add_argument("--once", action="store_true", help="Run one tick and exit")
    scheduling.set_defaults(execute=run_scheduler)
    groups.add_parser(
        "status", help="Check dependencies, counts, queue and worker/scheduler heartbeats"
    ).set_defaults(execute=lambda _: snapshot())
    migrations = groups.add_parser("db", help="Apply or inspect schema migrations").add_subparsers(
        dest="action", required=True
    )
    for action in ("upgrade", "check", "current"):
        item = migrations.add_parser(action)
        item.add_argument(
            "--config",
            type=Path,
            help="Path to alembic.ini (otherwise search current directory and parents)",
        )
        item.set_defaults(execute=migrate)
