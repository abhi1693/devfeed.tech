"""Run existing analysis specialists against an explicitly private article owner."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import cast

from devfeed_core.ai_content import eligible_article
from devfeed_core.analysis import (
    PROMPT_VERSION,
    VALIDATION_VERSION,
    AnalysisResult,
    SourceSnapshot,
    analysis_catalog_current,
    fail_analysis,
    reuse_candidates,
    snapshot_hash,
    validate_evidence,
)
from devfeed_core.article_jobs import approved_sources
from devfeed_core.article_refresh import (
    ACTIVE_REFRESH_STATES,
    refresh_for_article,
    refresh_job_current,
    touch_refresh,
)
from devfeed_core.article_refresh_processing import RefreshInputs, prepare_refresh_inputs
from devfeed_core.article_refresh_types import (
    RefreshContent,
    RefreshDraft,
    RefreshFields,
    RefreshTag,
)
from devfeed_core.catalog_cache import reuse_snapshots, snapshot_current
from devfeed_core.config import Settings
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.job_lifecycle import finish_job, start_job
from devfeed_core.jobs import owned_job
from devfeed_core.json_types import JsonRecord
from devfeed_core.logging import log_context
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleRefresh,
    utcnow,
)
from devfeed_core.source_tags import resolve_source_tags, source_tag_names
from devfeed_core.topics import lock_topics
from devfeed_core.transaction_retry import run_transaction
from sqlalchemy import select
from sqlalchemy.orm import Session, defer, load_only, sessionmaker

from devfeed_aggregator import analysis_tasks
from devfeed_aggregator.analysis_tasks import (
    AnalysisAttempt,
    AnalysisContext,
    ArticleAnalysisService,
)
from devfeed_aggregator.article_pages import PageArticle

LOCKED_REFRESH = "devfeed_private_refresh_subject"


def apply_refresh_enrichment(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    article_id: uuid.UUID,
    refresh_id: uuid.UUID,
    url: str,
    response: FetchResult,
    page: PageArticle,
) -> JsonRecord:
    """Store an extracted replacement without touching live metadata or associations."""
    from devfeed_core.article_refresh_processing import candidate_snapshot, resume_refresh

    from devfeed_aggregator.article_tasks import page_values

    names = source_tag_names(page.tags)
    if len(names) > 200:
        raise ValueError("Article maintenance contains too many source labels")
    for retry in range(3):
        with factory() as session:
            owner = session.get(
                ArticleRefresh,
                refresh_id,
                options=[
                    defer(ArticleRefresh.payload, raiseload=True),
                ],
            )
            public = session.get(Article, article_id)
            if (
                owner is None
                or public is None
                or owner.status not in ACTIVE_REFRESH_STATES
                or owner.payload_pruned_at is not None
            ):
                # Release the read connection before the guarded terminal write.
                session.close()
                return _retire_enrichment(factory, identifier, token, article_id, refresh_id)
            session.refresh(owner, ["payload"])
            draft = RefreshDraft.model_validate(owner.payload)
            version = owner.version
            temporary = Article(
                **draft.fields.model_dump(),
                image_url=owner.image_url,
                classification_provenance=dict(public.classification_provenance or {}),
            )
            updates = page_values(temporary, page)
            image_url = cast(str | None, updates.pop("image_url", owner.image_url))
            fields = RefreshFields.model_validate({**draft.fields.model_dump(), **updates})
            content = (
                RefreshContent(
                    text=page.text,
                    content_hash=hashlib.sha256(page.text.encode()).hexdigest(),
                    url=response.final_url,
                    method=page.text_source or "page",
                    retrieved_at=utcnow(),
                )
                if page.text
                else draft.content
            )
            updated = RefreshDraft.model_validate(
                {**draft.model_dump(), "fields": fields, "content": content}
            )
            source_hash = snapshot_hash(candidate_snapshot(public, updated))
            if source_hash != owner.source_hash:
                updated.analysis = None
            payload = updated.model_dump(mode="json")
            payload_bytes = len(json.dumps(payload, ensure_ascii=False).encode())
            old_tag_bytes = len(json.dumps(payload["tags"], ensure_ascii=False).encode())
            changed = sorted(
                name for name in updates if getattr(draft.fields, name) != getattr(fields, name)
            )
            if image_url != owner.image_url:
                changed.append("image_url")
            manual = (public.classification_provenance or {}).get("origin") == "manual"
        try:
            with factory.begin() as session:
                approved = bool(approved_sources(session, article_id, lock=True))
                resolved, _ = (
                    resolve_source_tags(session, names.values())
                    if approved and not manual
                    else ({}, 0)
                )
                public = session.scalar(
                    select(Article).where(Article.id == article_id).with_for_update(of=Article)
                )
                job = owned_job(session, ArticleEnrichmentJob, identifier, token)
                if job is None:
                    return {"outcome": "lease_lost", "changed_fields": []}
                if job.article_id != article_id or job.refresh_id != refresh_id:
                    return {"outcome": "superseded", "changed_fields": []}
                job.http_status, job.result = response.status, {**page.evidence, "input_url": url}
                if not approved:
                    finish_job(job, "unapproved", utcnow())
                    return {"outcome": job.outcome, "changed_fields": []}
                owner = (
                    refresh_for_article(session, public, refresh_id, lock=True) if public else None
                )
                if (
                    public is None
                    or owner is None
                    or public.canonical_url != url
                    or not refresh_job_current(owner, public, job, "enrichment")
                ):
                    finish_job(job, "superseded", utcnow())
                    return {"outcome": job.outcome, "changed_fields": []}
                if owner.version != version:
                    raise analysis_tasks._ApplicationInputsChanged()
                tags = {item.tag_id: item for item in updated.tags}
                for tag_id in resolved.values():
                    tags.setdefault(tag_id, RefreshTag(tag_id=tag_id, origin="source"))
                if len(tags) > 200:
                    raise ValueError("Article maintenance contains too many source labels")
                payload["tags"] = [
                    item.model_dump(mode="json")
                    for item in sorted(tags.values(), key=lambda item: item.tag_id)
                ]
                if (
                    payload_bytes - old_tag_bytes + len(json.dumps(payload["tags"]).encode())
                    > 1_048_576
                ):
                    raise ValueError("Maintenance inputs exceed the bounded private payload")
                if len(tags) != len(updated.tags):
                    changed.append("tags")
                owner.payload, owner.source_hash = payload, source_hash
                owner.image_url = image_url
                if owner.managed_image and owner.managed_image.get("source_url") != image_url:
                    owner.managed_image = {}
                touch_refresh(owner)
                job.changed_fields = sorted(changed)
                finish_job(
                    job,
                    "enriched" if page.has_text else "metadata_only" if changed else "not_found",
                    utcnow(),
                )
                result = {"outcome": job.outcome, "changed_fields": job.changed_fields}
        except analysis_tasks._ApplicationInputsChanged:
            if retry == 2:
                raise
            continue
        resume_refresh(factory, refresh_id)
        return result
    return {"outcome": "superseded", "changed_fields": []}


def _metadata(
    factory: sessionmaker[Session], refresh_id: uuid.UUID
) -> tuple[int, RefreshFields] | None:
    with factory() as session:
        return _read_metadata(session, refresh_id)


def _read_metadata(session: Session, refresh_id: uuid.UUID) -> tuple[int, RefreshFields] | None:
    row = session.execute(
        select(ArticleRefresh.version, ArticleRefresh.payload["fields"]).where(
            ArticleRefresh.id == refresh_id,
            ArticleRefresh.status.in_(ACTIVE_REFRESH_STATES),
            ArticleRefresh.payload_pruned_at.is_(None),
        )
    ).one_or_none()
    return (row[0], RefreshFields.model_validate(row[1])) if row is not None else None


def _retire_enrichment(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    article_id: uuid.UUID,
    refresh_id: uuid.UUID,
) -> JsonRecord:
    with factory.begin() as session:
        _lock_article(session, article_id)
        job = owned_job(session, ArticleEnrichmentJob, identifier, token)
        if job is None:
            return {"outcome": "lease_lost", "changed_fields": []}
        if job.article_id == article_id and job.refresh_id == refresh_id:
            finish_job(job, "superseded", utcnow())
        return {"outcome": "superseded", "changed_fields": []}


def _guard(
    session: Session,
    context: AnalysisContext,
    job: ArticleAnalysisJob,
    metadata: tuple[int, RefreshFields] | None,
    article: Article | None,
    approved: bool,
    locked_owner: ArticleRefresh | None = None,
) -> ArticleRefresh | None:
    if not approved:
        finish_job(job, "unapproved", utcnow())
        return None
    owner = locked_owner or (
        refresh_for_article(session, article, context.refresh_id, lock=True)
        if article is not None and context.refresh_id is not None
        else None
    )
    if (
        article is None
        or owner is None
        or metadata is None
        or owner.version != metadata[0]
        or not refresh_job_current(owner, article, job, "analysis")
        or owner.source_hash != job.input_hash
        or job.input_hash != context.input_hash
        or article.canonical_url != context.snapshot["url"]
    ):
        finish_job(job, "superseded", utcnow())
        return None
    eligible = Article(
        published_at=metadata[1].published_at,
        discovered_at=article.discovered_at,
    )
    if not eligible_article(eligible):
        finish_job(job, "content_date_deferred", utcnow())
        return None
    return owner


def _lock_article(session: Session, article_id: uuid.UUID) -> tuple[Article | None, bool]:
    approved = bool(approved_sources(session, article_id, lock=True))
    article = session.scalar(
        select(Article).where(Article.id == article_id).with_for_update(of=Article)
    )
    return article, approved


def analyze_refresh(
    factory: sessionmaker[Session], settings: Settings, identifier: uuid.UUID, refresh_id: uuid.UUID
) -> None:
    # Snapshot loading/hashing occurs before any source/subject/job write lock.
    with factory() as session:
        initial = session.get(
            ArticleAnalysisJob,
            identifier,
            options=[
                load_only(
                    ArticleAnalysisJob.id,
                    ArticleAnalysisJob.article_id,
                    ArticleAnalysisJob.refresh_id,
                )
            ],
        )
        if initial is None or initial.refresh_id != refresh_id:
            return
        metadata = _read_metadata(session, refresh_id)
        snapshot = cast(
            SourceSnapshot, dict(initial.input_snapshot) if metadata is not None else {}
        )
        digest = snapshot_hash(snapshot)
        article_id = initial.article_id
    with factory.begin() as session:
        article, approved = _lock_article(session, article_id)
        job = session.scalar(
            select(ArticleAnalysisJob)
            .options(
                defer(ArticleAnalysisJob.input_snapshot, raiseload=True),
                defer(ArticleAnalysisJob.catalog_snapshot, raiseload=True),
            )
            .where(ArticleAnalysisJob.id == identifier)
            .with_for_update(of=ArticleAnalysisJob)
        )
        if job is None or job.status != "queued" or job.available_at > utcnow():
            return
        if not settings.ai_enabled:
            fail_analysis(job, "ai_not_configured")
            return
        context = AnalysisContext(
            identifier, uuid.uuid4(), snapshot, article_id, refresh_id, digest
        )
        owner = _guard(session, context, job, metadata, article, approved)
        if owner is None:
            return
        token = start_job(job, utcnow(), 300)
        job.prompt_version, job.model = PROMPT_VERSION, settings.codex_model
        job.usage = {
            **(job.usage or {}),
            "active_step": None,
            "validation_version": VALIDATION_VERSION,
        }
        context = AnalysisContext(identifier, token, snapshot, article_id, refresh_id, digest)
        attempt = job.attempts
    with log_context(article_id=article_id, attempt=attempt):
        RefreshAnalysisService(settings, factory, analysis_tasks.CodexClient).run(context)


class RefreshAnalysisService(ArticleAnalysisService):
    def owned_job(self, session: Session, context: AnalysisContext) -> ArticleAnalysisJob | None:
        article, approved = _lock_article(session, context.article_id)
        job = owned_job(
            session, ArticleAnalysisJob, context.identifier, context.token, defer_inputs=True
        )
        if job is None:
            return None
        owner = (
            refresh_for_article(session, article, context.refresh_id, lock=True)
            if article is not None and context.refresh_id is not None
            else None
        )
        if not approved:
            finish_job(job, "unapproved", utcnow())
            return None
        if (
            article is None
            or owner is None
            or not refresh_job_current(owner, article, job, "analysis")
            or owner.source_hash != context.input_hash
            or job.input_hash != context.input_hash
        ):
            finish_job(job, "superseded", utcnow())
            return None
        session.info[LOCKED_REFRESH] = (article, owner)
        return job

    def start_step(self, context: AnalysisContext, name: str, digest: str) -> JsonRecord | None:
        assert context.refresh_id is not None
        metadata = _metadata(self.factory, context.refresh_id)
        with self.factory.begin() as session:
            job = self.owned_job(session, context)
            if job is None:
                return None
            article, owner = session.info[LOCKED_REFRESH]
            if _guard(session, context, job, metadata, article, True, owner) is None:
                return None
            return self.record_step(job, context, name, digest)

    def propose_topics(self, context: AnalysisContext, result: AnalysisResult) -> None:
        # Draft source assignments stay private; ordinary tag/topic automation
        # can observe their committed public assignments after promotion.
        return None

    def apply_result(
        self,
        context: AnalysisContext,
        result: AnalysisResult,
        topic_match_status: str | None,
        attempt: AnalysisAttempt,
    ) -> str | None:
        assert context.refresh_id is not None
        for retry in range(3):
            with reuse_snapshots(), reuse_candidates():
                with self.factory() as session:
                    prepared = prepare_refresh_inputs(session, context.refresh_id)
                metadata = prepared.version, prepared.draft.fields
                prior = prepared.facts["analysis"]
                catalog_current = bool(
                    prior is not None
                    and prepared.input_hash == context.input_hash
                    and analysis_catalog_current(prior, prepared.taxonomy, context.snapshot)
                )
                if catalog_current:
                    validate_evidence(result, context.snapshot, prepared.taxonomy)
                replacement = RefreshDraft.model_validate(
                    {**prepared.draft.model_dump(), "analysis": result}
                ).model_dump(mode="json")
                catalog_hash = prior.catalog_hash if prior is not None else None

                def apply(
                    session: Session,
                    prepared: RefreshInputs = prepared,
                    metadata: tuple[int, RefreshFields] = metadata,
                    catalog_current: bool = catalog_current,
                    catalog_hash: str | None = catalog_hash,
                    replacement: JsonRecord = replacement,
                ) -> str | None:
                    job = self.owned_job(session, context)
                    if job is None:
                        return None
                    article, locked_owner = session.info[LOCKED_REFRESH]
                    owner = _guard(session, context, job, metadata, article, True, locked_owner)
                    if owner is None:
                        return job.outcome
                    lock_topics(session, read=True)
                    if not snapshot_current(session, ("topics", "tags"), prepared.taxonomy):
                        raise analysis_tasks._ApplicationInputsChanged()
                    if not catalog_current or job.catalog_hash != catalog_hash:
                        finish_job(job, "superseded", utcnow())
                    else:
                        job.result = result.model_dump(mode="json")
                        if topic_match_status:
                            job.result["topic_match_status"] = topic_match_status
                        job.model = getattr(attempt.client, "model", self.settings.codex_model)
                        owner.payload = replacement
                        touch_refresh(owner)
                        finish_job(
                            job,
                            "applied" if result.outcome == "ready" else "insufficient_evidence",
                            utcnow(),
                        )
                    return job.outcome

                try:
                    outcome = run_transaction(self.factory, apply)
                except analysis_tasks._ApplicationInputsChanged:
                    if retry == 2:
                        raise
                    continue
            from devfeed_core.article_refresh_processing import resume_refresh

            resume_refresh(self.factory, context.refresh_id)
            return outcome
        return None
