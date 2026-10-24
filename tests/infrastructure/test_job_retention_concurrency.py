"""Retention rechecks protection after acquiring a concurrently updated job."""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_core import job_retention
from devfeed_core.models import ResearchVerificationJob, Topic, TopicAnalysisJob, utcnow
from sqlalchemy import event, text

from tests.support.source_readiness import configuration

pytestmark = pytest.mark.integration


def test_verification_admitted_after_selection_snapshot_preserves_inputs(database, monkeypatch):
    now = utcnow() + timedelta(seconds=2)
    monkeypatch.setattr(job_retention, "utcnow", lambda: now)
    monkeypatch.setattr(job_retention, "get_settings", configuration)
    with database.begin() as session:
        topic = Topic(
            name="Concurrent verification",
            slug="concurrent-verification",
            kind="technology",
            status="active",
        )
        session.add(topic)
        session.flush()
        jobs = [
            TopicAnalysisJob(
                topic_id=topic.id,
                status="succeeded",
                input_hash="a" * 64,
                input_snapshot={"evidence": "must remain available for verification"},
                requested_by={},
                prompt_version="historical-research-v1",
                created_at=now - timedelta(days=40) + timedelta(seconds=index),
                finished_at=now - timedelta(days=40) + timedelta(seconds=index),
            )
            for index in range(2)
        ]
        session.add_all(jobs)
        session.flush()
        oldest = jobs[0].id

    # Pause inside the lock statement, after PostgreSQL takes its snapshot but
    # before LockRows obtains the analysis-job lock. The other transaction can
    # then admit verification and commit an updated job. A combined eligibility
    # and FOR UPDATE statement uses its old cross-table snapshot even when
    # EvalPlanQual returns the new job version; a fresh statement must protect it.
    barrier_key = uuid.uuid4().int & ((1 << 63) - 1)
    barrier_ready, query_started = threading.Event(), threading.Event()
    owner = {}
    engine = database.kw["bind"]

    def pause_before_job_lock(connection, _cursor, statement, parameters, _context, _many):
        if (
            not query_started.is_set()
            and "FROM topic_analysis_jobs" in statement
            and "FOR UPDATE" in statement
        ):
            owner["pid"] = connection.connection.driver_connection.info.backend_pid
            # The deliberate test barrier may outlast the normal 100ms lock cap.
            connection.exec_driver_sql("SET LOCAL lock_timeout = '1s'")
            query_started.set()
            statement = statement.replace(
                "\nFROM topic_analysis_jobs",
                f", pg_advisory_xact_lock({barrier_key})\nFROM topic_analysis_jobs",
                1,
            )
        return statement, parameters

    def admit_verification():
        with database.begin() as writer:
            writer.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": barrier_key})
            barrier_ready.set()
            assert query_started.wait(timeout=3), "Retention never reached the job-lock statement"
            deadline = time.monotonic() + 1
            while not writer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted)"
                ),
                {"pid": owner["pid"]},
            ):
                assert time.monotonic() < deadline, "Retention did not reach the snapshot barrier"
                time.sleep(0.005)
            job = writer.get(TopicAnalysisJob, oldest, with_for_update=True)
            job.result = {"verification_policy_version": "new-policy"}
            writer.add(ResearchVerificationJob(id=oldest, relationships=True))

    event.listen(engine, "before_cursor_execute", pause_before_job_lock, retval=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            writer = executor.submit(admit_verification)
            assert barrier_ready.wait(timeout=3), "Verification did not establish the barrier"
            assert job_retention.prune_job_payloads(database) == 0
            writer.result(timeout=3)
    finally:
        event.remove(engine, "before_cursor_execute", pause_before_job_lock)

    with database() as session:
        job = session.get(TopicAnalysisJob, oldest)
        assert job.input_snapshot["evidence"]
        assert job.inputs_pruned_at is None
        assert session.get(ResearchVerificationJob, oldest).status == "queued"
