"""Explicit article-topic repairs without replacing unrelated classifications."""

import uuid

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_core.analysis import (
    Classifications,
    TopicSelection,
    snapshot_hash,
    source_snapshot,
    validate_evidence,
)
from devfeed_core.editorial import EditorialDecision, decide_article
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleReview,
    ArticleTag,
    ArticleTopic,
    RecommendationTopicEvent,
    Topic,
    utcnow,
)
from devfeed_core.schemas import InputModel, Name, ReviewNote
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topics import lock_topics

ACTION = "correct_topics"


class TopicCorrectionIntent(InputModel):
    article_id: uuid.UUID
    remove_topic_ids: list[uuid.UUID] = Field(min_length=1, max_length=12)
    topics: list[TopicSelection] = Field(default_factory=list, max_length=12)
    actor: Name
    note: ReviewNote
    restore_publication: bool = False

    @model_validator(mode="after")
    def unique_topics(self):
        for identifiers in (self.remove_topic_ids, [item.topic_id for item in self.topics]):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("Each topic can be selected only once")
        return self


class ArticleTopicCorrection(TopicCorrectionIntent):
    expected_revision: int = Field(ge=0)
    expected_state_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def _assignment(link):
    return {
        field: str(link.topic_id) if field == "topic_id" else getattr(link, field)
        for field in ("topic_id", "role", "relevance", "evidence", "origin")
    }


def _state(session, article, body):
    links = session.scalars(
        select(ArticleTopic)
        .where(ArticleTopic.article_id == article.id)
        .order_by(ArticleTopic.topic_id)
        .execution_options(populate_existing=True)
    ).all()
    tags = session.scalars(
        select(ArticleTag)
        .where(ArticleTag.article_id == article.id)
        .order_by(ArticleTag.tag_id)
        .execution_options(populate_existing=True)
    ).all()
    source = source_snapshot(
        article, session.get(ArticleContent, article.id, populate_existing=True)
    )
    identifiers = (
        {link.topic_id for link in links}
        | set(body.remove_topic_ids)
        | {item.topic_id for item in body.topics}
    )
    definitions = session.scalars(
        select(Topic)
        .where(Topic.id.in_(identifiers))
        .order_by(Topic.id)
        .execution_options(populate_existing=True)
    ).all()
    state = {
        "article_id": str(article.id),
        "source_hash": snapshot_hash(source),
        "topics": [_assignment(link) for link in links],
        "topic_definitions": [
            {
                "id": str(topic.id),
                **{
                    field: getattr(topic, field)
                    for field in (
                        "name",
                        "slug",
                        "kind",
                        "description",
                        "ai_description",
                        "status",
                        "aliases",
                        "keywords",
                    )
                },
            }
            for topic in definitions
        ],
        "tags": [{"tag_id": str(link.tag_id), "origin": link.origin} for link in tags],
        **{
            field: getattr(article, field)
            for field in (
                "editorial_revision",
                "review_status",
                "publication_status",
                "classification_provenance",
                "language",
                "content_type",
                "content_format",
                "ai_title",
                "ai_summary",
                "ai_description",
            )
        },
    }
    return state, links, source


def _article(session, identifier, *, apply):
    statement = (
        select(Article).where(Article.id == identifier).execution_options(populate_existing=True)
    )
    if apply:
        statement = statement.with_for_update(of=Article)
    article = session.scalar(statement)
    if article is None:
        raise RecordNotFound("Article not found")
    if apply:
        lock_topics(session, read=True)
    return article


def _changes(session, body, article, links, source):
    if body.restore_publication and article.publication_status != "published":
        raise OperationConflict("Only a previously published article can be republished")
    by_id = {link.topic_id: link for link in links}
    removed = []
    for identifier in body.remove_topic_ids:
        link = by_id.get(identifier)
        if link is None:
            raise OperationConflict("A selected topic assignment no longer exists")
        if link.origin != "ai":
            raise OperationConflict("Only inferred topic assignments can be corrected")
        removed.append(link)
    for item in body.topics:
        if item.topic_id in by_id and item.topic_id not in body.remove_topic_ids:
            raise OperationConflict("A retained topic assignment cannot be overwritten")
    if body.topics:
        active = set(
            session.scalars(
                select(Topic.id).where(
                    Topic.id.in_([item.topic_id for item in body.topics]), Topic.status == "active"
                )
            )
        )
        if active != {item.topic_id for item in body.topics}:
            raise OperationConflict("Replacement topics must already be active")
        validate_evidence(
            Classifications(
                developer_relevance="uncertain",
                language=None,
                content_type=None,
                content_format=None,
                topics=body.topics,
                tags=[],
            ),
            source,
            {"topics": [{"id": str(identifier)} for identifier in active], "tags": []},
        )
    return removed


def preview_article_topic_correction(session: Session, body: TopicCorrectionIntent) -> dict:
    """Return a reviewable plan; callers can enforce a read-only transaction."""
    article = _article(session, body.article_id, apply=False)
    state, links, source = _state(session, article, body)
    removed = _changes(session, body, article, links, source)
    plan = ArticleTopicCorrection(
        **body.model_dump(),
        expected_revision=article.editorial_revision,
        expected_state_hash=snapshot_hash(state),
    )
    return {
        "correction": plan.model_dump(mode="json"),
        "removed": [_assignment(link) for link in removed],
        "added": [{**item.model_dump(mode="json"), "origin": "manual"} for item in body.topics],
        "review_status": "approved"
        if body.restore_publication
        else "rejected"
        if article.review_status == "rejected"
        else "pending",
        "publication_status": "published" if body.restore_publication else "unpublished",
    }


def apply_article_topic_correction(session: Session, body: ArticleTopicCorrection) -> dict:
    """Apply an exact reviewed plan once, retaining manual links, tags and prose."""
    article = _article(session, body.article_id, apply=True)
    state, links, source = _state(session, article, body)
    current_hash = snapshot_hash(state)
    correction_id = snapshot_hash(body.model_dump(mode="json"))
    previous = session.scalar(
        select(ArticleReview).where(
            ArticleReview.article_id == article.id,
            ArticleReview.action == ACTION,
            ArticleReview.automation["correction_id"].astext == correction_id,
        )
    )
    if previous is not None:
        if (
            article.editorial_revision != previous.revision
            or current_hash != previous.automation["after_state_hash"]
        ):
            raise OperationConflict("Article changed after this correction")
        return {"article_id": str(article.id), "status": "already_applied"}
    if (
        article.editorial_revision != body.expected_revision
        or current_hash != body.expected_state_hash
    ):
        raise OperationConflict("Article changed; preview the correction again")
    removed = _changes(session, body, article, links, source)
    # Link and article visibility triggers can touch every retained topic. Match
    # classification's stable event-lock ordering before either kind of write.
    identifiers = {link.topic_id for link in links} | {item.topic_id for item in body.topics}
    session.execute(
        insert(RecommendationTopicEvent)
        .values([{"topic_id": identifier, "version": 0} for identifier in sorted(identifiers)])
        .on_conflict_do_update(
            index_elements=["topic_id"], set_={"version": RecommendationTopicEvent.version}
        )
    )
    removed_values = [_assignment(link) for link in removed]
    for link in removed:
        session.delete(link)
    # Flush removals before inserting a reviewed replacement of the same PK.
    session.flush()
    added_values = []
    for item in body.topics:
        values = {**item.model_dump(), "origin": "manual"}
        session.add(ArticleTopic(article_id=article.id, **values))
        added_values.append({**item.model_dump(mode="json"), "origin": "manual"})
    article.editorial_revision += 1
    article.publication_status = "unpublished"
    if article.review_status != "rejected":
        article.review_status = "pending"
    article.classification_provenance = {
        **article.classification_provenance,
        "origin": "manual",
        "topic_correction": {
            "id": correction_id,
            "actor": body.actor,
            "changed_at": utcnow().isoformat(),
        },
    }
    # Keep the existing human-review marker used by publication policy. The
    # separate correction audit below records the precise assignment delta.
    session.add(
        ArticleReview(
            article_id=article.id,
            action="classify",
            actor=body.actor,
            note=body.note,
            revision=article.editorial_revision,
        )
    )
    session.flush()
    session.expire(article, ["topic_links"])
    if body.restore_publication:
        for action in ("approve", "publish"):
            decide_article(
                session,
                article.id,
                EditorialDecision(
                    action=action,
                    actor=body.actor,
                    note=body.note,
                    expected_revision=article.editorial_revision,
                ),
            )
        session.flush()
    after, _, _ = _state(session, article, body)
    session.add(
        ArticleReview(
            article_id=article.id,
            action=ACTION,
            actor=body.actor,
            note=body.note,
            revision=article.editorial_revision,
            automation={
                "correction_id": correction_id,
                "before_state_hash": current_hash,
                "after_state_hash": snapshot_hash(after),
                "removed": removed_values,
                "added": added_values,
                "previous_provenance": state["classification_provenance"],
            },
        )
    )
    session.flush()
    session.expire(article, ["topic_links"])
    return {
        "article_id": str(article.id),
        "status": "applied",
        "removed": removed_values,
        "added": added_values,
        "editorial_revision": article.editorial_revision,
    }
