"""Lease-protected web research for topic metadata and active-topic relationships."""

import logging
import time
import uuid

from devfeed_core.ai_capacity import CAPACITY_ERRORS, safe_pause
from devfeed_core.analysis import fail_analysis, snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.models import Topic, TopicAnalysisJob, TopicProposal, utcnow
from devfeed_core.research_evidence import verify_citations
from devfeed_core.topic_analysis import (
    PROMPT_VERSION as METADATA_PROMPT_VERSION,
)
from devfeed_core.topic_analysis import (
    TopicResearchResult,
    apply_topic_research,
    queue_relationships_after_enrichment,
    research_prompt,
    resume_relationships_after_superseded,
)
from devfeed_core.topic_auto_approval import auto_approve_research
from devfeed_core.topic_relationships import (
    PROMPT_VERSION as RELATIONSHIP_PROMPT_VERSION,
)
from devfeed_core.topic_relationships import (
    RelationshipResearchResult,
    apply_relationship_research,
    relationship_prompt,
    research_current,
    topic_snapshot,
)
from devfeed_core.topics import lock_topics
from sqlalchemy import select

from devfeed_aggregator.analysis_telemetry import record_attempt
from devfeed_aggregator.codex_client import AnalysisError, CodexClient

logger = logging.getLogger(__name__)


def analyze_topic(job_id: str) -> None:
    with job_log_context("topic-analysis", job_id):
        try:
            _analyze(uuid.UUID(job_id))
        except Exception:
            logger.exception("topic_analysis_runtime_failed")
            raise


def _locked_job(session, identifier):
    return session.scalar(
        select(TopicAnalysisJob).where(TopicAnalysisJob.id == identifier).with_for_update()
    )


def _analyze(identifier):
    settings, factory = get_settings(), session_factory()
    with factory.begin() as session:
        job = _locked_job(session, identifier)
        if job is None or job.status != "queued" or job.available_at > utcnow():
            return
        if not settings.ai_enabled:
            fail_analysis(job, "ai_not_configured", retryable=False)
            return
        relationships = job.topic_id is not None
        job.prompt_version = (
            RELATIONSHIP_PROMPT_VERSION if relationships else METADATA_PROMPT_VERSION
        )
        snapshot, proposal_id = job.input_snapshot, job.proposal_id
        if relationships:
            job.prompt_version = RELATIONSHIP_PROMPT_VERSION
            if not research_current(session, job):
                finish_job(job, "superseded", utcnow())
                resume_relationships_after_superseded(session, job)
                return
            # Refresh eligibility before spending inference. Keep the persisted
            # snapshot immutable and validate results against the catalog sent.
            topics = session.scalars(
                select(Topic).where(
                    Topic.status == "active",
                    Topic.id.in_([uuid.UUID(value) for value in snapshot["snapshots"]]),
                )
            )
            eligible = {
                str(topic.id)
                for topic in topics
                if topic_snapshot(topic) == snapshot["snapshots"][str(topic.id)]
            }
            snapshot = {
                **snapshot,
                "catalog": [row for row in snapshot["catalog"] if row[0] in eligible],
            }
            if len(snapshot["catalog"]) < 2:
                finish_job(job, "superseded", utcnow())
                return
        else:
            proposal = session.get(TopicProposal, job.proposal_id)
            if (
                proposal is None
                or proposal.status != "pending"
                or snapshot_hash(proposal.proposed) != job.input_hash
            ):
                finish_job(job, "superseded", utcnow())
                return
        token = start_job(job, utcnow(), 300)
        job.model = settings.codex_model
        attempt = job.attempts
    logger.info(
        "topic_analysis_started",
        extra={"topic_id": job.topic_id} if relationships else {"proposal_id": proposal_id},
    )
    started, client = time.perf_counter(), None
    try:
        # Hosted web search is allowed for this task; all other tools remain disabled.
        client = CodexClient(settings)
        output = client.complete(
            relationship_prompt(snapshot) if relationships else research_prompt(snapshot),
            (
                RelationshipResearchResult if relationships else TopicResearchResult
            ).model_json_schema(),
            allow_web_search=True,
        )
        result = (
            RelationshipResearchResult if relationships else TopicResearchResult
        ).model_validate(output)
        # Verification uses public transport outside a transaction and is bounded
        # independently from inference. Keep unverified proposals for human review.
        verification = verify_citations(
            [(item.evidence_url, item.evidence_quote) for item in result.relationships]
            if isinstance(result, RelationshipResearchResult)
            else [(item.url, item.quote) for item in result.sources]
        )
        with factory.begin() as session:
            job = _locked_job(session, identifier)
            if job is None or job.status != "running" or job.lease_token != token:
                logger.warning("topic_analysis_lease_lost")
                return
            job.result = {
                **result.model_dump(mode="json"),
                "web_searches": getattr(client, "web_search_count", 0),
                "evidence_verification": verification,
            }
            if isinstance(result, RelationshipResearchResult):
                allowed = {row[0] for row in snapshot["catalog"]}
                if any(
                    str(value.topic_id) not in allowed or str(value.related_topic_id) not in allowed
                    for value in result.relationships
                ):
                    raise ValueError(
                        "Research referenced topics outside the supplied active catalog"
                    )
                outcome, created = apply_relationship_research(session, job, result)
                job.result = {**job.result, "proposal_ids": created}
            else:
                # Approval takes taxonomy then proposal locks. Take the same
                # order before saving metadata and inserting its follow-up job.
                lock_topics(session)
                proposal = session.scalar(
                    select(TopicProposal).where(TopicProposal.id == proposal_id).with_for_update()
                )
                outcome = apply_topic_research(proposal, job, result) if proposal else "superseded"
            finish_job(job, outcome, utcnow())
            auto_approve_research(session, job)
            if isinstance(result, TopicResearchResult) and outcome == "enriched":
                assert proposal is not None
                queue_relationships_after_enrichment(session, proposal, job)
            elif isinstance(result, RelationshipResearchResult) and outcome == "superseded":
                resume_relationships_after_superseded(session, job)
        logger.info(
            "topic_analysis_completed",
            extra={
                "outcome": outcome,
                "web_searches": getattr(client, "web_search_count", 0),
                "sources": len(result.relationships)
                if isinstance(result, RelationshipResearchResult)
                else len(result.sources),
            },
        )
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, AnalysisError)
            else "invalid_analysis_result"
            if isinstance(exc, ValueError)
            else "analysis_dependency_failure"
        )
        cooldown = safe_pause(getattr(exc, "retry_after", 0)) if reason in CAPACITY_ERRORS else 0
        with factory.begin() as session:
            job = _locked_job(session, identifier)
            if job is not None and job.status == "running" and job.lease_token == token:
                fail_analysis(
                    job,
                    reason,
                    retry_after=cooldown,
                    retryable=reason
                    not in {
                        "ai_not_configured",
                        "unexpected_tool_execution",
                        "unexpected_server_request",
                    },
                )
        logger.warning("topic_analysis_failed", extra={"reason": reason})
    finally:
        if client is not None:
            record_attempt(factory, TopicAnalysisJob, identifier, client, started, attempt=attempt)
