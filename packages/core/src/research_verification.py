"""Bounded backfill, retry policy and stale-input guards for completed research."""

from copy import deepcopy
from datetime import timedelta

from sqlalchemy import func, select

from devfeed_core.ai_capacity import CAPACITY_ERRORS
from devfeed_core.analysis import fail_analysis, snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import (
    ResearchVerificationJob,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
    utcnow,
)


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
                .where(
                    TopicAnalysisJob.status == "succeeded",
                    TopicAnalysisJob.outcome == "enriched",
                    select(proposal.id).where(linked, proposal.status == "pending").exists(),
                    ~select(ResearchVerificationJob.id)
                    .where(ResearchVerificationJob.id == TopicAnalysisJob.id)
                    .exists(),
                )
                .order_by(TopicAnalysisJob.finished_at, TopicAnalysisJob.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for job in jobs:
                session.add(ResearchVerificationJob(id=job.id, relationships=relationships))
            scheduled += len(jobs)
    return scheduled


def fail_verification(job: ResearchVerificationJob, reason: str, *, retry_after=0):
    fail_analysis(
        job,
        reason,
        retry_after=retry_after,
        retryable=reason
        not in {"unexpected_tool_execution", "unexpected_server_request", "ai_not_configured"},
    )
    if job.status == "queued" and reason not in CAPACITY_ERRORS:
        # Three attempts, exponential backoff, and publisher Retry-After honored.
        attempts = job.attempts - (job.usage or {}).get("capacity_deferrals", 0)
        job.available_at = utcnow() + timedelta(
            seconds=max(300 * 2 ** max(0, attempts - 1), min(86400, retry_after))
        )
