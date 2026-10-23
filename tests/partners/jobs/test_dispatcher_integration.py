"""The independent refill loop progresses while the main scheduler is idle."""

import time
from unittest.mock import Mock

import pytest
from devfeed_aggregator import partner_sync_tasks
from devfeed_aggregator.partner_dispatcher import partner_dispatcher
from devfeed_aggregator.queue import get_queue
from devfeed_core.config import get_settings

from tests.partners.jobs.test_pipeline_dispatch import published_count, seed_jobs

pytestmark = pytest.mark.integration


def wait_for_publications(database, expected):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if published_count(database) == expected:
            return
        time.sleep(0.01)
    pytest.fail(f"Independent partner dispatcher did not publish {expected} jobs")


def test_refill_progresses_without_a_main_scheduler_cycle(database, monkeypatch):
    seed_jobs(database, 600)
    batch = get_settings().scheduler_batch_size
    schedule = Mock(side_effect=AssertionError("Fast refill must not schedule source imports"))
    monkeypatch.setattr(partner_sync_tasks, "schedule_partner_syncs", schedule)
    queue = get_queue("ingestion")
    try:
        with partner_dispatcher(database, interval=0.01):
            wait_for_publications(database, 2 * batch)
            assert queue.count == 2 * batch
            for _ in range(batch):
                assert queue.connection.lpop(queue.key) is not None
            wait_for_publications(database, 3 * batch)
            assert queue.count == 2 * batch
    finally:
        queue.connection.close()
    schedule.assert_not_called()
