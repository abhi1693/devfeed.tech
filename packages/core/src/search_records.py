"""Bounded primary-key hydration and public visibility shared by search and indexing."""

import uuid
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import Select

from devfeed_core.feeds.parser import plain_text
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, ArticleTopic, Source, Tag, Topic
from devfeed_core.publication import visible_article

MODELS: dict[str, type[Article] | type[Source] | type[Tag] | type[Topic]] = {
    "articles": Article,
    "topics": Topic,
    "sources": Source,
    "tags": Tag,
}
LINKS: dict[
    str, tuple[type[ArticleTopic] | type[ArticleOrigin] | type[ArticleTag], InstrumentedAttribute]
] = {
    "topics": (ArticleTopic, ArticleTopic.topic_id),
    "sources": (ArticleOrigin, ArticleOrigin.source_id),
    "tags": (ArticleTag, ArticleTag.tag_id),
}


def public_records(session, kind, ids):
    if not ids:
        return {}
    model = MODELS[kind]
    statement: Select[Any]
    if kind == "articles":
        statement = select(
            Article.id,
            Article.title,
            Article.slug,
            Article.summary,
            Article.ai_summary,
            Article.ai_description,
            Article.author,
            Article.feed_at,
            Article.image_url,
            Article.content_type,
        ).where(visible_article())
    elif kind == "topics":
        statement = select(
            Topic.id,
            Topic.name,
            Topic.slug,
            Topic.description,
            Topic.ai_description,
            Topic.logo_url,
            Topic.aliases,
            Topic.keywords,
        ).where(Topic.status == "active")
    elif kind == "sources":
        statement = select(
            Source.id,
            Source.name,
            Source.slug,
            Source.description,
            Source.website_url,
            Source.feed_url,
            Source.logo_url,
        ).where(Source.approval_status == "approved", Source.enabled.is_(True))
    else:
        statement = select(Tag.id, Tag.name, Tag.slug, Tag.aliases)
    if kind != "articles":
        link, foreign = LINKS[kind]
        eligible = (
            select(1)
            .select_from(link)
            .join(Article, Article.id == link.article_id)
            .where(foreign == model.id, visible_article())
        )
        if kind == "topics":
            eligible = eligible.where(ArticleTopic.role.in_(["primary", "supporting"]))
        statement = statement.where(eligible.exists())
    return {
        row["id"]: dict(row)
        for row in session.execute(statement.where(model.id.in_(ids))).mappings()
    }


def hit(kind, record):
    title = record.get("title") or record["name"]
    description = plain_text(
        record.get("ai_description")
        or record.get("description")
        or record.get("ai_summary")
        or record.get("summary")
        or "",
        320,
    )
    identifier = str(record["id"])
    if kind == "tags":
        href = "/tags/" + quote(record["slug"], safe="")
    else:
        href = f"/{kind}/" + quote(record.get("slug", identifier), safe="")
    return {
        "id": identifier,
        "title": title,
        "description": description,
        "href": href,
        "image_url": record.get("image_url") or record.get("logo_url"),
        "label": record.get("content_type", kind[:-1]),
        "published_at": record["feed_at"].isoformat() if record.get("feed_at") else None,
    }


def documents(session, kind, ids):
    records = public_records(session, kind, ids)
    terms: dict[uuid.UUID, list[str]] = {identifier: [] for identifier in records}
    if kind == "articles" and records:
        for related, (link, foreign) in LINKS.items():
            model = MODELS[related]
            columns: list[Any] = [link.article_id]
            if related == "sources":
                columns += [Source.name]
            elif related == "topics":
                columns += [Topic.name, Topic.slug, Topic.aliases]
            else:
                columns += [Tag.name, Tag.slug, Tag.aliases]
            statement = (
                select(*columns)
                .join(model, model.id == foreign)
                .where(link.article_id.in_(records))
            )
            if related == "sources":
                statement = statement.where(Source.approval_status == "approved")
            elif related == "topics":
                statement = statement.where(
                    Topic.status == "active", ArticleTopic.role.in_(["primary", "supporting"])
                )
            for row in session.execute(statement):
                for value in row[1:]:
                    terms[row[0]].extend(value if isinstance(value, list) else [value])
    result = []
    for identifier, record in records.items():
        own_terms = [
            *record.get("aliases", []),
            *record.get("keywords", []),
            record.get("slug", ""),
            record.get("website_url", ""),
            record.get("feed_url", ""),
        ]
        result.append(
            {
                "id": str(identifier),
                "title": record.get("title") or record["name"],
                "description": plain_text(
                    " ".join(
                        str(record.get(key) or "")
                        for key in ["summary", "ai_summary", "ai_description", "description"]
                    ),
                    12000,
                ),
                "terms": list(
                    dict.fromkeys(
                        str(term)[:200] for term in [*terms[identifier], *own_terms] if term
                    )
                )[:300],
                "author": record.get("author") or "",
                "published_at": int(record["feed_at"].timestamp()) if record.get("feed_at") else 0,
            }
        )
    return result, set(ids) - records.keys()
