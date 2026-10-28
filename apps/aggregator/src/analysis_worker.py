"""Keep AI work in Redis while Codex is unavailable, without pausing other queues."""

import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devfeed_core.ai_capacity import cooldown_key
from devfeed_core.analytics_producer import shutdown as drain_analytics
from devfeed_core.config import get_settings
from devfeed_core.quota_pacing import quota_key, snapshot_pause_reason
from devfeed_core.runtime_files import write_runtime_marker
from devfeed_core.telemetry import current, extract_context, span, start_runtime, stop_runtime
from devfeed_core.worker_queues import AI_QUEUES, ROUTABLE_QUEUES, configured_queue
from opentelemetry.trace import StatusCode
from redis.client import Pipeline
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from rq import Worker
from rq.exceptions import DeserializationError, NoSuchJobError, StopRequested
from rq.job import Job
from rq.queue import Queue
from rq.suspension import WORKERS_SUSPENDED
from rq.worker import WorkerStatus

logger = logging.getLogger(__name__)
CHECK_INTERVAL = 10
POLL_INTERVAL = 1
HEARTBEAT_INTERVAL = 30
SHUTDOWN_CHECK_INTERVAL = 10
ADMISSION_SCAN_SIZE = 100

# Match RQ's LMOVE claim: a delivery is never absent from both transport lists.
# Compare status/origin as well as membership so racing workers cannot claim twice.
CLAIM_DELIVERY = """
if redis.call('HGET', KEYS[3], 'status') ~= 'queued' then return 0 end
if redis.call('HGET', KEYS[3], 'origin') ~= ARGV[2] then return 0 end
if redis.call('LREM', KEYS[1], 1, ARGV[1]) ~= 1 then return 0 end
redis.call('RPUSH', KEYS[2], ARGV[1])
return 1
"""
REMOVE_MISSING_DELIVERY = """
if redis.call('EXISTS', KEYS[2]) == 0 then
    return redis.call('LREM', KEYS[1], 1, ARGV[1])
end
return 0
"""


def job_metadata(job: Job) -> dict[str, Any]:
    metadata = getattr(job, "meta", {})
    return metadata if isinstance(metadata, dict) else {}


def logical_job_queue(job: Job, queue: Queue | None = None) -> str:
    """Keep execution policy independent of the physical transport queue."""
    logical = job_metadata(job).get("devfeed_queue")
    if isinstance(logical, str) and logical in ROUTABLE_QUEUES:
        return logical
    origin = getattr(job, "origin", None) or (queue.name if queue else None)
    owners = [name for name in ROUTABLE_QUEUES if configured_queue(name) == origin]
    if len(owners) == 1:
        return owners[0]
    # Deliveries lacking usable metadata use a conservative handler fallback.
    # Source enrichment can also perform AI assessment, so fail closed for that
    # ambiguous handler on a shared physical queue.
    from devfeed_core.job_definitions import JOB_DEFINITIONS

    try:
        handler = getattr(job, "func_name", None)
    except DeserializationError:
        # Claim malformed deliveries into RQ's intermediate list and let its
        # normal execution/failure handling quarantine them. Raising before
        # claim would leave a poison delivery at the head on every restart.
        return "other"
    if handler == "devfeed_aggregator.partner_tasks.process_evaluation":
        return "partner-evaluation"
    if handler == "devfeed_aggregator.partner_sync_tasks.process_pipeline":
        return "partner-pipeline"
    for definition in JOB_DEFINITIONS.values():
        if definition.handler == handler:
            return (
                "source-analysis" if definition.queue == "source-enrichment" else definition.queue
            )
    return "other"


def job_requires_ai(job: Job, queue: Queue | None = None) -> bool:
    logical = logical_job_queue(job, queue)
    if logical in {"partner-pipeline", "partner-evaluation"}:
        hint = job_metadata(job).get("devfeed_ai")
        # A partner pipeline can carry assessment or transport-only work. Its
        # outbox owns this hint; missing/invalid hints must fail closed.
        return hint if type(hint) is bool else True
    return logical in AI_QUEUES


@dataclass(frozen=True)
class AdmissionSnapshot:
    pending: dict[str, bool]
    reason: str | None = None
    suspended: bool = False


class CodexReadiness:
    def __init__(self) -> None:
        from devfeed_aggregator.codex_client import CodexClient

        self.client = CodexClient(get_settings())
        self.reason: str | None = "not_checked"
        self.next_check = 0.0

    def ready(self, *, pending: bool) -> bool:
        # Back off during outages. A healthy cached result is only sufficient
        # while idle: each attempt to dequeue AI work requires a fresh RPC.
        if time.monotonic() >= self.next_check or (pending and self.reason is None):
            self.reason = self.client.readiness()
            self.next_check = time.monotonic() + CHECK_INTERVAL
        return self.reason is None


class AnalysisAwareWorker(Worker):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # RQ owns this variadic constructor contract; preserve SDK forwarding.
        super().__init__(*args, **kwargs)
        queues = args[0] if args else kwargs.get("queues", [])
        ai_kinds = (*AI_QUEUES, "partner-evaluation", "partner-pipeline")
        self.ai_queue_names = {configured_queue(name) for name in ai_kinds}
        subscriptions = {queue.name for queue in queues}
        self.codex_readiness = CodexReadiness() if subscriptions & self.ai_queue_names else None
        self.requires_job_admission = any(
            self.mixed_capabilities(name, ai_kinds) for name in subscriptions
        )
        self.scan_offsets: dict[str, int] = {}
        self.analysis_paused = False
        self.health_pid = os.getpid()
        self.next_admission_heartbeat = 0.0

    @staticmethod
    def mixed_capabilities(physical: str, ai_kinds: tuple[str, ...]) -> bool:
        owners = {name for name in ROUTABLE_QUEUES if configured_queue(name) == physical}
        return "partner-pipeline" in owners or bool(
            owners & set(ai_kinds) and owners - set(ai_kinds)
        )

    def _shutdown(self) -> None:
        # RQ otherwise raises StopRequested from the first signal handler while
        # idle. Interrupting a Redis MULTI/EXEC response can poison the socket
        # subsequently reused by register_death. Finish the exchange, then stop
        # at our bounded dequeue boundary. RQ still owns second-signal SIGKILL.
        self._stop_requested = True

    def heartbeat(self, timeout: int | None = None, pipeline: Pipeline | None = None) -> object:
        try:
            result: object = super().heartbeat(timeout=timeout, pipeline=pipeline)
        except RedisConnectionError:
            # Stock RQ retries connection errors without consulting the stop
            # flag. Stop only after a failed exchange; a successful final
            # heartbeat may follow a dequeue whose assigned job must drain.
            if self._stop_requested and pipeline is None and self.get_state() != WorkerStatus.BUSY:
                raise StopRequested() from None
            raise
        # Pipeline heartbeats are confirmed by maintain_heartbeats after execute.
        if pipeline is None:
            self.write_health()
            self.next_admission_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL

        return result

    def serialize(self) -> dict[str, object]:
        return {**super().serialize(), "worker_ttl": self.worker_ttl}

    def maintain_heartbeats(self, job: Job) -> None:
        super().maintain_heartbeats(job)
        self.write_health()
        self.next_admission_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL

    def write_health(self) -> None:
        path = os.environ.get("DEVFEED_WORKER_HEALTH_PATH")
        if not path or os.getpid() != self.health_pid:
            return
        ttl = self.dequeue_timeout + 60 if self.get_state() == WorkerStatus.IDLE else 90
        # /proc/uptime uses the suspend-aware boot clock read by the native probe.
        stamp = int(time.clock_gettime(time.CLOCK_BOOTTIME))
        write_runtime_marker(Path(path), f"{self.health_pid} {stamp} {int(ttl)}\n")

    def execute_job(self, job: Job, queue: Queue) -> object:
        runtime = current()
        started = time.monotonic()
        name = logical_job_queue(job, queue)
        if runtime:
            runtime.instruments["worker_active"].set(1)
        try:
            result: object = super().execute_job(job, queue)
            return result
        finally:
            if runtime:
                runtime.instruments["worker_active"].set(0)
                runtime.instruments["executions"].add(1, {"messaging.destination.name": name})
                runtime.instruments["execution_duration"].record(
                    time.monotonic() - started, {"messaging.destination.name": name}
                )

    def perform_job(self, job: Job, queue: Queue) -> bool:
        # RQ calls this in the work horse, after its final fork. Never initialize
        # the native profiler or exporter threads in the forking parent.
        name = logical_job_queue(job, queue)
        telemetry = start_runtime("worker-" + name, serve_metrics=False)
        try:
            with span(
                "process " + name,
                context=extract_context(job_metadata(job).get("devfeed_trace", {})),
                attributes={
                    "messaging.system": "redis",
                    "messaging.destination.name": name,
                    "messaging.operation.name": "process",
                },
            ) as execution_span:
                result = super().perform_job(job, queue)
                if result is False and execution_span is not None:
                    execution_span.set_status(StatusCode.ERROR)
                return result
        finally:
            drain_analytics()
            stop_runtime(telemetry)

    def admission_snapshot(self) -> AdmissionSnapshot:
        """One bounded Redis exchange; only confirmed heartbeats refresh probes."""
        settings = get_settings()
        heartbeat = time.monotonic() >= self.next_admission_heartbeat
        with self.connection.pipeline(transaction=False) as pipe:
            if heartbeat:
                self.heartbeat(pipeline=pipe)
            for queue in self.queues:
                pipe.llen(queue.key)
            pipe.get(quota_key())
            pipe.ttl(cooldown_key())
            pipe.exists(WORKERS_SUSPENDED)
            values = pipe.execute()
        if heartbeat:
            self.write_health()
            self.next_admission_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
        # Admission reads are the final commands; RQ may change its heartbeat prefix.
        values = values[-(len(self.queues) + 3) :]
        pending = {
            queue.name: bool(value) for queue, value in zip(self.queues, values[:-3], strict=True)
        }
        reason = snapshot_pause_reason(values[-3], settings=settings)
        if not reason and values[-2] > 0:
            reason = "provider_capacity_cooldown"
        return AdmissionSnapshot(pending, reason, bool(values[-1]))

    def ai_ready(self, snapshot: AdmissionSnapshot) -> bool:
        readiness = self.codex_readiness
        if readiness is None or not any(snapshot.pending.get(name) for name in self.ai_queue_names):
            return False
        ready = False
        if snapshot.reason:
            readiness.reason = snapshot.reason
        else:
            ready = readiness.ready(pending=True)
        if not ready and not self.analysis_paused:
            logger.warning("analysis_queue_paused", extra={"reason": readiness.reason})
        elif ready and self.analysis_paused:
            logger.info("analysis_queue_resumed")
        self.analysis_paused = not ready
        return ready

    def wait_state(self, state: WorkerStatus) -> None:
        if self.get_state() != state:
            self.set_state(state)
            self.procline("AI analysis paused" if state == WorkerStatus.SUSPENDED else "Listening")
        if self.should_run_maintenance_tasks:
            self.run_maintenance_tasks()

    def dequeue_available(
        self, snapshot: AdmissionSnapshot, *, burst: bool
    ) -> tuple[Job, Queue] | None:
        if snapshot.suspended:
            if burst:
                raise StopRequested()
            self.wait_state(WorkerStatus.SUSPENDED)
            return None
        if self.requires_job_admission:
            return self.dequeue_priority(snapshot)
        ready = self.ai_ready(snapshot)
        if self._stop_requested:
            return None
        ordered = self._ordered_queues
        eligible = [
            queue
            for queue in ordered
            if snapshot.pending.get(queue.name) and (queue.name not in self.ai_queue_names or ready)
        ]
        if not eligible:
            paused = self.analysis_paused and any(
                snapshot.pending.get(name) for name in self.ai_queue_names
            )
            self.wait_state(WorkerStatus.SUSPENDED if paused else WorkerStatus.IDLE)
            return None
        try:
            self._ordered_queues = eligible
            # Every eligible AI delivery has just passed fresh admission. Never
            # block here and consume a later arrival using that old decision.
            result = super().dequeue_job_and_maintain_ttl(None)
        finally:
            self._ordered_queues = ordered
        if result is not None:
            self.reorder_queues(reference_queue=result[1])
        return result

    def dequeue_priority(self, snapshot: AdmissionSnapshot) -> tuple[Job, Queue] | None:
        """Scan bounded pages while leaving paused AI in its original queue order."""
        self.wait_state(WorkerStatus.IDLE)
        ai_blocked = False
        for job, queue in self.priority_candidates(snapshot):
            if self._stop_requested:
                return None
            admitted = self.priority_admission(job, queue, ai_blocked=ai_blocked)
            if admitted is None:
                return None  # Global suspension changed during the scan.
            if not admitted:
                ai_blocked = True
                continue
            if self.claim_priority(job, queue):
                return job, queue
        if ai_blocked:
            self.wait_state(WorkerStatus.SUSPENDED)
        return None

    def priority_candidates(self, snapshot: AdmissionSnapshot) -> Iterator[tuple[Job, Queue]]:
        """Advance across paused prefixes between polls without an unbounded read."""
        for queue in self._ordered_queues:
            if self._stop_requested:
                return
            if not snapshot.pending.get(queue.name):
                self.scan_offsets.pop(queue.name, None)
                continue
            yield from ((job, queue) for job in self.priority_page(queue))

    def priority_page(self, queue: Queue) -> Iterator[Job]:
        offset = self.scan_offsets.get(queue.name, 0)
        identifiers = self.connection.lrange(queue.key, offset, offset + ADMISSION_SCAN_SIZE - 1)
        self.scan_offsets[queue.name] = (
            offset + len(identifiers) if len(identifiers) == ADMISSION_SCAN_SIZE else 0
        )
        for identifier in identifiers:
            if self._stop_requested:
                return
            job_id = identifier.decode() if isinstance(identifier, bytes) else identifier
            try:
                yield self.job_class.fetch(
                    job_id, connection=self.connection, serializer=self.serializer
                )
            except NoSuchJobError:
                self.connection.eval(
                    REMOVE_MISSING_DELIVERY, 2, queue.key, Job.key_for(job_id), job_id
                )

    def priority_admission(self, job: Job, queue: Queue, *, ai_blocked: bool) -> bool | None:
        if not job_requires_ai(job, queue):
            return True
        if ai_blocked:
            return False
        # Recheck quota/cooldown immediately before each prospective AI claim.
        snapshot = self.admission_snapshot()
        if snapshot.suspended:
            return None
        readiness = self.codex_readiness
        ready = not snapshot.reason and readiness is not None and readiness.ready(pending=True)
        reason = snapshot.reason or (readiness.reason if readiness else "codex_unavailable")
        self.record_analysis_admission(ready=ready, reason=reason)
        return ready

    def record_analysis_admission(self, *, ready: bool, reason: str | None) -> None:
        if not ready and not self.analysis_paused:
            logger.warning("analysis_queue_paused", extra={"reason": reason})
        elif ready and self.analysis_paused:
            logger.info("analysis_queue_resumed")
        self.analysis_paused = not ready

    def claim_priority(self, job: Job, queue: Queue) -> bool:
        if self._stop_requested:
            return False
        # Equivalent to RQ's LMOVE transfer into its recoverable intermediate
        # list, but atomically claims one eligible delivery behind a paused prefix.
        claimed = self.connection.eval(
            CLAIM_DELIVERY, 3, queue.key, queue.intermediate_queue.key, job.key, job.id, queue.name
        )
        if not claimed:
            return False
        self.scan_offsets[queue.name] = 0
        job.redis_server_version = self.get_redis_server_version()
        self.reorder_queues(reference_queue=queue)
        return True

    def dequeue_job_and_maintain_ttl(
        self, timeout: int | None, max_idle_time: int | None = None
    ) -> tuple[Job, Queue] | None:
        if self.codex_readiness is None and not self.requires_job_admission:
            return self.dequeue_background(timeout, max_idle_time)
        idle_since = time.monotonic()
        while not self._stop_requested:
            try:
                snapshot = self.admission_snapshot()
            except RedisError:
                # Leave deliveries untouched and let the existing probe expire
                # if transport cannot confirm another successful heartbeat.
                snapshot = None
            if self._stop_requested:
                return None
            result = self.dequeue_available(snapshot, burst=timeout is None) if snapshot else None
            if result is not None:
                return result
            if timeout is None:  # Burst processes available work and exits, even if AI is paused.
                return None
            remaining = (
                max_idle_time - (time.monotonic() - idle_since)
                if max_idle_time is not None
                else POLL_INTERVAL
            )
            if remaining <= 0:
                return None
            time.sleep(min(POLL_INTERVAL, remaining))
        return None

    def dequeue_background(
        self, timeout: int | None, max_idle_time: int | None
    ) -> tuple[Job, Queue] | None:
        """Keep stock RQ blocking transport while checking warm shutdown safely."""
        if timeout is None:
            return super().dequeue_job_and_maintain_ttl(None, max_idle_time)
        idle_since = time.monotonic()
        while not self._stop_requested:
            remaining = (
                max_idle_time - (time.monotonic() - idle_since)
                if max_idle_time is not None
                else SHUTDOWN_CHECK_INTERVAL
            )
            if remaining <= 0:
                return None
            budget = min(SHUTDOWN_CHECK_INTERVAL, remaining)
            try:
                # RQ accepts fractional wait budgets; its annotation restricts seconds to int.
                result = super().dequeue_job_and_maintain_ttl(min(timeout, budget), budget)  # type: ignore[arg-type]
            except StopRequested:
                if not self._stop_requested:
                    raise
                return None
            if result is not None:
                return result
        return None
