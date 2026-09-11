"""HTTP-level SQL budgets; also the populated, disposable API profiling workload.

Run scripts/profile-api.sh for timings, plans and concurrent reads. Normal CI checks
query budgets without asserting machine-dependent latency. Never uses live data.
"""

import hashlib
import json
import math
import os
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from devfeed_core import cache
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleOrigin,
    ArticleReview,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelation,
)
from sqlalchemy import event, insert, text

pytestmark = pytest.mark.integration


def identity(kind, index):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"devfeed-profile/{kind}/{index}")


@pytest.fixture
def profile_data(database):
    size = int(os.environ.get("DEVFEED_PROFILE_ROWS", "120"))
    assert 100 <= size <= 10000
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    snapshot = {"text": "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(512))}
    # Core inserts intentionally bypass workers/cache invalidation during fixture setup.
    with get_engine().begin() as connection:
        for model, kind, values in (
            (
                Source,
                "source",
                lambda i: dict(
                    name=f"Source {i:05}",
                    feed_url=f"https://example.test/{i}/feed",
                    source_type="publisher",
                    approval_status="approved",
                ),
            ),
            (
                Topic,
                "topic",
                lambda i: dict(
                    name=f"Topic {i:05}", slug=f"topic-{i}", kind="technology", status="active"
                ),
            ),
            (Tag, "tag", lambda i: dict(name=f"Tag {i:05}", slug=f"tag-{i}")),
            (
                TopicProposal,
                "proposal",
                lambda i: dict(
                    batch_id=identity("batch", 0),
                    slug=f"proposal-{i}",
                    action="create",
                    origin="import",
                    source_name="Profile",
                    proposed={
                        "name": f"Proposal {i}",
                        "slug": f"proposal-{i}",
                        "kind": "technology",
                    },
                    created_by={},
                ),
            ),
        ):
            connection.execute(
                insert(model.__table__),
                [{"id": identity(kind, i), **values(i)} for i in range(size)],
            )
        connection.execute(
            insert(TopicRelation.__table__),
            [
                dict(
                    topic_id=identity("topic", i),
                    related_topic_id=identity("topic", i + 1),
                    relation="related_to",
                )
                for i in range(size - 1)
            ],
        )
        for kind, published in (("published", True), ("pending", False)):
            connection.execute(
                insert(Article.__table__),
                [
                    dict(
                        id=identity(kind, i),
                        canonical_url=f"https://example.test/{kind}/{i}",
                        url_hash=identity(kind, i).hex,
                        title=f"Database engineering {i:05}",
                        summary="Database software development and programming. " * 10,
                        review_status="approved" if published else "pending",
                        publication_status="published" if published else "unpublished",
                        discovered_at=now - timedelta(days=i % 60),
                        feed_at=now - timedelta(minutes=i),
                        published_to_feed_at=now - timedelta(minutes=i) if published else None,
                    )
                    for i in range(size)
                ],
            )
            connection.execute(
                insert(ArticleOrigin.__table__),
                [
                    dict(
                        article_id=identity(kind, i),
                        source_id=identity("source", i),
                        entry_key=f"{kind}-{i}",
                        original_url=f"https://example.test/{kind}/{i}",
                    )
                    for i in range(size)
                ],
            )
            connection.execute(
                insert(ArticleTag.__table__),
                [
                    dict(article_id=identity(kind, i), tag_id=identity("tag", i))
                    for i in range(size)
                ],
            )
            connection.execute(
                insert(ArticleTopic.__table__),
                [
                    dict(
                        article_id=identity(kind, i),
                        topic_id=identity("topic", i),
                        role="primary",
                        relevance=1.0,
                        evidence="Programming article",
                    )
                    for i in range(size)
                    if published or i % 5 != 1
                ],
            )
            connection.execute(
                insert(ArticleContent.__table__),
                [
                    dict(
                        article_id=identity(kind, i),
                        url=f"https://example.test/{kind}/{i}",
                        text="Short" if not published and i % 5 == 0 else snapshot["text"],
                        content_hash="a" * 64,
                        method="html",
                    )
                    for i in range(size)
                ],
            )
            connection.execute(
                insert(ArticleReview.__table__),
                [
                    dict(
                        article_id=identity(kind, i),
                        action="publish" if published else "update",
                        revision=0,
                        actor="profile",
                        created_at=now - timedelta(days=90),
                    )
                    for i in range(size)
                ],
            )
        for generation in range(3):
            common = dict(
                created_at=now - timedelta(hours=3 - generation),
                finished_at=now,
                input_snapshot=snapshot,
                usage={"totalTokens": 100},
                duration_ms=1000,
            )
            connection.execute(
                insert(ArticleAnalysisJob.__table__),
                [
                    dict(
                        **common,
                        id=identity(f"analysis-{generation}", i),
                        article_id=identity("pending", i),
                        catalog_snapshot=snapshot,
                        status="failed" if generation == 2 and i % 5 == 2 else "succeeded",
                        result={"publication_policy": {"status": "would_publish"}}
                        if generation == 2 and i % 5 == 3
                        else {},
                    )
                    for i in range(size)
                ],
            )
            connection.execute(
                insert(TopicAnalysisJob.__table__),
                [
                    dict(
                        **common,
                        id=identity(f"research-{generation}", i),
                        proposal_id=identity("proposal", i),
                        status="succeeded",
                        input_hash="b" * 64,
                        requested_by={},
                        prompt_version="profile",
                        result={"topic_verification": {"check": {"verdict": "uncertain"}}},
                    )
                    for i in range(size)
                ],
            )
        connection.execute(text("ANALYZE"))
    return size


def cases():
    # Budgets include ORM relationship hydration and HTTP response serialization.
    for path, budget in (
        ("/v1/feed", 4),
        ("/v1/feed?q=database", 4),
        ("/v1/sources", 1),
        ("/v1/topics", 1),
        ("/v1/topics?has_articles=true", 1),
        ("/v1/tags", 1),
        ("/v1/admin/articles", 5),
        ("/v1/admin/articles?q=database", 5),
        ("/v1/admin/sources", 2),
        ("/v1/admin/topics", 2),
        ("/v1/admin/tags", 2),
        ("/v1/admin/topic-proposals", 3),
        ("/v1/admin/topic-proposals?analysis=no_additions", 3),
        ("/v1/admin/topic-relationships", 4),
        ("/v1/admin/jobs/analysis", 3),
        ("/v1/admin/jobs/topic-analysis", 3),
        ("/v1/admin/jobs/ai-analysis", 4),
        ("/v1/admin/jobs/ai-analysis?status=failed", 4),
    ):
        for limit in (1, 100):
            yield path + ("&" if "?" in path else "?") + f"limit={limit}", budget
    article_id = identity("published", 0)
    yield f"/v1/articles/{article_id}", 4
    yield f"/v1/admin/articles/{article_id}", 4
    yield f"/v1/admin/articles/{article_id}/content", 2
    yield f"/v1/admin/articles/{article_id}/reviews", 3
    yield f"/v1/admin/automation/articles/{article_id}/decisions", 3
    yield f"/v1/admin/topic-proposals/{identity('proposal', 0)}", 2
    yield "/v1/topics/topic-0/relations", 2
    yield "/v1/admin/knowledge/search?q=Topic", 4
    yield "/v1/admin/knowledge/graph?limit=100", 12
    yield (
        (
            "/v1/admin/knowledge/path?from_node=topic:"
            + str(identity("topic", 0))
            + "&to_node=topic:"
            + str(identity("topic", 3))
        ),
        16,
    )
    yield "/v1/feed?topic=topic-0&limit=100", 4
    yield "/v1/admin/articles?limit=100&offset=100", 5
    yield "/v1/admin/topic-proposals?limit=100&offset=100", 3
    yield "/v1/sources?limit=500", 1
    yield "/v1/admin/overview?days=30", 16


def percentile(values, fraction):
    return round(sorted(values)[math.ceil(len(values) * fraction) - 1], 3)


def profile_request(http, path, budget, repeats=1, *, plans=False):
    engine = get_engine()
    elapsed, db_elapsed, counts, statements = [], [], [], []

    def before(conn, cursor, statement, parameters, context, executemany):
        context.profile_started = time.perf_counter()

    def after(conn, cursor, statement, parameters, context, executemany, captured=statements):
        captured.append((statement, parameters, time.perf_counter() - context.profile_started))

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        for _ in range(repeats):
            statements.clear()
            started = time.perf_counter()
            response = http.get(path)
            elapsed.append(1000 * (time.perf_counter() - started))
            assert response.status_code == 200, (path, response.text)
            counts.append(len(statements))
            db_elapsed.append(1000 * sum(row[2] for row in statements))
            assert len(statements) <= budget, (path, len(statements), budget)
            if "/jobs/" in path or "/topic-proposals" in path:
                assert all(
                    "input_snapshot" not in sql and "catalog_snapshot" not in sql
                    for sql, _, _ in statements
                    if not sql.startswith("SELECT count(")
                ), path
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
    row = dict(
        path=path,
        queries=max(counts),
        p50_ms=round(statistics.median(elapsed), 3),
        p95_ms=percentile(elapsed, 0.95),
        db_execute_p50_ms=round(statistics.median(db_elapsed), 3),
        response_bytes=len(response.content),
    )
    if plans:
        row["plans"] = []
        # Only SELECTs captured from our synthetic, explicitly disposable database.
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            for sql, parameters, _ in statements:
                if sql.lstrip().upper().startswith(("SELECT", "WITH")):
                    plan = connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, parameters
                    ).scalar_one()
                    row["plans"].append(dict(sql=sql, plan=plan))
    return row, response.json()


def test_populated_api_query_budgets(profile_data, client, admin_client, monkeypatch):
    report_path = os.environ.get("DEVFEED_PROFILE_REPORT")
    repeats = int(os.environ.get("DEVFEED_PROFILE_REPEATS", "1"))
    assert 1 <= repeats <= 100
    engine = get_engine()
    results = []
    for path, budget in cases():
        http = admin_client if path.startswith("/v1/admin/") else client
        warm = http.get(path)
        assert warm.status_code == 200, (path, warm.text)
        if path.startswith("/v1/admin/overview"):
            metrics = warm.json()["automation"]
            for remainder, blocker in enumerate(metrics["blockers"][:5]):
                expected = [i for i in range(profile_data) if i % 5 == remainder]
                assert blocker["count"] == len(expected)
                expected.sort(key=lambda i: (-(i % 60), identity("pending", i)))
                assert [target["id"] for target in blocker["targets"]] == [
                    str(identity("pending", i)) for i in expected[:5]
                ]
            assert metrics["blockers"][5]["count"] == profile_data
        row, _ = profile_request(http, path, budget, repeats, plans=bool(report_path))
        results.append(row)
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    try:
        path = "/v1/feed?limit=100"
        assert client.get(path).headers["x-cache"] == "MISS"
        cached_statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            cached_statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            started = time.perf_counter()
            cached = client.get(path)
            cache_ms = 1000 * (time.perf_counter() - started)
            assert cached.status_code == 200 and cached.headers["x-cache"] == "HIT"
            assert not cached_statements
        finally:
            event.remove(engine, "before_cursor_execute", capture)
    finally:
        monkeypatch.setattr(get_settings(), "cache_enabled", False)
        cache.close_cache()
    if report_path:
        concurrency = int(os.environ.get("DEVFEED_PROFILE_CONCURRENCY", "8"))
        assert 1 <= concurrency <= 16
        workload = [
            "/v1/admin/overview",
            "/v1/admin/articles?limit=100",
            "/v1/admin/jobs/ai-analysis?limit=100",
            "/v1/admin/topic-proposals?limit=100",
        ]

        def request(path):
            started = time.perf_counter()
            response = admin_client.get(path)
            assert response.status_code == 200, (path, response.text)
            return 1000 * (time.perf_counter() - started)

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            timings = list(pool.map(request, workload * repeats))
        Path(report_path).write_text(
            json.dumps(
                dict(
                    articles=profile_data * 2,
                    topics=profile_data,
                    proposals=profile_data,
                    analysis_jobs=profile_data * 6,
                    repeats=repeats,
                    cache=False,
                    cached_feed=dict(queries=0, elapsed_ms=round(cache_ms, 3)),
                    transport="TestClient handlers/serialization; no TCP, TLS or OIDC provider",
                    timing="Warm DB, cache disabled; SQL execute excludes hydration/serialization",
                    endpoints=results,
                    concurrent=dict(
                        workers=concurrency,
                        requests=len(timings),
                        p50_ms=round(statistics.median(timings), 3),
                        p95_ms=percentile(timings, 0.95),
                        elapsed_ms=round(1000 * (time.perf_counter() - started), 3),
                    ),
                ),
                indent=2,
            )
            + "\n"
        )


def test_blocker_text_threshold_and_overlapping_latest_results(database, admin_client):
    samples = [
        None,
        "",
        "x" * 39,
        "x" * 40,
        "1 " * 20000,
        "1 " * 20000 + "x" * 39,
        "1 " * 20000 + "x" * 40,
        "é界\n" * 20,
        "a\n\t1" * 40,
        "a\n\t1" * 39,
    ]
    now = datetime.now(UTC) - timedelta(minutes=1)
    with get_engine().begin() as connection:
        connection.execute(
            insert(Article.__table__),
            [
                dict(
                    id=identity("threshold", i),
                    canonical_url=f"https://example.test/{i}",
                    url_hash=identity("threshold", i).hex,
                    title=f"Threshold {i}",
                    summary="Fallback " * 40,
                    discovered_at=now,
                    editorial_revision=i,
                )
                for i in range(len(samples))
            ],
        )
        connection.execute(
            insert(ArticleContent.__table__),
            [
                dict(
                    article_id=identity("threshold", i),
                    url=f"https://example.test/{i}",
                    text=value,
                    content_hash="a" * 64,
                    method="html",
                )
                for i, value in enumerate(samples)
                if value is not None
            ],
        )
        # An old failure must not appear once superseded. The latest failure and
        # preview may both block the same short article; groups are not exclusive.
        connection.execute(
            insert(ArticleAnalysisJob.__table__),
            [
                dict(
                    article_id=identity("threshold", i),
                    status=status,
                    created_at=created,
                    result=result,
                )
                for i, status, created, result in [
                    (0, "failed", now - timedelta(hours=1), {}),
                    (0, "succeeded", now, {}),
                    (1, "failed", now, {"publication_policy": {"status": "would_publish"}}),
                ]
            ],
        )
        expected = connection.execute(
            text("""
            SELECT a.id, a.editorial_revision,
                   length(regexp_replace(coalesce(c.text, a.summary),
                          '[^[:alpha:]]', '', 'g')) >= 40 AS readable
            FROM articles a LEFT JOIN article_contents c ON c.article_id = a.id
            ORDER BY a.discovered_at, a.id
        """)
        ).all()
    response = admin_client.get("/v1/admin/overview")
    assert response.status_code == 200, response.text
    blockers = {b["code"]: b for b in response.json()["automation"]["blockers"]}
    for code, readable in (("insufficient_text", False), ("missing_primary_topic", True)):
        rows = [r for r in expected if r.readable is readable]
        assert blockers[code]["count"] == len(rows)
        assert [(r["id"], r["revision"]) for r in blockers[code]["targets"]] == [
            (str(r.id), r.editorial_revision) for r in rows[:5]
        ]
    for code in ("analysis_failed", "publication_preview"):
        assert blockers[code]["count"] == 1
        assert blockers[code]["targets"][0]["id"] == str(identity("threshold", 1))
    assert blockers["editorial_review"]["count"] == 0
    assert admin_client.get(f"/v1/admin/articles/{identity('threshold', 0)}/content").json() is None
    missing = uuid.uuid4()
    for path in (
        f"/v1/admin/articles/{missing}/content",
        f"/v1/admin/articles/{missing}/reviews",
        f"/v1/admin/automation/articles/{missing}/decisions",
    ):
        assert admin_client.get(path).status_code == 404
