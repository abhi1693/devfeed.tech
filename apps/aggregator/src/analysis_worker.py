"""Keep AI work in Redis while Codex is unavailable, without pausing other queues."""

import logging
import time

from devfeed_core.ai_capacity import cooldown_remaining
from devfeed_core.config import get_settings
from redis.exceptions import RedisError
from rq import Worker
from rq.worker import WorkerStatus

from devfeed_aggregator.codex_client import CodexClient

logger = logging.getLogger(__name__)
CHECK_INTERVAL = 10
POLL_INTERVAL = 1
AI_QUEUES = {"analysis", "relationships"}


class CodexReadiness:
    def __init__(self):
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
        self.codex_readiness = CodexReadiness()
        self.analysis_paused = False

    def dequeue_job_and_maintain_ttl(self, timeout, max_idle_time=None):
        analysis = [queue for queue in self.queues if queue.name in AI_QUEUES]
        if not analysis:
            return super().dequeue_job_and_maintain_ttl(timeout, max_idle_time)
        idle_since = time.monotonic()
        while not self._stop_requested:
            self.check_for_suspension(timeout is None)
            pending = {queue.name: queue.count > 0 for queue in analysis}
            ready = self.codex_readiness.ready(pending=any(pending.values()))
            try:
                if cooldown_remaining(self.connection):
                    ready = False
                    self.codex_readiness.reason = "provider_capacity_cooldown"
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
