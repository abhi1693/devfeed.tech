import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine, session_factory
from devfeed_core.models import Base, Tag
from redis import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def unit_test_settings(request, monkeypatch):
    # Ordinary behavior tests stay independent of cache state. Cache tests opt in
    # explicitly and use an in-memory fake or disposable integration Redis.
    monkeypatch.setenv("DEVFEED_CACHE_ENABLED", "false")
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "false")
    monkeypatch.setenv("DEVFEED_NOTIFICATIONS_ENABLED", "false")
    if request.node.get_closest_marker("integration"):
        yield
        return
    # Unit tests explicitly supply non-routable connection URLs instead of using .env.
    monkeypatch.setenv(
        "DEVFEED_DATABASE_URL", "postgresql+psycopg://unit@database.invalid/unit_test"
    )
    monkeypatch.setenv("DEVFEED_REDIS_URL", "redis://redis.invalid/15")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def integration_environment():
    database_url = os.environ.get("DEVFEED_TEST_DATABASE_URL")
    redis_url = os.environ.get("DEVFEED_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip(
            "Set DEVFEED_TEST_DATABASE_URL and DEVFEED_TEST_REDIS_URL for integration tests"
        )
    if not (make_url(database_url).database or "").endswith("_test"):
        pytest.fail("Integration database name must end in _test; tests truncate its tables")
    if urlsplit(redis_url).path != "/15":
        pytest.fail("Integration Redis must use database 15; tests flush that database")
    previous = {key: os.environ.get(key) for key in ("DEVFEED_DATABASE_URL", "DEVFEED_REDIS_URL")}
    os.environ["DEVFEED_DATABASE_URL"] = database_url
    os.environ["DEVFEED_REDIS_URL"] = redis_url
    get_settings.cache_clear()
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    # Migrations only provision the disposable schema for application tests;
    # generated revisions are not test subjects.
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield
    get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def database(integration_environment, monkeypatch, rss_bytes):
    from devfeed_aggregator import source_tasks
    from devfeed_api.dependencies import get_redis
    from devfeed_core.feeds import validation
    from devfeed_core.feeds.fetcher import FetchResult

    # Keep publisher preflight deterministic; tests can override individual failures.
    monkeypatch.setattr(validation, "fetch_feed", lambda url: FetchResult(200, rss_bytes, url))
    # Trusted submissions also queue profile jobs. Worker tests must not contact
    # live websites; profile-specific tests replace this stub with their scenario.
    monkeypatch.setattr(source_tasks, "lookup_profile", lambda *args: ({}, None))

    get_redis.cache_clear()
    # Only the explicitly configured, name-checked test database is modified.
    with get_engine().begin() as connection:
        names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        connection.execute(text(f"TRUNCATE TABLE {names} CASCADE"))
    redis = Redis.from_url(get_settings().redis_url)
    redis.flushdb()
    yield session_factory()
    redis.close()
    if get_redis.cache_info().currsize:
        get_redis().close()
    get_redis.cache_clear()


@pytest.fixture
def client(database):
    from devfeed_api.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def rss_bytes():
    return (ROOT / "tests/fixtures/feed.xml").read_bytes()


@pytest.fixture
def configured_tags(database):
    """Explicit scenario data; the application starts with an empty taxonomy."""
    with database.begin() as session:
        session.add_all(
            [
                Tag(name="Python", slug="python", aliases=[]),
                Tag(name="FastAPI", slug="fastapi", aliases=[]),
                Tag(name="PostgreSQL", slug="postgresql", aliases=["postgres"]),
                Tag(name="Kubernetes", slug="kubernetes", aliases=["k8s"]),
                Tag(name="Docker", slug="docker", aliases=[]),
            ]
        )


@pytest.fixture
def publish_for_read_test(database):
    """Explicit editorial setup for read/cache tests; never used by ingestion tests implicitly."""
    import uuid

    from devfeed_core.editorial import EditorialDecision, decide_article, meaningful_text
    from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Source, Topic
    from devfeed_core.urls import fingerprint
    from sqlalchemy import select

    def publish(identifiers=None):
        with database.begin() as session:
            topic = session.scalar(select(Topic).where(Topic.slug == "fixture-engineering"))
            if topic is None:
                topic = Topic(
                    name="Fixture engineering",
                    slug="fixture-engineering",
                    kind="domain",
                    status="active",
                )
                session.add(topic)
                session.flush()
            statement = select(Article)
            if identifiers is not None:
                statement = statement.where(Article.id.in_(identifiers))
            for article in session.scalars(statement).all():
                if not article.origins:
                    source = Source(
                        name="Fixture publisher",
                        feed_url=f"https://example.com/{uuid.uuid4()}/rss",
                        source_type="publisher",
                        approval_status="approved",
                        enabled=False,
                    )
                    session.add(source)
                    session.flush()
                    session.add(
                        ArticleOrigin(
                            article_id=article.id,
                            source_id=source.id,
                            entry_key=fingerprint(article.canonical_url),
                            original_url=article.canonical_url,
                        )
                    )
                if session.get(ArticleTopic, (article.id, topic.id)) is None:
                    session.add(
                        ArticleTopic(
                            article_id=article.id,
                            topic_id=topic.id,
                            role="primary",
                            relevance=1.0,
                            evidence="Explicit test fixture",
                            origin="manual",
                        )
                    )
                article.language = article.language or "en"
                if not meaningful_text(article.summary) and not meaningful_text(article.ai_summary):
                    article.ai_summary = (
                        "This test article describes software engineering practices "
                        "for developers building applications."
                    )
                article.classification_provenance = {
                    "origin": "manual",
                    "developer_relevance": "relevant",
                }
                session.flush()
                session.expire(article, ["origins", "topic_links"])
                decide_article(session, article.id, EditorialDecision(action="approve"))
                decide_article(session, article.id, EditorialDecision(action="publish"))

    return publish
