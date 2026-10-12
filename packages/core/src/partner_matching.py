"""Prepare grounded product/topic and product/article edges outside reader requests."""

import hashlib
import json
import re
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.models import (
    Article,
    PartnerArticleMatch,
    PartnerMatchState,
    PartnerProduct,
    PartnerProductTopic,
    Topic,
    TopicRelation,
    utcnow,
)
from devfeed_core.partner_catalog import has_active_listing
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tools import article_snapshot, eligible
from devfeed_core.publication import visible_article
from devfeed_core.tag_topic_discovery import identity_keys

MATCH_TTL = timedelta(minutes=30)
SWEEP_INTERVAL = timedelta(minutes=5)
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


def contains(text, term):
    return bool(term and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I))


def words(text):
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


def article_fingerprint(article):
    return hashlib.sha256(
        json.dumps(article_snapshot(article), sort_keys=True).encode()
    ).hexdigest()


def grounded_technologies(product):
    return [
        technology
        for technology in product.technologies
        if technology.casefold() not in BROAD
        and any(contains(item.get("quote", ""), technology) for item in product.evidence)
    ]


def article_match(product, article):
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


def prepare_topics(session, product, now):
    technologies = grounded_technologies(product)
    terms = [value.casefold() for value in technologies]
    direct = (
        session.scalars(
            select(Topic).where(
                Topic.status == "active",
                or_(
                    func.lower(Topic.name).in_(terms),
                    Topic.identity_keys.overlap(identity_keys(technologies)),
                    Topic.aliases.overlap(technologies),
                ),
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
    return hashlib.sha256(
        json.dumps(
            sorted(
                (str(topic.id), topic.updated_at.isoformat(), score)
                for topic, score, _ in rows.values()
            )
        ).encode()
    ).hexdigest()


def save_article_match(
    session, product, article, relevant, score, reason, *, origin="rules", now=None
):
    now = now or utcnow()
    fingerprint = article_fingerprint(article)
    # Rule sweeps can revisit busy inputs. Validated evaluations are a durable
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
        return
    previous = session.get(PartnerArticleMatch, (product.id, article.id))
    if (
        origin == "rules"
        and previous
        and previous.origin == "evaluation"
        and previous.product_revision == product.revision
        and previous.product_updated_at == product.updated_at
        and previous.article_fingerprint == fingerprint
    ):
        previous.updated_at = now
        return
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


def save_evaluation_matches(session, job):
    """Only call after the existing evaluator validates evidence and fences stale inputs."""
    product = session.get(PartnerProduct, job.product_id)
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


def refresh_partner_matches(factory, *, product_limit=10, article_limit=100, now=None):
    """Fair durable slices cover new articles first and continuously revisit older content."""
    now = now or utcnow()
    count = 0
    with factory.begin() as session:
        # Multiple scheduler processes share one bounded preparation lane.
        if not session.scalar(select(func.pg_try_advisory_xact_lock(832604282))):
            return 0
        products = session.scalars(
            select(PartnerProduct)
            .outerjoin(PartnerMatchState)
            .where(
                PartnerProduct.status == "approved",
                PartnerProduct.excluded.is_(False),
                PartnerProduct.merged_into_id.is_(None),
                PartnerProduct.verified_at >= now - timedelta(days=90),
                has_active_listing(),
                or_(
                    PartnerMatchState.product_id.is_(None),
                    PartnerMatchState.product_revision != PartnerProduct.revision,
                    PartnerMatchState.product_updated_at != PartnerProduct.updated_at,
                    PartnerMatchState.completed_at.is_(None),
                    PartnerMatchState.completed_at <= now - SWEEP_INTERVAL,
                ),
            )
            .order_by(PartnerMatchState.updated_at.asc().nullsfirst(), PartnerProduct.id)
            .limit(product_limit)
        ).all()
        for product in products:
            if not eligible(product, now):
                continue
            state = session.get(PartnerMatchState, product.id)
            if state is None:
                state = PartnerMatchState(
                    product_id=product.id,
                    product_revision=0,
                    product_updated_at=product.updated_at,
                )
                session.add(state)
            if (
                state.product_revision != product.revision
                or state.product_updated_at != product.updated_at
                or state.completed_at is not None
            ):
                state.article_cursor = None
                state.completed_at = None
            state.product_revision = product.revision
            state.product_updated_at = product.updated_at
            state.topic_generation = prepare_topics(session, product, now)
            recent = session.scalars(
                select(Article)
                .where(visible_article(), Article.content_type == "tutorial")
                .order_by(Article.feed_at.desc(), Article.id)
                .limit(20)
            ).all()
            page = session.scalars(
                select(Article)
                .where(
                    visible_article(),
                    Article.content_type == "tutorial",
                    *([Article.id > state.article_cursor] if state.article_cursor else []),
                )
                .order_by(Article.id)
                .limit(article_limit)
            ).all()
            articles = {article.id: article for article in recent + page}
            for article in articles.values():
                save_article_match(
                    session, product, article, *article_match(product, article), now=now
                )
                count += 1
            if len(page) < article_limit:
                state.completed_at = now
                state.article_cursor = None
            else:
                state.article_cursor = page[-1].id
            state.updated_at = now
        if products:
            ensure_automatic_assets(
                session, now=now, product_ids=[product.id for product in products]
            )
    return count
