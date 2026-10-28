"""Exact recurring counts and bounded plans on disposable, synthetic PostgreSQL data."""

import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from devfeed_core.db import get_engine
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
)
from devfeed_core.search_index import _visible_counts, _visible_tag_count_query
from sqlalchemy import event, text

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)

# Previous production queries are reference workloads, not an implementation of
# the new paths. Keep their wide joins to detect reintroduced spills at scale.
PREVIOUS_TAG_COUNT = text("""
    SELECT count(*) FROM tags t WHERE EXISTS (
        SELECT 1 FROM article_tags at JOIN articles a ON a.id=at.article_id
        WHERE at.tag_id=t.id AND a.publication_status='published' AND a.review_status='approved'
        AND EXISTS (SELECT 1 FROM article_origins o JOIN sources s ON s.id=o.source_id
                    WHERE o.article_id=a.id AND s.approval_status='approved'))
""")


def test_visible_counts_preserve_visibility_and_distinct_destinations(database):
    with database.begin() as session:
        enabled = Source(
            name="Enabled",
            feed_url="https://enabled.test/rss",
            source_type="publisher",
            approval_status="approved",
        )
        disabled = Source(
            name="Disabled",
            feed_url="https://disabled.test/rss",
            source_type="publisher",
            approval_status="approved",
            enabled=False,
        )
        rejected = Source(name="Rejected", source_type="publisher", approval_status="rejected")
        primary = Topic(name="Primary", slug="primary", kind="technology", status="active")
        supporting = Topic(name="Supporting", slug="supporting", kind="technology", status="active")
        incidental = Topic(name="Incidental", slug="incidental", kind="technology", status="active")
        proposed = Topic(name="Proposed", slug="proposed", kind="technology", status="proposed")
        shared = Tag(name="Shared", slug="shared")
        disabled_tag = Tag(name="Disabled source", slug="disabled-source")
        private = Tag(name="Private", slug="private")
        session.add_all(
            [
                enabled,
                disabled,
                rejected,
                primary,
                supporting,
                incidental,
                proposed,
                shared,
                disabled_tag,
                private,
                Tag(name="Unlinked", slug="unlinked"),
            ]
        )
        session.flush()
        for i, (publisher, publication, review, tag) in enumerate(
            [
                (enabled, "published", "approved", shared),
                (enabled, "published", "approved", shared),
                (disabled, "published", "approved", disabled_tag),
                (rejected, "published", "approved", private),
                (enabled, "unpublished", "approved", private),
                (enabled, "unpublished", "pending", private),
                (enabled, "unpublished", "rejected", private),
                (None, "published", "approved", private),
            ]
        ):
            article = Article(
                title=f"Article {i}",
                canonical_url=f"https://article.test/{i}",
                url_hash=str(i),
                publication_status=publication,
                review_status=review,
            )
            session.add(article)
            session.flush()
            session.add(ArticleTag(article_id=article.id, tag_id=tag.id))
            if publisher:
                session.add(
                    ArticleOrigin(
                        article_id=article.id,
                        source_id=publisher.id,
                        entry_key=str(i),
                        original_url=article.canonical_url,
                    )
                )
            if i < 2:
                # Duplicate approved origins must not multiply any count.
                session.add(
                    ArticleOrigin(
                        article_id=article.id,
                        source_id=enabled.id,
                        entry_key=f"duplicate-{i}",
                        original_url=article.canonical_url,
                    )
                )
                for topic, role in (
                    (primary, "primary"),
                    (supporting, "supporting"),
                    (incidental, "incidental"),
                    (proposed, "primary"),
                ):
                    session.add(
                        ArticleTopic(
                            article_id=article.id,
                            topic_id=topic.id,
                            role=role,
                            relevance=1.0,
                            evidence="Synthetic evidence",
                        )
                    )
        session.flush()
        assert _visible_counts(session) == dict(articles=3, topics=2, sources=1, tags=2)
        enabled.approval_status = "rejected"
        session.flush()
        # Disabled approved sources still make articles visible, but aren't indexed
        # as discoverable sources themselves. Visibility changes must count now.
        assert _visible_counts(session) == dict(articles=1, topics=0, sources=0, tags=1)
        disabled.approval_status = "rejected"
        session.flush()
        assert _visible_counts(session) == dict(articles=0, topics=0, sources=0, tags=0)


@pytest.fixture
def recurring_query_data(database):
    # Match the production cardinalities and the report's wide inline JSON. Using
    # raw INSERT SELECT keeps this reproducible without external data or credentials.
    with get_engine().begin() as connection:
        # Bulk fixture creation exercises foreign keys and outbox triggers. Allow
        # it longer than the application timeout only in this disposable database.
        connection.execute(text("SET LOCAL statement_timeout='5min'"))
        for sql in (
            """INSERT INTO sources(id,name,feed_url,source_type,approval_status,enabled,
                submission_channel,updated_at,poll_interval_seconds,next_fetch_at,
                consecutive_failures,created_at,slug)
                SELECT md5('s'||i)::uuid,'Publisher '||i,'https://publisher.test/'||i,'publisher',
                    CASE WHEN i<=1196 THEN 'approved' ELSE 'rejected' END,i<=1190,
                    'cli',now(),1800,now(),0,now(),'publisher-'||i
                FROM generate_series(1,1261) i""",
            """INSERT INTO tags(id,name,slug,aliases)
                SELECT md5('t'||i)::uuid,'Tag '||i,'tag-'||i,ARRAY[]::varchar[]
                FROM generate_series(1,34074) i""",
        ):
            connection.execute(text(sql), {"now": NOW})
        # Matching projection runs for every inserted article. Bound each setup
        # statement without reducing production cardinality or bypassing triggers.
        article_sql = text(
            """INSERT INTO articles(id,canonical_url,url_hash,title,summary,review_status,
                publication_status,feed_at,published_to_feed_at,discovered_at,content_type,slug)
                SELECT md5('a'||i)::uuid,'https://article.test/'||i,md5('a'||i),'Article '||i,
                    repeat('Synthetic article body ',30),
                    CASE WHEN i<=55730 THEN 'approved' ELSE 'rejected' END,
                    CASE WHEN i<=55730 THEN 'published' ELSE 'unpublished' END,
                    :now - (i%30)*interval '1 day',
                    CASE WHEN i<=55730 THEN :now - (i%30)*interval '1 day' END,
                    :now - interval '10 minutes' - (i%30)*interval '1 day','article','article-'||i
                FROM generate_series(
                    CAST(:batch_start AS integer),CAST(:batch_end AS integer)) i"""
        )
        for batch_start in range(1, 60341, 5000):
            connection.execute(
                article_sql,
                {
                    "now": NOW,
                    "batch_start": batch_start,
                    "batch_end": min(batch_start + 4999, 60340),
                },
            )
        for sql in (
            """INSERT INTO article_origins(id,article_id,source_id,entry_key,original_url,
                source_metadata)
                SELECT gen_random_uuid(),md5('a'||i)::uuid,md5('s'||(1+i%1261))::uuid,i::text,
                    'https://article.test/'||i,'{}'::jsonb FROM generate_series(
                        CAST(:batch_start AS integer),CAST(:batch_end AS integer)) i""",
            """INSERT INTO article_tags(article_id,tag_id)
                SELECT md5('a'||i)::uuid,md5('t'||(1+(i*10+j)%34074))::uuid
                FROM generate_series(
                    CAST(:batch_start AS integer),CAST(:batch_end AS integer)) i
                CROSS JOIN generate_series(1,10) j""",
            """INSERT INTO article_reviews(id,article_id,action,revision,automation,created_at)
                SELECT gen_random_uuid(),a.id,CASE WHEN j=1 THEN 'publish' ELSE 'approve' END,j,
                    CASE WHEN i%31=0 AND j=2 THEN '{}'::jsonb
                    ELSE jsonb_build_object('policy','synthetic','context',repeat(md5(i::text),9))
                    END,coalesce(a.published_to_feed_at,a.discovered_at)-interval '1 minute'
                FROM generate_series(
                    CAST(:batch_start AS integer),CAST(:batch_end AS integer)) i
                JOIN articles a ON a.id=md5('a'||i)::uuid
                CROSS JOIN generate_series(1,3) j WHERE j<=2 OR i<=33000""",
        ):
            # Keep the full relationship/review cardinalities and every FK/outbox
            # trigger, while bounding setup statements just like article inserts.
            for batch_start in range(1, 60341, 1000):
                connection.execute(
                    text(sql),
                    {
                        "batch_start": batch_start,
                        "batch_end": min(batch_start + 999, 60340),
                    },
                )
        for table in (
            "articles",
            "sources",
            "tags",
            "article_origins",
            "article_tags",
            "article_reviews",
        ):
            connection.execute(text(f"ANALYZE {table}"))


def explain(connection, statement, parameters):
    # Capture driver-ready values (including SQLAlchemy's JSONB bind processors),
    # so EXPLAIN measures the exact application SQL, not a rewritten approximation.
    captured = []

    def capture(conn, cursor, sql, params, context, executemany):
        captured.append((sql, params))

    event.listen(connection, "before_cursor_execute", capture)
    try:
        result = tuple(connection.execute(statement, parameters).one())
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert len(captured) == 1
    sql, params = captured[0]
    plan = connection.exec_driver_sql("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + sql, params)
    return result, plan.scalar_one()[0]


def profile_pair(connection, before, after, parameters):
    timings = {"before": [], "after": []}
    for _ in range(3):
        for name, statement in (("before", before), ("after", after)):
            started = time.perf_counter()
            connection.execute(statement, parameters).one()
            timings[name].append(1000 * (time.perf_counter() - started))
    before_result, before_plan = explain(connection, before, parameters)
    after_result, after_plan = explain(connection, after, parameters)
    assert before_result == after_result
    return {
        "result": list(after_result),
        "before_ms": statistics.median(timings["before"]),
        "after_ms": statistics.median(timings["after"]),
        "before_plan": before_plan,
        "after_plan": after_plan,
    }


def test_recurring_query_plans_bound_spills_at_production_scale(recurring_query_data):
    reports = {}
    with get_engine().connect() as connection:
        # Compare both paths at production's 4 MB limit; no global DB tuning.
        connection.execute(text("SET LOCAL work_mem='4MB'"))
        connection.execute(text("SET LOCAL statement_timeout='15s'"))
        reports["tags"] = profile_pair(
            connection, PREVIOUS_TAG_COUNT, _visible_tag_count_query(), {}
        )
        assert reports["tags"]["result"] == [34074]
        assert reports["tags"]["after_plan"]["Plan"]["Temp Written Blocks"] == 0
    if target := os.environ.get("DEVFEED_RECURRING_PROFILE_REPORT"):
        Path(target).write_text(json.dumps(reports, indent=2) + "\n")
