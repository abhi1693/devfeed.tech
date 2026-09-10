"""Private, attributed topic import and review operations."""

import uuid
from typing import Annotated, Literal

from devfeed_core import github_topics
from devfeed_core import topic_proposals as proposals
from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import TopicAnalysisJob, TopicProposal
from devfeed_core.topic_analysis import (
    FIELDS,
    TopicAnalysisBatchOut,
    request_all_topic_analysis,
    request_topic_analysis,
)
from devfeed_core.topics import lock_topics
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import load_only

from devfeed_admin_api.auth import Admin, actor, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.jobs import AdminJobOut, job_view
from devfeed_admin_api.pagination import Listing, Page, paginate, record
from devfeed_admin_api.search import text_search

router = APIRouter(prefix="/v1/admin", tags=["admin-topics"], dependencies=[Depends(require_admin)])


@router.post(
    "/topic-imports/preview",
    response_model=proposals.TopicImportPreview,
    operation_id="admin_topic_import_preview",
)
def preview_import(body: proposals.TopicImport, session: DB):
    try:
        return proposals.preview_import(session, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/topic-imports",
    response_model=list[proposals.TopicProposalOut],
    status_code=201,
    operation_id="admin_topic_import_submit",
)
def submit_import(body: proposals.TopicImportSubmit, session: DB, admin: Admin):
    try:
        values = proposals.submit_import(session, body, actor(admin))
    except (proposals.OperationConflict, proposals.RecordNotFound):
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return [proposals.proposal_view(value) for value in values]


@router.get(
    "/topic-proposals",
    response_model=Page[proposals.TopicProposalOut],
    operation_id="admin_topic_proposals_list",
)
def listing(
    session: DB,
    query: Listing,
    status: Literal["pending", "approved", "rejected"] | None = None,
    batch_id: uuid.UUID | None = None,
    kind: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    action: Literal["create", "update"] | None = None,
    analysis: Literal["not_run", "queued", "running", "enriched", "no_additions", "failed"]
    | None = None,
    missing: Literal[
        "any", "description", "aliases", "keywords", "website_url", "logo_url", "facts"
    ]
    | None = None,
):
    statement = select(TopicProposal)
    if status:
        statement = statement.where(TopicProposal.status == status)
    if batch_id:
        statement = statement.where(TopicProposal.batch_id == batch_id)
    if kind:
        statement = statement.where(TopicProposal.proposed["kind"].astext == kind)
    if source:
        statement = statement.where(TopicProposal.source_name == source)
    if action:
        statement = statement.where(TopicProposal.action == action)
    if analysis:
        latest = (
            latest_analysis_statement()
            .with_only_columns(
                TopicAnalysisJob.id,
                TopicAnalysisJob.proposal_id,
                TopicAnalysisJob.status,
                TopicAnalysisJob.outcome,
            )
            .subquery()
        )
        statement = statement.outerjoin(latest, latest.c.proposal_id == TopicProposal.id)
        if analysis == "not_run":
            statement = statement.where(latest.c.id.is_(None))
        elif analysis in {"enriched", "no_additions"}:
            statement = statement.where(
                latest.c.status == "succeeded",
                latest.c.outcome == "enriched"
                if analysis == "enriched"
                else latest.c.outcome.is_distinct_from("enriched"),
            )
        else:
            statement = statement.where(latest.c.status == analysis)
    if missing:
        fields = FIELDS if missing == "any" else [missing]
        statement = statement.where(
            or_(
                *[
                    func.coalesce(TopicProposal.proposed[field].astext, "").in_(
                        ["", "[]"] if field in {"aliases", "keywords", "facts"} else [""]
                    )
                    for field in fields
                ]
            )
        )
    if query.q:
        statement = statement.where(
            text_search(
                query.q,
                TopicProposal.slug,
                TopicProposal.source_name,
                *[
                    TopicProposal.proposed[field].astext
                    for field in ("name", "description", "aliases", "keywords")
                ],
            )
        )
    page = paginate(
        session,
        statement,
        query,
        {
            "created_at": TopicProposal.created_at,
            "slug": TopicProposal.slug,
            "status": TopicProposal.status,
        },
        default="-created_at",
    )
    analyses = latest_analyses(session, [value.id for value in page["items"]])
    return {
        **page,
        "items": [
            proposals.proposal_view(value, analyses.get(value.id)) for value in page["items"]
        ],
    }


class TopicProposalFilterOptions(BaseModel):
    kinds: list[str]
    sources: list[str]


@router.get(
    "/topic-proposals/filters",
    response_model=TopicProposalFilterOptions,
    operation_id="admin_topic_proposal_filter_options",
)
def filter_options(session: DB):
    """Use the whole proposal catalog so choices survive filtering and pagination."""
    kind = TopicProposal.proposed["kind"].astext
    return {
        "kinds": session.scalars(
            select(kind).where(kind.is_not(None), kind != "").distinct().order_by(kind)
        ).all(),
        "sources": session.scalars(
            select(TopicProposal.source_name).distinct().order_by(TopicProposal.source_name)
        ).all(),
    }


def latest_analysis_statement():
    return (
        select(TopicAnalysisJob)
        .distinct(TopicAnalysisJob.proposal_id)
        .order_by(
            TopicAnalysisJob.proposal_id,
            TopicAnalysisJob.created_at.desc(),
            TopicAnalysisJob.id.desc(),
        )
    )


def latest_analyses(session, identifiers):
    if not identifiers:
        return {}
    jobs = session.scalars(
        latest_analysis_statement()
        .where(TopicAnalysisJob.proposal_id.in_(identifiers))
        .options(
            load_only(
                TopicAnalysisJob.proposal_id,
                TopicAnalysisJob.result,
                *(
                    getattr(TopicAnalysisJob, name)
                    for name in proposals.TopicAnalysisOut.model_fields
                    if name != "reasons"
                ),
                raiseload=True,
            )
        )
    )
    return {job.proposal_id: job for job in jobs}


@router.post(
    "/topic-proposals/analysis",
    response_model=TopicAnalysisBatchOut,
    status_code=202,
    operation_id="admin_topic_proposals_analyze_all",
)
def analyze_all(session: DB, admin: Admin):
    if not get_settings().ai_enabled:
        raise HTTPException(503, "AI analysis is disabled. Start the AI services first")
    result = request_all_topic_analysis(session, actor(admin))
    session.commit()
    return result


@router.get(
    "/topic-proposals/{proposal_id}",
    response_model=proposals.TopicProposalOut,
    operation_id="admin_topic_proposal_get",
)
def detail(proposal_id: uuid.UUID, session: DB):
    return proposals.proposal_view(
        record(session, TopicProposal, proposal_id),
        latest_analyses(session, [proposal_id]).get(proposal_id),
    )


@router.post(
    "/topic-proposals/{proposal_id}/review",
    response_model=proposals.TopicProposalOut,
    operation_id="admin_topic_proposal_review",
)
def review(proposal_id: uuid.UUID, body: proposals.TopicReview, session: DB, admin: Admin):
    value = proposals.review_proposal(session, proposal_id, body, actor(admin))
    session.commit()
    return proposals.proposal_view(value)


@router.delete(
    "/topic-proposals/{proposal_id}",
    status_code=204,
    operation_id="admin_topic_proposal_delete",
)
def remove(
    proposal_id: uuid.UUID,
    session: DB,
    expected_input_hash: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
    expected_status: Literal["pending", "approved", "rejected"],
):
    # Match review's lock order. The proposal lock also serializes new AI requests.
    # Read active jobs without locking them: workers lock job before proposal.
    lock_topics(session)
    proposal = record(session, TopicProposal, proposal_id, lock=True)
    if (
        proposal.status != expected_status
        or snapshot_hash(proposal.proposed) != expected_input_hash
    ):
        raise proposals.OperationConflict("Proposal changed. Reload it before deleting")
    active = session.scalar(
        select(TopicAnalysisJob.id)
        .where(
            TopicAnalysisJob.proposal_id == proposal_id,
            TopicAnalysisJob.status.in_(["queued", "running"]),
        )
        .limit(1)
    )
    if active:
        raise proposals.OperationConflict("Wait for AI analysis to finish before deleting")
    # Completed research is owned by the proposal and cascades with it. Any approved
    # topic remains in the catalog; removing a proposal never removes that topic.
    session.execute(delete(TopicProposal).where(TopicProposal.id == proposal_id))
    session.commit()
    return Response(status_code=204)


@router.post(
    "/topics/{topic_id}/enrichment/preview",
    response_model=proposals.TopicEnrichmentPreview,
    operation_id="admin_topic_enrichment_preview",
)
def preview_enrichment(topic_id: uuid.UUID, session: DB):
    return proposals.preview_enrichment(session, topic_id)


@router.post(
    "/topics/{topic_id}/enrichment",
    response_model=proposals.TopicProposalOut,
    status_code=201,
    operation_id="admin_topic_enrichment_submit",
)
def submit_enrichment(
    topic_id: uuid.UUID, body: proposals.TopicEnrichmentSubmit, session: DB, admin: Admin
):
    value = proposals.submit_enrichment(session, topic_id, body, actor(admin))
    session.commit()
    return proposals.proposal_view(value)


@router.post(
    "/topic-discovery/github",
    response_model=github_topics.GitHubPullResult,
    operation_id="admin_topic_github_pull",
)
def github_pull(body: github_topics.GitHubPull, session: DB, admin: Admin):
    try:
        result = github_topics.pull_topics(session, body, actor(admin))
    except github_topics.GitHubUnavailable as exc:
        raise HTTPException(503, {"code": "github_unavailable", "message": str(exc)}) from exc
    session.commit()
    return result


@router.post(
    "/topic-proposals/{proposal_id}/analysis",
    response_model=AdminJobOut,
    status_code=202,
    operation_id="admin_topic_proposal_analyze",
)
def analyze(proposal_id: uuid.UUID, session: DB, admin: Admin):
    if not get_settings().ai_enabled:
        raise HTTPException(
            503, "AI analysis is disabled. Start Compose with --ai and sign in to Codex"
        )
    job = request_topic_analysis(session, proposal_id, actor(admin))
    session.commit()
    return job_view(job, "topic-analysis")
