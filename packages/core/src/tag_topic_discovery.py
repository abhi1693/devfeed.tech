"""Resolve tag identities against active topics without inference or AI calls."""

import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from devfeed_core.config import get_settings
from devfeed_core.models import TAG_TOPIC_CATALOG_REVISION, Tag, Topic, utcnow
from devfeed_core.topics import lock_topics

IDENTITY_FIELDS = ("name", "slug", "aliases")


def identity_keys(values: Iterable[str]) -> list[str]:
    # Keep meaningful punctuation: C, C++, C#, .NET and R must remain distinct.
    return sorted(
        {
            re.sub(r"[\s_-]+", "-", unicodedata.normalize("NFKC", value).strip().casefold())
            for value in values
            if value.strip()
        }
    )


def record_keys(record: Tag | Topic) -> list[str]:
    return identity_keys([record.name, record.slug, *(record.aliases or [])])


def catalog_changed(session: Session) -> None:
    # Call while holding the topic catalog lock, including for bulk deletions.
    # A rolled-back edit may advance the sequence: the extra scan is harmless.
    session.execute(select(TAG_TOPIC_CATALOG_REVISION.next_value()))


def schedule_tag_topic_discovery(factory) -> dict[str, int]:
    counts = {"tags_scanned": 0, "tags_linked": 0, "tags_unlinked": 0, "tags_ambiguous": 0}
    settings = get_settings()
    if not settings.auto_link_tags:
        return counts
    with factory.begin() as session:
        # A stable catalog and locked tags make checking and applying atomic.
        # Skip busy tags rather than waiting in the opposite order to editors.
        lock_topics(session)
        revision = session.scalar(text("SELECT last_value FROM tag_topic_catalog_revision"))
        tags = session.scalars(
            select(Tag)
            .where(Tag.auto_link_topic.is_(True), Tag.topic_match_revision < revision)
            .order_by(Tag.topic_match_revision, Tag.id)
            .limit(settings.automation_batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        if not tags:
            return counts
        keys = {tag.id: record_keys(tag) for tag in tags}
        requested = {key for values in keys.values() for key in values}
        # The GIN index avoids loading the full catalog, even for large imports.
        candidates: dict[str, set[uuid.UUID]] = defaultdict(set)
        for identifier, terms in session.execute(
            select(Topic.id, Topic.identity_keys).where(
                Topic.status == "active", Topic.identity_keys.overlap(sorted(requested))
            )
        ):
            for term in requested.intersection(terms):
                candidates[term].add(identifier)
        for tag in tags:
            matches = {identifier for key in keys[tag.id] for identifier in candidates[key]}
            target = next(iter(matches)) if len(matches) == 1 else None
            if tag.topic_id != target:
                counts["tags_linked"] += target is not None
                counts["tags_unlinked"] += tag.topic_id is not None
                tag.topic_id = target
            tag.topic_match_status = (
                "matched" if target else "ambiguous" if matches else "unmatched"
            )
            tag.topic_match_revision = revision
            tag.topic_match_checked_at = utcnow()
            counts["tags_scanned"] += 1
            counts["tags_ambiguous"] += len(matches) > 1
    return counts
