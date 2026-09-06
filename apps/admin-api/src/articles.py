"""Private article editing, evidence, classification and editorial review."""

import logging
import uuid
from datetime import datetime
from typing import Literal

from devfeed_core.analysis import ManualClassification, classify_manually
from devfeed_core.editorial import (
    EditorialDecision,
    decide_article,
    invalidate_editorial,
    publication_blockers,
)
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleCategory,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleReview,
    ArticleTag,
    ArticleTopic,
    Source,
)
from devfeed_core.schemas import (
    CategoryOut,
    ContentFormat,
    ContentType,
    InputModel,
    Language,
    Name,
    ORMModel,
    ReviewNote,
    SourceRef,
    TagOut,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.urls import canonicalize_url, fingerprint, validate_public_url
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field, field_validator
from sqlalchemy import delete, or_, select

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, prohibit_references, record

router = APIRouter(
    prefix="/v1/admin/articles", tags=["admin-articles"], dependencies=[Depends(require_admin)]
)
logger = logging.getLogger(__name__)


class ArticleFields(InputModel):
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(default="", max_length=100000)
    author: Name | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    language: Language | None = None
    content_type: ContentType = "article"
    content_format: ContentFormat = "article"
    published_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def readable_title(cls, value):
        if not value.strip():
            raise ValueError("Title must contain readable text")
        return value.strip()

    @field_validator("image_url")
    @classmethod
    def image_is_public(cls, value):
        return validate_public_url(value) if value is not None else None

    @field_validator("published_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Publication date requires a timezone")
        return value


class AdminArticleCreate(ArticleFields):
    canonical_url: str = Field(max_length=2048)
    source_id: uuid.UUID
    _canonical_url = field_validator("canonical_url")(canonicalize_url)


class AdminArticleUpdate(ArticleFields):
    expected_revision: int = Field(ge=0)


class ArticleTopicOut(ORMModel):
    topic_id: uuid.UUID
    name: str
    status: str
    role: str
    relevance: float
    evidence: str
    origin: str


class AdminArticleOut(ORMModel):
    id: uuid.UUID
    canonical_url: str
    title: str
    summary: str
    ai_summary: str | None
    ai_description: str | None
    author: str | None
    image_url: str | None
    language: str | None
    content_type: str
    content_format: str
    review_status: str
    publication_status: str
    editorial_revision: int
    published_at: datetime | None
    discovered_at: datetime
    published_to_feed_at: datetime | None
    classification_provenance: dict
    sources: list[SourceRef] = Field(default_factory=list)
    categories: list[CategoryOut]
    tags: list[TagOut]
    topics: list[ArticleTopicOut] = Field(default_factory=list)
    publication_blockers: list[str] = Field(default_factory=list)


class ArticleContentOut(ORMModel):
    article_id: uuid.UUID
    url: str
    text: str
    method: str
    retrieved_at: datetime


class ArticleReviewOut(ORMModel):
    id: uuid.UUID
    article_id: uuid.UUID
    action: str
    actor: str | None
    note: str | None
    revision: int
    created_at: datetime


class ReviewArticle(InputModel):
    action: Literal["approve", "reject", "publish", "unpublish"]
    note: ReviewNote | None = None
    expected_revision: int = Field(ge=0)


class ClassifyArticle(ManualClassification):
    # Clients cannot impersonate the audit actor. This field is populated from OIDC.
    actor: None = None
    expected_revision: int = Field(ge=0)


def article_view(article):
    result = AdminArticleOut.model_validate(article)
    result.sources = [SourceRef.model_validate(origin.source) for origin in article.origins]
    result.topics = [
        ArticleTopicOut(
            topic_id=link.topic_id,
            name=link.topic.name,
            status=link.topic.status,
            role=link.role,
            relevance=link.relevance,
            evidence=link.evidence,
            origin=link.origin,
        )
        for link in article.topic_links
    ]
    result.publication_blockers = publication_blockers(article)
    if not any(origin.source.approval_status == "approved" for origin in article.origins):
        result.publication_blockers.append("no_approved_source")
    return result


@router.get("", response_model=Page[AdminArticleOut], operation_id="admin_articles_list")
def articles(
    session: DB,
    query: Listing,
    review_status: Literal["pending", "approved", "rejected"] | None = None,
    publication_status: Literal["unpublished", "published"] | None = None,
    source_id: uuid.UUID | None = None,
    topic_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    tag_id: uuid.UUID | None = None,
):
    statement = select(Article)
    if query.q:
        statement = statement.where(
            or_(
                Article.title.icontains(query.q, autoescape=True),
                Article.canonical_url.icontains(query.q, autoescape=True),
            )
        )
    if review_status:
        statement = statement.where(Article.review_status == review_status)
    if publication_status:
        statement = statement.where(Article.publication_status == publication_status)
    for value, article_column, column in [
        (source_id, ArticleOrigin.article_id, ArticleOrigin.source_id),
        (topic_id, ArticleTopic.article_id, ArticleTopic.topic_id),
        (category_id, ArticleCategory.article_id, ArticleCategory.category_id),
        (tag_id, ArticleTag.article_id, ArticleTag.tag_id),
    ]:
        if value:
            statement = statement.where(
                Article.id.in_(select(article_column).where(column == value))
            )
    result = paginate(
        session,
        statement,
        query,
        {
            "title": Article.title,
            "discovered_at": Article.discovered_at,
            "review_status": Article.review_status,
            "publication_status": Article.publication_status,
        },
        "-discovered_at",
    )
    result["items"] = [article_view(article) for article in result["items"]]
    return result


@router.get("/{article_id}", response_model=AdminArticleOut, operation_id="admin_article_get")
def detail(article_id: uuid.UUID, session: DB):
    return article_view(record(session, Article, article_id))


@router.post(
    "", response_model=AdminArticleOut, status_code=201, operation_id="admin_article_create"
)
def create(body: AdminArticleCreate, session: DB, admin: Admin):
    record(session, Source, body.source_id)
    article = Article(
        **body.model_dump(exclude={"source_id"}),
        url_hash=fingerprint(body.canonical_url),
        review_status="pending",
        publication_status="unpublished",
    )
    session.add(article)
    session.flush()
    session.add(
        ArticleOrigin(
            article_id=article.id,
            source_id=body.source_id,
            entry_key=fingerprint("manual:" + body.canonical_url),
            original_url=body.canonical_url,
            source_metadata={"origin": "manual", "actor": admin.subject},
        )
    )
    session.add(
        ArticleReview(
            article_id=article.id,
            action="create",
            actor=admin.subject,
            revision=article.editorial_revision,
        )
    )
    session.commit()
    session.expire(article, ["origins"])
    logger.info("article_created", extra={"article_id": article.id})
    return article_view(article)


@router.put("/{article_id}", response_model=AdminArticleOut, operation_id="admin_article_update")
def update(article_id: uuid.UUID, body: AdminArticleUpdate, session: DB, admin: Admin):
    article = record(session, Article, article_id, lock=True)
    if article.editorial_revision != body.expected_revision:
        raise OperationConflict("Article changed; reload before saving")
    changes = body.model_dump(exclude={"expected_revision"})
    if any(getattr(article, field) != value for field, value in changes.items()):
        for field, value in changes.items():
            setattr(article, field, value)
        invalidate_editorial(article)
        session.add(
            ArticleReview(
                article_id=article_id,
                action="edit",
                actor=admin.subject,
                revision=article.editorial_revision,
            )
        )
    session.commit()
    logger.info("article_updated", extra={"article_id": article_id})
    return article_view(article)


@router.post(
    "/{article_id}/review", response_model=AdminArticleOut, operation_id="admin_article_review"
)
def review(article_id: uuid.UUID, body: ReviewArticle, session: DB, admin: Admin):
    article = decide_article(
        session, article_id, EditorialDecision(**body.model_dump(), actor=admin.subject)
    )
    session.commit()
    return article_view(article)


@router.post(
    "/{article_id}/classify", response_model=AdminArticleOut, operation_id="admin_article_classify"
)
def classify(article_id: uuid.UUID, body: ClassifyArticle, session: DB, admin: Admin):
    try:
        article = classify_manually(
            session,
            article_id,
            ManualClassification(**body.model_dump(exclude={"actor"}), actor=admin.subject),
        )
    except (OperationConflict, RecordNotFound):
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return article_view(article)


@router.get(
    "/{article_id}/content",
    response_model=ArticleContentOut | None,
    operation_id="admin_article_content",
)
def content(article_id: uuid.UUID, session: DB):
    record(session, Article, article_id)
    return session.get(ArticleContent, article_id)


@router.get(
    "/{article_id}/reviews",
    response_model=Page[ArticleReviewOut],
    operation_id="admin_article_reviews",
)
def reviews(article_id: uuid.UUID, session: DB, query: Listing):
    record(session, Article, article_id)
    return paginate(
        session,
        select(ArticleReview).where(ArticleReview.article_id == article_id),
        query,
        {"created_at": ArticleReview.created_at},
        "-created_at",
    )


@router.delete("/{article_id}", status_code=204, operation_id="admin_article_delete")
def remove(article_id: uuid.UUID, session: DB):
    article = record(session, Article, article_id, lock=True)
    if article.publication_status == "published":
        raise OperationConflict("Unpublish the article before deleting it")
    prohibit_references(
        session,
        [
            (
                "active jobs",
                select(model).where(
                    model.article_id == article_id, model.status.in_(["queued", "running"])
                ),
            )
            for model in (ArticleImageJob, ArticleEnrichmentJob, ArticleAnalysisJob)
        ],
    )
    session.execute(delete(Article).where(Article.id == article_id))
    session.commit()
    logger.info("article_deleted", extra={"article_id": article_id})
    return Response(status_code=204)
