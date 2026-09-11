"""Recheck completed research without repeating enrichment or losing review decisions."""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from devfeed_core import topic_verification
from devfeed_core.ai_capacity import CAPACITY_ERRORS, safe_pause
from devfeed_core.config import Settings, get_settings
from devfeed_core.db import session_factory
from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import owned_job
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
from devfeed_core.research_verification import (
    POLICY_VERSION,
    fail_verification,
    metadata_input_current,
)
from devfeed_core.topic_analysis import queue_relationships_after_enrichment
from devfeed_core.topic_auto_approval import auto_approve_research
from devfeed_core.topic_relationships import approval_blocker
from devfeed_core.topics import lock_topics
from devfeed_core.verification_results import read_topic_verdict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devfeed_aggregator.analysis_telemetry import record_attempt
from devfeed_aggregator.codex_client import AnalysisError, CodexClient

logger = logging.getLogger(__name__)


def _locked(session, model, identifier):
    return session.scalar(select(model).where(model.id == identifier).with_for_update())


class VerificationClient(Protocol):
    def complete(self, prompt: str, schema: dict, *, allow_web_search: bool = False) -> dict: ...


@dataclass(frozen=True)
class VerificationContext:
    identifier: uuid.UUID
    token: uuid.UUID
    attempt: int
    citations: list[tuple[str, str]]
    proposals: list[dict]
    metadata: dict | None
    verification: dict
    semantic: dict
    topic_semantic: dict


@dataclass
class ModelAttempt:
    started: float
    client: VerificationClient | None = None


@dataclass(frozen=True)
class VerificationOutcome:
    verification: dict
    semantic: dict
    topic_semantic: dict
    reason: str | None
    retry_after: int
    citations_retried: int
    relationships_checked: int
    topic_checked: bool


class ResearchVerificationService:
    def __init__(
        self,
        settings: Settings,
        factory: sessionmaker[Session],
        client_factory: Callable[[Settings], VerificationClient],
        citation_checker: Callable[[list[tuple[str, str]]], dict],
        clock: Callable[[], datetime],
    ):
        self.settings = settings
        self.factory = factory
        self.client_factory = client_factory
        self.citation_checker = citation_checker
        self.clock = clock

    def run(self, identifier: uuid.UUID) -> None:
        context = self.claim(identifier)
        if context is None:
            return
        attempt = ModelAttempt(time.perf_counter())
        try:
            result = self.evaluate(context, attempt)
            self.apply(context, result)
        except Exception as exc:
            self.fail(context, exc)
        finally:
            if attempt.client is not None:
                record_attempt(
                    self.factory,
                    ResearchVerificationJob,
                    identifier,
                    attempt.client,
                    attempt.started,
                    attempt=context.attempt,
                )

    def claim(self, identifier: uuid.UUID) -> VerificationContext | None:
        with self.factory.begin() as session:
            task = _locked(session, ResearchVerificationJob, identifier)
            if task is None or task.status != "queued" or task.available_at > self.clock():
                return None
            enabled = (
                self.settings.auto_approve_topic_relationships
                if task.relationships
                else self.settings.auto_approve_topics
            )
            if not self.settings.ai_enabled or not enabled:
                # Retain durable work for when the operator enables the policy again.
                task.dispatched_at = None
                return None
            job = _locked(session, TopicAnalysisJob, identifier)
            job.result = {**job.result, "verification_policy_version": POLICY_VERSION}
            lock_topics(session)
            proposals = []
            metadata = None
            topic_semantic = job.result.get("topic_verification", {})
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
                if citations:
                    metadata = topic_verification.verification_input(proposal)
                    if (
                        topic_semantic.get("version") == topic_verification.VERSION
                        and topic_semantic.get("check", {}).get("input_hash")
                        == metadata["input_hash"]
                    ):
                        # An uncertain verdict must be replaced, not gated forever by
                        # its own bad quotes. Research citations still have to pass.
                        if not read_topic_verdict(topic_semantic).uncertain:
                            citations.extend(
                                (s["url"], s["quote"])
                                for s in topic_semantic["check"].get("sources", [])
                            )
                    else:
                        topic_semantic = {}
            if job.status != "succeeded" or job.outcome != "enriched" or not citations:
                finish_job(task, "superseded", self.clock())
                return None
            verification = job.result.get("evidence_verification", {})
            semantic = job.result.get("relationship_verification", {})
            token = start_job(task, self.clock(), 300)
            attempt = task.attempts
        return VerificationContext(
            identifier,
            token,
            attempt,
            citations,
            proposals,
            metadata,
            verification,
            semantic,
            topic_semantic,
        )

    def evaluate(self, context: VerificationContext, attempt: ModelAttempt) -> VerificationOutcome:
        citations = list(context.citations)
        proposals, metadata = context.proposals, context.metadata
        verification, semantic = context.verification, context.semantic
        topic_semantic = context.topic_semantic
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
            checks.update(self.citation_checker(retry).get("checks", {}))
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
        semantic_error = (
            self._verify_relationships(to_review, semantic_checks, attempt) if to_review else None
        )
        semantic = {"version": VERSION, "checks": semantic_checks}
        topic_checked = False
        if metadata and all(
            citation_verified(verification, url, quote) for url, quote in citations
        ):
            topic_semantic, topic_checked, topic_error = self._verify_topic(
                metadata, topic_semantic, attempt
            )
            semantic_error = topic_error or semantic_error
            identity_citations = [
                (s["url"], s["quote"]) for s in topic_semantic.get("check", {}).get("sources", [])
            ]
            identity_retry = [
                (url, quote)
                for url, quote in identity_citations
                if citation_key(url, quote) not in checks
            ]
            if identity_retry:
                checks.update(self.citation_checker(identity_retry).get("checks", {}))
                verification = {"version": VERIFICATION_VERSION, "checks": checks}
                retry.extend(identity_retry)
            citations.extend(pair for pair in identity_citations if pair not in citations)
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
        if not reason and metadata and read_topic_verdict(topic_semantic).uncertain:
            reason = "topic_verification_uncertain"
        if reason in CAPACITY_ERRORS:
            retry_after = safe_pause(getattr(attempt.client, "retry_after", 0))
        return VerificationOutcome(
            verification,
            semantic,
            topic_semantic,
            reason,
            retry_after,
            len(retry),
            len(to_review),
            topic_checked,
        )

    def _verify_relationships(
        self, to_review: list[dict], semantic_checks: dict, attempt: ModelAttempt
    ) -> str | None:
        client = attempt.client = self.client_factory(self.settings)
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
                        "model": self.settings.codex_model,
                        "checked_at": self.clock().isoformat(),
                    }
                    for key, value in checked_verdicts(output, to_review)["checks"].items()
                }
            )
        except (AnalysisError, ValueError) as exc:
            # Successful HTTP recovery is durable even if the model is down.
            return str(exc) if isinstance(exc, AnalysisError) else "invalid_verification_result"
        return None

    def _verify_topic(
        self, metadata: dict, topic_semantic: dict, attempt: ModelAttempt
    ) -> tuple[dict, bool, str | None]:
        topic_checked, semantic_error = False, None
        if not topic_semantic or read_topic_verdict(topic_semantic).uncertain:
            client = attempt.client = self.client_factory(self.settings)
            try:
                output = client.complete(
                    topic_verification.verification_prompt(metadata),
                    topic_verification.TopicVerificationResult.model_json_schema(),
                    allow_web_search=True,
                )
                topic_semantic = {
                    **topic_verification.checked_verdict(output, metadata),
                    "model": self.settings.codex_model,
                    "checked_at": self.clock().isoformat(),
                }
                topic_checked = True
            except (AnalysisError, ValueError) as exc:
                semantic_error = (
                    str(exc) if isinstance(exc, AnalysisError) else "invalid_verification_result"
                )
        return topic_semantic, topic_checked, semantic_error

    def apply(self, context: VerificationContext, result: VerificationOutcome) -> None:
        identifier, token = context.identifier, context.token
        proposals, metadata = context.proposals, context.metadata
        verification, semantic, topic_semantic = (
            result.verification,
            result.semantic,
            result.topic_semantic,
        )
        reason, retry_after = result.reason, result.retry_after
        proposal = None
        with self.factory.begin() as session:
            task = owned_job(session, ResearchVerificationJob, identifier, token)
            if task is None:
                return
            job = _locked(session, TopicAnalysisJob, identifier)
            lock_topics(session)
            job.result = {
                **job.result,
                "evidence_verification": verification,
                "relationship_verification": semantic,
                **({"topic_verification": topic_semantic} if metadata else {}),
                "verification_attempts": [
                    *job.result.get("verification_attempts", [])[-19:],
                    {
                        "attempt": context.attempt,
                        "checked_at": self.clock().isoformat(),
                        "error": reason,
                        "citations_retried": result.citations_retried,
                        "relationships_checked": result.relationships_checked,
                        "topics_checked": int(result.topic_checked),
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
                finish_job(
                    task,
                    "approved"
                    if (not pending if proposals else proposal and proposal.status == "approved")
                    else "review_required",
                    self.clock(),
                )
        logger.info(
            "research_verification_completed",
            extra={"job_id": identifier, "outcome": task.outcome, "reason": reason},
        )

    def fail(self, context: VerificationContext, exc: Exception) -> None:
        identifier, token = context.identifier, context.token
        reason = str(exc) if isinstance(exc, AnalysisError) else "verification_dependency_failure"
        cooldown = safe_pause(getattr(exc, "retry_after", 0)) if reason in CAPACITY_ERRORS else 0
        with self.factory.begin() as session:
            task = owned_job(session, ResearchVerificationJob, identifier, token)
            if task is not None:
                fail_verification(task, reason, retry_after=cooldown)
        logger.warning(
            "research_verification_failed", extra={"job_id": identifier, "reason": reason}
        )


def verify_research(job_id: str) -> None:
    with job_log_context("topic-analysis", job_id):
        ResearchVerificationService(
            get_settings(), session_factory(), CodexClient, verify_citations, utcnow
        ).run(uuid.UUID(job_id))
