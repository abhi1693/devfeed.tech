"""Bounded native operator observations from transactional source records.

Only selected IDs are hydrated, and no PostgreSQL serving tables are queried.
Public card DTOs and compact typed metadata leave this module; source evidence,
bodies, prompts, credentials and full private source URLs stay transactional.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from urllib.parse import SplitResult, unquote_plus, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, load_only, sessionmaker

from devfeed_core.analysis import catalog, reuse_candidates
from devfeed_core.analytics import SqlRecord
from devfeed_core.analytics_blockers import automation_blocker_rows, automation_blockers
from devfeed_core.analytics_card_types import PipelineCard, PipelineJobs
from devfeed_core.article_lifecycle import ProcessingInputs, derive_publication_readiness
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleReview,
    Source,
    SourceCandidate,
    TopicProposal,
    TopicRelationProposal,
)
from devfeed_core.source_lifecycle import _job_items, source_readiness_batch
from devfeed_core.text_search import normalized_text

BATCH_SIZE = 200
_SECRET_QUERY_KEYS = frozenset(
    {
        "key",
        "apikey",
        "api_key",
        "api-key",
        "token",
        "access_token",
        "refresh_token",
        "auth",
        "authorization",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "signature",
        "sig",
        "credential",
        "credentials",
    }
)


def bounded_ids(identifiers: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    identifiers = list(dict.fromkeys(identifiers))
    if len(identifiers) > BATCH_SIZE:
        raise ValueError("Card observation batches must contain at most 200 identities")
    return identifiers


@contextmanager
def _observation_session(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Every card batch observes one bounded, read-only source snapshot."""
    with factory() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        session.execute(select(func.set_config("statement_timeout", "10000", True)))
        yield session


def _netloc(parts: SplitResult) -> str:
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{parts.port}" if parts.port else host


def uri_origin(value: str | None) -> str:
    """Only an origin is safe for private feed URLs with opaque path tokens."""
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return ""
        return urlunsplit((parts.scheme, _netloc(parts), "", "", ""))
    except (TypeError, ValueError):
        return ""


def public_article_uri(value: str | None) -> str:
    """Keep public article path/query search without explicit bearer credentials."""
    try:
        parts = urlsplit(value or "")
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return ""
        query = "&".join(
            part
            for part in parts.query.split("&")
            if unquote_plus(part.split("=", 1)[0]).lower() not in _SECRET_QUERY_KEYS
        )
        return urlunsplit((parts.scheme, _netloc(parts), parts.path, query, ""))
    except (TypeError, ValueError):
        return ""


def normalize_search_field(value: object) -> str:
    return " ".join(
        "".join(
            character if character.isalnum() or character in "#+" else " "
            for character in str(value or "").lower()
        ).split()
    )


def normalized_search(*fields: object) -> str:
    return "\n".join(normalize_search_field(value) for value in fields)


def literal_search(*fields: object) -> str:
    return json.dumps([str(value or "") for value in fields], ensure_ascii=False)


def article_search(article: Article, original_normalized: str) -> str:
    original = article.canonical_url or ""
    safe = public_article_uri(original)
    return (
        original_normalized
        if safe == original
        else normalized_search(article.title, article.ai_title, safe)
    )


def minimal_terminal_article(
    article: Article, policy: str, now: datetime, terminal_at: datetime | None
) -> SqlRecord:
    """Keep exact all-source review metadata without hydrating terminal evidence."""
    source_objects = sorted(
        (origin.source for origin in article.origins),
        key=lambda source: (source.approval_status != "approved", source.name, source.id),
    )
    source = source_objects[0] if source_objects else None
    card = PipelineCard(
        id=article.id,
        title=article.ai_title or article.title,
        source_name=source.name if source else None,
        source_id=source.id if source else None,
        discovered_at=article.discovered_at,
        stage="published" if article.publication_status == "published" else "rejected",
        state="complete",
        blockers=[],
        next_check_at=None,
        editorial_revision=article.editorial_revision,
        jobs=PipelineJobs(),
    )
    return _article_row(article, card, policy, now, terminal_at)


def _article_row(
    article: Article,
    card: PipelineCard,
    policy: str,
    now: datetime,
    terminal_at: datetime | None,
    *,
    search_text: str | None = None,
    jobs: Iterable[
        ArticleAnalysisJob | ArticleEnrichmentJob | ArticleImageJob | ArticleContent | None
    ] = (),
    blocker_codes: Iterable[str] = (),
    catalog_sensitive: bool = False,
) -> SqlRecord:
    """Active and terminal observations share one safe, typed article shape."""
    safe_uri = public_article_uri(article.canonical_url)
    jobs = tuple(jobs)
    return dict(
        policy_version=policy,
        kind="article",
        entity_id=article.id,
        payload=card.model_dump(mode="json"),
        stage=card.stage,
        state=card.state,
        source_id=card.source_id,
        source_name=card.source_name,
        source_sort=(card.source_name or "").casefold(),
        source_ids=[str(origin.source_id) for origin in article.origins],
        search_text=search_text
        if search_text is not None
        else normalized_search(article.title, article.ai_title, safe_uri),
        literal_search_text=literal_search(article.title, article.ai_title, safe_uri),
        source_type="",
        created_at=article.discovered_at,
        activity_at=article.discovered_at,
        terminal_at=terminal_at,
        has_active_work=any(getattr(job, "status", None) in {"queued", "running"} for job in jobs),
        next_transition_at=(
            getattr(getattr(card.jobs, card.stage, None), "available_at", None)
            if card.state == "retry_wait"
            else None
        ),
        evaluated_at=now,
        review_pending=int(article.review_status == "pending"),
        enabled=0,
        approval_status="",
        consecutive_failures=0,
        blocker_codes=list(blocker_codes),
        title=article.title,
        editorial_revision=article.editorial_revision,
        catalog_sensitive=int(catalog_sensitive),
    )


def article_rows(
    factory: sessionmaker[Session], identifiers: Iterable[uuid.UUID], policy: str, now: datetime
) -> list[SqlRecord]:
    from devfeed_core.analytics_article_cards import _article_facts, _batch_facts, _card, _statement

    identifiers = bounded_ids(identifiers)
    if not identifiers:
        return []
    with _observation_session(factory) as session:
        # Card revisions, catalog, job evidence and compact blockers must describe
        # the same observation even when ingestion commits between these reads.
        loaded = session.execute(
            _statement(None, None)
            .where(Article.id.in_(identifiers))
            .add_columns(
                func.concat_ws(
                    "\n",
                    *(
                        normalized_text(getattr(Article, name))
                        for name in ("title", "ai_title", "canonical_url")
                    ),
                )
            )
        ).all()
        articles = [article for article, _ in loaded]
        searches = {article.id: value for article, value in loaded}
        rejected = dict(
            session.execute(
                select(
                    ArticleReview.article_id,
                    func.max(ArticleReview.created_at),
                )
                .where(ArticleReview.article_id.in_(identifiers), ArticleReview.action == "reject")
                .group_by(ArticleReview.article_id)
            ).all()
        )
        terminal_times = {
            item.id: item.published_to_feed_at
            if item.publication_status == "published"
            else rejected.get(item.id, item.discovered_at)
            if item.review_status == "rejected"
            else None
            for item in articles
        }
        retained = [
            item
            for item in articles
            if (terminal_times[item.id] is None and item.publication_status != "published")
            or (
                (terminal_time := terminal_times[item.id]) is not None
                and terminal_time >= now - timedelta(days=7)
            )
        ]
        retained_ids = {item.id for item in retained}
        archived = [
            minimal_terminal_article(item, policy, now, terminal_times[item.id])
            for item in articles
            if item.id not in retained_ids
        ]
        articles = retained
        if not articles:
            return archived
        taxonomy = catalog(session)
        facts = _batch_facts(session, articles, lightweight=True)
        ids = [item.id for item in articles]
        list(session.scalars(select(ArticleContent).where(ArticleContent.article_id.in_(ids))))
        analysis_ids = [job.id for job in facts["analysis"].values()]
        if analysis_ids:
            list(
                session.scalars(
                    select(ArticleAnalysisJob)
                    .options(load_only(ArticleAnalysisJob.id, ArticleAnalysisJob.catalog_snapshot))
                    .where(ArticleAnalysisJob.id.in_(analysis_ids))
                )
            )
        blockers = automation_blocker_rows(session, ids)
        session.expunge_all()
    # No connection is held during catalog ranking, body hashing, and card evaluation.
    rows = []
    with reuse_candidates(reuse_existing=True):
        for article in articles:
            current = _article_facts(facts, article.id)
            if article.review_status == "rejected":
                current = {
                    "content": None,
                    "enrichment": None,
                    "analysis": None,
                    "discovery": None,
                    "storage": None,
                }
            inputs: ProcessingInputs = {
                **current,
                "taxonomy": taxonomy,
                "settings": get_settings(),
                "now": now,
            }
            readiness = derive_publication_readiness(article, **inputs)
            card = _card(article, readiness, current, taxonomy=taxonomy)
            rows.append(
                _article_row(
                    article,
                    card,
                    policy,
                    now,
                    terminal_times[article.id],
                    search_text=article_search(article, searches[article.id]),
                    blocker_codes=[
                        row["code"]
                        for row in blockers.get(article.id, [])
                        if row["code"] is not None
                    ],
                    catalog_sensitive=article.review_status != "rejected"
                    and (
                        bool(article.topic_links)
                        or getattr(current.get("analysis"), "status", None) == "succeeded"
                    ),
                    jobs=(
                        current["enrichment"],
                        current["analysis"],
                        current["discovery"],
                        current["storage"],
                    ),
                )
            )
    return rows + archived


def source_rows(
    factory: sessionmaker[Session],
    sources: Iterable[uuid.UUID],
    candidates: Iterable[uuid.UUID],
    policy: str,
    now: datetime,
) -> list[SqlRecord]:
    from devfeed_core.analytics_source_cards import _activity, _card

    sources, candidates = bounded_ids(sources), bounded_ids(candidates)
    if len(sources) + len(candidates) > BATCH_SIZE:
        raise ValueError("Source card batches must contain at most 200 total identities")
    if not sources and not candidates:
        return []
    with _observation_session(factory) as session:
        source_objects = list(session.scalars(select(Source).where(Source.id.in_(sources))))
        candidate_objects = list(
            session.scalars(
                select(SourceCandidate).where(
                    SourceCandidate.id.in_(candidates),
                    SourceCandidate.source_id.is_(None),
                )
            )
        )
        facts = source_readiness_batch(session, source_objects, candidate_objects, now=now)
        rows = []
        for fact in facts.values():
            card = _card(fact)
            payload = card.model_dump(mode="json")
            payload["website_url"] = payload["feed_url"] = None
            safe_urls = (uri_origin(card.website_url), uri_origin(card.feed_url))
            rows.append(
                dict(
                    policy_version=policy,
                    kind=card.kind,
                    entity_id=card.id,
                    payload=payload,
                    stage=card.stage,
                    state=card.state,
                    source_id=card.source_id,
                    source_name=card.name,
                    source_sort=card.name.casefold(),
                    source_ids=[str(card.source_id)] if card.source_id else [],
                    search_text=normalized_search(card.name, *safe_urls),
                    literal_search_text=literal_search(card.name, *safe_urls),
                    source_type=card.source_type,
                    review_pending=int(card.approval_status == "pending"),
                    enabled=int(card.enabled),
                    approval_status=card.approval_status,
                    consecutive_failures=max(0, fact.source.consecutive_failures or 0)
                    if fact.source
                    else 0,
                    blocker_codes=[],
                    title=card.name,
                    editorial_revision=0,
                    catalog_sensitive=0,
                    created_at=card.created_at,
                    activity_at=_activity(fact),
                    terminal_at=_activity(fact)
                    if card.stage in {"active", "paused", "rejected"}
                    else None,
                    has_active_work=any(
                        job and job.status in {"queued", "running"}
                        for _, job in _job_items(fact.jobs)
                    ),
                    next_transition_at=(
                        getattr(
                            fact.readiness.checks.get(fact.readiness.stage), "available_at", None
                        )
                        if card.state == "retry_wait"
                        else None
                    ),
                    evaluated_at=now,
                )
            )
        return rows


def proposal_rows(
    factory: sessionmaker[Session],
    topic_ids: Iterable[uuid.UUID],
    relationship_ids: Iterable[uuid.UUID],
    policy: str,
    now: datetime,
) -> list[SqlRecord]:
    """Classify selected review records without exporting their private evidence."""
    topic_ids, relationship_ids = bounded_ids(topic_ids), bounded_ids(relationship_ids)
    if len(topic_ids) + len(relationship_ids) > BATCH_SIZE:
        raise ValueError("Proposal card batches must contain at most 200 total identities")
    if not topic_ids and not relationship_ids:
        return []
    with _observation_session(factory) as session:
        topics = (
            list(
                session.execute(
                    select(
                        TopicProposal.id,
                        TopicProposal.status,
                        TopicProposal.created_at,
                        TopicProposal.reviewed_at,
                        TopicProposal.proposed["name"].astext.label("title"),
                    ).where(TopicProposal.id.in_(topic_ids))
                )
            )
            if topic_ids
            else []
        )
        relationships = (
            list(
                session.execute(
                    select(
                        TopicRelationProposal.id,
                        TopicRelationProposal.status,
                        TopicRelationProposal.created_at,
                        TopicRelationProposal.reviewed_at,
                        TopicRelationProposal.topic_snapshot["name"].astext.label("left"),
                        TopicRelationProposal.related_topic_snapshot["name"].astext.label("right"),
                    ).where(TopicRelationProposal.id.in_(relationship_ids))
                )
            )
            if relationship_ids
            else []
        )
        blockers = automation_blockers(
            session,
            [],
            topic_ids=topic_ids,
            relationship_ids=relationship_ids,
            target_limit=max(len(topic_ids), len(relationship_ids)),
        )
        codes: dict[tuple[str, UUID], list[str]] = {}
        for blocker in blockers:
            for target in blocker.targets:
                codes.setdefault((target.kind, target.id), []).append(blocker.code)
        rows = []
        for kind, records in (("topic-proposal", topics), ("relationship-proposal", relationships)):
            for record in records:
                title = (
                    (record.title or "Unnamed topic")
                    if kind == "topic-proposal"
                    else f"{record.left or 'Unnamed topic'} → {record.right or 'Unnamed topic'}"
                )
                blocked = codes.get((kind, record.id), [])
                state = (
                    "blocked"
                    if blocked
                    else "waiting"
                    if record.status == "pending"
                    else "complete"
                )
                rows.append(
                    dict(
                        policy_version=policy,
                        kind=kind,
                        entity_id=record.id,
                        payload={
                            "id": str(record.id),
                            "kind": kind,
                            "title": title,
                            "status": record.status,
                        },
                        stage=record.status,
                        state=state,
                        source_id=None,
                        source_name=None,
                        source_sort="",
                        source_ids=[],
                        search_text=normalized_search(title),
                        literal_search_text=literal_search(title),
                        source_type="",
                        created_at=record.created_at,
                        activity_at=record.reviewed_at or record.created_at,
                        terminal_at=record.reviewed_at if record.status != "pending" else None,
                        has_active_work=False,
                        next_transition_at=None,
                        evaluated_at=now,
                        catalog_sensitive=0,
                        review_pending=int(record.status == "pending"),
                        enabled=0,
                        approval_status="",
                        consecutive_failures=0,
                        blocker_codes=blocked,
                        title=title,
                        editorial_revision=0,
                    )
                )
    return rows
