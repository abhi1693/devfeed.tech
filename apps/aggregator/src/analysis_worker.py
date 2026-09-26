"""Keep AI work in Redis while Codex is unavailable, without pausing other queues."""

import json
import logging
import os
import time
from pathlib import Path

from devfeed_core.ai_capacity import cooldown_remaining
from devfeed_core.config import get_settings
from devfeed_core.quota_pacing import pause_reason
from devfeed_core.telemetry import current, extract_context, span, start_runtime, stop_runtime
from devfeed_core.worker_queues import AI_QUEUES, QUEUES
from opentelemetry.trace import StatusCode
from redis.exceptions import RedisError
from rq import Worker
from rq.worker import WorkerStatus

logger = logging.getLogger(__name__)
CHECK_INTERVAL = 10
POLL_INTERVAL = 1


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

    def heartbeat(self, timeout=None, pipeline=None):
        result = super().heartbeat(timeout=timeout, pipeline=pipeline)
        # Pipeline heartbeats are confirmed by maintain_heartbeats after execute.
        if pipeline is None:
            self.write_health()
        return result

    def serialize(self):
        return {**super().serialize(), "worker_ttl": self.worker_ttl}

    def maintain_heartbeats(self, job):
        super().maintain_heartbeats(job)
        self.write_health()

    def write_health(self):
        path = os.environ.get("DEVFEED_WORKER_HEALTH_PATH")
        if not path or os.getpid() != self.health_pid:
            return
        ttl = self.dequeue_timeout + 60 if self.get_state() == WorkerStatus.IDLE else 90
        target = Path(path)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"pid": self.health_pid, "at": time.monotonic(), "ttl": ttl})
        )
        temporary.replace(target)

    def execute_job(self, job, queue):
        runtime = current()
        started = time.monotonic()
        name = queue.name if queue.name in QUEUES else "other"
        if runtime:
            runtime.metrics.worker_busy.labels(runtime.service).set(1)
        try:
            return super().execute_job(job, queue)
        finally:
            if runtime:
                runtime.metrics.worker_busy.labels(runtime.service).set(0)
                runtime.metrics.executions.labels(runtime.service, name).inc()
                runtime.metrics.execution_duration.labels(runtime.service, name).observe(
                    time.monotonic() - started
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
            stop_runtime(telemetry)

    def dequeue_job_and_maintain_ttl(self, timeout, max_idle_time=None):
        analysis = [queue for queue in self.queues if queue.name in AI_QUEUES]
        if not analysis or self.codex_readiness is None:
            return super().dequeue_job_and_maintain_ttl(timeout, max_idle_time)
        idle_since = time.monotonic()
        while not self._stop_requested:
            self.check_for_suspension(timeout is None)
            pending = {queue.name: queue.count > 0 for queue in analysis}
            ready = False
            try:
                if reason := pause_reason(self.connection):
                    ready = False
                    self.codex_readiness.reason = reason
                elif cooldown_remaining(self.connection):
                    self.codex_readiness.reason = "provider_capacity_cooldown"
                else:
                    ready = self.codex_readiness.ready(pending=any(pending.values()))
            except RedisError:
                ready = False
                self.codex_readiness.reason = "capacity_check_unavailable"
            if self._stop_requested:
                return None
            if not ready and not self.analysis_paused:
                logger.warning(
                    "analysis_queue_paused", extra={"reason": self.codex_readiness.reason}
                )
            elif ready and self.analysis_paused:
                logger.info("analysis_queue_resumed")
            self.analysis_paused = not ready
            ordered = self._ordered_queues
            eligible = [
                queue
                for queue in ordered
                if queue.name not in AI_QUEUES or (ready and pending[queue.name])
            ]
            result = None
            try:
                self._ordered_queues = eligible
                if eligible:
                    # Never block on an analysis queue after a readiness check:
                    # the server could disappear before the next message arrives.
                    result = super().dequeue_job_and_maintain_ttl(None)
                else:
                    self.set_state(WorkerStatus.IDLE if ready else WorkerStatus.SUSPENDED)
                    self.procline(
                        "Listening on analysis"
                        if ready
                        else "AI analysis paused; waiting for Codex"
                    )
                    self.heartbeat()
                    if self.should_run_maintenance_tasks:
                        self.run_maintenance_tasks()
            finally:
                self._ordered_queues = ordered
            if result is not None:
                self.reorder_queues(reference_queue=result[1])
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
