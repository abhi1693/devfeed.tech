"""Preview or repair imported tag labels and hashtag-derived slugs."""

import argparse
import json
import re

from devfeed_core.db import session_factory
from devfeed_core.models import ArticleTag, Tag
from devfeed_core.source_tags import source_tag_slug
from devfeed_core.tag_names import normalize_tag_name
from sqlalchemy import case, delete, literal, select, text
from sqlalchemy.dialects.postgresql import insert


def normalize_tags(session, *, apply=False):
    rows = session.scalars(select(Tag).order_by(Tag.slug)).all()
    by_slug = {row.slug: row for row in rows}
    changes, conflicts = [], []
    for tag in rows:
        name = normalize_tag_name(tag.name)
        slug = tag.slug
        canonical = source_tag_slug(name)
        if name != tag.name and slug.startswith("sharp-") and slug[6:] == canonical:
            slug = canonical
        elif not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            slug = source_tag_slug(slug)
        if (name, slug) == (tag.name, tag.slug):
            continue
        target = by_slug.get(slug, tag)
        if not name or (
            target.id != tag.id
            and (
                (tag.topic_id is not None and tag.topic_id != target.topic_id)
                or (not tag.auto_link_topic and tag.topic_id != target.topic_id)
            )
        ):
            conflicts.append(
                {
                    "id": str(tag.id),
                    "name": tag.name,
                    "reason": "Conflicting topic mapping or empty name",
                }
            )
            continue
        aliases = list(dict.fromkeys([*target.aliases, *tag.aliases, tag.name, tag.slug]))
        aliases = [
            alias
            for alias in aliases
            if alias not in {name if target is tag else target.name, slug}
        ]
        if len(aliases) > 100:
            conflicts.append({"id": str(tag.id), "name": tag.name, "reason": "Too many aliases"})
            continue
        changes.append(
            {
                "id": str(tag.id),
                "old_name": tag.name,
                "old_slug": tag.slug,
                "name": name if target is tag else target.name,
                "slug": slug,
                "merge_into": str(target.id) if target.id != tag.id else None,
            }
        )
        if not apply:
            continue
        if target.id != tag.id:
            links = insert(ArticleTag).from_select(
                ["article_id", "tag_id", "origin"],
                select(ArticleTag.article_id, literal(target.id), ArticleTag.origin).where(
                    ArticleTag.tag_id == tag.id
                ),
            )
            rank = {"manual": 4, "source": 3, "ai": 2, "heuristic": 1}
            session.execute(
                links.on_conflict_do_update(
                    index_elements=[ArticleTag.article_id, ArticleTag.tag_id],
                    set_={"origin": links.excluded.origin},
                    where=case(rank, value=links.excluded.origin, else_=0)
                    > case(rank, value=ArticleTag.origin, else_=0),
                )
            )
            session.execute(delete(ArticleTag).where(ArticleTag.tag_id == tag.id))
            session.delete(tag)
        else:
            by_slug.pop(tag.slug)
            target.name, target.slug = name, slug
            by_slug[slug] = target
        target.aliases = aliases
        session.flush()
    return {"apply": apply, "changes": changes, "conflicts": conflicts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Apply the previewed cleanup in one transaction"
    )
    args = parser.parse_args()
    with session_factory().begin() as session:
        if not args.apply:
            session.execute(text("SET TRANSACTION READ ONLY"))
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        session.execute(text("SET LOCAL statement_timeout = '30s'"))
        if args.apply:
            # Keep tag/article-tag writes stable while duplicate identities are merged.
            session.execute(text("LOCK TABLE tags, article_tags IN SHARE ROW EXCLUSIVE MODE"))
        result = normalize_tags(session, apply=args.apply)
        if result["conflicts"]:
            session.rollback()
            print(json.dumps(result, indent=2))
            raise SystemExit("Resolve reported conflicts before applying; no changes saved")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
