"""Public/user API scale workload, using only disposable synthetic data."""

import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from devfeed_core.db import get_engine
from devfeed_core.models import (
    Article,
    ArticleEngagement,
    ArticleLike,
    ArticleOpen,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
    UserAccount,
    UserTopic,
    utcnow,
)
from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.engagement import optional_user
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import insert, text
from test_api_query_budgets import identity, percentile, profile_request

pytestmark = pytest.mark.integration


def user_dependency(user):
    return lambda: user


def article_rows_visited(node):
    own = (
        (node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0))
        * node.get("Actual Loops", 1)
        if node.get("Relation Name") == "articles"
        else 0
    )
    return own + sum(article_rows_visited(child) for child in node.get("Plans", []))


@pytest.fixture
def discovery_data(database):
    size = int(os.environ.get("DEVFEED_DISCOVERY_PROFILE_ROWS", "300"))
    assert 300 <= size <= 100000
    now = utcnow().replace(microsecond=0) - timedelta(minutes=1)
    user_id = identity("scale-user", 0)
    with get_engine().begin() as c:
        c.execute(
            insert(UserAccount.__table__),
            [
                dict(
                    id=identity("scale-user", i),
                    issuer="https://id.test",
                    subject=f"scale-{i}",
                    organization_id="test",
                )
                for i in range(3)
            ],
        )
        for model, kind, values in (
            (
                Source,
                "scale-source",
                lambda i: dict(
                    name=f"Publisher {i:04}",
                    feed_url=f"https://publisher.test/{i}/rss",
                    source_type="publisher",
                    approval_status="approved",
                ),
            ),
            (
                Topic,
                "scale-topic",
                lambda i: dict(
                    name=f"Topic {i:04}",
                    slug=f"scale-topic-{i}",
                    kind="technology",
                    status="active",
                ),
            ),
            (Tag, "scale-tag", lambda i: dict(name=f"Tag {i:04}", slug=f"scale-tag-{i}")),
        ):
            c.execute(
                insert(model.__table__),
                [dict(id=identity(kind, i), **values(i)) for i in range(201)],
            )
        c.execute(
            insert(UserTopic.__table__),
            [dict(user_id=user_id, topic_id=identity("scale-topic", 200))]
            + [
                dict(user_id=identity("scale-user", 1), topic_id=identity("scale-topic", i))
                for i in range(100)
            ],
        )
        for start in range(0, size, 1000):
            indexes = range(start, min(start + 1000, size))
            c.execute(
                insert(Article.__table__),
                [
                    dict(
                        id=identity("scale-article", i),
                        canonical_url=f"https://article.test/{i}",
                        url_hash=identity("scale-article", i).hex,
                        title=f"Database engineering {i:07}",
                        summary="Database software engineering. " * 10,
                        review_status="approved",
                        publication_status="published" if i % 10 else "unpublished",
                        feed_at=now - timedelta(minutes=i),
                        published_at=now - timedelta(minutes=i),
                        language="en",
                        content_type="tutorial" if i % 2 else "article",
                        classification_provenance={"evidence": "internal analysis " * 1000}
                        if i < 120
                        else {},
                    )
                    for i in indexes
                ],
            )
            # One very selective source/topic at the old end of the feed exposes
            # plans that walk the entire ordered feed before finding a match.
            c.execute(
                insert(ArticleOrigin.__table__),
                [
                    dict(
                        article_id=identity("scale-article", i),
                        source_id=identity("scale-source", 200 if i >= size - 12 else i % 200),
                        entry_key=str(i),
                        original_url=f"https://article.test/{i}",
                    )
                    for i in indexes
                ],
            )
            c.execute(
                insert(ArticleTopic.__table__),
                [
                    dict(
                        article_id=identity("scale-article", i),
                        topic_id=identity("scale-topic", 200 if i >= size - 12 else i % 200),
                        role="primary",
                        relevance=1.0,
                        evidence="Test data",
                    )
                    for i in indexes
                ],
            )
            c.execute(
                insert(ArticleTag.__table__),
                [
                    dict(
                        article_id=identity("scale-article", i),
                        tag_id=identity("scale-tag", i % 200),
                    )
                    for i in indexes
                ],
            )
            c.execute(
                insert(ArticleLike.__table__),
                [
                    dict(
                        article_id=identity("scale-article", i),
                        user_id=user_id,
                        created_at=now if i >= size - 12 else now - timedelta(days=20),
                    )
                    for i in indexes
                ],
            )
            c.execute(
                insert(ArticleOpen.__table__),
                [
                    dict(
                        article_id=identity("scale-article", i),
                        viewer_key="a" * 64,
                        opened_hour=now if i >= size - 12 else now - timedelta(days=20),
                    )
                    for i in indexes
                ],
            )
            c.execute(
                insert(ArticleEngagement.__table__),
                [dict(article_id=identity("scale-article", i), opens=1) for i in indexes],
            )
        c.execute(text("ANALYZE"))
    return size, user_id


@pytest.fixture
def discovery_user(discovery_data):
    _, user_id = discovery_data
    user = UserIdentity(
        subject="scale",
        issuer="https://id.test",
        organization_id="test",
        user_id=str(user_id),
        expires_at=4102444800,
        csrf_token="test",
    )
    app = create_app()
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[optional_user] = lambda: user
    with TestClient(app) as client:
        yield client


def test_discovery_scale_budgets(discovery_data, discovery_user, client):
    size, _ = discovery_data
    report_path = os.environ.get("DEVFEED_PROFILE_REPORT")
    repeats = int(os.environ.get("DEVFEED_PROFILE_REPEATS", "1"))
    assert 1 <= repeats <= 100
    paths = [
        ("/v1/feed?limit=1", 4),
        ("/v1/feed?limit=100", 4),
        ("/v1/feed?q=engineering&limit=100", 4),
        ("/v1/feed?q=nonexistent&limit=100", 4),
        ("/v1/feed?topic=scale-topic-200&limit=100", 5),
        ("/v1/feed?topic=scale-topic-200&limit=1", 5),
        (f"/v1/feed?source_id={identity('scale-source', 200)}&limit=100", 4),
        ("/v1/feed?tag=scale-tag-1&limit=100", 4),
        ("/v1/feed?content_type=tutorial&language=en&limit=100", 4),
        ("/v1/topics?has_articles=true&limit=100", 1),
        ("/v1/topics?has_articles=true&offset=200&limit=100", 1),
        ("/v1/user/preferences", 1),
        ("/v1/user/feed?limit=1", 5),
        ("/v1/user/feed?limit=100", 5),
        ("/v1/user/trending?limit=1", 4),
        ("/v1/user/trending?limit=100", 4),
    ]
    for limit in (1, 100):
        paths.append(
            (
                "/v1/user/engagement?"
                + "&".join(
                    f"article_id={identity('scale-article', i)}" for i in range(1, limit + 1)
                ),
                1,
            )
        )
    results = []
    for path, budget in paths:
        http = discovery_user if path.startswith("/v1/user/") else client
        assert http.get(path).status_code == 200
        row, payload = profile_request(http, path, budget, repeats, plans=bool(report_path))
        results.append(row)
        if (
            "topic=scale-topic-200" in path
            or "source_id=" in path
            or path.startswith(("/v1/user/feed", "/v1/user/trending"))
        ):
            expected = [str(identity("scale-article", i)) for i in range(size - 12, size) if i % 10]
            assert (
                [item["id"] for item in payload["items"]] == expected[:1]
                if "limit=1" in path and "limit=100" not in path
                else [item["id"] for item in payload["items"]] == expected
            )
            if report_path and size >= 10000:
                # Protect against a bounded query count hiding a catalogue scan.
                # All these sparse paths match twelve IDs, eleven of them public.
                assert (
                    sum(article_rows_visited(plan["plan"][0]["Plan"]) for plan in row["plans"])
                    <= 40
                ), path
        if path.startswith("/v1/user/engagement"):
            assert all(
                item["liked"] and item["likes"] == 1 and item["opens"] == 1 for item in payload
            )
        if path == "/v1/feed?limit=100":
            cursor = payload["next_cursor"]
            next_row, next_page = profile_request(
                client, f"/v1/feed?limit=100&cursor={cursor}", 4, repeats, plans=bool(report_path)
            )
            assert not ({i["id"] for i in payload["items"]} & {i["id"] for i in next_page["items"]})
            results.append(next_row)
    if report_path:
        concurrency = int(os.environ.get("DEVFEED_PROFILE_CONCURRENCY", "8"))
        assert 1 <= concurrency <= 16
        workload = [
            "/v1/feed?limit=100",
            "/v1/feed?topic=scale-topic-200&limit=100",
            "/v1/user/feed?limit=100",
            "/v1/user/trending?limit=100",
        ]

        def request(path):
            started = time.perf_counter()
            http = discovery_user if path.startswith("/v1/user/") else client
            response = http.get(path)
            assert response.status_code == 200
            return 1000 * (time.perf_counter() - started)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            timings = list(pool.map(request, workload * repeats))
        original_user = discovery_user.app.dependency_overrides[require_user]
        try:
            for index, scenario in ((1, "100 followed topics"), (2, "no followed topics")):
                current = original_user().model_copy(
                    update={"user_id": str(identity("scale-user", index))}
                )
                discovery_user.app.dependency_overrides[require_user] = user_dependency(current)
                for limit in (1, 100):
                    path = f"/v1/user/feed?limit={limit}"
                    discovery_user.get(path)
                    row, payload = profile_request(discovery_user, path, 5, repeats, plans=True)
                    row["scenario"] = scenario
                    assert bool(payload["items"]) == (index == 1)
                    results.append(row)
        finally:
            discovery_user.app.dependency_overrides[require_user] = original_user
        Path(report_path).write_text(
            json.dumps(
                dict(
                    articles=size,
                    likes=size,
                    opens=size,
                    repeats=repeats,
                    transport="TestClient; synthetic DB; authentication dependency overridden",
                    endpoints=results,
                    concurrent=dict(
                        workers=concurrency,
                        requests=len(timings),
                        p50_ms=statistics.median(timings),
                        p95_ms=percentile(timings, 0.95),
                    ),
                ),
                indent=2,
            )
            + "\n"
        )
