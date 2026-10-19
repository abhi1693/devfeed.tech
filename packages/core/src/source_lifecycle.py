"""Source readiness is derived from evidence, durable jobs and editorial intent.

Normal approved-source polling is grandfathered. Only a deliberate upstream board
retry pauses ingestion; ordinary reads and profile refreshes never revoke approval.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from devfeed_core.config import get_settings
from devfeed_core.models import (
    ArticleImageJob,
    IngestionJob,
    Source,
    SourceCandidate,
    SourceDiscoveryJob,
    SourceEnrichmentJob,
    utcnow,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.source_enrichment import profile_current, source_identity
from devfeed_core.source_relevance import VERSION

SourceStage = Literal[
    "discovery",
    "profile",
    "assessment",
    "branding",
    "review",
    "ingestion",
    "active",
    "paused",
    "rejected",
]
SourceState = Literal["waiting", "running", "retry_wait", "blocked", "ready", "complete"]
TARGETS = ("discovery", "profile", "assessment", "branding", "ingestion")


class SourceCheck(BaseModel):
    status: str
    job_id: uuid.UUID | None = None
    error: str | None = None
    available_at: datetime | None = None


class SourceReadiness(BaseModel):
    stage: SourceStage
    state: SourceState
    blockers: list[str] = Field(default_factory=list)
    checks: dict[str, SourceCheck] = Field(default_factory=dict)
    next_action: str | None = None
    retry_targets: list[str] = Field(default_factory=list)
    can_approve: bool = False
    can_ingest: bool = False


@dataclass
class SourceLifecycleFacts:
    key: str
    source: Source | None
    candidates: list[SourceCandidate]
    jobs: dict = field(default_factory=dict)
    readiness: SourceReadiness = field(init=False)
    snapshot: str = field(init=False)

    @property
    def candidate(self):
        job = self.jobs.get("discovery")
        return next(
            (row for row in self.candidates if job is not None and row.id == job.candidate_id),
            self.candidates[0] if self.candidates else None,
        )


def _check(job, now):
    if job is None:
        return SourceCheck(status="missing")
    state = "complete" if job.status == "succeeded" else job.status
    if state == "queued" and job.attempts and job.available_at > now:
        state = "retry_wait"
    return SourceCheck(status=state, job_id=job.id, error=job.error, available_at=job.available_at)


def _request(job):
    if job is None:
        return None
    payload = job.storage if isinstance(job, ArticleImageJob) else getattr(job, "result", {})
    return (payload or {}).get("source_pipeline_request")


def _needs_resume_checks(source, jobs):
    return source.approval_status != "approved" or bool(source.processing_request)


def assessment_current(source, profile, job):
    if not profile_current(source, profile):
        return False
    output = source.relevance_assessment or {}
    if job is not None:
        result = job.result or {}
        return bool(
            job.status == "succeeded"
            and result.get("input") == source_identity(source)
            and result.get("profile_job_id") == str(profile.id)
            and result.get("policy_version") == VERSION
            and result.get("output") == output
        )
    return bool(
        profile.operation == "legacy"
        and output.get("version") == VERSION
        and output.get("feed_url") == source.feed_url
        and output.get("source_type") == source.source_type
    )


def derive_source_readiness(source, *, candidates=(), jobs=None, settings=None, now=None):
    from devfeed_core.logos import logo_current

    settings, now, jobs = settings or get_settings(), now or utcnow(), jobs or {}
    checks = {name: _check(jobs.get(name), now) for name in TARGETS}
    result = SourceReadiness(stage="review", state="waiting", checks=checks)
    candidate = candidates[0] if candidates else None
    if (source is not None and not source.feed_url and source.approval_status == "rejected") or (
        source is None
        and candidate is not None
        and (candidate.approval_status == "rejected" or candidate.status == "rejected")
    ):
        result.stage, result.state = "rejected", "complete"
        if source is not None:
            result.next_action = "reopen"
        return result
    if source is None or not source.feed_url:
        check = checks["discovery"]
        result.stage = "discovery"
        if candidate is not None and candidate.selected_feed_id:
            result.next_action = "profile"
        elif check.status in {"missing", "stale"}:
            result.next_action = "discovery"
        elif check.status in {"complete", "failed"}:
            result.state = "blocked"
            result.blockers = ["feed_discovery_unresolved"]
            result.retry_targets = ["discovery"]
        else:
            result.state = _state(check)
        return result

    profile, assessment, branding, ingestion = (
        jobs.get(name) for name in ("profile", "assessment", "branding", "ingestion")
    )
    if checks["profile"].status not in {"queued", "running", "retry_wait"} and not profile_current(
        source, profile
    ):
        # Failed work for another input is stale, and may be replaced once.
        if (
            profile is None
            or profile.operation != "legacy"
            and (profile.result or {}).get("input") != source_identity(source)
        ):
            checks["profile"].status = "missing" if profile is None else "stale"
        elif checks["profile"].status != "failed":
            checks["profile"].status = "stale"

    ai = checks["assessment"]
    if ai.status not in {"queued", "running", "retry_wait"}:
        if assessment_current(source, profile, assessment):
            ai.status = "complete"
        elif assessment is None:
            ai.status = "missing" if settings.ai_enabled else "disabled"
        elif (
            (assessment.result or {}).get("input") != source_identity(source)
            or (assessment.result or {}).get("profile_job_id")
            != str(profile.id if profile else None)
            or (assessment.result or {}).get("policy_version") != VERSION
        ) or ai.status != "failed":
            ai.status = "stale"
    brand = checks["branding"]
    if brand.status not in {"queued", "running", "retry_wait"}:
        if not settings.image_storage_enabled or not source.logo_url:
            brand.status = "complete" if profile_current(source, profile) else "missing"
        elif branding is not None and branding.image_url != source.logo_url:
            brand.status = "stale"
        elif brand.status == "failed":
            pass  # A forced replacement must settle even if older variants exist.
        elif (
            logo_current(source)
            and settings.image_public_url
            and all(
                isinstance(item.get("key"), str) and item["key"]
                for item in (source.managed_logo or {}).get("variants", [])
            )
        ):
            brand.status = "complete"
        else:
            brand.status = "missing" if branding is None else "stale"
    result.can_approve = (
        source.approval_status != "approved"
        and checks["profile"].status == "complete"
        and brand.status == "complete"
    )
    if source.approval_status == "rejected":
        # Keep prerequisite facts and explicit editorial reapproval available,
        # while rejected cards expose no automatic processing actions.
        result.stage, result.state = "rejected", "complete"
        result.next_action = "reopen" if not result.can_approve else None
        return result
    result.can_ingest = (
        source.approval_status == "approved"
        and source.enabled
        and (ingestion is None or ingestion.status not in {"queued", "running"})
    )

    strict = _needs_resume_checks(source, jobs)
    processing = source.approval_status != "approved" or not source.enabled and strict
    if processing:
        required: list[SourceStage] = ["profile"]
        if (
            source.approval_status == "pending"
            and settings.full_automation
            and settings.ai_enabled
            or "assessment" in (source.processing_request or {}).get("required", [])
        ):
            required.append("assessment")
        required.append("branding")
        for name in required:
            check = checks[name]
            if check.status != "complete":
                result.stage = name
                result.state = _state(check)
                result.blockers = [f"{name}_{check.status}"]
                if check.status in {"missing", "stale"}:
                    result.next_action = name
                break
        else:
            result.stage = "review" if source.approval_status == "pending" else "paused"
            result.state = "ready" if result.stage == "review" else "complete"
            result.next_action = None if result.stage == "review" else "resume"
    elif not source.enabled:
        result.stage, result.state, result.next_action = "paused", "complete", "resume"
    elif checks["ingestion"].status in {"queued", "running", "retry_wait", "failed"}:
        result.stage, result.state = "ingestion", _state(checks["ingestion"])
        if result.state == "blocked":
            result.blockers = ["ingestion_failed"]
    elif ingestion is None and source.last_success_at is None:
        result.stage, result.state, result.next_action = "ingestion", "waiting", "ingestion"
    else:
        result.stage, result.state = "active", "complete"

    busy_profile = any(
        job is not None and job.status in {"queued", "running"} for job in (profile, assessment)
    )
    if not busy_profile:
        result.retry_targets.append("profile")
        if (
            settings.ai_enabled
            and profile_current(source, profile)
            and profile.operation == "profile"
        ):
            result.retry_targets.append("assessment")
    if (
        profile_current(source, profile)
        and source.logo_url
        and settings.image_storage_enabled
        and (branding is None or branding.status not in {"queued", "running"})
    ):
        result.retry_targets.append("branding")
    if (
        source.approval_status == "approved"
        and source.enabled
        and (ingestion is None or ingestion.status not in {"queued", "running"})
    ):
        result.retry_targets.append("ingestion")
    return result


def _state(check):
    return (
        "running"
        if check.status == "running"
        else "retry_wait"
        if check.status == "retry_wait"
        else "waiting"
        if check.status in {"missing", "queued"}
        else "blocked"
    )


def _latest(session, model, identifiers, *, subject="source_id", condition=None):
    if not identifiers:
        return {}
    column = getattr(model, subject)
    query = select(
        model.id,
        func.row_number()
        .over(
            partition_by=column,
            order_by=(
                model.status.in_(["queued", "running"]).desc(),
                model.created_at.desc(),
                model.id.desc(),
            ),
        )
        .label("position"),
    ).where(column.in_(identifiers))
    if condition is not None:
        query = query.where(condition)
    ranked = query.subquery()
    rows = session.scalars(
        select(model).join(ranked, ranked.c.id == model.id).where(ranked.c.position == 1)
    )
    return {getattr(row, subject): row for row in rows}


def source_snapshot_token(facts):
    def values(row):
        return (
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
            if row is not None
            else None
        )

    payload = {
        "source": values(facts.source),
        "candidates": [values(row) for row in sorted(facts.candidates, key=lambda row: row.id)],
        "jobs": {key: values(value) for key, value in facts.jobs.items()},
        "settings": {
            key: getattr(get_settings(), key)
            for key in (
                "ai_enabled",
                "full_automation",
                "image_storage_enabled",
                "image_public_url",
                "ai_content_not_before",
            )
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def source_readiness_batch(session, sources, candidates=None, *, settings=None, now=None):
    sources, candidates = list(sources), list(candidates or [])
    identifiers = [source.id for source in sources]
    linked = (
        list(
            session.scalars(
                select(SourceCandidate).where(SourceCandidate.source_id.in_(identifiers))
            )
        )
        if identifiers
        else []
    )
    all_candidates = {row.id: row for row in [*linked, *candidates]}
    discoveries = _latest(session, SourceDiscoveryJob, list(all_candidates), subject="candidate_id")
    profiles = _latest(
        session,
        SourceEnrichmentJob,
        identifiers,
        condition=SourceEnrichmentJob.operation.in_(["profile", "legacy"]),
    )
    assessments = _latest(
        session,
        SourceEnrichmentJob,
        identifiers,
        condition=SourceEnrichmentJob.operation == "relevance",
    )
    branding = _latest(
        session,
        ArticleImageJob,
        identifiers,
        condition=ArticleImageJob.operation.in_(["source-logo", "source-logo-refresh"]),
    )
    ingestion = _latest(session, IngestionJob, identifiers)
    result = {}
    for source in [*sources, None]:
        rows = (
            [row for row in all_candidates.values() if row.source_id == source.id]
            if source is not None
            else [row for row in candidates if row.source_id is None]
        )
        groups = (
            [(f"source:{source.id}", rows)]
            if source is not None
            else [(f"candidate:{row.id}", [row]) for row in rows]
        )
        for key, group in groups:
            group.sort(
                key=lambda row: (row.status in {"linked", "admitted"}, row.created_at, row.id)
            )
            discovery = max(
                (discoveries[row.id] for row in group if row.id in discoveries),
                key=lambda job: (job.status in {"queued", "running"}, job.created_at, job.id),
                default=None,
            )
            if discovery is not None:
                group.sort(key=lambda row: row.id != discovery.candidate_id)
            jobs = {
                "discovery": discovery,
                "profile": profiles.get(source.id) if source else None,
                "assessment": assessments.get(source.id) if source else None,
                "branding": branding.get(source.id) if source else None,
                "ingestion": ingestion.get(source.id) if source else None,
            }
            facts = SourceLifecycleFacts(key=key, source=source, candidates=group, jobs=jobs)
            facts.readiness = derive_source_readiness(
                source, candidates=group, jobs=jobs, settings=settings, now=now
            )
            facts.snapshot = source_snapshot_token(facts)
            result[key] = facts
    return result


def source_readiness(session, source, *, settings=None, now=None):
    return source_readiness_batch(session, [source], settings=settings, now=now)[
        f"source:{source.id}"
    ]


def source_activation_blockers(session, source):
    facts = source_readiness(session, source)
    if source.approval_status == "approved" and not _needs_resume_checks(source, facts.jobs):
        return []
    required = ["profile", "branding"]
    if source.approval_status == "approved" and "assessment" in (
        source.processing_request or {}
    ).get("required", []):
        required.append("assessment")
    return [
        f"{key}_{facts.readiness.checks[key].status}"
        for key in required
        if facts.readiness.checks[key].status != "complete"
    ]


def reconcile_source_processing(session, source, *, automatic=True):
    """Advance missing work once; never retry an unchanged terminal failure."""
    from devfeed_core.schemas import SourceDecision
    from devfeed_core.services import review_source
    from devfeed_core.source_enrichment import request_enrichment, request_relevance
    from devfeed_core.source_logos import request_source_logo

    facts = source_readiness(session, source)
    if source.approval_status == "rejected":
        return facts
    settings = get_settings()
    if (
        source.approval_status == "pending"
        and not (source.processing_request or {}).get("manual_review")
        and settings.full_automation
        and settings.ai_enabled
        and facts.readiness.checks["assessment"].status == "complete"
    ):
        assessment = source.relevance_assessment or {}
        negative = assessment.get("rejection_supported") is True
        if negative or assessment.get("approval_supported") is True and facts.readiness.can_approve:
            review_source(
                session,
                source.id,
                SourceDecision(
                    decision="rejected" if negative else "approved",
                    actor="devfeed:source-relevance",
                    note=("Source relevance evidence: " + assessment.get("reason", ""))[:1000],
                ),
            )
            return source_readiness(session, source)
    if source.approval_status == "approved" and source.enabled:
        return facts  # Readiness never backfills or suspends existing polling.
    action = facts.readiness.next_action
    if action == "profile":
        request_enrichment(session, source.id)
    elif action == "assessment" and (
        not automatic or settings.full_automation or source.processing_request
    ):
        # An operator's paused-source retry remains required across replacement
        # profile jobs; it is not a new automatic review of an approved source.
        request_relevance(
            session,
            source.id,
            profile=facts.jobs["profile"],
            automatic=automatic and source.approval_status == "pending",
        )
    elif action == "branding":
        request_source_logo(session, source.id, automatic=True)
    session.flush()
    return source_readiness(session, source)


def _locked_facts(session, kind, identifier, expected_snapshot):
    if kind == "source":
        source = session.scalar(select(Source).where(Source.id == identifier).with_for_update())
        if source is None:
            raise RecordNotFound("Source not found")
        session.scalars(
            select(SourceCandidate)
            .where(SourceCandidate.source_id == source.id)
            .order_by(SourceCandidate.id)
            .with_for_update()
        ).all()
        facts = source_readiness(session, source)
    elif kind == "candidate":
        candidate = session.scalar(
            select(SourceCandidate).where(SourceCandidate.id == identifier).with_for_update()
        )
        if candidate is None:
            raise RecordNotFound("Source candidate not found")
        if candidate.source_id is not None:
            raise OperationConflict("Candidate is linked to a source; reload its current card")
        facts = source_readiness_batch(session, [], [candidate])[f"candidate:{identifier}"]
    else:
        raise OperationConflict("Unknown source lifecycle item")
    if facts.snapshot != expected_snapshot:
        raise OperationConflict("Source processing changed; reload before continuing")
    return facts


def _discovery_request(session, facts):
    from devfeed_core.discovery import attach_source, enqueue, handoff
    from devfeed_core.source_enrichment import request_source_review

    candidate = facts.candidate
    if candidate is None:
        request_source_review(session, facts.source)
        return
    if candidate.status in {"rejected", "admitted", "linked"}:
        raise OperationConflict("This candidate requires an editorial decision before discovery")
    if candidate.selected_feed_id and (facts.source is None or not facts.source.feed_url):
        source = attach_source(session, candidate)
        handoff(session, candidate)
        return source
    candidate.status = "pending"
    enqueue(session, candidate.id, background=True)


def _after_action(session, facts, source=None):
    session.flush()
    source = source or facts.source
    if source is not None:
        return source_readiness(session, source)
    return source_readiness_batch(session, [], facts.candidates)[facts.key]


def advance_source_processing(session, kind, identifier, expected_snapshot, *, actor=None):
    from devfeed_core.jobs import request_ingestion
    from devfeed_core.source_enrichment import request_enrichment, request_relevance
    from devfeed_core.source_logos import request_source_logo

    facts = _locked_facts(session, kind, identifier, expected_snapshot)
    action, source = facts.readiness.next_action, facts.source
    if action == "discovery" or action == "profile" and source is None:
        return _after_action(session, facts, _discovery_request(session, facts))
    if source is None:
        raise OperationConflict("Discover a feed before source processing")
    if action == "reopen":
        from devfeed_core.source_enrichment import request_source_review

        source.approval_status, source.enabled = "pending", False
        source.processing_request = {
            "kind": "reopen_review",
            "id": str(uuid.uuid4()),
            "actor": actor or {},
            "requested_at": utcnow().isoformat(),
            "manual_review": True,
            "required": ["profile", "branding"],
        }
        if not source.feed_url and facts.candidate is not None:
            from devfeed_core.discovery import enqueue

            facts.candidate.status = "pending"
            facts.candidate.approval_status = "pending"
            enqueue(session, facts.candidate.id, background=True)
            job = None
        else:
            job = request_source_review(session, source)
        if job is not None:
            job.result = {
                **(job.result or {}),
                "source_pipeline_request": source.processing_request,
            }
    elif action == "profile":
        request_enrichment(session, source.id)
    elif action == "assessment":
        request_relevance(session, source.id, profile=facts.jobs["profile"])
    elif action == "branding":
        request_source_logo(session, source.id, automatic=True)
    elif action in {"ingestion", "resume"}:
        if source.approval_status != "approved":
            raise OperationConflict("Approve the source before ingestion")
        blockers = source_activation_blockers(session, source)
        if blockers:
            raise OperationConflict("Source processing is incomplete: " + ", ".join(blockers))
        source.enabled = True
        source.processing_request = {}
        request_ingestion(session, source)
    else:
        raise OperationConflict("No processing task is available; review or retry the source")
    return _after_action(session, facts)


def retry_source_processing(
    session, kind, identifier, expected_snapshot, target, job_id, *, actor=None
):
    from devfeed_core.jobs import request_ingestion
    from devfeed_core.source_enrichment import request_enrichment, request_relevance
    from devfeed_core.source_logos import request_source_logo

    facts = _locked_facts(session, kind, identifier, expected_snapshot)
    previous = facts.jobs.get(target)
    if target not in facts.readiness.retry_targets or (previous.id if previous else None) != job_id:
        raise OperationConflict("This source task cannot be retried from the current snapshot")
    source = facts.source
    if target == "discovery":
        return _after_action(session, facts, _discovery_request(session, facts))
    if source is None:
        raise OperationConflict("Discover a feed before source processing")
    if target != "ingestion" and source.approval_status == "approved":
        source.enabled = False
    request = {
        "kind": "upstream_retry",
        "target": target,
        "actor": actor or {},
        "previous_job_id": str(job_id) if job_id else None,
    }
    if target != "ingestion" and source.approval_status == "approved":
        required = {"profile", "branding", *(source.processing_request or {}).get("required", [])}
        if target == "assessment":
            required.add("assessment")
        source.processing_request = {
            **request,
            "id": str(uuid.uuid4()),
            "requested_at": utcnow().isoformat(),
            "required": sorted(required),
        }
    if target == "profile":
        job = request_enrichment(session, source.id)
    elif target == "assessment":
        job = request_relevance(session, source.id, profile=facts.jobs["profile"])
    elif target == "branding":
        job = request_source_logo(session, source.id, refresh=True)
    elif target == "ingestion":
        job = request_ingestion(session, source)
    else:
        raise OperationConflict("Unknown source processing target")
    if job is None:
        raise OperationConflict("No source processing task could be queued")
    if isinstance(job, SourceEnrichmentJob):
        job.result = {**(job.result or {}), "source_pipeline_request": request}
    elif isinstance(job, ArticleImageJob):
        job.storage = {**(job.storage or {}), "source_pipeline_request": request}
    return _after_action(session, facts)
