"""Profile bounded dispatch against owned disposable PostgreSQL and Redis only."""

import argparse
import hashlib
import json
import os
import platform
import resource
import statistics
import subprocess
import time
import uuid
from contextlib import chdir
from datetime import timedelta
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ci.services import disposable_services

ROOT = Path(__file__).resolve().parents[1]


def seed(engine, count):
    from devfeed_core.models import IngestionJob, Source, utcnow

    now = utcnow()
    for start in range(0, count, 1000):
        rows = [
            dict(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"dispatch-source-{number}"),
                name=str(number),
                feed_url=f"https://fixture.example/{number}",
                approval_status="approved",
                source_type="publisher",
            )
            for number in range(start, min(start + 1000, count))
        ]
        with engine.begin() as connection:
            connection.execute(Source.__table__.insert(), rows)
            connection.execute(
                IngestionJob.__table__.insert(),
                [
                    dict(
                        id=uuid.uuid5(uuid.NAMESPACE_URL, f"dispatch-job-{number}"),
                        source_id=row["id"],
                        available_at=now - timedelta(days=1) + timedelta(microseconds=number),
                    )
                    for number, row in enumerate(rows, start)
                ],
            )
    return now


def measure(factory, queue, mode, batch, now):
    from devfeed_core.job_dispatch import DispatchLane, dispatch_jobs, dispatch_lanes
    from sqlalchemy import event, text

    engine = factory.kw["bind"]
    # Both targets were created by this invocation. No supplied URL is accepted.
    queue.connection.flushdb()
    with engine.begin() as connection:
        connection.execute(text("UPDATE ingestion_jobs SET dispatched_at=NULL"))
    counts = dict(selects=0, update_batches=0, commits=0)

    def statement(connection, cursor, sql, parameters, context, many):
        command = sql.lstrip().split(None, 1)[0].upper()
        if command == "SELECT":
            counts["selects"] += 1
        elif command == "UPDATE":
            counts["update_batches"] += 1

    def commit(connection):
        counts["commits"] += 1

    event.listen(engine, "before_cursor_execute", statement)
    event.listen(engine, "commit", commit)
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        delivered = (
            dispatch_jobs(factory, queue, batch, now)
            if mode == "direct"
            else dispatch_lanes(factory, queue, batch, now, [DispatchLane("ingestion")])[
                "ingestion"
            ]
        )
        elapsed, consumed = time.perf_counter() - wall, time.process_time() - cpu
        if delivered != batch or queue.count != batch:
            raise RuntimeError("Dispatch did not publish the requested unique delivery count")
        return dict(
            milliseconds=round(elapsed * 1000, 3), python_cpu_ms=round(consumed * 1000, 3), **counts
        )
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        event.remove(engine, "commit", commit)


def profile(args):
    from alembic import command
    from alembic.config import Config
    from devfeed_aggregator.queue import get_queue
    from devfeed_core import analytics_producer
    from devfeed_core.db import get_engine, session_factory

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = get_engine()
    queue = get_queue()
    try:
        # Include real capture reads/normalization; measure dispatch separately
        # from ClickHouse transport and provider/network job execution.
        with patch.object(analytics_producer, "submit", lambda _changes: None):
            now = seed(engine, args.jobs)
            rows = []
            for mode in ("direct", "lanes"):
                for batch in args.batch:
                    samples = [
                        measure(session_factory(), queue, mode, batch, now)
                        for _ in range(args.repetitions)
                    ]
                    row = dict(
                        mode=mode,
                        batch=batch,
                        samples=samples,
                        median_ms=statistics.median(sample["milliseconds"] for sample in samples),
                    )
                    rows.append(row)
                    print(json.dumps(row), flush=True)
            return rows
    finally:
        queue.connection.close()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=25000)
    parser.add_argument("--batch", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/worker-dispatch.json")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 100000 or not 1 <= args.repetitions <= 10:
        parser.error("Require 1..100000 jobs and 1..10 repetitions")
    if not args.batch or any(not 1 <= value <= args.jobs for value in args.batch):
        parser.error("Each batch must be positive and no larger than the fixture")
    output = args.output.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    source = b"".join(
        (ROOT / name).read_bytes()
        for name in ("packages/core/src/job_dispatch.py", "packages/core/src/analytics_producer.py")
    )
    # Ignore repository .env files and inherited application settings.
    with (
        disposable_services() as environment,
        TemporaryDirectory(prefix="devfeed-dispatch-") as directory,
        patch.dict(os.environ, environment | {"OTEL_SDK_DISABLED": "true"}, clear=True),
        chdir(directory),
    ):
        results = profile(args)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    document = dict(
        architecture=platform.machine(),
        revision=revision,
        source_sha256=hashlib.sha256(source).hexdigest(),
        versions={name: version(name) for name in ("sqlalchemy", "psycopg", "redis", "rq")},
        fixture_jobs=args.jobs,
        repetitions=args.repetitions,
        postgres_limits="disposable service defaults; no CPU or memory cap",
        peak_python_process_rss_bytes=rss if platform.system() == "Darwin" else rss * 1024,
        results=results,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n")
    print(f"Saved {output}")


if __name__ == "__main__":
    main()
