"""Execute one resumable topic decision; network calls never hold database locks."""

import time
from copy import deepcopy
from datetime import timedelta

from devfeed_core.ai_capacity import CAPACITY_ERRORS, safe_pause
from devfeed_core.analysis import fail_analysis, snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.inference_routing import route_for, total_tokens
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.jobs import owned_job
from devfeed_core.models import TopicAnalysisJob, TopicDecisionRun, TopicProposal, utcnow
from devfeed_core.topic_auto_approval import ACTOR, auto_approve_research
from devfeed_core.topic_decision_budget import initial_state, reserve, settle
from devfeed_core.topic_decisions import VERSION, DecisionDeferred, fetch_bundle, run_decision
from devfeed_core.topic_proposals import TopicReview, review_proposal
from devfeed_core.topics import lock_topics
from sqlalchemy import select

from devfeed_aggregator.codex_client import AnalysisError, CodexClient


def execute_decision(factory, identifier, token, proposal_id, topic):
    settings = get_settings()

    def locked(session, *, current=True):
        job = owned_job(session, TopicAnalysisJob, identifier, token)
        if job is None:
            raise DecisionDeferred("lease_lost")
        proposal = session.get(TopicProposal, proposal_id)
        if current and (
            proposal is None
            or proposal.status != "pending"
            or snapshot_hash(proposal.proposed) != job.input_hash
        ):
            raise DecisionDeferred("proposal_changed")
        run = session.get(TopicDecisionRun, proposal_id, with_for_update=True)
        if run is None:
            run = TopicDecisionRun(
                proposal_id=proposal_id,
                input_hash=job.input_hash,
                status="active",
                state=initial_state(settings),
            )
            session.add(run)
            session.flush()
        job.lease_until = utcnow() + timedelta(seconds=300)
        return job, run

    def save(key, value):
        with factory.begin() as session:
            _, run = locked(session)
            run.state = {**run.state, key: value}
            run.updated_at = utcnow()
        state[key] = value

    def call(stage, prompt, schema, *, escalated=False, web=False):
        with factory.begin() as session:
            job, run = locked(session)
            if run.status != "active":
                raise DecisionDeferred(run.reason or "decision_not_active")
            # Conservative UTF-8 estimate also bounds unusually large prompts.
            run.state = reserve(
                run.state,
                stage,
                escalated=escalated,
                web=web,
                minimum_tokens=max(2000, len(prompt.encode()) // 3 + 1500),
            )
            reservation = run.state["calls"][-1]
            index, attempt = len(run.state["calls"]) - 1, job.attempts
            run.updated_at = utcnow()
        operation = {
            "discovery": "topic_discovery",
            "draft": "topic_draft",
            "verification": "topic_verification",
        }[stage]
        route = route_for(settings, operation, quality_failure=escalated)
        client = CodexClient(
            settings.model_copy(
                update={"codex_timeout_seconds": max(10, int(reservation["seconds"]))}
            )
        )
        client.operation, client.job_id, client.attempt = operation, identifier, attempt
        client.reason = "validation_escalation" if escalated else VERSION
        client.route_override = route
        client.token_limit, client.search_limit = (
            reservation["reserved_tokens"],
            reservation["charged_searches"] if web else 0,
        )
        started, succeeded = time.perf_counter(), False
        try:
            result = client.complete(prompt, schema, allow_web_search=web)
            succeeded = True
            return result
        finally:
            # Persist charges even if the lease expired; never save stale metadata.
            with factory.begin() as session:
                job = session.scalar(
                    select(TopicAnalysisJob)
                    .where(TopicAnalysisJob.id == identifier)
                    .with_for_update()
                )
                run = session.get(TopicDecisionRun, proposal_id, with_for_update=True)
                newly_settled = run.state["calls"][index]["status"] == "running"
                run.state = settle(
                    run.state,
                    index,
                    usage=client.usage,
                    searches=client.web_search_count,
                    seconds=time.perf_counter() - started,
                    model=route.model,
                    effort=route.effort,
                    succeeded=succeeded,
                )
                run.updated_at = utcnow()
                state.update(deepcopy(run.state))
                # Attribute this invocation to its job exactly once. A budget
                # grant creates a new job; older calls stay only in the run ledger.
                if newly_settled:
                    job.usage = {
                        **(job.usage or {}),
                        **{
                            name: (job.usage or {}).get(name, 0) + client.usage.get(name, 0)
                            for name in (
                                "inputTokens",
                                "cachedInputTokens",
                                "outputTokens",
                                "reasoningOutputTokens",
                            )
                        },
                        "totalTokens": (job.usage or {}).get("totalTokens", 0)
                        + total_tokens(client.usage),
                    }
                    job.model = route.model
                    job.duration_ms = (job.duration_ms or 0) + int(
                        run.state["calls"][index]["seconds"] * 1000
                    )

    state = {}
    try:
        with factory.begin() as session:
            job, run = locked(session)
            job.prompt_version = VERSION
            if run.status != "active" or run.input_hash != job.input_hash:
                raise DecisionDeferred(run.reason or "proposal_changed")
            state = deepcopy(run.state)

        def fetch(urls):
            started = time.perf_counter()
            try:
                return fetch_bundle(urls)
            finally:
                save(
                    "evidence_seconds",
                    state.get("evidence_seconds", 0) + time.perf_counter() - started,
                )

        result = run_decision(str(proposal_id), topic, state, call=call, save=save, fetch=fetch)
        with factory.begin() as session:
            job = owned_job(session, TopicAnalysisJob, identifier, token)
            if job is None:
                return
            lock_topics(session)
            proposal = session.get(TopicProposal, proposal_id, with_for_update=True)
            run = session.get(TopicDecisionRun, proposal_id, with_for_update=True)
            if proposal.status != "pending" or snapshot_hash(proposal.proposed) != job.input_hash:
                run.status, run.reason = "deferred", "proposal_changed"
                finish_job(job, "superseded", utcnow())
                return
            before = deepcopy(proposal.proposed)
            job.result = {**result, "applied_input_hash": snapshot_hash(result["topic"])}
            if result["decision"] == "rejected":
                if settings.full_automation:
                    review_proposal(
                        session,
                        proposal_id,
                        TopicReview(
                            decision="rejected",
                            expected_input_hash=job.input_hash,
                            note=(
                                "Independent evidence review found this exact identity "
                                "outside developer scope."
                            ),
                        ),
                        ACTOR,
                    )
                finish_job(job, "out_of_scope", utcnow())
            else:
                proposal.proposed = result["topic"]
                proposal.evidence = [
                    *proposal.evidence,
                    {
                        "provider": VERSION,
                        "analysis_id": str(identifier),
                        "retrieved_at": utcnow().isoformat(),
                        "before": before,
                        "after": result["topic"],
                        "sources": result["sources"],
                    },
                ]
                finish_job(job, "enriched", utcnow())
                auto_approve_research(session, job)
            run.status = "decided" if proposal.status in {"approved", "rejected"} else "review"
            run.reason = None if run.status == "decided" else "manual_review_required"
            run.updated_at = utcnow()
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, (DecisionDeferred, AnalysisError))
            else "decision_dependency_failure"
        )
        with factory.begin() as session:
            job = owned_job(session, TopicAnalysisJob, identifier, token)
            if job is None:
                return
            run = session.get(TopicDecisionRun, proposal_id, with_for_update=True)
            if reason in CAPACITY_ERRORS:
                fail_analysis(job, reason, retry_after=safe_pause(getattr(exc, "retry_after", 0)))
            else:
                if run is not None and run.status == "active":
                    run.status, run.reason, run.updated_at = "deferred", reason, utcnow()
                job.result = {
                    **(job.result or {}),
                    "workflow": VERSION,
                    "deferred_reason": reason,
                    "reasons": [
                        "Deferred: "
                        + reason.replace("_", " ")
                        + ". Review manually or grant additional processing budget."
                    ],
                }
                finish_job(job, "decision_deferred", utcnow())
