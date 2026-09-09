"""Imports and evidence can propose taxonomy changes; only a review applies them."""

import csv
import hashlib
import io
import json
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Literal

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import (
    Article,
    ArticleTag,
    ArticleTopic,
    Tag,
    Topic,
    TopicProposal,
    utcnow,
)
from devfeed_core.schemas import (
    InputModel,
    Keyword,
    Name,
    ReviewNote,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topics import TopicWrite, identity_terms, lock_topics, save_topic

MAX_IMPORT_BYTES = 262_144
MAX_IMPORT_ROWS = 100


class TopicDraft(TopicWrite):
    """Reviewed topic fields; lifecycle status cannot be supplied by imports."""


class TopicImport(InputModel):
    format: Literal["json", "csv"]
    content: str = Field(min_length=1, max_length=MAX_IMPORT_BYTES)
    source_name: Name


class TopicImportSubmit(TopicImport):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")


class TopicImportRow(InputModel):
    row: int
    action: Literal["create", "update", "unchanged", "invalid"]
    topic: TopicDraft | None = None
    issues: list[str] = Field(default_factory=list)


class TopicImportPreview(InputModel):
    rows: list[TopicImportRow]
    preview_token: str
    can_submit: bool


class TopicReview(InputModel):
    decision: Literal["approved", "rejected"]
    topic: TopicDraft | None = None
    note: ReviewNote | None = None
    expected_input_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def approval_requires_reviewed_fields(self):
        if self.decision == "approved" and self.topic is None:
            raise ValueError("Approval requires the reviewed topic fields")
        if self.decision == "rejected" and self.topic is not None:
            raise ValueError("Rejection cannot apply topic fields")
        return self


class TopicAnalysisOut(InputModel):
    id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    model: str | None
    created_at: datetime
    finished_at: datetime | None
    outcome: str | None
    error: str | None
    reasons: list[str] = Field(default_factory=list)


class TopicProposalOut(InputModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    topic_id: uuid.UUID | None
    action: Literal["create", "update"]
    origin: Literal["import", "article_enrichment", "ai_analysis"]
    source_name: str
    proposed: TopicDraft
    before: TopicDraft | None
    evidence: list[dict]
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime
    created_by: dict[str, str]
    reviewed_at: datetime | None
    reviewed_by: dict[str, str] | None
    review_note: str | None
    applied: TopicDraft | None
    content_hash: str | None = None
    analysis: TopicAnalysisOut | None = None


class KeywordSuggestion(InputModel):
    keyword: str
    article_count: int
    articles: list[dict[str, str]]


class TopicEnrichmentPreview(InputModel):
    topic: TopicDraft
    preview_token: str
    articles_examined: int
    suggestions: list[KeywordSuggestion]


class TopicEnrichmentSubmit(InputModel):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    keywords: list[Keyword] = Field(min_length=1, max_length=25)


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def topic_draft(topic: Topic) -> TopicDraft:
    return TopicDraft.model_validate(
        {name: getattr(topic, name) for name in TopicDraft.model_fields}
    )


def snapshot(topic: Topic) -> dict:
    return {
        "id": str(topic.id),
        "status": topic.status,
        "draft": topic_draft(topic).model_dump(mode="json"),
    }


def catalogs(session: Session) -> dict[str, Topic]:
    return {t.slug: t for t in session.scalars(select(Topic).order_by(Topic.slug))}


def proposal_view(proposal: TopicProposal, analysis=None) -> TopicProposalOut:
    return TopicProposalOut(
        **{
            name: getattr(proposal, name)
            for name in TopicProposalOut.model_fields
            if name not in {"before", "applied", "content_hash", "analysis"}
        },
        content_hash=snapshot_hash(proposal.proposed),
        analysis=TopicAnalysisOut(
            **{
                name: getattr(analysis, name)
                for name in TopicAnalysisOut.model_fields
                if name != "reasons"
            },
            reasons=(analysis.result or {}).get("reasons", []),
        )
        if analysis
        else None,
        before=proposal.baseline["draft"] if proposal.baseline else None,
        applied=proposal.applied["draft"] if proposal.applied else None,
    )


def parse_import(body: TopicImport) -> list[dict]:
    if len(body.content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ValueError("Topic imports are limited to 256 KiB")
    content = body.content.lstrip("\ufeff")
    if body.format == "json":

        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON field")
                result[key] = value
            return result

        try:
            rows = json.loads(content, object_pairs_hook=unique_keys)
        except (ValueError, RecursionError) as exc:
            raise ValueError("Import must be a valid JSON array without duplicate fields") from exc
    else:
        try:
            reader = csv.DictReader(io.StringIO(content), strict=True)
            fields = reader.fieldnames or []
            if len(fields) != len(set(fields)) or not {"name", "slug"}.issubset(fields):
                raise ValueError("CSV requires unique headers including name and slug")
            if set(fields) - set(TopicDraft.model_fields):
                raise ValueError("CSV contains unsupported columns")
            rows = []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("CSV row does not match its headers")
                for name in ("keywords", "aliases"):
                    if name in row:
                        row[name] = [v.strip() for v in row[name].split("|") if v.strip()]
                if "facts" in row:
                    row["facts"] = json.loads(row["facts"] or "[]")
                for name in ("description", "website_url", "logo_url"):
                    if name in row and not row[name].strip():
                        row[name] = None
                rows.append(row)
                if len(rows) > MAX_IMPORT_ROWS:
                    raise ValueError("Import at most 100 topics at a time")
        except csv.Error as exc:
            raise ValueError("Invalid CSV import") from exc
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_IMPORT_ROWS:
        raise ValueError("Import a JSON array or CSV file containing 1 to 100 topics")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Each topic must be an object")
    return rows


def preview_import(session: Session, body: TopicImport) -> TopicImportPreview:
    imported = parse_import(body)
    catalog = catalogs(session)
    pending = set(
        session.scalars(select(TopicProposal.slug).where(TopicProposal.status == "pending"))
    )
    results: list[TopicImportRow] = []
    seen: set[str] = set()
    for number, raw in enumerate(imported, 1):
        try:
            parsed = TopicDraft.model_validate(raw)
            existing = catalog.get(parsed.slug)
            # Missing columns retain existing values; explicit blanks/nulls clear optional fields.
            draft = TopicDraft.model_validate(
                {
                    **(topic_draft(existing).model_dump() if existing else {}),
                    **parsed.model_dump(exclude_unset=True),
                }
            )
            issues = []
            if draft.slug in seen:
                issues.append("Duplicate slug in this import")
            if draft.slug in pending:
                issues.append("Review the existing pending proposal for this slug first")
            seen.add(draft.slug)
            for other in catalog.values():
                if other.slug != draft.slug and identity_terms(other) & identity_terms(draft):
                    issues.append(
                        f'Name or slug matches existing topic "{other.name}" ({other.slug})'
                    )
            for other_row in results:
                if other_row.topic and identity_terms(other_row.topic) & identity_terms(draft):
                    issues.append(f"Name or slug matches imported row {other_row.row}")
            if existing and existing.status != "active":
                issues.append("Only active topics can be updated through imports")
            action = "update" if existing else "create"
            if existing and draft == topic_draft(existing):
                action = "unchanged"
            results.append(TopicImportRow(row=number, action=action, topic=draft, issues=issues))
        except ValidationError as exc:
            issues = [
                f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                for error in exc.errors(include_input=False)
            ]
            results.append(TopicImportRow(row=number, action="invalid", issues=issues))
    # Include current records so an import preview cannot silently overwrite later edits.
    token = fingerprint(
        {
            "source": body.model_dump(),
            "rows": [r.model_dump(mode="json") for r in results],
            "catalog": [snapshot(c) for c in catalog.values()],
        }
    )
    return TopicImportPreview(
        rows=results,
        preview_token=token,
        can_submit=(
            all(not r.issues for r in results)
            and any(r.action in {"create", "update"} for r in results)
        ),
    )


def submit_import(
    session: Session, body: TopicImportSubmit, actor: dict[str, str]
) -> list[TopicProposal]:
    lock_topics(session)
    source = TopicImport.model_validate(body.model_dump(exclude={"preview_token"}))
    preview = preview_import(session, source)
    if preview.preview_token != body.preview_token:
        raise OperationConflict("The import or catalog changed. Preview the import again")
    if not preview.can_submit:
        raise OperationConflict("Resolve all import issues before submitting proposals")
    catalog = catalogs(session)
    batch_id = uuid.uuid4()
    proposals = []
    for row in preview.rows:
        if row.action == "unchanged" or row.topic is None:
            continue
        existing = catalog.get(row.topic.slug)
        proposal = TopicProposal(
            batch_id=batch_id,
            topic_id=existing.id if existing else None,
            slug=row.topic.slug,
            action=row.action,
            origin="import",
            source_name=body.source_name,
            proposed=row.topic.model_dump(mode="json"),
            baseline=snapshot(existing) if existing else None,
            evidence=[
                {"row": row.row, "format": body.format, "content_hash": fingerprint(body.content)}
            ],
            created_by=actor,
        )
        session.add(proposal)
        proposals.append(proposal)
    session.flush()
    return proposals


def review_proposal(
    session: Session, identifier: uuid.UUID, body: TopicReview, actor: dict[str, str]
) -> TopicProposal:
    # Every taxonomy writer takes the same lock before touching proposal/topic rows.
    lock_topics(session)
    proposal = session.scalar(
        select(TopicProposal).where(TopicProposal.id == identifier).with_for_update()
    )
    if proposal is None:
        raise RecordNotFound("Topic proposal not found")
    if proposal.status != "pending":
        raise OperationConflict("This proposal has already been reviewed")
    if body.expected_input_hash and body.expected_input_hash != snapshot_hash(proposal.proposed):
        raise OperationConflict("Proposal changed. Reload it before reviewing")
    if body.decision == "approved":
        assert body.topic is not None
        catalog = catalogs(session)
        existing = session.get(Topic, proposal.topic_id) if proposal.topic_id else None
        if proposal.action == "update" and (
            existing is None or snapshot(existing) != proposal.baseline
        ):
            raise OperationConflict(
                "Topic changed after this proposal. Reject it and create a fresh proposal"
            )
        draft = body.topic
        duplicate = catalog.get(draft.slug)
        if duplicate is not None and (existing is None or duplicate.id != existing.id):
            raise OperationConflict("A topic with this slug already exists")
        topic = save_topic(session, draft, existing.id if existing else None)
        catalog[topic.slug] = topic
        proposal.topic_id = topic.id
        proposal.applied = snapshot(topic)
    proposal.status = body.decision
    proposal.reviewed_at = utcnow()
    proposal.reviewed_by = actor
    proposal.review_note = body.note
    session.flush()
    return proposal


def preview_enrichment(session: Session, topic_id: uuid.UUID) -> TopicEnrichmentPreview:
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise RecordNotFound("Topic not found")
    if topic.status != "active":
        raise OperationConflict("Only active topics can be enriched")
    articles = session.scalars(
        select(Article)
        .join(ArticleTopic)
        .where(
            ArticleTopic.topic_id == topic_id,
            ArticleTopic.role.in_(["primary", "supporting"]),
            Article.review_status == "approved",
            Article.publication_status == "published",
        )
        .order_by(Article.published_to_feed_at.desc(), Article.id)
        .limit(200)
    ).all()
    article_map = {
        a.id: {"id": str(a.id), "title": a.title, "url": a.canonical_url} for a in articles
    }
    evidence: dict[str, set[uuid.UUID]] = defaultdict(set)
    spelling: dict[str, str] = {}
    existing = {v.casefold() for v in topic.keywords}
    if article_map:
        for article_id, tag in session.execute(
            select(ArticleTag.article_id, Tag)
            .join(Tag, Tag.id == ArticleTag.tag_id)
            .where(ArticleTag.article_id.in_(article_map))
            .order_by(Tag.slug, ArticleTag.article_id)
        ):
            for value in [tag.name, *tag.aliases]:
                term = value.strip()
                if term and len(term) <= 100 and term.casefold() not in existing:
                    evidence[term.casefold()].add(article_id)
                    spelling.setdefault(term.casefold(), term)
    suggestions = [
        KeywordSuggestion(
            keyword=spelling[term],
            article_count=len(ids),
            articles=[article_map[identifier] for identifier in sorted(ids)[:5]],
        )
        for term, ids in sorted(evidence.items(), key=lambda item: (-len(item[1]), item[0]))
        if len(ids) >= 2
    ]
    suggestions = suggestions[: max(0, min(25, 100 - len(topic.keywords)))]
    token = fingerprint(
        {
            "topic": snapshot(topic),
            "suggestions": [s.model_dump() for s in suggestions],
        }
    )
    return TopicEnrichmentPreview(
        topic=topic_draft(topic),
        preview_token=token,
        articles_examined=len(articles),
        suggestions=suggestions,
    )


def submit_enrichment(
    session: Session, topic_id: uuid.UUID, body: TopicEnrichmentSubmit, actor: dict[str, str]
) -> TopicProposal:
    lock_topics(session)
    preview = preview_enrichment(session, topic_id)
    if preview.preview_token != body.preview_token:
        raise OperationConflict("The topic or evidence changed. Preview enrichment again")
    selected = set(body.keywords)
    suggestions = {s.keyword: s for s in preview.suggestions}
    if not selected.issubset(suggestions):
        raise OperationConflict("Select only keywords supported by the enrichment preview")
    if session.scalar(
        select(TopicProposal.id).where(
            TopicProposal.status == "pending",
            TopicProposal.topic_id == topic_id,
        )
    ):
        raise OperationConflict("Review this topic's pending proposal first")
    catalog = catalogs(session)
    topic = catalog[preview.topic.slug]
    proposed = preview.topic.model_copy(
        update={"keywords": [*preview.topic.keywords, *sorted(selected)]}
    )
    proposal = TopicProposal(
        batch_id=uuid.uuid4(),
        topic_id=topic_id,
        slug=topic.slug,
        action="update",
        origin="article_enrichment",
        source_name="Tags on approved, published articles",
        proposed=proposed.model_dump(mode="json"),
        baseline=snapshot(topic),
        evidence=[suggestions[key].model_dump() for key in sorted(selected)],
        created_by=actor,
    )
    session.add(proposal)
    session.flush()
    return proposal


def propose_analysis_topics(session: Session, job) -> list[TopicProposal]:
    """Discovery is a pending review, never an active topic or article assignment."""
    if job.status != "succeeded" or job.outcome != "applied" or not job.result:
        return []
    lock_topics(session)
    catalog = list(catalogs(session).values())
    prior = session.scalars(select(TopicProposal)).all()
    known = [identity_terms(topic) for topic in catalog]
    known.extend(identity_terms(TopicDraft.model_validate(p.proposed)) for p in prior)
    created = []
    for item in job.result.get("proposed_topics", []):
        draft = TopicDraft(name=item["name"], slug=item["slug"], kind=item["kind"])
        identity = identity_terms(draft)
        if any(identity & existing for existing in known):
            continue
        proposal = TopicProposal(
            batch_id=job.id,
            slug=draft.slug,
            action="create",
            origin="ai_analysis",
            source_name="Article AI analysis",
            proposed=draft.model_dump(mode="json"),
            evidence=[
                {
                    "analysis_id": str(job.id),
                    "article_id": str(job.article_id),
                    "quote": item["evidence"],
                    "model": job.model,
                }
            ],
            created_by={"subject": "ai-analysis", "issuer": "devfeed", "organization_id": "system"},
        )
        session.add(proposal)
        created.append(proposal)
        known.append(identity)
    session.flush()
    return created


def discover_topics(session: Session, limit: int = 100) -> list[TopicProposal]:
    from devfeed_core.models import ArticleAnalysisJob

    jobs = session.scalars(
        select(ArticleAnalysisJob)
        .where(ArticleAnalysisJob.status == "succeeded", ArticleAnalysisJob.outcome == "applied")
        .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id)
        .limit(limit)
    ).all()
    return [proposal for job in jobs for proposal in propose_analysis_topics(session, job)]
