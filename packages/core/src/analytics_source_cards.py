"""Pure source-card formatting shared by publishing and transactional detail."""

from __future__ import annotations

from datetime import datetime

from devfeed_core.analytics_card_types import (
    SourcePipelineCard,
    SourcePipelineKind,
    SourcePipelineRetryTarget,
)
from devfeed_core.source_lifecycle import SourceLifecycleFacts, _job_items


def _activity(facts: SourceLifecycleFacts) -> datetime:
    rows = ([facts.source] if facts.source is not None else []) + list(facts.candidates)
    values: list[datetime | None] = [
        getattr(row, field, None) for row in rows for field in ("updated_at", "created_at")
    ]
    values.extend(
        getattr(job, field, None)
        for job in facts.jobs.values()
        if job is not None
        for field in ("created_at", "finished_at")
    )
    return max(value for value in values if value is not None)


def _card(facts: SourceLifecycleFacts) -> SourcePipelineCard:
    source, candidate, readiness = facts.source, facts.candidate, facts.readiness
    subject = source if source is not None else candidate
    assert subject is not None
    kind: SourcePipelineKind = "source" if source is not None else "candidate"
    if source is not None:
        website_url = source.website_url
    else:
        assert candidate is not None
        website_url = candidate.identity_url
    jobs_by_stage = dict(_job_items(facts.jobs))
    return SourcePipelineCard(
        key=facts.key,
        kind=kind,
        id=subject.id,
        source_id=source.id if source is not None else None,
        candidate_id=candidate.id if candidate is not None else None,
        name=subject.name,
        website_url=website_url,
        feed_url=source.feed_url if source is not None else None,
        source_type=source.source_type if source is not None else "publisher",
        approval_status=subject.approval_status,
        enabled=source.enabled if source is not None else False,
        stage=readiness.stage,
        state=readiness.state,
        created_at=subject.created_at,
        updated_at=_activity(facts),
        snapshot=facts.snapshot,
        can_advance=readiness.next_action is not None,
        can_approve=readiness.can_approve,
        can_ingest=readiness.can_ingest,
        next_action=readiness.next_action,
        advance_label=(
            {
                "resume": "Resume",
                "reopen": "Reopen for review",
                "discovery": "Find feed",
                "profile": "Look up profile",
                "assessment": "Assess source",
                "branding": "Process branding",
                "ingestion": "Fetch feed",
            }.get(readiness.next_action, "Run next step")
            if readiness.next_action is not None
            else None
        ),
        retry_targets=[
            SourcePipelineRetryTarget(
                stage=target,
                job_id=job.id if (job := jobs_by_stage.get(target)) is not None else None,
            )
            for target in readiness.retry_targets
        ],
    )
