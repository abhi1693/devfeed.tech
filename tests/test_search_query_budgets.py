"""Real-engine scale profile. Every write is restricted by disposable DB fixtures."""

import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
    utcnow,
)
from devfeed_core.search_engine import KINDS
from devfeed_core.search_index import backfill, sync_batch
from sqlalchemy import insert, text
from test_api_query_budgets import identity, percentile, profile_request

pytestmark = pytest.mark.integration


def test_federated_search_query_budget_and_scale(database, client, search_engine):
    size = int(os.environ.get("DEVFEED_SEARCH_PROFILE_ROWS", "300"))
    assert 300 <= size <= 100000
    counts = {"sources": min(200, size), "topics": min(2000, size), "tags": min(4000, size)}
    now = utcnow() - timedelta(minutes=1)
    # Fixture-only fast bulk load: the database fixture validates the _test suffix.
    # Backfill exercises the real projection after triggers are restored.
    with get_engine().begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        for kind, model in (("sources", Source), ("topics", Topic), ("tags", Tag)):
            rows = []
            for i in range(counts[kind]):
                row = dict(id=identity(kind, i), name=f"Kubernetes {kind} {i}")
                if kind == "sources":
                    row.update(
                        feed_url=f"https://publisher.test/{i}/rss",
                        source_type="publisher",
                        approval_status="approved",
                    )
                else:
                    row.update(slug=f"kubernetes-{kind}-{i}")
                if kind == "topics":
                    row.update(kind="technology", status="active", aliases=[f"k8s{i}"])
                rows.append(row)
            c.execute(insert(model.__table__), rows)
        for start in range(0, size, 500):
            indexes = range(start, min(start + 500, size))
            c.execute(
                insert(Article.__table__),
                [
                    dict(
                        id=identity("article", i),
                        title=f"Kubernetes networking guide {i}",
                        slug=f"kubernetes-networking-{i}",
                        canonical_url=f"https://article.test/{i}",
                        url_hash=identity("article", i).hex,
                        review_status="approved",
                        publication_status="published",
                        feed_at=now - timedelta(minutes=i),
                        summary="Container software engineering. " * 20,
                    )
                    for i in indexes
                ],
            )
            c.execute(
                insert(ArticleOrigin.__table__),
                [
                    dict(
                        article_id=identity("article", i),
                        source_id=identity("sources", i % counts["sources"]),
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
                        article_id=identity("article", i),
                        topic_id=identity("topics", i % counts["topics"]),
                        role="primary",
                        relevance=1.0,
                        evidence="Synthetic profile",
                    )
                    for i in indexes
                ],
            )
            c.execute(
                insert(ArticleTag.__table__),
                [
                    dict(
                        article_id=identity("article", i),
                        tag_id=identity("tags", i % counts["tags"]),
                    )
                    for i in indexes
                ],
            )
    with get_engine().begin() as c:
        for table in (
            "articles",
            "article_origins",
            "article_topics",
            "article_tags",
            "sources",
            "topics",
            "tags",
        ):
            c.execute(text(f"ANALYZE {table}"))
    get_settings().search_index_batch_size = 500
    started = time.perf_counter()
    queued = backfill(database)
    batches = 0
    while sync_batch(database, search_engine):
        batches += 1
        assert batches < 10000
    index_seconds = round(time.perf_counter() - started, 3)
    assert queued == size + sum(counts.values())
    report_path = os.environ.get("DEVFEED_SEARCH_PROFILE_REPORT")
    repeats = int(os.environ.get("DEVFEED_PROFILE_REPEATS", "1"))
    assert 1 <= repeats <= 100
    cases = [
        "/v1/search?q=kubernetes",
        "/v1/search?q=kuberentes",
        "/v1/search?q=kubernetes+networking",
        "/v1/search?q=k8s199",
        "/v1/search?q=zzzznotfoundzzzz",
        "/v1/search?q=kubernetes&section=articles&page=2",
    ]
    results = []
    for path in cases:
        row, payload = profile_request(client, path, 8, repeats, plans=bool(report_path))
        if "zzzz" in path:
            assert all(not v["items"] for v in payload["sections"].values())
        else:
            assert payload["sections"]["articles"]["items"]
        if "section=" in path:
            assert set(payload["sections"]) == {"articles"}
        else:
            assert set(payload["sections"]) == set(KINDS)
        for plan in row.get("plans", []):
            sql = plan["sql"].lower()
            assert "ilike" not in sql and "count(" not in sql
            if "from articles" in sql:
                assert "articles.id in (" in sql or ".id in (" in sql
        results.append(row)
    if report_path:
        concurrency = 8

        def request(path):
            started = time.perf_counter()
            response = client.get(path)
            assert response.status_code == 200, response.text
            return 1000 * (time.perf_counter() - started)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            timings = list(pool.map(request, cases * 8))
        report = dict(
            articles=size,
            catalogue=counts,
            cache="disabled",
            index_seconds=index_seconds,
            index_batches=batches,
            requests=results,
            concurrent=dict(
                clients=concurrency,
                requests=len(timings),
                p50_ms=round(statistics.median(timings), 3),
                p95_ms=percentile(timings, 0.95),
                max_ms=round(max(timings), 3),
            ),
        )
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(report, indent=2) + "\n")
