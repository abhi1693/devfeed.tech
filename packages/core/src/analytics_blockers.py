"""Exact bounded operator blocker classification over authoritative evidence.

Only selected identities are evaluated. This module owns no serving table or
request route; private evidence never leaves the background classification step.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Literal, Protocol

from pydantic import BaseModel
from sqlalchemy import String, and_, case, cast, false, func, literal, or_, select, true, union_all
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import Session

from devfeed_core.ai_content import eligible_articles
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleOrigin,
    ArticleTopic,
    Source,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
)
from devfeed_core.research_evidence import VERIFICATION_VERSION


class RecoveryTarget(BaseModel):
    id: uuid.UUID
    title: str
    kind: Literal["article", "topic-proposal", "relationship-proposal"]
    revision: int = 0


class AutomationBlocker(BaseModel):
    code: str
    label: str
    count: int
    action: Literal["enrich", "analyze", "evaluate"] | None = None
    targets: list[RecoveryTarget]


class BlockerTarget(Protocol):
    """Columns emitted by the bounded blocker target projection."""

    id: uuid.UUID
    title: str
    editorial_revision: int
    total: int


def automation_blockers(
    session: Session,
    article_ids: Iterable[uuid.UUID],
    *,
    topic_ids: Iterable[uuid.UUID] = (),
    relationship_ids: Iterable[uuid.UUID] = (),
    include_proposals: bool = True,
    target_limit: int = 5,
) -> list[AutomationBlocker]:
    if article_ids is None:
        raise ValueError("Article blocker classification requires explicit bounded IDs")
    article_ids = list(dict.fromkeys(article_ids))
    topic_ids = list(dict.fromkeys(topic_ids))
    relationship_ids = list(dict.fromkeys(relationship_ids))
    if max(len(article_ids), len(topic_ids), len(relationship_ids)) > 200:
        raise ValueError("Blocker classification batches must contain at most 200 IDs per kind")
    if not 0 <= target_limit <= 200:
        raise ValueError("Blocker classification target limit must be between 0 and 200")
    full = get_settings().full_automation
    pending = (Article.review_status == "pending", Article.publication_status == "unpublished")
    primary_topic = (
        select(ArticleTopic.article_id)
        .join(Topic, ArticleTopic.topic_id == Topic.id)
        .where(
            ArticleTopic.article_id == Article.id,
            ArticleTopic.role == "primary",
            Topic.status == "active",
        )
        .exists()
    )
    content = case(
        (
            (ArticleContent.method == "description")
            & (func.length(Article.summary) > func.length(ArticleContent.text)),
            Article.summary,
        ),
        else_=func.coalesce(func.nullif(ArticleContent.text, ""), Article.summary),
    )
    # Match editorial.meaningful_text without relying on the database locale.
    # PostgreSQL's POSIX alpha class is ASCII-only in production, so it marks
    # otherwise valid Cyrillic and other non-ASCII article text as unreadable.
    # This excludes whitespace, punctuation, and digits while retaining letters
    # from those scripts. Repeating the broad expression forty times was also
    # costly on long bodies.
    readable_text = content.bool_op("~")("[^[:space:][:punct:][:digit:]]")
    # Keep JSON extraction outside the ordered LIMIT. Extracting the policy here
    # detoasts every historical result before sorting, even though only the
    # newest run contributes to the dashboard.
    latest = (
        select(
            ArticleAnalysisJob.status,
            ArticleAnalysisJob.outcome,
            ArticleAnalysisJob.result,
        )
        .where(ArticleAnalysisJob.article_id == Article.id)
        .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id.desc())
        .limit(1)
        .correlate(Article)
        .lateral()
    )
    latest_enrichment = (
        select(ArticleEnrichmentJob.status, ArticleEnrichmentJob.result)
        .where(ArticleEnrichmentJob.article_id == Article.id)
        .order_by(ArticleEnrichmentJob.created_at.desc(), ArticleEnrichmentJob.id.desc())
        .limit(1)
        .correlate(Article)
        .lateral()
    )
    # Materialize the expensive text/evidence checks once per pending article.
    # Each blocker can overlap another; rank within each group before limiting
    # recovery targets so counts stay exact and the response stays bounded.
    flags = (
        select(
            Article.id,
            Article.title,
            Article.editorial_revision,
            Article.discovered_at,
            func.coalesce(eligible_articles(), false()).label("date_eligible"),
            select(ArticleOrigin.id)
            .join(Source, Source.id == ArticleOrigin.source_id)
            .where(
                ArticleOrigin.article_id == Article.id,
                Source.approval_status == "approved",
                Source.enabled.is_(True),
            )
            .exists()
            .label("source_eligible"),
            readable_text.label("readable"),
            func.coalesce(
                (latest_enrichment.c.status == "succeeded")
                & latest_enrichment.c.result["paywalled"].as_boolean(),
                false(),
            ).label("paywalled"),
            latest_enrichment.c.status.label("enrichment_status"),
            select(ArticleEnrichmentJob.id)
            .where(
                ArticleEnrichmentJob.article_id == Article.id,
                ArticleEnrichmentJob.status.in_(["queued", "running"]),
            )
            .exists()
            .label("enriching"),
            primary_topic.label("primary"),
            func.coalesce(latest.c.status == "failed", false()).label("failed"),
            latest.c.status.label("analysis_status"),
            latest.c.outcome.label("analysis_outcome"),
            latest.c.result["developer_relevance"].astext.label("relevance"),
            latest.c.result["page_kind"].astext.label("page_kind"),
            latest_enrichment.c.result["text_source"].astext.label("text_source"),
            latest.c.result["topic_match_status"].astext.label("topic_match_status"),
            func.coalesce(
                func.jsonb_path_exists(latest.c.result, cast("$.topics[*]", JSONPATH)), false()
            ).label("has_analysis_topics"),
            func.coalesce(
                latest.c.result["publication_policy"]["status"].astext == "would_publish", false()
            ).label("preview"),
        )
        .outerjoin(ArticleContent, ArticleContent.article_id == Article.id)
        .outerjoin(latest, true())
        .outerjoin(latest_enrichment, true())
        .where(*pending, Article.id.in_(article_ids))
        .cte("pending_article_flags")
        .prefix_with("MATERIALIZED")
    )
    readable, primary, failed, preview = (
        flags.c.readable,
        flags.c.primary,
        flags.c.failed,
        flags.c.preview,
    )
    enrichment_failed = flags.c.enrichment_status == "failed"
    analysis_pending = or_(
        flags.c.analysis_status.is_(None), flags.c.analysis_status.in_(("queued", "running"))
    )
    analysis_succeeded = flags.c.analysis_status == "succeeded"
    negative = func.coalesce(
        analysis_succeeded
        & or_(flags.c.relevance == "unrelated", flags.c.page_kind == "non_article"),
        false(),
    )
    relevant = flags.c.relevance == "relevant"
    # An applied analysis can outlive its active topic links. Classify the
    # current missing topic/primary directly from authoritative facts.
    applied_without_topics = and_(
        ~primary, flags.c.analysis_outcome == "applied", ~flags.c.has_analysis_topics
    )
    applied_without_primary = and_(
        ~primary, flags.c.analysis_outcome == "applied", flags.c.has_analysis_topics
    )
    topic_not_matched = or_(flags.c.topic_match_status == "no_topic_match", applied_without_topics)
    topic_without_primary = or_(
        flags.c.topic_match_status == "no_primary_topic", applied_without_primary
    )
    no_failed_enrichment = or_(
        flags.c.enrichment_status.is_(None), flags.c.enrichment_status != "failed"
    )
    definitions = (
        (
            "paywalled_content",
            "Paywalled articles awaiting rejection",
            flags.c.paywalled,
            None,
        ),
        (
            "negative_classification",
            "Unrelated or non-article content awaiting decision",
            negative,
            "evaluate",
        ),
        (
            "content_date_deferred",
            "Outside the configured content date window",
            ~flags.c.date_eligible,
            None,
        ),
        (
            "source_ineligible",
            "No enabled, approved source",
            ~flags.c.source_eligible,
            None,
        ),
        (
            "awaiting_enrichment",
            "Awaiting article text extraction",
            ~readable & flags.c.enriching,
            None,
        ),
        (
            "enrichment_failed",
            "Article enrichment failed",
            ~readable & ~flags.c.enriching & enrichment_failed,
            "enrich",
        ),
        (
            "insufficient_text",
            "Insufficient article text",
            ~readable & ~flags.c.enriching & no_failed_enrichment,
            "enrich",
        ),
        (
            "analysis_pending",
            "Analysis queued or in progress",
            readable & analysis_pending,
            None,
        ),
        ("analysis_failed", "Failed article analysis", readable & failed, "analyze"),
        (
            "topic_not_matched",
            "Relevant article, no topic matched",
            readable & analysis_succeeded & relevant & topic_not_matched,
            None,
        ),
        (
            "topic_without_primary",
            "Relevant article, topic match has no primary",
            readable & analysis_succeeded & relevant & topic_without_primary,
            None,
        ),
        (
            "incomplete_extraction",
            "Only a page description is available",
            readable
            & analysis_succeeded
            & (flags.c.analysis_outcome == "insufficient_evidence")
            & (flags.c.text_source == "description")
            & ~negative,
            "enrich",
        ),
        (
            "analysis_insufficient_evidence",
            "Analysis needs more evidence",
            readable
            & analysis_succeeded
            & (flags.c.analysis_outcome == "insufficient_evidence")
            & flags.c.topic_match_status.is_(None)
            & flags.c.text_source.is_distinct_from("description")
            & ~negative,
            None,
        ),
        (
            "analysis_relevance_review",
            "Article relevance needs review",
            ~primary
            & readable
            & analysis_succeeded
            & flags.c.analysis_outcome.is_distinct_from("insufficient_evidence")
            & flags.c.relevance.is_distinct_from("relevant")
            & ~negative,
            "evaluate",
        ),
        ("publication_preview", "Ready in publication preview", preview, "evaluate"),
        (
            "editorial_review",
            "Publication policy needs review" if full else "Classified articles awaiting review",
            primary
            & readable
            & ~preview
            & ~failed
            & ~negative
            & analysis_succeeded
            & flags.c.analysis_outcome.is_distinct_from("insufficient_evidence"),
            "evaluate",
        ),
    )
    # Preserve visibility of excluded records, but don't advertise recovery work
    # the scheduler is prohibited from performing. Paywalls are an access policy.
    scoped_definitions = []
    for code, label, condition, action in definitions:
        if code != "paywalled_content":
            condition &= ~flags.c.paywalled
            if code not in {"content_date_deferred", "source_ineligible"}:
                condition &= flags.c.date_eligible & flags.c.source_eligible
        scoped_definitions.append((code, label, condition, action))
    matches = union_all(
        *(
            select(
                flags.c.id,
                flags.c.title,
                flags.c.editorial_revision,
                flags.c.discovered_at,
                literal(code).label("code"),
            ).where(condition)
            for code, _, condition, _ in scoped_definitions
        )
    ).subquery()
    ranked = select(
        matches,
        func.count().over(partition_by=matches.c.code).label("total"),
        func.row_number()
        .over(partition_by=matches.c.code, order_by=(matches.c.discovered_at, matches.c.id))
        .label("position"),
    ).subquery()
    targets: dict[str, list[BlockerTarget]] = {}
    for row in (
        ()
        if not article_ids
        else session.execute(
            select(ranked)
            .where(ranked.c.position <= target_limit)
            .order_by(ranked.c.code, ranked.c.position)
        )
    ):
        targets.setdefault(row.code, []).append(row)
    blockers = []
    for code, label, _, action in scoped_definitions:
        rows = targets.get(code, [])
        blockers.append(
            AutomationBlocker(
                code=code,
                label=label,
                count=rows[0].total if rows else 0,
                action=action,
                targets=[
                    RecoveryTarget(
                        id=row.id, title=row.title, kind="article", revision=row.editorial_revision
                    )
                    for row in rows
                ],
            )
        )
    if not include_proposals:
        return blockers
    # Select one latest result per pending proposal before evaluating large JSON.
    # A correlated EXISTS against the entire job table repeats the latest-job
    # lookup for each candidate and can time out as history grows.
    latest_topic = (
        select(TopicAnalysisJob.result)
        .where(TopicAnalysisJob.proposal_id == TopicProposal.id)
        .order_by(TopicAnalysisJob.created_at.desc(), TopicAnalysisJob.id.desc())
        .limit(1)
        .correlate(TopicProposal)
        .lateral()
    )
    unverified = or_(
        func.jsonb_path_exists(
            latest_topic.c.result,
            cast('$.evidence_verification.checks.* ? (@.status == "unverified")', JSONPATH),
        ),
        latest_topic.c.result["topic_verification"]["check"]["verdict"].astext.in_(
            ["unsupported", "uncertain"]
        ),
        func.jsonb_path_exists(
            latest_topic.c.result,
            cast("$.topic_verification.check.fields[*] ? (@.supported == false)", JSONPATH),
        ),
        func.jsonb_path_exists(
            latest_topic.c.result,
            cast(
                "$.topic_verification.check.aliases[*] ? (@.same_identity == false)",
                JSONPATH,
            ),
        ),
    )
    query = (
        select(
            TopicProposal.id,
            TopicProposal.proposed,
            func.count().over().label("total"),
        )
        .join(latest_topic, true())
        .where(TopicProposal.id.in_(topic_ids), TopicProposal.status == "pending", unverified)
        .order_by(TopicProposal.created_at, TopicProposal.id)
        .limit(target_limit)
    )
    proposal_rows = session.execute(query).all() if topic_ids else []
    blockers.append(
        AutomationBlocker(
            code="evidence_unverified",
            label="Topic research in progress"
            if full
            else "Research evidence or identity needs review",
            count=proposal_rows[0].total if proposal_rows else 0,
            targets=[
                RecoveryTarget(
                    id=row.id,
                    title=row.proposed.get("name", "Unnamed topic"),
                    kind="topic-proposal",
                )
                for row in proposal_rows
            ],
        )
    )
    relation_query = (
        select(TopicRelationProposal)
        .join(TopicAnalysisJob, TopicRelationProposal.job_id == TopicAnalysisJob.id)
        .where(
            TopicRelationProposal.id.in_(relationship_ids),
            TopicRelationProposal.status == "pending",
            or_(
                func.jsonb_path_exists(
                    TopicAnalysisJob.result,
                    cast(
                        "$.relationship_verification.checks.* ? (@.proposal_id == $id "
                        '&& (@.verdict != "supported" || @.scope_matches == false))',
                        JSONPATH,
                    ),
                    func.jsonb_build_object("id", cast(TopicRelationProposal.id, String)),
                ),
                TopicAnalysisJob.result["evidence_verification"]["version"].astext.is_distinct_from(
                    VERIFICATION_VERSION
                ),
                ~func.jsonb_path_exists(
                    TopicAnalysisJob.result,
                    cast(
                        '$.evidence_verification.checks.* ? (@.status == "verified" '
                        "&& @.url == $url && @.quote == $quote)",
                        JSONPATH,
                    ),
                    func.jsonb_build_object(
                        "url",
                        TopicRelationProposal.evidence_url,
                        "quote",
                        TopicRelationProposal.evidence_quote,
                    ),
                ),
            ),
        )
    )
    relation_count = (
        (session.scalar(select(func.count()).select_from(relation_query.subquery())) or 0)
        if relationship_ids
        else 0
    )
    relation_rows: Iterable[TopicRelationProposal] = (
        []
        if not relationship_ids
        else session.scalars(
            relation_query.order_by(
                TopicRelationProposal.created_at, TopicRelationProposal.id
            ).limit(target_limit)
        )
    )
    blockers.append(
        AutomationBlocker(
            code="relationship_evidence_unverified",
            label="Relationship verification in progress"
            if full
            else "Relationship evidence or meaning needs review",
            count=relation_count,
            targets=[
                RecoveryTarget(
                    id=row.id,
                    title=(
                        f"{row.topic_snapshot.get('name', 'Unnamed topic')} → "
                        f"{row.related_topic_snapshot.get('name', 'Unnamed topic')}"
                    ),
                    kind="relationship-proposal",
                )
                for row in relation_rows
            ],
        )
    )
    return blockers


def automation_blocker_rows(
    session: Session, article_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, str | None]]]:
    """Compact exact blocker classifications for one bounded native observation.

    The native publisher stores only codes beside current card metadata. Serving
    can aggregate them without loading historical article bodies or AI JSON.
    """
    identifiers = list(article_ids)
    if len(identifiers) > 200:
        raise ValueError("Blocker observation batches must contain at most 200 articles")
    output: dict[uuid.UUID, list[dict[str, str | None]]] = {
        identifier: [] for identifier in identifiers
    }
    if not identifiers:
        return output
    for blocker in automation_blockers(
        session, identifiers, include_proposals=False, target_limit=len(identifiers)
    ):
        for target in blocker.targets:
            output[target.id].append(
                {"code": blocker.code, "label": blocker.label, "action": blocker.action}
            )
    return output


def blocker_catalog(*, full_automation: bool | None = None) -> list[AutomationBlocker]:
    """Return all safe labels/actions without querying authoritative records."""
    full = get_settings().full_automation if full_automation is None else full_automation
    definitions = (
        ("paywalled_content", "Paywalled articles awaiting rejection", None),
        (
            "negative_classification",
            "Unrelated or non-article content awaiting decision",
            "evaluate",
        ),
        ("content_date_deferred", "Outside the configured content date window", None),
        ("source_ineligible", "No enabled, approved source", None),
        ("awaiting_enrichment", "Awaiting article text extraction", None),
        ("enrichment_failed", "Article enrichment failed", "enrich"),
        ("insufficient_text", "Insufficient article text", "enrich"),
        ("analysis_pending", "Analysis queued or in progress", None),
        ("analysis_failed", "Failed article analysis", "analyze"),
        ("topic_not_matched", "Relevant article, no topic matched", None),
        ("topic_without_primary", "Relevant article, topic match has no primary", None),
        ("incomplete_extraction", "Only a page description is available", "enrich"),
        ("analysis_insufficient_evidence", "Analysis needs more evidence", None),
        ("analysis_relevance_review", "Article relevance needs review", "evaluate"),
        ("publication_preview", "Ready in publication preview", "evaluate"),
        (
            "editorial_review",
            "Publication policy needs review" if full else "Classified articles awaiting review",
            "evaluate",
        ),
    )
    result = [
        AutomationBlocker(code=code, label=label, action=action, count=0, targets=[])
        for code, label, action in definitions
    ]
    result.extend(
        [
            AutomationBlocker(
                code="evidence_unverified",
                label="Topic research in progress"
                if full
                else "Research evidence or identity needs review",
                count=0,
                targets=[],
            ),
            AutomationBlocker(
                code="relationship_evidence_unverified",
                label="Relationship verification in progress"
                if full
                else "Relationship evidence or meaning needs review",
                count=0,
                targets=[],
            ),
        ]
    )
    return result
