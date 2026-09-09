"""Remove a topic and reconcile its links in one editorial transaction."""

import uuid

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from devfeed_core.models import (
    Article,
    ArticleReview,
    ArticleTopic,
    Tag,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelation,
    TopicRelationProposal,
    utcnow,
)
from devfeed_core.schemas import ORMModel
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_proposals import TopicDraft, snapshot
from devfeed_core.topics import lock_topics, save_topic


class TopicDeleteImpact(ORMModel):
    articles: int
    published_articles: int
    tags: int
    relationships: int
    relationship_proposals: int
    research_jobs: int
    pending_topic_proposals: int


def _edges(model, identifier):
    return or_(model.topic_id == identifier, model.related_topic_id == identifier)


def deletion_impact(session: Session, identifier: uuid.UUID) -> TopicDeleteImpact:
    if session.get(Topic, identifier) is None:
        raise RecordNotFound("Topic not found")

    def count(model, *where):
        return session.scalar(select(func.count()).select_from(model).where(*where)) or 0

    article_ids = select(ArticleTopic.article_id).where(ArticleTopic.topic_id == identifier)
    return TopicDeleteImpact(
        articles=count(ArticleTopic, ArticleTopic.topic_id == identifier),
        published_articles=count(
            Article, Article.id.in_(article_ids), Article.publication_status == "published"
        ),
        tags=count(Tag, Tag.topic_id == identifier),
        relationships=count(TopicRelation, _edges(TopicRelation, identifier)),
        relationship_proposals=count(
            TopicRelationProposal, _edges(TopicRelationProposal, identifier)
        ),
        research_jobs=count(TopicAnalysisJob, TopicAnalysisJob.topic_id == identifier),
        pending_topic_proposals=count(
            TopicProposal, TopicProposal.topic_id == identifier, TopicProposal.status == "pending"
        ),
    )


def delete_topic(
    session: Session,
    identifier: uuid.UUID,
    actor: dict[str, str],
    replacement_id: uuid.UUID | None = None,
    replacement_proposal_id: uuid.UUID | None = None,
) -> None:
    try:
        with session.begin_nested():
            _delete_topic(session, identifier, actor, replacement_id, replacement_proposal_id)
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise OperationConflict(
                "This topic or its linked records are being updated. Try deleting again."
            ) from exc
        raise


def _delete_topic(session, identifier, actor, replacement_id, replacement_proposal_id):
    if replacement_id and replacement_proposal_id:
        raise OperationConflict("Choose one replacement topic or proposal")
    lock_topics(session)
    # Workers lock article/job rows before the topic catalog. Never wait for
    # those rows while holding the catalog lock: roll back cleanly on contention.
    # Lock the deleted topic first so no new FK links can appear during cleanup.
    topic = session.scalar(select(Topic).where(Topic.id == identifier).with_for_update(nowait=True))
    if topic is None:
        raise RecordNotFound("Topic not found")
    replacement_proposal = None
    if replacement_proposal_id:
        replacement_proposal = session.scalar(
            select(TopicProposal)
            .where(TopicProposal.id == replacement_proposal_id)
            .with_for_update(nowait=True)
        )
        if replacement_proposal is None:
            raise RecordNotFound("Replacement proposal not found")
        if replacement_proposal.status != "pending":
            raise OperationConflict("Replacement proposal changed. Choose a replacement again")
        if replacement_proposal.topic_id is None and replacement_proposal.action != "create":
            raise OperationConflict(
                "This proposal no longer has a topic. Choose another replacement"
            )
        replacement_id = replacement_proposal.topic_id
    draft = (
        TopicDraft.model_validate(replacement_proposal.proposed) if replacement_proposal else None
    )
    replacement = session.get(Topic, replacement_id) if replacement_id else None
    if replacement_id:
        if replacement_id == identifier:
            raise OperationConflict("Choose a different replacement topic")
        if replacement is None:
            raise RecordNotFound("Replacement topic not found")

    def locked(model, *where):
        return session.scalars(
            select(model).where(*where).with_for_update(of=model, nowait=True)
        ).all()

    articles = locked(
        Article,
        Article.id.in_(select(ArticleTopic.article_id).where(ArticleTopic.topic_id == identifier)),
    )
    links = locked(ArticleTopic, ArticleTopic.topic_id == identifier)
    tags = locked(Tag, Tag.topic_id == identifier)
    proposals = locked(TopicProposal, TopicProposal.topic_id == identifier)
    jobs = locked(TopicAnalysisJob, TopicAnalysisJob.topic_id == identifier)
    # Take child locks before any writes, including rows removed by FK cascades.
    locked(TopicRelation, _edges(TopicRelation, identifier))
    relationship_proposals = or_(
        _edges(TopicRelationProposal, identifier),
        TopicRelationProposal.job_id.in_([job.id for job in jobs]),
    )
    locked(TopicRelationProposal, relationship_proposals)

    now = utcnow()
    note = f"Topic '{topic.name}' deleted. "
    replacement_name = replacement.name if replacement else draft.name if draft else None
    note += (
        f"Article and tag links reassigned to '{replacement_name}'."
        if replacement_name
        else "Topic links removed."
    )
    # Remove the old identity first so a pending replacement can legitimately
    # reuse its former name/aliases. Everything remains in the same savepoint.
    for tag in tags:
        tag.topic_id = None
    for proposal in proposals:
        if proposal.status == "pending":
            proposal.status, proposal.reviewed_at, proposal.reviewed_by = "rejected", now, actor
            proposal.review_note = note
        proposal.topic_id = None
    session.flush()
    session.execute(delete(ArticleTopic).where(ArticleTopic.topic_id == identifier))
    session.execute(delete(TopicRelationProposal).where(relationship_proposals))
    session.execute(delete(TopicAnalysisJob).where(TopicAnalysisJob.topic_id == identifier))
    session.execute(delete(TopicRelation).where(_edges(TopicRelation, identifier)))
    session.execute(delete(Topic).where(Topic.id == identifier))

    if replacement_proposal and replacement is None:
        assert draft is not None
        replacement = save_topic(session, draft, initial_status="proposed")
        replacement_id = replacement.id
        replacement_proposal.topic_id = replacement.id
        replacement_proposal.baseline = snapshot(replacement)
    for article in articles:
        article.publication_status = "unpublished"
        if article.review_status != "rejected":
            article.review_status = "pending"
        # In-flight AI results cannot overwrite this explicit editorial change.
        article.editorial_revision += 1
        article.classification_provenance = {
            **article.classification_provenance,
            "topic_change": {
                "removed_topic_id": str(identifier),
                "replacement_topic_id": str(replacement_id) if replacement_id else None,
                "actor": actor,
                "changed_at": now.isoformat(),
            },
        }
        session.add(
            ArticleReview(
                article_id=article.id,
                action="topic_relink" if replacement else "topic_delete",
                actor=actor.get("subject"),
                note=note,
                revision=article.editorial_revision,
            )
        )

    if replacement:
        strength = {"primary": 4, "supporting": 3, "comparison": 2, "incidental": 1}
        for link in links:
            existing = session.get(ArticleTopic, (link.article_id, replacement.id))
            if existing:
                # A primary link must not disappear when both topics were assigned.
                if strength[link.role] > strength[existing.role]:
                    existing.role, existing.evidence = link.role, link.evidence
                existing.relevance = max(existing.relevance, link.relevance)
                existing.origin = "manual"
            else:
                session.add(
                    ArticleTopic(
                        article_id=link.article_id,
                        topic_id=replacement.id,
                        role=link.role,
                        relevance=link.relevance,
                        evidence=link.evidence,
                        origin="manual",
                    )
                )
    for tag in tags:
        tag.topic_id = replacement_id
    session.flush()
