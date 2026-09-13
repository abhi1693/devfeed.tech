"""Durable admission accounting; failed or interrupted calls cannot regain budget."""

from copy import deepcopy
from datetime import timedelta

from sqlalchemy import func, select

from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.inference_routing import total_tokens
from devfeed_core.models import TopicAnalysisJob, TopicDecisionRun, TopicProposal, utcnow
from devfeed_core.topic_decisions import VERSION, DecisionDeferred


def initial_state(settings) -> dict:
    return {
        "version": VERSION,
        "limits": {
            "calls": settings.topic_decision_max_calls,
            "tokens": settings.topic_decision_max_tokens,
            "call_tokens": settings.topic_decision_call_tokens,
            "searches": settings.topic_decision_max_searches,
            "seconds": settings.topic_decision_max_seconds,
            "escalations": 1,
        },
        "calls": [],
    }


def reserve(state: dict, stage: str, *, escalated=False, web=False, minimum_tokens=2000) -> dict:
    state = deepcopy(state)
    calls, limits = state["calls"], state["limits"]
    if any(call["status"] == "running" for call in calls):
        raise DecisionDeferred("interrupted_call_requires_review")
    if len(calls) >= limits["calls"]:
        raise DecisionDeferred("call_budget_exhausted")
    if escalated and sum(call["escalated"] for call in calls) >= limits["escalations"]:
        raise DecisionDeferred("escalation_budget_exhausted")
    tokens = min(
        limits["call_tokens"], limits["tokens"] - sum(call["charged_tokens"] for call in calls)
    )
    seconds = (
        limits["seconds"]
        - sum(call["seconds"] for call in calls)
        - state.get("evidence_seconds", 0)
    )
    searches = limits["searches"] - sum(call["charged_searches"] for call in calls)
    if tokens < minimum_tokens:
        raise DecisionDeferred("token_budget_exhausted")
    if seconds < 10:
        raise DecisionDeferred("time_budget_exhausted")
    if web and searches <= 0:
        raise DecisionDeferred("search_budget_exhausted")
    calls.append(
        {
            "stage": stage,
            "escalated": escalated,
            "status": "running",
            "reserved_tokens": tokens,
            "charged_tokens": tokens,
            "charged_searches": searches if web else 0,
            "seconds": min(seconds, get_settings().codex_timeout_seconds),
            "started_at": utcnow().isoformat(),
        }
    )
    return state


def settle(
    state: dict,
    index: int,
    *,
    usage: dict,
    searches: int,
    seconds: float,
    model: str | None,
    effort: str | None,
    succeeded: bool,
) -> dict:
    state = deepcopy(state)
    call = state["calls"][index]
    if call["status"] != "running":
        return state
    measured = total_tokens(usage)
    call.update(
        status="returned" if succeeded else "failed",
        tokens=usage,
        charged_tokens=(measured or call["reserved_tokens"])
        if succeeded
        else max(measured, call["reserved_tokens"]),
        charged_searches=searches if succeeded else max(searches, call["charged_searches"]),
        seconds=seconds,
        model=model,
        reasoning_effort=effort,
        finished_at=utcnow().isoformat(),
        usage_known=bool(measured),
    )
    return state


def schedule_decisions(factory) -> int:
    settings = get_settings()
    if not (
        settings.ai_enabled and settings.full_automation and settings.ai_bounded_topics_enabled
    ):
        return 0
    with factory.begin() as session:
        if not session.scalar(select(func.pg_try_advisory_xact_lock(0x444643, 0))):
            return 0
        pending = session.scalar(
            select(func.count())
            .select_from(TopicAnalysisJob)
            .where(
                TopicAnalysisJob.proposal_id.is_not(None),
                TopicAnalysisJob.status.in_(["queued", "running"]),
            )
        )
        slots = min(
            settings.automation_batch_size, max(0, settings.topic_decision_max_pending - pending)
        )
        if not slots:
            return 0
        attempted = (
            select(TopicDecisionRun.proposal_id)
            .where(TopicDecisionRun.proposal_id == TopicProposal.id)
            .exists()
        )
        busy = (
            select(TopicAnalysisJob.id)
            .where(
                TopicAnalysisJob.proposal_id == TopicProposal.id,
                TopicAnalysisJob.status.in_(["queued", "running"]),
            )
            .exists()
        )
        proposals = session.scalars(
            select(TopicProposal)
            .where(
                TopicProposal.status == "pending",
                ~attempted,
                ~busy,
            )
            .order_by(TopicProposal.created_at, TopicProposal.id)
            .limit(slots)
            .with_for_update(skip_locked=True)
        ).all()
        for proposal in proposals:
            session.add(
                TopicAnalysisJob(
                    proposal_id=proposal.id,
                    input_hash=snapshot_hash(proposal.proposed),
                    input_snapshot={"topic": deepcopy(proposal.proposed), "evidence": []},
                    requested_by={
                        "subject": VERSION,
                        "issuer": "devfeed",
                        "organization_id": "system",
                        "name": "Bounded topic review",
                    },
                    prompt_version=VERSION,
                )
            )
            proposal.research_requested = False
        return len(proposals)


def actionable_topic_backlog(session) -> bool:
    """Deferred/manual cases do not block background research forever."""
    return bool(
        session.scalar(
            select(TopicProposal.id)
            .outerjoin(TopicDecisionRun, TopicDecisionRun.proposal_id == TopicProposal.id)
            .where(
                TopicProposal.status == "pending",
                (TopicDecisionRun.proposal_id.is_(None)) | (TopicDecisionRun.status == "active"),
            )
            .limit(1)
        )
    )


def relationship_allowance(session) -> bool:
    from devfeed_core.models import InferenceCall

    settings = get_settings()
    if not settings.ai_bounded_topics_enabled:
        return True
    if settings.relationship_pause_for_topic_backlog and actionable_topic_backlog(session):
        return False
    # Reserve through actual running calls as well as completed ones. The worker
    # serializes admission using an advisory lock before transport begins.
    calls = session.scalar(
        select(func.count())
        .select_from(InferenceCall)
        .where(
            InferenceCall.operation.in_(["relationship_research", "relationship_verification"]),
            InferenceCall.started_at >= utcnow() - timedelta(days=1),
        )
    )
    return calls < settings.relationship_daily_call_budget


def admit_relationship_call(values: dict) -> bool:
    """Atomic, fail-closed reservation, independent of best-effort usage telemetry."""
    from devfeed_core.db import session_factory
    from devfeed_core.models import InferenceCall

    with session_factory().begin() as session:
        session.execute(select(func.pg_advisory_xact_lock(0x444652, 0)))
        if not relationship_allowance(session):
            return False
        session.add(InferenceCall(**values))
    return True


def grant_budget(session, proposal_id, *, calls: int, tokens: int, note: str, actor: str) -> dict:
    """Explicit operator recovery. Preserve all spent budget and earlier evidence audits."""
    from devfeed_core.services import OperationConflict

    if not (
        1 <= calls <= 5
        and 2000 <= tokens <= 64000
        and 1 <= len(note.strip()) <= 500
        and 1 <= len(actor.strip()) <= 100
    ):
        raise ValueError("Supply 1-5 calls, 2000-64000 tokens, an actor and a bounded reason")
    # Lock every existing job before the proposal, matching worker lock order.
    jobs = session.scalars(
        select(TopicAnalysisJob)
        .where(TopicAnalysisJob.proposal_id == proposal_id)
        .order_by(TopicAnalysisJob.id)
        .with_for_update()
    ).all()
    proposal = session.get(TopicProposal, proposal_id, with_for_update=True)
    run = session.get(TopicDecisionRun, proposal_id, with_for_update=True)
    if (
        proposal is None
        or proposal.status != "pending"
        or run is None
        or run.status not in {"deferred", "review"}
    ):
        raise OperationConflict(
            "Only a pending deferred or manual-review decision can receive budget"
        )
    if any(job.status in {"queued", "running"} for job in jobs):
        raise OperationConflict("Wait for the current job before granting budget")
    state = deepcopy(run.state)
    if len(state.get("grants", [])) >= 2:
        raise OperationConflict(
            "Two additional budgets were already granted; review this topic manually"
        )
    previous_hash = run.input_hash
    state.setdefault("grants", []).append(
        {
            "actor": actor,
            "note": note,
            "granted_at": utcnow().isoformat(),
            "calls": calls,
            "tokens": tokens,
            "previous_input_hash": previous_hash,
            "previous_state_hash": snapshot_hash(run.state),
            "previous_call_tokens": state["limits"]["call_tokens"],
            "call_tokens": get_settings().topic_decision_call_tokens,
        }
    )
    for call in state["calls"]:
        if call["status"] == "running":
            call["status"] = "unknown"  # Retain full reservations for the lost invocation.
    # Fresh evidence and prompts, without forgiving a single spent token or call.
    for key in (
        "discovery",
        "evidence",
        "hint_evidence",
        "draft",
        "draft_verification",
        "escalated_draft",
        "escalated_draft_verification",
    ):
        state.pop(key, None)
    for key, extra in {
        "calls": calls,
        "tokens": tokens,
        "searches": 4,
        "seconds": 240,
        "escalations": 1,
    }.items():
        state["limits"][key] += extra
    # An explicit grant adopts the current per-call guard, without forgiving any
    # consumed capacity. Otherwise an old undersized guard can never be repaired.
    state["limits"]["call_tokens"] = get_settings().topic_decision_call_tokens
    run.state, run.status, run.reason = state, "active", None
    run.input_hash, run.updated_at = snapshot_hash(proposal.proposed), utcnow()
    job = TopicAnalysisJob(
        proposal_id=proposal.id,
        input_hash=run.input_hash,
        input_snapshot={"topic": deepcopy(proposal.proposed), "evidence": []},
        requested_by={
            "subject": actor,
            "issuer": "devfeed",
            "organization_id": "system",
            "name": "Explicit budget grant",
        },
        prompt_version=VERSION,
    )
    session.add(job)
    session.flush()
    return {"proposal_id": str(proposal.id), "job_id": str(job.id), "limits": state["limits"]}
