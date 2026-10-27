"""Prepare grounded product/topic and product/article edges outside reader requests."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import Text, and_, delete, false, func, literal, or_, select, true, union
from sqlalchemy.dialects.postgresql import ARRAY, array, insert
from sqlalchemy.orm import Session, raiseload, sessionmaker
from sqlalchemy.sql import CompoundSelect
from sqlalchemy.sql.elements import ColumnElement

from devfeed_core.models import (
    Article,
    PartnerArticleMatch,
    PartnerEvaluation,
    PartnerMatchJob,
    PartnerProduct,
    PartnerProductTopic,
    Topic,
    TopicRelation,
    utcnow,
)
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tools import ArticleSnapshotSource, article_snapshot

# Shared by topic publication, placement issuance, and taxonomy invalidation triggers.
TAXONOMY_LOCK_KEY = 832604285

BROAD = {"ai", "developer tools", "productivity", "software", "technology", "development"}
STOP_WORDS = set(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "best",
        "build",
        "building",
        "by",
        "can",
        "code",
        "developers",
        "development",
        "do",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "platform",
        "product",
        "provides",
        "software",
        "that",
        "the",
        "their",
        "this",
        "to",
        "tool",
        "tools",
        "use",
        "using",
        "with",
        "you",
        "your",
        "supports",
        "support",
        "allows",
        "enable",
        "enables",
        "easy",
        "powerful",
        "solution",
        "new",
        "modern",
        "application",
        "applications",
        "help",
        "helps",
    ]
)


def contains(text: str, term: str) -> bool:
    return bool(term and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I))


def words(text: str) -> set[str]:
    """Small lexical normalization; broad marketing words never establish a task match."""
    result = set()
    for word in re.findall(r"[a-z][a-z0-9_-]{2,}", text.casefold()):
        if word in STOP_WORDS:
            continue
        if word.endswith("ies"):
            word = word[:-3] + "y"
        elif word.endswith("ing") and len(word) > 6:
            word = word[:-3]
        elif word.endswith("ed") and len(word) > 5:
            word = word[:-2]
        elif word.endswith("s") and len(word) > 4:
            word = word[:-1]
        result.add(word)
    return result


def article_fingerprint(article: ArticleSnapshotSource) -> str:
    snapshot = article_snapshot(article)
    # Rules read the full summary, even when evaluations sample a bounded excerpt.
    snapshot["text"] = article.summary
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def grounded_technologies(product: PartnerProduct) -> list[str]:
    return [
        technology
        for technology in product.technologies
        if technology.casefold() not in BROAD
        and any(contains(item.get("quote", ""), technology) for item in product.evidence)
    ]


def article_match(product: PartnerProduct, article: ArticleSnapshotSource) -> tuple[bool, int, str]:
    """Require a specific shared technology AND two independently grounded task terms."""
    if (
        article.content_type != "tutorial"
        or article.publication_status != "published"
        or len(article.summary.strip()) < 40
    ):
        return False, 0, "No grounded practical task match."
    text = article.title + "\n" + article.summary
    article_words = words(article.summary)
    best = (False, 0, "No grounded practical task match.")
    for technology in grounded_technologies(product):
        if not contains(text, technology):
            continue
        for item in product.evidence:
            quote = item.get("quote", "")
            if not contains(quote, technology):
                continue
            task = words(item.get("capability", "")) & words(quote)
            task -= words(technology)
            shared = task & article_words
            if len(shared) < 2:
                continue
            score = min(94, 75 + len(shared) * 4)
            reason = f"Matches this {technology} tutorial's task: {', '.join(sorted(shared)[:4])}."
            if score > best[1]:
                best = (True, score, reason)
    return best


def prepare_topics(session: Session, product: PartnerProduct, now: datetime) -> bool | None:
    technologies = grounded_technologies(product)
    direct = (
        session.scalars(
            select(Topic).where(
                Topic.status == "active",
                true()
                if "__any__" in product.matching_tokens
                else Topic.matching_tokens.overlap([*product.matching_tokens, "__unicode__"]),
            )
        ).all()
        if technologies
        else []
    )
    direct = [
        topic
        for topic in direct
        if any(
            value.casefold()
            in {topic.name.casefold(), *(alias.casefold() for alias in topic.aliases)}
            for value in technologies
        )
    ]
    rows = {topic.id: (topic, 100, f"Verified support for {topic.name}.") for topic in direct}
    if rows:
        relations = (
            session.execute(
                select(TopicRelation)
                .where(
                    or_(TopicRelation.topic_id.in_(rows), TopicRelation.related_topic_id.in_(rows)),
                    TopicRelation.relation.in_(
                        ["related_to", "part_of", "uses_language", "depends_on"]
                    ),
                )
                .limit(200)
            )
            .scalars()
            .all()
        )
        related_ids = set()
        for relation in relations:
            if relation.topic_id in rows:
                related_ids.add(relation.related_topic_id)
            elif relation.relation == "related_to":
                related_ids.add(relation.topic_id)
        related = session.scalars(
            select(Topic).where(Topic.id.in_(related_ids), Topic.status == "active")
        ).all()
        for topic in related:
            if topic.id not in rows and topic.name.casefold() not in BROAD:
                rows[topic.id] = (
                    topic,
                    65,
                    f"Related to {topic.name} through verified technology topics.",
                )
    if rows:
        # Preflight implicit FK locks without waiting on an editor who locked a
        # topic before entering the taxonomy advisory barrier.
        locked = session.scalars(
            select(Topic.id)
            .where(Topic.id.in_(rows))
            .with_for_update(read=True, key_share=True, skip_locked=True)
        ).all()
        if len(locked) != len(rows):
            return None
    session.execute(delete(PartnerProductTopic).where(PartnerProductTopic.product_id == product.id))
    for topic, score, reason in rows.values():
        session.add(
            PartnerProductTopic(
                product_id=product.id,
                topic_id=topic.id,
                product_revision=product.revision,
                product_updated_at=product.updated_at,
                topic_updated_at=topic.updated_at,
                reason=reason,
                score=score,
                updated_at=now,
            )
        )
    return True


def save_article_match(
    session: Session,
    product: PartnerProduct,
    article: ArticleSnapshotSource,
    relevant: bool,
    score: int,
    reason: str,
    *,
    origin: str = "rules",
    now: datetime | None = None,
) -> bool:
    now = now or utcnow()
    fingerprint = article_fingerprint(article)
    # Queued rules retry busy inputs. Validated evaluations are a durable
    # authority: wait for a short editing/issuance transaction, then recheck the
    # snapshot rather than silently dropping its negative and succeeding the job.
    skip_busy = origin != "evaluation"
    current_product = session.execute(
        select(
            PartnerProduct.revision,
            PartnerProduct.updated_at,
        )
        .where(PartnerProduct.id == product.id)
        .with_for_update(read=True, skip_locked=skip_busy)
    ).first()
    current_article = session.execute(
        select(
            Article.id,
            Article.title,
            Article.summary,
            Article.content_type,
            Article.publication_status,
            Article.editorial_revision,
        )
        .where(Article.id == article.id)
        .with_for_update(read=True, skip_locked=skip_busy)
    ).first()
    if (
        current_product is None
        or current_article is None
        or tuple(current_product) != (product.revision, product.updated_at)
        or article_fingerprint(current_article) != fingerprint
    ):
        return False
    previous = session.get(PartnerArticleMatch, (product.id, article.id))
    if (
        origin == "rules"
        and previous
        and previous.origin == "evaluation"
        and previous.product_revision == product.revision
        and previous.product_updated_at == product.updated_at
        and previous.article_fingerprint == fingerprint
    ):
        return True
    values = dict(
        product_id=product.id,
        article_id=article.id,
        product_revision=product.revision,
        product_updated_at=product.updated_at,
        editorial_revision=article.editorial_revision,
        article_fingerprint=fingerprint,
        relevant=relevant,
        score=score,
        reason=reason,
        origin=origin,
        updated_at=now,
    )
    statement = insert(PartnerArticleMatch).values(**values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[PartnerArticleMatch.product_id, PartnerArticleMatch.article_id],
            set_=values,
            where=and_(
                PartnerArticleMatch.product_updated_at <= statement.excluded.product_updated_at,
                PartnerArticleMatch.editorial_revision <= statement.excluded.editorial_revision,
                or_(
                    statement.excluded.origin == "evaluation",
                    PartnerArticleMatch.origin != "evaluation",
                    PartnerArticleMatch.product_revision != statement.excluded.product_revision,
                    PartnerArticleMatch.product_updated_at != statement.excluded.product_updated_at,
                    PartnerArticleMatch.article_fingerprint
                    != statement.excluded.article_fingerprint,
                ),
            ),
        )
    )

    return True


def save_evaluation_matches(session: Session, job: PartnerEvaluation) -> None:
    """Only call after the existing evaluator validates evidence and fences stale inputs."""
    product = session.get(PartnerProduct, job.product_id)
    assert product is not None
    articles = {
        str(article.id): article
        for article in session.scalars(
            select(Article).where(
                Article.id.in_([row["article_id"] for row in job.result["decisions"]])
            )
        )
    }
    for decision in job.result["decisions"]:
        article = articles.get(decision["article_id"])
        if article:
            save_article_match(
                session,
                product,
                article,
                decision["relevant"],
                100 if decision["relevant"] else 0,
                decision["reason"],
                origin="evaluation",
            )


def product_article_candidates(
    product: PartnerProduct, *, after: uuid.UUID | None = None
) -> CompoundSelect[uuid.UUID]:
    """Indexed technology candidates plus old positives needing invalidation."""
    tokens = product.matching_tokens
    condition: ColumnElement[bool] = false()
    if grounded_technologies(product):
        condition = true() if "__any__" in tokens else Article.matching_tokens.overlap(tokens)
    return union(
        select(Article.id).where(
            Article.content_type == "tutorial",
            Article.publication_status == "published",
            condition,
            *([Article.id > after] if after else []),
        ),
        select(PartnerArticleMatch.article_id).where(
            PartnerArticleMatch.product_id == product.id,
            PartnerArticleMatch.relevant.is_(True),
            *([PartnerArticleMatch.article_id > after] if after else []),
        ),
    )


def article_product_candidates(
    article: Article, *, after: uuid.UUID | None = None
) -> CompoundSelect[uuid.UUID]:
    """Reverse technology lookup includes tutorials with no topic labels."""
    tokens = func.unnest(literal([*article.matching_tokens, "__any__"], type_=ARRAY(Text)))
    terms = tokens.table_valued("token").render_derived()
    # A long summary has many terms absent from the catalog. A combined overlap
    # overestimates their selectivity and can scan every product. Parameterized
    # probes keep each technology lookup indexed; OFFSET prevents flattening.
    products = (
        select(PartnerProduct.id)
        .where(
            PartnerProduct.matching_tokens.contains(array([terms.c.token])),
            PartnerProduct.evidence != [],
            *([PartnerProduct.id > after] if after else []),
        )
        .correlate(terms)
        .offset(0)
        .lateral()
    )
    candidates = select(products.c.id).select_from(terms.join(products, true()))
    if (
        article.content_type != "tutorial"
        or article.publication_status != "published"
        or len(article.summary.strip()) < 40
    ):
        candidates = candidates.where(false())
    return union(
        candidates,
        select(PartnerArticleMatch.product_id).where(
            PartnerArticleMatch.article_id == article.id,
            PartnerArticleMatch.relevant.is_(True),
            *([PartnerArticleMatch.product_id > after] if after else []),
        ),
    )


def _prepare_job(
    session: Session, job: PartnerMatchJob, *, article_limit: int, now: datetime
) -> tuple[int, bool]:
    """Process a bounded page; retain the cursor before any busy input."""
    product_job = job.kind != "article"
    model = PartnerProduct if product_job else Article
    # Editors lock the source before their trigger coalesces its queue row. Never
    # wait on that source while holding its queue lock in the opposite order.
    source = session.scalar(
        select(model)
        .options(raiseload("*"))
        .where(model.id == job.entity_id)
        .with_for_update(read=True, skip_locked=True)
    )
    if source is None:
        return 0, not session.scalar(select(model.id).where(model.id == job.entity_id))
    if product_job:
        product = cast(PartnerProduct, source)
    if product_job and (job.kind == "topics" or job.cursor is None):
        # Serialize topic publication with atomic taxonomy invalidation without
        # waiting on the editor's row/queue locks.
        if not session.scalar(select(func.pg_try_advisory_xact_lock_shared(TAXONOMY_LOCK_KEY))):
            return 0, False
        if prepare_topics(session, product, now) is None:
            return 0, False
    if job.kind == "topics":
        return 0, True
    candidate_ids = (
        product_article_candidates(product, after=job.cursor)
        if product_job
        else article_product_candidates(cast(Article, source), after=job.cursor)
    )
    candidate_model = Article if product_job else PartnerProduct
    candidates = candidate_ids.subquery()
    candidate_id = next(iter(candidates.c))
    page_ids = session.scalars(
        select(candidate_id).order_by(candidate_id).limit(article_limit + 1)
    ).all()
    # Load only this page. Keeping the catalog table outside the candidate UNION
    # also prevents a broad estimated semijoin from scanning all source rows.
    page = (
        session.scalars(
            select(candidate_model)
            .options(raiseload("*"))
            .where(candidate_model.id.in_(page_ids[:article_limit]))
            .order_by(candidate_model.id)
        ).all()
        if page_ids
        else []
    )
    count = 0
    for candidate in page[:article_limit]:
        if product_job:
            article = cast(Article, candidate)
        else:
            product = cast(PartnerProduct, candidate)
            article = cast(Article, source)
        if not save_article_match(
            session, product, article, *article_match(product, article), now=now
        ):
            return count, False
        job.cursor = article.id if product_job else product.id
        count += 1
    return count, len(page_ids) <= article_limit


def refresh_partner_matches(
    factory: sessionmaker[Session],
    *,
    product_limit: int = 10,
    article_limit: int = 100,
    now: datetime | None = None,
) -> int:
    """Drain durable changes in bounded indexed pages; quiet catalogs do no work.

    Both endpoints enqueue transactionally, so whichever concurrent change is
    processed last sees the other's latest indexed facts. Reader policy decides
    visibility/provider eligibility; factual preparation need not repeat when an
    unchanged source becomes approved or a provider is re-enabled.
    """
    if product_limit < 1 or article_limit < 1:
        raise ValueError("Matching batch limits must be positive")
    now = now or utcnow()
    count = 0
    products = []
    with factory.begin() as session:
        if not session.scalar(select(func.pg_try_advisory_xact_lock(832604282))):
            return 0
        jobs = session.scalars(
            select(PartnerMatchJob)
            .order_by(PartnerMatchJob.updated_at, PartnerMatchJob.kind, PartnerMatchJob.entity_id)
            .limit(product_limit)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            processed, complete = _prepare_job(session, job, article_limit=article_limit, now=now)
            count += processed
            if job.kind == "product" and (complete or processed):
                products.append(job.entity_id)
            if complete:
                session.delete(job)
            else:
                job.updated_at = utcnow()
    if products:
        # Enrollment acquires account/campaign locks independently of matching
        # inputs and queue rows; it must not extend the preparation lock graph.
        with factory.begin() as session:
            ensure_automatic_assets(session, now=now, product_ids=products)
    return count
