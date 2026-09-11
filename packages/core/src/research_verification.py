"""Bounded backfill, retry policy and stale-input guards for completed research."""

from copy import deepcopy
from datetime import timedelta

from sqlalchemy import func, or_, select

from devfeed_core.ai_capacity import CAPACITY_ERRORS
from devfeed_core.analysis import fail_analysis, snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import VERIFICATION_RETRY
from devfeed_core.models import (
    ResearchVerificationJob,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
    utcnow,
)
from devfeed_core.relationship_verification import VERSION as RELATIONSHIP_VERSION
from devfeed_core.topic_verification import VERSION as TOPIC_VERSION

POLICY_VERSION = f"{TOPIC_VERSION}/{RELATIONSHIP_VERSION}"


def metadata_input_current(job: TopicAnalysisJob, proposal: TopicProposal) -> bool:
    if proposal.status != "pending":
        return False
    current = snapshot_hash(proposal.proposed)
    if recorded := job.result.get("applied_input_hash"):
        return current == recorded
    # Recover old successful runs only if their original draft + recorded patch
    # still describes the entire current proposal. Never bless later human edits.
    evidence = next(
        (
            item
            for item in reversed(proposal.evidence or [])
            if item.get("provider") == "ai_topic_research"
        ),
        {},
    )
    if evidence.get("analysis_id") != str(job.id):
        return False
    expected = deepcopy(job.input_snapshot["topic"])
    for field in evidence.get("fields", []):
        if field not in job.result:
            return False
        expected[field] = deepcopy(job.result[field])
    actual = deepcopy(proposal.proposed)
    # Retrieval timestamps are generated during enrichment, not model facts.
    for draft in (expected, actual):
        for fact in draft.get("facts", []):
            fact.pop("retrieved_at", None)
    if expected != actual:
        return False
    job.result = {**job.result, "applied_input_hash": current}
    return True


def schedule_verification(factory) -> int:
    settings = get_settings()
    if not settings.ai_enabled:
        return 0
    scheduled = 0
    for relationships, enabled in (
        (False, settings.auto_approve_topics),
        (True, settings.auto_approve_topic_relationships),
    ):
        if not enabled:
            continue
        with factory.begin() as session:
            # Serialize admission per lane across scheduler replicas, without
            # blocking workers or holding locks during Redis/network operations.
            if not session.scalar(
                select(func.pg_try_advisory_xact_lock(0x444656, int(relationships)))
            ):
                continue
            pending = (
                session.scalar(
                    select(func.count())
                    .select_from(ResearchVerificationJob)
                    .where(
                        ResearchVerificationJob.relationships.is_(relationships),
                        ResearchVerificationJob.status.in_(["queued", "running"]),
                    )
                )
                or 0
            )
            limit = min(
                settings.automation_batch_size, max(0, (4 if relationships else 50) - pending)
            )
            if not limit:
                continue
            proposal = TopicRelationProposal if relationships else TopicProposal
            linked = (
                (TopicRelationProposal.job_id == TopicAnalysisJob.id)
                if relationships
                else (proposal.id == TopicAnalysisJob.proposal_id)
            )
            jobs = session.scalars(
                select(TopicAnalysisJob)
                .outerjoin(
                    ResearchVerificationJob, ResearchVerificationJob.id == TopicAnalysisJob.id
                )
                .where(
                    TopicAnalysisJob.status == "succeeded",
                    TopicAnalysisJob.outcome == "enriched",
                    select(proposal.id).where(linked, proposal.status == "pending").exists(),
                    or_(
                        ResearchVerificationJob.id.is_(None),
                        ResearchVerificationJob.status.in_(["succeeded", "failed"])
                        & (
                            func.coalesce(
                                TopicAnalysisJob.result["verification_policy_version"].astext, ""
                            )
                            != POLICY_VERSION
                        ),
                    ),
                )
                .order_by(TopicAnalysisJob.finished_at, TopicAnalysisJob.id)
                .limit(limit)
                .with_for_update(of=TopicAnalysisJob, skip_locked=True)
            ).all()
            for job in jobs:
                task = session.get(ResearchVerificationJob, job.id)
                if task is None:
                    session.add(ResearchVerificationJob(id=job.id, relationships=relationships))
                else:
                    # A new verification policy gets one new bounded cycle. Preserve
                    # the previous outcome/usage instead of erasing its audit history.
                    job.result = {
                        **job.result,
                        "verification_cycles": [
                            *job.result.get("verification_cycles", []),
                            {
                                "policy": job.result.get("verification_policy_version"),
                                "status": task.status,
                                "outcome": task.outcome,
                                "attempts": task.attempts,
                                "usage": task.usage,
                                "error": task.error,
                                "finished_at": str(task.finished_at),
                            },
                        ],
                    }
                    task.status, task.attempts, task.usage = "queued", 0, {}
                    task.available_at = utcnow()
                    task.dispatched_at = task.lease_until = task.lease_token = None
                    task.finished_at = task.outcome = task.error = None
                job.result = {**job.result, "verification_policy_version": POLICY_VERSION}
            scheduled += len(jobs)
    return scheduled


def fail_verification(job: ResearchVerificationJob, reason: str, *, retry_after=0):
    fail_analysis(job, reason, retry_after=retry_after)
    if job.status == "queued" and reason not in CAPACITY_ERRORS:
        # Three attempts, exponential backoff, and publisher Retry-After honored.
        attempts = job.attempts - (job.usage or {}).get("capacity_deferrals", 0)
        job.available_at = utcnow() + timedelta(
            seconds=VERIFICATION_RETRY.delay(max(1, attempts), retry_after)
        )
