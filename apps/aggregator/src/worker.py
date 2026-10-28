import argparse
import importlib.util
import logging
import os
import uuid
from resource import struct_rusage
from types import TracebackType
from typing import get_args

from devfeed_core.analytics_producer import shutdown as drain_analytics
from devfeed_core.config import get_settings
from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.job_logs import capture_runtime_logs
from devfeed_core.logging import configure_logging, log_context
from devfeed_core.runtime_files import runtime_marker, write_runtime_marker
from devfeed_core.services import OperationConflict
from devfeed_core.telemetry import start_runtime, stop_runtime
from devfeed_core.version import __version__
from devfeed_core.worker_queues import WorkerQueue, configured_queue, physical_queues, worker_queues
from rq.job import Job
from rq.serializers import JSONSerializer
from rq.worker import DequeueStrategy

from devfeed_aggregator.analysis_worker import AnalysisAwareWorker as Worker
from devfeed_aggregator.queue import get_queue

logger = logging.getLogger(__name__)

JOB_FUNCTIONS: dict[str, str] = {
    d.handler: ("topic-analysis" if d.kind == "research-verification" else d.kind)
    for d in JOB_DEFINITIONS.values()
}
JOB_FUNCTIONS["devfeed_aggregator.discovery_tasks.process_candidate"] = "source-discovery"
JOB_FUNCTIONS["devfeed_aggregator.partner_tasks.process_evaluation"] = "partner-evaluation"
JOB_FUNCTIONS["devfeed_aggregator.partner_sync_tasks.process_pipeline"] = "partner-pipeline"


def job_fields(job: Job) -> dict[str, object]:
    return {
        "job_id": job.args[0] if job.args else job.id,
        "rq_job_id": job.id,
        "job_kind": JOB_FUNCTIONS.get(getattr(job, "func_name", "") or ""),
    }


def log_job_exception(
    job: Job, exc_type: type[BaseException], exc_value: BaseException, tb: TracebackType | None
) -> bool:
    logger.error(
        "rq_job_failed",
        extra=job_fields(job),
        exc_info=(exc_type, exc_value, tb),
    )
    return True  # Retain RQ's normal failure handling.


def log_work_horse_killed(job: Job, retpid: int, ret_val: int, rusage: struct_rusage) -> None:
    logger.error("rq_work_horse_killed", extra={**job_fields(job), "exit_code": ret_val})


def run(
    *,
    burst: bool = False,
    name: str | None = None,
    max_jobs: int | None = None,
    queue_name: str = "all",
    legacy_queues: bool = False,
) -> None:
    settings = get_settings()
    solver_selector = queue_name in {"solver", configured_queue("solver", settings)}
    if solver_selector and not settings.solver_services:
        raise OperationConflict("The solver worker requires DEVFEED_SOLVER_SERVICES")
    if settings.solver_services and not solver_selector:
        raise OperationConflict("Only the dedicated solver worker may use solver services")
    configure_logging("worker", settings.log_level, settings.log_format)
    queues = []
    worker_name = name
    telemetry = start_runtime("worker-" + queue_name, profiling=False, tracing=False)
    try:
        names = worker_queues(
            queue_name,
            ai_enabled=settings.ai_enabled,
            notifications_enabled=settings.notifications_enabled,
            settings=settings,
            legacy=legacy_queues,
        )
        if (
            configured_queue("notifications", settings) in names
            and importlib.util.find_spec("devfeed_notifications") is None
        ):
            raise OperationConflict(
                "Notification delivery requires devfeed-aggregator[notifications] "
                "or devfeed-aggregator[all-workers]"
            )
        if (
            settings.image_storage_enabled
            and queue_name in {"all", "background"}
            and "images" in names
        ):
            # R2 credentials belong only to the dedicated Images pipeline worker.
            names.remove("images")
        for queue_label in names:
            if settings.job_queue_mode == "priority" or legacy_queues:
                queues.append(get_queue(queue_label, physical=True))
            else:
                queues.append(get_queue() if queue_label == "ingestion" else get_queue(queue_label))
        worker = Worker(
            queues,
            connection=queues[0].connection,
            serializer=JSONSerializer,
            name=name,
            exception_handlers=[log_job_exception],
            work_horse_killed_handler=log_work_horse_killed,
            # Bound idle heartbeat/probe freshness without polling every second.
            worker_ttl=90,
        )
        worker_name = worker.name
        with capture_runtime_logs(), log_context(service="worker", worker_name=worker.name):
            logger.info("worker_started", extra={"burst": burst, "max_jobs": max_jobs})
            try:
                worker.work(
                    burst=burst,
                    max_jobs=max_jobs,
                    logging_level=settings.log_level,
                    dequeue_strategy=(
                        DequeueStrategy.DEFAULT
                        if settings.job_queue_mode == "priority" and not legacy_queues
                        else DequeueStrategy.ROUND_ROBIN
                    ),
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
        drain_analytics()
        for queue in queues:
            queue.connection.close()
        stop_runtime(telemetry)


def main() -> int:
    """Start a worker without importing unrelated interactive CLI commands."""
    parser = worker_parser("Run an RQ worker")
    parser.add_argument(
        "--queue",
        choices=tuple(dict.fromkeys((*get_args(WorkerQueue), *physical_queues()))),
        default="all",
    )
    args = parser.parse_args()
    return start_queue_worker(args, args.queue)


def queue_worker_main(queue_name: str, description: str) -> int:
    """Shared command-line contract for apps pinned to one durable queue."""
    return start_queue_worker(worker_parser(description).parse_args(), queue_name)


def worker_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--legacy-queues",
        action="store_true",
        default=os.environ.get("DEVFEED_WORKER_LEGACY_QUEUES") == "true",
        help="Drain original per-kind queues during migration",
    )
    parser.add_argument("--burst", action="store_true", help="Exit when the queue is empty")
    parser.add_argument("--name", help="Unique RQ worker name")
    parser.add_argument("--max-jobs", type=int, help="Stop after this many jobs")
    return parser


def start_queue_worker(args: argparse.Namespace, queue_name: str) -> int:
    worker_name = args.name or f"{os.uname().nodename}-{uuid.uuid4().hex}"
    write_runtime_marker(runtime_marker("worker-name"), worker_name)
    options = {"legacy_queues": True} if getattr(args, "legacy_queues", False) else {}
    run(
        burst=args.burst, name=worker_name, max_jobs=args.max_jobs, queue_name=queue_name, **options
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
