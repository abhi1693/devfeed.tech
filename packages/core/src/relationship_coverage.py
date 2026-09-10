"""Incremental relationship coverage: newer topic revisions research older revisions."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select

from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import (
    RELATIONSHIP_GENERATION,
    Topic,
    TopicAnalysisJob,
    TopicRelationshipScan,
    utcnow,
)
from devfeed_core.services import OperationConflict
from devfeed_core.topic_relationships import (
    RelationshipAnalysisRequest,
    request_relationship_analysis,
    topic_snapshot,
)
from devfeed_core.topics import lock_topics

ACTOR = {
    "subject": "relationship-coverage",
    "issuer": "devfeed",
    "organization_id": "system",
    "name": "Automatic relationship research",
}


def reset_scan(session, topic, scan=None):
    """Caller holds the taxonomy lock; the scan commits or rolls back with the topic."""
    if scan is None:
        scan = TopicRelationshipScan(topic_id=topic.id, topic=topic)
        session.add(scan)
    scan.generation = session.scalar(RELATIONSHIP_GENERATION.next_value())
    scan.topic_snapshot = topic_snapshot(topic)
    scan.after_topic_id = scan.job_id = scan.finished_at = scan.last_error = None
    scan.next_run_at, scan.failures = utcnow(), 0
    return scan


def _completed_job(scan, job, now):
    scan.job_id = None
    if job.status == "failed":
        # The worker already made its bounded immediate attempts. Retry this
        # batch later, without advancing coverage or creating a tight loop.
        _defer(scan, job.error, now)
        return
    scan.next_run_at = now
    if job.outcome not in {"enriched", "no_additions"}:
        return
    if job.input_snapshot["topic"] != scan.topic_snapshot:
        return
    scan.failures, scan.last_error = 0, None
    # A full response may omit more edges. Drain the same batch with its newly
    # proposed edges excluded before moving the cursor forward.
    if len(job.result.get("relationships", [])) == 20 and job.result.get("proposal_ids"):
        return
    scan.after_topic_id = uuid.UUID(job.input_snapshot["coverage"]["through_topic_id"])


def _defer(scan, error, now):
    scan.failures += 1
    scan.last_error = error
    scan.next_run_at = now + timedelta(hours=min(24, 2 ** min(scan.failures - 1, 5)))


def _request_batch(session, topic, peers):
    # Long Unicode identities and excluded edges can fill the byte budget before
    # the count limit. Shrink the batch rather than blocking the scheduler.
    while True:
        try:
            job = request_relationship_analysis(
                session, topic.id, RelationshipAnalysisRequest(), ACTOR, candidate_ids=peers
            )
            return job, peers[-1]
        except OperationConflict as exc:
            if len(peers) == 1 or "within the model limit" not in str(exc):
                raise
            peers = peers[: max(1, len(peers) // 2)]


def schedule_relationship_coverage(factory) -> dict[str, int]:
    counts = {"relationship_jobs_scheduled": 0, "relationship_scans_completed": 0}
    settings = get_settings()
    if not settings.ai_enabled or not settings.auto_research_relationships:
        return counts
    now = utcnow()
    with factory.begin() as session:
        # Serialize scheduling with topic edits and manual requests, without ever
        # locking a job after the taxonomy lock (workers lock jobs first).
        lock_topics(session)
        missing = session.scalars(
            select(Topic)
            .outerjoin(TopicRelationshipScan, TopicRelationshipScan.topic_id == Topic.id)
            .where(Topic.status == "active", TopicRelationshipScan.topic_id.is_(None))
            .order_by(Topic.created_at, Topic.id)
            .limit(settings.automation_batch_size)
        ).all()
        for topic in missing:
            reset_scan(session, topic)
        session.flush()
        completed = session.execute(
            select(TopicRelationshipScan, TopicAnalysisJob)
            .join(TopicAnalysisJob, TopicAnalysisJob.id == TopicRelationshipScan.job_id)
            .where(TopicAnalysisJob.status.in_(["succeeded", "failed"]))
            .order_by(TopicRelationshipScan.next_run_at, TopicRelationshipScan.topic_id)
            .limit(settings.automation_batch_size)
        ).all()
        for scan, job in completed:
            _completed_job(scan, job, now)
        session.flush()
        pending = session.scalar(
            select(func.count())
            .select_from(TopicAnalysisJob)
            .where(
                TopicAnalysisJob.topic_id.is_not(None),
                TopicAnalysisJob.status.in_(["queued", "running"]),
            )
        )
        slots = max(0, settings.relationship_research_max_pending - pending)
        active_job = select(TopicAnalysisJob.id).where(
            TopicAnalysisJob.topic_id == TopicRelationshipScan.topic_id,
            TopicAnalysisJob.status.in_(["queued", "running"]),
        )
        scans = session.scalars(
            select(TopicRelationshipScan)
            .where(
                TopicRelationshipScan.finished_at.is_(None),
                TopicRelationshipScan.job_id.is_(None),
                TopicRelationshipScan.next_run_at <= now,
                ~active_job.exists(),
            )
            .order_by(TopicRelationshipScan.next_run_at, TopicRelationshipScan.topic_id)
            .limit(settings.automation_batch_size)
        ).all()
        for scan in scans:
            topic = session.get(Topic, scan.topic_id)
            if topic.status != "active":
                scan.finished_at = now
                continue
            # Only older revisions are candidates. If a peer changes or appears
            # behind our cursor, its newer scan takes responsibility for this pair.
            statement = (
                select(Topic.id)
                .join(TopicRelationshipScan, TopicRelationshipScan.topic_id == Topic.id)
                .where(Topic.status == "active", TopicRelationshipScan.generation < scan.generation)
                .order_by(Topic.id)
                .limit(settings.relationship_research_batch_size)
            )
            if scan.after_topic_id:
                statement = statement.where(Topic.id > scan.after_topic_id)
            peers = list(session.scalars(statement))
            if not peers:
                scan.finished_at = now
                counts["relationship_scans_completed"] += 1
                continue
            if not slots:
                break
            try:
                job, through = _request_batch(session, topic, peers)
            except OperationConflict as exc:
                _defer(scan, str(exc), now)
                continue
            job.input_snapshot = {
                **job.input_snapshot,
                "coverage": {"generation": scan.generation, "through_topic_id": str(through)},
            }
            job.input_hash = snapshot_hash(job.input_snapshot)
            scan.job_id, scan.next_run_at = job.id, now
            slots -= 1
            counts["relationship_jobs_scheduled"] += 1
    return counts
