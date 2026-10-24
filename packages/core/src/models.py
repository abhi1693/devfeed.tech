import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    FetchedValue,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
    case,
    func,
    literal,
    or_,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql.elements import ColumnElement

from devfeed_core.partner_link_rel import default_link_rel
from devfeed_core.partner_surfaces import default_campaign_surfaces, default_promotion_surfaces
from devfeed_core.topic_kinds import TOPIC_KINDS


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


class CatalogRevision(Base):
    """Transactional cache identities, changed by database triggers."""

    __tablename__ = "catalog_revisions"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    revision: Mapped[uuid.UUID] = mapped_column(server_default=text("gen_random_uuid()"))


class AdminPreference(Base):
    __tablename__ = "admin_preferences"

    owner_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OverviewDaily(Base):
    """Daily analytics snapshots; no identities or per-viewer data are retained."""

    __tablename__ = "overview_daily"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    metrics: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND slug <> 'suggest' "
            "AND slug !~ '^[a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12}$'",
            name="ck_sources_slug",
        ),
        CheckConstraint(
            "approval_status <> 'approved' OR feed_url IS NOT NULL", name="ck_sources_approved_feed"
        ),
        CheckConstraint("poll_interval_seconds >= 300"),
        CheckConstraint("polling_mode IN ('fixed','adaptive')", name="ck_sources_polling_mode"),
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
    slug: Mapped[str] = mapped_column(String(200), unique=True, server_default=FetchedValue())
    feed_url: Mapped[str | None] = mapped_column(String(2048), unique=True)
    source_type: Mapped[str] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(500))
    website_url: Mapped[str | None] = mapped_column(String(2048))
    logo_url: Mapped[str | None] = mapped_column(String(2048))
    managed_logo: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    image_url: Mapped[str | None] = mapped_column(String(2048))
    language: Mapped[str | None] = mapped_column(String(35))
    relevance_assessment: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    processing_request: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
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
    polling_mode: Mapped[str] = mapped_column(String(20), default="fixed", server_default="fixed")
    polling_state: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    scheduled_polling_mode: Mapped[str | None] = mapped_column(String(20))
    scheduled_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=43200)

    @property
    def adaptive_polling_enabled(self) -> bool:
        from devfeed_core.config import get_settings

        return get_settings().adaptive_source_polling_enabled

    @property
    def effective_polling_mode(self) -> str:
        return (
            "adaptive"
            if self.adaptive_polling_enabled and self.polling_mode == "adaptive"
            else "fixed"
        )

    @property
    def effective_interval_seconds(self) -> int:
        from devfeed_core.polling import resolve_interval

        return resolve_interval(self)

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


Index(
    "ix_sources_polling_reconcile",
    Source.scheduled_polling_mode,
    Source.polling_mode,
    Source.id,
    postgresql_where=Source.enabled.is_(True)
    & (Source.approval_status == "approved")
    & ((Source.polling_mode == "adaptive") | (Source.scheduled_polling_mode == "adaptive")),
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
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        CheckConstraint(
            "operation IN ('legacy','profile','relevance')", name="ck_source_enrichment_operation"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    error: Mapped[str | None] = mapped_column(String(1000))
    operation: Mapped[str] = mapped_column(String(20), default="legacy", server_default="legacy")
    result: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    changed_fields: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)
    requires_solver: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


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
Index(
    "ix_source_enrichment_current",
    SourceEnrichmentJob.source_id,
    SourceEnrichmentJob.operation,
    SourceEnrichmentJob.created_at.desc(),
    SourceEnrichmentJob.id.desc(),
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
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
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
    inputs_pruned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
        CheckConstraint("jsonb_typeof(sponsor_names) = 'array'", name="ck_article_sponsor_names"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    canonical_url: Mapped[str] = mapped_column(String(2048))
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    slug: Mapped[str] = mapped_column(String(200), unique=True, server_default=FetchedValue())
    summary: Mapped[str] = mapped_column(Text, default="")
    # Database-maintained ASCII candidates include Python re.I Unicode equivalents.
    matching_tokens: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default="{}", server_onupdate=FetchedValue()
    )
    ai_title: Mapped[str | None] = mapped_column(String(200))
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
    # Historical commercial disclosure survives campaign expiry and withdrawal.
    sponsor_names: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    author: Mapped[str | None] = mapped_column(String(200))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    managed_image: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
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
    postgresql_where=(Article.review_status.in_(["pending", "approved"]))
    & (Article.publication_status == "unpublished"),
)
Index(
    "ix_articles_feed",
    Article.feed_at.desc(),
    Article.id.desc(),
)
Index(
    "ix_articles_public_facets",
    Article.content_type,
    Article.language,
    Article.id,
    postgresql_where=(Article.publication_status == "published")
    & (Article.review_status == "approved"),
)
Index("ix_articles_published_to_feed_at", Article.published_to_feed_at)
Index("ix_articles_discovered_at", Article.discovered_at)
Index("ix_articles_matching_tokens", Article.matching_tokens, postgresql_using="gin")
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


Index("ix_article_origins_article_source", ArticleOrigin.article_id, ArticleOrigin.source_id)


class IngestionJob(LeasedJobMixin, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (CheckConstraint("status IN ('queued','running','succeeded','failed')"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    new_source_entries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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
    requires_solver: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    operation: Mapped[str] = mapped_column(
        String(20), default="discover", server_default="discover"
    )
    storage: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    __table_args__ = (
        CheckConstraint(
            "(article_id IS NOT NULL AND topic_id IS NULL AND source_id IS NULL "
            "AND product_id IS NULL AND account_id IS NULL AND subject_revision IS NULL "
            "AND operation NOT IN ('topic-logo','source-logo','source-logo-refresh',"
            "'product-logo','product-image','product-ad-image')) OR "
            "(article_id IS NULL AND topic_id IS NOT NULL AND source_id IS NULL "
            "AND product_id IS NULL AND account_id IS NULL AND subject_revision IS NULL "
            "AND operation = 'topic-logo') OR "
            "(article_id IS NULL AND topic_id IS NULL AND source_id IS NOT NULL "
            "AND product_id IS NULL AND account_id IS NULL AND subject_revision IS NULL "
            "AND operation IN ('source-logo','source-logo-refresh')) OR "
            "(article_id IS NULL AND topic_id IS NULL AND source_id IS NULL "
            "AND product_id IS NOT NULL AND account_id IS NULL AND subject_revision IS NULL "
            "AND operation IN ('product-logo','product-image')) OR "
            "(article_id IS NULL AND topic_id IS NULL AND source_id IS NULL "
            "AND product_id IS NOT NULL AND account_id IS NOT NULL "
            "AND subject_revision IS NOT NULL AND subject_revision >= 0 "
            "AND operation = 'product-ad-image')",
            name="ck_image_job_subject",
        ),
        CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        CheckConstraint("outcome IN ('found','not_found','already_present')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    source: Mapped["Source | None"] = relationship()
    topic: Mapped["Topic | None"] = relationship()
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partner_products.id", ondelete="CASCADE"), index=True
    )
    product: Mapped["PartnerProduct | None"] = relationship(foreign_keys=[product_id])
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="CASCADE"), index=True
    )
    subject_revision: Mapped[int | None] = mapped_column(Integer)
    storage_version: Mapped[str | None] = mapped_column(String(20))
    http_status: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(String(20))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    method: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(Text)


Index(
    "uq_source_image_active",
    ArticleImageJob.source_id,
    unique=True,
    postgresql_where=ArticleImageJob.status.in_(["queued", "running"]),
)
Index(
    "uq_topic_image_active",
    ArticleImageJob.topic_id,
    unique=True,
    postgresql_where=ArticleImageJob.status.in_(["queued", "running"]),
)
Index(
    "uq_article_image_active",
    ArticleImageJob.article_id,
    unique=True,
    postgresql_where=ArticleImageJob.status.in_(["queued", "running"]),
)
Index(
    "uq_product_image_active",
    ArticleImageJob.product_id,
    ArticleImageJob.operation,
    unique=True,
    postgresql_where=ArticleImageJob.status.in_(["queued", "running"])
    & ArticleImageJob.account_id.is_(None),
)
Index(
    "uq_partner_ad_image_active",
    ArticleImageJob.account_id,
    ArticleImageJob.product_id,
    unique=True,
    postgresql_where=ArticleImageJob.status.in_(["queued", "running"])
    & (ArticleImageJob.operation == "product-ad-image"),
)
Index(
    "ix_partner_ad_image_history",
    ArticleImageJob.account_id,
    ArticleImageJob.product_id,
    ArticleImageJob.subject_revision,
    ArticleImageJob.created_at,
    postgresql_where=ArticleImageJob.operation == "product-ad-image",
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
    requires_solver: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


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
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{kind}'" for kind in TOPIC_KINDS) + ")",
            name="ck_topic_kind",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    identity_keys: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    matching_tokens: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default="{}", server_onupdate=FetchedValue()
    )
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), default=list, server_default="{}"
    )
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    description: Mapped[str | None] = mapped_column(Text)
    ai_description: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(String(2048))
    logo_url: Mapped[str | None] = mapped_column(String(2048))
    managed_logo: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # Typed and source-attributed facts are validated at the service boundary.
    facts: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


Index("ix_topics_identity_keys", Topic.identity_keys, postgresql_using="gin")
Index("ix_topics_matching_tokens", Topic.matching_tokens, postgresql_using="gin")


class TopicRelation(Base):
    __tablename__ = "topic_relations"
    __table_args__ = (
        CheckConstraint("topic_id <> related_topic_id", name="ck_topic_relation_not_self"),
        Index("ix_topic_relations_related_topic", "related_topic_id", "topic_id"),
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
    __table_args__ = (
        Index(
            "ix_article_reviews_manual_publication",
            "article_id",
            "created_at",
            postgresql_where=text("automation = '{}'::jsonb"),
        ),
        Index(
            "ix_article_reviews_automatic_publication",
            "article_id",
            "created_at",
            postgresql_where=text("action = 'publish' AND automation <> '{}'::jsonb"),
        ),
    )
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
    inputs_pruned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def steps(self) -> dict:
        """Durable specialist progress in the existing parent outbox row.

        Assign a new dictionary through the setter when changing a step. This
        keeps JSONB change tracking and telemetry merges safe without introducing
        another queue, lease, or partially applied article representation.
        """
        value = (self.usage or {}).get("article_steps", {})
        return value if isinstance(value, dict) else {}

    @steps.setter
    def steps(self, value: dict) -> None:
        self.usage = {**(self.usage or {}), "article_steps": value}

    @property
    def active_step(self) -> str | None:
        value = (self.usage or {}).get("active_step")
        return value if isinstance(value, str) else None

    @active_step.setter
    def active_step(self, value: str | None) -> None:
        self.usage = {**(self.usage or {}), "active_step": value}


Index(
    "ix_article_image_history",
    ArticleImageJob.article_id,
    ArticleImageJob.created_at.desc(),
    ArticleImageJob.id.desc(),
)


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
    source_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, server_default=text("'{}'")
    )
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
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_user_identity"),
        UniqueConstraint("username", name="uq_user_accounts_username"),
        CheckConstraint("username ~ '^[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]$'", name="ck_user_username"),
        CheckConstraint(
            "username NOT IN ('admin','api','auth','devfeed','help','login','logout','me','new',"
            "'profiles','settings','signup','support','system','www')",
            name="ck_user_username_reserved",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    organization_id: Mapped[str] = mapped_column(Text)
    username: Mapped[str | None] = mapped_column(Text)
    about: Mapped[str | None] = mapped_column(Text)
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


class UserLink(Base):
    __tablename__ = "user_links"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_user_link_url"),
        UniqueConstraint("user_id", "position", name="uq_user_link_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer)


class UserStackAssociation(Base):
    __tablename__ = "user_stack_associations"
    __table_args__ = (
        UniqueConstraint("user_id", "topic_id", name="uq_user_stack_topic"),
        UniqueConstraint("user_id", "position", name="uq_user_stack_position"),
        CheckConstraint(
            "section IN ('primary', 'hobby', 'learning', 'past')",
            name="ck_user_stack_section",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    section: Mapped[str] = mapped_column(String(20))
    since_year: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)


class UserReadingStreak(Base):
    __tablename__ = "user_reading_streaks"
    __table_args__ = (
        CheckConstraint(
            "current_days >= 0 AND longest_days >= current_days AND total_days >= longest_days",
            name="ck_user_streak_counts",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    current_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    longest_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_read_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


class UserReadingDay(Base):
    __tablename__ = "user_reading_days"
    __table_args__ = (CheckConstraint("article_count > 0", name="ck_reading_day_count"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    read_date: Mapped[date] = mapped_column(Date, primary_key=True)
    article_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


class UserReadingEvent(Base):
    """Distinct daily opens retained after article deletion; account deletion cascades.

    Article UUID deliberately has no FK so catalog pruning cannot erase activity.
    """

    __tablename__ = "user_reading_events"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    read_date: Mapped[date] = mapped_column(Date, primary_key=True)
    article_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(
        String(30), default="article_open", server_default="article_open"
    )
    rule_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


Index("ix_user_accounts_created_at", UserAccount.created_at)


class UserTopic(Base):
    __tablename__ = "user_topics"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserTag(Base):
    __tablename__ = "user_tags"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSource(Base):
    __tablename__ = "user_sources"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


Index("ix_user_sources_source_user", UserSource.source_id, UserSource.user_id)


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


class ArticleBookmark(Base):
    __tablename__ = "article_bookmarks"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


Index(
    "ix_article_bookmarks_user_recent",
    ArticleBookmark.user_id,
    ArticleBookmark.created_at.desc(),
    ArticleBookmark.article_id.desc(),
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
    candidates_dirty: Mapped[bool] = mapped_column(Boolean, server_default="true")
    ranked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Shared revision for preferences and the candidate pool used by retained orders.
    preference_revision: Mapped[int] = mapped_column(BigInteger, server_default="0")


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
    topic_id: Mapped[uuid.UUID | None] = mapped_column()
    seed_topic_id: Mapped[uuid.UUID | None] = mapped_column()
    source_id: Mapped[uuid.UUID | None] = mapped_column()
    tag_id: Mapped[uuid.UUID | None] = mapped_column()
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


class RecommendationSourceEvent(Base):
    __tablename__ = "recommendation_source_events"
    source_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, server_default="1")
    pass_version: Mapped[int] = mapped_column(BigInteger, server_default="1")
    cursor: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class SearchEvent(Base):
    """Append-only outbox; no external index call occurs in a content transaction."""

    __tablename__ = "search_events"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('articles','topics','sources','tags')", name="ck_search_event_kind"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    fanout: Mapped[bool] = mapped_column(Boolean, server_default="false")
    after_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class SearchQueryStat(Base):
    """Identity-free successful-query aggregate awaiting editorial moderation."""

    __tablename__ = "search_query_stats"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected')", name="ck_search_query_stat_status"
        ),
        CheckConstraint("successful_count >= 0", name="ck_search_query_stat_count"),
    )
    query_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[str] = mapped_column(String(200), unique=True)
    successful_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    review_note: Mapped[str | None] = mapped_column(String(1000))


# Recovery polls running leases, independently of the existing queued-job indexes.
# Keep completed payload history out of these frequent scheduler scans.
for _leased_job in (
    IngestionJob,
    ArticleImageJob,
    SourceEnrichmentJob,
    ArticleEnrichmentJob,
    ArticleAnalysisJob,
    TopicAnalysisJob,
    ResearchVerificationJob,
    NotificationDelivery,
):
    Index(
        f"ix_{_leased_job.__tablename__}_running_lease",
        _leased_job.lease_until,
        _leased_job.id,
        postgresql_where=_leased_job.status == "running",
    )
    Index(
        f"ix_{_leased_job.__tablename__}_finished_metrics",
        _leased_job.finished_at,
        postgresql_include=["status", "attempts", "created_at"],
        postgresql_where=_leased_job.finished_at.is_not(None),
    )

# Production EXPLAIN identified these wide-table inventory/active-state scans.
for _state_model, _state_column in (
    (ArticleAnalysisJob, ArticleAnalysisJob.status),
    (ArticleEnrichmentJob, ArticleEnrichmentJob.status),
    (NotificationDelivery, NotificationDelivery.status),
    (Article, Article.publication_status),
):
    Index(f"ix_{_state_model.__tablename__}_state_metrics", _state_column)


class InferenceCall(Base):
    """One row per transport invocation, separate from job-level cumulative counters."""

    __tablename__ = "inference_calls"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operation: Mapped[str] = mapped_column(String(50))
    job_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    attempt: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    reasoning_effort: Mapped[str | None] = mapped_column(String(30))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    tokens: Mapped[dict] = mapped_column(JSONB)
    web_searches: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)


class InferenceDailyUsage(Base):
    """Derived invocation totals, maintained by transactional ledger triggers."""

    __tablename__ = "inference_daily_usage"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    operation: Mapped[str] = mapped_column(String(50), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    model_is_null: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    effort: Mapped[str] = mapped_column(String(30), primary_key=True)
    calls: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    returned: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    failed: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    running: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    unreported: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    input_tokens: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    cached_input_tokens: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    output_tokens: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    reasoning_tokens: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    web_searches: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    duration_ms: Mapped[Decimal] = mapped_column(Numeric, server_default="0")


class TopicDecisionRun(Base):
    """One lifetime processing budget per proposal; metadata edits never reset it."""

    __tablename__ = "topic_decision_runs"
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topic_proposals.id", ondelete="CASCADE"), primary_key=True
    )
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    reason: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TopicDecisionCall(Base):
    """Indexed report facts, projected transactionally from each run's call ledger."""

    __tablename__ = "topic_decision_calls"
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topic_decision_runs.proposal_id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stage: Mapped[str | None] = mapped_column(Text)
    stage_call: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    tokens: Mapped[Decimal] = mapped_column(Numeric)
    charged_tokens: Mapped[int | None] = mapped_column(BigInteger)
    escalated: Mapped[bool] = mapped_column(Boolean)


class DiscoverySeed(Base):
    __tablename__ = "discovery_seeds"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    format: Mapped[str] = mapped_column(String(20))
    checksum: Mapped[str | None] = mapped_column(String(64))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceCandidate(Base):
    __tablename__ = "source_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','ready','unresolved','retry_wait','rejected','admitted','linked')"
        ),
        CheckConstraint(
            "approval_status IN ('pending','approved','rejected')",
            name="source_candidate_approval_status",
        ),
        Index("ix_source_candidates_status_created", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    identity_url: Mapped[str] = mapped_column(String(2048), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    approval_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    selected_feed_id: Mapped[uuid.UUID | None] = mapped_column(Uuid())
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL")
    )
    review: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CandidateDiscovery(Base):
    __tablename__ = "candidate_discoveries"
    __table_args__ = (UniqueConstraint("candidate_id", "origin_key"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_candidates.id", ondelete="CASCADE"), index=True
    )
    origin_key: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(2048))
    seed_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("discovery_seeds.id"))
    feed_hint: Mapped[str | None] = mapped_column(String(2048))
    checksum: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CandidateFeed(Base):
    __tablename__ = "candidate_feeds"
    __table_args__ = (UniqueConstraint("candidate_id", "url"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_candidates.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048))
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CandidateAssessment(Base):
    __tablename__ = "candidate_assessments"
    __table_args__ = (
        Index("ix_candidate_assessments_candidate_created", "candidate_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_candidates.id", ondelete="CASCADE")
    )
    feed_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidate_feeds.id"))
    version: Mapped[str] = mapped_column(String(40))
    evidence: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceDiscoveryJob(LeasedJobMixin, Base):
    __tablename__ = "source_discovery_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        CheckConstraint("stage IN ('discover','assess')"),
        Index(
            "uq_source_discovery_active",
            "candidate_id",
            "stage",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        # CLI v1 deliberately runs one crawler at a time across processes.
        Index(
            "uq_source_discovery_running",
            "status",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_source_discovery_due", "status", "available_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_candidates.id", ondelete="CASCADE")
    )
    stage: Mapped[str] = mapped_column(String(20), default="discover")
    error: Mapped[str | None] = mapped_column(String(100))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)


Index("ix_topics_active_name", Topic.name, Topic.id, postgresql_where=Topic.status == "active")
for _payload_model in (ArticleAnalysisJob, TopicAnalysisJob):
    Index(
        f"ix_{_payload_model.__tablename__}_retention",
        _payload_model.finished_at,
        _payload_model.id,
        postgresql_where=(_payload_model.status == "succeeded")
        & _payload_model.inputs_pruned_at.is_(None),
    )


class PartnerProduct(Base):
    """Canonical catalog identity; account policy and contextual matches govern delivery."""

    __tablename__ = "partner_products"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','paused','withdrawn')",
            name="ck_partner_status",
        ),
        CheckConstraint(
            "metadata_listing_id IS NULL OR metadata_account_id IS NULL",
            name="ck_partner_metadata_authority",
        ),
        Index("ix_partner_products_matching_tokens", "matching_tokens", postgresql_using="gin"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    product_url: Mapped[str] = mapped_column(String(2048))
    tagline: Mapped[str] = mapped_column(String(160))
    pricing: Mapped[str] = mapped_column(String(20), default="unknown")
    technologies: Mapped[list] = mapped_column(JSONB, default=list)
    # Each Unicode or punctuation-only technology adds __any__; exact rules remain authoritative.
    matching_tokens: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default="{}", server_onupdate=FetchedValue()
    )
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    managed_logo: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    managed_image: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    revision: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_onupdate=FetchedValue()
    )
    reviews: Mapped[list] = mapped_column(JSONB, default=list)
    assessment: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    assessment_revision: Mapped[int] = mapped_column(default=0, server_default="0")
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    metadata_listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partner_listings.id", use_alter=True, name="fk_partner_metadata_listing")
    )
    metadata_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="SET NULL")
    )
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partner_products.id"))


class PartnerListing(Base):
    __tablename__ = "partner_listings"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_partner_listing_identity"),
        CheckConstraint(
            "identity_status IN ('resolved', 'unresolved')",
            name="ck_partner_listing_identity_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partner_products.id"), index=True)
    provider: Mapped[str] = mapped_column(ForeignKey("partner_connections.provider"))
    external_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    product_url: Mapped[str] = mapped_column(String(2048))
    listing_url: Mapped[str] = mapped_column(String(2048))
    tagline: Mapped[str] = mapped_column(String(160))
    pricing: Mapped[str] = mapped_column(String(20), default="unknown")
    attribution: Mapped[str] = mapped_column(String(300), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    identity_status: Mapped[str] = mapped_column(String(20), default="resolved")
    identity_reason: Mapped[str | None] = mapped_column(Text)
    seen_generation: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PartnerProductURL(Base):
    __tablename__ = "partner_product_urls"
    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(String(2048))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partner_products.id"), index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PartnerEvaluation(LeasedJobMixin, Base):
    __tablename__ = "partner_evaluations"
    __table_args__ = (
        Index(
            "uq_partner_evaluation_active",
            "product_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        Index("ix_partner_evaluation_created", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_products.id", ondelete="CASCADE"), index=True
    )
    snapshot: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    reviews: Mapped[list] = mapped_column(JSONB, default=list)
    requested_by: Mapped[dict] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class PartnerConnection(Base):
    __tablename__ = "partner_connections"
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="RESTRICT"), index=True
    )
    __table_args__ = (
        CheckConstraint(
            "sync_interval_minutes BETWEEN 1 AND 10080", name="ck_partner_sync_interval"
        ),
        CheckConstraint("partnership_type = 'launch_platform'", name="ck_partner_connection_type"),
        Index(
            "ix_partner_connections_active_account",
            "account_id",
            "name",
            "provider",
            postgresql_where=text("archived_at IS NULL"),
        ),
    )
    partnership_type: Mapped[str] = mapped_column(
        String(40), default="launch_platform", server_default="launch_platform"
    )
    provider: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    logo_url: Mapped[str | None] = mapped_column(String(2048))
    managed_logo: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    connector: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    encrypted_credential: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(default=1)
    sync_revision: Mapped[int] = mapped_column(default=1, server_default="1")
    sync_interval_minutes: Mapped[int] = mapped_column(default=360, server_default="360")
    next_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[dict] = mapped_column(JSONB, default=dict)


class PartnerPipelineJob(LeasedJobMixin, Base):
    __tablename__ = "partner_pipeline_jobs"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('sync','sync_product','assess')", name="ck_partner_pipeline_operation"
        ),
        CheckConstraint(
            "operation = 'assess' OR provider IS NOT NULL", name="ck_partner_pipeline_provider"
        ),
        UniqueConstraint("parent_id", "external_id", name="uq_partner_product_sync"),
        Index(
            "uq_partner_sync_active",
            "provider",
            unique=True,
            postgresql_where=text("operation = 'sync' AND status IN ('queued','running')"),
        ),
        Index(
            "uq_partner_assessment_active",
            "product_id",
            unique=True,
            postgresql_where=text("operation = 'assess' AND status IN ('queued','running')"),
        ),
        Index("ix_partner_pipeline_due", "status", "available_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str | None] = mapped_column(ForeignKey("partner_connections.provider"))
    operation: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partner_products.id", ondelete="CASCADE")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partner_pipeline_jobs.id"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    @hybrid_property
    def display_status(self) -> str:
        """A catalog operation stays in progress while its page/child tasks requeue."""
        if self.operation == "sync" and self.status == "queued":
            payload = self.payload if isinstance(self.payload, dict) else {}
            pages = payload.get("pages")
            if (
                self.attempts > 0
                or (isinstance(pages, (int, float)) and not isinstance(pages, bool) and pages > 0)
                or payload.get("discovery_complete") is True
            ):
                return "running"
        return self.status

    @display_status.inplace.expression
    @classmethod
    def _display_status_expression(cls) -> ColumnElement[str]:
        return case(
            (
                and_(
                    cls.operation == "sync",
                    cls.status == "queued",
                    or_(
                        cls.attempts > 0,
                        and_(
                            func.jsonb_typeof(cls.payload["pages"]) == "number",
                            cls.payload["pages"] > literal(0, type_=JSONB),
                        ),
                        cls.payload["discovery_complete"] == literal(True, type_=JSONB),
                    ),
                ),
                "running",
            ),
            else_=cls.status,
        )


class PartnerAccount(Base):
    """Commercial partner identity, independent of catalog import providers."""

    __tablename__ = "partner_accounts"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(link_rel) = 'array' AND jsonb_array_length(link_rel) <= 4 AND "
            'link_rel <@ \'["nofollow","sponsored","ugc","external"]\'::jsonb AND '
            "jsonb_array_length(link_rel) = ((link_rel ? 'nofollow')::int + "
            "(link_rel ? 'sponsored')::int + (link_rel ? 'ugc')::int + "
            "(link_rel ? 'external')::int)",
            name="ck_partner_account_link_rel",
        ),
        CheckConstraint("status IN ('active','paused')", name="ck_partner_account_status"),
        CheckConstraint("jsonb_typeof(audience) = 'object'", name="ck_partner_account_audience"),
        CheckConstraint(
            "tier IN ('bronze','silver','gold','platinum','diamond')",
            name="ck_partner_account_tier",
        ),
        CheckConstraint(
            "approval_mode IN ('qualified','trusted')", name="ck_partner_approval_mode"
        ),
        CheckConstraint(
            "outbound_destination IN ('product_url','listing_url')",
            name="ck_partner_outbound_destination",
        ),
        CheckConstraint(
            "promotion_kind IS NULL OR promotion_kind IN ('product','ad')",
            name="ck_partner_promotion_kind",
        ),
        CheckConstraint(
            "promotion_kind IS DISTINCT FROM 'ad' OR "
            "tier IN ('silver','gold','platinum','diamond')",
            name="ck_partner_promotion_entitlement",
        ),
        CheckConstraint(
            "jsonb_typeof(promotion_surfaces) = 'array' AND "
            "jsonb_array_length(promotion_surfaces) BETWEEN 1 AND 5 AND "
            'promotion_surfaces <@ \'["article","topic","feed","search","header"]\'::jsonb',
            name="ck_partner_promotion_surfaces",
        ),
        CheckConstraint(
            "jsonb_typeof(ad_formats) = 'array' AND jsonb_array_length(ad_formats) <= 3 AND "
            'ad_formats <@ \'["feed","header_sponsorship","engagement"]\'::jsonb',
            name="ck_partner_account_ad_formats",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    tier: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    organic_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    auto_include_products: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    promotion_surfaces: Mapped[list] = mapped_column(
        JSONB,
        default=default_promotion_surfaces,
        server_default='["article","topic","feed","search","header"]',
    )
    promotion_kind: Mapped[str | None] = mapped_column(String(20))
    approval_mode: Mapped[str] = mapped_column(
        String(20), default="qualified", server_default="qualified"
    )
    outbound_destination: Mapped[str] = mapped_column(
        String(20), default="product_url", server_default="product_url"
    )
    audience: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    ad_formats: Mapped[list] = mapped_column(
        JSONB, default=lambda: ["feed"], server_default='["feed"]'
    )
    link_rel: Mapped[list] = mapped_column(
        JSONB, default=default_link_rel, server_default='["nofollow"]'
    )

    @property
    def configurable_link_rel(self) -> bool:
        from .partner_tiers import tier_policy

        return tier_policy(self.tier)["configurable_link_rel"]

    @property
    def link_rel_options(self) -> list[str]:
        from .partner_link_rel import LINK_REL_OPTIONS

        return list(LINK_REL_OPTIONS)

    @property
    def allowed_ad_formats(self) -> list[str]:
        from .partner_tiers import tier_policy

        return list(tier_policy(self.tier)["allowed_ad_formats"])

    @property
    def advanced_targeting(self) -> bool:
        from .partner_tiers import tier_policy

        return tier_policy(self.tier)["advanced_targeting"]

    @property
    def allowed_promotion_kinds(self) -> list[str]:
        from .partner_tiers import tier_policy

        return list(tier_policy(self.tier)["allowed_promotion_kinds"])

    @property
    def effective_promotion_kind(self) -> str:
        from .partner_tiers import effective_promotion_kind

        return effective_promotion_kind(self)

    @property
    def promotion_kind_source(self) -> str:
        return "account" if self.promotion_kind is not None else "tier"

    @property
    def benefits(self) -> list[str]:
        from .partner_tiers import TIER_BENEFITS

        return list(TIER_BENEFITS[self.tier])


class PartnerManualProduct(Base):
    """Explicit account inventory, independent of external launch platform imports."""

    __tablename__ = "partner_manual_products"
    __table_args__ = (Index("ix_partner_manual_products_product_active", "product_id", "active"),)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_products.id", ondelete="CASCADE"), primary_key=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


class PartnerPromotionEnrollment(Base):
    """Resumable automatic asset enrollment for one partner account."""

    __tablename__ = "partner_promotion_enrollments"
    __table_args__ = (
        Index("ix_partner_promotion_enrollment_due", "available_at", "updated_at", "account_id"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    product_cursor: Mapped[uuid.UUID | None]
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    sweep_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PartnerCampaign(Base):
    """Explicit agreements and the stable inherited account sponsorship ledger."""

    __tablename__ = "partner_campaigns"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_partner_campaign_account"),
        CheckConstraint(
            "audience IS NULL OR jsonb_typeof(audience) = 'object'",
            name="ck_partner_campaign_audience",
        ),
        CheckConstraint(
            "ad_format IN ('feed','header_sponsorship','sponsored_content','engagement')",
            name="ck_partner_campaign_ad_format",
        ),
        CheckConstraint(
            "(ad_format = 'sponsored_content') = (article_id IS NOT NULL)",
            name="ck_partner_campaign_article",
        ),
        CheckConstraint(
            "status IN ('draft','active','paused','ended')", name="ck_partner_campaign_status"
        ),
        CheckConstraint(
            "targeting IN ('all','selected','filters')", name="ck_partner_campaign_targeting"
        ),
        CheckConstraint(
            "ends_at > starts_at AND isfinite(starts_at) AND isfinite(ends_at)",
            name="ck_partner_campaign_dates",
        ),
        CheckConstraint("priority BETWEEN 1 AND 100", name="ck_partner_campaign_priority"),
        CheckConstraint(
            "daily_impression_limit IS NULL OR daily_impression_limit > 0",
            name="ck_partner_campaign_daily_limit",
        ),
        CheckConstraint(
            "jsonb_typeof(surfaces) = 'array' AND jsonb_array_length(surfaces) BETWEEN 1 AND 5 AND "
            'surfaces <@ \'["article","topic","feed","search","header"]\'::jsonb AND '
            "(ad_format = 'engagement' OR NOT surfaces @> '[\"search\"]'::jsonb) AND "
            "((ad_format = 'header_sponsorship' AND surfaces = '[\"header\"]'::jsonb) OR "
            "(ad_format != 'header_sponsorship' AND NOT surfaces @> '[\"header\"]'::jsonb))",
            name="ck_partner_campaign_surfaces",
        ),
        CheckConstraint(
            "jsonb_typeof(product_ids) = 'array' AND jsonb_typeof(topic_ids) = 'array' AND "
            "jsonb_typeof(technologies) = 'array' AND "
            "((targeting = 'all' AND jsonb_array_length(product_ids) = 0 AND "
            "jsonb_array_length(topic_ids) = 0 AND jsonb_array_length(technologies) = 0) OR "
            "(targeting = 'selected' AND jsonb_array_length(product_ids) > 0 AND "
            "jsonb_array_length(topic_ids) = 0 AND jsonb_array_length(technologies) = 0) OR "
            "(targeting = 'filters' AND jsonb_array_length(product_ids) = 0 AND "
            "(jsonb_array_length(topic_ids) > 0 OR jsonb_array_length(technologies) > 0)))",
            name="ck_partner_campaign_selection",
        ),
        Index("ix_partner_campaign_live", "status", "starts_at", "ends_at"),
        Index(
            "ix_partner_campaign_article_sponsor",
            "article_id",
            "id",
            postgresql_where=text("ad_format = 'sponsored_content' AND status = 'active'"),
        ),
        Index(
            "uq_partner_managed_campaign",
            "account_id",
            "ad_format",
            unique=True,
            postgresql_where=text("managed"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    targeting: Mapped[str] = mapped_column(String(20), default="all", server_default="all")
    ad_format: Mapped[str] = mapped_column(String(30), default="feed", server_default="feed")
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("articles.id", ondelete="RESTRICT")
    )
    product_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    topic_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    technologies: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    audience: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    surfaces: Mapped[list] = mapped_column(
        JSONB,
        default=lambda context: default_campaign_surfaces(
            context.get_current_parameters().get("ad_format", "feed")
        ),
        server_default='["article","topic","feed"]',
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(default=50, server_default="50")
    daily_impression_limit: Mapped[int | None] = mapped_column(BigInteger)
    managed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PartnerProductPromotion(Base):
    """Account-scoped commercial acceptance; factual qualification remains independent."""

    __tablename__ = "partner_product_promotions"
    __table_args__ = (
        CheckConstraint(
            "link_rel IS NULL OR (jsonb_typeof(link_rel) = 'array' AND "
            "jsonb_array_length(link_rel) <= 4 AND "
            'link_rel <@ \'["nofollow","sponsored","ugc","external"]\'::jsonb AND '
            "jsonb_array_length(link_rel) = ((link_rel ? 'nofollow')::int + "
            "(link_rel ? 'sponsored')::int + (link_rel ? 'ugc')::int + "
            "(link_rel ? 'external')::int))",
            name="ck_partner_product_link_rel",
        ),
        CheckConstraint(
            "promotion_surfaces IS NULL OR (jsonb_typeof(promotion_surfaces) = 'array' AND "
            "jsonb_array_length(promotion_surfaces) <= 5 AND "
            'promotion_surfaces <@ \'["article","topic","feed","search","header"]\'::jsonb)',
            name="ck_partner_product_surfaces",
        ),
        CheckConstraint(
            "ad_formats IS NULL OR (jsonb_typeof(ad_formats) = 'array' AND "
            "jsonb_array_length(ad_formats) <= 4 AND "
            'ad_formats <@ \'["feed","header_sponsorship",'
            '"sponsored_content","engagement"]\'::jsonb)',
            name="ck_partner_product_ad_formats",
        ),
        CheckConstraint(
            "(commercial_approved_at IS NULL AND approval_source IS NULL) OR "
            "(commercial_approved_at IS NOT NULL AND approval_source IS NOT NULL "
            "AND approval_source = 'trusted')",
            name="ck_partner_commercial_approval",
        ),
        CheckConstraint("ad_image_revision >= 0", name="ck_partner_ad_image_revision"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_products.id", ondelete="CASCADE"), primary_key=True
    )
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    promotion_surfaces: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    ad_formats: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    link_rel: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    ad_image_url: Mapped[str | None] = mapped_column(String(2048))
    managed_ad_image: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    ad_image_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    commercial_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_source: Mapped[str | None] = mapped_column(String(20))


class PartnerProductTopic(Base):
    __tablename__ = "partner_product_topics"
    __table_args__ = (Index("ix_partner_product_topic_score", "topic_id", "score"),)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_products.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    product_revision: Mapped[int] = mapped_column()
    product_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    topic_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PartnerArticleMatch(Base):
    __tablename__ = "partner_article_matches"
    __table_args__ = (Index("ix_partner_article_match_score", "article_id", "relevant", "score"),)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_products.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    product_revision: Mapped[int] = mapped_column()
    product_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    editorial_revision: Mapped[int] = mapped_column()
    article_fingerprint: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(20), default="rules", server_default="rules")
    relevant: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PartnerMatchJob(Base):
    """Coalesced factual changes; source deletion survives as a job without a foreign key."""

    __tablename__ = "partner_match_jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('product','article','topics')", name="ck_partner_match_job_kind"),
        CheckConstraint("generation > 0", name="ck_partner_match_job_generation"),
        Index("ix_partner_match_job_due", "updated_at", "kind", "entity_id"),
    )
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")
    cursor: Mapped[uuid.UUID | None]
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


class PartnerMembership(Base):
    """Issuer/subject is the immutable identity; an email is never an access grant."""

    __tablename__ = "partner_memberships"
    __table_args__ = (
        Index("ix_partner_memberships_identity_accounts", "issuer", "subject", "account_id"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    issuer: Mapped[str] = mapped_column(String(2048), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PartnerAsset(Base):
    """Account-owned product placement or advertisement, with durable attribution."""

    __tablename__ = "partner_assets"
    __table_args__ = (
        CheckConstraint("kind IN ('product','ad')", name="ck_partner_asset_kind"),
        CheckConstraint(
            "ad_format IN ('feed','header_sponsorship','sponsored_content','engagement')",
            name="ck_partner_asset_ad_format",
        ),
        CheckConstraint(
            "(ad_format = 'sponsored_content') = (article_id IS NOT NULL)",
            name="ck_partner_asset_article",
        ),
        CheckConstraint(
            "kind != 'product' OR product_id IS NOT NULL", name="ck_partner_asset_product"
        ),
        CheckConstraint(
            "status IN ('draft','active','paused','ended')", name="ck_partner_asset_status"
        ),
        CheckConstraint(
            "NOT automatic OR (product_id IS NOT NULL AND ((campaign_id IS NULL AND kind = "
            "'product') OR (campaign_id IS NOT NULL AND kind = 'ad')))",
            name="ck_partner_automatic_asset_identity",
        ),
        ForeignKeyConstraint(
            ["account_id", "campaign_id"],
            ["partner_campaigns.account_id", "partner_campaigns.id"],
            name="fk_partner_asset_campaign_account",
            ondelete="RESTRICT",
        ),
        Index("ix_partner_asset_product", "product_id", "id"),
        Index(
            "ix_partner_asset_article_sponsor",
            "article_id",
            "account_id",
            "product_id",
            postgresql_where=text("ad_format = 'sponsored_content'"),
        ),
        Index(
            "uq_partner_automatic_organic_asset",
            "account_id",
            "product_id",
            "ad_format",
            unique=True,
            postgresql_where=text("automatic AND campaign_id IS NULL"),
        ),
        Index(
            "uq_partner_automatic_sponsored_asset",
            "account_id",
            "product_id",
            "campaign_id",
            "ad_format",
            "article_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("automatic AND campaign_id IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partner_accounts.id"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partner_products.id"))
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    ad_format: Mapped[str] = mapped_column(String(30), default="feed", server_default="feed")
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("articles.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PartnerPlacementDelivery(Base):
    """Durable issued-receipt reservations prevent concurrent campaign overspending."""

    __tablename__ = "partner_placement_deliveries"
    __table_args__ = (
        Index("ix_partner_delivery_asset_issued", "asset_id", "issued_at"),
        CheckConstraint(
            "surface IS NULL OR surface IN ('article','topic','feed','search','header')",
            name="ck_partner_delivery_surface",
        ),
    )
    delivery_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_assets.id", ondelete="RESTRICT"), index=True
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    surface: Mapped[str | None] = mapped_column(String(20))


class PartnerDailyMetric(Base):
    """Externally imported totals, independent of tracked delivery counters."""

    __tablename__ = "partner_daily_metrics"
    __table_args__ = (
        CheckConstraint("impressions >= 0 AND clicks >= 0", name="ck_partner_metric_nonnegative"),
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partner_assets.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    impressions: Mapped[int] = mapped_column(BigInteger)
    clicks: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PartnerDeliveryEvent(Base):
    """One impression and one click per anonymous placement delivery."""

    __tablename__ = "partner_delivery_events"
    __table_args__ = (
        CheckConstraint("kind IN ('impression','click')", name="ck_partner_delivery_kind"),
        Index("ix_partner_delivery_event_day", "day"),
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partner_assets.id"), primary_key=True)
    delivery_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    day: Mapped[date] = mapped_column(Date)


class PartnerTrackedDailyMetric(Base):
    """Atomic live counters, independent of externally imported totals."""

    __tablename__ = "partner_tracked_daily_metrics"
    __table_args__ = (
        CheckConstraint("impressions >= 0 AND clicks >= 0", name="ck_partner_tracked_nonnegative"),
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("partner_assets.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    impressions: Mapped[int] = mapped_column(BigInteger)
    clicks: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserMustRead(Base):
    """Stable account-private daily selection and cross-device presentation claim."""

    __tablename__ = "user_must_reads"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    selection_date: Mapped[date] = mapped_column(Date, primary_key=True)
    timezone: Mapped[str] = mapped_column(String(100))
    picks: Mapped[list] = mapped_column(JSONB, default=list)
    presented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# The overview selects one latest run per article before inspecting result JSON.
for _job_model, _index_name in (
    (ArticleAnalysisJob, "ix_article_analysis_latest"),
    (ArticleEnrichmentJob, "ix_article_enrichment_latest"),
):
    Index(_index_name, _job_model.article_id, _job_model.created_at.desc(), _job_model.id.desc())


for _target in (TopicAnalysisJob.topic_id, TopicAnalysisJob.proposal_id):
    Index(
        f"ix_topic_analysis_{_target.key}_newer",
        _target,
        TopicAnalysisJob.created_at,
        TopicAnalysisJob.id,
        postgresql_where=_target.is_not(None),
    )


class PipelineProjection(Base):
    """Disposable operator read model; durable entities retain mutation authority."""

    __tablename__ = "pipeline_projections"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('article','source','candidate')", name="ck_pipeline_projection_kind"
        ),
    )

    policy_version: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    stage: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[uuid.UUID | None] = mapped_column()
    source_name: Mapped[str | None] = mapped_column(String(200))
    search_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    has_active_work: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    next_transition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index(
    "ix_pipeline_projection_lane",
    PipelineProjection.policy_version,
    PipelineProjection.kind,
    PipelineProjection.stage,
    # Preserve PostgreSQL's reflected CASE layout for Alembic expression comparison.
    text("(\nCASE\n    WHEN state::text = 'running'::text THEN 0\n    ELSE 1\nEND)"),
    PipelineProjection.created_at,
    PipelineProjection.entity_id,
)
Index(
    "ix_pipeline_projection_source",
    PipelineProjection.policy_version,
    PipelineProjection.kind,
    PipelineProjection.source_id,
    PipelineProjection.stage,
    PipelineProjection.state,
)
Index(
    "ix_pipeline_projection_terminal",
    PipelineProjection.policy_version,
    PipelineProjection.kind,
    PipelineProjection.stage,
    PipelineProjection.terminal_at.desc(),
    PipelineProjection.entity_id,
)
Index(
    "ix_pipeline_projection_transition",
    PipelineProjection.policy_version,
    PipelineProjection.next_transition_at,
    PipelineProjection.kind,
    PipelineProjection.entity_id,
    postgresql_where=PipelineProjection.next_transition_at.is_not(None),
)


class ReadModelEvent(Base):
    """Transactional changes survive crashes, deletes, and out-of-order commits."""

    __tablename__ = "read_model_events"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('article','source','candidate','catalog','policy',"
            "'backfill_article','backfill_source','backfill_candidate','source_fanout')",
            name="ck_read_model_event_kind",
        ),
        CheckConstraint(
            "entity_id IS NOT NULL OR kind IN ('catalog','policy',"
            "'backfill_article','backfill_source','backfill_candidate')",
            name="ck_read_model_event_entity",
        ),
        Index("ix_read_model_event_created", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    after_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


class ReadModelState(Base):
    __tablename__ = "read_model_states"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


class ReportSnapshot(Base):
    """Last complete report, independent of refresh availability or failure."""

    __tablename__ = "report_snapshots"
    __table_args__ = (CheckConstraint("days BETWEEN 1 AND 90", name="ck_report_snapshot_days"),)

    group: Mapped[str] = mapped_column(String(50), primary_key=True)
    days: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_version: Mapped[str] = mapped_column(String(100))


class ReportRefresh(Base):
    """Durable demand and token-guarded refresh leases for report snapshots."""

    __tablename__ = "report_refreshes"
    __table_args__ = (
        CheckConstraint("days BETWEEN 1 AND 90", name="ck_report_refresh_days"),
        CheckConstraint("attempts >= 0", name="ck_report_refresh_attempts"),
        Index("ix_report_refresh_due", "available_at", "group", "days"),
    )

    group: Mapped[str] = mapped_column(String(50), primary_key=True)
    days: Mapped[int] = mapped_column(Integer, primary_key=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column()
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(100))
