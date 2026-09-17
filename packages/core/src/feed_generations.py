"""Scheduled shuffled article-ID sequences. Candidate ranking stays in PostgreSQL."""

import hashlib
import math
import uuid
from collections import Counter

from sqlalchemy import select

from devfeed_core.cache import CacheUnavailable, get_cache
from devfeed_core.models import ArticleOrigin, Source, UserRecommendation

RETENTION_HOURS = 9


def shuffled_ids(candidates, generation, publishers):
    """Seeded weighted permutation, with a soft first-screen publisher limit."""
    size = len(candidates)

    ranks = {row.article_id: index for index, row in enumerate(candidates)}

    def priority(row):
        digest = hashlib.blake2b(generation.bytes + row.article_id.bytes, digest_size=8).digest()
        uniform = ((int.from_bytes(digest, "big") >> 11) + 0.5) / (1 << 53)
        weight = 1 + 3 * (size - 1 - ranks[row.article_id]) / max(1, size - 1)
        return -math.log(uniform) / weight, row.article_id

    pending = sorted(candidates, key=priority)
    result: list[uuid.UUID] = []
    counts: Counter = Counter()
    while pending:
        choice = 0
        if len(result) < 8:
            choice = next(
                (i for i, row in enumerate(pending) if counts[publishers.get(row.article_id)] < 2),
                0,
            )
        row = pending.pop(choice)
        counts[publishers.get(row.article_id)] += 1
        result.append(row.article_id)
    return result


def sequence_key(user_id, revision, generation):
    return f"v1:{user_id}:{revision}:{generation}"


def publish_generation(session, state):
    candidates = list(
        session.scalars(
            select(UserRecommendation)
            .where(UserRecommendation.user_id == state.user_id)
            .order_by(UserRecommendation.position)
        )
    )
    publishers = (
        dict(
            session.execute(
                select(ArticleOrigin.article_id, ArticleOrigin.source_id)
                .join(Source)
                .where(
                    ArticleOrigin.article_id.in_([r.article_id for r in candidates]),
                    Source.approval_status == "approved",
                )
                .distinct(ArticleOrigin.article_id)
                .order_by(ArticleOrigin.article_id, ArticleOrigin.source_id)
            ).all()
        )
        if candidates
        else {}
    )
    generation = uuid.uuid4()
    identifiers = shuffled_ids(candidates, generation, publishers)
    # Publish Redis first. The DB state pointer becomes visible only on commit.
    # A rolled-back transaction leaves an unreachable list which expires naturally.
    # Redis failures leave the worker free to commit DB candidates for fallback.
    get_cache().write_sequence(
        sequence_key(state.user_id, state.preference_revision, generation),
        [identifier.bytes for identifier in identifiers] or [b""],
        RETENTION_HOURS * 3600,
    )
    state.generation = generation
    return len(identifiers)


def generation_ids(user_id, revision, generation, offset, count):
    rows = get_cache().read_sequence(
        sequence_key(user_id, revision, generation), offset, offset + count - 1
    )
    if rows is None:
        return None
    if rows == [b""]:
        return []
    try:
        return [uuid.UUID(bytes=value) for value in rows]
    except (ValueError, TypeError, AttributeError) as exc:
        raise CacheUnavailable from exc
