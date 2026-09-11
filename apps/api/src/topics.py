from devfeed_core.models import Article, ArticleTopic, Topic, TopicRelation
from devfeed_core.publication import visible_article
from devfeed_core.topics import TopicOut
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1/topics", tags=["topics"], route_class=CachedReadRoute)


@router.get("", response_model=list[TopicOut])
def topics(
    session: DB,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    has_articles: bool = Query(
        False, description="Only topics with articles visible in their feed"
    ),
):
    statement = select(Topic).where(Topic.status == "active")
    if has_articles:
        # Match the topic feed's publication, provenance and direct-assignment rules.
        statement = statement.where(
            select(1)
            .select_from(ArticleTopic)
            .join(Article, Article.id == ArticleTopic.article_id)
            .where(
                ArticleTopic.topic_id == Topic.id,
                ArticleTopic.role.in_(["primary", "supporting"]),
                visible_article(),
            )
            .exists()
        )
    return session.scalars(
        statement.order_by(Topic.name, Topic.id).offset(offset).limit(limit)
    ).all()


@router.get("/{slug}", response_model=TopicOut)
def topic(slug: str, session: DB):
    result = session.scalar(select(Topic).where(Topic.slug == slug, Topic.status == "active"))
    if result is None:
        raise HTTPException(404, "Topic not found")
    return result


@router.get("/{slug}/relations")
def relations(slug: str, session: DB):
    current = topic(slug, session)
    rows = session.execute(
        select(TopicRelation, Topic)
        .join(Topic, Topic.id == TopicRelation.related_topic_id)
        .where(TopicRelation.topic_id == current.id, Topic.status == "active")
        .order_by(TopicRelation.relation, Topic.slug)
        .limit(500)
    ).all()
    return [
        {
            "relation": link.relation,
            "topic": TopicOut.model_validate(related),
            "evidence_url": link.evidence_url,
        }
        for link, related in rows
    ]
