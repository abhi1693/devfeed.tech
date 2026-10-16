"""Bounded primary-key hydration and public visibility shared by search and indexing."""

import uuid
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import Select

from devfeed_core.feeds.parser import plain_text
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, ArticleTopic, Source, Tag, Topic
from devfeed_core.partner_link_rel import default_link_rel, sponsored_article_link_rel
from devfeed_core.publication import visible_article
from devfeed_core.schemas import VideoMetadata

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
            func.coalesce(Article.ai_title, Article.title).label("title"),
            Article.slug,
            Article.summary,
            Article.ai_summary,
            Article.ai_description,
            Article.author,
            Article.feed_at,
            Article.image_url,
            Article.content_type,
            Article.content_format,
            Article.provider,
            Article.provider_resource_id,
            Article.video_metadata,
            Article.sponsor_names,
        ).where(visible_article())
    elif kind == "topics":
        statement = select(
            Topic.id,
            Topic.name,
            Topic.slug,
            Topic.description,
            Topic.ai_description,
            Topic.logo_url,
            Topic.managed_logo,
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
            Source.managed_logo,
            Source.provider,
            Source.resource_kind,
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
        if kind == "sources":
            eligible = eligible.where(ArticleOrigin.active.is_(True))
        statement = statement.where(eligible.exists())
    records = {
        row["id"]: dict(row)
        for row in session.execute(statement.where(model.id.in_(ids))).mappings()
    }
    if kind == "articles":
        policies = sponsored_article_link_rel(
            session,
            [identifier for identifier, record in records.items() if record["sponsor_names"]],
        )
        for identifier, record in records.items():
            record["sponsor_link_rel"] = policies.get(identifier, default_link_rel())
    return records


def hit(kind, record):
    if kind in {"topics", "sources"}:
        from types import SimpleNamespace

        from devfeed_core.logos import logo_url, logo_variants

        topic = SimpleNamespace(**{"logo_url": None, **record})
        record = {
            **record,
            "logo_url": logo_url(topic) or (record.get("logo_url") if kind == "sources" else None),
            "logo_variants": logo_variants(topic),
        }

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
        "logo_variants": record.get("logo_variants", []),
        "sponsor_names": record.get("sponsor_names", []),
        "sponsor_link_rel": record.get("sponsor_link_rel", default_link_rel()),
        "label": record.get("content_type", kind[:-1]),
        "published_at": record["feed_at"].isoformat() if record.get("feed_at") else None,
        "content_format": record.get("content_format"),
        "provider": record.get("provider"),
        "provider_resource_id": record.get("provider_resource_id"),
        "video_metadata": VideoMetadata.model_validate(
            record.get("video_metadata") or {}
        ).model_dump(mode="json")
        if kind == "articles" and record.get("provider") == "youtube"
        else None,
    }


def documents(session, kind, ids):
    records = public_records(session, kind, ids)
    terms: dict[uuid.UUID, list[str]] = {identifier: [] for identifier in records}
    related_ids: dict[str, dict[uuid.UUID, set[str]]] = {
        related: {identifier: set() for identifier in records} for related in LINKS
    }
    if kind == "articles" and records:
        for related, (link, foreign) in LINKS.items():
            model = MODELS[related]
            columns: list[Any] = [link.article_id, foreign]
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
                statement = statement.where(
                    Source.approval_status == "approved", ArticleOrigin.active.is_(True)
                )
            elif related == "topics":
                statement = statement.where(
                    Topic.status == "active", ArticleTopic.role.in_(["primary", "supporting"])
                )
            for row in session.execute(statement):
                related_ids[related][row[0]].add(str(row[1]))
                for value in row[2:]:
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
                "topics": sorted(related_ids["topics"][identifier]),
                "sources": sorted(related_ids["sources"][identifier]),
                "tags": sorted(related_ids["tags"][identifier]),
                "content_type": record.get("content_type") or "article",
            }
        )
    return result, set(ids) - records.keys()
