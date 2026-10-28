"""Keep AI work in Redis while Codex is unavailable, without pausing other queues."""

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from devfeed_core.ai_capacity import cooldown_key
from devfeed_core.analytics_producer import shutdown as drain_analytics
from devfeed_core.config import get_settings
from devfeed_core.quota_pacing import quota_key, snapshot_pause_reason
from devfeed_core.runtime_files import write_runtime_marker
from devfeed_core.telemetry import current, extract_context, span, start_runtime, stop_runtime
from devfeed_core.worker_queues import AI_QUEUES, QUEUES
from opentelemetry.trace import StatusCode
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from rq import Worker
from rq.exceptions import StopRequested
from rq.suspension import WORKERS_SUSPENDED
from rq.worker import WorkerStatus

logger = logging.getLogger(__name__)
CHECK_INTERVAL = 10
POLL_INTERVAL = 1
HEARTBEAT_INTERVAL = 30
SHUTDOWN_CHECK_INTERVAL = 10


@dataclass(frozen=True)
class AdmissionSnapshot:
    pending: dict[str, bool]
    reason: str | None = None
    suspended: bool = False


class CodexReadiness:
    def __init__(self):
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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queues = args[0] if args else kwargs.get("queues", [])
        self.codex_readiness = (
            CodexReadiness() if any(queue.name in AI_QUEUES for queue in queues) else None
        )
        self.analysis_paused = False
        self.health_pid = os.getpid()
        self.next_admission_heartbeat = 0.0

    def _shutdown(self):
        # RQ otherwise raises StopRequested from the first signal handler while
        # idle. Interrupting a Redis MULTI/EXEC response can poison the socket
        # subsequently reused by register_death. Finish the exchange, then stop
        # at our bounded dequeue boundary. RQ still owns second-signal SIGKILL.
        self._stop_requested = True

    def heartbeat(self, timeout=None, pipeline=None):
        try:
            result = super().heartbeat(timeout=timeout, pipeline=pipeline)
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

    def serialize(self):
        return {**super().serialize(), "worker_ttl": self.worker_ttl}

    def maintain_heartbeats(self, job):
        super().maintain_heartbeats(job)
        self.write_health()
        self.next_admission_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL

    def write_health(self):
        path = os.environ.get("DEVFEED_WORKER_HEALTH_PATH")
        if not path or os.getpid() != self.health_pid:
            return
        ttl = self.dequeue_timeout + 60 if self.get_state() == WorkerStatus.IDLE else 90
        # /proc/uptime uses the suspend-aware boot clock read by the native probe.
        stamp = int(time.clock_gettime(time.CLOCK_BOOTTIME))
        write_runtime_marker(Path(path), f"{self.health_pid} {stamp} {int(ttl)}\n")

    def execute_job(self, job, queue):
        runtime = current()
        started = time.monotonic()
        name = queue.name if queue.name in QUEUES else "other"
        if runtime:
            runtime.instruments["worker_active"].set(1)
        try:
            return super().execute_job(job, queue)
        finally:
            if runtime:
                runtime.instruments["worker_active"].set(0)
                runtime.instruments["executions"].add(1, {"messaging.destination.name": name})
                runtime.instruments["execution_duration"].record(
                    time.monotonic() - started, {"messaging.destination.name": name}
                )

    def perform_job(self, job, queue):
        # RQ calls this in the work horse, after its final fork. Never initialize
        # the native profiler or exporter threads in the forking parent.
        name = queue.name if queue.name in QUEUES else "other"
        telemetry = start_runtime("worker-" + name, serve_metrics=False)
        try:
            with span(
                "process " + name,
                context=extract_context(job.meta.get("devfeed_trace", {})),
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

    def admission_snapshot(self):
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

    def ai_ready(self, snapshot):
        readiness = self.codex_readiness
        if readiness is None or not any(snapshot.pending.get(name) for name in AI_QUEUES):
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

    def wait_state(self, state):
        if self.get_state() != state:
            self.set_state(state)
            self.procline("AI analysis paused" if state == WorkerStatus.SUSPENDED else "Listening")
        if self.should_run_maintenance_tasks:
            self.run_maintenance_tasks()

    def dequeue_available(self, snapshot, *, burst):
        if snapshot.suspended:
            if burst:
                raise StopRequested()
            self.wait_state(WorkerStatus.SUSPENDED)
            return None
        ready = self.ai_ready(snapshot)
        if self._stop_requested:
            return None
        ordered = self._ordered_queues
        eligible = [
            queue
            for queue in ordered
            if snapshot.pending.get(queue.name) and (queue.name not in AI_QUEUES or ready)
        ]
        if not eligible:
            paused = self.analysis_paused and any(snapshot.pending.get(name) for name in AI_QUEUES)
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

    def dequeue_job_and_maintain_ttl(self, timeout, max_idle_time=None):
        if self.codex_readiness is None:
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

    def dequeue_background(self, timeout, max_idle_time):
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
                result = super().dequeue_job_and_maintain_ttl(min(timeout, budget), budget)
            except StopRequested:
                if not self._stop_requested:
                    raise
                return None
            if result is not None:
                return result
        return None
