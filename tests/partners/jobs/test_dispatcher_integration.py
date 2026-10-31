"""The independent refill loop progresses while the main scheduler is idle."""

import time
from unittest.mock import Mock

import pytest
from devfeed_aggregator import partner_sync_tasks
from devfeed_aggregator.partner_dispatcher import partner_dispatcher
from devfeed_aggregator.queue import get_queue
from devfeed_aggregator.scheduler_resources import SchedulerResources
from devfeed_core.config import get_settings

from tests.partners.jobs.test_pipeline_dispatch import published_count, seed_jobs

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("priority_queue_routes")]


def wait_for_publications(database, expected):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if published_count(database) == expected:
            return
        time.sleep(0.01)
    pytest.fail(f"Independent partner dispatcher did not publish {expected} jobs")


@pytest.mark.parametrize("reuse", [False, True])
def test_refill_progresses_without_a_main_scheduler_cycle(database, monkeypatch, reuse):
    seed_jobs(database, 600)
    batch = get_settings().scheduler_batch_size
    schedule = Mock(side_effect=AssertionError("Fast refill must not schedule source imports"))
    monkeypatch.setattr(partner_sync_tasks, "schedule_partner_syncs", schedule)
    queue = get_queue("partner-pipeline")
    try:
        settings = get_settings().model_copy(
            update={
                "database_pool_enabled": False,
                "scheduler_connection_reuse_enabled": reuse,
            }
        )
        with (
            SchedulerResources(settings) as resources,
            partner_dispatcher(resources.factory, interval=0.01),
        ):
            wait_for_publications(database, 2 * batch)
            assert queue.count == 2 * batch
            for _ in range(batch):
                assert queue.connection.lpop(queue.key) is not None
            wait_for_publications(database, 3 * batch)
            assert queue.count == 2 * batch
    finally:
        queue.connection.close()
    schedule.assert_not_called()


def test_idle_partner_refill_uses_no_redis_and_one_database_probe(database):
    from devfeed_aggregator.partner_outbox import PartnerOutbox
    from devfeed_core.models import PartnerPipelineJob, utcnow
    from sqlalchemy import event, select

    with database() as session:
        engine = session.get_bind()
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    lookup = Mock(side_effect=AssertionError("An empty lane must not open Redis"))
    try:
        outbox = PartnerOutbox(database, lookup, batch_size=25, now=utcnow())
        assert (
            outbox.publish(
                "ingestion",
                select(PartnerPipelineJob).where(PartnerPipelineJob.status == "queued"),
                model=PartnerPipelineJob,
                handler="devfeed_aggregator.partner_sync_tasks.process_pipeline",
                delivery_id=lambda job: str(job.id),
                event="partner_pipeline_dispatched",
                fields=lambda job: {"job_id": str(job.id)},
            )
            == 0
        )
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len(statements) == 1 and "EXISTS" in statements[0]
    assert "pg_try_advisory" not in statements[0]
    lookup.assert_not_called()
