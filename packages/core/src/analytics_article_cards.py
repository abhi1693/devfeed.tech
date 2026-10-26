"""Bounded transactional evidence loading and pure article-card calculation.

These helpers never query a PostgreSQL projection or publish anything. Large
private evidence is loaded only for the selected IDs and is never exported.
"""

import hashlib
import json
import re
from collections import OrderedDict
from datetime import datetime
from threading import Lock

from sqlalchemy import case, func, literal, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import defer, lazyload, load_only, selectinload

from devfeed_core.analysis import PROMPT_VERSION
from devfeed_core.analytics_card_types import (
    AnalysisStepOut,
    PipelineCard,
    PipelineJob,
    PipelineJobs,
)
from devfeed_core.article_analysis_steps import STEP_SPECS
from devfeed_core.article_lifecycle import ProcessingReadiness, derive_publication_readiness
from devfeed_core.article_processing_retry import (
    current_processing_job,
    processing_retry_targets,
    retry_readiness,
)
from devfeed_core.config import get_settings
from devfeed_core.job_retries import retry_candidate
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleTopic,
    Source,
    Topic,
)
from devfeed_core.text_search import text_search

BATCH_SIZE = 200
_readiness_cache: OrderedDict[tuple, ProcessingReadiness] = OrderedDict()
_readiness_lock = Lock()
_CACHE_SIZE = 4096


def _display_source_id():
    """Match the source attribution displayed by _card, including missing origins."""
    return (
        select(Source.id)
        .join(ArticleOrigin, ArticleOrigin.source_id == Source.id)
        .where(ArticleOrigin.article_id == Article.id)
        .order_by(Source.approval_status != "approved", Source.name.collate("C"), Source.id)
        .limit(1)
        .correlate(Article)
        .scalar_subquery()
    )


def _statement(q, source_id, source_group=None):
    statement = select(Article).options(
        lazyload(Article.tags),
        selectinload(Article.origins)
        .load_only(ArticleOrigin.source_id, ArticleOrigin.article_id, raiseload=True)
        .joinedload(ArticleOrigin.source)
        .load_only(Source.id, Source.name, Source.approval_status, raiseload=True),
        selectinload(Article.topic_links)
        .load_only(ArticleTopic.topic_id, ArticleTopic.role, raiseload=True)
        .joinedload(ArticleTopic.topic)
        .load_only(Topic.id, Topic.status, raiseload=True),
    )
    if q:
        statement = statement.where(
            text_search(q, Article.title, Article.ai_title, Article.canonical_url)
        )
    if source_id:
        statement = statement.where(
            select(ArticleOrigin.id)
            .where(ArticleOrigin.article_id == Article.id, ArticleOrigin.source_id == source_id)
            .exists()
        )
    if source_group is not None:
        displayed = _display_source_id()
        statement = statement.where(
            displayed.is_(None) if source_group == "unknown" else displayed == source_group
        )
    return statement


def _latest(session, model, identifiers, *, operation=None, lightweight=False):
    current = select(model.id).where(model.article_id.in_(identifiers))
    order = [model.article_id, model.status.in_(["queued", "running"]).desc()]
    if operation:
        current = current.where(model.operation == operation)
        current = current.join(Article, Article.id == model.article_id)
        current_input = (
            model.image_url.is_not_distinct_from(Article.image_url)
            if operation == "store"
            else (model.storage["input_url"].as_string() == Article.canonical_url).is_(True)
        )
        order.append(current_input.desc())
    current = current.ext(distinct_on(model.article_id)).order_by(
        *order,
        model.created_at.desc(),
        model.id.desc(),
    )
    statement = select(model, retry_candidate(model)).where(model.id.in_(current))
    if lightweight and model is ArticleAnalysisJob:
        pending_step = case(
            *(
                (
                    func.coalesce(
                        model.usage["article_steps"][name]["status"].as_string(), "pending"
                    ).not_in(["succeeded", "skipped"]),
                    literal(name),
                )
                for name in STEP_SPECS
            ),
            else_=None,
        )
        progress = case(
            (
                model.status.in_(["queued", "running", "failed"])
                & (model.prompt_version == PROMPT_VERSION),
                func.coalesce(model.usage["active_step"].as_string(), pending_step),
            ),
            else_=None,
        )
        statement = statement.add_columns(progress).options(
            defer(model.input_snapshot), defer(model.catalog_snapshot), defer(model.usage)
        )
    result = {}
    for row in session.execute(statement.options(lazyload("*"))):
        job, eligible = row[:2]
        job._pipeline_retryable = eligible
        if lightweight and model is ArticleAnalysisJob:
            job._pipeline_current_step = row[2]
        result[job.article_id] = job
    return result


def _batch_facts(session, articles, *, lightweight=False):
    identifiers = [article.id for article in articles]
    content_query = select(ArticleContent).where(ArticleContent.article_id.in_(identifiers))
    if lightweight:
        content_query = content_query.options(
            load_only(
                ArticleContent.article_id,
                ArticleContent.content_hash,
                ArticleContent.method,
                ArticleContent.url,
            )
        )
    contents = {content.article_id: content for content in session.scalars(content_query)}
    return {
        "content": contents,
        "enrichment": _latest(session, ArticleEnrichmentJob, identifiers),
        "analysis": _latest(session, ArticleAnalysisJob, identifiers, lightweight=lightweight),
        "discovery": _latest(session, ArticleImageJob, identifiers, operation="discover"),
        "storage": _latest(session, ArticleImageJob, identifiers, operation="store"),
    }


def _readiness_key(article, facts, *, catalog_hash, settings, now):
    """Identity of every admission input, excluding immutable large evidence.

    Content text is identified by its extraction hash; immutable inference input
    and candidate snapshots by their stored hashes. The cache is only a display
    optimization. Publication always recomputes readiness in its locked transaction.
    """
    values = {
        "article": {
            field: getattr(article, field)
            for field in (
                "canonical_url",
                "title",
                "summary",
                "ai_title",
                "ai_summary",
                "ai_description",
                "review_status",
                "publication_status",
                "language",
                "content_type",
                "content_format",
                "classification_provenance",
                "metadata_source_type",
                "published_at",
                "image_url",
                "managed_image",
            )
        },
        "sources": sorted(
            (str(origin.source_id), origin.source.approval_status) for origin in article.origins
        ),
        "topics": sorted(
            (str(link.topic_id), link.role, link.topic.status) for link in article.topic_links
        ),
        "catalog": catalog_hash,
        "settings": (
            settings.ai_enabled,
            settings.image_storage_enabled,
            settings.image_public_url,
            settings.analysis_max_candidates,
            settings.analysis_fallback_candidates,
            settings.ai_content_not_before,
        ),
    }
    content = facts["content"]
    values["content"] = (content.content_hash, content.method, content.url) if content else None
    for name, job in facts.items():
        if name == "content":
            continue
        values[name] = (
            None
            if job is None
            else {
                field: getattr(job, field)
                for field in (
                    "id",
                    "status",
                    "outcome",
                    "error",
                    "available_at",
                    "attempts",
                    *(
                        (
                            "input_hash",
                            "catalog_hash",
                            "prompt_version",
                            "result",
                            "inputs_pruned_at",
                        )
                        if name == "analysis"
                        else ("result",)
                        if name == "enrichment"
                        else ("image_url", "storage")
                    ),
                )
            }
        )
        if job is not None:
            values[name]["future"] = job.available_at > now
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).digest()
    return article.id, digest


def _batch_readiness(session, articles, *, taxonomy, settings, now):
    facts = _batch_facts(session, articles, lightweight=True)
    catalog_hash = (
        hashlib.sha256(json.dumps(taxonomy, sort_keys=True, default=str).encode()).digest().hex()
    )
    keys = {}
    results = {}
    misses = []
    for article in articles:
        current = {name: rows.get(article.id) for name, rows in facts.items()}
        key = keys[article.id] = _readiness_key(
            article, current, catalog_hash=catalog_hash, settings=settings, now=now
        )
        with _readiness_lock:
            cached = _readiness_cache.get(key)
            if cached is not None:
                _readiness_cache.move_to_end(key)
        if cached is not None:
            results[article.id] = cached
        else:
            misses.append(article)
    if misses:
        ids = [article.id for article in misses]
        # Hydrate private evidence in batches only when its identity changed.
        content_ids = [identifier for identifier in ids if identifier in facts["content"]]
        analysis_ids = [
            facts["analysis"][identifier].id
            for identifier in ids
            if identifier in facts["analysis"]
        ]
        if content_ids:
            list(
                session.scalars(
                    select(ArticleContent).where(ArticleContent.article_id.in_(content_ids))
                )
            )
        if analysis_ids:
            list(
                session.scalars(
                    select(ArticleAnalysisJob).where(ArticleAnalysisJob.id.in_(analysis_ids))
                )
            )
        for article in misses:
            current = {name: rows.get(article.id) for name, rows in facts.items()}
            readiness = derive_publication_readiness(
                article, **current, taxonomy=taxonomy, settings=settings, now=now
            )
            results[article.id] = readiness
            with _readiness_lock:
                _readiness_cache[keys[article.id]] = readiness
                while len(_readiness_cache) > _CACHE_SIZE:
                    _readiness_cache.popitem(last=False)
    return facts, results


def _job(job, *, retryable=True):
    if job is None:
        return None
    return PipelineJob(
        id=job.id,
        status=job.status,
        outcome=job.outcome,
        available_at=job.available_at,
        finished_at=job.finished_at,
        retryable=job.status == "failed"
        and retryable
        and bool(getattr(job, "_pipeline_retryable", False)),
    )


def _jobs(facts, *, allow_retry=True, readiness=None):
    latest_image = current_processing_job(facts, "images", readiness=readiness)
    return PipelineJobs(
        enrichment=_job(facts["enrichment"], retryable=allow_retry),
        analysis=_job(facts["analysis"], retryable=allow_retry and get_settings().ai_enabled),
        images=_job(latest_image, retryable=allow_retry),
    )


_STEP_LABELS = {
    "developer_relevance": "Developer relevance",
    "page_kind": "Article eligibility",
    "language": "Source language",
    "content_type": "Content type",
    "content_format": "Content format",
    "topics": "Topic matching",
    "tags": "Tag matching",
    "identifying_title": "Identifying title",
    "overview": "Article overview",
    "description": "Search description",
}


def _step_time(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _analysis_steps(job):
    if job is None or (not job.steps and job.prompt_version != PROMPT_VERSION):
        return []
    terminal = job.status == "succeeded"
    rows = []
    for name, spec in STEP_SPECS.items():
        step = job.steps.get(name, {})
        status = step.get("status", "skipped" if terminal else "pending")
        if status not in {"pending", "running", "succeeded", "failed", "skipped"}:
            status = "pending"
        error = step.get("error")
        # Worker records contain safe reason codes, never a provider response.
        # Retain that boundary when presenting malformed/legacy step metadata.
        if error is not None and (
            not isinstance(error, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", error)
        ):
            error = "step_failed"
        rows.append(
            AnalysisStepOut(
                name=name,
                label=_STEP_LABELS[name],
                status=status,
                prompt_version=step.get("prompt_version", spec.prompt_version),
                attempts=max(0, step.get("attempts", 0)),
                error=error,
                available_at=_step_time(step.get("available_at"))
                or (
                    job.available_at if job.status == "queued" and name == job.active_step else None
                ),
                started_at=_step_time(step.get("started_at")),
                finished_at=_step_time(step.get("finished_at")),
            )
        )
    return rows


def _current_analysis_step(job):
    if job is None or job.status not in {"queued", "running", "failed"}:
        return None
    if hasattr(job, "_pipeline_current_step"):
        value = job._pipeline_current_step
        return value if value in STEP_SPECS else None
    if job.active_step in STEP_SPECS:
        return job.active_step
    steps = _analysis_steps(job)
    for status in ("running", "failed", "pending"):
        current = next((step.name for step in steps if step.status == status), None)
        if current:
            return current
    return None


def _actions(readiness):
    if "no_approved_source" in readiness.blockers:
        return []
    if readiness.stage not in {"ingested", "enrichment", "analysis", "images"}:
        return []
    key = "enrichment" if readiness.stage == "ingested" else readiness.stage
    if key == "analysis" and {
        "content_date_deferred",
        "ai_not_configured",
        "insufficient_analysis_evidence",
        "manual_analysis_review_required",
    }.intersection(readiness.blockers):
        return []
    check = readiness.checks.get(key)
    if (
        key == "enrichment"
        and check is not None
        and check.status == "complete"
        and "insufficient_source_text" in readiness.blockers
    ):
        return ["advance"]
    if (
        check is not None
        and check.status in {"missing", "stale"}
        and (key != "analysis" or get_settings().ai_enabled)
    ):
        return ["advance"]
    return []


def _card(article, readiness, facts, *, taxonomy=None):
    sources = sorted(
        (origin.source for origin in article.origins),
        key=lambda source: (source.approval_status != "approved", source.name, source.id),
    )
    source = sources[0] if sources else None
    processing = retry_readiness(article, readiness, facts, taxonomy=taxonomy)
    return PipelineCard(
        id=article.id,
        title=article.ai_title or article.title,
        source_name=source.name if source else None,
        source_id=source.id if source else None,
        discovered_at=article.discovered_at,
        stage=readiness.stage,
        state=readiness.state,
        blockers=readiness.blockers,
        editorial_revision=article.editorial_revision,
        next_check_at=article.automation_next_check_at if readiness.state != "complete" else None,
        jobs=_jobs(facts, readiness=processing),
        allowed_actions=_actions(readiness),
        current_analysis_step=_current_analysis_step(facts["analysis"]),
        retry_targets=processing_retry_targets(article, processing, facts),
    )
