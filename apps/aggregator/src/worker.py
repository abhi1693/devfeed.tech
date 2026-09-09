import argparse
import logging

from devfeed_core.config import get_settings
from devfeed_core.job_logs import capture_runtime_logs
from devfeed_core.logging import configure_logging, log_context
from devfeed_core.services import OperationConflict
from devfeed_core.version import __version__
from rq.serializers import JSONSerializer
from rq.worker import DequeueStrategy

from devfeed_aggregator.analysis_worker import AnalysisAwareWorker as Worker
from devfeed_aggregator.queue import get_queue

logger = logging.getLogger(__name__)

JOB_FUNCTIONS = {
    "devfeed_aggregator.topic_analysis_tasks.analyze_topic": "topic-analysis",
    "devfeed_aggregator.tasks.ingest": "ingestion",
    "devfeed_aggregator.article_tasks.enrich_article": "article-enrichment",
    "devfeed_aggregator.image_tasks.enrich_image": "images",
    "devfeed_aggregator.source_tasks.enrich_source": "source-enrichment",
    "devfeed_aggregator.analysis_tasks.analyze_article": "analysis",
    "devfeed_notifications.delivery.deliver_notification": "notifications",
}


def job_fields(job):
    return {
        "job_id": job.args[0] if job.args else job.id,
        "rq_job_id": job.id,
        "job_kind": JOB_FUNCTIONS.get(getattr(job, "func_name", "") or ""),
    }


def log_job_exception(job, exc_type, exc_value, tb):
    logger.error(
        "rq_job_failed",
        extra=job_fields(job),
        exc_info=(exc_type, exc_value, tb),
    )
    return True  # Retain RQ's normal failure handling.


def log_work_horse_killed(job, retpid, ret_val, rusage):
    logger.error("rq_work_horse_killed", extra={**job_fields(job), "exit_code": ret_val})


def run(
    *,
    burst: bool = False,
    name: str | None = None,
    max_jobs: int | None = None,
    queue_name: str = "all",
) -> None:
    settings = get_settings()
    configure_logging("worker", settings.log_level, settings.log_format)
    queues = []
    worker_name = name
    try:
        names = (
            ["ingestion"]
            + (["analysis"] if settings.ai_enabled and queue_name == "all" else [])
            + (["notifications"] if settings.notifications_enabled else [])
            if queue_name in {"all", "background"}
            else [queue_name]
        )
        for queue_label in names:
            queues.append(get_queue() if queue_label == "ingestion" else get_queue(queue_label))
        worker = Worker(
            queues,
            connection=queues[0].connection,
            serializer=JSONSerializer,
            name=name,
            exception_handlers=[log_job_exception],
            work_horse_killed_handler=log_work_horse_killed,
        )
        worker_name = worker.name
        with capture_runtime_logs(), log_context(service="worker", worker_name=worker.name):
            logger.info("worker_started", extra={"burst": burst, "max_jobs": max_jobs})
            try:
                worker.work(
                    burst=burst,
                    max_jobs=max_jobs,
                    logging_level=settings.log_level,
                    dequeue_strategy=DequeueStrategy.ROUND_ROBIN,
                )
            except ValueError as exc:
                if str(exc) == f"There exists an active worker named {worker.name!r} already":
                    raise OperationConflict(
                        "An RQ worker with this name is already registered. "
                        "Choose a unique --name or stop the existing worker before retrying."
                    ) from exc
                raise
            finally:
                logger.info("worker_stopped")
    except Exception:
        logger.exception(
            "worker_runtime_failed", extra={"service": "worker", "worker_name": worker_name}
        )
        raise
    finally:
        for queue in queues:
            queue.connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an RQ ingestion worker")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--burst", action="store_true", help="Exit when the queue is empty")
    args = parser.parse_args()
    run(burst=args.burst)
