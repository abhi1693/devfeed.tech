"""Exercise the installed RQ wire format and real SQL aggregates on disposable services."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import (
    ResearchVerificationJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    utcnow,
)
from redis import Redis
from rq import Queue, Worker
from rq.serializers import JSONSerializer
from rq.worker import WorkerStatus

pytestmark = pytest.mark.integration


def test_real_rq_snapshot_and_durable_queue_outcomes(database, admin_client):
    now = utcnow()
    with database.begin() as session:
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="react",
            action="create",
            origin="import",
            source_name="test",
            proposed={"name": "React"},
            created_by={},
        )
        topic = Topic(name="React", slug="react", kind="technology")
        session.add_all([proposal, topic])
        session.flush()
        research = TopicAnalysisJob(
            proposal_id=proposal.id,
            status="succeeded",
            outcome="enriched",
            finished_at=now,
            input_hash="a" * 64,
            input_snapshot={},
            requested_by={},
            prompt_version="test",
        )
        pending = TopicAnalysisJob(
            topic_id=topic.id,
            status="queued",
            created_at=now - timedelta(hours=2),
            available_at=now + timedelta(hours=1),
            input_hash="b" * 64,
            input_snapshot={},
            requested_by={},
            prompt_version="test",
        )
        session.add_all([research, pending])
        session.flush()
        session.add(
            ResearchVerificationJob(
                id=research.id,
                relationships=False,
                status="succeeded",
                outcome="review_required",
            )
        )
        research_id, proposal_id = research.id, proposal.id
    with Redis.from_url(get_settings().redis_url) as redis:
        queue = Queue("analysis", connection=redis, serializer=JSONSerializer)
        rq_job = queue.enqueue(
            "devfeed_aggregator.research_verification_tasks.verify_research", str(research_id)
        )
        rq_job.started_at = now
        rq_job.save()
        worker = Worker(
            [queue, Queue("relationships", connection=redis)],
            connection=redis,
            serializer=JSONSerializer,
            name="ai:host.1",
        )
        worker.register_birth()
        worker.set_state(WorkerStatus.BUSY)
        worker.set_current_job_id(rq_job.id)
        # The retained RQ success counter deliberately differs from database job totals.
        redis.hset(worker.key, "successful_job_count", 50)
        response = admin_client.get("/v1/admin/workers")
        assert response.status_code == 200, response.text
        body = response.json()
        current = body["workers"][0]["current_job"]
        assert current["id"] == str(research_id) and current["proposal_id"] == str(proposal_id)
        assert current["kind"] == "research-verification" and current["target_name"] == "React"
        assert current["elapsed_seconds"] is not None
        queues = {q["name"]: q for q in body["queues"]}
        assert queues["analysis"]["succeeded"] == 0  # Legacy transport only.
        assert queues["topic-analysis"]["succeeded"] == 1
        assert queues["research-verification"]["succeeded"] == 1
        assert queues["research-verification"]["review_required"] == 1
        assert queues["analysis"]["failed"] == 0
        assert queues["analysis"]["dispatched"] == 1
        assert queues["relationships"]["queued"] == 1  # includes not-yet-due work
        assert queues["relationships"]["dispatched"] == 0
        assert queues["relationships"]["registered_workers"] == 1
        assert admin_client.get("/v1/admin/workers/ai:host.1").status_code == 200
        # A worker expiring between requests must disappear, not look idle/healthy.
        redis.delete(worker.key)
        assert admin_client.get("/v1/admin/workers/ai:host.1").status_code == 404
        after = admin_client.get("/v1/admin/workers").json()
        assert after["workers"] == []
        assert all(q["registered_workers"] == 0 for q in after["queues"])
        assert redis.sismember("rq:workers", worker.key)  # dashboard performs no cleanup
