import argparse
import logging

from devfeed_core.config import get_settings
from devfeed_core.logging import configure_logging, log_context
from devfeed_core.version import __version__
from rq import Worker
from rq.serializers import JSONSerializer

from devfeed_aggregator.queue import get_queue

logger = logging.getLogger(__name__)


def log_job_exception(job, exc_type, exc_value, tb):
    logger.error(
        "rq_job_failed",
        extra={"job_id": job.args[0] if job.args else job.id},
        exc_info=(exc_type, exc_value, tb),
    )
    return True  # Retain RQ's normal failure handling.


def run(
    *,
    burst: bool = False,
    name: str | None = None,
    max_jobs: int | None = None,
    queue_name: str = "ingestion",
) -> None:
    settings = get_settings()
    configure_logging("worker", settings.log_level, settings.log_format)
    queue = None
    worker_name = name
    try:
        queue = get_queue() if queue_name == "ingestion" else get_queue(queue_name)
        worker = Worker(
            [queue],
            connection=queue.connection,
            serializer=JSONSerializer,
            name=name,
            exception_handlers=[log_job_exception],
        )
        worker_name = worker.name
        with log_context(service="worker", worker_name=worker.name):
            logger.info("worker_started", extra={"burst": burst, "max_jobs": max_jobs})
            try:
                worker.work(burst=burst, max_jobs=max_jobs, logging_level=settings.log_level)
            finally:
                logger.info("worker_stopped")
    except Exception:
        logger.exception(
            "worker_runtime_failed", extra={"service": "worker", "worker_name": worker_name}
        )
        raise
    finally:
        if queue is not None:
            queue.connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an RQ ingestion worker")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--burst", action="store_true", help="Exit when the queue is empty")
    args = parser.parse_args()
    run(burst=args.burst)
