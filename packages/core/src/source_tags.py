"""Import explicit feed labels independently of inferred article classifications."""

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, lazyload

from devfeed_core.feeds.parser import plain_text
from devfeed_core.models import Article, ArticleTag, Tag
from devfeed_core.tag_names import normalize_tag_name


def source_tag_names(values: Iterable[object]) -> dict[str, str]:
    names: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str):
            continue
        name = normalize_tag_name(plain_text(unicodedata.normalize("NFKC", value), 1000))[:100]
        if name and any(character.isalnum() for character in name):
            names.setdefault(name.casefold(), name)
    return names


def source_tag_slug(name: str) -> str:
    name = normalize_tag_name(name)
    normalized = unicodedata.normalize("NFKD", name.casefold())
    normalized = normalized.replace("+", " plus ").replace("#", " sharp ")
    ascii_name = normalized.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    digest = hashlib.sha256(name.casefold().encode()).hexdigest()[:12]
    if not slug:
        return f"tag-{digest}"
    if any(character.isalnum() and not character.isascii() for character in normalized):
        return f"{slug[:87].rstrip('-')}-{digest}"
    return slug if len(slug) <= 100 else f"{slug[:87].rstrip('-')}-{digest}"


def resolve_source_tags(
    session: Session, values: Iterable[object]
) -> tuple[dict[str, uuid.UUID], int]:
    names = source_tag_names(values)
    if not names:
        return {}, 0
    tags = session.scalars(select(Tag).order_by(Tag.slug)).all()
    identities: dict[str, uuid.UUID] = {}
    # Exact names/slugs take priority over aliases, with a deterministic tie-break.
    for tag in tags:
        for key in source_tag_names([tag.name, tag.slug]):
            identities.setdefault(key, tag.id)
    for tag in tags:
        for key in source_tag_names(tag.aliases):
            identities.setdefault(key, tag.id)
    by_slug = {tag.slug: tag.id for tag in tags}
    resolved: dict[str, uuid.UUID] = {}
    missing: dict[str, str] = {}
    for key, name in sorted(names.items()):
        slug = source_tag_slug(name)
        identifier = identities.get(key) or by_slug.get(slug)
        if identifier:
            resolved[key] = identifier
        else:
            missing.setdefault(slug, name)
    created = 0
    if missing:
        # Resolve all labels in slug order before taking article locks, including
        # overlapping imports by different workers. Never overwrite admin edits.
        created = len(
            session.scalars(
                insert(Tag)
                .values(
                    [
                        {"name": missing[slug], "slug": slug, "aliases": []}
                        for slug in sorted(missing)
                    ]
                )
                .on_conflict_do_nothing(index_elements=[Tag.slug])
                .returning(Tag.id)
            ).all()
        )
        by_slug.update(
            session.execute(select(Tag.slug, Tag.id).where(Tag.slug.in_(missing))).tuples().all()
        )
    for key, name in names.items():
        if key not in resolved:
            resolved[key] = by_slug[source_tag_slug(name)]
    return resolved, created


def attach_source_tags(
    session: Session, article_id: uuid.UUID, tag_ids: Iterable[uuid.UUID]
) -> int:
    identifiers = sorted(set(tag_ids))
    if not identifiers:
        return 0
    article = session.scalar(
        select(Article)
        .options(lazyload("*"))
        .where(Article.id == article_id)
        .with_for_update(of=Article)
    )
    if article is None or (article.classification_provenance or {}).get("origin") == "manual":
        return 0  # An explicit classification can remove a supplied tag permanently.
    saved = session.scalars(
        insert(ArticleTag)
        .values(
            [
                {"article_id": article_id, "tag_id": tag_id, "origin": "source"}
                for tag_id in identifiers
            ]
        )
        .on_conflict_do_update(
            index_elements=[ArticleTag.article_id, ArticleTag.tag_id],
            set_={"origin": "source"},
            where=ArticleTag.origin.in_(["heuristic", "ai"]),
        )
        .returning(ArticleTag.tag_id)
    ).all()
    session.expire(article, ["tags"])
    return len(saved)
