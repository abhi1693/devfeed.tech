"""Seed only the disposable local database created by scripts/load-test.py."""

import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from devfeed_core.config import Settings
from devfeed_core.db import create_database_engine
from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Source, Topic
from sqlalchemy import func, insert, select
from sqlalchemy.engine import make_url


def identity(kind, index):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"devfeed-load/{kind}/{index}")


def main():
    url = os.environ["DEVFEED_DATABASE_URL"]
    parsed = make_url(url)
    if parsed.host != "127.0.0.1" or parsed.database != "devfeed_load_test":
        raise ValueError(
            "Seeding requires the runner's disposable loopback devfeed_load_test database"
        )
    size = int(sys.argv[1])
    if not 100 <= size <= 10000:
        raise ValueError("Seed size must be 100..10000")
    settings = Settings(_env_file=None, database_url=url, redis_url=os.environ["DEVFEED_REDIS_URL"])
    engine = create_database_engine(settings)
    now = datetime.now(UTC) - timedelta(minutes=1)
    try:
        with engine.begin() as connection:
            for model in (Article, Source, Topic):
                if connection.scalar(select(func.count()).select_from(model)):
                    raise ValueError("Refusing to seed a nonempty database")
            connection.execute(
                insert(Source.__table__),
                [
                    dict(
                        id=identity("source", i),
                        name=f"Load publisher {i}",
                        feed_url=f"https://publisher-{i}.example.test/feed",
                        source_type="publisher",
                        approval_status="approved",
                    )
                    for i in range(20)
                ],
            )
            connection.execute(
                insert(Topic.__table__),
                [
                    dict(
                        id=identity("topic", i),
                        name=f"Load topic {i}",
                        slug=f"load-topic-{i}",
                        kind="technology",
                        status="active",
                    )
                    for i in range(20)
                ],
            )
            connection.execute(
                insert(Article.__table__),
                [
                    dict(
                        id=identity("article", i),
                        title=f"Database engineering article {i}",
                        canonical_url=f"https://publisher-{i % 20}.example.test/article/{i}",
                        url_hash=identity("article", i).hex,
                        summary="Python database API engineering. " * 30,
                        language="en",
                        content_type="article",
                        review_status="approved",
                        publication_status="published",
                        discovered_at=now,
                        feed_at=now - timedelta(minutes=i),
                        published_to_feed_at=now - timedelta(minutes=i),
                    )
                    for i in range(size)
                ],
            )
            connection.execute(
                insert(ArticleOrigin.__table__),
                [
                    dict(
                        article_id=identity("article", i),
                        source_id=identity("source", i % 20),
                        entry_key=f"load-{i}",
                        original_url=f"https://publisher-{i % 20}.example.test/article/{i}",
                    )
                    for i in range(size)
                ],
            )
            connection.execute(
                insert(ArticleTopic.__table__),
                [
                    dict(
                        article_id=identity("article", i),
                        topic_id=identity("topic", i % 20),
                        role="primary",
                        relevance=1.0,
                        evidence="Synthetic load fixture",
                    )
                    for i in range(size)
                ],
            )
    finally:
        engine.dispose()
    print(f"Seeded {size} published articles, 20 sources and 20 topics")


if __name__ == "__main__":
    main()
