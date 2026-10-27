"""Bounded public search context and evidence-grounded search intent."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Self

from sqlalchemy import Text, and_, case, column, false, func, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement, Select, SQLColumnExpression

from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    PartnerProduct,
    PartnerProductTopic,
    Source,
    Tag,
    Topic,
)
from devfeed_core.partner_matching import BROAD, STOP_WORDS, words
from devfeed_core.publication import visible_article
from devfeed_core.search_records import public_records
from devfeed_core.text_search import normalized_text

SEARCH_FILLER = words(
    "about article articles example examples find guide guides how learn learning "
    "read reading tutorial tutorials what"
)


@dataclass
class PublicSearchContext:
    """Validated result identities with candidate-specific, indexed public witnesses."""

    results: dict[str, set[uuid.UUID]]
    blocked: bool = False

    @classmethod
    def resolve[K: str](cls, session: Session, results: Mapping[K, Iterable[uuid.UUID]]) -> Self:
        return cls(
            {
                kind: set(public_records(session, kind, identifiers))
                for kind, identifiers in results.items()
                if identifiers
            }
        )

    def _source_result(self) -> ColumnElement[bool]:
        return (
            select(1)
            .select_from(ArticleOrigin)
            .join(Source)
            .where(
                ArticleOrigin.article_id == Article.id,
                Source.id.in_(self.results.get("sources", set())),
                Source.approval_status == "approved",
                Source.enabled.is_(True),
            )
            .correlate(Article)
            .exists()
        )

    def _tag_result(self) -> ColumnElement[bool]:
        return (
            select(1)
            .select_from(ArticleTag)
            .join(Tag)
            .where(ArticleTag.article_id == Article.id, Tag.id.in_(self.results.get("tags", set())))
            .correlate(Article)
            .exists()
        )

    def _article_context(self) -> ColumnElement[bool]:
        return or_(
            Article.id.in_(self.results.get("articles", set())),
            ArticleTopic.topic_id.in_(self.results.get("topics", set())),
            self._source_result(),
            self._tag_result(),
        )

    def _witness_conditions(self) -> tuple[ColumnElement[bool], ...]:
        return (
            visible_article(),
            self._article_context(),
            ArticleTopic.role.in_(["primary", "supporting"]),
            ArticleTopic.relevance * case((ArticleTopic.role == "primary", 100), else_=80) >= 39.5,
        )

    def _prepared_conditions(self, query: str) -> tuple[ColumnElement[bool], ...]:
        return (
            PartnerProductTopic.score == 100,
            Topic.status == "active",
            PartnerProductTopic.topic_updated_at == Topic.updated_at,
            PartnerProductTopic.product_revision == PartnerProduct.revision,
            PartnerProductTopic.product_updated_at == PartnerProduct.updated_at,
            search_intent_condition(query),
        )

    def candidates(self, query: str) -> Select[uuid.UUID, int, str, int, datetime]:
        witness = (
            select(1)
            .select_from(ArticleTopic)
            .join(Article)
            .where(ArticleTopic.topic_id == Topic.id, *self._witness_conditions())
            .correlate(Topic)
            .exists()
        )
        ranked = (
            select(
                PartnerProductTopic.product_id,
                PartnerProductTopic.score,
                PartnerProductTopic.reason,
                PartnerProductTopic.product_revision,
                PartnerProductTopic.product_updated_at,
                func.row_number()
                .over(
                    partition_by=PartnerProductTopic.product_id,
                    order_by=PartnerProductTopic.topic_id,
                )
                .label("rank"),
            )
            .join(Topic)
            .join(PartnerProduct)
            .where(*self._prepared_conditions(query), witness)
            .subquery()
        )
        return select(
            ranked.c.product_id,
            ranked.c.score,
            ranked.c.reason,
            ranked.c.product_revision,
            ranked.c.product_updated_at,
        ).where(ranked.c.rank == 1)

    def lock_witness(self, session: Session, product_id: uuid.UUID, query: str) -> bool:
        """Fence the selected context once; never retry busy context for every product."""
        if self.blocked:
            return False
        witness = session.execute(
            select(Article.id, Topic.id)
            .select_from(ArticleTopic)
            .join(Article)
            .join(Topic)
            .join(PartnerProductTopic, PartnerProductTopic.topic_id == Topic.id)
            .join(PartnerProduct, PartnerProduct.id == PartnerProductTopic.product_id)
            .where(
                PartnerProduct.id == product_id,
                *self._prepared_conditions(query),
                *self._witness_conditions(),
            )
            .limit(1)
            .with_for_update(read=True, of=[Article, ArticleTopic, Topic], skip_locked=True)
        ).first()
        if witness is None:
            self.blocked = True
            return False
        article_id, topic_id = witness
        direct = article_id in self.results.get("articles", set()) or topic_id in self.results.get(
            "topics", set()
        )
        tagged = (
            None
            if direct or not self.results.get("tags")
            else session.scalar(
                select(ArticleTag.tag_id)
                .join(Tag)
                .where(
                    ArticleTag.article_id == article_id, Tag.id.in_(self.results.get("tags", set()))
                )
                .limit(1)
                .with_for_update(read=True, of=[ArticleTag, Tag], skip_locked=True)
            )
        )
        source = (
            select(Source.id)
            .select_from(ArticleOrigin)
            .join(Source)
            .where(
                ArticleOrigin.article_id == article_id,
                Source.approval_status == "approved",
            )
        )
        if not direct and tagged is None:
            source = source.where(
                Source.id.in_(self.results.get("sources", set())), Source.enabled.is_(True)
            )
        public_source = session.scalar(
            source.limit(1).with_for_update(read=True, of=[ArticleOrigin, Source], skip_locked=True)
        )
        self.blocked = public_source is None
        return not self.blocked


def _phrase_in(
    text: SQLColumnExpression[str] | SQLColumnExpression[str | None],
    phrase: SQLColumnExpression[str] | SQLColumnExpression[str | None],
) -> ColumnElement[bool]:
    return (
        func.strpos(
            literal(" ") + normalized_text(text) + literal(" "),
            literal(" ") + normalized_text(phrase) + literal(" "),
        )
        > 0
    )


def _evidence_terms(
    value: SQLColumnExpression[str] | SQLColumnExpression[str | None],
) -> ColumnElement[Sequence[str]]:
    """Apply the query's lexical rules in SQL, once per evidence text."""
    separated = func.regexp_replace(func.lower(value), "[-_]", " ", "g")
    tokens = (
        func.regexp_matches(
            separated,
            "[a-z][a-z0-9]{2,}|(?<![a-z0-9])[a-z0-9]{1,2}(?![a-z0-9])",
            "g",
        )
        .table_valued(column("parts", ARRAY(Text)))
        .render_derived()
    )
    token = tokens.c.parts[1]
    length = func.char_length(token)
    stem = case(
        (token.endswith("ies"), func.left(token, length - 3) + literal("y")),
        (and_(token.endswith("ing"), length > 6), func.left(token, length - 3)),
        (and_(token.endswith("ed"), length > 5), func.left(token, length - 2)),
        (and_(token.endswith("s"), length > 4), func.left(token, length - 1)),
        else_=token,
    )
    return (
        select(func.array_agg(stem))
        .select_from(tokens)
        .where(token.not_in(STOP_WORDS))
        .correlate_except(tokens)
        .scalar_subquery()
        .cast(ARRAY(Text))
    )


def search_intent_condition(query: str) -> ColumnElement[bool]:
    """Match a query to one supported capability and its independent source quote.

    Prepared direct topic edges remain mandatory in the caller. This adds a
    capability fence so implementation-only technology mentions and unrelated
    tasks cannot borrow those edges. It runs inside the batched candidate SQL
    and again in the final relevance check, never as one query per product.
    """
    evidence = (
        func.jsonb_array_elements(PartnerProduct.evidence)
        .table_valued(column("value", JSONB))
        .render_derived()
    )
    capability = evidence.c.value["capability"].astext
    quote = evidence.c.value["quote"].astext
    aliases = func.unnest(Topic.aliases).table_valued("name").render_derived()
    grounded_alias = (
        select(1)
        .select_from(aliases)
        .where(_phrase_in(capability, aliases.c.name), _phrase_in(quote, aliases.c.name))
        .correlate(Topic, evidence)
        .exists()
    )
    grounded_topic = or_(
        and_(_phrase_in(capability, Topic.name), _phrase_in(quote, Topic.name)), grounded_alias
    )
    requested_alias = (
        select(1)
        .select_from(aliases)
        .where(normalized_text(aliases.c.name) == normalized_text(literal(query)))
        .correlate(Topic)
        .exists()
    )
    exact_topic = or_(
        normalized_text(Topic.name) == normalized_text(literal(query)), requested_alias
    )
    separated_query = re.sub("[-_]", " ", query.casefold())
    short_terms = set(re.findall(r"(?<!\w)[a-z0-9]{1,2}(?!\w)", separated_query))
    short_terms -= STOP_WORDS | {"i", "me", "my"}
    symbols = set(re.findall(r"(?<!\w)(?:c\+\+|c#|f#)(?!\w)", query.casefold()))
    terms = (words(separated_query) - SEARCH_FILLER - words(" ".join(BROAD))) | short_terms
    required = sorted(terms)
    task = (
        and_(
            _evidence_terms(capability).contains(required),
            _evidence_terms(quote).contains(required),
            *(
                and_(_phrase_in(capability, literal(term)), _phrase_in(quote, literal(term)))
                for term in sorted(symbols)
            ),
        )
        if (terms | symbols) - BROAD
        else false()
    )
    return and_(
        func.lower(Topic.name).not_in(BROAD),
        select(1)
        .select_from(evidence)
        .where(grounded_topic, or_(exact_topic, task))
        .correlate(PartnerProduct, Topic)
        .exists(),
    )
