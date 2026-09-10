"""Recheck completed research without repeating enrichment or losing review decisions."""

import logging
import time
import uuid
from datetime import timedelta

from devfeed_core.ai_capacity import CAPACITY_ERRORS, safe_pause
from devfeed_core.analysis import finish_analysis
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.job_logs import job_log_context
from devfeed_core.models import (
    ResearchVerificationJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
    utcnow,
)
from devfeed_core.relationship_verification import (
    VERSION,
    RelationshipVerificationResult,
    checked_verdicts,
    verification_input,
    verification_prompt,
)
from devfeed_core.research_evidence import (
    VERIFICATION_VERSION,
    citation_key,
    citation_retryable,
    citation_verified,
    verify_citations,
)
from devfeed_core.research_verification import fail_verification, metadata_input_current
from devfeed_core.topic_analysis import queue_relationships_after_enrichment
from devfeed_core.topic_auto_approval import auto_approve_research
from devfeed_core.topic_relationships import approval_blocker
from devfeed_core.topics import lock_topics
from sqlalchemy import select

from devfeed_aggregator.analysis_telemetry import record_attempt
from devfeed_aggregator.codex_client import AnalysisError, CodexClient

logger = logging.getLogger(__name__)


def _locked(session, model, identifier):
    return session.scalar(select(model).where(model.id == identifier).with_for_update())


def verify_research(job_id: str):
    with job_log_context("topic-analysis", job_id):
        _verify(uuid.UUID(job_id))


def _verify(identifier):
    settings, factory = get_settings(), session_factory()
    with factory.begin() as session:
        task = _locked(session, ResearchVerificationJob, identifier)
        if task is None or task.status != "queued" or task.available_at > utcnow():
            return
        enabled = (
            settings.auto_approve_topic_relationships
            if task.relationships
            else settings.auto_approve_topics
        )
        if not settings.ai_enabled or not enabled:
            # Retain durable work for when the operator enables the policy again.
            task.dispatched_at = None
            return
        job = _locked(session, TopicAnalysisJob, identifier)
        lock_topics(session)
        proposals = []
        if task.relationships:
            rows = session.scalars(
                select(TopicRelationProposal)
                .where(
                    TopicRelationProposal.job_id == identifier,
                    TopicRelationProposal.status == "pending",
                )
                .with_for_update()
            ).all()
            topics = {
                topic.id: topic
                for topic in session.scalars(
                    select(Topic).where(
                        Topic.id.in_(
                            {
                                value
                                for row in rows
                                for value in (row.topic_id, row.related_topic_id)
                            }
                        )
                    )
                )
            }
            proposals = [
                verification_input(row) for row in rows if not approval_blocker(row, topics)
            ]
            citations = [(item["evidence_url"], item["evidence_quote"]) for item in proposals]
        else:
            proposal = _locked(session, TopicProposal, job.proposal_id)
            citations = (
                [(item["url"], item["quote"]) for item in job.result.get("sources", [])]
                if proposal and metadata_input_current(job, proposal)
                else []
            )
        if job.status != "succeeded" or job.outcome != "enriched" or not citations:
            finish_analysis(task, "superseded")
            return
        verification = job.result.get("evidence_verification", {})
        semantic = job.result.get("relationship_verification", {})
        task.status, task.attempts = "running", task.attempts + 1
        task.lease_token = token = uuid.uuid4()
        task.lease_until = utcnow() + timedelta(seconds=300)
        attempt = task.attempts
    started, client = time.perf_counter(), None
    try:
        # Preserve previously verified citations and permanent failures. A missing
        # old check is fetched once; subsequent failures use transport retryability.
        checks = (
            dict(verification.get("checks", {}))
            if verification.get("version") == VERIFICATION_VERSION
            else {}
        )
        retry = [
            (url, quote)
            for url, quote in citations
            if citation_key(url, quote) not in checks
            or citation_retryable(checks[citation_key(url, quote)])
        ]
        if retry:
            checks.update(verify_citations(retry).get("checks", {}))
        verification = {"version": VERIFICATION_VERSION, "checks": checks}
        semantic_checks = (
            dict(semantic.get("checks", {})) if semantic.get("version") == VERSION else {}
        )
        to_review = [
            item
            for item in proposals
            if citation_verified(verification, item["evidence_url"], item["evidence_quote"])
            and (
                semantic_checks.get(item["proposal_id"], {}).get("input_hash") != item["input_hash"]
                or semantic_checks.get(item["proposal_id"], {}).get("verdict") == "uncertain"
            )
        ]
        semantic_error = None
        if to_review:
            client = CodexClient(settings)
            try:
                output = client.complete(
                    verification_prompt(to_review),
                    RelationshipVerificationResult.model_json_schema(),
                    allow_web_search=True,
                )
                semantic_checks.update(
                    {
                        key: {
                            **value,
                            "model": settings.codex_model,
                            "checked_at": utcnow().isoformat(),
                        }
                        for key, value in checked_verdicts(output, to_review)["checks"].items()
                    }
                )
            except (AnalysisError, ValueError) as exc:
                # Successful HTTP recovery is durable even if the model is down.
                semantic_error = (
                    str(exc) if isinstance(exc, AnalysisError) else "invalid_verification_result"
                )
        semantic = {"version": VERSION, "checks": semantic_checks}
        retry_checks = [checks.get(citation_key(url, quote), {}) for url, quote in citations]
        retry_after = max((value.get("retry_after", 0) or 0 for value in retry_checks), default=0)
        reason = semantic_error or (
            "citation_temporarily_unavailable"
            if any(citation_retryable(value) for value in retry_checks)
            else None
        )
        if not reason and any(
            semantic_checks.get(item["proposal_id"], {}).get("verdict") == "uncertain"
            for item in to_review
        ):
            reason = "relationship_verification_uncertain"
        if reason in CAPACITY_ERRORS:
            retry_after = safe_pause(getattr(client, "retry_after", 0))
        with factory.begin() as session:
            task = _locked(session, ResearchVerificationJob, identifier)
            if task is None or task.status != "running" or task.lease_token != token:
                return
            job = _locked(session, TopicAnalysisJob, identifier)
            lock_topics(session)
            job.result = {
                **job.result,
                "evidence_verification": verification,
                "relationship_verification": semantic,
                "verification_attempts": [
                    *job.result.get("verification_attempts", [])[-19:],
                    {
                        "attempt": attempt,
                        "checked_at": utcnow().isoformat(),
                        "error": reason,
                        "citations_retried": len(retry),
                        "relationships_checked": len(to_review),
                    },
                ],
            }
            # Re-read locked inputs after network work. Review services also check
            # status and identity so stale workers cannot undo an administrator.
            auto_approve_research(session, job)
            if job.proposal_id:
                proposal = session.get(TopicProposal, job.proposal_id)
                if proposal and proposal.status == "approved":
                    queue_relationships_after_enrichment(session, proposal, job)
            if reason:
                fail_verification(task, reason, retry_after=retry_after)
            else:
                pending = (
                    session.scalar(
                        select(TopicRelationProposal.id)
                        .where(
                            TopicRelationProposal.id.in_(
                                [uuid.UUID(item["proposal_id"]) for item in proposals]
                            ),
                            TopicRelationProposal.status != "approved",
                        )
                        .limit(1)
                    )
                    if proposals
                    else None
                )
                finish_analysis(
                    task,
                    "approved"
                    if (not pending if proposals else proposal and proposal.status == "approved")
                    else "review_required",
                )
        logger.info(
            "research_verification_completed",
            extra={"job_id": identifier, "outcome": task.outcome, "reason": reason},
        )
    except Exception as exc:
        reason = str(exc) if isinstance(exc, AnalysisError) else "verification_dependency_failure"
        cooldown = safe_pause(getattr(exc, "retry_after", 0)) if reason in CAPACITY_ERRORS else 0
        with factory.begin() as session:
            task = _locked(session, ResearchVerificationJob, identifier)
            if task and task.status == "running" and task.lease_token == token:
                fail_verification(task, reason, retry_after=cooldown)
        logger.warning(
            "research_verification_failed", extra={"job_id": identifier, "reason": reason}
        )
    finally:
        if client is not None:
            record_attempt(
                factory, ResearchVerificationJob, identifier, client, started, attempt=attempt
            )
