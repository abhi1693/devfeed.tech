import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    FetchedValue,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class LeasedJobMixin:
    """Shared columns only; each durable job retains its own table and constraints."""

    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminPreference(Base):
    __tablename__ = "admin_preferences"

    owner_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
            "submission_channel IN ('api','cli','legacy')",
            name="ck_sources_submission_channel",
        ),
        CheckConstraint(
            "publication_policy IN ('manual','preview','auto')",
            name="ck_source_publication_policy",
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
    publication_policy: Mapped[str] = mapped_column(
        String(20), default="manual", server_default="manual"
    )
    publication_policy_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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


class SourceEnrichmentJob(LeasedJobMixin, Base):
    __tablename__ = "source_enrichment_jobs"
    __table_args__ = (CheckConstraint("status IN ('queued','running','succeeded','failed')"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
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


class TaxonomyMigrationArchive(Base):
    """Original records retained for audit, never queried as the subject catalog."""

    __tablename__ = "taxonomy_migration_archive"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_table: Mapped[str] = mapped_column(String(50))
    record: Mapped[dict] = mapped_column(JSONB)


class TopicProposal(Base):
    __tablename__ = "topic_proposals"
    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected')"),
        CheckConstraint("action IN ('create','update')"),
        CheckConstraint("origin IN ('import','article_enrichment','ai_analysis')"),
        CheckConstraint(
            "status = 'pending' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)"
        ),
        CheckConstraint("status <> 'approved' OR applied IS NOT NULL"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(index=True)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), index=True
    )
    slug: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(10))
    origin: Mapped[str] = mapped_column(String(30))
    source_name: Mapped[str] = mapped_column(String(200))
    proposed: Mapped[dict] = mapped_column(JSONB)
    baseline: Mapped[dict | None] = mapped_column(JSONB)
    evidence: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[dict] = mapped_column(JSONB)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[dict | None] = mapped_column(JSONB)
    review_note: Mapped[str | None] = mapped_column(String(1000))
    applied: Mapped[dict | None] = mapped_column(JSONB)
    research_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


Index(
    "uq_topic_proposal_pending_slug",
    TopicProposal.slug,
    unique=True,
    postgresql_where=TopicProposal.status == "pending",
)
Index(
    "uq_topic_proposal_pending_target",
    TopicProposal.topic_id,
    unique=True,
    postgresql_where=TopicProposal.status == "pending",
)


class TopicAnalysisJob(LeasedJobMixin, Base):
    """Durable topic metadata or relationship research; never a catalog writer."""

    __tablename__ = "topic_analysis_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        CheckConstraint("attempts >= 0"),
        CheckConstraint(
            "(proposal_id IS NULL) <> (topic_id IS NULL)",
            name="ck_topic_analysis_target",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topic_proposals.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="RESTRICT"), index=True
    )
    input_hash: Mapped[str] = mapped_column(String(64))
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    requested_by: Mapped[dict] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    outcome: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(String(1000))
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


Index(
    "uq_topic_analysis_active",
    TopicAnalysisJob.proposal_id,
    unique=True,
    postgresql_where=TopicAnalysisJob.status.in_(["queued", "running"]),
)
Index(
    "ix_topic_analysis_dispatch",
    TopicAnalysisJob.available_at,
    postgresql_where=TopicAnalysisJob.status == "queued",
)
Index(
    "uq_topic_relationship_analysis_active",
    TopicAnalysisJob.topic_id,
    unique=True,
    postgresql_where=TopicAnalysisJob.status.in_(["queued", "running"]),
)


class ResearchVerificationJob(LeasedJobMixin, Base):
    """One durable verification pass per research run, independent of inference retries."""

    __tablename__ = "research_verification_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        CheckConstraint("attempts >= 0"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topic_analysis_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    relationships: Mapped[bool] = mapped_column(Boolean)
    outcome: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(String(1000))
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


Index(
    "ix_research_verification_dispatch",
    ResearchVerificationJob.available_at,
    postgresql_where=ResearchVerificationJob.status == "queued",
)


class TopicRelationProposal(Base):
    """Source-backed graph suggestions; only an administrator creates the edge."""

    __tablename__ = "topic_relation_proposals"
    __table_args__ = (
        CheckConstraint("topic_id <> related_topic_id"),
        CheckConstraint("status IN ('pending','approved','rejected')"),
        CheckConstraint(
            "status = 'pending' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topic_analysis_jobs.id", ondelete="RESTRICT"), index=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="RESTRICT"), index=True
    )
    related_topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="RESTRICT"), index=True
    )
    relation: Mapped[str] = mapped_column(String(50))
    explanation: Mapped[str] = mapped_column(String(1000))
    evidence_url: Mapped[str] = mapped_column(String(2048))
    evidence_title: Mapped[str] = mapped_column(String(300))
    evidence_quote: Mapped[str] = mapped_column(String(500))
    topic_snapshot: Mapped[dict] = mapped_column(JSONB)
    related_topic_snapshot: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[dict] = mapped_column(JSONB)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[dict | None] = mapped_column(JSONB)
    review_note: Mapped[str | None] = mapped_column(String(1000))


Index(
    "uq_relation_proposal_pending",
    TopicRelationProposal.topic_id,
    TopicRelationProposal.related_topic_id,
    TopicRelationProposal.relation,
    unique=True,
    postgresql_where=TopicRelationProposal.status == "pending",
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id"))
    auto_link_topic: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    topic_match_revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    topic_match_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    topic_match_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_tags_topic_discovery",
    Tag.topic_match_revision,
    Tag.id,
    postgresql_where=Tag.auto_link_topic.is_(True),
)
TAG_TOPIC_CATALOG_REVISION = Sequence("tag_topic_catalog_revision", metadata=Base.metadata)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        CheckConstraint(
            "metadata_source_type IN ('publisher', 'aggregator', 'page')",
            name="ck_articles_metadata_source_type",
        ),
        CheckConstraint(
            "review_status IN ('pending','approved','rejected')",
            name="ck_article_review",
        ),
        CheckConstraint(
            "publication_status IN ('unpublished','published')",
            name="ck_article_publication",
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
    slug: Mapped[str] = mapped_column(String(200), unique=True, server_default=FetchedValue())
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
    automation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    automation_next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    origins: Mapped[list["ArticleOrigin"]] = relationship(lazy="selectin")
    topic_links: Mapped[list["ArticleTopic"]] = relationship(lazy="selectin")


Index(
    "ix_articles_automation_due",
    Article.automation_next_check_at,
    Article.id,
    postgresql_where=(Article.review_status == "pending")
    & (Article.publication_status == "unpublished"),
)
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


class IngestionJob(LeasedJobMixin, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (CheckConstraint("status IN ('queued','running','succeeded','failed')"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
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


class ArticleImageJob(LeasedJobMixin, Base):
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


class ArticleEnrichmentJob(LeasedJobMixin, Base):
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
    identity_keys: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), default=list, server_default="{}"
    )
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


Index("ix_topics_identity_keys", Topic.identity_keys, postgresql_using="gin")


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
    automation: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
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


class ArticleAnalysisJob(LeasedJobMixin, Base):
    """Durable analysis outbox and immutable result history (one row per run)."""

    __tablename__ = "article_analysis_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name="ck_analysis_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    input_hash: Mapped[str | None] = mapped_column(String(64))
    input_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    catalog_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    catalog_hash: Mapped[str | None] = mapped_column(String(64))
    editorial_revision: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    outcome: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(String(1000))
    usage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class TopicReanalysis(Base):
    """A resumable scan created atomically with a classification-relevant topic change."""

    __tablename__ = "topic_reanalysis"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"))
    topic_snapshot: Mapped[dict] = mapped_column(JSONB)
    topic: Mapped["Topic"] = relationship(lazy="raise")
    after_article_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_topic_reanalysis_pending",
    TopicReanalysis.created_at,
    postgresql_where=TopicReanalysis.finished_at.is_(None),
)


RELATIONSHIP_GENERATION = Sequence("topic_relationship_generation", metadata=Base.metadata)


class TopicRelationshipScan(Base):
    """One resumable coverage pass per topic revision, retained while automation is paused."""

    __tablename__ = "topic_relationship_scans"
    __table_args__ = (CheckConstraint("failures >= 0"),)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    topic: Mapped["Topic"] = relationship(lazy="raise")
    generation: Mapped[int] = mapped_column(
        BigInteger, server_default=RELATIONSHIP_GENERATION.next_value(), index=True
    )
    topic_snapshot: Mapped[dict] = mapped_column(JSONB)
    after_topic_id: Mapped[uuid.UUID | None]
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topic_analysis_jobs.id", ondelete="SET NULL"), index=True
    )
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(1000))


Index(
    "ix_topic_relationship_scans_pending",
    TopicRelationshipScan.next_run_at,
    postgresql_where=TopicRelationshipScan.finished_at.is_(None),
)


class SourcePublicationPolicyReview(Base):
    __tablename__ = "source_publication_policy_reviews"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ArticlePublicationDecision(Base):
    __tablename__ = "article_publication_decisions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    decision: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class NotificationDelivery(LeasedJobMixin, Base):
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
    error: Mapped[str | None] = mapped_column(String(200))


Index(
    "ix_notification_delivery_dispatch",
    NotificationDelivery.available_at,
    postgresql_where=NotificationDelivery.status == "queued",
)


class FeedNotificationEvent(Base):
    """One first-publication event; recipient expansion resumes in bounded pages."""

    __tablename__ = "feed_notification_events"
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    topic_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recipient_cursor: Mapped[uuid.UUID | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_feed_notification_pending",
    FeedNotificationEvent.created_at,
    postgresql_where=FeedNotificationEvent.completed_at.is_(None),
)


class UserAccount(Base):
    __tablename__ = "user_accounts"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_user_identity"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    organization_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    notification_settings: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    appearance_settings: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    feed_settings: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class UserTopic(Base):
    __tablename__ = "user_topics"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ArticleEngagement(Base):
    __tablename__ = "article_engagement"
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    opens: Mapped[int] = mapped_column(BigInteger, default=0)


class ArticleLike(Base):
    __tablename__ = "article_likes"
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


Index(
    "ix_article_likes_user_recent",
    ArticleLike.user_id,
    ArticleLike.created_at.desc(),
    ArticleLike.article_id,
)


class ArticleOpen(Base):
    __tablename__ = "article_opens"
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    viewer_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    opened_hour: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, index=True
    )


class UserRecommendationState(Base):
    """Recurring durable work and the currently published recommendation generation."""

    __tablename__ = "user_recommendation_states"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    generation: Mapped[uuid.UUID | None] = mapped_column()
    invalidated: Mapped[bool] = mapped_column(Boolean, server_default="true")
    next_refresh_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    interest_count: Mapped[int] = mapped_column(Integer, server_default="0")


Index("ix_user_recommendations_due", UserRecommendationState.next_refresh_at)


class UserInterest(Base):
    __tablename__ = "user_interests"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    # Retain removed topic IDs until refresh so deletion events can find affected users.
    topic_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    seed_topic_id: Mapped[uuid.UUID] = mapped_column()
    weight: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(30))


Index("ix_user_interests_topic_user", UserInterest.topic_id, UserInterest.user_id)


class UserRecommendation(Base):
    __tablename__ = "user_recommendations"
    __table_args__ = (UniqueConstraint("user_id", "position"),)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    topic_id: Mapped[uuid.UUID] = mapped_column()
    seed_topic_id: Mapped[uuid.UUID] = mapped_column()
    reason: Mapped[str] = mapped_column(String(30))


class RecommendationTopicEvent(Base):
    """Coalesced topic changes; finish each fanout pass before replaying new changes."""

    __tablename__ = "recommendation_topic_events"
    topic_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, server_default="1")
    pass_version: Mapped[int] = mapped_column(BigInteger, server_default="1")
    cursor: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
