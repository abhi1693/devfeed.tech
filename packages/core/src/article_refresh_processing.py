"""Explicit private job subjects and replacement inputs for published articles."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session, defer, lazyload, sessionmaker

from devfeed_core.analysis import (
    PROMPT_VERSION,
    AnalysisCatalog,
    SourceSnapshot,
    _ensure_primary_topic,
    analysis_candidates,
    analysis_catalog_current,
    catalog,
    snapshot_hash,
)
from devfeed_core.article_lifecycle import ProcessingFacts, ProcessingInputs
from devfeed_core.article_refresh import (
    ACTIVE_REFRESH_STATES,
    PreparedRefreshBase,
    select_refresh_job,
)
from devfeed_core.article_refresh_types import RefreshDraft, RefreshTag, RefreshTopic
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleRefresh,
    ArticleTopic,
    Tag,
    Topic,
    utcnow,
)
from devfeed_core.services import OperationConflict, RecordNotFound


@dataclass(frozen=True)
class RefreshInputs:
    refresh_id: uuid.UUID
    version: int
    public: Article
    article: Article
    draft: RefreshDraft
    facts: ProcessingFacts
    taxonomy: AnalysisCatalog
    snapshot: SourceSnapshot
    input_hash: str
    catalog_hash: str
    candidates: AnalysisCatalog


def candidate_snapshot(article: Article, draft: RefreshDraft) -> SourceSnapshot:
    content = draft.content
    use_content = bool(
        content
        and content.text
        and not (content.method == "description" and len(draft.fields.summary) > len(content.text))
    )
    return {
        "url": article.canonical_url,
        "title": draft.fields.title,
        "source_summary": draft.fields.summary,
        "text": content.text if content and use_content else draft.fields.summary,
        "text_source": content.method
        if content and use_content
        else draft.fields.metadata_source_type,
    }


def candidate_assignments(draft: RefreshDraft) -> tuple[list[RefreshTopic], list[RefreshTag]]:
    """Match the live AI replacement policy while preserving explicit assignments."""
    result = draft.analysis
    if result is None or result.outcome != "ready":
        return list(draft.topics), list(draft.tags)
    topics = {item.topic_id: item for item in draft.topics if item.origin == "manual"}
    for item in _ensure_primary_topic(result.developer_relevance, result.topics):
        topics.setdefault(
            item.topic_id,
            RefreshTopic(**item.model_dump(), origin="ai"),
        )
    tags = {item.tag_id: item for item in draft.tags if item.origin in {"manual", "source"}}
    for label in result.tags:
        tags.setdefault(label.id, RefreshTag(tag_id=label.id, origin="ai"))
    return sorted(topics.values(), key=lambda item: item.topic_id), sorted(
        tags.values(), key=lambda item: item.tag_id
    )


def candidate_view(
    session: Session,
    public: Article,
    owner: ArticleRefresh,
    draft: RefreshDraft,
    facts: ProcessingFacts,
) -> Article:
    """A transient view evaluates private facts; it is never attached or flushed."""
    values = {column.name: getattr(public, column.name) for column in Article.__table__.columns}
    values.update(draft.fields.model_dump())
    values.update(
        publication_status="unpublished",
        review_status="approved",
        image_url=owner.image_url,
        managed_image=dict(owner.managed_image or {}),
    )
    article = Article(**values)
    article.origins = list(public.origins)
    topics, tags = candidate_assignments(draft)
    topic_objects = {
        item.id: item
        for item in session.scalars(
            select(Topic).where(Topic.id.in_([item.topic_id for item in topics]))
        )
    }
    article.topic_links = [
        ArticleTopic(article_id=article.id, **item.model_dump(), topic=topic_objects[item.topic_id])
        for item in topics
        if item.topic_id in topic_objects
    ]
    article.tags = list(
        session.scalars(select(Tag).where(Tag.id.in_([item.tag_id for item in tags])))
    )
    result, job = draft.analysis, facts["analysis"]
    if result is not None and result.outcome == "ready" and job is not None:
        article.ai_title = result.ai_title
        article.ai_summary, article.ai_description = result.ai_summary, result.ai_description
        article.language = result.language
        article.content_type = cast(str, result.content_type)
        article.content_format = cast(str, result.content_format)
        generation = {
            "analysis_id": str(job.id),
            "model": job.model,
            "prompt_version": job.prompt_version,
            "input_hash": job.input_hash,
        }
        article.classification_provenance = {
            "origin": "ai",
            **generation,
            "ai_generation": generation,
            "generated_at": job.finished_at.isoformat() if job.finished_at else None,
            "developer_relevance": result.developer_relevance,
            "page_kind": result.page_kind,
            "title_evidence": result.title_evidence,
            "tag_ids": [str(item.id) for item in result.tags],
        }
    return article


def refresh_facts(
    session: Session, owner: ArticleRefresh, draft: RefreshDraft, *, lightweight: bool = False
) -> ProcessingFacts:
    analysis_options = (
        (defer(ArticleAnalysisJob.input_snapshot), defer(ArticleAnalysisJob.catalog_snapshot))
        if lightweight
        else ()
    )
    facts: ProcessingFacts = {
        "content": ArticleContent(article_id=owner.article_id, **draft.content.model_dump())
        if draft.content
        else None,
        "enrichment": session.get(ArticleEnrichmentJob, owner.enrichment_job_id)
        if owner.enrichment_job_id
        else None,
        "analysis": session.get(ArticleAnalysisJob, owner.analysis_job_id, options=analysis_options)
        if owner.analysis_job_id
        else None,
        "discovery": session.get(ArticleImageJob, owner.discovery_job_id)
        if owner.discovery_job_id
        else None,
        "storage": session.get(ArticleImageJob, owner.storage_job_id)
        if owner.storage_job_id
        else None,
    }
    for slot in ("enrichment", "analysis", "discovery", "storage"):
        job = facts[slot]
        if job is None:
            continue
        if job.article_id != owner.article_id:
            raise OperationConflict("Private maintenance references another article's task")
        if (
            slot in {"discovery", "storage"}
            and isinstance(job, ArticleImageJob)
            and job.operation != ("discover" if slot == "discovery" else "store")
        ):
            raise OperationConflict("Private maintenance references the wrong image operation")
        if job.refresh_id not in {None, owner.id}:
            previous = session.get(ArticleRefresh, job.refresh_id)
            if (
                previous is None
                or previous.status != "promoted"
                or previous.article_id != owner.article_id
                or getattr(previous, f"{slot}_job_id") != job.id
            ):
                raise OperationConflict("Private maintenance references an unapproved task owner")
    return facts


def prepare_refresh_inputs(session: Session, refresh_id: uuid.UUID) -> RefreshInputs:
    """Caller has not acquired business write locks; body/ranking stay outside them."""
    owner = session.get(ArticleRefresh, refresh_id)
    if owner is None or owner.status not in ACTIVE_REFRESH_STATES or owner.payload_pruned_at:
        raise OperationConflict("This maintenance candidate is no longer available")
    public = session.get(Article, owner.article_id)
    if public is None:
        raise RecordNotFound("Article not found")
    draft = RefreshDraft.model_validate(owner.payload)
    facts = refresh_facts(session, owner, draft)
    article = candidate_view(session, public, owner, draft, facts)
    snapshot = candidate_snapshot(public, draft)
    taxonomy = catalog(session)
    candidates = analysis_candidates(taxonomy, snapshot)
    return RefreshInputs(
        owner.id,
        owner.version,
        public,
        article,
        draft,
        facts,
        taxonomy,
        snapshot,
        snapshot_hash(snapshot),
        snapshot_hash(candidates),
        candidates,
    )


def prepare_initial_refresh_inputs(
    session: Session,
    public: Article,
    base: PreparedRefreshBase,
    facts: ProcessingFacts,
    taxonomy: AnalysisCatalog | None = None,
) -> RefreshInputs:
    owner = ArticleRefresh(
        id=base.refresh_id,
        article_id=base.article_id,
        base_revision=base.revision,
        version=1,
        image_url=base.image_url,
        managed_image=base.managed_image,
    )
    article = candidate_view(session, public, owner, base.draft, facts)
    snapshot = candidate_snapshot(public, base.draft)
    current = taxonomy if taxonomy is not None else catalog(session)
    candidates = analysis_candidates(current, snapshot)
    return RefreshInputs(
        owner.id,
        1,
        public,
        article,
        base.draft,
        facts,
        current,
        snapshot,
        snapshot_hash(snapshot),
        snapshot_hash(candidates),
        candidates,
    )


def request_refresh_analysis(
    session: Session,
    refresh_id: uuid.UUID,
    *,
    prepared: RefreshInputs | None = None,
    expected_article_id: uuid.UUID | None = None,
) -> ArticleAnalysisJob:
    """Admit a version-bound analysis without mutating its approved Article."""
    from devfeed_core.article_jobs import approved_sources
    from devfeed_core.article_refresh import refresh_for_article

    inputs = prepared or prepare_refresh_inputs(session, refresh_id)
    if expected_article_id is not None and inputs.public.id != expected_article_id:
        raise OperationConflict("Maintenance task does not match the requested article")
    if not get_settings().ai_enabled:
        raise OperationConflict("Analysis is not configured")
    if not approved_sources(session, inputs.public.id, lock=True):
        raise OperationConflict("Article maintenance requires an approved source")
    public = session.scalar(
        select(Article)
        .options(lazyload("*"))
        .where(Article.id == inputs.public.id)
        .with_for_update(of=Article)
        .execution_options(populate_existing=True)
    )
    if public is None:
        raise RecordNotFound("Article not found")
    owner = refresh_for_article(session, public, refresh_id, lock=True)
    if owner is None or owner.version != inputs.version or owner.source_hash != inputs.input_hash:
        raise OperationConflict("Maintenance inputs changed; reload before retrying")
    active = session.scalar(
        select(ArticleAnalysisJob).where(
            ArticleAnalysisJob.article_id == public.id,
            ArticleAnalysisJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        if active.refresh_id != owner.id or active.id != owner.analysis_job_id:
            raise OperationConflict("Other analysis is still running for this article")
        return active
    job = ArticleAnalysisJob(
        article_id=public.id,
        refresh_id=owner.id,
        editorial_revision=owner.base_revision,
        input_snapshot=dict(inputs.snapshot),
        input_hash=inputs.input_hash,
        catalog_snapshot=inputs.candidates,
        catalog_hash=inputs.catalog_hash,
        model=get_settings().codex_model,
        prompt_version=PROMPT_VERSION,
        usage={"requested_reason": "published_maintenance"},
    )
    session.add(job)
    session.flush()
    select_refresh_job(owner, "analysis", job)
    return job


def resume_refresh(factory: sessionmaker[Session], refresh_id: uuid.UUID) -> str:
    """Resume only this durable owner; failed work still requires an explicit retry.

    Inspection and ranking happen before admission locks. Each admission rechecks
    the selected version and subject authority; the scheduler retries a lost CAS.
    """
    from devfeed_core.analysis import reuse_candidates
    from devfeed_core.article_jobs import approved_sources
    from devfeed_core.article_lifecycle import derive_publication_readiness
    from devfeed_core.article_refresh import try_promote_refresh
    from devfeed_core.catalog_cache import reuse_snapshots
    from devfeed_core.image_jobs import request_image

    with factory() as session:
        owner = session.scalar(
            select(ArticleRefresh)
            .options(defer(ArticleRefresh.payload, raiseload=True))
            .where(ArticleRefresh.id == refresh_id)
        )
        if owner is None:
            return "missing"
        if owner.status not in ACTIVE_REFRESH_STATES:
            return owner.status
        article_id, target, version = owner.article_id, owner.target, owner.version
        jobs = [
            session.get(ArticleEnrichmentJob, owner.enrichment_job_id)
            if owner.enrichment_job_id
            else None,
            session.get(ArticleAnalysisJob, owner.analysis_job_id)
            if owner.analysis_job_id
            else None,
            session.get(ArticleImageJob, owner.discovery_job_id)
            if owner.discovery_job_id
            else None,
            session.get(ArticleImageJob, owner.storage_job_id) if owner.storage_job_id else None,
        ]
        if any(
            job is not None and job.refresh_id == refresh_id and job.status in {"queued", "running"}
            for job in jobs
        ):
            return "pending"
        public = session.get(Article, article_id)
        current = bool(
            public is not None
            and public.publication_status == "published"
            and public.review_status == "approved"
            and public.editorial_revision == owner.base_revision
            and approved_sources(session, article_id)
        )
    if not current:
        # Never acquire Source/Article while retaining the scheduler's owner lock.
        with factory.begin() as session:
            approved_sources(session, article_id, lock=True)
            public = session.scalar(
                select(Article).where(Article.id == article_id).with_for_update()
            )
            owner = session.scalar(
                select(ArticleRefresh)
                .where(ArticleRefresh.id == refresh_id)
                .options(defer(ArticleRefresh.payload, raiseload=True))
                .with_for_update()
            )
            if (
                owner is not None
                and owner.status in ACTIVE_REFRESH_STATES
                and owner.version == version
                and (
                    public is None
                    or public.publication_status != "published"
                    or public.review_status != "approved"
                    or public.editorial_revision != owner.base_revision
                    or not approved_sources(session, article_id)
                )
            ):
                owner.status, owner.completed_at = "superseded", utcnow()
                owner.version += 1
                return owner.status
        return "pending"
    if target == "images":
        return try_promote_refresh(factory, refresh_id)
    with reuse_snapshots(), reuse_candidates():
        with factory() as session:
            inputs = prepare_refresh_inputs(session, refresh_id)
            current_inputs: ProcessingInputs = {**inputs.facts, "taxonomy": inputs.taxonomy}
            readiness = derive_publication_readiness(inputs.article, **current_inputs)
        primary = inputs.facts["enrichment" if target == "enrichment" else "analysis"]
        if primary is None or primary.refresh_id != refresh_id or primary.status != "succeeded":
            return "pending"
        if readiness.checks["enrichment"].status != "complete":
            return "blocked"
        if readiness.checks["analysis"].status != "complete":
            previous = inputs.facts["analysis"]
            if (
                previous is not None
                and previous.refresh_id == refresh_id
                and previous.input_hash == inputs.input_hash
                and (
                    readiness.checks["analysis"].status == "unavailable"
                    or previous.status == "failed"
                    and analysis_catalog_current(previous, inputs.taxonomy, inputs.snapshot)
                )
            ):
                return "blocked"
            if {
                "paywalled_content",
                "insufficient_source_text",
                "content_date_deferred",
            }.intersection(readiness.blockers):
                return "blocked"
            with factory.begin() as session:
                request_refresh_analysis(session, refresh_id, prepared=inputs)
            return "analysis_queued"
        images = readiness.checks["images"]
        if images.status not in {"complete", "unavailable"}:
            if images.status == "failed":
                return "blocked"
            with factory.begin() as session:
                queued = request_image(session, article_id, automatic=True, refresh_id=refresh_id)
            return "images_queued" if queued is not None else "blocked"
        return try_promote_refresh(factory, refresh_id)


def reconcile_refreshes(
    factory: sessionmaker[Session], limit: int, now: datetime | None = None
) -> int:
    """Claim a bounded due page, commit it, then resume without owner-lock inversion."""
    import logging

    if not 1 <= limit <= 500:
        raise ValueError("Maintenance reconciliation limit must be between 1 and 500")
    now = now or utcnow()
    with factory.begin() as session:
        owners = session.scalars(
            select(ArticleRefresh)
            .options(defer(ArticleRefresh.payload, raiseload=True))
            .where(
                # Keep this fixed predicate visible to generic prepared plans,
                # so terminal history cannot hide the partial due-page index.
                ArticleRefresh.status.in_((literal_column("'active'"), literal_column("'review'"))),
                ArticleRefresh.next_check_at <= now,
            )
            .order_by(ArticleRefresh.next_check_at, ArticleRefresh.id)
            .limit(min(limit, 20))
            .with_for_update(of=ArticleRefresh, skip_locked=True)
        ).all()
        identifiers = [owner.id for owner in owners]
        for owner in owners:
            owner.next_check_at = now + timedelta(minutes=5)
    for identifier in identifiers:
        try:
            resume_refresh(factory, identifier)
        except OperationConflict:
            pass  # The durable next_check_at retries current inputs, never stale output.
        except Exception:
            logging.getLogger(__name__).exception(
                "article_maintenance_reconciliation_failed", extra={"refresh_id": str(identifier)}
            )
    return len(identifiers)
