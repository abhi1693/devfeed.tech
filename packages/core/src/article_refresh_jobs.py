"""SQL ownership boundaries for private and promoted article job evidence."""

from __future__ import annotations

from typing import cast

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleRefresh,
    Source,
)

type ArticleJobModel = type[ArticleAnalysisJob] | type[ArticleEnrichmentJob] | type[ArticleImageJob]


def _selected_job(model: ArticleJobModel) -> ColumnElement[bool]:
    if model is ArticleAnalysisJob:
        return ArticleRefresh.analysis_job_id == model.id
    if model is ArticleEnrichmentJob:
        return ArticleRefresh.enrichment_job_id == model.id
    image_model = cast(type[ArticleImageJob], model)
    return or_(
        and_(image_model.operation == "discover", ArticleRefresh.discovery_job_id == model.id),
        and_(image_model.operation == "store", ArticleRefresh.storage_job_id == model.id),
    )


def ordinary_job_condition(model: ArticleJobModel) -> ColumnElement[bool]:
    """Only unbound jobs and the promoted owner's selected artifacts are public facts."""
    promoted = (
        select(ArticleRefresh.id)
        .where(
            ArticleRefresh.id == model.refresh_id,
            ArticleRefresh.article_id == model.article_id,
            ArticleRefresh.status == "promoted",
            _selected_job(model),
        )
        .correlate(model)
        .exists()
    )
    return or_(model.refresh_id.is_(None), promoted)


def refresh_retry_condition(model: ArticleJobModel) -> ColumnElement[bool]:
    """An active owner and current live authority must select this exact private job."""
    conditions = [
        ArticleRefresh.id == model.refresh_id,
        ArticleRefresh.article_id == model.article_id,
        ArticleRefresh.status.in_(("active", "review")),
        ArticleRefresh.base_revision == Article.editorial_revision,
        Article.publication_status == "published",
        Article.review_status == "approved",
        Article.origins.any(ArticleOrigin.source.has(Source.approval_status == "approved")),
        _selected_job(model),
    ]
    if model is ArticleImageJob:
        conditions.append(
            or_(
                and_(model.operation == "store", model.image_url == ArticleRefresh.image_url),
                and_(
                    model.operation == "discover",
                    model.storage["input_url"].as_string() == Article.canonical_url,
                    or_(
                        model.storage["article_refresh"].as_boolean().is_not(True),
                        model.storage["previous_image_url"]
                        .as_string()
                        .is_not_distinct_from(ArticleRefresh.image_url),
                    ),
                ),
            )
        )
    return (
        select(ArticleRefresh.id)
        .join(Article, Article.id == ArticleRefresh.article_id)
        .where(*conditions)
        .correlate(model)
        .exists()
    )
