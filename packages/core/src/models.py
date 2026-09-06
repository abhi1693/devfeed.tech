import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("poll_interval_seconds >= 300"),
        CheckConstraint(
            "source_type IN ('publisher', 'aggregator')", name="ck_sources_source_type"
        ),
        CheckConstraint(
            "approval_status IN ('pending','approved','rejected')",
            name="ck_sources_approval_status",
        ),
        CheckConstraint(
            "submission_channel IN ('api','cli','legacy')", name="ck_sources_submission_channel"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    feed_url: Mapped[str] = mapped_column(String(2048), unique=True)
    source_type: Mapped[str] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(500))
    website_url: Mapped[str | None] = mapped_column(String(2048))
    logo_url: Mapped[str | None] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    language: Mapped[str | None] = mapped_column(String(35))
    submitted_by: Mapped[dict | None] = mapped_column(JSONB)
    submission_channel: Mapped[str] = mapped_column(String(20), default="cli")
    approval_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    review_note: Mapped[str | None] = mapped_column(String(1000))
    metadata_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_error: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    etag: Mapped[str | None] = mapped_column(String(1000))
    last_modified: Mapped[str | None] = mapped_column(String(1000))
    next_fetch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index(
    "ix_sources_due",
    Source.next_fetch_at,
    postgresql_where=Source.enabled.is_(True) & (Source.approval_status == "approved"),
)


class SourceReview(Base):
    __tablename__ = "source_reviews"
    __table_args__ = (CheckConstraint("decision IN ('approved','rejected')"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceEnrichmentJob(Base):
    __tablename__ = "source_enrichment_jobs"
    __table_args__ = (CheckConstraint("status IN ('queued','running','succeeded','failed')"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(1000))
    changed_fields: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)


Index(
    "uq_source_enrichment_active",
    SourceEnrichmentJob.source_id,
    unique=True,
    postgresql_where=SourceEnrichmentJob.status.in_(["queued", "running"]),
)
Index(
    "ix_source_enrichment_dispatch",
    SourceEnrichmentJob.available_at,
    postgresql_where=SourceEnrichmentJob.status == "queued",
)


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (CheckConstraint("parent_id <> id", name="ck_categories_not_self_parent"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), index=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id"))


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[Category | None] = relationship(lazy="joined")
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id"))


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        CheckConstraint(
            "metadata_source_type IN ('publisher', 'aggregator', 'page')",
            name="ck_articles_metadata_source_type",
        ),
        CheckConstraint(
            "review_status IN ('pending','approved','rejected')", name="ck_article_review"
        ),
        CheckConstraint(
            "publication_status IN ('unpublished','published')", name="ck_article_publication"
        ),
        CheckConstraint(
            "publication_status <> 'published' OR review_status = 'approved'",
            name="ck_article_published_approved",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    canonical_url: Mapped[str] = mapped_column(String(2048))
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    ai_summary: Mapped[str | None] = mapped_column(Text)
    ai_description: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    publication_status: Mapped[str] = mapped_column(
        String(20), default="unpublished", server_default="unpublished"
    )
    published_to_feed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # All explicit editorial decisions increment this token. Workers compare it
    # before applying results, so a late result cannot undo an operator decision.
    editorial_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    classification_provenance: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    author: Mapped[str | None] = mapped_column(String(200))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    language: Mapped[str | None] = mapped_column(String(35))
    content_type: Mapped[str] = mapped_column(String(30), default="article")
    content_format: Mapped[str] = mapped_column(
        String(30), default="article", server_default="article"
    )
    tags: Mapped[list[Tag]] = relationship(secondary="article_tags", lazy="selectin")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Feed ordering is independent of publication metadata that enrichment may correct.
    feed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_source_type: Mapped[str | None] = mapped_column(String(20))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    categories: Mapped[list[Category]] = relationship(
        secondary="article_categories", lazy="selectin"
    )
    origins: Mapped[list["ArticleOrigin"]] = relationship(lazy="selectin")
    topic_links: Mapped[list["ArticleTopic"]] = relationship(lazy="selectin")


Index(
    "ix_articles_feed",
    Article.feed_at.desc(),
    Article.id.desc(),
)
Index(
    "ix_articles_search",
    text(
        # Match PostgreSQL's reflected grouping so Alembic does not propose
        # rebuilding an unchanged expression index on every schema check.
        "to_tsvector('english'::regconfig, (((title::text || ' '::text) || summary) "
        "|| ' '::text) || COALESCE(ai_summary, ''::text))"
    ),
    postgresql_using="gin",
    _table=cast(Table, Article.__table__),
)


class ArticleTag(Base):
    __tablename__ = "article_tags"
    origin: Mapped[str] = mapped_column(String(20), default="heuristic", server_default="heuristic")

    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


Index("ix_article_tags_tag", ArticleTag.tag_id, ArticleTag.article_id)


class ArticleCategory(Base):
    __tablename__ = "article_categories"
    origin: Mapped[str] = mapped_column(String(20), default="heuristic", server_default="heuristic")

    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )


Index("ix_article_categories_category", ArticleCategory.category_id, ArticleCategory.article_id)


class ArticleOrigin(Base):
    __tablename__ = "article_origins"
    __table_args__ = (UniqueConstraint("source_id", "entry_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    entry_key: Mapped[str] = mapped_column(String(64))
    original_url: Mapped[str] = mapped_column(String(2048))
    # Bounded entry evidence, not authoritative metadata of the linked resource.
    source_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    source: Mapped[Source] = relationship(lazy="joined")


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (CheckConstraint("status IN ('queued','running','succeeded','failed')"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    entries_seen: Mapped[int] = mapped_column(Integer, default=0)
    articles_created: Mapped[int] = mapped_column(Integer, default=0)
    entries_skipped: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


Index(
    "uq_ingestion_active_source",
    IngestionJob.source_id,
    unique=True,
    postgresql_where=IngestionJob.status.in_(["queued", "running"]),
)
Index(
    "ix_ingestion_dispatch",
    IngestionJob.available_at,
    postgresql_where=IngestionJob.status == "queued",
)


class ArticleImageJob(Base):
    """Durable, independent image lookup; RSS failures and image failures never mix."""

    __tablename__ = "article_image_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        CheckConstraint("outcome IN ('found','not_found','already_present')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(String(20))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    method: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(Text)


Index(
    "uq_article_image_active",
    ArticleImageJob.article_id,
    unique=True,
    postgresql_where=ArticleImageJob.status.in_(["queued", "running"]),
)
Index(
    "ix_article_image_dispatch",
    ArticleImageJob.available_at,
    postgresql_where=ArticleImageJob.status == "queued",
)


class ArticleEnrichmentJob(Base):
    """Independent outbox for original-page metadata, not a second RSS import."""

    __tablename__ = "article_enrichment_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        CheckConstraint(
            "outcome IN ('enriched','metadata_only','not_found','superseded','unapproved')"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(String(20))
    changed_fields: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


Index(
    "uq_article_enrichment_active",
    ArticleEnrichmentJob.article_id,
    unique=True,
    postgresql_where=ArticleEnrichmentJob.status.in_(["queued", "running"]),
)
Index(
    "ix_article_enrichment_dispatch",
    ArticleEnrichmentJob.available_at,
    postgresql_where=ArticleEnrichmentJob.status == "queued",
)


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        CheckConstraint("status IN ('proposed','active','rejected')", name="ck_topic_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    description: Mapped[str | None] = mapped_column(Text)
    ai_description: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(String(2048))
    logo_url: Mapped[str | None] = mapped_column(String(2048))
    # Typed and source-attributed facts are validated at the service boundary.
    facts: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TopicRelation(Base):
    __tablename__ = "topic_relations"
    __table_args__ = (
        CheckConstraint("topic_id <> related_topic_id", name="ck_topic_relation_not_self"),
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    related_topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    relation: Mapped[str] = mapped_column(String(50), primary_key=True)
    evidence_url: Mapped[str | None] = mapped_column(String(2048))


class ArticleTopic(Base):
    __tablename__ = "article_topics"
    __table_args__ = (
        CheckConstraint(
            "role IN ('primary','supporting','comparison','incidental')",
            name="ck_article_topic_role",
        ),
        CheckConstraint("relevance >= 0 AND relevance <= 1", name="ck_article_topic_relevance"),
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20))
    relevance: Mapped[float] = mapped_column(Float)
    evidence: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(20), default="ai")
    topic: Mapped[Topic] = relationship(lazy="joined")


class ArticleReview(Base):
    __tablename__ = "article_reviews"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(1000))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ArticleContent(Base):
    """Private, bounded extraction evidence; never serialized by public GETs."""

    __tablename__ = "article_contents"
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    url: Mapped[str] = mapped_column(String(2048))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(30))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ArticleAnalysisJob(Base):
    """Durable analysis outbox and immutable result history (one row per run)."""

    __tablename__ = "article_analysis_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed')", name="ck_analysis_status"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    input_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    catalog_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    editorial_revision: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    outcome: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(String(1000))


Index(
    "uq_article_analysis_active",
    ArticleAnalysisJob.article_id,
    unique=True,
    postgresql_where=ArticleAnalysisJob.status.in_(["queued", "running"]),
)
Index(
    "ix_article_analysis_dispatch",
    ArticleAnalysisJob.available_at,
    postgresql_where=ArticleAnalysisJob.status == "queued",
)
Index("ix_article_topics_topic", ArticleTopic.topic_id, ArticleTopic.article_id)


class NotificationDelivery(Base):
    """Audience-scoped transactional outbox, independent of Chimely availability."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        CheckConstraint("attempts >= 0"),
        CheckConstraint("audience IN ('admin','user')"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_key: Mapped[str] = mapped_column(String(255))
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    audience: Mapped[str] = mapped_column(String(16))
    subscriber_id: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None]
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(200))


Index(
    "ix_notification_delivery_dispatch",
    NotificationDelivery.available_at,
    postgresql_where=NotificationDelivery.status == "queued",
)
