"""Canonical topic identities and explicit graph edges; never transitive feed matches."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from devfeed_core.models import Topic, TopicRelation
from devfeed_core.schemas import InputModel, Keyword, ORMModel, Slug, TaxonomyName, TopicKind
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.urls import validate_public_url


class TopicFact(InputModel):
    name: Keyword
    value: str = Field(min_length=1, max_length=500)
    source_url: str = Field(max_length=2048)
    retrieved_at: datetime
    _public_url = field_validator("source_url")(validate_public_url)

    @field_validator("retrieved_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("retrieved_at requires a timezone")
        return value


class TopicWrite(InputModel):
    name: TaxonomyName
    slug: Slug
    kind: TopicKind
    aliases: list[Keyword] = Field(default_factory=list, max_length=50)
    keywords: list[Keyword] = Field(default_factory=list, max_length=100)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    website_url: str | None = Field(default=None, max_length=2048)
    logo_url: str | None = Field(default=None, max_length=2048)
    facts: list[TopicFact] = Field(default_factory=list, max_length=50)

    @field_validator("keywords", "aliases")
    @classmethod
    def unique_terms(cls, values):
        return list({value.casefold(): value for value in values}.values())

    @field_validator("website_url", "logo_url")
    @classmethod
    def public_url(cls, value):
        return validate_public_url(value) if value is not None else None


class TopicOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: str
    aliases: list[str]
    keywords: list[str] = Field(default_factory=list)
    description: str | None
    ai_description: str | None
    website_url: str | None
    logo_url: str | None
    facts: list[TopicFact]


class RelationWrite(InputModel):
    topic_id: uuid.UUID
    related_topic_id: uuid.UUID
    relation: Literal["uses_language", "depends_on", "implements", "part_of", "related_to"]
    evidence_url: str | None = Field(default=None, max_length=2048)

    @field_validator("evidence_url")
    @classmethod
    def public_url(cls, value):
        return validate_public_url(value) if value is not None else None


def identity_terms(topic: Topic | TopicWrite) -> set[str]:
    """Canonical names/slugs are unique; searchable aliases may be shared."""
    return {value.strip().casefold() for value in [topic.name, topic.slug]}


def lock_topics(session: Session) -> None:
    session.execute(text("LOCK TABLE topics IN SHARE ROW EXCLUSIVE MODE"))


def save_topic(
    session: Session,
    body: TopicWrite,
    identifier=None,
    *,
    initial_status: Literal["active", "proposed", "rejected"] = "active",
):
    # Serialize identity changes; a concurrent editor or proposal cannot create a
    # second canonical entity while this transaction is resolving identity.
    lock_topics(session)
    topics = session.scalars(select(Topic)).all()
    current = next((item for item in topics if item.id == identifier), None)
    if identifier is not None and current is None:
        raise RecordNotFound("Topic not found")
    for topic in topics:
        if topic.id != identifier and identity_terms(topic) & identity_terms(body):
            field = "name" if body.name.strip().casefold() in identity_terms(topic) else "slug"
            raise OperationConflict(
                f'{field.capitalize()} "{getattr(body, field)}" already identifies topic '
                f'"{topic.name}" ({topic.slug}). Choose a different {field} '
                "or edit the existing topic."
            )
    if current is None:
        current = Topic(status=initial_status)
        session.add(current)
    for field, value in body.model_dump(mode="json").items():
        setattr(current, field, value)
    session.flush()
    return current


def relate_topics(session: Session, body: RelationWrite):
    if body.topic_id == body.related_topic_id:
        raise OperationConflict("A topic cannot be related to itself")
    if any(
        session.get(Topic, identifier) is None
        for identifier in (body.topic_id, body.related_topic_id)
    ):
        raise RecordNotFound("Topic not found")
    key = (body.topic_id, body.related_topic_id, body.relation)
    relation = session.get(TopicRelation, key)
    if relation is None:
        relation = TopicRelation(**body.model_dump())
        session.add(relation)
    else:
        relation.evidence_url = body.evidence_url
    return relation
